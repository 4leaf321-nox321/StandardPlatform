"""형제 설치에서 코어를 당겨오는 소스 — data_sources.since_mark.

증분 동기화는 「지난번에 어디까지 받았나」 를 들고 있어야 한다. 그 값은 **상대가 준
`as_of`** 다 — 우리 시계로 만들면 몇 초 차이로 그 사이 행이 새고, 샌 줄은 아무도 모른다.

**끝까지 받고 적용에 성공했을 때만** 옮긴다. 중간에 실패하고 옮기면 그 사이 것을 영영
안 받는다.

Revision ID: 0026_sp_core_source
Revises: 0025_core_types
Create Date: 2026-09-23 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0026_sp_core_source"
down_revision: Union[str, Sequence[str], None] = "0025_core_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "data_sources",
        sa.Column("since_mark", sa.String(length=64), server_default="", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("data_sources", "since_mark")
