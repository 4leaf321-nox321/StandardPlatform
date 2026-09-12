"""행을 읽어 오는 조각 — **소스 종류마다 하나.** 나머지 파이프라인은 종류를 모른다.

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
) -> Fetched:
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
    """바이트 → 행. CSV·JSON 은 파일 가져오기와 같은 규칙(BOM 벗김, `{"rows": [...]}` 허용)."""
    if fmt == "xlsx":
        return _rows_from_xlsx(raw, sheet)
    text = raw.decode("utf-8-sig")
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
