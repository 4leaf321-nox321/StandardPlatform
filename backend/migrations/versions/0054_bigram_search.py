"""두 글자 검색이 인덱스를 탄다 — 두 글자 조각(`sp_bigrams`) GIN 인덱스(ADR 0010).

trigram(0051)은 세 글자 조각이라 「소음」 「볼트」 같은 **두 글자** 검색은 조각이 없어 표 전체를
읽었다. 한글은 두 글자 낱말이 흔하다. 글자를 두 글자 조각 배열로 만드는 함수를 두고 그 배열에
GIN 인덱스를 건다 — 두 글자로 찾을 때 `sp_bigrams(label) @> ARRAY['소음']` 으로 거른 뒤 `ILIKE`
가 다시 본다. 확장이 필요 없다(SQL 함수 하나).

함수 본문은 `objects/models.py` 의 `BIGRAMS_SQL` 과 같다(시험은 모델로 세운다). 바꾸면 인덱스를
다시 세워야 하므로 새 이름으로 만든다.

Revision ID: 0054_bigram_search
Revises: 0053_type_usage
Create Date: 2026-10-03 19:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0054_bigram_search"
down_revision: Union[str, Sequence[str], None] = "0053_type_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIGRAMS = """
CREATE OR REPLACE FUNCTION sp_bigrams(t text) RETURNS text[]
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT coalesce(array_agg(DISTINCT substr(lower(t), i, 2)), '{}'::text[])
  FROM generate_series(1, char_length(t) - 1) AS i
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(BIGRAMS)
    op.execute("CREATE INDEX ix_objects_label_bigram ON objects USING gin (sp_bigrams(label))")
    op.execute("CREATE INDEX ix_objects_key_bigram ON objects USING gin (sp_bigrams(key))")
    op.execute(
        "CREATE INDEX ix_object_aliases_value_bigram ON object_aliases "
        "USING gin (sp_bigrams(value))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_object_aliases_value_bigram")
    op.execute("DROP INDEX IF EXISTS ix_objects_key_bigram")
    op.execute("DROP INDEX IF EXISTS ix_objects_label_bigram")
    op.execute("DROP FUNCTION IF EXISTS sp_bigrams(text)")
