"""정의 스냅샷 시각 — `now()` → `clock_timestamp()`.

`now()` 는 **트랜잭션이 시작한 시각**이다. 복원은 한 요청에서 스냅샷을 **둘** 남긴다 —
「복원 직전 …」 하나와, 그 뒤 가져오기가 남기는 하나다. 둘의 `taken_at` 이 완전히 같아지고,
목록은 그 칸 하나로만 정렬하므로 **순서가 질의마다 달라진다.** 화면은 그것을 「순서가
바뀌었다」 로 보여 주고, 시험은 간헐 실패로 드러냈다(2026-09-28).

`clock_timestamp()` 는 행을 넣는 그 순간의 시계다 — 같은 트랜잭션의 두 행도 갈린다.
데이터 소스 실행에 같은 이유로 넣은 적이 있다(0032).

Revision ID: 0036_snapshot_clock_timestamp
Revises: 0035_caegroup_pair_kind
Create Date: 2026-09-28 12:10:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0036_snapshot_clock_timestamp"
down_revision: Union[str, Sequence[str], None] = "0035_caegroup_pair_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE ontology_snapshots ALTER COLUMN taken_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE ontology_snapshots ALTER COLUMN taken_at SET DEFAULT now()")
