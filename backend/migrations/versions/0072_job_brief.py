"""작업 결과의 요약 함수(`sp_job_brief`) — 작업 목록이 줄 목록 없이 싣는다.

결과에는 파일의 모든 줄의 계획이 든다(5만 줄이면 17 ~ 19MB). 목록이 그것을 작업마다 통째로 싣던
때는 목록 한 번이 93MB 였다. 줄 목록을 비우고 그 수만 남기는 일을 DB 안에서 한다 — 모델의
`BRIEF_SQL`(시험이 스키마를 세울 때)과 같은 글이다.

Revision ID: 0072_job_brief
Revises: 0071_relation_datasource
Create Date: 2026-10-09 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0072_job_brief"
down_revision: Union[str, Sequence[str], None] = "0071_relation_datasource"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BRIEF_SQL = """CREATE OR REPLACE FUNCTION sp_job_brief(r jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
  out jsonb;
  k text;
  v jsonb;
  n jsonb;
BEGIN
  IF r IS NULL THEN
    RETURN NULL;
  END IF;
  IF jsonb_typeof(r) = 'object' THEN
    -- 줄 수는 jsonpath 로 센다 — `r -> 'rows'` 로 꺼내면 수십 MB 배열을 통째로 베낀다
    -- (50건에 0.24초 → 0.04초, 2026-10-09 실측). 줄 목록을 뺀 나머지만 훑는다.
    -- strict 라야 배열을 낱개로 풀지 않는다(lax 는 `rows` 의 줄마다 걸러 못 알아본다).
    n := jsonb_path_query_first(r, 'strict $.rows ? (@.type() == "array").size()', '{}', true);
    out := '{}'::jsonb;
    FOR k, v IN SELECT * FROM jsonb_each(CASE WHEN n IS NULL THEN r ELSE r - 'rows' END) LOOP
      out := out || jsonb_build_object(k, sp_job_brief(v));
    END LOOP;
    IF n IS NOT NULL THEN
      out := out || jsonb_build_object('rows', '[]'::jsonb, 'rows_omitted', n);
    END IF;
    RETURN out;
  END IF;
  -- 배열은 안에 객체가 있을 때만 들어간다(묶음의 `objects[].plan.rows`) — 글자 목록
  -- (`errors` 등)을 한 칸씩 부르면 그것만으로 느려진다.
  IF jsonb_typeof(r) = 'array' AND jsonb_typeof(r -> 0) IN ('object', 'array') THEN
    SELECT coalesce(jsonb_agg(sp_job_brief(e) ORDER BY i), '[]'::jsonb)
      INTO out FROM jsonb_array_elements(r) WITH ORDINALITY AS t(e, i);
    RETURN out;
  END IF;
  RETURN r;
END
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(BRIEF_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS sp_job_brief(jsonb)")
