"""디지털 트윈 역량 — 평가와 그 이력.

축 종류마다 채우는 칸이 다르다(수치형 `value`+`rung` · 수준 선택 `rung` · 선택형 `rungs` ·
매트릭스 `rungs`+`defects`). 원본은 한 칸에 쉼표로 쌓았는데, 문자열을 갈라 읽는 코드가 화면 · API ·
집계에 세 번 생기고 그중 하나만 고쳐지는 날 같은 평가가 다르게 읽힌다.

이력을 감사 기록으로 갈음하지 않는다 — 감사는 시스템 관리자만 읽고, 이 이력은 자료를
채운 담당자가 봐야 한다.

Revision ID: 0030_caegroup_assessment
Revises: 0029_caegroup_dt
Create Date: 2026-09-24 21:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0030_caegroup_assessment"
down_revision: Union[str, Sequence[str], None] = "0029_caegroup_dt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cae_dt_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pair_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("axis", sa.String(length=40), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("rung", sa.String(length=40), nullable=True),
        sa.Column("rungs", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("defects", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("evidence_tier", sa.String(length=20), nullable=False),
        sa.Column("evidence_ref", sa.String(length=300), nullable=False),
        sa.Column(
            "assessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("assessed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assessed_by_label", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["pair_id"], ["cae_dt_pairs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assessed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pair_id", "axis", name="uq_cae_dt_assessment"),
    )
    op.create_index("ix_cae_dt_assessments_pair_id", "cae_dt_assessments", ["pair_id"])
    op.create_table(
        "cae_dt_assessment_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pair_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("axis", sa.String(length=40), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_by_label", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["pair_id"], ["cae_dt_pairs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cae_dt_assessment_history_pair_id", "cae_dt_assessment_history", ["pair_id"]
    )
    op.create_index("ix_cae_dt_assessment_history_axis", "cae_dt_assessment_history", ["axis"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_cae_dt_assessment_history_axis", table_name="cae_dt_assessment_history")
    op.drop_index(
        "ix_cae_dt_assessment_history_pair_id", table_name="cae_dt_assessment_history"
    )
    op.drop_table("cae_dt_assessment_history")
    op.drop_index("ix_cae_dt_assessments_pair_id", table_name="cae_dt_assessments")
    op.drop_table("cae_dt_assessments")
