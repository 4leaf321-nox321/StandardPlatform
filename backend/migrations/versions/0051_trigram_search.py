"""「이 글자가 들어간 것」 을 인덱스로 — `pg_trgm`(ADR 0010).

검색 · 목록 글 검색 · 이름 풀이의 포함 단계는 `ILIKE '%q%'` 다. 앞에 `%` 가 붙으면 B-tree 를 못
타서, 기록 200만 건에서 검색이 2.7초였다(실측). trigram GIN 인덱스는 세 글자 이상의 조각으로
걸러 그것을 탄다 — **두 글자 검색은 조각이 없어 여전히 못 탄다.** 한글도 조각이 된다(C.UTF-8).

`pg_trgm` 은 Postgres 13 부터 「신뢰된 확장」 이라 DB 소유자면 만들 수 있다 — 폐쇄망에도 새로
깔 것이 없다(contrib 에 들어 있다).

Revision ID: 0051_trigram_search
Revises: 0050_object_lookup_indexes
Create Date: 2026-10-03 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0051_trigram_search"
down_revision: Union[str, Sequence[str], None] = "0050_object_lookup_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_objects_label_trgm",
        "objects",
        ["label"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"label": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_objects_key_trgm",
        "objects",
        ["key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"key": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_object_aliases_value_trgm",
        "object_aliases",
        ["value"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"value": "gin_trgm_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_object_aliases_value_trgm", table_name="object_aliases")
    op.drop_index("ix_objects_key_trgm", table_name="objects")
    op.drop_index("ix_objects_label_trgm", table_name="objects")
