"""날짜 읽기를 DB 함수로 — `sp_date(text)`.

통계의 기간 묶기 · 날짜 조건 · 지표의 기간이 글자 칸을 날짜로 읽는다(못 읽는 값은 NULL). 그 검사를
SQL 식으로 적으면 같은 문자열 가공을 행마다 십수 번 되풀이해, 기록 200만 건의 날짜 조건 하나를
세는 데 3.6초였다. 함수 안에서는 한 번이다(1.5초, 병렬 없이 잰 값). 뜻은 그대로다 — 앞 열 글자에서
대시를 떼고 여덟 자리 숫자인지, 달의 날수 안인지 본 뒤에만 바꾼다.

모델의 `DATE_SQL`(시험이 스키마를 세울 때)과 같은 글이다 — 여기 사본을 두는 것은 마이그레이션이
나중의 코드를 import 하지 않게 하려는 것이다.

Revision ID: 0061_date_function
Revises: 0060_metric_alerts
Create Date: 2026-10-04 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0061_date_function"
down_revision: Union[str, Sequence[str], None] = "0060_metric_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATE_SQL = """
CREATE OR REPLACE FUNCTION sp_date(raw text) RETURNS date
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE
  d text := substr(translate(substr(raw, 1, 10), '-', ''), 1, 8);
  y int;
  m int;
  dd int;
BEGIN
  IF length(d) <> 8 OR translate(d, '0123456789', '') <> '' THEN
    RETURN NULL;
  END IF;
  y := substr(d, 1, 4)::int;
  m := substr(d, 5, 2)::int;
  dd := substr(d, 7, 2)::int;
  IF y < 1 OR m < 1 OR m > 12 OR dd < 1 OR dd > (
    CASE
      WHEN m = 2 THEN
        CASE WHEN (mod(y, 4) = 0 AND mod(y, 100) <> 0) OR mod(y, 400) = 0 THEN 29 ELSE 28 END
      WHEN m IN (4, 6, 9, 11) THEN 30
      ELSE 31
    END
  ) THEN
    RETURN NULL;
  END IF;
  RETURN make_date(y, m, dd);
END
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    # 윤년 셈은 `%` 가 아니라 `mod()` — 드라이버가 `%` 를 자리표시로 읽는다.
    op.execute(DATE_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS sp_date(text)")
