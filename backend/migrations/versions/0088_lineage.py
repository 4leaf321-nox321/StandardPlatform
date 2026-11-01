"""파생 · 계보 관계와 개정 고정(ADR 0041) — 관계 종류의 원천 끝, 타입의 개정 칸, 선의 고정 두 칸.

계보 관계(`relation_types.lineage_source` = `src` · `dst`)의 선은 맺는 순간 원천의 지금 개정 값과
바뀐 때를 박는다(`object_relations.pinned_revision` · `pinned_at`). 박는 일은 선을 넣을 때 도는
트리거가 한다 — 0086 의 `object_relations_effectivity` 가 종류를 이미 한 번 읽으므로 그 트리거를
`object_relations_kind` 로 바꿔 같은 조회에서 둘을 함께 한다(계보 아닌 선은 칸 하나를 더 읽을
뿐이다). 원천 끝 · 개정 칸을 바꿀 때 그 선들의 고정을 다시 맞추는 트리거 둘과, 개정 값 · 낡음의
식(DB 함수 둘 — `sp_revision` · `sp_lineage_stale`)을 함께 건다(`objects/pinning.py`).

**운영에서 드는 것** — 칸 넷은 기본값 없는 NULL 칸이거나 작은 표(관계 종류 · 타입)의 기본값 칸이라
즉시다. 처음에는 계보 관계 종류가 없으므로 박을 선도 없다(종류를 계보로 켤 때 그 종류의 선을
한 번 박는다 — 트리거 `relation_types_lineage`). 새 색인은 없다(판정은 출발 · 도착 색인으로
걷는다).

Revision ID: 0088_lineage
Revises: 0087_relation_quantity
Create Date: 2026-10-30 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0088_lineage"
down_revision: Union[str, Sequence[str], None] = "0087_relation_quantity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 그날의 사본 — 앱 코드를 import 하지 않는다(`objects/pinning.py` · `objects/effectivity.py` 와 같은
# SQL). `sp_lineage_stale` 이 `sp_revision` 을 부르므로 그 차례로.
FUNCTIONS = """
CREATE OR REPLACE FUNCTION sp_revision(properties jsonb, key text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN key <> '' THEN properties ->> key END
$$;

CREATE OR REPLACE FUNCTION sp_lineage_stale(
    key text, properties jsonb, updated_at timestamptz, pinned_revision text,
    pinned_at timestamptz
) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN key <> '' THEN sp_revision(properties, key) IS DISTINCT FROM pinned_revision
        ELSE coalesce(updated_at > pinned_at, false)
    END
$$;

CREATE OR REPLACE FUNCTION relation_types_lineage() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 원천 끝이 바뀌었다 — 계보가 아니게 됐으면 고정을 비우고, 끝이 정해지면 그 끝의 지금
    -- 값으로 박는다(그때까지의 선은 그때 맺은 것으로 친다).
    IF NEW.lineage_source = 'src' THEN
        UPDATE object_relations r
           SET pinned_at = o.updated_at,
               pinned_revision = sp_revision(o.properties, t.revision_key)
          FROM objects o JOIN object_types t ON t.id = o.type_id
         WHERE r.relation = NEW.slug AND o.id = r.src_object_id;
    ELSIF NEW.lineage_source = 'dst' THEN
        UPDATE object_relations r
           SET pinned_at = o.updated_at,
               pinned_revision = sp_revision(o.properties, t.revision_key)
          FROM objects o JOIN object_types t ON t.id = o.type_id
         WHERE r.relation = NEW.slug AND o.id = r.dst_object_id;
    ELSE
        UPDATE object_relations SET pinned_at = NULL, pinned_revision = NULL
         WHERE relation = NEW.slug AND (pinned_at IS NOT NULL OR pinned_revision IS NOT NULL);
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_types_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 이 타입의 객체를 원천으로 둔 계보 선 — 옛 칸으로 본 판정을 새 칸에서도 지킨다. 낡지
    -- 않았던 선은 새 칸의 지금 값으로 박고, 낡았던 선은 새 칸에서도 낡게 둔다(새 칸의 값이
    -- 비었으면 빈 글자를 박아 다름을 남긴다). 새 칸이 비면 바뀐 때로 판정한다.
    UPDATE object_relations r SET
        pinned_revision = CASE
            WHEN NEW.revision_key = '' THEN NULL
            WHEN NOT sp_lineage_stale(OLD.revision_key, o.properties, o.updated_at,
                                      r.pinned_revision, r.pinned_at)
                THEN sp_revision(o.properties, NEW.revision_key)
            WHEN sp_revision(o.properties, NEW.revision_key) IS NULL THEN ''
            ELSE NULL
        END,
        pinned_at = CASE
            WHEN NEW.revision_key <> '' THEN r.pinned_at
            WHEN NOT sp_lineage_stale(OLD.revision_key, o.properties, o.updated_at,
                                      r.pinned_revision, r.pinned_at)
                THEN greatest(r.pinned_at, o.updated_at)
            ELSE least(r.pinned_at, o.updated_at - interval '1 microsecond')
        END
      FROM objects o, relation_types k
     WHERE o.type_id = NEW.id AND k.lineage_source = 'src'
       AND r.relation = k.slug AND r.src_object_id = o.id;
    UPDATE object_relations r SET
        pinned_revision = CASE
            WHEN NEW.revision_key = '' THEN NULL
            WHEN NOT sp_lineage_stale(OLD.revision_key, o.properties, o.updated_at,
                                      r.pinned_revision, r.pinned_at)
                THEN sp_revision(o.properties, NEW.revision_key)
            WHEN sp_revision(o.properties, NEW.revision_key) IS NULL THEN ''
            ELSE NULL
        END,
        pinned_at = CASE
            WHEN NEW.revision_key <> '' THEN r.pinned_at
            WHEN NOT sp_lineage_stale(OLD.revision_key, o.properties, o.updated_at,
                                      r.pinned_revision, r.pinned_at)
                THEN greatest(r.pinned_at, o.updated_at)
            ELSE least(r.pinned_at, o.updated_at - interval '1 microsecond')
        END
      FROM objects o, relation_types k
     WHERE o.type_id = NEW.id AND k.lineage_source = 'dst'
       AND r.relation = k.slug AND r.dst_object_id = o.id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_relations_kind() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    lineage text;
BEGIN
    -- 종류를 한 번 읽는다 — 유효 기간의 사본과 계보의 원천 끝. 못 찾으면(지운 종류의 남은
    -- 선) 둘 다 꺼진 것으로 친다.
    SELECT CASE WHEN rt.effectivity THEN TRUE END, rt.lineage_source
      INTO NEW.effectivity, lineage
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
    -- 계보 선을 맺는 순간 — 원천의 지금 개정 값과 바뀐 때를 박는다(ADR 0041). 고정을 실어
    -- 온 줄(묶음 · 되돌리기)은 그대로 둔다. 원천을 못 찾으면 비운 채로 둔다(FK 가 막는다).
    IF TG_OP = 'INSERT' AND lineage IN ('src', 'dst') AND NEW.pinned_at IS NULL THEN
        SELECT o.updated_at,
               coalesce(NEW.pinned_revision, sp_revision(o.properties, t.revision_key))
          INTO NEW.pinned_at, NEW.pinned_revision
          FROM objects o JOIN object_types t ON t.id = o.type_id
         WHERE o.id = CASE lineage WHEN 'src' THEN NEW.src_object_id
                                   ELSE NEW.dst_object_id END;
    END IF;
    RETURN NEW;
END $$;
"""

TRIGGERS = """
DROP TRIGGER IF EXISTS relation_types_lineage ON relation_types;
CREATE TRIGGER relation_types_lineage AFTER UPDATE OF lineage_source ON relation_types
    FOR EACH ROW WHEN (OLD.lineage_source IS DISTINCT FROM NEW.lineage_source)
    EXECUTE FUNCTION relation_types_lineage();

-- 새 종류 — 대개 선이 없지만, 지운 종류의 slug 를 다시 쓰면 남은 선이 있을 수 있다.
DROP TRIGGER IF EXISTS relation_types_lineage_new ON relation_types;
CREATE TRIGGER relation_types_lineage_new AFTER INSERT ON relation_types
    FOR EACH ROW WHEN (NEW.lineage_source <> '')
    EXECUTE FUNCTION relation_types_lineage();

DROP TRIGGER IF EXISTS object_types_revision ON object_types;
CREATE TRIGGER object_types_revision AFTER UPDATE OF revision_key ON object_types
    FOR EACH ROW WHEN (OLD.revision_key IS DISTINCT FROM NEW.revision_key)
    EXECUTE FUNCTION object_types_revision();

DROP TRIGGER IF EXISTS object_relations_effectivity ON object_relations;
DROP TRIGGER IF EXISTS object_relations_kind ON object_relations;
CREATE TRIGGER object_relations_kind
    BEFORE INSERT OR UPDATE OF relation, valid_from, valid_to, effectivity ON object_relations
    FOR EACH ROW EXECUTE FUNCTION object_relations_kind();
DROP FUNCTION IF EXISTS object_relations_effectivity();
"""

# 내릴 때 되살리는 0086 의 함수(그날의 사본).
OLD_FUNCTION = """
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
"""


def upgrade() -> None:
    op.add_column(
        "relation_types",
        sa.Column("lineage_source", sa.String(length=8), server_default="", nullable=False),
    )
    op.add_column(
        "object_types",
        sa.Column("revision_key", sa.String(length=48), server_default="", nullable=False),
    )
    op.add_column("object_relations", sa.Column("pinned_revision", sa.Text(), nullable=True))
    op.add_column(
        "object_relations",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(FUNCTIONS)
    op.execute(TRIGGERS)


def downgrade() -> None:
    op.execute(OLD_FUNCTION)
    op.execute(
        """
        DROP TRIGGER IF EXISTS object_relations_kind ON object_relations;
        CREATE TRIGGER object_relations_effectivity
            BEFORE INSERT OR UPDATE OF relation, valid_from, valid_to, effectivity
            ON object_relations
            FOR EACH ROW EXECUTE FUNCTION object_relations_effectivity();
        DROP FUNCTION IF EXISTS object_relations_kind();
        DROP TRIGGER IF EXISTS relation_types_lineage ON relation_types;
        DROP TRIGGER IF EXISTS relation_types_lineage_new ON relation_types;
        DROP TRIGGER IF EXISTS object_types_revision ON object_types;
        DROP FUNCTION IF EXISTS relation_types_lineage();
        DROP FUNCTION IF EXISTS object_types_revision();
        DROP FUNCTION IF EXISTS sp_lineage_stale(text, jsonb, timestamptz, text, timestamptz);
        DROP FUNCTION IF EXISTS sp_revision(jsonb, text);
        """
    )
    op.drop_column("object_relations", "pinned_at")
    op.drop_column("object_relations", "pinned_revision")
    op.drop_column("object_types", "revision_key")
    op.drop_column("relation_types", "lineage_source")
