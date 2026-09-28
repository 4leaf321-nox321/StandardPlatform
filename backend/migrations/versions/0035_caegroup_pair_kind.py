"""디지털 트윈 — 연계의 종류(시험 연계 · 전용 검토).

대응 시험이 없는데 **상시로 하는 판단**이 있다 — 시뮬레이션만으로 시장 불량을 판단하는 자리다.
그것을 시험 연계에 섞으면 **가상검증률이 오염된다**: 전사에서 「시험 결과와의 일치율」 로
취합하는 지표라, 비교할 시험이 없는 줄이 같은 분모에 들면 그 값의 뜻이 달라진다.

표를 새로 만들지 않는다. 연계 · 평가 · 이력 · 근거 규칙은 그대로 쓰고, **축의 적용 범위만**
종류로 가른다(정의 파일). 전용 검토에는 가상검증률 · 시험 대체의 줄이 서지 않는다.

이미 있는 줄은 전부 `test` 다 — 지금까지 등록된 것은 모두 시험 연계다.

Revision ID: 0035_caegroup_pair_kind
Revises: 0034_caegroup_catalogs
Create Date: 2026-09-28 09:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0035_caegroup_pair_kind"
down_revision: Union[str, Sequence[str], None] = "0034_caegroup_catalogs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "cae_dt_pairs",
        sa.Column("kind", sa.String(length=16), server_default="test", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("cae_dt_pairs", "kind")
