"""참조 색인 — 참조 칸의 값을 **좁은 표**(`object_refs`)에 두고 DB 트리거가 유지한다.

ADR 0010. 참조 칸(`object_ref`)은 상대의 id 를 JSONB(`properties-><칸>`, 글자 하나 또는
목록)에 담는다. 그래서 「이 개발모델을 가리키는 기록」 을 찾으려면 기록 타입을 통째로 읽어야
했다 — 200만 건에서 개발모델 상세가 오류로 멈추고, 「참조 너머 칸」 으로 거르거나 묶는 질의는
2분을 넘겼다(실측). 값을 `(누가 · 어느 칸으로 · 누구를)` 한 줄씩 따로 두면 그 물음이 인덱스
하나로 끝난다.

## 왜 트리거인가

객체를 쓰는 길이 열 곳이 넘는다 — 화면 · 일괄 입력 · 묶음 · 데이터 소스 · 종류 변경 · 병합 ·
이력 되돌리기 · 코드표 승격 · 영구 삭제 …. 코드로 맞추면 언젠가 한 길이 빠지고, 빠진 길로 쓴
값은 **색인에 없어서 조용히 안 보인다**(「관련 객체」 에서 사라진다). 그래서 쓰는 자리가 아니라
**표가** 맞춘다.

- 객체를 넣으면(문장 단위) 그 행의 줄을 넣는다. 고치면 **참조가 달라진 행의 달라진 줄만**
  지우고 넣는다 — 옛 값과 새 값의 줄을 집합으로 빼 본다(`EXCEPT`). 이름 · 값처럼 참조가 아닌
  칸만 고친 갱신은 아무 줄도 건드리지 않는다. 지운 객체(`deleted_at`)는 줄이 없다 — 가리키는
  쪽이 살아 있을 때만 「가리킨다」.
- 객체 행을 정말 지우면 FK(`ON DELETE CASCADE`)가 함께 지운다.
- 속성 정의가 참조가 되거나 참조가 아니게 되면(종류 변경 · 삭제 · 키 바꿈) 그 타입 · 그 칸을
  통째로 다시 쓴다. 순서는 상관없다 — 값을 먼저 바꾸든 정의를 먼저 바꾸든 끝은 같다.
- 가리키는 **상대**는 FK 로 묶지 않는다 — 원 표를 비추는 타입(부서 · 계정)의 id 도 들고, 지운
  상대를 가리키는 줄도 남아야 「지워진 것을 가리키는 칸」 을 찾는다.

## 두 벌인 이유

시험은 모델로 표를 만든다(`create_all`) — 그래서 이 파일의 SQL 을 메타데이터 이벤트로 건다.
운영은 마이그레이션(`0049_object_refs`, 고치기는 `0075_refs_trigger`)이 건다 — 마이그레이션은
앱 코드를 import 하지 않으므로 **그날의 사본**을 든다. 여기를 고치면 새 마이그레이션도 함께
쓴다(`tests/unit/test_refindex_sql.py` 가 사본과 같은 글인지 본다).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, event

UUID_TEXT = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def rows_of(rows: str, key_filter: str = "") -> str:
    """행 집합(`rows`, 별칭 `n`)에서 참조 줄(src_id · src_type_id · key · dst_id)을 뽑는
    SELECT. 넣기 · 고치기 · 정의 바꿈 · 처음 채우기가 **같은 것**을 쓴다."""
    return f"""
    SELECT DISTINCT n.id, n.type_id, d.key, v.value::uuid
      FROM {rows} n
      JOIN property_defs d
        ON d.owner_kind = 'type' AND d.owner_id = n.type_id
       AND d.data_type = 'object_ref' {key_filter}
      CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE jsonb_typeof(n.properties -> d.key)
              WHEN 'array' THEN n.properties -> d.key
              WHEN 'string' THEN jsonb_build_array(n.properties -> d.key)
              ELSE '[]'::jsonb
          END
      ) AS v(value)
     WHERE n.deleted_at IS NULL
       AND v.value ~ '{UUID_TEXT}'
    """


#: 참조가 달라졌을 수 있는 갱신(옛 행 `o` · 새 행 `n` · 그 타입의 참조 칸 이름들 `k.keys`) —
#: 타입이 바뀌었거나, 살았는지(지움)가 바뀌었거나, **참조 칸** 하나라도 값이 다른 것. 이름 ·
#: 값처럼 참조가 아닌 칸만 고친 갱신은 여기서 빠진다.
#:
#: ⚠️ 예전에는 「속성이 바뀌었나」 만 봤다 — 일괄 입력이 비용 칸 하나를 고쳐도 그 행의 참조 줄을
#:    전부 지우고 다시 넣었다(5만 줄이면 23만 줄을 지우고 넣고 외래키를 다시 본다, 2026-10-10).
REF_CHANGED = (
    "o.type_id IS DISTINCT FROM n.type_id "
    "OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL) "
    "OR (o.properties IS DISTINCT FROM n.properties AND EXISTS ("
    "SELECT 1 FROM unnest(k.keys) AS r(key) "
    "WHERE (o.properties -> r.key) IS DISTINCT FROM (n.properties -> r.key)))"
)

#: 타입마다 참조 칸 이름들 — 행마다 정의를 따로 찾지 않게 한 번 모은다.
_REF_KEYS = (
    "(SELECT owner_id, array_agg(key::text) AS keys FROM property_defs "
    "WHERE owner_kind = 'type' AND data_type = 'object_ref' GROUP BY owner_id)"
)

#: 이만큼보다 큰 갱신 문장은 트리거가 **그때그때 계획한다**(`EXECUTE`).
#:
#: ⚠️ plpgsql 은 정적 SQL 의 계획을 세션의 첫 호출에서 굳혀 두고 다시 쓴다 — 전이 표의 크기도
#:    그때 것으로 어림한다. 한 줄을 고친 연결이 그 뒤에 5천 줄짜리 갱신을 하면 한 줄짜리
#:    계획(옛 행 x 새 행 중첩 반복)으로 5천 x 5천을 돌아 **덩어리 하나가 39초**였다(1.3초여야
#:    할 것). 계획이 언제 다시 서는지(다른 연결의 자동 청소가 표 통계를 고칠 때)는 우연이라,
#:    같은 일괄 갱신이 11 ~ 81초로 흔들렸다(2026-10-10 실측 — GIN 대기 목록 탓으로 보였던 것).
#:    작은 문장은 정적 SQL 그대로다: 계획을 매번 세우는 값이 한 줄 고치기에는 그 일보다 크다.
PLAN_EACH_ABOVE = 64

_INSERT = "INSERT INTO object_refs (src_id, src_type_id, key, dst_id)"
_TYPE_ROWS = "(SELECT * FROM objects WHERE type_id = NEW.owner_id)"

#: 갱신 문장에서 참조가 달라졌을 수 있는 행 — 옛 값과 새 값을 함께 든다. `whole` 은 타입이나
#: 지움이 바뀐 행이다(칸마다가 아니라 그 행의 줄 전부가 달라질 수 있다).
_CHANGED = f"""ch AS MATERIALIZED (
        SELECT n.id, n.type_id, n.deleted_at, n.properties,
               o.type_id AS old_type_id, o.deleted_at AS old_deleted_at,
               o.properties AS old_properties,
               (o.type_id IS DISTINCT FROM n.type_id
                OR (o.deleted_at IS NULL) IS DISTINCT FROM (n.deleted_at IS NULL)) AS whole
          FROM new_rows n
          JOIN old_rows o ON o.id = n.id
          LEFT JOIN {_REF_KEYS} k ON k.owner_id = n.type_id
         WHERE {REF_CHANGED}
    )"""
#: 옛 값 · 새 값 쪽 행 — `other` 는 맞은편 값이다(값이 그대로인 칸은 양쪽에서 함께 뺀다).
_OLD = (
    "(SELECT id, old_type_id AS type_id, old_deleted_at AS deleted_at, "
    "old_properties AS properties, properties AS other, whole FROM ch)"
)
_NEW = "(SELECT id, type_id, deleted_at, properties, old_properties AS other, whole FROM ch)"
#: 값이 바뀐 칸만 줄로 푼다 — 값이 같은 칸의 줄은 옛 쪽 · 새 쪽에 똑같이 있어 빼면 사라지니
#: 처음부터 풀지 않는다(참조 칸 하나를 고친 5만 줄에서 풀 것이 다섯에 하나로 준다).
_ONLY_MOVED = "AND (n.whole OR (n.properties -> d.key) IS DISTINCT FROM (n.other -> d.key))"

#: 옛 값에만 있던 줄을 지운다 — 새 값에도 있는 줄은 그대로 둔다.
_UPDATE_DELETE = f"""WITH {_CHANGED}
    DELETE FROM object_refs r
     USING (({rows_of(_OLD, _ONLY_MOVED)}) EXCEPT ({rows_of(_NEW, _ONLY_MOVED)}))
           AS g (src_id, src_type_id, key, dst_id)
     WHERE r.src_id = g.src_id AND r.key = g.key AND r.dst_id = g.dst_id"""

#: 새 값에만 있는 줄을 넣는다. 타입이 바뀐 행은 같은 (src, key, dst) 를 앞 문장이 지웠다.
_UPDATE_INSERT = f"""WITH {_CHANGED}
    {_INSERT}
    ({rows_of(_NEW, _ONLY_MOVED)}) EXCEPT ({rows_of(_OLD, _ONLY_MOVED)})
    ON CONFLICT DO NOTHING"""

INSERTED_FUNCTION = f"""
CREATE OR REPLACE FUNCTION object_refs_inserted() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    {_INSERT}
    {rows_of("new_rows")}
    ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;
"""

#: 고치기 — 0075 가 운영에 거는 글(그날의 사본)과 같아야 한다.
UPDATED_FUNCTION = f"""
CREATE OR REPLACE FUNCTION object_refs_updated() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 참조가 달라진 행의 달라진 줄만 — 비용 · 이름만 고친 갱신은 아무 줄도 안 건드린다.
    -- 큰 문장은 그때그때 계획한다(굳은 한 줄짜리 계획으로 5천 줄을 돌면 39초, 2026-10-10).
    IF (SELECT count(*) FROM new_rows) > {PLAN_EACH_ABOVE} THEN
        EXECUTE $sql$
    {_UPDATE_DELETE}
        $sql$;
        EXECUTE $sql$
    {_UPDATE_INSERT}
        $sql$;
    ELSE
    {_UPDATE_DELETE};
    {_UPDATE_INSERT};
    END IF;
    RETURN NULL;
END $$;
"""

REDEFINED_FUNCTION = f"""
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
            {_INSERT}
            {rows_of(_TYPE_ROWS, "AND d.id = NEW.id")}
            ON CONFLICT DO NOTHING;
        END IF;
    END IF;
    RETURN NULL;
END $$;
"""

FUNCTIONS = INSERTED_FUNCTION + UPDATED_FUNCTION + REDEFINED_FUNCTION

TRIGGERS = """
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

#: 이미 있는 값으로 채운다 — 마이그레이션이 한 번 돈다.
BACKFILL = f"{_INSERT} {rows_of('objects')} ON CONFLICT DO NOTHING"


def attach(metadata: MetaData) -> None:
    """`create_all` 뒤에 함수 · 트리거를 건다(시험 · 모델로 세우는 길).

    표가 다 선 뒤라야 `property_defs` 에 트리거를 걸 수 있다 — 그래서 표 하나가 아니라
    메타데이터에 건다."""

    def install(_target: Any, connection: Any, **_kw: Any) -> None:
        for sql in (FUNCTIONS, TRIGGERS):
            connection.exec_driver_sql(sql)

    event.listen(metadata, "after_create", install)
