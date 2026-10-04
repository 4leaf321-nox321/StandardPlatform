"""RA 보고서를 기록으로 — 데이터 소스 종류 `ra_reports`(ADR 0018).

ReportArchive(RA)의 발행 보고서 피드(`GET /api/feeds/published-reports`)를 쌍둥이의
「보고서」 기록으로 쌓는다. 조직(게시판) 하나를 고르면 그 하위 전부, 발행본만, 본문까지.
에이전트(Claude + SP MCP)가 보고서와 개발모델 · 과제 · 부서를 한 온톨로지로 묻게 하려는 것이다.

일반 REST 소스와 다른 점:

    칸 대응    사람이 적지 않는다 — 틀(`FIELDS`)의 칸 키가 약속이다. 타입에 없는 칸은
               건너뛴다
    쪽 넘김    RA 의 커서는 두 값(`updated_since` · `after_id`)이다
    쌓기       증분(저장한 커서에서 5분 겹쳐) + 하루 한 번 전량(본문 없이 — 태그만 바뀐 것을
               메운다)
    원본 상태  전량 대조에서 안 온 것은 「원본에서 내려감」 — **지우지도 사용 중지하지도
               않는다**
    다시 찾기  외부 식별자와 `RA-<번호>` 로만 — 이름으로 붙이지 않는다(같은 제목의 보고서가
               흔하다)
    소유 부서  RA 작성 부서와 slug 가 같은 SP 부서, 없으면 소스의 기본 부서

나머지(계획 → 적용 · 검증 · 감사 · 별칭 `source:<slug>`)는 다른 소스와 같은 길이다
(`services.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.datasources import odata
from app.modules.datasources.models import DataSource
from app.modules.datasources.odata import Auth, Fetched
from app.modules.objects import aliases, bulk
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.objects.services import audit_state, properties_of
from app.modules.ontology import importer
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.errors import AppError, code

KIND = "ra_reports"
FEED_PATH = "/api/feeds/published-reports"
BOARDS_PATH = "/api/workspaces"

#: 피드의 단계 — `finalized`(발행 버튼을 누른 것, 기본) · `published`(게시된 것 전부).
PHASES = ("finalized", "published")
#: 피드 한 쪽의 상한(RA 가 정한 것).
PAGE_MAX = 500
#: 증분을 이만큼 겹쳐 읽는다 — RA 의 커밋 순서 · 시계 때문에 경계의 한두 건이 늦을 수 있다.
#: 고치기는 멱등이라 겹쳐 읽어도 해롭지 않다.
OVERLAP = timedelta(minutes=5)
#: 한 번의 전량 대조에서 이보다 많이(가진 것의 비율) 「내려감」 이 되면 표시하지 않고 멈춘다 —
#: RA 계정의 권한이 바뀌었거나 조직을 잘못 골랐을 때 전부가 내려간 것처럼 보이는 것을 막는다.
GONE_LIMIT = 0.5
#: 그 비율을 보지 않는 작은 수 — 보고서 몇 건짜리 조직에서 두 건이 내려간 것은 정상이다.
GONE_FLOOR = 10
#: 전량 대조의 간격 — 태그만 바뀐 것(RA 는 `updated_at` 을 안 움직인다)과 내려간 것을 잡는다.
RECONCILE_EVERY = timedelta(hours=24)
MAX_PAGES = 1_000

KEY_PREFIX = "RA-"
REF_PREFIX = "ref_"
ORIGIN_LIVE = "게시 중"
ORIGIN_GONE = "원본에서 내려감"
PHASE_LABELS = {"finalized": "발행", "reviewing": "검토 중", "drafting": "작성 중"}

#: 틀의 칸 — (키, 이름, 종류, 여러 값, 고를 값). **키가 약속이다** — 변환(`to_row`)이 이
#: 키로 값을 넣고, 타입에 그 칸이 없으면 건너뛴다(손으로 만든 타입도 받는다).
FIELDS: tuple[tuple[str, str, str, bool, tuple[str, ...] | None], ...] = (
    ("ra_id", "RA 번호", "text", False, None),
    ("url", "원본 주소", "url", False, None),
    ("report_date", "보고일", "date", False, None),
    ("phase", "단계", "enum", False, tuple(PHASE_LABELS.values())),
    ("report_type", "보고서 유형", "text", False, None),
    ("revision", "개정", "number", False, None),
    ("ra_workspace", "작성 부서", "text", False, None),
    ("boards", "게시판", "text", True, None),
    ("author", "작성자", "text", False, None),
    ("tags", "태그", "text", True, None),
    ("ra_tags", "RA 태그", "text", True, None),
    ("body", "본문", "text_long", False, None),
    ("origin_state", "원본 상태", "enum", False, (ORIGIN_LIVE, ORIGIN_GONE)),
    ("removed_on", "원본에서 내려간 날", "date", False, None),
)

#: 칸의 설명(`help`) — 사람과 에이전트가 이 칸을 **무엇으로 읽어야 하는지**. RA 의 본문은
#: 검색용 평문이라 첨부 파일 이름이 섞여 있다(RA 회신, 2026-10-04) — 그것을 본문 내용으로
#: 옮기지 않게 적어 둔다.
HELP = {
    "body": (
        "RA 의 검색용 평문 — 제목 · 본문 글과 첨부 파일 이름이 섞여 있습니다. 1MB 를 넘으면 "
        "앞부분만 있고 끝에 잘렸다고 적힙니다."
    ),
    "origin_state": (
        "하루 한 번 전량 대조에서 RA 에 없으면 「원본에서 내려감」 — 삭제 · 게시 취소 · 발행 "
        "취소 · 권한 변경 중 무엇인지는 알 수 없습니다."
    ),
    "ra_tags": "이 쌍둥이에 없는 축 태그 — 「종류: 값」.",
}

#: 변환이 행에 남기는 숨은 자리 — bulk 로 넘기기 전에 뗀다.
_OWNER = "_owner"
_ID = "_ra_id"
_ENTITIES = "_entities"


def _refuse(number: int, message: str, status: int = 422) -> AppError:
    return AppError(code("DATASOURCES", number), message, status=status)


# --- 설정 ------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    board: str
    """RA 조직(게시판) slug — 그 게시판과(하위 포함이면) 그 아래에 게시된 것."""
    include_descendants: bool
    phase: str
    include_text: bool


def options_of(raw: dict[str, Any] | None) -> Options:
    """소스의 `options` → 설정. 틀린 값은 이유를 말하고 거절한다."""
    raw = raw or {}
    unknown = sorted(set(raw) - {"board", "include_descendants", "phase", "include_text"})
    if unknown:
        raise _refuse(50, f"RA 보고서 소스에 없는 설정입니다: {', '.join(unknown)}")
    phase = str(raw.get("phase") or "finalized")
    if phase not in PHASES:
        raise _refuse(50, f"단계는 {' · '.join(PHASES)} 중 하나입니다: {phase}")
    return Options(
        board=str(raw.get("board") or "").strip(),
        include_descendants=bool(raw.get("include_descendants", True)),
        phase=phase,
        include_text=bool(raw.get("include_text", True)),
    )


def _url(base_url: str, path: str) -> str:
    """RA 루트 + 경로. 루트를 `…/api` 까지 적었어도 같은 곳으로 간다."""
    base = base_url.rstrip("/")
    if base.endswith("/api") and path.startswith("/api/"):
        path = path[len("/api") :]
    return f"{base}{path}"


# 머리글은 latin-1 만 실린다 — 한글이 섞인 토큰은 보내기 전에 깨진다.
BAD_TOKEN = (
    "토큰에 영문 · 숫자 외의 글자가 있습니다 — RA 에서 발급한 액세스 토큰을 그대로 입력하세요."
)


def _client(auth: Auth, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        timeout=odata.TIMEOUT_SECONDS,
        transport=transport,
        headers={"Accept": "application/json", **auth.headers()},
        auth=auth.basic(),
        follow_redirects=True,
    )


def _data(response: httpx.Response) -> Any:
    """RA 봉투 `{success, data}` 를 벗긴다 — 실패는 **무엇을 고치면 되는지** 말한다."""
    if response.status_code in (401, 403):
        raise _refuse(
            51,
            f"RA 가 토큰을 거절했습니다(HTTP {response.status_code}) — RA 의 읽기 계정 "
            "액세스 토큰인지, 그 계정이 선택한 조직의 게시판을 볼 수 있는지 확인하세요.",
            status=502,
        )
    if response.status_code == 404:
        raise _refuse(
            52,
            "RA 에 이 창구가 없습니다(HTTP 404) — 주소가 RA 의 루트인지, 발행 보고서 피드가 "
            "있는 RA 버전인지 확인하세요. 선택한 조직이 RA 에 없어도 404 입니다.",
            status=502,
        )
    if response.status_code >= 400:
        raise _refuse(53, f"RA HTTP {response.status_code}: {response.text[:300]}", status=502)
    try:
        body = response.json()
    except ValueError:
        raise _refuse(53, "RA 응답이 JSON 이 아닙니다.", status=502) from None
    if not isinstance(body, dict) or body.get("success") is False or "data" not in body:
        raise _refuse(53, f"RA 응답의 모양이 다릅니다: {str(body)[:200]}", status=502)
    return body["data"]


# --- 언제 무엇을 받나 ---------------------------------------------------------------


def mode_of(source: DataSource, now: datetime | None = None) -> str:
    """`initial`(커서가 없다 — 본문까지 전량) · `reconcile`(하루 한 번 — 본문 없이 전량,
    대조) · `incremental`(지난 커서에서 5분 겹쳐)."""
    if not source.since_mark:
        return "initial"
    now = now or datetime.now(UTC)
    if source.reconciled_at is None or source.reconciled_at + RECONCILE_EVERY <= now:
        return "reconcile"
    return "incremental"


def _decode(mark: str) -> tuple[datetime, int]:
    stamp, _, last = mark.partition("|")
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError as caught:
        raise _refuse(
            54,
            f"저장된 수신 기준 시각을 읽지 못했습니다: {mark!r} — 소스의 수신 기준 시각을 "
            "비우면 처음부터 다시 수신합니다.",
        ) from caught
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when, int(last or 0)


def _encode(cursor: dict[str, Any]) -> str:
    return f"{cursor['updated_since']}|{int(cursor.get('after_id') or 0)}"


def fetch(
    source: DataSource,
    *,
    auth: Auth,
    mode: str,
    page_size: int,
    max_rows: int,
    transport: httpx.BaseTransport | None,
) -> Fetched:
    """피드를 끝까지(상한 안에서) 읽어 평평한 행으로. 끝까지 받았을 때만 `as_of`(다음 커서)를
    채운다 — 중간에 끊긴 채 커서를 옮기면 남은 것을 영영 안 받는다."""
    opts = options_of(source.options)
    if not opts.board:
        raise _refuse(
            55,
            "RA 조직을 선택하세요 — 그 조직과 하위 조직의 게시판에 게시된 보고서를 "
            "수신합니다.",
        )
    params: dict[str, str] = {
        "board": opts.board,
        "include_descendants": "true" if opts.include_descendants else "false",
        "phase": opts.phase,
        "limit": str(max(1, min(page_size, PAGE_MAX))),
    }
    # 전량 대조는 본문 없이 — 본문이 바뀌면 `updated_at` 이 움직여 증분이 잡는다. 빈 칸은 「안
    # 건드림」 이라 본문 없이 받아도 우리 본문은 그대로다.
    if opts.include_text and mode != "reconcile":
        params["include_text"] = "true"
    old_mark: datetime | None = None
    if mode == "incremental":
        old_mark, _ = _decode(source.since_mark)
        params["updated_since"] = (old_mark - OVERLAP).isoformat()
    out = Fetched(full=mode != "incremental")
    last: dict[str, Any] | None = None
    url = _url(source.base_url, FEED_PATH)
    try:
        with _client(auth, transport) as client:
            while out.pages < MAX_PAGES:
                data = _data(client.get(url, params=params))
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    raise _refuse(53, "RA 피드에 items 가 없습니다.", status=502)
                out.pages += 1
                for item in items:
                    if isinstance(item, dict):
                        out.rows.append(flatten(item, base_url=source.base_url))
                if len(out.rows) >= max_rows:
                    out.truncated = True
                    out.rows = out.rows[:max_rows]
                    return out
                cursor = data.get("next")
                if isinstance(cursor, dict) and cursor.get("updated_since"):
                    last = cursor
                if not data.get("has_more") or last is None:
                    break
                params["updated_since"] = str(last["updated_since"])
                params["after_id"] = str(int(last.get("after_id") or 0))
            else:
                out.truncated = True
                return out
    except httpx.HTTPError as caught:
        raise _refuse(
            56, f"RA 에 연결하지 못했습니다: {str(caught)[:300]}", status=502
        ) from None
    except UnicodeEncodeError:
        raise _refuse(51, BAD_TOKEN) from None
    if last is not None:
        mark = _encode(last)
        # 겹쳐 읽은 쪽만 왔으면 커서가 뒤로 갈 수 있다 — 앞선 것을 지킨다.
        if old_mark is None or _decode(mark)[0] >= old_mark:
            out.as_of = mark
    return out


# --- 행 바꾸기 --------------------------------------------------------------------


#: RA 가 본문을 한 건 1MB 에서 자르면(`text_truncated`) 끝에 붙인다 — 에이전트가 앞부분을
#: 전부로 읽지 않게.
CUT_NOTE = "\n\n[RA 피드의 한 건 상한(1MB)에서 잘렸습니다 — 전문은 원본 주소에서 봅니다.]"


def absolute(base_url: str, url: object) -> str | None:
    """원문 주소 — RA 에 기준 주소(`APP_BASE_URL`)가 비어 있으면 `/w/…` 상대 경로가 온다.
    그때는 소스의 RA 루트를 붙인다(루트를 `…/api` 까지 적었어도)."""
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith(("http://", "https://")) or not url.startswith("/"):
        return url
    base = base_url.strip().rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return f"{base}{url}"


def flatten(item: dict[str, Any], *, base_url: str = "") -> dict[str, Any]:
    """피드 항목 하나 → 틀의 칸 키로 된 평평한 행(숨은 자리 `_…` 포함)."""
    rid = item.get("id")
    workspace: dict[str, Any] = (
        item["workspace"] if isinstance(item.get("workspace"), dict) else {}
    )
    author: dict[str, Any] = item["author"] if isinstance(item.get("author"), dict) else {}
    phase = str(item.get("phase") or "")
    boards = [
        str(one.get("name") or one.get("slug"))
        for one in item.get("boards") or []
        if isinstance(one, dict) and (one.get("name") or one.get("slug"))
    ]
    entities: list[tuple[str | None, str | None, str]] = []
    for one in item.get("entities") or []:
        if not isinstance(one, dict):
            continue
        sp = one.get("sp") if isinstance(one.get("sp"), dict) else None
        kind = one.get("type_label") or one.get("type") or "태그"
        shown = f"{kind}: {one.get('value') or ''}"
        entities.append(
            (
                str(sp["type"]) if sp and sp.get("type") else None,
                str(sp["key"]) if sp and sp.get("key") else None,
                shown.strip(),
            )
        )
    row: dict[str, Any] = {
        _ID: "" if rid is None else str(rid),
        _OWNER: workspace.get("slug"),
        _ENTITIES: entities,
        "key": f"{KEY_PREFIX}{rid}" if rid is not None else "",
        "label": str(item.get("title") or "").strip(),
        "ra_id": "" if rid is None else str(rid),
        "url": absolute(base_url, item.get("url")),
        "report_date": item.get("report_date"),
        "phase": PHASE_LABELS.get(phase, phase),
        "report_type": item.get("report_type"),
        "revision": item.get("revision"),
        "ra_workspace": workspace.get("name") or workspace.get("slug"),
        "boards": boards,
        "author": author.get("name"),
        "tags": [str(one) for one in item.get("tags") or [] if str(one).strip()],
        "origin_state": ORIGIN_LIVE,
    }
    # 본문은 **왔을 때만** 싣는다 — 안 온 것(전량 대조 · RA 백필 전)으로 우리 본문을 지우지
    # 않는다.
    if isinstance(item.get("text"), str):
        row["body"] = item["text"] + (CUT_NOTE if item.get("text_truncated") else "")
    return row


@dataclass
class Converted:
    external_id: str
    row: dict[str, Any]
    owner_slug: str | None
    error: str | None = None


def to_row(raw: dict[str, Any], defs: list[PropertyDef]) -> Converted:
    """평평한 행 → bulk 의 행. 타입에 없는 칸은 건너뛰고, 축 태그는 `ref_<SP 타입>` 칸이 있으면
    그 칸에 식별자로, 없으면 「RA 태그」 에 글자로 남긴다(잃지 않는다)."""
    external = str(raw.get(_ID) or "")
    owner = raw.get(_OWNER)
    if not external:
        return Converted("", {}, None, "RA 번호(id)가 비어 있습니다.")
    if not raw.get("label"):
        return Converted(external, {}, None, "제목이 비어 있습니다.")
    by_key = {one.key: one for one in defs}
    out: dict[str, Any] = {"key": raw["key"], "label": raw["label"]}
    for key, *_ in FIELDS:
        if key not in by_key or key not in raw:
            continue
        value = raw[key]
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            value = bulk.MULTI_SEP.join(str(one) for one in value)
        out[key] = value
    refs: dict[str, list[str]] = {}
    loose: list[str] = []
    for sp_type, sp_key, shown in raw.get(_ENTITIES) or []:
        target = by_key.get(f"{REF_PREFIX}{sp_type}") if sp_type else None
        if target is not None and target.data_type == "object_ref" and sp_key:
            refs.setdefault(target.key, []).append(sp_key)
        else:
            loose.append(shown)
    for key, keys in refs.items():
        out[key] = bulk.MULTI_SEP.join(dict.fromkeys(keys))
    if loose and "ra_tags" in by_key:
        existing = [one for one in str(out.get("ra_tags") or "").split(bulk.MULTI_SEP) if one]
        out["ra_tags"] = bulk.MULTI_SEP.join(dict.fromkeys([*existing, *loose]))
    return Converted(external, out, str(owner) if owner else None)


#: RA 가 주인인 여럿 값 칸 — 축 참조 칸 `ref_<타입>` 도 그렇다.
OWNED_MULTI = ("boards", "tags", "ra_tags")


def clear_emptied(defs: list[PropertyDef], rows: list[Converted]) -> None:
    """피드가 비워 보낸 여럿 값 칸은 **우리도 비운다**(`\\null`). 일괄 입력은 빈 값을 「안
    건드림」 으로 읽어서, RA 에서 태그를 다 뺀 보고서가 옛 태그로 남는다 — RA 가 `sp` 를 채워
    글 태그(`ra_tags`)가 모두 참조 칸으로 옮겨 갈 때도 그렇다. `prune_refs` 뒤에 부른다(그
    전이면 `\\null` 을 식별자로 묻는다)."""
    owned = [
        one.key
        for one in defs
        if one.key in OWNED_MULTI
        or (one.key.startswith(REF_PREFIX) and one.data_type == "object_ref")
    ]
    for one in rows:
        for key in owned:
            if key not in one.row:
                one.row[key] = bulk.NULL_MARK


def prune_refs(db: Session, defs: list[PropertyDef], rows: list[Converted]) -> int:
    """참조 칸에 **이 쌍둥이에 있는 식별자만** 남긴다 — 없는 것은 「RA 태그」 에 글자로 옮긴다.

    일괄 입력의 「못 찾는 참조 비우기」 는 여러 값 칸에서 하나만 못 찾아도 그 칸을 통째로
    비운다 — 모델 태그 셋 중 하나가 아직 허브에 없으면 나머지 둘의 연결까지 잃는다. 대상
    타입마다 식별자를 한 번에 물어 미리 가른다. 옮긴 수를 돌려준다."""
    by_key = {one.key: one for one in defs}
    wanted: dict[str, set[str]] = {}
    for one in rows:
        for key, value in one.row.items():
            target = by_key.get(key)
            if (
                target is None
                or target.data_type != "object_ref"
                or not key.startswith(REF_PREFIX)
            ):
                continue
            wanted.setdefault(target.ref_type_slug or "", set()).update(
                str(value).split(bulk.MULTI_SEP)
            )
    known: dict[str, set[str]] = {}
    for slug, keys in wanted.items():
        type_ids = [
            row.id for row in db.scalars(select(ObjectType).where(ObjectType.slug == slug))
        ]
        known[slug] = set(
            db.scalars(
                select(ObjectInstance.key).where(
                    ObjectInstance.type_id.in_(type_ids),
                    ObjectInstance.key.in_(sorted(keys)),
                    ObjectInstance.deleted_at.is_(None),
                )
            )
        )
    moved = 0
    for one in rows:
        loose: list[str] = []
        for key in [k for k in one.row if k.startswith(REF_PREFIX) and k in by_key]:
            target = by_key[key]
            if target.data_type != "object_ref":
                continue
            values = str(one.row[key]).split(bulk.MULTI_SEP)
            have = known.get(target.ref_type_slug or "", set())
            kept = [value for value in values if value in have]
            loose.extend(f"{target.label}: {value}" for value in values if value not in have)
            if kept:
                one.row[key] = bulk.MULTI_SEP.join(kept)
            else:
                one.row.pop(key)
        if loose:
            moved += len(loose)
            if "ra_tags" in by_key:
                existing = [
                    tag
                    for tag in str(one.row.get("ra_tags") or "").split(bulk.MULTI_SEP)
                    if tag
                ]
                one.row["ra_tags"] = bulk.MULTI_SEP.join(dict.fromkeys([*existing, *loose]))
    return moved


def owners(db: Session, slugs: set[str]) -> dict[str, uuid.UUID]:
    """RA 부서 slug → 같은 slug 의 SP 부서. SP 부서를 RA 「부서 정보 내보내기」 로 만들었다면
    같다(`workspaces` 의 붙여 넣기)."""
    if not slugs:
        return {}
    return {
        row.slug: row.id
        for row in db.scalars(select(Workspace).where(Workspace.slug.in_(sorted(slugs))))
    }


# --- 원본 상태 --------------------------------------------------------------------


def reconcile(
    db: Session,
    actor: User,
    object_type: ObjectType,
    source: DataSource,
    seen: set[str],
    *,
    full: bool,
    today: date | None = None,
) -> dict[str, int]:
    """이 소스가 남긴 기록의 **원본 상태**를 맞춘다 — 지우지도 사용 중지하지도 않는다.

    - 이번에 온 것에 「내려간 날」 이 남아 있으면 지운다(다시 게시됐다 — 상태는 행이
      「게시 중」 으로 이미 돌렸다).
    - **전량을 받았을 때만** 안 온 것을 「원본에서 내려감」 · 그날로. 증분에서 안 온 것은 그냥
      안 바뀐 것이다.

    사유(삭제 · 게시취소 · 발행취소 · 범위 밖 이동 · 계정 권한)는 피드로 가를 수 없어 하나로
    말한다. 타입에 `origin_state` 칸이 없으면(손으로 만든 타입) 아무것도 안 한다."""
    keys = {one.key for one in properties_of(db, object_type.id)}
    if "origin_state" not in keys:
        return {"gone": 0, "back": 0}
    today = today or datetime.now(UTC).date()
    kind = aliases.source_kind(source.slug)
    gone = back = 0
    rows = db.execute(
        select(ObjectInstance, ObjectAlias.norm)
        .join(ObjectAlias, ObjectAlias.object_id == ObjectInstance.id)
        .where(
            ObjectAlias.kind == kind,
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
        )
    )
    held = list(rows)
    if full:
        candidates = sum(
            1
            for row, norm in held
            if norm not in seen and (row.properties or {}).get("origin_state") != ORIGIN_GONE
        )
        if candidates > GONE_FLOOR and candidates > GONE_LIMIT * len(held):
            # **표시하지 않고 멈춘다** — 부르는 쪽이 실패로 적고 사람에게 알린다.
            full = False
            gone = -candidates
    for row, norm in held:
        properties = dict(row.properties or {})
        if norm in seen:
            if "removed_on" not in properties:
                continue
            properties.pop("removed_on", None)
            reason = f"{source.name} 에 다시 올라옴"
            back += 1
        elif full and properties.get("origin_state") != ORIGIN_GONE:
            properties["origin_state"] = ORIGIN_GONE
            if "removed_on" in keys:
                properties["removed_on"] = today.isoformat()
            reason = f"{source.name} 에서 볼 수 없게 됨 — 기록은 남긴다"
            gone += 1
        else:
            continue
        before = audit_state(row)
        row.properties = properties
        audit.record(
            db,
            action="object.update",
            actor=actor,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes=audit.diff(before, audit_state(row)),
            reason=reason,
        )
    if gone < 0:
        return {"gone": 0, "back": back, "gone_held_back": -gone}
    return {"gone": gone, "back": back}


# --- RA 조직 --------------------------------------------------------------------


def boards(
    source: DataSource, *, auth: Auth, transport: httpx.BaseTransport | None
) -> list[dict[str, Any]]:
    """RA 의 조직 트리 — 고르개가 그린다. 조직(`kind=org`)만, 가상 공간은 빼고, 위에서 아래로
    (깊이 · 경로를 붙여)."""
    try:
        with _client(auth, transport) as client:
            data = _data(client.get(_url(source.base_url, BOARDS_PATH)))
    except httpx.HTTPError as caught:
        raise _refuse(
            56, f"RA 에 연결하지 못했습니다: {str(caught)[:300]}", status=502
        ) from None
    except UnicodeEncodeError:
        raise _refuse(51, BAD_TOKEN) from None
    if not isinstance(data, list):
        raise _refuse(53, "RA 조직 목록의 모양이 다릅니다.", status=502)
    orgs = {
        str(one["slug"]): one
        for one in data
        if isinstance(one, dict)
        and one.get("slug")
        and str(one.get("kind") or "org") == "org"
        and not one.get("virtual")
    }
    children: dict[str | None, list[str]] = {}
    for slug, one in orgs.items():
        parent = one.get("parent_slug")
        children.setdefault(parent if parent in orgs else None, []).append(slug)
    out: list[dict[str, Any]] = []

    def walk(parent: str | None, depth: int, path: list[str]) -> None:
        kids = sorted(
            children.get(parent, []),
            key=lambda slug: (int(orgs[slug].get("sort_order") or 0), str(orgs[slug]["name"])),
        )
        for slug in kids:
            name = str(orgs[slug].get("name") or slug)
            out.append(
                {
                    "slug": slug,
                    "name": name,
                    "parent_slug": parent,
                    "depth": depth,
                    "path": " / ".join([*path, name]),
                }
            )
            walk(slug, depth + 1, [*path, name])

    walk(None, 0, [])
    return out


# --- 보고서 기록 타입의 틀 ---------------------------------------------------------


def type_payload(
    db: Session,
    *,
    slug: str,
    label: str,
    axes: list[str],
    nav_group_slug: str | None = None,
) -> dict[str, Any]:
    """정의 가져오기 한 덩어리 — 표준 칸 + 고른 축(SP 타입)의 참조 칸 `ref_<타입>`."""
    targets = {
        row.slug: row
        for row in db.scalars(select(ObjectType).where(ObjectType.slug.in_(axes or [""])))
    }
    missing = [one for one in axes if one not in targets]
    if missing:
        raise _refuse(57, f"이 쌍둥이에 없는 타입입니다: {', '.join(missing)}")
    properties: list[dict[str, Any]] = []
    for index, (key, name, data_type, multi, options) in enumerate(FIELDS):
        one: dict[str, Any] = {
            "key": key,
            "label": name,
            "data_type": data_type,
            "multi": multi,
            "sort_order": index,
        }
        if options:
            one["enum_options"] = list(options)
        if key in HELP:
            one["help"] = HELP[key]
        properties.append(one)
    for index, axis in enumerate(axes):
        target = targets[axis]
        properties.append(
            {
                "key": f"{REF_PREFIX}{axis}",
                "label": target.label,
                "data_type": "object_ref",
                "ref_type_slug": axis,
                "multi": True,
                "inverse_label": label,
                "sort_order": len(FIELDS) + index,
            }
        )
    one_type: dict[str, Any] = {
        "slug": slug,
        "label": label,
        "usage": "log",
        "key_policy": "required",
        "description": (
            "ReportArchive 의 발행 보고서 — 데이터 소스 「RA 보고서」 가 채운다(ADR 0018)."
        ),
        "properties": properties,
        "list_view": {
            "columns": [
                "label",
                "properties.report_date",
                "properties.ra_workspace",
                "properties.author",
                "properties.origin_state",
            ],
            "sort": {"field": "properties.report_date", "dir": "desc"},
            "search": ["label", "properties.body", "properties.tags", "properties.ra_tags"],
            "filters": ["properties.ra_workspace", "properties.origin_state"],
        },
    }
    if nav_group_slug:
        one_type["nav_group_slug"] = nav_group_slug
    return {"types": [one_type]}


def create_type(
    db: Session,
    user: User,
    *,
    slug: str,
    label: str,
    axes: list[str],
    nav_group_slug: str | None = None,
) -> importer.Plan:
    """보고서 기록 타입을 한 번에 — 정의 가져오기(화면 · MCP 와 같은 함수). 이미 있는 타입이면
    모자란 칸만 더한다. **부르는 쪽이 커밋한다.**"""
    payload = type_payload(
        db, slug=slug, label=label, axes=axes, nav_group_slug=nav_group_slug
    )
    plan = importer.plan(db, payload)
    if plan.errors:
        raise _refuse(
            58, "보고서 기록 타입을 생성할 수 없습니다 — " + " / ".join(plan.errors[:5])
        )
    return importer.apply(db, payload, actor=user, reason="RA 보고서 기록 타입 생성")
