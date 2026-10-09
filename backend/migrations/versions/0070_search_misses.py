"""못 찾은 말(`search_misses`) — 검색 · 이름 풀이가 못 찾은 글자를 모아 별칭 후보로.

사람과 AI 가 이름으로 찾다가 못 찾으면 없는 줄 알고 새로 만든다. 그 말이 어떤 객체의 다른
이름이었다면 별칭으로 붙이는 순간 다음부터 찾힌다 — 그 말을 모으는 표다(ADR 0025).

Revision ID: 0070_search_misses
Revises: 0069_user_session_epoch
Create Date: 2026-10-08 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0070_search_misses"
down_revision: Union[str, Sequence[str], None] = "0069_user_session_epoch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "search_misses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=32), server_default="", nullable=False),
        sa.Column("norm", sa.String(length=200), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False),
        sa.Column("hits", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "askers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "vias",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column("decided_by_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_search_misses_decided_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["objects.id"],
            name=op.f("fk_search_misses_object_id_objects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_misses")),
        sa.UniqueConstraint("scope", "norm", name="uq_search_misses_scope_norm"),
    )
    op.create_index(
        "ix_search_misses_status_hits", "search_misses", ["status", "hits"], unique=False
    )
    op.create_index("ix_search_misses_last_at", "search_misses", ["last_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_search_misses_last_at", table_name="search_misses")
    op.drop_index("ix_search_misses_status_hits", table_name="search_misses")
    op.drop_table("search_misses")
