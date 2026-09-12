"""데이터 소스 — OData 에서 읽어 온톨로지를 채운다. 정의와 동기화 기록.

Revision ID: 0016_datasources
Revises: 0015_object_aliases
Create Date: 2026-09-12 10:12:44.619232

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016_datasources"
down_revision: Union[str, Sequence[str], None] = "0015_object_aliases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "data_sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="odata", nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("entity_set", sa.String(length=200), nullable=False),
        sa.Column("filter", sa.Text(), server_default="", nullable=False),
        sa.Column("select", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "auth",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("page_size", sa.Integer(), server_default="500", nullable=False),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column(
            "mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("deprecate_missing", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("interval_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_data_sources_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["object_types.id"],
            name=op.f("fk_data_sources_type_id_object_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_data_sources_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sources")),
        sa.UniqueConstraint("slug", name=op.f("uq_data_sources_slug")),
    )
    op.create_table(
        "data_source_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="planned", nullable=False),
        sa.Column("applied", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("actor_label", sa.String(length=200), server_default="", nullable=False),
        sa.Column("rows_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name=op.f("fk_data_source_runs_source_id_data_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_source_runs")),
    )
    op.create_index(
        op.f("ix_data_source_runs_source_id"), "data_source_runs", ["source_id"], unique=False
    )
    op.create_index(
        "ix_data_source_runs_source_started",
        "data_source_runs",
        ["source_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_data_source_runs_source_started", table_name="data_source_runs")
    op.drop_index(op.f("ix_data_source_runs_source_id"), table_name="data_source_runs")
    op.drop_table("data_source_runs")
    op.drop_table("data_sources")
