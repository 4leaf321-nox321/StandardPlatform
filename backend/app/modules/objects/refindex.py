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

- 객체를 넣거나 고치면(문장 단위, 바뀐 행만) 그 행의 줄을 다시 쓴다. 지운 객체(`deleted_at`)는
  줄이 없다 — 가리키는 쪽이 살아 있을 때만 「가리킨다」.
- 객체 행을 정말 지우면 FK(`ON DELETE CASCADE`)가 함께 지운다.
- 속성 정의가 참조가 되거나 참조가 아니게 되면(종류 변경 · 삭제 · 키 바꿈) 그 타입 · 그 칸을
  통째로 다시 쓴다. 순서는 상관없다 — 값을 먼저 바꾸든 정의를 먼저 바꾸든 끝은 같다.
- 가리키는 **상대**는 FK 로 묶지 않는다 — 원 표를 비추는 타입(부서 · 계정)의 id 도 들고, 지운
  상대를 가리키는 줄도 남아야 「지워진 것을 가리키는 칸」 을 찾는다.

## 두 벌인 이유

시험은 모델로 표를 만든다(`create_all`) — 그래서 이 파일의 SQL 을 메타데이터 이벤트로 건다.
운영은 마이그레이션(`0049_object_refs`)이 건다 — 마이그레이션은 앱 코드를 import 하지 않으므로
**그날의 사본**을 든다. 여기를 고치면 새 마이그레이션도 함께 쓴다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, event

UUID_TEXT = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

#: 줄을 다시 써야 하는 갱신 — 값 · 타입 · 지움이 바뀐 것만.
CHANGED = (
    "o.properties IS DISTINCT FROM n.properties "
    "OR o.type_id IS DISTINCT FROM n.type_id "
    "OR o.deleted_at IS DISTINCT FROM n.deleted_at"
)


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


_INSERT = "INSERT INTO object_refs (src_id, src_type_id, key, dst_id)"
_CHANGED_ROWS = f"(SELECT n.* FROM new_rows n JOIN old_rows o ON o.id = n.id WHERE {CHANGED})"
_TYPE_ROWS = "(SELECT * FROM objects WHERE type_id = NEW.owner_id)"

FUNCTIONS = f"""
CREATE OR REPLACE FUNCTION object_refs_inserted() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    {_INSERT}
    {rows_of("new_rows")}
    ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_refs_updated() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- 바뀐 행만 — updated_at 만 고친 갱신까지 다시 쓰면 큰 일괄 갱신이 두 배로 무겁다.
    DELETE FROM object_refs r
     USING new_rows n JOIN old_rows o ON o.id = n.id
     WHERE r.src_id = n.id AND ({CHANGED});
    {_INSERT}
    {rows_of(_CHANGED_ROWS)}
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
            {_INSERT}
            {rows_of(_TYPE_ROWS, "AND d.id = NEW.id")}
            ON CONFLICT DO NOTHING;
        END IF;
    END IF;
    RETURN NULL;
END $$;
"""

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
