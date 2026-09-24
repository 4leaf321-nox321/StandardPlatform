"""평가의 근거는 **글 한 줄**이다 — 등급·자료 칸을 걷는다.

등급(진술 · 확인 · 검증)과 근거 자료 칸을 두었다가 걷었다. 칸이 늘수록 채우는 사람이
줄고, 안 채운 칸은 「모름」 과 구별되지 않는다 — 무엇을 보고 매겼는지는 근거 글에 적는다.

**되돌릴 때는 빈 칸으로 돌아온다.** 지운 값은 이력(`snapshot`)에 남아 있던 것뿐이고,
그것까지 복원하지는 않는다.

Revision ID: 0031_caegroup_note_only
Revises: 0030_caegroup_assessment
Create Date: 2026-09-24 22:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0031_caegroup_note_only"
down_revision: Union[str, Sequence[str], None] = "0030_caegroup_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("cae_dt_assessments", "evidence_tier")
    op.drop_column("cae_dt_assessments", "evidence_ref")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "cae_dt_assessments",
        sa.Column("evidence_ref", sa.String(length=300), server_default="", nullable=False),
    )
    op.add_column(
        "cae_dt_assessments",
        sa.Column(
            "evidence_tier", sa.String(length=20), server_default="stated", nullable=False
        ),
    )
