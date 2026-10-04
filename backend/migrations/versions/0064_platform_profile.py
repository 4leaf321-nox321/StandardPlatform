"""플랫폼의 자기소개 — 무엇을 담고, 다른 플랫폼과 어떤 사이인가(한 행).

같은 틀로 띄운 플랫폼 여럿이 한 에이전트에 도구로 붙으면 도구 이름 · 설명이 전부 같아, 어느 플랫폼에
물을지 가를 단서가 slug 하나뿐이었다. MCP 서버가 이 행을 읽어 안내문 첫머리 · `whoami` 에 싣는다.
`facts_seen` 은 사람이 쓸 때 무엇이 담겨 있었나 — 지금과 달라지면 소개가 낡은 것이다.

Revision ID: 0064_platform_profile
Revises: 0063_datasource_reconciled_at
Create Date: 2026-10-04 19:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0064_platform_profile"
down_revision: Union[str, Sequence[str], None] = "0063_datasource_reconciled_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "platform_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(length=300), server_default="", nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "facts_seen",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("platform_profile")
