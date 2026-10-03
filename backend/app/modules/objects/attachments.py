"""객체에 첨부를 붙이는 자리 — 공통 틀(files)이 묻고 객체가 답한다(ADR 0012).

files 는 객체 표를 모른다(ADR 0001). 그래서 예전에는 올린 쪽이 고른 부서의 관리자이기만
하면 아무 id · 아무 자리에 붙었다 — 없는 객체, 남의 객체, 파일 칸이 아닌 자리에도. 여기서
셋을 본다:

- 객체가 있고 보이나(없는 것과 안 보이는 것은 같은 말로 답한다)
- 그 객체를 고칠 수 있나 — 소유 부서 관리자, 허브가 내려준 타입이면 받는 쪽은 못 고친다
- 자리가 그 타입의 **파일 칸**인가, 그 칸이 「이미지만」 인가

첨부의 부서는 **객체의 부서**를 따른다 — 올린 쪽이 부서를 고르면 객체와 첨부가 서로 다른
부서에 서고, 객체를 보는 사람이 사진을 못 본다.

붙이고 뗀 일은 객체의 이력에 남는다(`object.attachment.add` · `.remove`) — 지켜보기 ·
웹훅도 같은 기록에서 나간다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import properties_of
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType
from app.shared import audit
from app.shared.errors import Conflict, NotFound, code
from app.shared.extensions import AttachmentOwner
from app.shared.permissions import require_owner_edit, visible_owner_clause

ATTACHED = "object.attachment.add"
DETACHED = "object.attachment.remove"


def owner(
    db: Session, user: User, owner_id: uuid.UUID, owner_field: str | None
) -> AttachmentOwner:
    """`extensions.register_attachment_owner("objects", owner)` 로 등록된다."""
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == owner_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if row is None:
        raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")
    object_type = db.get(ObjectType, row.type_id)
    assert object_type is not None  # FK 다
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
    )
    managed.require_objects_editable(object_type, what="첨부를 변경하지")

    accept: str | None = None
    if owner_field is not None:
        props = properties_of(db, object_type.id)
        prop = next((one for one in props if one.key == owner_field), None)
        if prop is None or prop.data_type != "file":
            raise Conflict(
                code("OBJECTS", 97),
                f"{object_type.label}에는 파일 속성 「{owner_field}」 가 없습니다 — 첨부는 "
                "파일 속성이나 「그 밖의 첨부」에만 업로드할 수 있습니다.",
                details={"type_slug": object_type.slug, "owner_field": owner_field},
            )
        accept = prop.accept

    def changed(db: Session, user: User, field: str | None, name: str, added: bool) -> None:
        audit.record(
            db,
            action=ATTACHED if added else DETACHED,
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=row.label,
            workspace_id=row.owner_workspace_id,
            # 자리 없는 첨부는 `attachments._` — 이력 화면이 「그 밖의 첨부」 로 읽는다.
            changes={
                f"attachments.{field or '_'}": {
                    "before": None if added else name,
                    "after": name if added else None,
                }
            },
        )

    return AttachmentOwner(
        workspace_id=row.owner_workspace_id, accept=accept, label=row.label, changed=changed
    )
