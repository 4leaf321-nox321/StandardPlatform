"""객체를 누가 넣고 고치나 — **기록은 구성원도 넣는다**(ADR 0036).

축(모델 · 부품 · 과제 …)은 여러 기록이 가리키는 기준이라 그 부서의 관리자가 고친다. 기록은
일어난 일을 적는 것이라 **그 부서의 구성원이 넣고, 넣은 사람이 고친다.** 관리자만 넣게 두면
보고 넣는 사람이 관리자에게 부탁해야 하고, 부탁은 늦거나 빠진다 — 사용자 안내서가 「멤버: 보고
넣음」 이라고 적은 채로 코드는 관리자만 받던 어긋남이 그것이었다(2026-10-23).

다른 객체에 손대는 일(합치기 · 참조를 끊고 삭제)과 일괄 입력은 여전히 부서 관리자의 일이다 — 내
기록을 지우면서 남의 기록의 칸을 비우는 것은 「내 기록을 고치는 것」 이 아니다.

판정은 여기 하나다. 화면의 단추(`can_edit`)와 서버의 거절이 같은 식을 쓴다.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared.errors import Forbidden
from app.shared.permissions import (
    membership_of,
    require_owner_edit,
    resolve_owner_workspace,
    workspace_by_slug,
)


def is_record(object_type: ObjectType) -> bool:
    """기록 타입인가(용도 `log`). 축 타입은 지금까지처럼 관리자가 넣는다."""
    return object_type.usage == "log"


def resolve_create_owner(
    db: Session,
    user: User,
    object_type: ObjectType,
    slug: str | None,
    *,
    code_value: str,
) -> uuid.UUID | None:
    """만들 때 어느 부서 것으로 할지 — 기록이면 그 부서의 **구성원**이면 된다.

    부서 없이(전역) 만드는 것은 기록이어도 시스템 관리자뿐이다 — 전역은 여러 부서가 함께 쓴다.
    """
    if slug is None or not is_record(object_type):
        return resolve_owner_workspace(db, user, slug, what="객체", code_value=code_value)
    workspace = workspace_by_slug(db, slug)
    if not user.is_system_admin and not _member(db, user, workspace.id):
        raise Forbidden(
            code_value,
            f"「{workspace.name}」 의 구성원만 이 부서에 기록을 추가할 수 있습니다.",
        )
    return workspace.id


def require_edit(
    db: Session,
    user: User,
    object_type: ObjectType,
    row: ObjectInstance,
    *,
    code_value: str,
) -> None:
    """이 객체를 고칠 수 있나 — 축은 소유 부서의 관리자, 기록은 **넣은 사람**도."""
    if is_record(object_type) and _authored(db, user, row):
        return
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code_value
        )
    except Forbidden:
        # 전역 기록은 시스템 관리자만 — 원래 말(「여러 부서가 함께 쓰기 때문」)이 맞다.
        if not is_record(object_type) or row.owner_workspace_id is None:
            raise
        raise Forbidden(
            code_value, "이 기록은 만든 사람과 부서 관리자만 수정할 수 있습니다."
        ) from None


def can_edit(db: Session, user: User, object_type: ObjectType, row: ObjectInstance) -> bool:
    """`require_edit` 를 참 · 거짓으로 — 화면이 단추를 세울지."""
    try:
        require_edit(db, user, object_type, row, code_value="")
    except Exception:
        return False
    return True


def _authored(db: Session, user: User, row: ObjectInstance) -> bool:
    """내가 넣은 기록이고 **아직 그 부서의 구성원**인가. 부서를 떠나면 그 부서의 기록은 그
    부서의 관리자가 맡는다 — 떠난 사람이 계속 고치면 부서는 자기 자료를 모른다."""
    return (
        row.created_by_id == user.id
        and row.owner_workspace_id is not None
        and _member(db, user, row.owner_workspace_id)
    )


def _member(db: Session, user: User, workspace_id: uuid.UUID) -> bool:
    return membership_of(db, workspace_id=workspace_id, user_id=user.id) is not None
