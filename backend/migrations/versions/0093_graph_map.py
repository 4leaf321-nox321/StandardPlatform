"""지식 그래프의 전체 지도(ADR 0046) — 판 · 기준정보 자리 · 얼린 수 · 시간마다 센 수 · 공동 참조 선 ·
허브 칸 설정 · 확대 점 표.

- 표 여덟(`graph_map_*`) — 비어서 시작한다. 처음 판은 워커가 만든다(기준정보가 있으면 5분 안). 확대 점
  표(`graph_map_points`)는 그 처음 판이 채운다(2,000만 건 DB 약 2분 · 3GB).
- DB 함수 `sp_map_hash(uuid)` — 기록의 자리를 정하는 해시(md5 앞 64비트). **영구 고정**이다.

참조 색인(`object_refs`)에 식 색인을 거는 길은 쓰지 않는다 — 쓰기마다 그 색인에도 써서 기록 넣기가
두 배가 됐다(ADR 0046 「확대 색인의 형태」).

내리면 표와 함수를 지운다(지도는 계산해 둔 사본이라 잃는 정본이 없다).

Revision ID: 0093_graph_map
Revises: 0092_jajuverify_schedule
Create Date: 2026-11-01 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0093_graph_map"
down_revision: Union[str, Sequence[str], None] = "0092_jajuverify_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 그날의 사본 — 앱 코드를 import 하지 않는다(`graph/maphash.py` 의 `HASH_SQL` 과 같은 글).
HASH_SQL = """
CREATE OR REPLACE FUNCTION sp_map_hash(id uuid) RETURNS bigint
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT ('x' || substr(md5(id::text), 1, 16))::bit(64)::bigint
$$
"""


def upgrade() -> None:
    op.execute(HASH_SQL)
    op.create_table(
        "graph_map_layouts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("base_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("scale", sa.Float(), nullable=False),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hub_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("slots", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_map_layouts")),
    )
    op.create_table(
        "graph_map_masters",
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("placed_in", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("object_id", name=op.f("pk_graph_map_masters")),
    )
    op.create_table(
        "graph_map_frozen",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hub_id", sa.UUID(), nullable=True),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.Column("layout_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_map_frozen")),
    )
    op.create_index("ix_graph_map_frozen_hub", "graph_map_frozen", ["hub_id"], unique=False)
    op.create_table(
        "graph_map_counts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hub_id", sa.UUID(), nullable=True),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_map_counts")),
    )
    op.create_table(
        "graph_map_counted",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("layout_id", sa.Integer(), nullable=False),
        sa.Column("points_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("points_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_map_counted")),
    )
    op.create_table(
        "graph_map_points",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("hub_id", sa.UUID(), nullable=False),
        sa.Column("h", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_map_points")),
    )
    op.create_index(
        "ix_graph_map_points_hub",
        "graph_map_points",
        ["hub_id", "h"],
        unique=False,
        postgresql_include=["id"],
    )
    op.create_table(
        "graph_map_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("a_id", sa.UUID(), nullable=False),
        sa.Column("b_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_map_links")),
    )
    op.create_index("ix_graph_map_links_a", "graph_map_links", ["a_id"], unique=False)
    op.create_index("ix_graph_map_links_b", "graph_map_links", ["b_id"], unique=False)
    op.create_table(
        "graph_map_hub_keys",
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=48), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["object_types.id"],
            name=op.f("fk_graph_map_hub_keys_type_id_object_types"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("type_id", name=op.f("pk_graph_map_hub_keys")),
    )


def downgrade() -> None:
    op.drop_index("ix_graph_map_points_hub", table_name="graph_map_points")
    op.drop_table("graph_map_points")
    op.drop_table("graph_map_hub_keys")
    op.drop_index("ix_graph_map_links_b", table_name="graph_map_links")
    op.drop_index("ix_graph_map_links_a", table_name="graph_map_links")
    op.drop_table("graph_map_links")
    op.drop_table("graph_map_counted")
    op.drop_table("graph_map_counts")
    op.drop_index("ix_graph_map_frozen_hub", table_name="graph_map_frozen")
    op.drop_table("graph_map_frozen")
    op.drop_table("graph_map_masters")
    op.drop_table("graph_map_layouts")
    op.execute("DROP FUNCTION IF EXISTS sp_map_hash(uuid)")
