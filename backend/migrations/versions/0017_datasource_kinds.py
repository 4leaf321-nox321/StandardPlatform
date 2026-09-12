"""데이터 소스 종류(odata·rest·file)와 종류별 옵션. 파일 소스는 루트 주소가 없다.

Revision ID: 0017_datasource_kinds
Revises: 0016_datasources
Create Date: 2026-09-12 11:09:19.752254

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017_datasource_kinds"
down_revision: Union[str, Sequence[str], None] = "0016_datasources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "data_sources",
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.alter_column(
        "data_sources",
        "entity_set",
        existing_type=sa.VARCHAR(length=200),
        type_=sa.String(length=500),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "data_sources",
        "entity_set",
        existing_type=sa.String(length=500),
        type_=sa.VARCHAR(length=200),
        existing_nullable=False,
    )
    op.drop_column("data_sources", "options")
