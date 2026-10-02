"""타입의 쓰임새 — 축(`axis`)인가 기록(`log`)인가(ADR 0011).

저장 · 권한 · 값은 같고 기본 동작만 다르다(통합 검색 · 축의 상세 · 그래프 · 참조 후보). 이미 있는
타입은 모두 축으로 둔다 — 기록인지는 사람이 정한다(크기로 가르지 않는다).

Revision ID: 0053_type_usage
Revises: 0052_drop_properties_gin
Create Date: 2026-10-03 17:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0053_type_usage"
down_revision: Union[str, Sequence[str], None] = "0052_drop_properties_gin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "object_types",
        sa.Column("usage", sa.String(length=16), server_default="axis", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("object_types", "usage")
