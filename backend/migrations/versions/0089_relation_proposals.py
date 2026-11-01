"""제안된 관계(ADR 0042) — 선으로 만들기 전에 남기는 「이 둘은 이어져야 할 것 같다」.

`object_relations` 에 상태 칸을 두지 않고 **새 표 하나**다 — 선을 읽는 자리(트리 · 롤업 · 지표 ·
코어 API · RDF · 묶음 · 개수 제약 · 순환 · 유일 색인 · 계수표 트리거)는 이 표를 모른다. 확정은
보통의 선 만들기라 그쪽 트리거 · 제약이 그대로 돈다.

**운영에서 드는 것** — 빈 표와 그 색인을 만들 뿐이라 즉시다(있는 표는 안 건드린다). FK 셋(객체
둘 · 선)이 붙지만 새 표 쪽의 제약이라 가리켜지는 표(객체 · 선)를 훑지 않는다.

Revision ID: 0089_relation_proposals
Revises: 0088_lineage
Create Date: 2026-10-30 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0089_relation_proposals"
down_revision: Union[str, Sequence[str], None] = "0088_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "relation_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("src_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dst_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("evidence_note", sa.String(length=500), server_default="", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("origin", sa.String(length=20), server_default="user", nullable=False),
        sa.Column("origin_ref", sa.String(length=200), server_default="", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="proposed", nullable=False),
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
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected')",
            name=op.f("ck_relation_proposals_status"),
        ),
        sa.CheckConstraint(
            "origin IN ('user', 'mcp', 'rule', 'datasource', 'import')",
            name=op.f("ck_relation_proposals_origin"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_relation_proposals_confidence"),
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to",
            name=op.f("ck_relation_proposals_period"),
        ),
        sa.ForeignKeyConstraint(
            ["src_object_id"],
            ["objects.id"],
            name=op.f("fk_relation_proposals_src_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dst_object_id"],
            ["objects.id"],
            name=op.f("fk_relation_proposals_dst_object_id_objects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relation_id"],
            ["object_relations.id"],
            name=op.f("fk_relation_proposals_relation_id_object_relations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_id"],
            ["users.id"],
            name=op.f("fk_relation_proposals_proposed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_relation_proposals_decided_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relation_proposals")),
    )
    op.create_index(
        "uq_relation_proposals_open",
        "relation_proposals",
        ["relation", "src_object_id", "dst_object_id"],
        unique=True,
        postgresql_where=sa.text("status = 'proposed'"),
    )
    op.create_index(
        "ix_relation_proposals_src", "relation_proposals", ["src_object_id", "status"]
    )
    op.create_index(
        "ix_relation_proposals_dst", "relation_proposals", ["dst_object_id", "status"]
    )
    op.create_index(
        "ix_relation_proposals_open_created",
        "relation_proposals",
        ["created_at"],
        postgresql_where=sa.text("status = 'proposed'"),
    )
    op.create_index(
        "ix_relation_proposals_relation_id",
        "relation_proposals",
        ["relation_id"],
        postgresql_where=sa.text("relation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_relation_proposals_relation_id", table_name="relation_proposals")
    op.drop_index("ix_relation_proposals_open_created", table_name="relation_proposals")
    op.drop_index("ix_relation_proposals_dst", table_name="relation_proposals")
    op.drop_index("ix_relation_proposals_src", table_name="relation_proposals")
    op.drop_index("uq_relation_proposals_open", table_name="relation_proposals")
    op.drop_table("relation_proposals")
