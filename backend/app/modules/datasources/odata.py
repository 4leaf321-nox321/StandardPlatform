"""OData 읽기 — **v4 를 기준으로, v2 봉투도 받는다.**

ENOVIA·Teamcenter 같은 PLM 은 v4 로 낸다(`value` + `@odata.nextLink`). SAP Gateway 계열은 v2
(`d.results` + `__next`). 둘의 차이는 봉투뿐이라 한 함수가 둘 다 벗긴다.

읽기만 한다. 페이지를 끝까지 따라가되 **상한이 있다** — 상한 없이 따라가면 잘못 적은
`$filter` 하나가 표 전체를 끌어온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.shared.errors import AppError, code

TIMEOUT_SECONDS = 60.0
#: 한 번의 동기화가 읽는 행의 상한. 넘으면 `$filter` 로 나누라고 말한다.
MAX_ROWS = 50_000
#: 따라가는 페이지 수 상한 — nextLink 가 자기 자신을 가리키는 서버가 실제로 있다.
MAX_PAGES = 1_000


@dataclass
class Auth:
    kind: str = "none"
    user: str = ""
    secret: str = ""

    def headers(self) -> dict[str, str]:
        if self.kind == "bearer" and self.secret:
            return {"Authorization": f"Bearer {self.secret}"}
        if self.kind == "header" and self.user:
            # 이름이 정해진 헤더 하나 — `X-API-Key: …` 처럼. REST 에서 흔하다.
            return {self.user: self.secret}
        return {}

    def basic(self) -> tuple[str, str] | None:
        if self.kind == "basic" and self.user:
            return (self.user, self.secret)
        return None


@dataclass
class Fetched:
    rows: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    truncated: bool = False
    """MAX_ROWS 에서 끊었다 — 계획은 서지만 적용은 막는다."""


def _unwrap(body: Any) -> tuple[list[dict[str, Any]], str | None]:
    """봉투를 벗긴다 — v4 `value`/`@odata.nextLink`, v2 `d.results`/`__next`, 또는
    그냥 배열."""
    if isinstance(body, list):
        return [one for one in body if isinstance(one, dict)], None
    if not isinstance(body, dict):
        raise AppError(
            code("DATASOURCES", 10), "OData 응답이 JSON 객체가 아닙니다.", status=502
        )
    if "value" in body and isinstance(body["value"], list):
        return body["value"], body.get("@odata.nextLink") or body.get("odata.nextLink")
    inner = body.get("d")
    if isinstance(inner, dict) and isinstance(inner.get("results"), list):
        return inner["results"], inner.get("__next")
    if isinstance(inner, list):
        return inner, None
    raise AppError(
        code("DATASOURCES", 10),
        "OData 응답에서 행을 찾을 수 없습니다 (`value` 나 `d.results` 가 없습니다).",
        status=502,
    )


def _pick(row: dict[str, Any], path: str) -> Any:
    """`Address/Country` 나 `Address.Country` 처럼 안으로 들어간 열도 읽는다."""
    current: Any = row
    for part in path.replace("/", ".").split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    # v2 의 확장 항목은 {"results": [...]} 로 온다 — 배열로 편다.
    if isinstance(current, dict) and isinstance(current.get("results"), list):
        return current["results"]
    return current


def fetch(
    *,
    base_url: str,
    entity_set: str,
    select: str = "",
    filter_: str = "",
    auth: Auth | None = None,
    page_size: int = 500,
    max_rows: int = MAX_ROWS,
    transport: httpx.BaseTransport | None = None,
) -> Fetched:
    """엔티티 셋을 끝까지(상한 안에서) 읽는다. 연결·인증 실패는 **무엇이 실패했는지**
    말한다."""
    url = f"{base_url.rstrip('/')}/{entity_set.strip('/')}"
    page = max(1, min(page_size, 5000))
    params: dict[str, str] = {"$top": str(page)}
    if select.strip():
        params["$select"] = select.strip()
    if filter_.strip():
        params["$filter"] = filter_.strip()
    headers = {"Accept": "application/json", **(auth.headers() if auth else {})}
    out = Fetched()
    skip = 0
    next_url: str | None = url
    next_params: dict[str, str] | None = params
    try:
        with httpx.Client(
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers=headers,
            auth=auth.basic() if auth else None,
            follow_redirects=True,
        ) as client:
            while next_url and out.pages < MAX_PAGES:
                response = client.get(next_url, params=next_params)
                if response.status_code in (401, 403):
                    raise AppError(
                        code("DATASOURCES", 11),
                        f"OData 인증이 거절됐습니다 (HTTP {response.status_code}). "
                        "인증 방식과 비밀을 확인하세요.",
                        status=502,
                    )
                if response.status_code >= 400:
                    raise AppError(
                        code("DATASOURCES", 12),
                        f"OData 가 HTTP {response.status_code} 를 돌려줬습니다: "
                        f"{response.text[:300]}",
                        status=502,
                    )
                try:
                    body = response.json()
                except ValueError:
                    raise AppError(
                        code("DATASOURCES", 10), "OData 응답이 JSON 이 아닙니다.", status=502
                    ) from None
                rows, next_link = _unwrap(body)
                out.pages += 1
                for row in rows:
                    if len(out.rows) >= max_rows:
                        out.truncated = True
                        return out
                    out.rows.append(row)
                if next_link:
                    # 서버가 쪽을 넘긴다(server-driven paging). nextLink 는 이미 쿼리를 담고
                    # 있다 — 다시 붙이면 `$top` 이 두 번 간다.
                    next_url = next_link
                    next_params = None
                elif len(rows) >= page:
                    # 서버가 안 넘기면 `$top` 은 상한일 뿐이다 — `$skip` 으로 우리가 넘긴다.
                    # 한 쪽이 꽉 찼을 때만 다음을 묻는다(덜 차면 끝이다).
                    skip += page
                    next_url = url
                    next_params = {**params, "$skip": str(skip)}
                else:
                    next_url = None
    except httpx.HTTPError as caught:
        raise AppError(
            code("DATASOURCES", 13),
            f"OData 에 연결하지 못했습니다: {str(caught)[:300]}",
            status=502,
        ) from None
    return out


def pick(row: dict[str, Any], path: str) -> Any:
    return _pick(row, path)
