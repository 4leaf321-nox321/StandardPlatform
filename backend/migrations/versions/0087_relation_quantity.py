"""관계 종류의 수량 칸(ADR 0040) — 전개(BOM)가 선마다 「몇 개」 로 읽는 관계 속성 하나의 키.

빈 글자는 「수량 칸 없음」 — 그 종류의 선은 모두 1개로 센다(예전과 같은 뜻). 칸 하나를 기본값과
함께 더할 뿐이라 즉시다(관계 종류는 작은 표 — 수십 줄).

Revision ID: 0087_relation_quantity
Revises: 0086_relation_periods
Create Date: 2026-10-30 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0087_relation_quantity"
down_revision: Union[str, Sequence[str], None] = "0086_relation_periods"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "relation_types",
        sa.Column("quantity_key", sa.String(length=48), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("relation_types", "quantity_key")
