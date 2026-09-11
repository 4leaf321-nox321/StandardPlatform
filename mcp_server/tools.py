"""MCP 도구의 알맹이 — **REST 를 얇게 감싼다.**

여기에 규칙을 두지 않는다. 검증도 권한도 서버가 한다 — 두 벌이 되면 **MCP 로는
되는데 화면에서는 안 되는** 상태가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.

## 왜 도구가 타입마다 있지 않은가

설계 문서는 「스키마를 읽어 도구를 동적으로 만든다」 고 적었다. 타입마다 도구를
만들면 타입 20개에 도구가 80개가 되고, **도구 목록이 길어질수록 모델은 엉뚱한
것을 고른다.** 대신 도구는 일곱으로 고정하고 `ontology_schema` 하나가 「지금 이
설치에 무엇이 있고 각 타입이 무엇을 받는가」 를 말한다 — **동적인 것은 도구가
아니라 스키마다.**

## 어댑터와 가른 이유

`server.py` 는 `mcp` 패키지를 쓰지만 이 파일은 `httpx` 만 쓴다. 그래서 이 알맹이는
**MCP 없이도 시험할 수 있다** — 붙여 보기 전에 도는지 아는 유일한 방법이다.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8030/api"


class PlatformError(RuntimeError):
    """서버가 거절했다. **메시지를 그대로 전한다** — 서버의 오류 문구는 무엇을
    고쳐야 하는지 적혀 있고, 여기서 고쳐 쓰면 그것을 잃는다."""


class Platform:
    """플랫폼 REST 클라이언트.

    자격은 **개인 액세스 토큰(PAT)** 이다. 사람 세션과 달리 범위가 있어서,
    「객체만 넣는」 토큰과 「정의도 고치는」 토큰을 가를 수 있다.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("PLATFORM_URL") or DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("PLATFORM_TOKEN") or ""
        if not self.token:
            raise PlatformError(
                "PLATFORM_TOKEN 이 없습니다. 화면의 「내 정보」 에서 개인 액세스 토큰을 "
                "만들고 환경변수로 주세요. 범위는 read + objects:write "
                "(정의까지 고치려면 ontology:write) 입니다."
            )
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60,
        )

    def request(self, method: str, path: str, **kw: Any) -> Any:
        response = self._client.request(method, path, **kw)
        if response.status_code >= 400:
            body: dict[str, Any] = {}
            with contextlib.suppress(ValueError):
                body = response.json()
            message = (body.get("error") or {}).get("message") or response.text[:300]
            code = (body.get("error") or {}).get("code") or response.status_code
            raise PlatformError(f"[{code}] {message}")
        return response.json() if response.content else None


# --- 도구 -------------------------------------------------------------------


def ontology_schema(platform: Platform) -> Any:
    """이 설치의 **정의 전부** — 묶음·타입·속성·관계 종류.

    무엇을 만들 수 있고 각 타입이 어떤 값을 받는지가 여기 다 있다. **다른 도구를
    부르기 전에 이것부터 읽는다.**
    """
    return platform.request("GET", "/ontology/schema")


def ontology_import(platform: Platform, schema: dict[str, Any], apply: bool = False) -> Any:
    """정의를 통째로 **한 트랜잭션으로** 적용한다.

    `apply=False`(기본)면 **아무것도 안 바꾸고** 계획만 돌려준다 — 무엇이 새로
    생기고, 무엇이 바뀌고, **무엇을 조용히 잃는지**(경고). 사람이 그 계획을 읽고
    판단할 자리다.

    더하고 고치기만 한다. **스키마에 없다고 지우지 않는다.**
    """
    return platform.request(
        "POST",
        f"/ontology/import?dry_run={'false' if apply else 'true'}",
        json=schema,
    )


def objects_list(
    platform: Platform,
    type_slug: str,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    properties: dict[str, str] | None = None,
) -> Any:
    """그 타입의 객체 목록. `properties` 는 `{"등급": "A"}` 처럼 **속성 키로** 거른다."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q:
        params["q"] = q
    for key, value in (properties or {}).items():
        params[f"p.{key}"] = value
    return platform.request("GET", f"/objects/{type_slug}", params=params)


def object_get(platform: Platform, type_slug: str, object_id: str) -> Any:
    """객체 하나 — 속성·첨부·**관련 객체**(양방향)까지."""
    return platform.request("GET", f"/objects/{type_slug}/{object_id}")


def object_create(
    platform: Platform,
    type_slug: str,
    label: str,
    key: str | None = None,
    properties: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    description: str = "",
) -> Any:
    """객체 하나를 만든다.

    `workspace_slug` 를 비우면 **전역**이 되고 시스템 관리자만 만들 수 있다.
    속성은 정의에 없는 키를 넣으면 거절된다 — 먼저 `ontology_schema` 를 읽는다.
    """
    return platform.request(
        "POST",
        f"/objects/{type_slug}",
        json={
            "key": key,
            "label": label,
            "description": description,
            "properties": properties or {},
            "workspace_slug": workspace_slug,
        },
    )


def object_update(
    platform: Platform,
    type_slug: str,
    object_id: str,
    label: str | None = None,
    properties: dict[str, Any] | None = None,
    description: str | None = None,
) -> Any:
    """객체를 고친다 — **보낸 것만.**

    `properties` 는 보낸 키만 병합한다. 값을 지우려면 그 키에 `null` 을 넣는다 —
    통째로 덮으면 다른 속성이 함께 날아가고 **그 손실은 아무 데도 안 뜬다.**
    """
    body: dict[str, Any] = {}
    if label is not None:
        body["label"] = label
    if description is not None:
        body["description"] = description
    if properties is not None:
        body["properties"] = properties
    return platform.request("PATCH", f"/objects/{type_slug}/{object_id}", json=body)


def relation_add(
    platform: Platform,
    type_slug: str,
    object_id: str,
    relation: str,
    dst_object_id: str,
    evidence_note: str = "",
) -> Any:
    """객체 둘을 잇는다.

    **근거를 적는다.** 근거 없는 연결은 시간이 지나면 아무도 못 믿는다 — 맞는지
    확인하려면 처음부터 다시 조사해야 하기 때문이다. 기계가 이은 것이면 더 그렇다.
    """
    return platform.request(
        "POST",
        f"/objects/{type_slug}/{object_id}/relations",
        json={
            "relation": relation,
            "dst_object_id": dst_object_id,
            "evidence_note": evidence_note,
        },
    )


def objects_import(
    platform: Platform,
    type_slug: str,
    rows: list[dict[str, Any]],
    workspace_slug: str | None = None,
    apply: bool = False,
) -> Any:
    """객체를 **여러 행 한 번에** — 같은 식별자(`key`)면 만들지 않고 고친다(upsert).

    `apply=False`(기본)면 **아무것도 안 바꾸고** 행마다 무엇이 될지(새로/고침/그대로/
    오류)를 돌려준다. 사람에게 보여 주고 판단을 받은 뒤 `apply=True` 로 부른다.
    **한 행이라도 오류면 아무것도 안 넣는다.**

    행은 `{"key": ..., "label": ..., <속성 키>: ...}` 꼴. 없는 키는 안 건드리고,
    비우려면 `null` 을 넣는다. 참조 속성은 상대의 식별자(없으면 이름)로 적어도 된다.
    한 번에 5000행까지.
    """
    return platform.request(
        "POST",
        f"/objects/{type_slug}/import-rows",
        json={"rows": rows, "workspace_slug": workspace_slug, "apply": apply},
    )


def relations_import(
    platform: Platform,
    type_slug: str,
    rows: list[dict[str, Any]],
    apply: bool = False,
) -> Any:
    """관계를 **여러 줄 한 번에** — `type_slug` 의 객체에서 출발하는 선들.

    행은 `{"src": ..., "relation": ..., "dst": ..., "evidence_note": ...}` 꼴. 끝점은
    식별자(없으면 이름). 이미 이어진 것은 「그대로」 라 두 번 올려도 두 겹이 안 된다.
    `apply=False` 면 계획만. **근거(evidence_note)를 적는다** — 기계가 이은 것이면 더.
    """
    return platform.request(
        "POST",
        f"/objects/{type_slug}/relations/import-rows",
        json={"rows": rows, "apply": apply},
    )


#: 도구 이름 -> 함수. `server.py` 가 이 표로 등록한다.
TOOLS = {
    "ontology_schema": ontology_schema,
    "ontology_import": ontology_import,
    "objects_list": objects_list,
    "object_get": object_get,
    "object_create": object_create,
    "object_update": object_update,
    "relation_add": relation_add,
    "objects_import": objects_import,
    "relations_import": relations_import,
}
