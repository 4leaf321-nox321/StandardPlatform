"""별칭 만든 시각 — `now()` → `clock_timestamp()`.

`now()` 는 **트랜잭션이 시작한** 시각이다. 한 번에 붙인 별칭은 전부 같은 값이 되고, 그러면
차례가 질의마다 달라진다. 별칭은 **사람이 적은 차례**로 보여야 하고(첫 별칭이 대표 표기다),
허브와 쌍둥이를 견주는 자리에서는 그 차례가 흔들리는 순간 **매번 「별칭이 바뀌었다」** 가
되어 바뀐 것이 없는데도 다시 쓴다(실측).

같은 이유로 데이터 소스 실행(0032) · 정의 스냅샷(0036) · 끊긴 선의 무덤(0037)이 이미
`clock_timestamp()` 를 쓴다.

Revision ID: 0040_alias_clock
Revises: 0039_object_renamed_from
Create Date: 2026-09-30 01:20:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0040_alias_clock"
down_revision: Union[str, Sequence[str], None] = "0039_object_renamed_from"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE object_aliases ALTER COLUMN created_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE object_aliases ALTER COLUMN created_at SET DEFAULT now()")
