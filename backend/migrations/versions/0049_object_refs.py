"""참조 색인 — 참조 칸의 값을 좁은 표(`object_refs`)에 두고 **DB 트리거가 유지한다**(ADR 0010).

기록 타입이 200만 건이 되는 설치를 실측했다(`scripts/scale_rehearsal.py`). 참조 칸이 JSONB
안에 있어서 「이 개발모델을 가리키는 기록」 을 찾을 때마다 기록 타입을 통째로 읽었고, 인기
모델의 상세 · 그래프는 오류로 멈췄고, 「참조 너머 칸」 으로 거르거나 묶는 질의는 2분을 넘겼다.
값을 `(누가 · 어느 칸으로 · 누구를)` 한 줄씩 따로 두면 그 물음이 인덱스 하나로 끝난다.

**트리거 SQL 은 그날의 사본이다** — 정본은 `app/modules/objects/refindex.py`(시험이
`create_all` 뒤에 같은 것을 건다). 마이그레이션은 앱 코드를 import 하지 않는다. 고칠 때는 둘 다.

이미 있는 값은 여기서 한 번 채운다 — 200만 건이면 몇십 초가 걸린다.

Revision ID: 0049_object_refs
Revises: 0048_group_color
Create Date: 2026-10-03 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0049_object_refs"
down_revision: Union[str, Sequence[str], None] = "0048_group_color"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION object_refs_inserted() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO object_refs (src_id, src_type_id, key, dst_id)
    
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM new_rows n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' 
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    
    ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_refs_updated() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 바뀐 행만 — updated_at 만 고친 갱신까지 다시 쓰면 큰 일괄 갱신이 두 배로 무겁다.
    DELETE FROM object_refs r
     USING new_rows n JOIN old_rows o ON o.id = n.id
     WHERE r.src_id = n.id AND (o.properties IS DISTINCT FROM n.properties OR o.type_id IS DISTINCT FROM n.type_id OR o.deleted_at IS DISTINCT FROM n.deleted_at);
    INSERT INTO object_refs (src_id, src_type_id, key, dst_id)
    
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT n.* FROM new_rows n JOIN old_rows o ON o.id = n.id WHERE o.properties IS DISTINCT FROM n.properties OR o.type_id IS DISTINCT FROM n.type_id OR o.deleted_at IS DISTINCT FROM n.deleted_at) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' 
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    
    ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_refs_redefined() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.owner_kind = 'type'
       AND OLD.data_type = 'object_ref' THEN
        IF TG_OP = 'DELETE'
           OR NEW.data_type IS DISTINCT FROM 'object_ref'
           OR NEW.key IS DISTINCT FROM OLD.key
           OR NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
            DELETE FROM object_refs WHERE src_type_id = OLD.owner_id AND key = OLD.key;
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.owner_kind = 'type'
       AND NEW.data_type = 'object_ref' THEN
        IF TG_OP = 'INSERT'
           OR OLD.data_type IS DISTINCT FROM 'object_ref'
           OR OLD.key IS DISTINCT FROM NEW.key
           OR OLD.owner_id IS DISTINCT FROM NEW.owner_id THEN
            INSERT INTO object_refs (src_id, src_type_id, key, dst_id)
            
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT * FROM objects WHERE type_id = NEW.owner_id) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND d.id = NEW.id
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    
            ON CONFLICT DO NOTHING;
        END IF;
    END IF;
    RETURN NULL;
END $$;
"""

TRIGGERS = r"""
DROP TRIGGER IF EXISTS object_refs_on_insert ON objects;
CREATE TRIGGER object_refs_on_insert AFTER INSERT ON objects
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT EXECUTE FUNCTION object_refs_inserted();
DROP TRIGGER IF EXISTS object_refs_on_update ON objects;
CREATE TRIGGER object_refs_on_update AFTER UPDATE ON objects
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT EXECUTE FUNCTION object_refs_updated();
DROP TRIGGER IF EXISTS object_refs_on_redefine ON property_defs;
CREATE TRIGGER object_refs_on_redefine AFTER INSERT OR UPDATE OR DELETE ON property_defs
    FOR EACH ROW EXECUTE FUNCTION object_refs_redefined();
"""

BACKFILL = r"""
INSERT INTO object_refs (src_id, src_type_id, key, dst_id) 
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM objects n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' 
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
     ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_refs",
        sa.Column("src_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=48), nullable=False),
        sa.Column("dst_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("src_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["src_id"], ["objects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("src_id", "key", "dst_id"),
    )
    op.create_index("ix_object_refs_dst", "object_refs", ["dst_id", "key"], unique=False)
    op.create_index(
        "ix_object_refs_src_type", "object_refs", ["src_type_id", "key"], unique=False
    )
    op.execute(FUNCTIONS)
    op.execute(TRIGGERS)
    op.execute(BACKFILL)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS object_refs_on_redefine ON property_defs")
    op.execute("DROP TRIGGER IF EXISTS object_refs_on_update ON objects")
    op.execute("DROP TRIGGER IF EXISTS object_refs_on_insert ON objects")
    op.execute("DROP FUNCTION IF EXISTS object_refs_redefined()")
    op.execute("DROP FUNCTION IF EXISTS object_refs_updated()")
    op.execute("DROP FUNCTION IF EXISTS object_refs_inserted()")
    op.drop_index("ix_object_refs_src_type", table_name="object_refs")
    op.drop_index("ix_object_refs_dst", table_name="object_refs")
    op.drop_table("object_refs")
