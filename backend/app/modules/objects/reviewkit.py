"""검토함 제공자들이 함께 쓰는 것(ADR 0027) — 객체 쪽 넷(`review.py`)과 낡은 파생물
(`lineage_review.py`), 제안된 관계(`proposal_review.py`, ADR 0042)가 같은 셈을 쓴다.

    digest     항목 id · 지문을 만드는 짧은 해시
    Editable   「이 사람이 이 부서 것을 고칠 수 있나」 — 부서마다 한 번만 묻는다
    ignored    결정 기록에만 기억하는 「무시」 — (항목, 지문). 지문이 바뀌면 다시 뜬다

제공자마다 따로 적으면 같은 「무시」 가 종류마다 다른 뜻이 된다(어느 것은 지문을 보고 어느
것은 안 본다).
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.review.models import ReviewDecision
from app.shared.errors import AppError, code
from app.shared.permissions import require_owner_edit


def digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class Editable:
    """「이 사람이 이 부서 것을 고칠 수 있나」 — 부서마다 한 번만 묻는다."""

    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user
        self.seen: dict[uuid.UUID | None, str | None] = {}

    def why_not(self, workspace_id: uuid.UUID | None) -> str | None:
        if workspace_id not in self.seen:
            try:
                require_owner_edit(
                    self.db,
                    self.user,
                    workspace_id,
                    what="객체",
                    code_value=code("OBJECTS", 12),
                )
            except AppError as denied:
                self.seen[workspace_id] = denied.message
            else:
                self.seen[workspace_id] = None
        return self.seen[workspace_id]


def ignored(db: Session, kind: str) -> set[tuple[str, str]]:
    """결정 기록에만 기억하는 무시 — (항목, 지문). 복원한 것은 뺀다."""
    return {
        (item_id, signature)
        for item_id, signature in db.execute(
            select(ReviewDecision.item_id, ReviewDecision.signature).where(
                ReviewDecision.kind == kind,
                ReviewDecision.action == "ignore",
                ReviewDecision.status == "done",
                ReviewDecision.undone_at.is_(None),
            )
        )
    }
