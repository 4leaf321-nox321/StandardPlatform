"""첨부 — **무엇에 붙었는지는 문자열 두 칸으로 든다.**

도메인마다 첨부 표를 따로 만들면 업로드·내려받기·권한 판정이 그 수만큼 갈라진다.
그렇다고 공통 틀이 도메인 표에 외래키를 걸 수도 없다 — 그러면 이 모듈이 도메인을
알게 되고, 포크한 플랫폼은 없는 표를 가리키게 된다.

그래서 **느슨하게 가리킨다**: `owner_table` + `owner_id`. 외래키가 아니므로 DB 가
정합성을 지켜 주지 않는 대신, 도메인이 무엇이든 이 표 하나로 끝난다.

`workspace_id` 를 함께 두는 이유: 권한 판정이 도메인 표를 몰라도 되게 하려는
것이다. 첨부를 만들 때 그 자료의 소유 부서를 함께 적어 두면, 내려받기는 이 칸만
보고 판정할 수 있다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    owner_table: Mapped[str] = mapped_column(String(60), index=True)
    """무슨 표에 붙었나. **외래키가 아니다** — 공통 틀은 도메인 표를 모른다."""
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), index=True)

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """어느 부서의 것인가. NULL 은 전역이다.

    **이 칸이 없으면 내려받기가 도메인 표를 조회해야 한다** — 그러면 이 모듈이
    도메인을 알게 된다. RESTRICT 인 이유는 부서를 지울 때 첨부가 조용히 사라지지
    않게 하려는 것이고, 부서 삭제 확인 화면이 그 수를 보여 준다."""

    original_name: Mapped[str] = mapped_column(String(255))
    """사람이 올린 이름. 저장 이름은 해시라 이것이 없으면 무슨 파일인지 알 수 없다."""
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")

    sha256: Mapped[str] = mapped_column(String(64), index=True)
    """내용의 해시이자 저장 이름. **같은 내용은 한 번만 저장된다** — 여러 행이 같은
    해시를 가리킬 수 있어서 unique 가 아니다."""
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    """**우리가 실제로 쓴 바이트.** 브라우저가 보낸 값을 믿지 않는다."""

    relative_path: Mapped[str] = mapped_column(String(300))
    """filestore 아래의 상대 경로. **절대경로를 넣지 않는다** — 서버를 옮기면 그
    값 전부가 틀린 것이 된다."""

    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """**행만 지운다. 파일은 안 지운다** — 같은 내용을 다른 행이 가리킬 수 있다."""
