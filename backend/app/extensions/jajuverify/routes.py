"""자주검증 — 확장 라우터(`/api/ext/jajuverify/…`).

회의 목록 · 회의 한 장 · 회의 · 묶음 · 줄의 만들기 · 고치기 · 지우기 · 배치 · 작성 완료 · 확정,
그리고 여럿이 함께 쓸 때의 두 가지 — 바뀌었나(`pulse`, 화면이 10~15초마다)와 지금 수정하는 사람
(`presence`). 칸의 사진은 공통 첨부(`/api/attachments`, `owner_table=jv_rows`, `owner_field` =
칸)로 간다 — 자리 확인은 이 확장이 등록한 것이 답한다(`services.attachment_owner`).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.extensions.jajuverify import services
from app.extensions.jajuverify.schemas import (
    ArrangeOut,
    DoneIn,
    GroupIn,
    GroupOut,
    GroupPatch,
    Kind,
    MeetingIn,
    MeetingOut,
    MeetingPageOut,
    MeetingPatch,
    MoveIn,
    PresenceIn,
    PulseOut,
    RowIn,
    RowOut,
    RowPatch,
    SheetOut,
)
from app.modules.accounts.models import User
from app.shared.auth import current_user
from app.shared.pagination import clamp_limit

router = APIRouter(prefix="/ext/jajuverify", tags=["ext:jajuverify"])

#: 화면이 폴링하는 경로의 틀 — 접근 로그 · 요청 시간 기록에서 뺀다(`__init__.register`).
POLLING_ROUTES = (
    "/api/ext/jajuverify/meetings/{meeting_id}/pulse",
    "/api/ext/jajuverify/meetings/{meeting_id}/presence",
)


def _sent(model: BaseModel | None) -> dict[str, Any] | None:
    """보낸 칸만 — 「안 보낸 것」 과 「비운 것(null)」 을 가른다."""
    if model is None:
        return None
    return model.model_dump(include=model.model_fields_set)


@router.get("/meetings", response_model=MeetingPageOut)
def meeting_list(
    kind: Kind = Query(
        description="review(심사회) · postmortem(포스트모텀) · briefing(설명회)"
    ),
    q: str = Query(default="", description="제목 · 묶음 이름 · 줄의 글의 일부"),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeetingPageOut:
    """회의 목록 — kind(review · postmortem · briefing) 필수, q 로 제목 · 묶음 · 칸 글 검색.

    일자가 새것부터, 줄 수 · 작성 완료 수 · 확정과 함께. 볼 수 있는 것만(ADR 0034)."""
    capped = clamp_limit(limit)
    items, total = services.meetings(db, user, kind=kind, q=q, limit=capped, offset=offset)
    return MeetingPageOut.model_validate(
        {"items": items, "total": total, "limit": capped, "offset": offset}
    )


@router.post("/meetings", response_model=MeetingOut, status_code=201)
def meeting_create(
    payload: MeetingIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> MeetingOut:
    """회의 만들기 — kind · title · held_on · workspace_slug(그 부서의 구성원만)."""
    made = services.create_meeting(
        db,
        user,
        kind=payload.kind,
        title=payload.title,
        held_on=payload.held_on,
        workspace_slug=payload.workspace_slug,
    )
    return MeetingOut.model_validate(services.one_meeting(db, made))


@router.get("/meetings/{meeting_id}", response_model=SheetOut)
def meeting_sheet(
    meeting_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> SheetOut:
    """회의 한 장 읽기 — 고치기 전에 먼저 읽는다(expected 에 실을 지금 값 · 줄 id · 묶음 id).

    머리 · 묶음(`groups`) · 줄 전부(묶음 없는 것부터 표의 차례대로) · 칸마다 사진(정본) · 열
    정의(`cells` — kind `text` · `schedule`) · 수정할 수 있나(`can_write` · `can_manage`)를 한
    번에."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    return SheetOut.model_validate(services.sheet(db, user, meeting))


@router.get("/meetings/{meeting_id}/pulse", response_model=PulseOut)
def meeting_pulse(
    meeting_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> PulseOut:
    """바뀌었나 — 판 번호(revision) · 확정 · 30초 안에 칸을 수정하던 다른 사람.

    판 번호가 가진 것과 다를 때만 한 장(`GET meetings/{id}`)을 다시 읽는다(화면이
    10~15초마다)."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    return PulseOut.model_validate(services.pulse(db, user, meeting))


@router.patch("/meetings/{meeting_id}", response_model=MeetingOut)
def meeting_update(
    meeting_id: uuid.UUID,
    payload: MeetingPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeetingOut:
    """회의 제목 · 일자 수정 — 고친 칸마다 expected(읽은 값) 필수, 관리 권한.

    **보낸 칸만** 바꾼다(일자를 비우려면 `held_on: null`). 만든 사람 · 그 부서 관리자 · 시스템
    관리자. 그사이 남이 고친 칸은 409(`details.current` · `updated_by`) — 덮어쓰지 말고
    사람에게."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    changes = payload.model_dump(include=payload.model_fields_set - {"expected"})
    if "title" in changes and changes["title"] is None:
        changes.pop("title")  # 제목은 비울 수 없다 — null 은 「안 보냄」 으로 읽는다
    services.update_meeting(db, user, meeting, changes, _sent(payload.expected))
    return MeetingOut.model_validate(services.one_meeting(db, meeting))


@router.delete("/meetings/{meeting_id}", status_code=204)
def meeting_delete(
    meeting_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """회의 삭제 — 사람에게 먼저 묻는다. 관리 권한, 확정된 회의는 409.

    `deleted_at` 만 채운다(지우지 않는다). 묶음 · 줄 · 사진은 남지만 화면에서 함께 사라진다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    services.delete_meeting(db, user, meeting)
    return Response(status_code=204)


@router.post("/meetings/{meeting_id}/confirm", response_model=SheetOut)
def meeting_confirm(
    meeting_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> SheetOut:
    """회의 확정 — 사람에게 먼저 묻는다. 그 뒤 이 회의의 쓰기가 전부 409.

    줄 · 묶음 · 사진 · 작성 완료 · 머리 · 옮기기 · 지금 수정하는 칸이 막힌다(확정 해제까지).
    만든 사람 · 그 부서 관리자 · 시스템 관리자. 바뀐 한 장을 돌려준다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    services.confirm(db, user, meeting)
    return SheetOut.model_validate(services.sheet(db, user, meeting))


@router.delete("/meetings/{meeting_id}/confirm", response_model=SheetOut)
def meeting_unconfirm(
    meeting_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> SheetOut:
    """확정 해제 — 다시 수정할 수 있다. 확정과 같은 사람. 바뀐 한 장을 돌려준다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    services.unconfirm(db, user, meeting)
    return SheetOut.model_validate(services.sheet(db, user, meeting))


@router.put("/meetings/{meeting_id}/presence", status_code=204)
def presence_touch(
    meeting_id: uuid.UUID,
    payload: PresenceIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """지금 이 칸을 수정하는 중(화면용) — row_id/group_id + field. AI 는 부르지 않아도 된다.

    칸에 들어가면, 머무는 동안 10초마다. 다른 사람의 `pulse` 에 30초 동안 보인다. 감사는 남기지
    않는다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    services.touch_presence(
        db,
        user,
        meeting,
        row_id=payload.row_id,
        group_id=payload.group_id,
        field=payload.field,
    )
    return Response(status_code=204)


@router.delete("/meetings/{meeting_id}/presence", status_code=204)
def presence_leave(
    meeting_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """칸을 떠났다(화면용) — 내 「수정 중」 을 지운다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    services.leave_presence(db, user, meeting)
    return Response(status_code=204)


@router.post("/meetings/{meeting_id}/rows", response_model=RowOut, status_code=201)
def row_create(
    meeting_id: uuid.UUID,
    payload: RowIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RowOut:
    """줄 추가 — after_id 아래(그 줄의 묶음) 또는 group_id 묶음의 맨 아래, 처음 값을 함께.

    둘 다 비우면 묶음 없음(표 맨 위)의 맨 아래. 처음 값(글 칸 넷 · `schedule`)에는 `expected`
    가 없다. 그 회의 부서의 구성원."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    row = services.add_row(
        db,
        user,
        meeting,
        after_id=payload.after_id,
        group_id=payload.group_id,
        values=payload.model_dump(exclude={"after_id", "group_id"}),
    )
    return RowOut.model_validate(services.one_row(db, user, row))


@router.post("/meetings/{meeting_id}/move", response_model=ArrangeOut)
def meeting_move(
    meeting_id: uuid.UUID,
    payload: MoveIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ArrangeOut:
    """줄 · 묶음 옮기기(상대 동작, 차례 충돌 없음) — rows+step · group+step · rows+to.

    - `{"rows": [줄], "step": "up"|"down"}` — 그 묶음 안의 이웃과 자리를 바꾼다(경계는 안
      넘는다)
    - `{"group": 묶음, "step": "up"|"down"}` — 묶음의 차례
    - `{"rows": [줄…], "to": {"group": 묶음|null, "before": 줄?, "after": 줄?, "at":
      "start"|"end"}}` — 그 자리로(여럿이면 지금 표의 차례대로). 기준 줄이 살아 있으면 그 줄의
      묶음이 이긴다.

    서버가 그때의 차례에 대고 적용하므로 그사이 남이 줄을 더하거나 지워도 409 가 아니다. 사라진
    줄 · 묶음은 건너뛰고 `skipped` 에 적는다(기준 줄이 사라졌으면 `anchor_missing` — 그 묶음의
    끝에 놓았다). 바뀐 한 장을 돌려준다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    done = services.move(
        db,
        user,
        meeting,
        rows=payload.rows,
        group=payload.group,
        step=payload.step,
        to=payload.to.model_dump() if payload.to else None,
    )
    return ArrangeOut.model_validate(
        {
            **services.sheet(db, user, meeting),
            "skipped": done.skipped,
            "anchor_missing": done.anchor_missing,
            "changed": done.changed,
        }
    )


@router.post("/meetings/{meeting_id}/groups", response_model=ArrangeOut, status_code=201)
def group_create(
    meeting_id: uuid.UUID,
    payload: GroupIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ArrangeOut:
    """묶음 추가 — title, row_ids(지금 표의 차례대로 그 묶음으로 옮길 줄 — 비워도 된다).

    맨 아래에 선다. 그사이 지워진 줄은 건너뛰고 `skipped` 에 적는다. 바뀐 한 장과 새 묶음의
    id(`group_id`)를 돌려준다."""
    meeting = services.meeting_or_404(db, user, meeting_id)
    made, skipped = services.create_group(
        db, user, meeting, title=payload.title, row_ids=payload.row_ids
    )
    return ArrangeOut.model_validate(
        {**services.sheet(db, user, meeting), "skipped": skipped, "group_id": made.id}
    )


@router.patch("/groups/{group_id}", response_model=GroupOut)
def group_update(
    group_id: uuid.UUID,
    payload: GroupPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> GroupOut:
    """묶음 이름 수정 — title 과 expected.title(읽은 이름) 필수.

    그사이 남이 고쳤으면 409(`details.current`) — 덮어쓰지 말고 사람에게."""
    group, meeting = services.group_or_404(db, user, group_id)
    services.update_group(
        db, user, group, meeting, title=payload.title, expected=_sent(payload.expected)
    )
    return GroupOut.model_validate(services.one_group(db, group))


@router.delete("/groups/{group_id}", response_model=SheetOut)
def group_delete(
    group_id: uuid.UUID,
    with_rows: bool = Query(
        default=False,
        description="false — 묶음만 해제(줄은 묶음 없음의 맨 아래로) · true — 그 줄까지 삭제",
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SheetOut:
    """묶음 삭제 — 사람에게 먼저 묻는다. with_rows=false 면 묶음만 해제, true 면 줄까지.

    묶음만 해제하면 줄은 묶음 없음의 맨 아래로 간다. 줄까지면 `deleted_at`(사진은 남는다). 바뀐
    한 장을 돌려준다."""
    group, meeting = services.group_or_404(db, user, group_id)
    services.delete_group(db, user, group, meeting, with_rows=with_rows)
    return SheetOut.model_validate(services.sheet(db, user, meeting))


@router.patch("/rows/{row_id}", response_model=RowOut)
def row_update(
    row_id: uuid.UUID,
    payload: RowPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RowOut:
    """칸 수정 — 고친 칸마다 expected(읽은 값) 필수. 글 칸 · schedule, 보낸 칸만.

    - 글 칸(`topic` · `problem` · `action` · `remark`) — 빈 글로 비운다. **사진의 자리 · 폭은
      그 칸 글 안의 한 줄 표시** `![](photo:<첨부 id>?w=50)`(제 줄 하나, 폭은 칸 너비의 %
      10~100) — 그 줄을 옮기면 자리가, `w` 를 고치면 폭이 바뀐다. 표시가 없는 사진은 칸 끝에
      선다.
    - `schedule` — `[{"date": "YYYY-MM-DD", "label": "설계 변경"}]`(날짜 차례로 저장, `[]` 로
      비움). `due_on` 은 그 가장 늦은 날로 따라온다(직접 못 쓴다).
    - `expected` 가 빠진 칸이 있으면 422(먼저 한 장을 읽는다). 그사이 남이 같은 칸을 고쳤으면
      409(`details.current` · `updated_by` · `updated_at`) — 덮어쓰지 말고 사람에게 묻는다.
      다른 칸끼리는 부딪치지 않는다."""
    row, meeting = services.row_or_404(db, user, row_id)
    services.update_row(
        db,
        user,
        row,
        meeting,
        payload.model_dump(include=payload.model_fields_set - {"expected"}),
        _sent(payload.expected),
    )
    return RowOut.model_validate(services.one_row(db, user, row))


@router.put("/rows/{row_id}/done", response_model=RowOut)
def row_done(
    row_id: uuid.UUID,
    payload: DoneIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RowOut:
    """작성 완료 켜기 · 끄기 — {"done": true|false}. 그 회의 부서의 구성원 누구나."""
    row, meeting = services.row_or_404(db, user, row_id)
    services.set_done(db, user, row, meeting, done=payload.done)
    return RowOut.model_validate(services.one_row(db, user, row))


@router.delete("/rows/{row_id}", status_code=204)
def row_delete(
    row_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """줄 삭제 — 사람에게 먼저 묻는다. `deleted_at` 만(사진 파일은 남는다)."""
    row, meeting = services.row_or_404(db, user, row_id)
    services.delete_row(db, user, row, meeting)
    return Response(status_code=204)
