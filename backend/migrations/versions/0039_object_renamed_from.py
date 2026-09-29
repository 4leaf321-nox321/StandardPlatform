"""객체에 **옛 식별자** — 키를 바꾼 사실이 밖으로 가야 같은 것이 둘이 안 된다.

키 체계가 바뀌는 일은 실제로 일어난다(`FM-001` → `FM-BRK-001`). 안에서는 `renamed_from`
열로 찾아 바꿀 수 있게 했지만(0.4.17), **그 사실이 쌍둥이와 코어 API 로 가지 않았다** —
받는 쪽에는 새 식별자가 처음 보는 것이라 같은 객체가 둘이 된다.

합쳐져 사라진 것에 `merged_into` 를 주는 것과 같은 이유로, 바뀐 것에는 옛 식별자를 준다.

Revision ID: 0039_object_renamed_from
Revises: 0038_alias_review
Create Date: 2026-09-30 01:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039_object_renamed_from"
down_revision: Union[str, Sequence[str], None] = "0038_alias_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("objects", sa.Column("renamed_from", sa.String(length=120), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("objects", "renamed_from")
