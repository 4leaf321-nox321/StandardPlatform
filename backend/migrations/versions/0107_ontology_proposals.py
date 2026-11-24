"""정의 제안(ADR 0096).

AI · 사람이 낸 정의를 곧바로 적용하지 않고 쌓아 두는 표 — 보낸 정의(`schema`), 그때의 계획(`plan`)과
지문, 근거 · 누가 · 어느 도구. 시스템 관리자가 검토함에서 적용 · 거절한다. 새 표라 비어서 시작한다.

Revision ID: 0107_ontology_proposals
Revises: 0106_type_label_prefix
Create Date: 2026-11-25 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0107_ontology_proposals"
down_revision: Union[str, Sequence[str], None] = "0106_type_label_prefix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ontology_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column(
            "schema",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("source", sa.String(length=40), server_default="", nullable=False),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), server_default="", nullable=False),
        sa.Column(
            "planned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("summary", sa.String(length=300), server_default="", nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("origin_ref", sa.String(length=300), server_default="", nullable=False),
        sa.Column("tool", sa.String(length=64), server_default="", nullable=False),
        sa.Column("token_name", sa.String(length=120), server_default="", nullable=False),
        sa.Column("proposed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), server_default="", nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected', 'withdrawn')",
            name=op.f("ck_ontology_proposals_status"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_ontology_proposals_decided_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_id"],
            ["users.id"],
            name=op.f("fk_ontology_proposals_proposed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["ontology_snapshots.id"],
            name=op.f("fk_ontology_proposals_snapshot_id_ontology_snapshots"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_proposals")),
    )
    op.create_index(
        "ix_ontology_proposals_by",
        "ontology_proposals",
        ["proposed_by_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_ontology_proposals_open",
        "ontology_proposals",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'proposed'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ontology_proposals_open",
        table_name="ontology_proposals",
        postgresql_where=sa.text("status = 'proposed'"),
    )
    op.drop_index("ix_ontology_proposals_by", table_name="ontology_proposals")
    op.drop_table("ontology_proposals")
