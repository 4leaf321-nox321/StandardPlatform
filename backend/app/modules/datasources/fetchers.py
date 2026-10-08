"""행을 읽어 오는 조각 — **소스 종류마다 하나.** 나머지 파이프라인은 종류를 모른다.

    sp_core 형제 Standard Platform 의 코어 창구(`/api/core/<타입>`) — **지난번 이후만.**
    odata   `odata.py` — v4/v2, 쪽 넘김
    rest    JSON 을 주는 REST. 행이 있는 자리(`rows_path`)와 쪽 넘김 방식(`paging`)을
            정의가 적는다
    file    CSV · Excel · JSON — URL 이거나 `datasource_dir` 아래 파일

셋 다 `Fetched(rows=list[dict])` 를 돌려주고, 그 뒤(칸 대응·다시 찾기·계획·적용)는 같다.
새 종류를 더하려면 여기 함수 하나와 화면의 종류별 칸 몇 개면 된다.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import httpx

from app.config import get_settings
from app.modules.datasources import odata
from app.modules.datasources.models import DataSource
from app.modules.datasources.odata import Auth, Fetched
from app.shared import tabular
from app.shared.errors import AppError, code

MAX_PAGES = 1_000


def fetch(
    source: DataSource,
    *,
    auth: Auth,
    max_rows: int = odata.MAX_ROWS,
    page_size: int | None = None,
    select: str = "",
    transport: httpx.BaseTransport | None = None,
    since: str | None = None,
    cursor: str = "",
) -> Fetched:
    """`since` · `cursor` 는 `sp_core` 만 쓴다 — 안 주면 소스의 시계에서 처음 쪽부터."""
    if source.kind == "odata":
        return odata.fetch(
            base_url=source.base_url,
            entity_set=source.entity_set,
            select=select,
            filter_=source.filter,
            auth=auth,
            page_size=page_size or source.page_size,
            max_rows=max_rows,
            transport=transport,
        )
    if source.kind == "rest":
        return fetch_rest(
            base_url=source.base_url,
            path=source.entity_set,
            options=source.options or {},
            auth=auth,
            page_size=page_size or source.page_size,
            max_rows=max_rows,
            transport=transport,
        )
    if source.kind == "sp_core":
        return fetch_sp_core(
            base_url=source.base_url,
            type_slug=source.entity_set,
            since=source.since_mark if since is None else since,
            cursor=cursor,
            auth=auth,
            page_size=page_size or source.page_size,
            max_rows=max_rows,
            transport=transport,
        )
    if source.kind == "file":
        return fetch_file(
            location=source.entity_set,
            options=source.options or {},
            auth=auth,
            max_rows=max_rows,
            transport=transport,
        )
    raise AppError(
        code("DATASOURCES", 30), f"모르는 소스 종류입니다: {source.kind}", status=422
    )


# --- 형제 Standard Platform 의 코어 ------------------------------------------------
#
# 다른 곳과 다른 점이 셋이다.
#
#   증분      `since` 에 지난번 `as_of` 를 넣으면 그 뒤에 바뀐 것만 온다. 새벽마다 전량을
#             끌어오지 않아도 된다.
#   무덤      지워진 것도 온다(`deleted: true`) — 그래야 받는 쪽이 사라진 것을 안다.
#   봉투      값은 `properties` 안에 한 겹 들어 있다. 대응은 평평한 열 이름으로 적으므로
#             여기서 펴 준다.
#
# **끝까지 받았을 때만 `as_of` 를 들고 나온다.** 쪽이 남았는데 시계를 옮기면 남은 쪽을 영영
# 안 받는다 — 상대도 그래서 `next` 가 있으면 `as_of` 를 안 준다.

#: 봉투의 자리. 펼친 행에서 **이 이름이 이긴다** — 같은 이름의 속성이 있으면 봉투가 남는다.
CORE_ENVELOPE = ("key", "label", "status", "updated_at", "deleted", "merged_into")

#: 무덤 표시 — `services.py` 가 이 열을 보고 「사라진 것」 으로 다룬다.
CORE_DELETED = "deleted"


def core_row(item: dict[str, Any]) -> dict[str, Any]:
    """한 행을 평평하게 — `properties` 를 펴고 봉투를 위에 얹는다."""
    values = item.get("properties")
    out: dict[str, Any] = dict(values) if isinstance(values, dict) else {}
    for name in CORE_ENVELOPE:
        if name in item:
            out[name] = item[name]
    return out


def _core_client(auth: Auth, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        timeout=odata.TIMEOUT_SECONDS,
        transport=transport,
        headers={"Accept": "application/json", **auth.headers()},
        auth=auth.basic(),
        follow_redirects=True,
    )


def _core_body(response: httpx.Response, what: str) -> Any:
    """상대의 몸을 읽는다 — **JSON 이 아니면 무엇이 왔는지 말한다.**

    SSO 앞단은 로그인 화면(HTML)을 200 으로 준다. 그것을 그대로 `json()` 하면
    `JSONDecodeError` 가 실행 기록도 남기지 못하고 작업을 통째로 죽였다(2026-10-08)."""
    try:
        return response.json()
    except ValueError:
        head = " ".join(response.text[:200].split())
        raise AppError(
            code("DATASOURCES", 44),
            f"{what} 응답이 JSON 이 아닙니다 — 로그인 화면(SSO)이나 다른 서버가 대신 답했을 "
            f"수 있습니다. 주소와 인증을 확인하세요. 받은 것: {head}",
            status=502,
        ) from None


def _core_pages(
    *,
    url: str,
    what: str,
    since: str,
    cursor: str,
    auth: Auth,
    page_size: int,
    max_rows: int,
    transport: httpx.BaseTransport | None,
    shape: Any,
) -> Fetched:
    """코어 창구의 쪽을 따라간다 — 객체와 선이 **같은 규칙**(`since` · `next` · `as_of`)이다.

    `cursor` 를 주면 그 자리부터 잇는다(지난 차례가 끊은 자리 — `Fetched.next`).

    **쪽 경계에서 끊는다.** 상한에 닿으면 받은 쪽은 통째로 들고 나가고, 상대가 준 다음
    커서를 `next` 에 담는다 — 쪽 가운데서 자르면 커서가 가리키는 자리와 받은 것이 어긋나
    잘린 줄을 영영 못 받는다. 예전에는 끊으면 커서를 버렸고, 다음 차례가 같은 `since` 로 같은
    5만 행을 다시 받아 **영영 앞으로 못 갔다**(2026-10-08).
    """
    out = Fetched()
    current = cursor or None
    try:
        with _core_client(auth, transport) as client:
            while out.pages < MAX_PAGES:
                params = {"limit": str(page_size)}
                if since:
                    params["since"] = since
                if current:
                    params["cursor"] = current
                response = client.get(url, params=params)
                _raise_for_core(response)
                body = _core_body(response, what)
                if isinstance(body, dict) and body.get("reset"):
                    out.reset = True
                    out.reset_reason = str(body.get("reset_reason") or "")
                    out.rows = []
                    return out
                items = body.get("items") if isinstance(body, dict) else None
                if not isinstance(items, list):
                    raise AppError(
                        code("DATASOURCES", 37),
                        f"코어 응답에 items 가 없습니다 — 주소가 그 설치의 `{what}` 인지 "
                        "확인하세요.",
                        status=502,
                    )
                out.rows.extend(shape(one) for one in items if isinstance(one, dict))
                out.pages += 1
                current = body.get("next")
                if not current:
                    # 끝까지 받았다 — 이때만 시계가 온다.
                    out.as_of = body.get("as_of")
                    return out
                if len(out.rows) >= max_rows:
                    out.truncated = True
                    out.next = str(current)
                    return out
    except httpx.HTTPError as caught:
        raise AppError(
            code("DATASOURCES", 38), f"코어 창구에 닿지 못했습니다: {caught}", status=502
        ) from caught
    out.truncated = True
    out.next = str(current) if current else None
    return out


def fetch_sp_core(
    *,
    base_url: str,
    type_slug: str,
    since: str,
    auth: Auth,
    page_size: int,
    max_rows: int,
    transport: httpx.BaseTransport | None,
    cursor: str = "",
) -> Fetched:
    """형제 설치의 코어 창구에서 **지난번 이후**를 받는다."""
    if not type_slug.strip():
        raise AppError(
            code("DATASOURCES", 36),
            "가져올 코어 타입을 적으세요 — 상대의 `GET /api/core` 가 목록을 줍니다.",
            status=422,
        )
    return _core_pages(
        url=f"{base_url.rstrip('/')}/core/{type_slug.strip()}",
        what="/api/core/<타입>",
        since=since,
        cursor=cursor,
        auth=auth,
        page_size=page_size,
        max_rows=max_rows,
        transport=transport,
        shape=core_row,
    )


#: 「여기까지 봤다」 만 묻는 `since` — 이보다 뒤에 바뀐 것은 없으니 빈 쪽과 시계만 온다.
FAR_SINCE = "9999-12-31T00:00:00Z"


def core_watermark(
    *, base_url: str, type_slug: str, auth: Auth, transport: httpx.BaseTransport | None
) -> str | None:
    """상대의 **지금 시계**(`as_of`) — 빈 쪽 하나를 청해 받는다. 못 받으면 None.

    끊은 자리에서 다음 차례가 잇는 동안(`services._resume`) 상대에서 바뀌고 지워진 것을
    놓치지 않으려고, **처음 끊었을 때의 시계**를 적어 두었다가 다 받은 뒤 거기서 다시 받는다.
    커서는 지나간 자리를 다시 안 보므로, 그 사이 늦게 커밋된 적재 · 받은 뒤에 지워진 것은
    커서만으로는 영영 안 온다. 상대의 `as_of` 는 도는 적재보다 앞서지 않는다
    (`coreapi.services.watermark`) — 그 시계 뒤의 것은 다음 증분이 다시 준다."""
    try:
        got = fetch_sp_core(
            base_url=base_url,
            type_slug=type_slug,
            since=FAR_SINCE,
            auth=auth,
            page_size=1,
            max_rows=1,
            transport=transport,
        )
    except AppError:
        return None
    return got.as_of or None


#: 코어 선 한 줄의 봉투 — 그대로 관계 적재의 칸 이름이 된다(`src` · `relation` · `dst`).
CORE_EDGE = ("src", "relation", "dst", "dst_type", "evidence_note", "deleted")


def core_edge(item: dict[str, Any]) -> dict[str, Any]:
    """선 한 줄을 평평하게 — 관계에 붙은 속성을 펴고 봉투를 위에 얹는다.

    봉투의 이름이 **관계 적재의 칸 이름과 같다**(`src` · `relation` · `dst` ·
    `evidence_note`) — 그래서 대응 표가 필요 없다. 코어 창구가 그 약속을 지킨다.
    """
    values = item.get("properties")
    out: dict[str, Any] = dict(values) if isinstance(values, dict) else {}
    for name in CORE_EDGE:
        if name in item:
            out[name] = item[name]
    return out


def fetch_sp_core_relations(
    *,
    base_url: str,
    type_slug: str,
    since: str,
    auth: Auth,
    page_size: int,
    max_rows: int,
    transport: httpx.BaseTransport | None,
    cursor: str = "",
) -> Fetched:
    """형제 설치의 **선**을 받는다 — `/api/core/<타입>/relations`.

    객체 쪽과 같은 규칙(`since` · `next` · `as_of`)이고, 끊긴 선은 `deleted: true` 로 온다.
    상대가 **`reset: true`** 를 주면(그 시각부터는 끊긴 선을 알려 줄 수 없다 — 무덤의 보관
    기간이 지났다) 받은 것을 버리고 `reset` 만 표시해 돌려준다: 부르는 쪽이 시계를 비우고
    처음부터 다시 받는다. 빈 쪽을 「바뀐 것 없음」 으로 읽으면 이미 끊긴 선을 영영 들고 있다.
    """
    return _core_pages(
        url=f"{base_url.rstrip('/')}/core/{type_slug.strip()}/relations",
        what="/api/core/<타입>/relations",
        since=since,
        cursor=cursor,
        auth=auth,
        page_size=page_size,
        max_rows=max_rows,
        transport=transport,
        shape=core_edge,
    )


def _raise_for_core(response: httpx.Response) -> None:
    """**상대의 말을 그대로 옮긴다.** 우리가 다시 쓴 문구는 상대 쪽 원인을 지운다."""
    if response.status_code < 400:
        return
    said = ""
    try:
        body = response.json()
        said = str((body.get("error") or {}).get("message") or "")
    except (ValueError, AttributeError):
        said = response.text[:300]
    if response.status_code in (401, 403):
        raise AppError(
            code("DATASOURCES", 11),
            f"인증이 거절됐습니다 (HTTP {response.status_code}). 상대가 발급한 토큰인지, "
            f"범위가 `core:read` 인지 확인하세요. 상대의 말: {said}",
            status=502,
        )
    if response.status_code == 404:
        raise AppError(
            code("DATASOURCES", 39),
            f"그 설치에 열려 있는 코어 타입이 아닙니다. 상대의 말: {said}",
            status=502,
        )
    raise AppError(code("DATASOURCES", 12), f"HTTP {response.status_code}: {said}", status=502)


def core_catalog(
    *, base_url: str, auth: Auth, transport: httpx.BaseTransport | None
) -> dict[str, Any]:
    """상대가 **무엇을 열어 뒀나.** 대응을 손으로 옮겨 적지 않게 하는 자리."""
    try:
        with _core_client(auth, transport) as client:
            response = client.get(f"{base_url.rstrip('/')}/core")
            _raise_for_core(response)
            body = _core_body(response, "/api/core")
    except httpx.HTTPError as caught:
        raise AppError(
            code("DATASOURCES", 38), f"코어 창구에 닿지 못했습니다: {caught}", status=502
        ) from caught
    if not isinstance(body, dict) or not isinstance(body.get("types"), list):
        raise AppError(
            code("DATASOURCES", 37),
            "코어 카탈로그를 읽지 못했습니다 — 주소가 그 설치의 `/api/core` 인지 확인하세요.",
            status=502,
        )
    return body


# --- REST -----------------------------------------------------------------------

#: 쪽 넘김 방식.
#:   none    한 번에 전부
#:   page    `?page=1&page_size=N` — 쪽이 덜 차면 끝
#:   offset  `?offset=0&limit=N`
#:   cursor  응답의 `cursor_path` 에 다음 커서(또는 다음 URL)가 온다 — 비면 끝
REST_PAGING = ("none", "page", "offset", "cursor")


def _dig(body: Any, path: str) -> Any:
    current = body
    for part in [one for one in path.replace("/", ".").split(".") if one]:
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def fetch_rest(
    *,
    base_url: str,
    path: str,
    options: dict[str, Any],
    auth: Auth,
    page_size: int,
    max_rows: int,
    transport: httpx.BaseTransport | None,
) -> Fetched:
    """JSON REST. 행이 있는 자리와 쪽 넘김을 정의가 말한다 — API 마다 다르고 맞힐 수 없다."""
    rows_path = str(options.get("rows_path") or "")
    paging = str(options.get("paging") or "none")
    if paging not in REST_PAGING:
        raise AppError(
            code("DATASOURCES", 31),
            f"REST 쪽 넘김은 {', '.join(REST_PAGING)} 중 하나여야 합니다: {paging}",
            status=422,
        )
    page_param = str(options.get("page_param") or "page")
    size_param = str(options.get("size_param") or "page_size")
    offset_param = str(options.get("offset_param") or "offset")
    cursor_param = str(options.get("cursor_param") or "cursor")
    cursor_path = str(options.get("cursor_path") or "next")
    start_page = int(options.get("start_page") or 1)
    fixed = {str(k): str(v) for k, v in (options.get("params") or {}).items()}

    url = (
        path
        if path.startswith(("http://", "https://"))
        else f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    )
    out = Fetched()
    page_no = start_page
    offset = 0
    cursor: str | None = None
    next_url: str | None = url
    try:
        with httpx.Client(
            timeout=odata.TIMEOUT_SECONDS,
            transport=transport,
            headers={"Accept": "application/json", **auth.headers()},
            auth=auth.basic(),
            follow_redirects=True,
        ) as client:
            while next_url and out.pages < MAX_PAGES:
                params = dict(fixed)
                if paging == "page":
                    params[page_param] = str(page_no)
                    params[size_param] = str(page_size)
                elif paging == "offset":
                    params[offset_param] = str(offset)
                    params[size_param] = str(page_size)
                elif paging == "cursor":
                    params[size_param] = str(page_size)
                    if cursor and not cursor.startswith(("http://", "https://")):
                        params[cursor_param] = cursor
                response = client.get(next_url, params=params or None)
                if response.status_code in (401, 403):
                    raise AppError(
                        code("DATASOURCES", 11),
                        f"인증이 거절됐습니다 (HTTP {response.status_code}). "
                        "인증 방식과 비밀을 확인하세요.",
                        status=502,
                    )
                if response.status_code >= 400:
                    raise AppError(
                        code("DATASOURCES", 12),
                        f"HTTP {response.status_code}: {response.text[:300]}",
                        status=502,
                    )
                try:
                    body = response.json()
                except ValueError:
                    raise AppError(
                        code("DATASOURCES", 10), "응답이 JSON 이 아닙니다.", status=502
                    ) from None
                rows = _dig(body, rows_path) if rows_path else body
                if not isinstance(rows, list):
                    raise AppError(
                        code("DATASOURCES", 10),
                        f"응답에서 행의 배열을 찾을 수 없습니다 (rows_path={rows_path!r}). "
                        "행이 있는 자리를 적으세요 — 예: items, data.results.",
                        status=502,
                    )
                out.pages += 1
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if len(out.rows) >= max_rows:
                        out.truncated = True
                        return out
                    out.rows.append(row)
                if paging == "none":
                    next_url = None
                elif paging == "cursor":
                    found = _dig(body, cursor_path)
                    cursor = str(found) if found else None
                    if not cursor:
                        next_url = None
                    elif cursor.startswith(("http://", "https://")):
                        next_url = cursor
                        fixed = {}
                    # 커서가 URL 이 아니면 같은 주소에 파라미터로 붙인다.
                elif len(rows) < page_size:
                    next_url = None
                else:
                    page_no += 1
                    offset += page_size
            if next_url:
                # 쪽 수 상한에서 멈췄다 — 끊김으로 말한다(OData 와 같다).
                out.truncated = True
    except httpx.HTTPError as caught:
        raise AppError(
            code("DATASOURCES", 13), f"연결하지 못했습니다: {str(caught)[:300]}", status=502
        ) from None
    return out


# --- 파일 -----------------------------------------------------------------------

FILE_FORMATS = ("csv", "xlsx", "json")


def _format_of(location: str, options: dict[str, Any]) -> str:
    declared = str(options.get("format") or "").lower()
    if declared:
        if declared not in FILE_FORMATS:
            raise AppError(
                code("DATASOURCES", 32),
                f"파일 형식은 {', '.join(FILE_FORMATS)} 중 하나여야 합니다: {declared}",
                status=422,
            )
        return declared
    lowered = location.lower().split("?", 1)[0]
    for candidate in FILE_FORMATS:
        if lowered.endswith(f".{candidate}"):
            return candidate
    if lowered.endswith(".xlsm"):
        return "xlsx"
    raise AppError(
        code("DATASOURCES", 32),
        "파일 형식을 알 수 없습니다 — 확장자가 .csv/.xlsx/.json 이 아니면 형식을 적으세요.",
        status=422,
    )


def _read_bytes(location: str, auth: Auth, transport: httpx.BaseTransport | None) -> bytes:
    if location.startswith(("http://", "https://")):
        try:
            with httpx.Client(
                timeout=odata.TIMEOUT_SECONDS,
                transport=transport,
                headers=auth.headers(),
                auth=auth.basic(),
                follow_redirects=True,
            ) as client:
                response = client.get(location)
        except httpx.HTTPError as caught:
            raise AppError(
                code("DATASOURCES", 13),
                f"연결하지 못했습니다: {str(caught)[:300]}",
                status=502,
            ) from None
        if response.status_code >= 400:
            raise AppError(
                code("DATASOURCES", 12),
                f"HTTP {response.status_code}: {response.text[:300]}",
                status=502,
            )
        return response.content

    # 로컬 파일 — 허용 폴더 아래만. 아무 경로나 읽게 두면 이 화면이 서버의 모든 파일을 읽는
    # 문이 된다.
    base = get_settings().datasource_dir
    if base is None:
        raise AppError(
            code("DATASOURCES", 33),
            "서버에 파일 폴더(DATASOURCE_DIR)가 정해져 있지 않아 로컬 파일은 읽지 않습니다. "
            "URL 로 주거나 운영자가 .env 에 DATASOURCE_DIR 을 적어야 합니다.",
            status=422,
        )
    root = base.resolve()
    target = (root / location).resolve()
    if root not in target.parents and target != root:
        raise AppError(
            code("DATASOURCES", 33),
            f"파일은 {root} 아래에서만 읽습니다: {location}",
            status=422,
        )
    if not target.is_file():
        raise AppError(code("DATASOURCES", 34), f"파일이 없습니다: {location}", status=502)
    return target.read_bytes()


def _rows_from_xlsx(raw: bytes, sheet: str) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - requirements 에 있다
        raise AppError(
            code("DATASOURCES", 35), "openpyxl 이 없어 Excel 을 읽지 못합니다.", status=500
        ) from None
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        worksheet = book[sheet] if sheet else book.worksheets[0]
    except KeyError:
        raise AppError(
            code("DATASOURCES", 34),
            f"시트가 없습니다: {sheet}. 있는 것: {', '.join(book.sheetnames)}",
            status=422,
        ) from None
    rows_iter = worksheet.iter_rows(values_only=True)
    header = [str(cell).strip() if cell is not None else "" for cell in next(rows_iter, ())]
    out: list[dict[str, Any]] = []
    for values in rows_iter:
        if values is None or all(cell is None for cell in values):
            continue
        out.append({name: cell for name, cell in zip(header, values, strict=False) if name})
    return out


def parse_file(raw: bytes, *, fmt: str, sheet: str = "") -> list[dict[str, Any]]:
    """바이트 → 행. CSV·JSON 은 일괄 입력와 같은 규칙(BOM 벗김, CP949 도 읽음,
    `{"rows": [...]}` 허용)."""
    if fmt == "xlsx":
        return _rows_from_xlsx(raw, sheet)
    try:
        text = tabular.decode_text(raw)
    except tabular.TabularError as caught:
        raise AppError(code("DATASOURCES", 10), str(caught), status=502) from None
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as caught:
            raise AppError(
                code("DATASOURCES", 10), f"JSON 을 읽을 수 없습니다: {caught}", status=502
            ) from None
        rows = (
            data.get("rows", data.get("items", data.get("value")))
            if isinstance(data, dict)
            else data
        )
        if not isinstance(rows, list):
            raise AppError(
                code("DATASOURCES", 10),
                'JSON 은 객체의 배열이거나 {"rows": [...]} 여야 합니다.',
                status=502,
            )
        return [one for one in rows if isinstance(one, dict)]
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or "").strip(): v for k, v in row.items() if k is not None} for row in reader]


def fetch_file(
    *,
    location: str,
    options: dict[str, Any],
    auth: Auth,
    max_rows: int,
    transport: httpx.BaseTransport | None,
) -> Fetched:
    fmt = _format_of(location, options)
    raw = _read_bytes(location, auth, transport)
    rows = parse_file(raw, fmt=fmt, sheet=str(options.get("sheet") or ""))
    out = Fetched(pages=1)
    if len(rows) > max_rows:
        out.truncated = True
        rows = rows[:max_rows]
    out.rows = rows
    return out
