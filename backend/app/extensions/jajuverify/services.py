"""자주검증 — 회의 표의 읽기 · 쓰기 · 권한 · 사진 자리(ADR 0044).

## 권한 — 여럿이 나눠 쓰는 표(2026-10-31 사용자 결정)

    보기        누구나(ADR 0034) — 조회를 부서로 좁힌 설치면 그 규칙(`visible_owner_clause`)
    만들기      그 부서의 구성원(ADR 0036 의 기록과 같다) · 시스템 관리자
    쓰기(can_write)
                그 회의 부서의 구성원(역할 무관) · 시스템 관리자 — 줄 · 묶음 · 사진 ·
                작성 완료 · 배치 · 지금 수정하는 사람. **담당자를 따로 두지 않는다** —
                누구나 어느 줄이든.
    관리(can_manage)
                회의를 만든 사람(아직 그 부서의 구성원일 때) · 그 부서 관리자 · 시스템 관리자 —
                회의의 제목 · 일자 · 삭제 · 확정 · 확정 해제

판정은 `can_write` · `can_manage` 두 곳이다 — 화면의 단추(한 장의 `can_write` · `can_manage`)와
서버의 거절이 같은 식을 쓴다. **확정된 회의는 쓰기가 전부 409 다**(확정 해제까지) — 사진도.

## 함께 쓸 때 — 덮어쓰기 보호 · 판 번호

- **칸 단위 덮어쓰기 보호** — 쓰기에 `expected`(고치기 시작할 때 본 값)를 실으면, 그 칸의 지금
  값이 다를 때 409 로 거절하고 지금 값 · 누가 · 언제를 준다. 줄 하나에 판 번호 하나를 두면 남이
  비고를 고친 것만으로 내 문제점 저장이 막힌다 — 그래서 칸마다 견준다.
- **판 번호(`revision`)** — 회의의 쓰기는 모두 `_lock` 을 지난다: 회의 행의 판 번호를 하나
  올리는 `UPDATE … RETURNING` 이 그 행을 잠가 **같은 회의의 쓰기가 줄을 서고**, 잠근 뒤에 지금
  값을 다시 읽어 견준다(앞에서 읽은 값으로 견주면 두 사람이 같은 칸을 동시에 저장할 때 둘 다
  지나간다). 바뀐 줄 · 묶음에 그 번호를 적는다. 화면은 이 번호만 묻고(`pulse`) 바뀌었을 때만 한
  장을 다시 받는다 — **시각을 커서로 쓰지 않는다**(이 개발 기계는 벽시계가 뒤로 튄다, ADR
  0024).

## 감사

회의 · 묶음 · 줄 만들기 · 고치기 · 지우기, 배치, 작성 완료, 확정 · 확정 해제, 사진 붙이기 ·
떼기가 `audit.record` 로 남는다 (`jajuverify.*`). 자동 저장이라 칸 하나를 고칠 때마다 한 줄이다
— 객체 수정과 같다. 지금 수정하는 사람(presence)은 남기지 않는다(쓰기가 아니라 눈짓이다).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.extensions.jajuverify import cells
from app.extensions.jajuverify.models import (
    MEETING_KINDS,
    JvGroup,
    JvMeeting,
    JvPresence,
    JvRow,
)
from app.modules.accounts.models import User
from app.modules.files import services as files_services
from app.modules.files.models import Attachment
from app.modules.server import services as server_services
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared import extensions as extension_points
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from app.shared.permissions import membership_of, visible_owner_clause, workspace_by_slug

NAME = "jajuverify"

#: 회의 종류 — 저장되는 키 → 화면의 이름. 차례가 메뉴의 차례다.
KINDS: dict[str, str] = {"review": "심사회", "postmortem": "포스트모텀", "briefing": "설명회"}
assert tuple(KINDS) == MEETING_KINDS

#: 글 칸(= 사진의 `owner_field`) → 열 이름. 열 전부(일정 포함)와 종류는 `cells.COLUMNS`.
CELLS: dict[str, str] = cells.TEXT_CELLS

#: 사진이 붙는 첨부의 자리(`attachments.owner_table`).
OWNER_TABLE = "jv_rows"

#: 회의 한 장의 줄 상한 — 한 장의 표에 이보다 많으면 이미 사람이 못 읽는다.
MAX_ROWS = 300
#: 묶음 상한 — 줄 상한과 같은 까닭.
MAX_GROUPS = 100

#: 「수정 중」 으로 보이는 동안 — 화면은 칸에 머무는 동안 10초마다 다시 적는다.
PRESENCE_FRESH = timedelta(seconds=30)
#: 이보다 낡은 「수정 중」 줄은 다음 적기 때 지운다(창을 닫아 「나감」 을 못 보낸 것).
PRESENCE_KEEP = timedelta(minutes=10)

MANAGE_REFUSED = (
    "회의의 제목 · 일자 · 삭제 · 확정은 만든 사람 · 그 부서 관리자 · 시스템 관리자만 "
    "할 수 있습니다."
)
CONFIRMED = "확정된 회의입니다 — 확정을 해제해야 수정할 수 있습니다."
CONFIRMED_DELETE = "확정된 회의는 삭제할 수 없습니다 — 확정을 해제한 뒤 삭제합니다."
NEED_EXPECTED = "고치기 전 값을 expected 에 함께 보냅니다 — 먼저 한 장을 읽으세요"


def require_expected(changes: dict[str, Any], expected: dict[str, Any] | None) -> None:
    """**고친 칸마다 본 값이 있어야 한다** — 사람 화면은 늘 보내고, AI · 스크립트도 보내게 한다
    (2026-10-31 사용자 결정). 안 보내면 그사이 남이 쓴 글을 말없이 덮는다 — 그것을 막는 길이
    「읽고 나서 쓴다」 하나뿐이다."""
    missing = [field for field in changes if field not in (expected or {})]
    if missing:
        raise AppError(
            code("JAJUVERIFY", 14),
            f"{NEED_EXPECTED}(빠진 칸: {', '.join(missing)}).",
            status=422,
            details={"missing": missing},
        )


def enabled(db: Session) -> bool:
    """이 설치에서 켜져 있나 — 라우터는 문이 막지만, 첨부 · 자기소개 · 서버 화면은 확장의 문을
    거치지 않고 부른다. 끈 설치에서 그 길로 회의 표를 고치거나 말하지 않게."""
    return NAME in server_services.enabled_names(db)


# --- 권한 ------------------------------------------------------------------------


def can_write(db: Session, user: User, meeting: JvMeeting) -> bool:
    """줄 · 묶음 · 사진 · 작성 완료 · 배치를 고칠 수 있나 — 그 회의 부서의 구성원(역할 무관) ·
    시스템 관리자. 확정 여부는 따로 본다(`_lock`)."""
    if user.is_system_admin:
        return True
    return membership_of(db, workspace_id=meeting.workspace_id, user_id=user.id) is not None


def can_manage(db: Session, user: User, meeting: JvMeeting) -> bool:
    """회의의 머리(제목 · 일자) · 삭제 · 확정을 할 수 있나 — 만든 사람(아직 구성원) · 부서
    관리자 · 시스템 관리자. 부서를 떠난 사람의 회의는 그 부서 관리자가 맡는다(ADR 0036 과
    같다)."""
    if user.is_system_admin:
        return True
    member = membership_of(db, workspace_id=meeting.workspace_id, user_id=user.id)
    if member is None:
        return False
    return member.role == "manager" or meeting.created_by_id == user.id


def _write_refused(db: Session, meeting: JvMeeting) -> str:
    space = db.get(Workspace, meeting.workspace_id)
    name = space.name if space else "그 부서"
    return f"이 회의의 표는 「{name}」 의 구성원만 수정할 수 있습니다."


def require_write(db: Session, user: User, meeting: JvMeeting) -> None:
    if not can_write(db, user, meeting):
        raise Forbidden(code("JAJUVERIFY", 11), _write_refused(db, meeting))


def require_manage(db: Session, user: User, meeting: JvMeeting) -> None:
    if not can_manage(db, user, meeting):
        raise Forbidden(code("JAJUVERIFY", 3), MANAGE_REFUSED)


def require_create(db: Session, user: User, workspace: Workspace) -> None:
    if user.is_system_admin:
        return
    if membership_of(db, workspace_id=workspace.id, user_id=user.id) is None:
        raise Forbidden(
            code("JAJUVERIFY", 4),
            f"「{workspace.name}」 의 구성원만 이 부서에 회의를 만들 수 있습니다.",
        )


def _lock(db: Session, meeting: JvMeeting, *, refusal: str | None = CONFIRMED) -> int:
    """판 번호를 하나 올리고 그 값을 돌려준다 — **이 UPDATE 가 회의 행을 잠가 같은 회의의
    쓰기가 여기서 줄을 선다**(커밋까지). 확정은 잠근 뒤의 값으로 본다 — 앞에서 읽은 것은 그사이
    확정됐을 수 있다. `refusal=None` 이면 확정이어도 지나간다(확정 · 확정 해제)."""
    got = db.execute(
        update(JvMeeting)
        .where(JvMeeting.id == meeting.id)
        .values(revision=JvMeeting.revision + 1)
        .returning(JvMeeting.revision, JvMeeting.confirmed_at)
        .execution_options(synchronize_session=False)
    ).one()
    if refusal is not None and got.confirmed_at is not None:
        db.rollback()
        raise Conflict(code("JAJUVERIFY", 9), refusal)
    set_committed_value(meeting, "revision", got.revision)
    set_committed_value(meeting, "confirmed_at", got.confirmed_at)
    return int(got.revision)


def _stamp(user: User) -> dict[str, str]:
    return {"by": str(user.id), "at": datetime.now(UTC).isoformat()}


def _mark(target: JvMeeting | JvGroup | JvRow, fields: Iterable[str], user: User) -> None:
    """칸마다 마지막으로 고친 사람 · 때 — 새 dict 로 바꿔 넣는다(제자리 고침은 ORM 이 못
    본다)."""
    stamp = _stamp(user)
    target.edits = {**(target.edits or {}), **{field: stamp for field in fields}}


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def _check_expected(
    db: Session,
    target: JvMeeting | JvGroup | JvRow,
    *,
    key: str,
    expected: dict[str, Any] | None,
    changes: dict[str, Any],
    fallback_by: uuid.UUID | None = None,
) -> None:
    """**덮어쓰기 보호** — 보낸 칸마다 지금 값이 「고치기 시작할 때 본 값」 과 다르면 거절한다.
    `_lock` 뒤에 다시 읽은 값으로 부른다. 지금 값이 보내는 값과 같으면(그쪽도 같은 글을 썼으면)
    부딪칠 것이 없다. 다른 칸끼리는 부딪치지 않는다 — 보낸 칸만 견준다."""
    if not expected:
        return
    clashes = [
        field
        for field, seen in expected.items()
        if field in changes
        and getattr(target, field) != seen
        and getattr(target, field) != changes[field]
    ]
    if not clashes:
        return
    field = clashes[0]
    mark = (target.edits or {}).get(field) or {}
    by = mark.get("by")
    who_id = uuid.UUID(by) if by else fallback_by
    who = _names(db, [who_id]).get(who_id) if who_id else None
    at = mark.get("at") or target.updated_at.isoformat()
    details = {
        key: str(target.id),
        "field": field,
        "fields": clashes,
        "current": _json(getattr(target, field)),
        "updated_by": who,
        "updated_at": at,
    }
    db.rollback()
    raise Conflict(
        code("JAJUVERIFY", 10),
        (f"{who} 님이" if who else "다른 사람이")
        + " 이 칸을 먼저 수정했습니다 — 지금 값을 확인한 뒤 다시 저장합니다.",
        details=details,
    )


# --- 찾기 ------------------------------------------------------------------------


def require_kind(kind: str) -> str:
    if kind not in KINDS:
        raise NotFound(
            code("JAJUVERIFY", 1),
            f"모르는 회의 종류입니다: {kind} — {', '.join(KINDS)} 중 하나입니다.",
        )
    return kind


def meeting_or_404(db: Session, user: User, meeting_id: uuid.UUID) -> JvMeeting:
    """보이는 회의 — 없는 것 · 지운 것 · 볼 수 없는 것을 같은 말로 답한다."""
    found = db.scalar(
        select(JvMeeting).where(
            JvMeeting.id == meeting_id,
            JvMeeting.deleted_at.is_(None),
            visible_owner_clause(user, JvMeeting.workspace_id),
        )
    )
    if found is None:
        raise NotFound(code("JAJUVERIFY", 2), "자주검증 회의를 찾을 수 없습니다.")
    return found


def row_or_404(db: Session, user: User, row_id: uuid.UUID) -> tuple[JvRow, JvMeeting]:
    row = db.get(JvRow, row_id)
    if row is None or row.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.")
    try:
        meeting = meeting_or_404(db, user, row.meeting_id)
    except NotFound:
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.") from None
    return row, meeting


def group_or_404(db: Session, user: User, group_id: uuid.UUID) -> tuple[JvGroup, JvMeeting]:
    group = db.get(JvGroup, group_id)
    if group is None or group.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 12), "자주검증 표의 묶음을 찾을 수 없습니다.")
    try:
        meeting = meeting_or_404(db, user, group.meeting_id)
    except NotFound:
        raise NotFound(
            code("JAJUVERIFY", 12), "자주검증 표의 묶음을 찾을 수 없습니다."
        ) from None
    return group, meeting


def _names(db: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {one for one in ids if one is not None}
    if not wanted:
        return {}
    return {
        row.id: row.display_name or row.email
        for row in db.scalars(select(User).where(User.id.in_(wanted)))
    }


def _spaces(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Workspace]:
    wanted = set(ids)
    if not wanted:
        return {}
    return {
        row.id: row for row in db.scalars(select(Workspace).where(Workspace.id.in_(wanted)))
    }


def _live_groups(db: Session, meeting_id: uuid.UUID) -> list[JvGroup]:
    return list(
        db.scalars(
            select(JvGroup)
            .where(JvGroup.meeting_id == meeting_id, JvGroup.deleted_at.is_(None))
            .order_by(JvGroup.position, JvGroup.created_at, JvGroup.id)
        )
    )


def _live_rows(db: Session, meeting_id: uuid.UUID, groups: list[JvGroup]) -> list[JvRow]:
    """줄을 **표의 차례대로** — 묶음 없는 줄이 맨 위, 그 아래 묶음의 차례대로, 묶음 안은 줄의
    차례대로. 지운 묶음을 가리키는 줄(있어서는 안 되지만)은 묶음 없음으로 읽는다."""
    rank = {group.id: index for index, group in enumerate(groups, start=1)}
    rows = list(
        db.scalars(
            select(JvRow).where(JvRow.meeting_id == meeting_id, JvRow.deleted_at.is_(None))
        )
    )
    rows.sort(
        key=lambda one: (
            rank.get(one.group_id, 0) if one.group_id else 0,
            one.position,
            one.created_at,
            one.id,
        )
    )
    return rows


def _group_of(row: JvRow, live: set[uuid.UUID]) -> uuid.UUID | None:
    return row.group_id if row.group_id in live else None


def _renumber(rows: list[JvRow], revision: int) -> None:
    """한 묶음의 줄 차례를 1부터 — 바뀐 줄에만 판 번호를 적는다."""
    for index, one in enumerate(rows, start=1):
        if one.position != index:
            one.position = index
            one.revision = revision


# --- 회의 목록 · 한 장 -------------------------------------------------------------


def meetings(
    db: Session, user: User, *, kind: str, q: str, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """그 종류의 회의 — **일자가 새것부터**(일자가 없으면 뒤로, 같으면 만든 차례가 새것부터).
    검색은 제목 · 묶음 이름 · 줄의 글(항목 · 문제점 · 개선대책 · 비고)."""
    require_kind(kind)
    stmt = select(JvMeeting).where(
        JvMeeting.kind == kind,
        JvMeeting.deleted_at.is_(None),
        visible_owner_clause(user, JvMeeting.workspace_id),
    )
    needle = q.strip()
    if needle:
        in_rows = exists().where(
            JvRow.meeting_id == JvMeeting.id,
            JvRow.deleted_at.is_(None),
            or_(*(getattr(JvRow, key).icontains(needle, autoescape=True) for key in CELLS)),
        )
        in_groups = exists().where(
            JvGroup.meeting_id == JvMeeting.id,
            JvGroup.deleted_at.is_(None),
            JvGroup.title.icontains(needle, autoescape=True),
        )
        stmt = stmt.where(
            or_(JvMeeting.title.icontains(needle, autoescape=True), in_rows, in_groups)
        )
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(
                JvMeeting.held_on.desc().nulls_last(),
                JvMeeting.created_at.desc(),
                JvMeeting.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    ids = [one.id for one in rows]
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    if ids:
        counts = {
            meeting_id: (int(count), int(done))
            for meeting_id, count, done in db.execute(
                select(
                    JvRow.meeting_id,
                    func.count(),
                    func.count().filter(JvRow.done.is_(True)),
                )
                .where(JvRow.meeting_id.in_(ids), JvRow.deleted_at.is_(None))
                .group_by(JvRow.meeting_id)
            )
        }
    names = _names(
        db, [*(one.created_by_id for one in rows), *(one.confirmed_by_id for one in rows)]
    )
    spaces = _spaces(db, (one.workspace_id for one in rows))
    out = [
        {
            **meeting_out(one, names, spaces),
            "row_count": counts.get(one.id, (0, 0))[0],
            "done_count": counts.get(one.id, (0, 0))[1],
        }
        for one in rows
    ]
    return out, total


def meeting_out(
    meeting: JvMeeting,
    names: dict[uuid.UUID, str],
    spaces: dict[uuid.UUID, Workspace],
) -> dict[str, Any]:
    space = spaces.get(meeting.workspace_id)
    return {
        "id": meeting.id,
        "kind": meeting.kind,
        "kind_label": KINDS.get(meeting.kind, meeting.kind),
        "title": meeting.title,
        "held_on": meeting.held_on,
        "workspace_slug": space.slug if space else None,
        "workspace_name": space.name if space else None,
        "author_name": names.get(meeting.created_by_id) if meeting.created_by_id else None,
        "created_at": meeting.created_at,
        "updated_at": meeting.updated_at,
        "revision": meeting.revision,
        "confirmed_at": meeting.confirmed_at,
        "confirmed_by_name": (
            names.get(meeting.confirmed_by_id)
            if meeting.confirmed_at and meeting.confirmed_by_id
            else None
        ),
    }


def photos_of(
    db: Session, user: User, row_ids: list[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[dict[str, Any]]]:
    """줄마다 칸마다 사진 — 첨부를 내려받는 규칙과 같은 가시성(`files`)."""
    out: dict[tuple[uuid.UUID, str], list[dict[str, Any]]] = {}
    if not row_ids:
        return out
    found = list(
        db.scalars(
            select(Attachment)
            .where(
                Attachment.owner_table == OWNER_TABLE,
                Attachment.owner_id.in_(row_ids),
                Attachment.owner_field.in_(list(CELLS)),
                Attachment.deleted_at.is_(None),
                visible_owner_clause(user, Attachment.workspace_id),
            )
            .order_by(Attachment.created_at, Attachment.id)
        )
    )
    # 판별 칸이 생기기 전에 올라온 것은 처음 보일 때 본다(첨부 목록과 같다).
    files_services.inspect_pending(db, found)
    for one in found:
        if one.owner_field is None:
            continue
        image = one.media == "image"
        out.setdefault((one.owner_id, one.owner_field), []).append(
            {
                "id": one.id,
                "name": one.original_name,
                "is_image": image,
                "width": one.width,
                "height": one.height,
                "size_bytes": one.size_bytes,
                "thumbnail_path": f"/attachments/{one.id}/thumbnail" if image else None,
            }
        )
    return out


def _row_people(row: JvRow) -> list[uuid.UUID | None]:
    return [row.created_by_id, row.updated_by_id, row.done_by_id]


def row_out(
    row: JvRow,
    photos: dict[tuple[uuid.UUID, str], list[dict[str, Any]]],
    names: dict[uuid.UUID, str],
    live_groups: set[uuid.UUID] | None = None,
) -> dict[str, Any]:
    group_id = row.group_id if live_groups is None else _group_of(row, live_groups)
    return {
        "id": row.id,
        "group_id": group_id,
        "position": row.position,
        **{key: getattr(row, key) for key in CELLS},
        "schedule": list(row.schedule or []),
        "due_on": row.due_on,
        "photos": {key: photos.get((row.id, key), []) for key in CELLS},
        "done": row.done,
        "done_by_name": names.get(row.done_by_id) if row.done and row.done_by_id else None,
        "done_at": row.done_at if row.done else None,
        "author_name": names.get(row.created_by_id) if row.created_by_id else None,
        "updated_by_name": names.get(row.updated_by_id) if row.updated_by_id else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "revision": row.revision,
    }


def group_out(group: JvGroup, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": group.id,
        "position": group.position,
        "title": group.title,
        "author_name": names.get(group.created_by_id) if group.created_by_id else None,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "revision": group.revision,
    }


def sheet(db: Session, user: User, meeting: JvMeeting) -> dict[str, Any]:
    """회의 한 장 — 머리 · 묶음 · 줄 전부(표의 차례대로) · 칸마다 사진 · 고칠 수 있나를
    **한 번에**."""
    groups = _live_groups(db, meeting.id)
    rows = _live_rows(db, meeting.id, groups)
    names = _names(
        db,
        [
            meeting.created_by_id,
            meeting.confirmed_by_id,
            *(one.created_by_id for one in groups),
            *(person for one in rows for person in _row_people(one)),
        ],
    )
    spaces = _spaces(db, [meeting.workspace_id])
    photos = photos_of(db, user, [one.id for one in rows])
    live = {one.id for one in groups}
    writable = can_write(db, user, meeting)
    refusal = None if writable else _write_refused(db, meeting)
    if writable and meeting.confirmed_at is not None:
        writable, refusal = False, CONFIRMED
    manageable = can_manage(db, user, meeting)
    return {
        "meeting": meeting_out(meeting, names, spaces),
        "cells": [
            {"key": one.key, "label": one.label, "kind": one.kind} for one in cells.COLUMNS
        ],
        "groups": [group_out(one, names) for one in groups],
        "rows": [row_out(one, photos, names, live) for one in rows],
        "can_write": writable,
        "write_refusal": refusal,
        "can_manage": manageable,
        "manage_refusal": None if manageable else MANAGE_REFUSED,
        "max_rows": MAX_ROWS,
        "max_groups": MAX_GROUPS,
    }


def one_row(db: Session, user: User, row: JvRow) -> dict[str, Any]:
    return row_out(row, photos_of(db, user, [row.id]), _names(db, _row_people(row)))


def one_group(db: Session, group: JvGroup) -> dict[str, Any]:
    return group_out(group, _names(db, [group.created_by_id]))


def one_meeting(db: Session, meeting: JvMeeting) -> dict[str, Any]:
    return meeting_out(
        meeting,
        _names(db, [meeting.created_by_id, meeting.confirmed_by_id]),
        _spaces(db, [meeting.workspace_id]),
    )


# --- 회의 쓰기 ---------------------------------------------------------------------


def _meeting_state(meeting: JvMeeting) -> dict[str, Any]:
    return {
        "kind": meeting.kind,
        "title": meeting.title,
        "held_on": meeting.held_on.isoformat() if meeting.held_on else None,
        "workspace_id": str(meeting.workspace_id),
    }


def _row_state(row: JvRow) -> dict[str, Any]:
    return {
        "position": row.position,
        **{key: getattr(row, key) for key in CELLS},
        "schedule": list(row.schedule or []),
    }


def _meeting_label(meeting: JvMeeting) -> str:
    return f"{KINDS.get(meeting.kind, meeting.kind)} · {meeting.title}"


def _row_label(meeting: JvMeeting, row: JvRow) -> str:
    # 사진 표시 줄은 이름표에 안 싣는다 — 「![](photo:…)」 가 감사 목록에 서면 아무도 못
    # 읽는다.
    lines = cells.plain(row.topic or "").splitlines()
    head = lines[0][:60] if lines else ""
    return f"{meeting.title} · {head or '(항목 없음)'}"


def _group_label(meeting: JvMeeting, group: JvGroup) -> str:
    return f"{meeting.title} · {group.title or '(이름 없음)'}"


def create_meeting(
    db: Session,
    user: User,
    *,
    kind: str,
    title: str,
    held_on: date | None,
    workspace_slug: str,
) -> JvMeeting:
    require_kind(kind)
    workspace = workspace_by_slug(db, workspace_slug)
    require_create(db, user, workspace)
    meeting = JvMeeting(
        kind=kind,
        title=title.strip(),
        held_on=held_on,
        workspace_id=workspace.id,
        created_by_id=user.id,
    )
    db.add(meeting)
    db.flush()
    audit.record(
        db,
        action="jajuverify.meeting.create",
        actor=user,
        target_table="jv_meetings",
        target_id=meeting.id,
        target_label=_meeting_label(meeting),
        workspace_id=workspace.id,
        changes=_meeting_state(meeting),
    )
    db.commit()
    db.refresh(meeting)
    return meeting


def update_meeting(
    db: Session,
    user: User,
    meeting: JvMeeting,
    changes: dict[str, Any],
    expected: dict[str, Any] | None = None,
) -> JvMeeting:
    """**보낸 칸만** 바꾼다 — 일자를 비우려면 `null` 을 보낸다(안 보낸 것과 다르다). 고친
    칸마다 `expected` 가 있어야 한다 — 줄의 칸과 같은 덮어쓰기 보호다."""
    require_manage(db, user, meeting)
    require_expected(changes, expected)
    _lock(db, meeting)
    db.refresh(meeting)
    if "title" in changes:
        changes["title"] = str(changes["title"]).strip()
    if expected and "title" in expected:
        expected["title"] = str(expected["title"] or "").strip()
    _check_expected(db, meeting, key="meeting_id", expected=expected, changes=changes)
    before = _meeting_state(meeting)
    for field in ("title", "held_on"):
        if field in changes:
            setattr(meeting, field, changes[field])
    diff = audit.diff(before, _meeting_state(meeting))
    if not diff:
        db.rollback()  # 판 번호도 그대로 — 바뀐 것이 없다
        db.refresh(meeting)
        return meeting
    _mark(meeting, diff, user)
    audit.record(
        db,
        action="jajuverify.meeting.update",
        actor=user,
        target_table="jv_meetings",
        target_id=meeting.id,
        target_label=_meeting_label(meeting),
        workspace_id=meeting.workspace_id,
        changes=diff,
    )
    db.commit()
    db.refresh(meeting)
    return meeting


def delete_meeting(db: Session, user: User, meeting: JvMeeting) -> None:
    """회의를 지운다 — `deleted_at` 만 채운다. 묶음 · 줄 · 사진은 그대로 남는다(그 회의를
    통해서만 보이므로 화면에서는 함께 사라진다). 사진 파일도 남는다 — 살아 있는 첨부가
    가리킨다. **확정된 회의는 못 지운다** — 확정을 해제한 뒤 지운다."""
    require_manage(db, user, meeting)
    _lock(db, meeting, refusal=CONFIRMED_DELETE)
    rows = int(
        db.scalar(
            select(func.count())
            .select_from(JvRow)
            .where(JvRow.meeting_id == meeting.id, JvRow.deleted_at.is_(None))
        )
        or 0
    )
    meeting.deleted_at = func.now()
    meeting.deleted_by_id = user.id
    audit.record(
        db,
        action="jajuverify.meeting.delete",
        actor=user,
        target_table="jv_meetings",
        target_id=meeting.id,
        target_label=_meeting_label(meeting),
        workspace_id=meeting.workspace_id,
        changes={"rows": rows},
    )
    db.commit()


def confirm(db: Session, user: User, meeting: JvMeeting) -> None:
    """회의 확정 — 그 뒤 이 회의의 쓰기가 전부 막힌다. 작성 중인 줄이 남아도 확정한다(화면이
    먼저 묻는다). 이미 확정이면 아무 일도 없다. 지금 수정하는 사람 표시는 지운다."""
    require_manage(db, user, meeting)
    _lock(db, meeting, refusal=None)
    if meeting.confirmed_at is not None:
        db.rollback()
        return
    rows, done = db.execute(
        select(func.count(), func.count().filter(JvRow.done.is_(True))).where(
            JvRow.meeting_id == meeting.id, JvRow.deleted_at.is_(None)
        )
    ).one()
    meeting.confirmed_at = func.now()
    meeting.confirmed_by_id = user.id
    db.execute(delete(JvPresence).where(JvPresence.meeting_id == meeting.id))
    audit.record(
        db,
        action="jajuverify.meeting.confirm",
        actor=user,
        target_table="jv_meetings",
        target_id=meeting.id,
        target_label=_meeting_label(meeting),
        workspace_id=meeting.workspace_id,
        changes={"rows": int(rows), "done": int(done)},
    )
    db.commit()
    db.refresh(meeting)


def unconfirm(db: Session, user: User, meeting: JvMeeting) -> None:
    """확정 해제 — 다시 고칠 수 있다. 확정이 아니면 아무 일도 없다."""
    require_manage(db, user, meeting)
    _lock(db, meeting, refusal=None)
    if meeting.confirmed_at is None:
        db.rollback()
        return
    meeting.confirmed_at = None
    meeting.confirmed_by_id = None
    audit.record(
        db,
        action="jajuverify.meeting.unconfirm",
        actor=user,
        target_table="jv_meetings",
        target_id=meeting.id,
        target_label=_meeting_label(meeting),
        workspace_id=meeting.workspace_id,
        changes={},
    )
    db.commit()
    db.refresh(meeting)


# --- 묶음 쓰기 ---------------------------------------------------------------------


def create_group(
    db: Session, user: User, meeting: JvMeeting, *, title: str, row_ids: list[uuid.UUID]
) -> tuple[JvGroup, list[uuid.UUID]]:
    """묶음을 맨 아래에 더하고, 고른 줄을 (지금 표의 차례대로) 그 묶음으로 옮긴다 — 한
    트랜잭션이다. 고른 줄이 그사이 지워졌으면 **건너뛰고** 돌려준다(차례 때문에 거절하지
    않는다)."""
    require_write(db, user, meeting)
    revision = _lock(db, meeting)
    groups = _live_groups(db, meeting.id)
    if len(groups) >= MAX_GROUPS:
        raise Conflict(
            code("JAJUVERIFY", 13),
            f"회의 한 장의 묶음은 {MAX_GROUPS}개까지입니다 — 회의를 나누세요.",
        )
    rows = _live_rows(db, meeting.id, groups)
    live_rows = {one.id for one in rows}
    chosen = set(row_ids) & live_rows
    skipped = list(dict.fromkeys(one for one in row_ids if one not in live_rows))
    group = JvGroup(
        meeting_id=meeting.id,
        position=len(groups) + 1,
        title=title.strip(),
        created_by_id=user.id,
        revision=revision,
        edits={"title": _stamp(user)},
    )
    db.add(group)
    db.flush()
    live = {one.id for one in groups}
    moving = [one for one in rows if one.id in chosen]
    for one in moving:
        one.group_id = group.id
    _renumber(moving, revision)
    for one in moving:
        one.revision = revision
    # 줄이 빠진 묶음의 차례를 다시 붙인다.
    for source in {None, *live}:
        _renumber(
            [one for one in rows if one.id not in chosen and _group_of(one, live) == source],
            revision,
        )
    audit.record(
        db,
        action="jajuverify.group.create",
        actor=user,
        target_table="jv_groups",
        target_id=group.id,
        target_label=_group_label(meeting, group),
        workspace_id=meeting.workspace_id,
        changes={
            "title": group.title,
            "rows": [str(one.id) for one in moving],
            **({"skipped": [str(one) for one in skipped]} if skipped else {}),
        },
    )
    db.commit()
    db.refresh(group)
    return group, skipped


def update_group(
    db: Session,
    user: User,
    group: JvGroup,
    meeting: JvMeeting,
    *,
    title: str,
    expected: dict[str, Any] | None = None,
) -> JvGroup:
    """묶음 이름 — `expected.title` 이 있어야 하고, 덮어쓰기 보호는 줄의 칸과 같다."""
    require_write(db, user, meeting)
    require_expected({"title": title}, expected)
    revision = _lock(db, meeting)
    db.refresh(group)
    if group.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 12), "자주검증 표의 묶음을 찾을 수 없습니다.")
    changes = {"title": title.strip()}
    if expected and "title" in expected:
        expected["title"] = str(expected["title"] or "").strip()
    _check_expected(db, group, key="group_id", expected=expected, changes=changes)
    if group.title == changes["title"]:
        db.rollback()
        db.refresh(group)
        return group
    before = group.title
    group.title = changes["title"]
    group.revision = revision
    _mark(group, ["title"], user)
    audit.record(
        db,
        action="jajuverify.group.update",
        actor=user,
        target_table="jv_groups",
        target_id=group.id,
        target_label=_group_label(meeting, group),
        workspace_id=meeting.workspace_id,
        changes={"title": {"before": before, "after": group.title}},
    )
    db.commit()
    db.refresh(group)
    return group


def delete_group(
    db: Session, user: User, group: JvGroup, meeting: JvMeeting, *, with_rows: bool
) -> None:
    """묶음을 지운다 — `with_rows` 가 아니면 묶음만 풀고 그 줄은 묶음 없음의 맨 아래로 간다.
    `with_rows` 면 그 줄까지 지운다(`deleted_at` — 사진은 남는다, 줄 삭제와 같다)."""
    require_write(db, user, meeting)
    revision = _lock(db, meeting)
    db.refresh(group)
    if group.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 12), "자주검증 표의 묶음을 찾을 수 없습니다.")
    groups = _live_groups(db, meeting.id)
    rows = _live_rows(db, meeting.id, groups)
    live = {one.id for one in groups}
    members = [one for one in rows if _group_of(one, live) == group.id]
    if with_rows:
        for one in members:
            one.deleted_at = func.now()
            one.revision = revision
    else:
        loose = [one for one in rows if _group_of(one, live) is None]
        for one in members:
            one.group_id = None
            one.revision = revision
        _renumber([*loose, *members], revision)
    group.deleted_at = func.now()
    group.revision = revision
    rest = [other for other in groups if other.id != group.id]
    for index, other in enumerate(rest, start=1):
        if other.position != index:
            other.position = index
            other.revision = revision
    audit.record(
        db,
        action="jajuverify.group.delete",
        actor=user,
        target_table="jv_groups",
        target_id=group.id,
        target_label=_group_label(meeting, group),
        workspace_id=meeting.workspace_id,
        changes={
            "title": group.title,
            "rows": len(members),
            "with_rows": with_rows,
        },
    )
    db.commit()


def _layout_state(groups: list[JvGroup], rows: list[JvRow]) -> dict[str, Any]:
    live = {one.id for one in groups}
    return {
        "ungrouped": [str(one.id) for one in rows if _group_of(one, live) is None],
        "groups": [
            {
                "id": str(group.id),
                "rows": [str(one.id) for one in rows if _group_of(one, live) == group.id],
            }
            for group in groups
        ],
    }


class Moved:
    """옮기기의 결과 — 바뀌었나와 건너뛴 것."""

    def __init__(self) -> None:
        self.changed = False
        self.skipped: list[uuid.UUID] = []
        self.anchor_missing = False


def move(
    db: Session,
    user: User,
    meeting: JvMeeting,
    *,
    rows: list[uuid.UUID],
    group: uuid.UUID | None,
    step: str | None,
    to: dict[str, Any] | None,
) -> Moved:
    """옮기기 — **상대 동작**을 회의 잠금(`_lock`) 안에서 **그때의 차례**에 대고 적용한다.

    통째 배치(「지금 있는 줄 전부를 이 차례로」)는 그사이 남이 줄 하나를 더해도 409 였다 —
    사람은 다시 읽고 다시 옮겨야 했다(사용자 요청 「줄 옮기기 중 충돌은 없앨 수 있도록」,
    2026-10-31). 상대 동작은 「이 줄을 한 칸 위로」 · 「이 줄들을 저 줄 뒤로」 라서 남의 줄이
    늘고 줄어도 뜻이 그대로다. 그사이 사라진 줄 · 묶음은 건너뛰고 알린다. 기준 줄이 사라졌으면
    그 묶음의 끝에 놓는다.
    """
    require_write(db, user, meeting)
    revision = _lock(db, meeting)
    groups = _live_groups(db, meeting.id)
    table = _live_rows(db, meeting.id, groups)
    live = {one.id for one in groups}
    before = _layout_state(groups, table)
    # 묶음(없음 = None)마다 줄 id 목록 — 이것을 고치고 마지막에 차례 · 묶음을 적는다.
    lists: dict[uuid.UUID | None, list[uuid.UUID]] = {None: []}
    lists.update({one.id: [] for one in groups})
    for one in table:
        lists[_group_of(one, live)].append(one.id)
    where = {rid: gid for gid, ids in lists.items() for rid in ids}
    order = [one.id for one in groups]
    out = Moved()

    if group is not None and step is not None:
        if group not in live:
            out.skipped.append(group)
        else:
            at = order.index(group)
            to_at = at - 1 if step == "up" else at + 1
            if 0 <= to_at < len(order):
                order[at], order[to_at] = order[to_at], order[at]
    elif step is not None:
        rid = rows[0]
        if rid not in where:
            out.skipped.append(rid)
        else:
            ids = lists[where[rid]]
            at = ids.index(rid)
            to_at = at - 1 if step == "up" else at + 1
            if 0 <= to_at < len(ids):
                ids[at], ids[to_at] = ids[to_at], ids[at]
    elif to is not None:
        out.skipped.extend(one for one in dict.fromkeys(rows) if one not in where)
        chosen = set(rows) & set(where)
        moving = [one.id for one in table if one.id in chosen]  # 지금 표의 차례대로
        target: uuid.UUID | None = to.get("group")
        anchor: uuid.UUID | None = to.get("before") or to.get("after")
        if anchor is not None and (anchor not in where or anchor in chosen):
            if anchor not in chosen:
                out.skipped.append(anchor)
            out.anchor_missing = True
            anchor = None
        if anchor is not None:
            target = where[anchor]
        if target is not None and target not in live:
            out.skipped.append(target)
            moving = []
        if moving:
            for rid in moving:
                lists[where[rid]].remove(rid)
            ids = lists[target]
            if anchor is not None:
                at = ids.index(anchor) + (0 if to.get("before") else 1)
            elif out.anchor_missing:
                at = len(ids)
            else:
                at = 0 if to.get("at") == "start" else len(ids)
            ids[at:at] = moving

    after = {
        "ungrouped": [str(one) for one in lists[None]],
        "groups": [
            {"id": str(gid), "rows": [str(one) for one in lists[gid]]} for gid in order
        ],
    }
    if after == before:
        db.rollback()  # 바뀐 것이 없다(끝에서 한 칸 더 · 이미 그 자리) — 판 번호도 그대로
        return out
    out.changed = True
    # 적는다 — 바뀐 것에만 판 번호.
    group_by_id = {one.id: one for one in groups}
    for index, ordered in enumerate(order, start=1):
        if group_by_id[ordered].position != index:
            group_by_id[ordered].position = index
            group_by_id[ordered].revision = revision
    row_by_id = {one.id: one for one in table}
    for home, ids in lists.items():
        for index, rid in enumerate(ids, start=1):
            row = row_by_id[rid]
            if _group_of(row, live) != home or row.position != index:
                row.group_id = home
                row.position = index
                row.revision = revision
    audit.record(
        db,
        action="jajuverify.move",
        actor=user,
        target_table="jv_meetings",
        target_id=meeting.id,
        target_label=_meeting_label(meeting),
        workspace_id=meeting.workspace_id,
        changes={
            "before": before,
            "after": after,
            **({"skipped": [str(one) for one in out.skipped]} if out.skipped else {}),
        },
    )
    db.commit()
    return out


# --- 줄 쓰기 -----------------------------------------------------------------------


def add_row(
    db: Session,
    user: User,
    meeting: JvMeeting,
    *,
    after_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
    values: dict[str, Any],
) -> JvRow:
    """줄 하나를 더한다 — `after_id` 를 주면 그 줄 바로 아래(그 줄의 묶음), 아니면 `group_id`
    묶음의 맨 아래(비우면 묶음 없음의 맨 아래)."""
    require_write(db, user, meeting)
    revision = _lock(db, meeting)
    groups = _live_groups(db, meeting.id)
    rows = _live_rows(db, meeting.id, groups)
    if len(rows) >= MAX_ROWS:
        raise Conflict(
            code("JAJUVERIFY", 6),
            f"회의 한 장의 표는 {MAX_ROWS}줄까지입니다 — 회의를 나누세요.",
        )
    live = {one.id for one in groups}
    if after_id is not None:
        anchor = next((one for one in rows if one.id == after_id), None)
        if anchor is None:
            raise NotFound(code("JAJUVERIFY", 5), "기준 줄을 이 회의에서 찾을 수 없습니다.")
        target = _group_of(anchor, live)
    else:
        if group_id is not None and group_id not in live:
            raise NotFound(code("JAJUVERIFY", 12), "그 묶음을 이 회의에서 찾을 수 없습니다.")
        target = group_id
    siblings = [one for one in rows if _group_of(one, live) == target]
    at = len(siblings)
    if after_id is not None:
        at = next(index for index, one in enumerate(siblings) if one.id == after_id) + 1
    row = JvRow(
        meeting_id=meeting.id,
        group_id=target,
        created_by_id=user.id,
        updated_by_id=user.id,
        revision=revision,
    )
    for key in CELLS:
        setattr(row, key, str(values.get(key) or ""))
    row.schedule = cells.schedule_of(values.get("schedule") or [])
    row.due_on = cells.last_date(row.schedule)
    db.add(row)
    _renumber([*siblings[:at], row, *siblings[at:]], revision)
    row.revision = revision
    db.flush()
    audit.record(
        db,
        action="jajuverify.row.create",
        actor=user,
        target_table="jv_rows",
        target_id=row.id,
        target_label=_row_label(meeting, row),
        workspace_id=meeting.workspace_id,
        changes={**_row_state(row), "group_id": str(target) if target else None},
    )
    db.commit()
    db.refresh(row)
    return row


def update_row(
    db: Session,
    user: User,
    row: JvRow,
    meeting: JvMeeting,
    changes: dict[str, Any],
    expected: dict[str, Any] | None = None,
) -> JvRow:
    """**보낸 칸만** — 칸 글은 빈 글로 비우고, 일정은 `[]` 로 비운다. 고친 칸마다 `expected` 가
    있어야 하고(`require_expected`), 칸마다 덮어쓰기 보호(`_check_expected`)를 거친다. 완료
    예정일(`due_on`)은 일정의 가장 늦은 날로 따라온다."""
    require_write(db, user, meeting)
    require_expected(changes, expected)
    revision = _lock(db, meeting)
    db.refresh(row)
    if row.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.")
    for key in CELLS:
        if key in changes:
            changes[key] = str(changes[key] or "")
        if expected and key in expected:
            expected[key] = str(expected[key] or "")
    # 일정은 저장하는 모양(날짜 차례)으로 맞춰 견준다 — 같은 단계를 다른 차례로 보내도 같다.
    if "schedule" in changes:
        changes["schedule"] = cells.schedule_of(changes["schedule"] or [])
    if expected and "schedule" in expected:
        expected["schedule"] = cells.schedule_of(expected["schedule"] or [])
    _check_expected(
        db,
        row,
        key="row_id",
        expected=expected,
        changes=changes,
        fallback_by=row.updated_by_id,
    )
    before = _row_state(row)
    for key in CELLS:
        if key in changes:
            setattr(row, key, changes[key])
    if "schedule" in changes:
        row.schedule = changes["schedule"]
        row.due_on = cells.last_date(row.schedule)
    diff = audit.diff(before, _row_state(row))
    if not diff:
        db.rollback()  # 판 번호도 그대로 — 바뀐 것이 없다
        db.refresh(row)
        return row
    row.revision = revision
    row.updated_by_id = user.id
    _mark(row, diff, user)
    audit.record(
        db,
        action="jajuverify.row.update",
        actor=user,
        target_table="jv_rows",
        target_id=row.id,
        target_label=_row_label(meeting, row),
        workspace_id=meeting.workspace_id,
        changes=diff,
    )
    db.commit()
    db.refresh(row)
    return row


def set_done(db: Session, user: User, row: JvRow, meeting: JvMeeting, *, done: bool) -> JvRow:
    """작성 완료를 켜고 끈다 — 쓸 수 있는 사람 누구나. 고치기를 막지는 않는다(표시일
    뿐이다)."""
    require_write(db, user, meeting)
    revision = _lock(db, meeting)
    db.refresh(row)
    if row.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.")
    if row.done == done:
        db.rollback()
        db.refresh(row)
        return row
    row.done = done
    row.done_by_id = user.id if done else None
    row.done_at = func.now() if done else None
    row.revision = revision
    audit.record(
        db,
        action="jajuverify.row.done",
        actor=user,
        target_table="jv_rows",
        target_id=row.id,
        target_label=_row_label(meeting, row),
        workspace_id=meeting.workspace_id,
        changes={"done": {"before": not done, "after": done}},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_row(db: Session, user: User, row: JvRow, meeting: JvMeeting) -> None:
    """줄을 지운다 — `deleted_at` 만. 사진(첨부 행)은 그대로 둔다 — 그 파일은 고아 정리의
    대상이 아니다(살아 있는 첨부가 가리킨다). 객체의 휴지통과 같은 규칙이다."""
    require_write(db, user, meeting)
    revision = _lock(db, meeting)
    db.refresh(row)
    if row.deleted_at is not None:
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.")
    photos = int(
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(
                Attachment.owner_table == OWNER_TABLE,
                Attachment.owner_id == row.id,
                Attachment.deleted_at.is_(None),
            )
        )
        or 0
    )
    row.deleted_at = func.now()
    row.revision = revision
    audit.record(
        db,
        action="jajuverify.row.delete",
        actor=user,
        target_table="jv_rows",
        target_id=row.id,
        target_label=_row_label(meeting, row),
        workspace_id=meeting.workspace_id,
        changes={**_row_state(row), "photos": photos},
    )
    db.commit()


# --- 지금 수정하는 사람 · 바뀌었나 ----------------------------------------------------


def touch_presence(
    db: Session,
    user: User,
    meeting: JvMeeting,
    *,
    row_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
    field: str | None,
) -> None:
    """「지금 이 칸을 수정하는 중」 — 회의마다 사람마다 한 줄을 고쳐 쓴다(`seen_at` = DB 의
    now). 판 번호는 올리지 않는다(내용이 아니다 — 올리면 10초마다 모두가 한 장을 다시 받는다).
    낡은 줄(창을 닫아 「나감」 을 못 보낸 것)은 여기서 함께 지운다."""
    require_write(db, user, meeting)
    if meeting.confirmed_at is not None:
        raise Conflict(code("JAJUVERIFY", 9), CONFIRMED)
    if row_id is not None and not db.scalar(
        select(
            exists().where(
                JvRow.id == row_id, JvRow.meeting_id == meeting.id, JvRow.deleted_at.is_(None)
            )
        )
    ):
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.")
    if group_id is not None and not db.scalar(
        select(
            exists().where(
                JvGroup.id == group_id,
                JvGroup.meeting_id == meeting.id,
                JvGroup.deleted_at.is_(None),
            )
        )
    ):
        raise NotFound(code("JAJUVERIFY", 12), "자주검증 표의 묶음을 찾을 수 없습니다.")
    values = {"row_id": row_id, "group_id": group_id, "field": field, "seen_at": func.now()}
    db.execute(
        pg_insert(JvPresence)
        .values(meeting_id=meeting.id, user_id=user.id, **values)
        .on_conflict_do_update(index_elements=["meeting_id", "user_id"], set_=values)
    )
    db.execute(delete(JvPresence).where(JvPresence.seen_at < func.now() - PRESENCE_KEEP))
    db.commit()


def leave_presence(db: Session, user: User, meeting: JvMeeting) -> None:
    """칸을 떠났다 — 내 줄을 지운다(없으면 아무 일도 없다)."""
    db.execute(
        delete(JvPresence).where(
            JvPresence.meeting_id == meeting.id, JvPresence.user_id == user.id
        )
    )
    db.commit()


def pulse(db: Session, user: User, meeting: JvMeeting) -> dict[str, Any]:
    """한 장이 바뀌었나 — 판 번호 · 확정 · **30초 안에** 칸을 수정하던 다른 사람(묻는 사람은
    뺀다). 화면이 10~15초마다 부른다 — 가벼워야 한다(회의 행 하나 + 작은 표 하나)."""
    people: list[dict[str, Any]] = []
    if meeting.confirmed_at is None:
        found = db.execute(
            select(JvPresence, User.display_name, User.email)
            .join(User, User.id == JvPresence.user_id)
            .where(
                JvPresence.meeting_id == meeting.id,
                JvPresence.user_id != user.id,
                JvPresence.seen_at > func.now() - PRESENCE_FRESH,
            )
            .order_by(JvPresence.seen_at.desc())
        )
        people = [
            {
                "user_id": one.user_id,
                "name": display or email,
                "row_id": one.row_id,
                "group_id": one.group_id,
                "field": one.field,
            }
            for one, display, email in found
        ]
    return {
        "revision": meeting.revision,
        "confirmed": meeting.confirmed_at is not None,
        "presence": people,
    }


# --- 사진(첨부)의 자리 ---------------------------------------------------------------


def _cell_photo_ids(db: Session, row_id: uuid.UUID, field: str) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(Attachment.id).where(
                Attachment.owner_table == OWNER_TABLE,
                Attachment.owner_id == row_id,
                Attachment.owner_field == field,
                Attachment.deleted_at.is_(None),
            )
        )
    )


def attachment_owner(
    db: Session, user: User, owner_id: uuid.UUID, owner_field: str | None
) -> extension_points.AttachmentOwner:
    """`extensions.register_attachment_owner("jv_rows", …)` — 공통 첨부가 묻는 넷(ADR 0012):

    - 그 줄이 있고 보이나(없는 것 · 지운 것 · 볼 수 없는 것은 같은 말로)
    - 고칠 수 있나 — 그 회의 부서의 구성원(`can_write`), 그리고 **확정 전**
    - 그 자리가 칸인가(`topic` · `problem` · `action` · `remark`) — 칸이면 **사진만**. 뗄 때는
      자리 없이(None) 묻는다(공통 첨부의 규칙)
    - 끈 설치에서는 없는 자리다(확장의 문을 거치지 않는 길이라 여기서 막는다)

    첨부의 부서는 **회의의 부서**다. 붙이고 뗀 일은 감사에 남고(`jajuverify.photo.*`), 회의의
    판 번호를 올린다(남의 화면이 그 사진을 받게).
    """
    if not enabled(db):
        raise NotFound(code("JAJUVERIFY", 5), "자주검증 표의 줄을 찾을 수 없습니다.")
    row, meeting = row_or_404(db, user, owner_id)
    require_write(db, user, meeting)
    if meeting.confirmed_at is not None:
        raise Conflict(code("JAJUVERIFY", 9), CONFIRMED)
    if owner_field is not None and owner_field not in CELLS:
        raise Conflict(
            code("JAJUVERIFY", 8),
            f"자주검증 표에는 칸 「{owner_field}」 가 없습니다 — 사진은 "
            f"{' · '.join(CELLS)} 칸에 붙입니다.",
            details={"owner_field": owner_field, "fields": list(CELLS)},
        )

    def changed(db: Session, user: User, field: str | None, name: str, added: bool) -> None:
        # 첨부 행과 같은 트랜잭션 — 잠근 뒤 확정이면 첨부째 거절된다.
        revision = _lock(db, meeting)
        extra: dict[str, Any] = {}
        if not added and field in CELLS:
            # **뗀 사진의 표시 줄도 뺀다** — 남겨 두면 글에 아무것도 안 그리는 줄이 남고,
            # 사람은 그 줄이 왜 있는지 모른다. 어느 첨부를 뗐는지는 부르는 쪽이 안 준다 — 떼기
            # 전후의 살아 있는 첨부로 가린다(떼기는 아직 이 트랜잭션에만 있다).
            before_ids = _cell_photo_ids(db, row.id, field)
            db.flush()
            gone = before_ids - _cell_photo_ids(db, row.id, field)
            db.refresh(row)
            text = getattr(row, field) or ""
            kept = cells.without_photos(text, gone)
            if kept != text:
                setattr(row, field, kept)
                row.updated_by_id = user.id
                _mark(row, [field], user)
                extra = {field: {"before": text, "after": kept}}
        row.revision = revision
        audit.record(
            db,
            action="jajuverify.photo.add" if added else "jajuverify.photo.remove",
            actor=user,
            target_table="jv_rows",
            target_id=row.id,
            target_label=_row_label(meeting, row),
            workspace_id=meeting.workspace_id,
            changes={
                f"photos.{field or '_'}": {
                    "before": None if added else name,
                    "after": name if added else None,
                },
                **extra,
            },
        )

    return extension_points.AttachmentOwner(
        workspace_id=meeting.workspace_id,
        accept="image" if owner_field is not None else None,
        label=_row_label(meeting, row),
        changed=changed,
    )


# --- 공통 화면에 끼우는 것 -----------------------------------------------------------


def profile_facts(db: Session) -> list[extension_points.ProfileFact]:
    """플랫폼 자기소개(MCP 안내문)의 한 갈래 — 켠 설치에서만."""
    if not enabled(db):
        return []
    counts = {
        kind: int(count)
        for kind, count in db.execute(
            select(JvMeeting.kind, func.count())
            .where(JvMeeting.deleted_at.is_(None))
            .group_by(JvMeeting.kind)
        )
    }
    told = " · ".join(
        f"{label} {extension_points.count_text(counts.get(kind, 0))}건"
        for kind, label in KINDS.items()
    )
    return [
        extension_points.ProfileFact(
            key="jajuverify",
            label="자주검증",
            lines=[
                f"자주검증 회의 표 — {told}. 열은 항목 · 문제점 · 개선대책 · 일정 · "
                "비고(글 칸마다 사진 — 자리 · 폭은 칸 글의 `![](photo:<id>?w=50)` 줄), 줄은 "
                "묶음(머리줄)으로 나뉜다. 확장 자리로 읽고 쓴다(`extensions_schema` · "
                "`extension_call` — get_guide 의 자주검증 절) — 온톨로지 타입이 아니라 "
                "objects_* 로는 안 보인다. 여럿이 함께 쓰는 표라 칸을 고칠 때 읽은 값을 "
                "`expected` 로 함께 보낸다(없으면 422)."
            ],
            marks={},
        )
    ]


def stats(db: Session) -> list[extension_points.StatItem]:
    """서버 화면의 「쌓인 것」 — 켠 설치에서만."""
    if not enabled(db):
        return []
    total = db.scalar(
        select(func.count()).select_from(JvMeeting).where(JvMeeting.deleted_at.is_(None))
    )
    return [extension_points.StatItem(label="자주검증 회의", count=int(total or 0))]


def _meetings_of(db: Session, workspace_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(JvMeeting)
            .where(JvMeeting.workspace_id == workspace_id)
        )
        or 0
    )


def workspace_reference(
    db: Session, workspace_id: uuid.UUID
) -> list[extension_points.WorkspaceReference]:
    """부서 삭제 확인 — 회의가 그 부서를 가리키면 막는다(FK RESTRICT — 지운 회의까지 센다)."""
    return [
        extension_points.WorkspaceReference(
            table="jv_meetings",
            label="자주검증 회의",
            count=_meetings_of(db, workspace_id),
            blocks_delete=True,
        )
    ]


def _move_meetings(db: Session, source: uuid.UUID, target: uuid.UUID) -> int:
    done = db.execute(
        update(JvMeeting).where(JvMeeting.workspace_id == source).values(workspace_id=target)
    )
    return extension_points.rows_changed(done)


def workspace_content(
    db: Session, workspace_id: uuid.UUID
) -> list[extension_points.WorkspaceContent]:
    """부서 통폐합 때 옮길 회의 — 사진(첨부)은 첨부 쪽이 옮긴다. 끈 설치에서는 회의가 있을 때만
    선다(없는 기능의 「0건」 이 옮기기 화면에 서지 않게 — 있으면 그 부서를 비우려면 옮겨야
    한다)."""
    count = _meetings_of(db, workspace_id)
    if not count and not enabled(db):
        return []
    return [
        extension_points.WorkspaceContent(
            kind="jv_meetings",
            label="자주검증 회의",
            count=count,
            move=_move_meetings,
        )
    ]
