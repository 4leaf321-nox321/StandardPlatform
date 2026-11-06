"""문서 속성(ADR 0053) — 지금 본문(`object_documents`) · 판(`object_document_revisions`) · 언급
(`object_document_mentions`).

셋 다 비어서 시작한다 — 문서 속성(`data_type = 'document'`)을 정의하고 본문을 쓰면 채워진다.
속성 종류는 글자 칸이라 마이그레이션이 없다(`DATA_TYPES` 에 하나 더했다).

내리면 표를 지운다 — **문서 본문이 사라진다.** 내리기 전에 백업한다.

Revision ID: 0095_documents
Revises: 0094_semantic
Create Date: 2026-11-06 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0095_documents"
down_revision: Union[str, Sequence[str], None] = "0094_semantic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "object_documents",
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chars", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_via", sa.String(length=120), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("object_id", "key"),
    )
    op.create_table(
        "object_document_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("chars", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("via", sa.String(length=120), server_default="", nullable=False),
        sa.Column("note", sa.String(length=200), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_id", "key", "revision"),
    )
    op.create_table(
        "object_document_mentions",
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["objects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id", "key", "target_id"),
    )
    op.create_index(
        op.f("ix_object_document_mentions_target_id"),
        "object_document_mentions",
        ["target_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_object_document_mentions_target_id"), table_name="object_document_mentions"
    )
    op.drop_table("object_document_mentions")
    op.drop_table("object_document_revisions")
    op.drop_table("object_documents")
