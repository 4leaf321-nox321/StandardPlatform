"""첨부 로직 — 올리기·목록·지우기, 그리고 **누가 볼 수 있는가.**

권한은 `workspace_id` 한 칸으로 판정한다. 도메인 표를 조회하지 않는다 — 그러면
이 모듈이 도메인을 알게 되고, 포크한 플랫폼은 없는 표를 가리킨다.
"""

from __future__ import annotations

import uuid
from typing import BinaryIO

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.files.models import Attachment
from app.modules.files.schemas import AttachmentOut
from app.shared import extensions, filestore
from app.shared.errors import AppError, Forbidden, NotFound, code
from app.shared.permissions import (
    require_owner_edit,
    resolve_owner_workspace,
    visible_owner_clause,
)
from app.shared.text import clean

#: 한 파일의 상한. **서버가 강제한다** — 화면의 검사는 우회할 수 있고, 우회되면
#: 디스크가 찬다. 디스크가 차면 앱만이 아니라 DB 도 함께 멈춘다.
MAX_BYTES = 50 * 1024 * 1024


def _out(attachment: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        owner_table=attachment.owner_table,
        owner_id=attachment.owner_id,
        owner_field=attachment.owner_field,
        original_name=attachment.original_name,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        sha256=attachment.sha256,
        created_at=attachment.created_at,
    )


def _visible(db: Session, user: User) -> Select[tuple[Attachment]]:
    """볼 수 있는 첨부. 전역 + 내 부서."""
    return select(Attachment).where(
        Attachment.deleted_at.is_(None),
        visible_owner_clause(user, Attachment.workspace_id),
    )


def upload(
    db: Session,
    *,
    user: User,
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None,
    workspace_slug: str | None,
    filename: str,
    content_type: str | None,
    stream: BinaryIO,
) -> AttachmentOut:
    """파일 하나를 붙인다.

    **쓰기 권한은 부서로 판정한다.** 전역(부서 없음)에 붙이는 것은 시스템
    관리자뿐이다 — 여러 부서가 함께 보는 자리이기 때문이다.
    """
    workspace_id = resolve_owner_workspace(
        db, user, workspace_slug, what="첨부", code_value=code("FILES", 1)
    )

    stored = filestore.save(stream)
    if stored.size == 0:
        raise AppError(code("FILES", 2), "빈 파일입니다.", status=400)
    if stored.size > MAX_BYTES:
        # **저장하고 나서 잰다.** 미리 재려면 Content-Length 를 믿어야 하는데 그
        # 값은 틀릴 수 있다. 파일은 남지만 행이 없으므로 아무도 못 본다.
        raise AppError(
            code("FILES", 3),
            f"파일이 너무 큽니다 (최대 {MAX_BYTES // 1024 // 1024}MB).",
            status=413,
            details={"size_bytes": stored.size, "max_bytes": MAX_BYTES},
        )

    attachment = Attachment(
        owner_table=owner_table,
        owner_id=owner_id,
        owner_field=owner_field,
        workspace_id=workspace_id,
        original_name=clean(filename)[:255] or "이름없음",
        content_type=(content_type or "application/octet-stream")[:120],
        sha256=stored.sha256,
        size_bytes=stored.size,
        relative_path=stored.relative_path,
        uploaded_by_id=user.id,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return _out(attachment)


def list_for(
    db: Session,
    *,
    user: User,
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None = None,
) -> list[AttachmentOut]:
    """그 자료의 첨부들.

    `owner_field` 를 주면 **그 자리의 것만** 돌려준다. 안 주면 전부다 — 자리를
    안 나눠 쓰던 기존 화면이 그대로 돈다.
    """
    stmt = _visible(db, user).where(
        Attachment.owner_table == owner_table, Attachment.owner_id == owner_id
    )
    if owner_field is not None:
        stmt = stmt.where(Attachment.owner_field == owner_field)
    rows = db.scalars(stmt.order_by(Attachment.created_at))
    return [_out(row) for row in rows]


def get_for_download(db: Session, *, user: User, attachment_id: uuid.UUID) -> Attachment:
    """내려받을 첨부. **없는 것과 못 보는 것을 같게 답한다.**

    403 으로 가르면 그 id 가 존재한다는 사실이 새고, 그것은 알려 줄 이유가 없는
    정보다.
    """
    found = db.scalar(_visible(db, user).where(Attachment.id == attachment_id))
    if found is None:
        raise NotFound(code("FILES", 4), "첨부를 찾을 수 없습니다.")
    return found


def remove(db: Session, *, user: User, attachment_id: uuid.UUID) -> None:
    """첨부를 뗀다. **행만 지운다 — 파일은 안 지운다.**

    같은 내용을 다른 행이 가리킬 수 있어서, 파일까지 지우면 그쪽이 가리킬 곳을
    잃는다. 「아무도 안 가리키는 파일」 을 정리하는 일은 따로다.
    """
    found = db.scalar(_visible(db, user).where(Attachment.id == attachment_id))
    if found is None:
        raise NotFound(code("FILES", 4), "첨부를 찾을 수 없습니다.")
    require_owner_edit(db, user, found.workspace_id, what="첨부", code_value=code("FILES", 5))
    found.deleted_at = func.now()
    db.commit()


def ensure_readable(path_exists: bool) -> None:
    """DB 에는 있는데 파일이 없을 때. **조용히 빈 파일을 주지 않는다.**

    백업을 DB 만 되돌리면 이 상태가 된다 — 그때 0바이트 파일을 내보내면 사람은
    그것을 원본이라고 믿는다.
    """
    if not path_exists:
        raise Forbidden(
            code("FILES", 6),
            "파일이 저장소에 없습니다. 백업에서 파일스토어가 함께 복구됐는지 확인하세요.",
        )


def stats(db: Session) -> list[extensions.StatItem]:
    total = (
        db.scalar(
            select(func.count()).select_from(Attachment).where(Attachment.deleted_at.is_(None))
        )
        or 0
    )
    return [extensions.StatItem(label="첨부", count=total)]


def workspace_reference(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceReference]:
    """부서 삭제 확인에 뜨는 줄.

    FK 가 RESTRICT 라 **DB 가 거부한다** — blocks_delete 를 False 로 두면 화면은
    지울 수 있다고 말하고 서버가 500 을 낸다.
    """
    count = (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceReference(
            table="attachments", label="첨부", count=count, blocks_delete=True
        )
    ]
