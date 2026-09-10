"""첨부 API 형태."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_table: str
    owner_id: uuid.UUID
    original_name: str
    """사람이 올린 이름. 저장 이름은 해시라 이것이 없으면 무슨 파일인지 알 수 없다."""
    content_type: str
    size_bytes: int
    sha256: str
    """내용의 해시. **같은 파일인지 눈으로 확인할 수 있는 유일한 값이다.**"""
    created_at: datetime
