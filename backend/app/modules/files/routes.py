"""첨부 라우터 — 올리기·목록·내려받기·떼기.

**내려받기는 평범한 링크로 안 된다.** access 토큰은 메모리에만 있어서 브라우저가
스스로 여는 주소(a href · img src)에는 안 실린다 — 프론트는 `downloadFile` 로
받는다(`shared/api/client.ts`).
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.files import services
from app.modules.files.schemas import AttachmentOut
from app.shared import filestore
from app.shared.auth import current_user

router = APIRouter(prefix="/attachments", tags=["files"])


@router.post("", response_model=AttachmentOut, status_code=201)
def upload(
    owner_table: str = Form(max_length=60),
    owner_id: uuid.UUID = Form(),
    owner_field: str | None = Form(default=None, max_length=48),
    workspace_slug: str | None = Form(default=None),
    upload_file: UploadFile = File(alias="file"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttachmentOut:
    """파일 하나를 어떤 자료에 붙인다.

    `owner_table` · `owner_id` 는 **외래키가 아니다** — 공통 틀은 도메인 표를
    모른다. 붙이는 쪽이 자기 표 이름을 준다.
    """
    return services.upload(
        db,
        user=user,
        owner_table=owner_table,
        owner_id=owner_id,
        owner_field=owner_field,
        workspace_slug=workspace_slug,
        filename=upload_file.filename or "이름없음",
        content_type=upload_file.content_type,
        stream=upload_file.file,
    )


@router.get("", response_model=list[AttachmentOut])
def list_attachments(
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AttachmentOut]:
    """그 자료의 첨부들. `owner_field` 를 주면 **그 자리의 것만** 나온다."""
    return services.list_for(
        db, user=user, owner_table=owner_table, owner_id=owner_id, owner_field=owner_field
    )


@router.get("/{attachment_id}/content", include_in_schema=False)
def download(
    attachment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """파일 내용.

    **스키마에 안 싣는다** — 생성되는 프론트 타입에 파일 응답이 끼면 그 타입은
    `unknown` 이 되고, 그것을 쓰는 화면이 타입 검사를 통과해 버린다.
    """
    attachment = services.get_for_download(db, user=user, attachment_id=attachment_id)
    path = filestore.resolve(attachment.relative_path)
    services.ensure_readable(path is not None)
    assert path is not None  # ensure_readable 이 None 이면 이미 던졌다

    # 파일 이름은 둘로 낸다 — ASCII 는 옛 브라우저용, UTF-8 은 한글 이름용.
    disposition = "attachment; filename=\"download\"; filename*=UTF-8''" + quote(
        attachment.original_name
    )
    return FileResponse(
        path,
        media_type=attachment.content_type,
        headers={"Content-Disposition": disposition},
    )


@router.delete("/{attachment_id}", status_code=204)
def remove(
    attachment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.remove(db, user=user, attachment_id=attachment_id)
