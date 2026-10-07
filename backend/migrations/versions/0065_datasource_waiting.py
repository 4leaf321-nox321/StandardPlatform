"""데이터 소스 — 끝점을 아직 못 찾아 기다리는 선(`sp_core` + `options.relations`).

타입마다 소스가 따로라, 선이 가리키는 쪽이 아직 안 들어왔을 수 있다. 그런 줄 하나가 그 소스의
선 전부를 막던 것을 고친다 — 나머지를 넣고, 못 찾은 줄만 여기 남겨 다음 동기화가 다시 넣는다.

Revision ID: 0065_datasource_waiting
Revises: 0064_platform_profile
Create Date: 2026-10-07 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0065_datasource_waiting"
down_revision: Union[str, Sequence[str], None] = "0064_platform_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "data_sources",
        sa.Column(
            "relations_waiting",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("data_sources", "relations_waiting")
