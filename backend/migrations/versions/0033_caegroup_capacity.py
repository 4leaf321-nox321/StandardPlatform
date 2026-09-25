"""인력 · 인프라 — 역량을 떠받치는 조건.

축은 전부 **결과**를 잰다. 그 결과를 만든 조건(사람 · 도구 · 계산 자원)이 옆에 서야
「전담 0.5 FTE 라 여기까지」 가 자료로 말해지고, 낮은 수준이 변명이 아니라 설명이 된다.

**투입률 칸은 없다.** 한 사람은 1.0 이고 담당 해석이 n 개면 각 1/n — 셈은 코드가 한다.
퍼센트를 사람이 적으면 정의가 흔들리고 합이 사람 수를 넘는다.

`created_at` 이 `clock_timestamp()` 인 이유: 가명(담당 A · B)이 이 순서로 붙으므로 한
요청에서 둘을 넣었을 때 시각이 같으면 가명이 질의마다 바뀐다.

Revision ID: 0033_caegroup_capacity
Revises: 0032_run_clock_timestamp
Create Date: 2026-09-25 10:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0033_caegroup_capacity"
down_revision: Union[str, Sequence[str], None] = "0032_run_clock_timestamp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cae_dt_staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agents", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("skill_kinds", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("outside", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cae_dt_staff_workspace_id", "cae_dt_staff", ["workspace_id"])
    op.create_table(
        "cae_dt_capacity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sw", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("hw", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("material_types", sa.Integer(), nullable=True),
        sa.Column("has_process_std", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_cae_dt_capacity_workspace"),
    )
    op.create_index("ix_cae_dt_capacity_workspace_id", "cae_dt_capacity", ["workspace_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_cae_dt_capacity_workspace_id", table_name="cae_dt_capacity")
    op.drop_table("cae_dt_capacity")
    op.drop_index("ix_cae_dt_staff_workspace_id", table_name="cae_dt_staff")
    op.drop_table("cae_dt_staff")
