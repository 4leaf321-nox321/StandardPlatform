"""관계의 유효 기간(ADR 0039) — 관계 종류의 표시, 선의 기간 두 칸과 종류의 사본, 제약 둘.

유효 기간을 켠 종류의 선은 `valid_from`(포함) · `valid_to`(제외)를 받고, 같은 두 객체 사이에
기간을 달리한 선이 여럿일 수 있다. 그래서 예전의 유일 제약(출발 · 도착 · 종류)을 **꺼진 종류의
선에만 거는 부분 유일 색인**으로 바꾸고, 켜진 종류의 선에는 **기간이 겹치면 거절하는 배제
제약**(gist, `btree_gist`)을 건다. 둘을 가르는 것이 선마다 든 종류의 사본(`effectivity` — 참 ·
NULL)이고, 그 사본은 트리거가 맞춘다(`objects/effectivity.py`).

**운영(선 수천만 줄)에서 드는 것** — 3백만 줄 합성 표로 잰 값(2026-10-30, 개발 PC):

- 칸 넷(선 셋 · 무덤 둘 · 종류 하나)은 기본값 없는 NULL 칸이라 즉시다(종류의 표시만 기본값 —
  작은 표).
- 기간 검사(`CHECK`)는 `NOT VALID` 로 걸고 따로 검증한다 — 표를 한 번 읽는다(3백만 줄 0.17초).
- **부분 유일 색인을 새로 세운다**(지금 있는 선 전부가 든다) — 3백만 줄 1.5초, 수천만 줄이면
  수십 초. 옛 유일 제약은 새 색인이 선 **뒤에** 지운다(그 사이에도 같은 선이 막힌다).
- 배제 제약은 켜진 종류의 선만 든다 — 마이그레이션 때는 0줄이라 표를 한 번 읽고 끝(0.35초).
  **전체에 거는 배제 제약은 택하지 않았다** — 백만 줄 20초 · 145MB(btree 의 40배 · 2.6배)로,
  수천만 줄이면 10분 넘게 표를 잠근다.

Revision ID: 0086_relation_periods
Revises: 0085_type_counts
Create Date: 2026-10-30 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0086_relation_periods"
down_revision: Union[str, Sequence[str], None] = "0085_type_counts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 그날의 사본 — 앱 코드를 import 하지 않는다(`objects/effectivity.py` 와 같은 SQL).
FUNCTIONS = """
CREATE OR REPLACE FUNCTION object_relations_effectivity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 종류를 못 찾으면(지운 종류의 남은 선) NULL — 꺼진 것으로 친다.
    SELECT CASE WHEN rt.effectivity THEN TRUE END INTO NEW.effectivity
      FROM relation_types rt WHERE rt.slug = NEW.relation;
    IF NEW.effectivity IS NULL
       AND (NEW.valid_from IS NOT NULL OR NEW.valid_to IS NOT NULL) THEN
        -- 메시지에 퍼센트 자리표를 쓰지 않는다 — 드라이버가 SQL 글의 그 글자를 자리표로
        -- 읽는다(시험이 모델로 세울 때 `exec_driver_sql`).
        RAISE EXCEPTION USING
            MESSAGE = '관계 종류 ' || NEW.relation || '은(는) 유효 기간을 받지 않습니다',
            ERRCODE = 'check_violation',
            CONSTRAINT = 'ck_object_relations_period';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION relation_types_effectivity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 값은 위 트리거가 종류에서 다시 읽는다 — 여기서는 그 트리거를 부르기만 한다.
    UPDATE object_relations SET effectivity = CASE WHEN NEW.effectivity THEN TRUE END
     WHERE relation = NEW.slug;
    RETURN NULL;
END $$;
"""

TRIGGERS = """
DROP TRIGGER IF EXISTS object_relations_effectivity ON object_relations;
CREATE TRIGGER object_relations_effectivity
    BEFORE INSERT OR UPDATE OF relation, valid_from, valid_to, effectivity ON object_relations
    FOR EACH ROW EXECUTE FUNCTION object_relations_effectivity();

DROP TRIGGER IF EXISTS relation_types_effectivity ON relation_types;
CREATE TRIGGER relation_types_effectivity AFTER UPDATE OF effectivity ON relation_types
    FOR EACH ROW WHEN (OLD.effectivity IS DISTINCT FROM NEW.effectivity)
    EXECUTE FUNCTION relation_types_effectivity();
"""


def upgrade() -> None:
    # uuid · 글자의 `=` 를 gist 에서 — 배제 제약이 쓴다. pg_trgm 과 같은 contrib 이고 신뢰된
    # 확장이라 DB 주인이 건다(0051 이 pg_trgm 을 건 길).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.add_column(
        "relation_types",
        sa.Column("effectivity", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("object_relations", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("object_relations", sa.Column("valid_to", sa.Date(), nullable=True))
    op.add_column("object_relations", sa.Column("effectivity", sa.Boolean(), nullable=True))
    op.add_column(
        "object_relation_tombstones", sa.Column("valid_from", sa.Date(), nullable=True)
    )
    op.add_column(
        "object_relation_tombstones", sa.Column("valid_to", sa.Date(), nullable=True)
    )

    op.execute(
        "ALTER TABLE object_relations ADD CONSTRAINT ck_object_relations_period "
        "CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to) NOT VALID"
    )
    op.execute("ALTER TABLE object_relations VALIDATE CONSTRAINT ck_object_relations_period")

    # 새 색인을 먼저 세우고 옛 제약을 지운다 — 그 사이에도 같은 선이 막힌다.
    op.create_index(
        "uq_object_relations_edge_partial",
        "object_relations",
        ["src_object_id", "dst_object_id", "relation"],
        unique=True,
        postgresql_where=sa.text("effectivity IS NULL"),
    )
    op.drop_constraint("uq_object_relations_edge", "object_relations", type_="unique")
    op.execute(
        "ALTER INDEX uq_object_relations_edge_partial RENAME TO uq_object_relations_edge"
    )
    op.execute(
        "ALTER TABLE object_relations ADD CONSTRAINT ex_object_relations_period "
        "EXCLUDE USING gist (src_object_id WITH =, dst_object_id WITH =, relation WITH =, "
        "daterange(valid_from, valid_to, '[)') WITH &&) WHERE (effectivity)"
    )
    op.execute(FUNCTIONS)
    op.execute(TRIGGERS)


def downgrade() -> None:
    """**기간을 달리한 같은 선이 있으면 실패한다**(옛 유일 제약이 다시 못 선다) — 그 선을
    먼저 정리한다."""
    op.execute(
        """
        DROP TRIGGER IF EXISTS object_relations_effectivity ON object_relations;
        DROP TRIGGER IF EXISTS relation_types_effectivity ON relation_types;
        DROP FUNCTION IF EXISTS object_relations_effectivity();
        DROP FUNCTION IF EXISTS relation_types_effectivity();
        """
    )
    op.execute("ALTER TABLE object_relations DROP CONSTRAINT ex_object_relations_period")
    op.drop_index("uq_object_relations_edge", table_name="object_relations")
    op.create_unique_constraint(
        "uq_object_relations_edge",
        "object_relations",
        ["src_object_id", "dst_object_id", "relation"],
    )
    # 이름 규칙이 `ck_` 를 한 번 더 붙이지 않게 글로 지운다.
    op.execute("ALTER TABLE object_relations DROP CONSTRAINT ck_object_relations_period")
    op.drop_column("object_relation_tombstones", "valid_to")
    op.drop_column("object_relation_tombstones", "valid_from")
    op.drop_column("object_relations", "effectivity")
    op.drop_column("object_relations", "valid_to")
    op.drop_column("object_relations", "valid_from")
    op.drop_column("relation_types", "effectivity")
