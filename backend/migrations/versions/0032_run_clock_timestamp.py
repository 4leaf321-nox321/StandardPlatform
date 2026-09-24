"""데이터 소스 실행 시각 — `now()` → `clock_timestamp()`.

`now()` 는 **트랜잭션이 시작한 시각**이다. 한 요청에서 실행을 두 번 남기면 두 행의
`started_at` 이 같아지고, 그러면 「최근 것부터」 목록의 순서가 질의마다 달라진다 —
화면은 그것을 「순서가 바뀌었다」 로 보여 준다. 시험이 그 흔들림을 잡았다(2026-09-25).

`clock_timestamp()` 는 행을 넣는 그 순간의 시계다.

Revision ID: 0032_run_clock_timestamp
Revises: 0031_caegroup_note_only
Create Date: 2026-09-25 09:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0032_run_clock_timestamp"
down_revision: Union[str, Sequence[str], None] = "0031_caegroup_note_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE data_source_runs ALTER COLUMN started_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE data_source_runs ALTER COLUMN started_at SET DEFAULT now()")
