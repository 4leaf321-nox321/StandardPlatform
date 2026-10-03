"""데이터 소스가 **자기 출처 이름**을 갖는다 — 잠긴 타입에 받기가 막히던 것.

허브가 내려준 정의 · 객체는 받는 쪽에서 고치는 길을 막는다(`object_types.managed_by`).
그런데 적재가 자기 출처 이름을 말하지 않아서, **허브가 준 것을 받는 길도 함께 막혔다** —
잠근 뜻은 「아무나 고치지 마라」 이고 「허브가 준 것도 들어오지 마라」 가 아니다.

    data_sources.source_name   비우면 slug 를 쓴다

`managed_by` 와 같을 때만 그 타입에 넣는다. slug 와 따로 두는 이유: slug 는 별칭
`source:<slug>` 에 박혀 바꿀 수 없어서, 허브가 적은 이름과 다르면 소스를 지우고 다시
만들어야 하고 — 그러면 그 소스가 남긴 외부 식별자를 전부 잃는다(같은 것이 둘이 된다).

Revision ID: 0058_datasource_source_name
Revises: 0057_metrics
Create Date: 2026-10-03 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0058_datasource_source_name"
down_revision: Union[str, Sequence[str], None] = "0057_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "data_sources",
        sa.Column("source_name", sa.String(length=100), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("data_sources", "source_name")
