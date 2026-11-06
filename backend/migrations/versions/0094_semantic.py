"""뜻으로 찾기(ADR 0052) — 켠 타입(`semantic_targets`)과 객체마다의 뜻의 자리(`object_embeddings`).

**pgvector 를 쓰는 첫 표다.** 확장은 superuser 만 만든다(ADR 0050) — 배포(`deploy.sh` 의
`ensure_vector`)가 이 마이그레이션 앞에서 postgres 계정으로 만들고, 그 판(0.4.70)으로 한 번
올린 서버에는 이미 있다. 그래도 없으면 **여기서 할 일을 말하며 멈춘다**(superuser 면 만든다) —
확장이 없는 설치에서 이 표만 건너뛰면, 나중에 확장을 깔아도 마이그레이션은 이미 지나가 표가 안
생긴다.

둘 다 비어서 시작한다 — 관리자가 서버 화면에서 타입을 켜면 워커가 채운다.

내리면 표를 지운다(뜻의 자리는 다시 계산할 수 있는 사본이다). 확장은 남긴다.

Revision ID: 0094_semantic Revises: 0093_graph_map Create Date: 2026-11-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0094_semantic"
down_revision: Union[str, Sequence[str], None] = "0093_graph_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 그날의 사본 — 앱 코드를 import 하지 않는다(`app/shared/vectors.py` 와 같은 값 · 같은 말).
SCHEMA = "extensions"
DIM = 1024
MISSING = (
    "pgvector 확장이 없습니다(ADR 0050) — 확장은 postgres 계정만 만듭니다. "
    "운영: sudo ./deploy.sh pgvector · 개발 PC: sudo ./scripts/dev_pgvector.sh — 그 뒤 다시 갱신"
)


class _Vector(sa.types.UserDefinedType):
    """`extensions.vector(1024)` — 그날의 사본(`vectors.Vector`)."""

    cache_ok = True

    def get_col_spec(self, **_kw: object) -> str:
        return f"{SCHEMA}.vector({DIM})"


def _ensure_vector() -> None:
    bind = op.get_bind()
    found = bind.execute(
        sa.text(
            "SELECT extversion, extnamespace::regnamespace::text FROM pg_extension "
            "WHERE extname = 'vector'"
        )
    ).first()
    superuser = bind.execute(
        sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    ).scalar()
    if found is None and superuser:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO PUBLIC")
        op.execute(f"CREATE EXTENSION vector SCHEMA {SCHEMA}")
        return
    if found is None:
        raise RuntimeError(MISSING)
    version, schema = found
    if schema != SCHEMA:
        raise RuntimeError(
            f"pgvector 확장이 {schema} 스키마에 있습니다 — {SCHEMA} 로 옮겨야 합니다 "
            "(sudo ./deploy.sh pgvector 가 옮긴다)"
        )
    parts = tuple(int(one) for one in version.split(".") if one.isdigit())
    if parts < (0, 5, 0):
        raise RuntimeError(
            f"pgvector {version} 은 HNSW 색인이 없습니다 — 0.5 이상이 필요합니다 "
            "(패키지를 올린 뒤 sudo ./deploy.sh pgvector)"
        )


def upgrade() -> None:
    _ensure_vector()
    op.create_table(
        "semantic_targets",
        sa.Column("type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["type_id"], ["object_types.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("type_id"),
    )
    op.create_table(
        "object_embeddings",
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("embedding", _Vector(), nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["type_id"], ["object_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
    )
    op.create_index(
        op.f("ix_object_embeddings_type_id"), "object_embeddings", ["type_id"], unique=False
    )
    op.create_index(
        "ix_object_embeddings_hnsw",
        "object_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": f"{SCHEMA}.vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_object_embeddings_hnsw", table_name="object_embeddings")
    op.drop_index(op.f("ix_object_embeddings_type_id"), table_name="object_embeddings")
    op.drop_table("object_embeddings")
    op.drop_table("semantic_targets")
