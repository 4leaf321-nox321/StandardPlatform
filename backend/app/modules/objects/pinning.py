"""계보 선의 **고정을 DB 가 박는 자리**(ADR 0041) — 개정 값의 식 · 낡음의 식 · 트리거.

계보 관계(`RelationType.lineage_source`)의 선은 맺는 순간 원천의 **지금 개정 값**(원천 타입의
개정 칸, `ObjectType.revision_key`)과 **원천의 바뀐 때**(`objects.updated_at`)를 박는다
(`object_relations.pinned_revision` · `pinned_at`). 뒤에 원천이 바뀌면 그 파생물이 낡음이다
— 판정과 번짐은 `objects/lineage.py` 한 곳이고, 여기는 그 판정이 믿는 **값의 모양**과 **식**을
둔다.

## 식 둘 — DB 함수 하나씩

    sp_revision(properties, key)          개정 값의 글자 — 칸이 없으면(빈 키) NULL.
                                          JSONB 의 `->>` 그대로(숫자 3 은 「3」)
    sp_lineage_stale(key, properties,     그 선이 바로 낡았나 — 개정 칸이 있으면 「지금 값 ≠
                     updated_at,          고정한 값」, 없으면 「원천의 바뀐 때 > 고정한 때」.
                     pinned_revision,     고정한 때가 비면 낡지 않음(모른다)
                     pinned_at)

둘 다 `IMMUTABLE` SQL 함수라 플래너가 펼쳐 넣는다(함수를 부르는 값이 없다). 박는 트리거 ·
개정 칸을 바꿀 때의 옮김 · `lineage.py` 의 판정 질의가 **같은 함수**를 부른다 — 파이썬에서 따로
비교하면 숫자 개정(3 과 3.0)이나 빈 값에서 두 판정이 갈린다.

## 박는 자리 — 트리거 셋

    object_relations_kind      선을 넣을 때(`effectivity.py` — 종류를 한 번 읽어 유효 기간의
                               사본과 함께). 값을 실어 온 줄(묶음 · 되돌리기)은 그대로 둔다
    relation_types_lineage     관계 종류의 원천 끝을 바꿀 때 — 그 종류의 선 전부를 지금 값으로
                               박는다(끄면 비운다)
    object_types_revision      타입의 개정 칸을 바꿀 때 — 그 타입을 원천으로 둔 선의 고정을
                               **판정을 지키며** 새 칸으로 옮긴다(낡았던 선은 낡은 채로, 아니던
                               선은 아닌 채로)

**왜 트리거인가** — 선을 넣는 길이 열 곳이 넘는다(화면 · API · 관계 파일 · 묶음 · 되돌리기 ·
데이터 소스 · 합치기 · MCP · COPY 쓰기). ADR 0039 의 유효 기간 사본과 같은 사정이고, 같은
트리거가 종류를 이미 한 번 읽으므로 그 자리에 붙였다 — 계보 아닌 선에는 칸 하나를 더 읽는
값뿐이다.

시험은 모델로 표를 세우므로 메타데이터 이벤트로 걸고, 운영은 마이그레이션 `0088_lineage` 가
**그날의 사본**으로 건다. 여기를 고치면 새 마이그레이션도 쓴다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, event

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
"""


def attach(metadata: MetaData) -> None:
    """`create_all` 뒤에 — 함수가 표를 읽으므로 표가 다 선 뒤에. `sp_lineage_stale` 이
    `sp_revision` 을 부르므로 그 차례로 만든다(SQL 함수는 만들 때 본문을 확인한다)."""

    def install(_target: Any, connection: Any, **_kw: Any) -> None:
        for sql in (FUNCTIONS, TRIGGERS):
            connection.exec_driver_sql(sql)

    event.listen(metadata, "after_create", install)
