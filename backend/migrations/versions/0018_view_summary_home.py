"""저장된 뷰가 묶어 보기 설정을 담고, 부서 홈에 올라간다.

Revision ID: 0018_view_summary_home
Revises: 0017_datasource_kinds
Create Date: 2026-09-12 18:46:52.890920

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0018_view_summary_home"
down_revision: Union[str, Sequence[str], None] = "0017_datasource_kinds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "saved_views",
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    # NULL 이면 홈에 없다 — 「안 올림」 과 「0번 자리」 가 구별돼야 한다.
    op.add_column("saved_views", sa.Column("home_order", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_saved_views_home_order"), "saved_views", ["home_order"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_saved_views_home_order"), table_name="saved_views")
    op.drop_column("saved_views", "home_order")
    op.drop_column("saved_views", "summary")
