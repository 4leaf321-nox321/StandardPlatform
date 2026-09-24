"""확장 모듈의 켜짐 — **설치마다 다르고, 화면에서 바꾼다.**

`.env` 의 `EXTENSIONS` 는 여전히 **기본값**이다. 이 표에 행이 있으면 그것이 이기고,
없으면 `.env` 가 답한다 — 그래서 아무것도 안 켜 본 설치는 예전과 똑같이 돈다.

**왜 표로 옮겼나.** 운영은 uvicorn 워커 넷에 작업 워커 하나다. 켜짐이 프로세스
메모리에 있으면 넷이 서로 다른 답을 하고, A · B 이중화에서는 두 대의 `.env` 가
갈려도 평소엔 안 드러난다 — 넘어간 날 메뉴가 달라진다. 표는 한 벌이다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExtensionState(Base):
    """확장 하나의 켜짐. **행이 없으면 `.env` 의 기본값**이다."""

    __tablename__ = "extension_states"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    """확장 이름 — `app/extensions/<이름>` 의 그 이름."""
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    """**누가 바꿨나는 감사가 답한다**(`extension.toggle`) — 여기는 언제만 둔다."""
