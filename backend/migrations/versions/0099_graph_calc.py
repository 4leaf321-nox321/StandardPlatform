"""밤 그래프 계산(ADR 0067) — 판 · 지표 · 무리 · 비슷한 것.

기대는 수 · 다리 역할 · 군집을 객체 속성에 쓰지 않고 따로 둔다(밤마다 수십만 객체가 「바뀜」 이
되면 감사 · 색인 · 구독이 함께 움직인다). 지도처럼 계산해 둔 사본이라 객체에 FK 가 없다.

Revision ID: 0099_graph_calc
Revises: 0098_datasource_run_error_kind
Create Date: 2026-11-11 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0099_graph_calc"
down_revision: Union[str, Sequence[str], None] = "0098_datasource_run_error_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_calc_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("engine", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "graph_calc_scores",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=24), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["graph_calc_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "metric", "relation", "object_id"),
    )
    op.create_index(
        "ix_graph_calc_scores_rank",
        "graph_calc_scores",
        ["run_id", "metric", "relation", "rank"],
    )
    op.create_index(
        "ix_graph_calc_scores_value",
        "graph_calc_scores",
        ["run_id", "metric", "relation", "value"],
    )
    op.create_index("ix_graph_calc_scores_object", "graph_calc_scores", ["object_id"])
    op.create_table(
        "graph_calc_clusters",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("cluster", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("representative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["graph_calc_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "cluster"),
    )
    op.create_table(
        "graph_calc_similar",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("other_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["graph_calc_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "relation", "object_id", "rank"),
    )
    op.create_index("ix_graph_calc_similar_object", "graph_calc_similar", ["object_id"])


def downgrade() -> None:
    op.drop_index("ix_graph_calc_similar_object", table_name="graph_calc_similar")
    op.drop_table("graph_calc_similar")
    op.drop_table("graph_calc_clusters")
    op.drop_index("ix_graph_calc_scores_object", table_name="graph_calc_scores")
    op.drop_index("ix_graph_calc_scores_value", table_name="graph_calc_scores")
    op.drop_index("ix_graph_calc_scores_rank", table_name="graph_calc_scores")
    op.drop_table("graph_calc_scores")
    op.drop_table("graph_calc_runs")
