"""system 타입 — 원 표를 가리키는 칸과 그 끝단의 관계

`kind_class='system'` 인 타입은 `objects` 에 행이 없다(부서·계정 같은 1급 표를
투영한다). 어느 표를 비추는지가 `object_types.system_source` 고, 그 끝단과 잇는
선은 FK 로 묶을 수 없어 `object_links` 에 따로 담는다.

Revision ID: 0012_system_links
Revises: 0011_audit_seq
Create Date: 2026-09-12 07:52:43.839333

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012_system_links"
down_revision: Union[str, Sequence[str], None] = "0011_audit_seq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("src_type", sa.String(length=32), nullable=False),
        sa.Column("src_id", sa.UUID(), nullable=False),
        sa.Column("dst_type", sa.String(length=32), nullable=False),
        sa.Column("dst_id", sa.UUID(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("evidence_note", sa.String(length=500), server_default="", nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_object_links_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_links")),
        sa.UniqueConstraint("src_id", "dst_id", "relation", name="uq_object_links_edge"),
    )
    op.create_index(
        "ix_object_links_dst", "object_links", ["dst_id", "relation"], unique=False
    )
    op.create_index(
        op.f("ix_object_links_relation"), "object_links", ["relation"], unique=False
    )
    op.create_index(
        "ix_object_links_src", "object_links", ["src_id", "relation"], unique=False
    )
    op.add_column(
        "object_types",
        sa.Column("system_source", sa.String(length=40), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("object_types", "system_source")
    op.drop_index("ix_object_links_src", table_name="object_links")
    op.drop_index(op.f("ix_object_links_relation"), table_name="object_links")
    op.drop_index("ix_object_links_dst", table_name="object_links")
    op.drop_table("object_links")
