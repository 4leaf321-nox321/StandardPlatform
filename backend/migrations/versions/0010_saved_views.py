"""저장된 뷰 — 조건을 이름 붙여 두는 자리

「영남 공급사 중 심사 점수 80 미만」 을 매번 다시 거르면 사람은 곧 안 거른다.
부서가 함께 쓰는 뷰와 내 뷰를 한 표에 둔다 — workspace_id 가 NULL 이면 내 것.

Revision ID: 0010_saved_views
Revises: 0009_object_merged_into
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_saved_views"
down_revision = "0009_object_merged_into"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "saved_views",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column(
            "query",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_saved_views_owner_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["object_types.id"],
            name=op.f("fk_saved_views_type_id_object_types"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_saved_views_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_views")),
    )
    op.create_index(op.f("ix_saved_views_type_id"), "saved_views", ["type_id"], unique=False)
    op.create_index(
        "ix_saved_views_type_owner", "saved_views", ["type_id", "owner_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_saved_views_workspace_id"), "saved_views", ["workspace_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_saved_views_workspace_id"), table_name="saved_views")
    op.drop_index("ix_saved_views_type_owner", table_name="saved_views")
    op.drop_index(op.f("ix_saved_views_type_id"), table_name="saved_views")
    op.drop_table("saved_views")
