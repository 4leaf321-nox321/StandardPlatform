"""참조 색인의 고치기 트리거(`object_refs_updated`) — **참조가 달라진 줄만**, 큰 문장은 그때 계획.

0049 의 트리거는 속성이 바뀐 행이면 그 행의 참조 줄을 전부 지우고 다시 넣었다 — 일괄 입력이 비용
칸 하나를 고친 5만 줄에서 23만 줄을 지우고 넣었다. 그리고 그 SQL 의 계획은 연결마다 첫 호출에서
굳어, 한 줄짜리 갱신을 한 연결이 5천 줄 덩어리를 고치면 옛 행 x 새 행을 중첩 반복으로 돌아 덩어리
하나가 39초였다(1.3초여야 할 것, 같은 일괄 갱신이 11 ~ 81초로 흔들렸다 — 2026-10-10 개발 DB 실측).

이제: 참조 칸의 값 · 타입 · 지움이 달라진 행만 고르고, 그 행의 옛 값 · 새 값 줄을 집합으로 빼서
(`EXCEPT`) 사라진 줄만 지우고 새 줄만 넣는다. 64줄이 넘는 문장은 그때그때 계획한다(`EXECUTE`).
트리거 정의는 그대로다 — 함수 몸만 바꾼다(표를 잠그지 않는다). 남는 색인은 예전과 같다.

**모델의 `refindex.UPDATED_FUNCTION`(시험이 스키마를 세울 때)과 같은 글이다** —
`tests/unit/test_refindex_sql.py` 가 본다.

Revision ID: 0075_refs_trigger
Revises: 0074_review_queue
Create Date: 2026-10-10 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0075_refs_trigger"
down_revision: Union[str, Sequence[str], None] = "0074_review_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPDATED_FUNCTION = r"""
CREATE OR REPLACE FUNCTION object_refs_updated() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 참조가 달라진 행의 달라진 줄만 — 비용 · 이름만 고친 갱신은 아무 줄도 안 건드린다.
    -- 큰 문장은 그때그때 계획한다(굳은 한 줄짜리 계획으로 5천 줄을 돌면 39초, 2026-10-10).
    IF (SELECT count(*) FROM new_rows) > 64 THEN
        EXECUTE $sql$
    WITH ch AS MATERIALIZED (
        SELECT n.id, n.type_id, n.deleted_at, n.properties,
               o.type_id AS old_type_id, o.deleted_at AS old_deleted_at,
               o.properties AS old_properties,
               (o.type_id IS DISTINCT FROM n.type_id
                OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL)) AS whole
          FROM new_rows n
          JOIN old_rows o ON o.id = n.id
          LEFT JOIN (SELECT owner_id, array_agg(key::text) AS keys FROM property_defs WHERE owner_kind = 'type' AND data_type = 'object_ref' GROUP BY owner_id) k ON k.owner_id = n.type_id
         WHERE o.type_id IS DISTINCT FROM n.type_id OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL) OR (o.properties IS DISTINCT FROM n.properties AND EXISTS (SELECT 1 FROM unnest(k.keys) AS r(key) WHERE (o.properties -> r.key) IS DISTINCT FROM (n.properties -> r.key)))
    )
    DELETE FROM object_refs r
     USING ((
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, old_type_id AS type_id, old_deleted_at AS deleted_at, old_properties AS properties, properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    ) EXCEPT (
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, type_id, deleted_at, properties, old_properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    ))
           AS g (src_id, src_type_id, key, dst_id)
     WHERE r.src_id = g.src_id AND r.key = g.key AND r.dst_id = g.dst_id
        $sql$;
        EXECUTE $sql$
    WITH ch AS MATERIALIZED (
        SELECT n.id, n.type_id, n.deleted_at, n.properties,
               o.type_id AS old_type_id, o.deleted_at AS old_deleted_at,
               o.properties AS old_properties,
               (o.type_id IS DISTINCT FROM n.type_id
                OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL)) AS whole
          FROM new_rows n
          JOIN old_rows o ON o.id = n.id
          LEFT JOIN (SELECT owner_id, array_agg(key::text) AS keys FROM property_defs WHERE owner_kind = 'type' AND data_type = 'object_ref' GROUP BY owner_id) k ON k.owner_id = n.type_id
         WHERE o.type_id IS DISTINCT FROM n.type_id OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL) OR (o.properties IS DISTINCT FROM n.properties AND EXISTS (SELECT 1 FROM unnest(k.keys) AS r(key) WHERE (o.properties -> r.key) IS DISTINCT FROM (n.properties -> r.key)))
    )
    INSERT INTO object_refs (src_id, src_type_id, key, dst_id)
    (
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, type_id, deleted_at, properties, old_properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    ) EXCEPT (
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, old_type_id AS type_id, old_deleted_at AS deleted_at, old_properties AS properties, properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    )
    ON CONFLICT DO NOTHING
        $sql$;
    ELSE
    WITH ch AS MATERIALIZED (
        SELECT n.id, n.type_id, n.deleted_at, n.properties,
               o.type_id AS old_type_id, o.deleted_at AS old_deleted_at,
               o.properties AS old_properties,
               (o.type_id IS DISTINCT FROM n.type_id
                OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL)) AS whole
          FROM new_rows n
          JOIN old_rows o ON o.id = n.id
          LEFT JOIN (SELECT owner_id, array_agg(key::text) AS keys FROM property_defs WHERE owner_kind = 'type' AND data_type = 'object_ref' GROUP BY owner_id) k ON k.owner_id = n.type_id
         WHERE o.type_id IS DISTINCT FROM n.type_id OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL) OR (o.properties IS DISTINCT FROM n.properties AND EXISTS (SELECT 1 FROM unnest(k.keys) AS r(key) WHERE (o.properties -> r.key) IS DISTINCT FROM (n.properties -> r.key)))
    )
    DELETE FROM object_refs r
     USING ((
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, old_type_id AS type_id, old_deleted_at AS deleted_at, old_properties AS properties, properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    ) EXCEPT (
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, type_id, deleted_at, properties, old_properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    ))
           AS g (src_id, src_type_id, key, dst_id)
     WHERE r.src_id = g.src_id AND r.key = g.key AND r.dst_id = g.dst_id;
    WITH ch AS MATERIALIZED (
        SELECT n.id, n.type_id, n.deleted_at, n.properties,
               o.type_id AS old_type_id, o.deleted_at AS old_deleted_at,
               o.properties AS old_properties,
               (o.type_id IS DISTINCT FROM n.type_id
                OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL)) AS whole
          FROM new_rows n
          JOIN old_rows o ON o.id = n.id
          LEFT JOIN (SELECT owner_id, array_agg(key::text) AS keys FROM property_defs WHERE owner_kind = 'type' AND data_type = 'object_ref' GROUP BY owner_id) k ON k.owner_id = n.type_id
         WHERE o.type_id IS DISTINCT FROM n.type_id OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL) OR (o.properties IS DISTINCT FROM n.properties AND EXISTS (SELECT 1 FROM unnest(k.keys) AS r(key) WHERE (o.properties -> r.key) IS DISTINCT FROM (n.properties -> r.key)))
    )
    INSERT INTO object_refs (src_id, src_type_id, key, dst_id)
    (
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, type_id, deleted_at, properties, old_properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    ) EXCEPT (
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM (SELECT id, old_type_id AS type_id, old_deleted_at AS deleted_at, old_properties AS properties, properties AS other, whole FROM ch) n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    )
    ON CONFLICT DO NOTHING;
    END IF;
    RETURN NULL;
END $$;
"""

#: 되돌리면 0049 의 것으로.
OLD_UPDATED_FUNCTION = r"""
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
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(UPDATED_FUNCTION)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(OLD_UPDATED_FUNCTION)
