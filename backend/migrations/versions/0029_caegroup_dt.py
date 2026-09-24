"""디지털 트윈 역량 — 설정 한 줄과 연계.

기준 정보(시험 항목 · 시뮬레이션)는 온톨로지 객체라 표가 없다. 여기 있는 것은 **어느
타입을 쓸지**(`cae_dt_settings`)와 **그 객체들의 짝**(`cae_dt_pairs`)이다.

확장 `caegroup` 을 안 켠 설치에도 표는 생긴다 — 마이그레이션을 확장마다 가르지 않는다.
비어 있을 뿐이고, 그것이 「켰다 껐다」 를 배포 없이 하게 해 주는 대가다.

Revision ID: 0029_caegroup_dt
Revises: 0028_extension_states
Create Date: 2026-09-24 20:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0029_caegroup_dt"
down_revision: Union[str, Sequence[str], None] = "0028_extension_states"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cae_dt_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_type_slug", sa.String(length=64), nullable=True),
        sa.Column("agent_type_slug", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_cae_dt_settings_single"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cae_dt_pairs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "agent_id", name="uq_cae_dt_pair"),
    )
    op.create_index("ix_cae_dt_pairs_workspace_id", "cae_dt_pairs", ["workspace_id"])
    op.create_index("ix_cae_dt_pairs_subject_id", "cae_dt_pairs", ["subject_id"])
    op.create_index("ix_cae_dt_pairs_agent_id", "cae_dt_pairs", ["agent_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_cae_dt_pairs_agent_id", table_name="cae_dt_pairs")
    op.drop_index("ix_cae_dt_pairs_subject_id", table_name="cae_dt_pairs")
    op.drop_index("ix_cae_dt_pairs_workspace_id", table_name="cae_dt_pairs")
    op.drop_table("cae_dt_pairs")
    op.drop_table("cae_dt_settings")
