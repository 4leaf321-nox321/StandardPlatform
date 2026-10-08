"""지표의 **날짜 칸 색인** — 증분 계산(`incremental.py`)이 바뀐 기간의 기록만 읽게.

증분은 「이 기간에 드는 원천 기록」 을 날짜 범위로 고른다. 색인이 없으면 그 타입의 기록을
전부 훑고 날짜를 하나씩 읽는다 — 묶기는 줄지만 읽기는 전량과 같다. 그래서 켜진 지표의 시간
칸마다 **식 색인**을 하나씩 둔다:

    CREATE INDEX CONCURRENTLY ix_objects_mt_<지문> ON objects
        (sp_date(properties ->> '<칸>')) WHERE type_id = '<원천>' AND deleted_at IS NULL

- **모델에 안 적는다.** 지표 정의는 실행 중에 생기고 바뀐다 — 마이그레이션이 미리 알 수 없다.
  자동 생성이 이것을 지우는 마이그레이션을 만들지 않게 `migrations/env.py` 가 이름 앞머리로
  거른다.
- **타이머 스크립트가 맞춘다**(`scripts/recompute_metrics.py`) — 워커의 트랜잭션 안에서는
  `CONCURRENTLY` 를 못 쓰고, 그것 없이 세우면 2,000만 건 표가 세우는 동안 쓰기를 막는다.
  자동 커밋 연결에서 하나씩 세우고, 너무 오래 기다리면(`lock_timeout`) 다음 차례로 미룬다.
- 안 쓰는 색인(지표를 지웠거나 끔 · 시간 칸을 바꿈)과 세우다 끊긴 색인(INVALID)은 지운다.
- 작은 타입에는 안 세운다 — 훑어도 싸고, 색인은 쓰기마다 값을 든다.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.modules.metrics.models import MetricDef
from app.modules.objects.models import ObjectInstance

PREFIX = "ix_objects_mt_"
#: 기록이 이보다 적은 타입에는 색인을 안 세운다.
MIN_ROWS = 50_000
#: 색인을 세우며 기다리는 한도 — 넘으면 다음 차례로. `CONCURRENTLY` 는 마지막에 **그보다
#: 먼저 시작한 트랜잭션이 다 끝나기를** 기다린다(밤의 지표 계산은 수 분). 기다리는 동안 쓰기는
#: 안 막으므로 넉넉히 둔다 — 짧으면 다 세운 색인을 마지막에 버린다.
LOCK_TIMEOUT = "30min"


def name_of(type_id: uuid.UUID, key: str) -> str:
    digest = hashlib.sha256(f"{type_id}:{key}".encode()).hexdigest()[:16]
    return f"{PREFIX}{digest}"


def wanted(db: Session, *, min_rows: int = MIN_ROWS) -> dict[str, tuple[uuid.UUID, str]]:
    """켜진 지표의 시간 칸 → 색인 이름 : (원천 타입, 칸)."""
    pairs: set[tuple[uuid.UUID, str]] = set()
    for source, spec in db.execute(
        select(MetricDef.source_type_id, MetricDef.spec).where(MetricDef.is_active.is_(True))
    ):
        address = ((spec or {}).get("time") or {}).get("address") or ""
        if address.startswith("properties.") and len(address) > len("properties."):
            pairs.add((source, address.split(".", 1)[1]))
    out: dict[str, tuple[uuid.UUID, str]] = {}
    sizes: dict[uuid.UUID, int] = {}
    for source, key in sorted(pairs, key=lambda one: (str(one[0]), one[1])):
        if source not in sizes:
            # 어림(통계의 행 수)이 아니라 셈 — 타입마다 한 번, 색인(type_id)으로.
            sizes[source] = int(
                db.scalar(
                    select(func.count())
                    .select_from(ObjectInstance)
                    .where(ObjectInstance.type_id == source)
                )
                or 0
            )
        if sizes[source] >= min_rows:
            out[name_of(source, key)] = (source, key)
    return out


def existing(db: Session) -> dict[str, bool]:
    """있는 날짜 칸 색인 → 쓸 수 있나(세우다 끊기면 INVALID 로 남는다)."""
    rows = db.execute(
        text(
            "SELECT c.relname, i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_class t ON t.oid = i.indrelid "
            "WHERE t.relname = 'objects' AND c.relname LIKE :prefix"
        ),
        {"prefix": f"{PREFIX}%"},
    )
    return {str(name): bool(valid) for name, valid in rows}


def has(db: Session, type_id: uuid.UUID, key: str) -> bool:
    """이 타입 · 칸의 날짜 색인이 서 있고 쓸 수 있나 — 증분이 거르는 식을 고른다."""
    return bool(
        db.scalar(
            text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name"
            ),
            {"name": name_of(type_id, key)},
        )
    )


def create_sql(name: str, type_id: uuid.UUID, key: str) -> str:
    """키 · 타입을 글자로 박는다 — 색인 식은 매개변수를 못 받는다. 계산 쪽 식
    (`incremental.date_expr`)과 **글자 그대로 같아야** 플래너가 맞춰 본다."""
    quoted = key.replace("'", "''")
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON objects "
        f"(sp_date(properties ->> '{quoted}')) "
        f"WHERE type_id = '{uuid.UUID(str(type_id))}'::uuid AND deleted_at IS NULL"
    )


@dataclass
class Synced:
    created: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def sync(
    engine: Engine,
    db: Session,
    *,
    min_rows: int = MIN_ROWS,
    say: Callable[[str], None] = lambda _line: None,
) -> Synced:
    """있어야 할 색인을 세우고 안 쓰는 것을 지운다 — **하나씩, 자동 커밋으로.** 실패한 것은
    적고 넘어간다(다음 차례에 다시)."""
    want = wanted(db, min_rows=min_rows)
    have = existing(db)
    db.rollback()  # 읽은 트랜잭션을 닫는다 — 열린 채면 CONCURRENTLY 가 그것을 기다린다
    done = Synced()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
        for name, valid in sorted(have.items()):
            if name in want and valid:
                continue
            try:
                connection.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
                done.dropped.append(name)
                say(f"날짜 칸 색인 지움: {name}" + ("" if valid else " (세우다 끊긴 것)"))
            except Exception as caught:
                done.failed.append(f"{name}: {caught}")
        for name, (type_id, key) in sorted(want.items()):
            if have.get(name):
                continue
            say(f"날짜 칸 색인 세우는 중: {name} ({key})")
            try:
                connection.execute(text(create_sql(name, type_id, key)))
                done.created.append(name)
            except Exception as caught:
                # 끊긴 것은 INVALID 로 남는다 — 다음 차례에 지우고 다시 세운다.
                done.failed.append(f"{name}: {caught}")
    return done
