"""검토함의 자동 확인 규칙(ADR 0075).

시스템 관리자가 종류 하나를 켜면 밤마다 「추천대로」 처리한다 — 켠 사람의 이름으로, 사람이
하나씩 정한 결정의 기록이 그 확신도 구간에서 충분히 맞을 때만. 결정은 묶음으로 남아 복원한다.

Revision ID: 0101_review_auto_rules
Revises: 0100_search_miss_suggestions
Create Date: 2026-11-17 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0101_review_auto_rules"
down_revision: Union[str, Sequence[str], None] = "0100_search_miss_suggestions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_auto_rules",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("threshold", sa.Float(), server_default="0.95", nullable=False),
        sa.Column("enabled_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_note", sa.String(length=500), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["enabled_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("kind"),
    )


def downgrade() -> None:
    op.drop_table("review_auto_rules")
