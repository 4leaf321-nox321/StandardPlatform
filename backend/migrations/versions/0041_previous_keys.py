"""옛 식별자를 **목록으로** — 동기화 사이에 두 번 바뀌어도 따라온다.

0039 는 옛 식별자를 한 칸(`renamed_from`)에 뒀다. 그런데 받는 쪽이 하루에 한 번 받아 가고
그 사이에 키가 두 번 바뀌면(A→B→C), 마지막 값만 보내서는 **B 밖에 말하지 못한다** — 받는
쪽이 가진 것은 A 이므로 따라오지 못하고, 같은 객체가 둘이 된다.

`previous_keys` 로 옮기고 `renamed_from` 의 값을 그 첫 줄로 넣는다. 밖으로 나가는 `renamed_from`
(코어 API · 허브 묶음)은 **가장 마지막 것**으로 남긴다 — 이미 그것을 읽는 쪽이 있다.

Revision ID: 0041_previous_keys
Revises: 0040_alias_clock
Create Date: 2026-09-30 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0041_previous_keys"
down_revision: Union[str, Sequence[str], None] = "0040_alias_clock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "objects",
        sa.Column(
            "previous_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE objects SET previous_keys = jsonb_build_array(renamed_from) "
        "WHERE renamed_from IS NOT NULL"
    )
    op.drop_column("objects", "renamed_from")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("objects", sa.Column("renamed_from", sa.String(length=120), nullable=True))
    op.execute(
        "UPDATE objects SET renamed_from = previous_keys ->> -1 "
        "WHERE jsonb_array_length(previous_keys) > 0"
    )
    op.drop_column("objects", "previous_keys")
