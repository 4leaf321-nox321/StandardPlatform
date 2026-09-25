"""디지털 트윈 — 화면에서 고치는 목록(S/W 단위 · 용도).

단위를 하나 더하는 일이 **배포**였다. 「대」 와 「코어」 중 무엇으로 셀지는 이 설치의 사정이고,
그 사정이 바뀔 때마다 코드를 고치면 목록은 결국 안 고쳐진 채로 쓰인다.

칸 하나(`catalogs`)에 이름 → 목록으로 둔다. **어떤 목록이 더 필요해질지 모르므로** 목록마다
칸을 만들지 않는다 — 그러면 다음 목록에서 또 마이그레이션을 한다.

비어 있으면 정의 파일의 기본값을 쓴다. 기본값을 DB 에 미리 심지 않는 이유: 심어 두면 코드의
기본값을 고쳐도 이미 깔린 설치는 옛 값을 계속 들고, 그 차이를 아무도 모른다.

Revision ID: 0034_caegroup_catalogs
Revises: 0033_caegroup_capacity
Create Date: 2026-09-26 09:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0034_caegroup_catalogs"
down_revision: Union[str, Sequence[str], None] = "0033_caegroup_capacity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "cae_dt_settings",
        sa.Column("catalogs", postgresql.JSONB(), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("cae_dt_settings", "catalogs")
