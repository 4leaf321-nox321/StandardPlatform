"""뜻으로 찾기의 조각(ADR 0054) — 객체 하나에 자리 여럿(기본 글 + 문서의 절마다), 맞춰 본 때.

- `object_embeddings` — 열쇠를 (객체, 조각)으로. `part`(""= 기본 글, "doc:<칸>:<번호>" = 문서의 절) ·
  `heading`(절의 경로) · `anchor`(그 제목의 id), 글 해시 색인(같은 글은 자리를 옮겨 쓴다).
- `semantic_marks` — 객체를 마지막으로 맞춰 본 때. 0094 에서는 자리 줄의 `embedded_at` 이 그 일을 했다 —
  조각이 없는 객체(고른 칸이 다 빈 기록)는 그 줄이 없어 5분마다 다시 보게 된다.

있던 자리는 그대로 기본 글 조각(`part = ""`)이 된다. 맞춰 본 때는 비어서 시작한다 — 다음 차례(5분
안)가 전부를 해시로 맞춰 채운다(글이 같으면 다시 바꾸지 않는다).

Revision ID: 0096_semantic_pieces
Revises: 0095_documents
Create Date: 2026-11-06 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0096_semantic_pieces"
down_revision: Union[str, Sequence[str], None] = "0095_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "object_embeddings",
        sa.Column("part", sa.String(length=200), server_default="", nullable=False),
    )
    op.add_column(
        "object_embeddings",
        sa.Column("heading", sa.String(length=300), server_default="", nullable=False),
    )
    op.add_column(
        "object_embeddings",
        sa.Column("anchor", sa.String(length=300), server_default="", nullable=False),
    )
    op.drop_constraint("pk_object_embeddings", "object_embeddings", type_="primary")
    op.create_primary_key("pk_object_embeddings", "object_embeddings", ["object_id", "part"])
    op.create_index(
        "ix_object_embeddings_text_hash", "object_embeddings", ["text_hash"], unique=False
    )
    op.create_table(
        "semantic_marks",
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["type_id"], ["object_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
    )
    op.create_index(
        op.f("ix_semantic_marks_type_id"), "semantic_marks", ["type_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_semantic_marks_type_id"), table_name="semantic_marks")
    op.drop_table("semantic_marks")
    op.drop_index("ix_object_embeddings_text_hash", table_name="object_embeddings")
    # 기본 글이 아닌 조각(문서의 절)은 0094 의 모양(객체 하나에 자리 하나)에 들어가지 않는다.
    op.execute("DELETE FROM object_embeddings WHERE part <> ''")
    op.drop_constraint("pk_object_embeddings", "object_embeddings", type_="primary")
    op.create_primary_key("pk_object_embeddings", "object_embeddings", ["object_id"])
    op.drop_column("object_embeddings", "anchor")
    op.drop_column("object_embeddings", "heading")
    op.drop_column("object_embeddings", "part")
