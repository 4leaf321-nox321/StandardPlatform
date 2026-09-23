"""바깥에 여는 코어 타입 — object_types.core 와 증분 인덱스.

`core` 가 켜진 타입만 `/api/core` 로 나간다. 사이드바 묶음을 공유 경계로 쓰면 누가 메뉴를
정리하는 순간 공개 범위가 조용히 바뀐다 — 그래서 타입 제 칸으로 둔다.

인덱스는 「지난번 이후 바뀐 것」 을 위한 것이다. 없으면 증분 질의가 타입 전체를 훑고,
행이 몇 만을 넘는 순간 새벽 동기화가 느려진다 — 느려진 이유는 어디에도 안 적힌다.

Revision ID: 0025_core_types
Revises: 0024_jobs
Create Date: 2026-09-22 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0025_core_types"
down_revision: Union[str, Sequence[str], None] = "0024_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "object_types",
        sa.Column("core", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index("ix_objects_type_updated", "objects", ["type_id", "updated_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_objects_type_updated", table_name="objects")
    op.drop_column("object_types", "core")
