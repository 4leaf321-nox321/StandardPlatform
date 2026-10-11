"""검토함의 결정 기록(`review_decisions`) — 무엇을 누가 언제 어떻게 정했나, 추천을 따랐나.

검토함은 항목을 복사해 두지 않는다 — 원래 자리(못 찾은 말 · 별칭 · 객체 · 참조 색인)에서 읽는다.
남기는 것은 사람의 결정뿐이다: 묶음 복원(`batch_id` · `undo`), 추천의 정확도(`followed`),
원래 자리에 「무시」 가 없는 종류의 무시 기억(`signature`)(ADR 0027).

Revision ID: 0074_review_queue
Revises: 0073_access_timing
Create Date: 2026-10-10 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0074_review_queue"
down_revision: Union[str, Sequence[str], None] = "0073_access_timing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("item_id", sa.String(length=300), nullable=False),
        sa.Column("question", sa.String(length=500), server_default="", nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("choice", sa.String(length=200), server_default="", nullable=False),
        sa.Column("choice_label", sa.String(length=300), server_default="", nullable=False),
        sa.Column("recommended", sa.String(length=200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("followed", sa.Boolean(), nullable=True),
        sa.Column("mode", sa.String(length=12), server_default="one", nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("message", sa.String(length=500), server_default="", nullable=False),
        sa.Column("rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("signature", sa.String(length=64), server_default="", nullable=False),
        sa.Column(
            "undo",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("undoable", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("type_slug", sa.String(length=64), server_default="", nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("decided_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_review_decisions_decided_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["undone_by_id"],
            ["users.id"],
            name=op.f("fk_review_decisions_undone_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_review_decisions_workspace_id_workspaces"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_decisions")),
    )
    op.create_index(
        "ix_review_decisions_batch", "review_decisions", ["batch_id"], unique=False
    )
    op.create_index(
        "ix_review_decisions_item", "review_decisions", ["kind", "item_id"], unique=False
    )
    op.create_index(
        "ix_review_decisions_kind_at", "review_decisions", ["kind", "decided_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_review_decisions_kind_at", table_name="review_decisions")
    op.drop_index("ix_review_decisions_item", table_name="review_decisions")
    op.drop_index("ix_review_decisions_batch", table_name="review_decisions")
    op.drop_table("review_decisions")
