"""선의 유효 기간을 **DB 가 지키는 자리**(ADR 0039) — 표시 칸 하나와 트리거 둘. 선을 넣을 때
도는 트리거는 계보 선의 고정(ADR 0041)도 함께 박는다.

어느 선이 그 날 살아 있나를 가르는 판정은 `edges.py` 다. 여기는 그 판정이 믿는 데이터의 모양을
지킨다:

- **같은 선은 하나**(유효 기간이 꺼진 종류) — 예전의 유일 제약이 부분 색인
  (`uq_object_relations_edge`, `effectivity IS NULL`)이 됐다.
- **같은 선의 기간은 겹치지 않는다**(켜진 종류) — 배제 제약(`ex_object_relations_period`,
  `EXCLUDE USING gist … daterange && … WHERE effectivity`). 기간이 빈 선은 (-∞, ∞) 라 그
  종류의 다른 같은 선과 늘 겹친다 — 「늘 유효한 선 하나」 와 「기간 있는 선 여럿」 이 섞이지
  않는다.

두 제약을 가르는 것이 선마다 든 **종류의 사본**(`object_relations.effectivity` — 켜진 종류면
참, 아니면 NULL)이다. 부분 제약의 조건은 그 행의 칸만 볼 수 있어서(다른 표를 못 읽는다) 사본을
둔다.
**사본은 앱이 아니라 트리거가 맞춘다** — 선을 넣는 길이 열 곳이 넘고(화면 · 파일 · 묶음 ·
되돌리기 · 데이터 소스 · 합치기 · COPY), 한 길이 빠뜨리면 그 선은 제약 밖으로 샌다.

    object_relations_kind          선을 넣고 고칠 때 — 종류에서 사본을 읽어 넣고, 꺼진
                                   종류에 기간이 오면 거절한다. **같은 조회로** 계보 관계의
                                   선이면 맺는 순간의 고정(원천의 개정 값 · 바뀐 때)도 박는다
                                   (ADR 0041, 식은 `pinning.py`)
    relation_types_effectivity     종류의 표시를 바꿀 때 — 그 종류의 선 전부의 사본을 다시
                                   맞춘다(끄려는 종류에 기간이 든 선이 있으면 위 트리거가
                                   거절한다)

선마다 도는 트리거는 하나다 — 유효 기간(0039)과 계보 고정(0041)이 각자 트리거를 두면 선 하나를
넣을 때 관계 종류를 두 번 읽는다(일괄 입력 10만 줄이면 10만 번 더). 0039 때의 이름
`object_relations_effectivity` 는 0088 이 이 이름으로 바꿨다.

**왜 앱 검사가 아니라 배제 제약인가**(동시성 · 이중화): 앱이 「겹치는 것이 있나」 를 읽고
넣으면, 두 요청(두 서버)이 같은 순간에 서로를 못 보고 둘 다 넣는다. 배제 제약은 색인에 넣는
순간 다른 트랜잭션의 줄까지 보고 기다린다 — 유일 제약과 같은 보장이다. 앱은 앞서 같은 것을
읽어 **사람이 읽을 말**로 거절할 뿐이다(`edges.require_no_overlap`).

시험은 모델로 표를 세우므로 메타데이터 이벤트로 걸고, 운영은 마이그레이션
`0086_relation_periods` 가 **그날의 사본**으로 건다(선마다 도는 트리거는 `0088_lineage` 의
사본).
여기를 고치면 새 마이그레이션도 쓴다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, event

#: 배제 제약 · 기간 검사의 이름 — 오류를 사람의 말로 바꿀 때 이것으로 가른다(`edges`).
OVERLAP_CONSTRAINT = "ex_object_relations_period"
PERIOD_CHECK = "ck_object_relations_period"
SAME_EDGE = "uq_object_relations_edge"

FUNCTIONS = """
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
DROP TRIGGER IF EXISTS object_relations_kind ON object_relations;
CREATE TRIGGER object_relations_kind
    BEFORE INSERT OR UPDATE OF relation, valid_from, valid_to, effectivity ON object_relations
    FOR EACH ROW EXECUTE FUNCTION object_relations_kind();

DROP TRIGGER IF EXISTS relation_types_effectivity ON relation_types;
CREATE TRIGGER relation_types_effectivity AFTER UPDATE OF effectivity ON relation_types
    FOR EACH ROW WHEN (OLD.effectivity IS DISTINCT FROM NEW.effectivity)
    EXECUTE FUNCTION relation_types_effectivity();
"""


def attach(metadata: MetaData) -> None:
    """`create_all` 앞뒤로 — 배제 제약이 서려면 `btree_gist`(uuid · 글자의 `=` 를 gist 에서)가
    **표보다 먼저**, 트리거는 두 표가 다 선 뒤에."""

    def need(_target: Any, connection: Any, **_kw: Any) -> None:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS btree_gist")

    def install(_target: Any, connection: Any, **_kw: Any) -> None:
        for sql in (FUNCTIONS, TRIGGERS):
            connection.exec_driver_sql(sql)

    event.listen(metadata, "before_create", need)
    event.listen(metadata, "after_create", install)
