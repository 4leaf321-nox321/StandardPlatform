"""평가 이력 시각 — `now()` → `clock_timestamp()`.

`now()` 는 **트랜잭션이 시작한** 시각이다. 한 요청에서 두 축을 고치면 이력 두 줄이 같은
값을 갖고, 그러면 차례가 질의마다 달라진다 — **이력은 차례가 곧 뜻**이라 뒤집히면 「무엇이
무엇으로 바뀌었나」 가 거꾸로 읽힌다(시험이 간헐로 잡았다).

같은 이유로 데이터 소스 실행(0032) · 정의 스냅샷(0036) · 끊긴 선의 무덤(0037) · 별칭(0040)이
이미 `clock_timestamp()` 를 쓴다.

Revision ID: 0042_dt_history_clock
Revises: 0041_previous_keys
Create Date: 2026-09-30 02:40:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042_dt_history_clock"
down_revision: Union[str, Sequence[str], None] = "0041_previous_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE cae_dt_assessment_history "
        "ALTER COLUMN changed_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE cae_dt_assessment_history ALTER COLUMN changed_at SET DEFAULT now()"
    )
