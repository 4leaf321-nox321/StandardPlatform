"""확장 `svinput`(자주검증 입력, ADR 0051) — 설정 한 줄 표.

문서 · 문제점 · 판정 · 지표는 허브의 온톨로지 객체라 표가 없다. 여기 있는 것은 **어느 타입 · 어느
칸이 어느 역할인가**(`svinput_settings.roles`)뿐이다. 확장을 안 켠 설치에도 표는 생긴다 — 마이그레이션을
확장마다 가르지 않는다. 비어 있을 뿐이다.

Revision ID: 0094_svinput_settings
Revises: 0093_graph_map
Create Date: 2026-11-04 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0094_svinput_settings"
down_revision: Union[str, Sequence[str], None] = "0093_graph_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "svinput_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "roles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_svinput_settings_single"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("svinput_settings")
