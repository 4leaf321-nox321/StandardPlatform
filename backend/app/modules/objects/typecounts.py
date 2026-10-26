"""타입별 객체 수 — **DB 트리거가 늘 맞춰 적는 계수표**(ADR 0038).

스키마 · 타입 목록 · 인터페이스 · 자기소개 · 그래프 · 코어 현황이 「타입마다 몇 건」 을
보이는데, 그것을 읽을 때마다 객체 표 전체를 셌다 — 운영(객체 수천만)에서 24초, 그 사이 목록
화면은 머리글 「부서 · 상태」 만 선 채 기다렸다(2026-10-26). 이제 객체가 생기고 지워질 때
트리거가 그 타입의 증감을 줄로 적고, 읽을 때는 그 줄들을 더한다(작은 표).

- 넣기 · 영구 지우기는 **문장마다 한 번**(전이 표) — 2만 줄 적재도 타입마다 증감 줄 하나.
- 지우기 · 되살리기 · 타입 옮기기는 그 칸을 SET 하는 갱신에서만, 값이 바뀐 행에만(행 트리거).
  속성만 고치는 덩어리 갱신(종류 변경 200만 건)에는 걸리지 않는다.
- **한 줄을 고쳐 쓰지 않고 줄을 더한다** — 타입마다 한 줄을 두고 고치면 그 줄이 잠겨, 같은
  타입을 넣는 두 트랜잭션이 서로의 커밋을 기다린다(2만 줄 적재 동안 화면의 「생성」 이 멈춘다).
  줄이 쌓이면 읽는 쪽이 합친다(`COMPACT_AT`).

시험은 모델로 표를 세우므로 메타데이터 이벤트로 걸고, 운영은 마이그레이션 `0085_type_counts` 가
**그날의 사본**으로 건다. 여기를 고치면 새 마이그레이션도 쓴다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import MetaData, event, text
from sqlalchemy.orm import Session

FUNCTIONS = """
CREATE OR REPLACE FUNCTION object_type_counts_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO object_type_counts (type_id, delta)
    SELECT type_id, count(*) FROM counted_new WHERE deleted_at IS NULL GROUP BY type_id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_type_counts_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO object_type_counts (type_id, delta)
    SELECT type_id, -count(*) FROM counted_old WHERE deleted_at IS NULL GROUP BY type_id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_type_counts_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.deleted_at IS NULL THEN
        INSERT INTO object_type_counts (type_id, delta) VALUES (OLD.type_id, -1);
    END IF;
    IF NEW.deleted_at IS NULL THEN
        INSERT INTO object_type_counts (type_id, delta) VALUES (NEW.type_id, 1);
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_type_counts_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM object_type_counts;
    RETURN NULL;
END $$;
"""

TRIGGERS = """
DROP TRIGGER IF EXISTS object_type_counts_insert ON objects;
CREATE TRIGGER object_type_counts_insert AFTER INSERT ON objects
    REFERENCING NEW TABLE AS counted_new
    FOR EACH STATEMENT EXECUTE FUNCTION object_type_counts_insert();

DROP TRIGGER IF EXISTS object_type_counts_delete ON objects;
CREATE TRIGGER object_type_counts_delete AFTER DELETE ON objects
    REFERENCING OLD TABLE AS counted_old
    FOR EACH STATEMENT EXECUTE FUNCTION object_type_counts_delete();

DROP TRIGGER IF EXISTS object_type_counts_update ON objects;
CREATE TRIGGER object_type_counts_update AFTER UPDATE OF type_id, deleted_at ON objects
    FOR EACH ROW WHEN (
        OLD.type_id IS DISTINCT FROM NEW.type_id
        OR (OLD.deleted_at IS NULL) IS DISTINCT FROM (NEW.deleted_at IS NULL)
    )
    EXECUTE FUNCTION object_type_counts_update();

DROP TRIGGER IF EXISTS object_type_counts_truncate ON objects;
CREATE TRIGGER object_type_counts_truncate AFTER TRUNCATE ON objects
    FOR EACH STATEMENT EXECUTE FUNCTION object_type_counts_truncate();
"""

#: 처음 한 번 — 지금 있는 것을 센다(마이그레이션). 객체 표 전체를 한 번 훑는다.
BACKFILL = """
DELETE FROM object_type_counts;
INSERT INTO object_type_counts (type_id, delta)
SELECT type_id, count(*) FROM objects WHERE deleted_at IS NULL GROUP BY type_id;
"""

#: 증감 줄이 이보다 많으면 읽는 쪽이 타입마다 한 줄로 합친다.
COMPACT_AT = 1000

#: 합치기는 한 번에 하나 — 둘이 겹쳐도 틀리지는 않지만(지운 줄만 합친다) 일만 두 번이다.
_COMPACT_LOCK = "object_type_counts:compact"

COMPACT = """
WITH gone AS (DELETE FROM object_type_counts RETURNING type_id, delta)
INSERT INTO object_type_counts (type_id, delta)
SELECT type_id, sum(delta) FROM gone GROUP BY type_id HAVING sum(delta) <> 0
"""


def counts(db: Session) -> dict[uuid.UUID, int]:
    """타입마다 지우지 않은 객체 수 — **부서와 상관없이 전부.** 보는 사람마다 보이는 것만
    세야 하는 자리(`VIEW_SCOPE=department`)는 이것을 쓰지 않는다
    (`permissions.sees_every_department`)."""
    rows = db.execute(
        text("SELECT type_id, sum(delta), count(*) FROM object_type_counts GROUP BY type_id")
    ).all()
    if sum(int(one[2]) for one in rows) > COMPACT_AT:
        compact()
    return {one[0]: int(one[1]) for one in rows if int(one[1])}


def compact() -> None:
    """증감 줄을 타입마다 한 줄로 — **따로 연 연결에서**(부른 요청의 트랜잭션과 상관없이)
    커밋한다. 지우는 순간에 보이는 줄만 합치므로, 그 사이 다른 트랜잭션이 적은 줄은 남는다."""
    from app.database import engine

    with engine.begin() as connection:
        got = connection.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": _COMPACT_LOCK}
        ).scalar()
        if got:
            connection.execute(text(COMPACT))


def attach(metadata: MetaData) -> None:
    """`create_all` 뒤에 함수 · 트리거를 건다(시험 · 모델로 세우는 길)."""

    def install(_target: Any, connection: Any, **_kw: Any) -> None:
        for sql in (FUNCTIONS, TRIGGERS):
            connection.exec_driver_sql(sql)

    event.listen(metadata, "after_create", install)
