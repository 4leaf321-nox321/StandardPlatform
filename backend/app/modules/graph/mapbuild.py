"""지도 만들기 — **판(전체 배치 · 매일 덧붙임)과 시간마다의 집계**(ADR 0046). 작업 셋이 부른다.

- 전체 배치: 처음 판, 그리고 시스템 관리자의 「전체 다시 배치」. 기준정보 전부를 힘 배치로
  놓고(앞 판이 있으면 그 자리에서 출발 — 기존 자리가 1 ~ 5% 움직인다), 원반 크기를 정할 수를
  얼리고, 공동 참조 위 k 선을 적는다.
- 덧붙임: 매일 밤. **새 기준정보만** 빈자리에 놓는다 — 기존 자리는 고정(움직임 0). 새 허브의
  수만 얼린다(기존 원반은 판의 크기 그대로).
- 집계: 한 시간마다. (허브, 기록 타입, 부서) → 지금의 수. 트리거로 유지하지 않는다.

**셋은 겹쳐 돌지 않는다** — 같은 자문 잠금(`LOCK`)을 트랜잭션 동안 쥔다. 배치는 기다리고
(관리자가 누른 것이 사라지면 안 된다), 집계는 못 잡으면 건너뛴다(배치가 집계를 함께 한다).

기록의 자리는 여기서 정하지 않는다 — 원반 안에서 id 해시가 정한다(`maphash`). 여기서 정하는
것은 기준정보의 자리와 원반의 반지름(= 판의 `scale` x √얼린 수)뿐이다.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.graph import mapengine as engine
from app.modules.graph import maphash, maphubs
from app.modules.graph.models import (
    MapCount,
    MapCounted,
    MapFrozen,
    MapLayout,
    MapLink,
    MapMaster,
)
from app.modules.objects import edges as edge_rule
from app.modules.objects import typecounts
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.shared.errors import AppError, code

log = logging.getLogger(__name__)

#: 기준정보 상한 — 넘으면 판을 만들지 않고 **정직하게 거절한다.** 기준정보까지 수십만이면
#: 힘 배치의 질이 무너지고(100만에서 이웃 적중 0.013 — 측정 보고) 자리 표가 커진다. 그 설치는
#: 기준정보 층에 자리 표 + GiST, 멀리서는 기준정보도 집계로 가야 한다(ADR 0046 「다음」).
MAX_MASTERS = 200_000
#: 지도 틀의 크기 — 전체 배치가 기준정보 범위를 이만큼으로 맞춘다(덧붙임은 그 틀 그대로).
SPAN = 1000.0
FIRST_ITERS = 300
#: 전체 다시 배치 — 앞 판의 자리에서 출발해 덜 돌린다(기존 자리가 1 ~ 5% 움직인다). 80번 · 온도
#: 0.25 로 두었더니 2,000만 건 DB 에서 가운데 3.8% · 95% 7.7% 움직였다(실측) — 측정 보고의 안정
#: 시험(50번 · 0.1)으로 낮췄다.
RELAYOUT_ITERS = 50
RELAYOUT_TEMP = 0.1
#: 덧붙임 — 새 기준정보만 움직인다.
APPEND_ITERS = 50
APPEND_TEMP = 0.1
#: 공동 참조 선은 노드마다 위 k 개만(코사인 정규화 뒤).
TOPK = 8
#: 허브끼리 가장 가까운 거리의 가운데값 = 가운데 원반 반지름의 이 배.
DISC_SPACING = 2.2
LOCK = "graph-map"
KINDS = ("graph_map_full", "graph_map_append", "graph_map_count")
#: 시간마다의 집계 — 지난 집계에서 이만큼 지났으면 넣는다(타이머가 5분마다 본다).
COUNT_EVERY = timedelta(minutes=55)
#: 매일 덧붙임 — 밤 시간에 지난 판에서 이만큼 지났으면. 밤을 놓쳤으면 `APPEND_MISSED` 뒤에 아무
#: 때나.
APPEND_EVERY = timedelta(hours=20)
APPEND_MISSED = timedelta(hours=30)
#: 실패 · 취소한 **집계**를 타이머가 다시 넣기까지. 전체 배치 · 덧붙임이 실패 · 취소되면
#: 타이머는 사람이 「전체 다시 배치」 를 누를 때까지 다시 넣지 않는다(ADR 0049) — 고장 추적
#: DB(293GB)에서 전체 배치가 메모리를 다 써 강제 종료되고, 판이 없으니 5분마다 다시 넣어 끝없이
#: 돌았다(운영).
COUNT_RETRY = timedelta(hours=6)
#: 공동 참조 짝짓기가 지나가는 짝 줄의 상한 — 넘을 것 같으면 기록을 id 해시로 고르게 덜어
#: 낸다. 2,000만 건 DB 는 기록마다 참조가 대여섯이라 다 든다. 기록이 서로 다른 것을 수십 개씩
#: 가리키면 짝은 그 제곱으로 는다(기록 하나에 참조 45개면 짝 990줄). 운영의 고장 추적 DB 에서
#: 전체 배치가 임시 파일 50GB · 메모리 31GB 를 다 썼다(2026-11-02).
COREF_PAIR_ROWS = 200_000_000
#: 짝지을 참조 줄의 상한 — 짝이 적어도 참조 줄이 많으면 두 벌을 잇는 조인이 디스크로
#: 넘친다(2,000만 건 DB: 기록 참조 5,019만 줄에 임시 파일 5.5GB, 2026-11-02). 넘으면 같은
#: 길로 덜어 낸다.
COREF_REF_ROWS = 100_000_000
#: 짝 줄을 어림하려고 읽는 참조 표의 몫(%, 블록 표본).
COREF_PROBE_PERCENT = 1.0
#: 기준정보마다 받는 공동 참조 짝 — 배치는 노드마다 위 `TOPK` 개만 쓰므로 그 몇 배만 받는다.
#: 안 거르면 짝 전부가 파이썬 목록으로 올라온다.
COREF_KEEP = 3 * TOPK

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

Progress = Callable[[str, int, int], None]


def _quiet(_stage: str, _done: int, _total: int) -> None:
    return None


def lock(db: Session, *, wait: bool) -> bool:
    """지도 작업의 자문 잠금 — 트랜잭션이 끝날 때 풀린다."""
    if wait:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK})
        return True
    return bool(
        db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": LOCK})
    )


def current(db: Session) -> MapLayout | None:
    return db.scalar(select(MapLayout).order_by(MapLayout.id.desc()).limit(1))


# --- 집계 ------------------------------------------------------------------------


@dataclass
class Counts:
    rows: list[tuple[uuid.UUID | None, uuid.UUID, uuid.UUID | None, int]]
    """(허브 — 비면 주 참조가 빔, 기록 타입, 부서 — 비면 전역, 수)."""
    seconds: float


def count_records(db: Session, hub_keys: dict[str, str | None]) -> Counts:
    """(허브, 기록 타입, 부서) → 지금의 수 — **객체 표를 한 번** 훑는다(2,000만 건 9 ~ 16초).

    허브는 판의 칸 값(`properties ->> 칸`)이다. 비었거나 id 꼴이 아니면 「미지정」(None)."""
    started = time.perf_counter()
    records = [uuid.UUID(one) for one in hub_keys]
    if not records:
        return Counts([], 0.0)
    params: dict[str, Any] = {"records": records}
    whens: list[str] = []
    for at, (type_id, key) in enumerate(sorted(hub_keys.items())):
        if key:
            whens.append(
                f"WHEN CAST(:t{at} AS uuid) THEN o.properties ->> CAST(:k{at} AS text)"
            )
            params[f"t{at}"] = type_id
            params[f"k{at}"] = key
    hub = f"CASE o.type_id {' '.join(whens)} END" if whens else "NULL::text"
    rows = db.execute(
        text(f"""
            SELECT {hub} AS hub, o.type_id, o.owner_workspace_id, count(*)
              FROM objects o
             WHERE o.type_id = ANY(CAST(:records AS uuid[])) AND o.deleted_at IS NULL GROUP BY
             1, 2, 3
        """),
        params,
    ).all()
    merged: dict[tuple[uuid.UUID | None, uuid.UUID, uuid.UUID | None], int] = defaultdict(int)
    for raw, type_id, workspace_id, n in rows:
        hub_id = uuid.UUID(raw) if isinstance(raw, str) and _UUID.match(raw) else None
        merged[(hub_id, type_id, workspace_id)] += int(n)
    return Counts(
        [(h, t, w, n) for (h, t, w), n in merged.items()],
        round(time.perf_counter() - started, 3),
    )


def _store_counts(db: Session, counts: Counts, layout_id: int) -> None:
    db.execute(delete(MapCount))
    _insert(
        db,
        MapCount,
        [{"hub_id": h, "type_id": t, "workspace_id": w, "n": n} for h, t, w, n in counts.rows],
    )
    values = {
        "counted_at": datetime.now(UTC),
        "seconds": counts.seconds,
        "layout_id": layout_id,
    }
    # 한 줄을 갈아 쓴다 — 같은 트랜잭션에서 두 번 불려도(배치 뒤 집계) 줄은 하나다.
    db.execute(
        pg_insert(MapCounted)
        .values(id=1, **values)
        .on_conflict_do_update(index_elements=[MapCounted.id], set_=values)
    )


def _insert(db: Session, model: Any, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), 5000):
        chunk = rows[start : start + 5000]
        if chunk:
            db.execute(insert(model), chunk)


#: 점 표를 바뀐 기록만으로 맞출 때 지난 맞춤보다 이만큼 앞부터 다시 본다 — 그 맞춤 때 아직
#: 커밋 안 된 긴 트랜잭션(일괄 입력 · 동기화)이 앞선 수정 시각으로 들어온 것을 놓치지 않게.
POINTS_OVERLAP = timedelta(hours=2)


def _hub_expr(hub_keys: dict[str, str | None], params: dict[str, Any]) -> str:
    """기록 타입별 판의 허브 칸 값(글자)."""
    whens: list[str] = []
    for at, (type_id, key) in enumerate(sorted(hub_keys.items())):
        if key:
            whens.append(
                f"WHEN CAST(:pt{at} AS uuid) THEN o.properties ->> CAST(:pk{at} AS text)"
            )
            params[f"pt{at}"] = type_id
            params[f"pk{at}"] = key
    return f"CASE o.type_id {' '.join(whens)} END" if whens else "NULL::text"


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def refresh_points(db: Session, hub_keys: dict[str, str | None]) -> dict[str, Any]:
    """확대 점 표를 맞춘다 — 허브 칸이 점 표의 것과 같으면 **지난 맞춤 뒤 바뀐 기록만**(수정
    시각 색인 `ix_objects_type_updated`), 다르거나 처음이면 통째로. 점 표에는 허브 칸 값이 id
    꼴인 살아 있는 기록만 든다(지운 기록 · 미지정은 없다). 통째로 채우는 동안 점 표는
    잠긴다(확대가 그동안 기다린다) — 처음 판과 허브 칸을 바꾼 뒤의 전체 배치에서만이다."""
    started = time.perf_counter()
    state = db.get(MapCounted, 1)
    records = [uuid.UUID(one) for one in hub_keys]
    now = db.scalar(text("SELECT transaction_timestamp()"))
    params: dict[str, Any] = {"records": records, "pattern": _UUID.pattern}
    hub = _hub_expr(hub_keys, params)
    rows = f"""
        SELECT o.id, CAST(hub AS uuid), sp_map_hash(o.id)
          FROM objects o CROSS JOIN LATERAL (SELECT {hub} AS hub) k
         WHERE o.type_id = ANY(CAST(:records AS uuid[])) AND o.deleted_at IS NULL
           AND k.hub ~ :pattern
    """
    full = state is None or state.points_at is None or state.points_keys != hub_keys
    if full:
        # 통째로 — 색인을 떼고 넣은 뒤 다시 세운다(색인을 단 채 2,000만 줄을 넣으면 해시 차례라
        # 쪽지가 무작위로 흩어진다).
        db.execute(text("TRUNCATE graph_map_points"))
        db.execute(text("DROP INDEX IF EXISTS ix_graph_map_points_hub"))
        db.execute(
            text("ALTER TABLE graph_map_points DROP CONSTRAINT IF EXISTS pk_graph_map_points")
        )
        done = _rowcount(
            db.execute(text(f"INSERT INTO graph_map_points (id, hub_id, h) {rows}"), params)
        )
        db.execute(
            text(
                "ALTER TABLE graph_map_points "
                "ADD CONSTRAINT pk_graph_map_points PRIMARY KEY (id)"
            )
        )
        db.execute(
            text(
                "CREATE INDEX ix_graph_map_points_hub "
                "ON graph_map_points (hub_id, h) INCLUDE (id)"
            )
        )
        changed = done
    else:
        assert state is not None and state.points_at is not None
        params["since"] = state.points_at - POINTS_OVERLAP
        db.execute(
            text("""
                CREATE TEMPORARY TABLE graph_map_changed ON COMMIT DROP AS
                SELECT id FROM objects
                 WHERE type_id = ANY(CAST(:records AS uuid[])) AND updated_at >= :since
            """),
            params,
        )
        changed = int(db.scalar(text("SELECT count(*) FROM graph_map_changed")) or 0)
        db.execute(
            text("DELETE FROM graph_map_points WHERE id IN (SELECT id FROM graph_map_changed)")
        )
        done = _rowcount(
            db.execute(
                text(
                    f"INSERT INTO graph_map_points (id, hub_id, h) {rows} "
                    "AND o.id IN (SELECT id FROM graph_map_changed)"
                ),
                params,
            )
        )
        db.execute(text("DROP TABLE graph_map_changed"))
    db.execute(
        pg_insert(MapCounted)
        .values(
            id=1,
            counted_at=now,
            seconds=0.0,
            layout_id=0,
            points_at=now,
            points_keys=hub_keys,
        )
        .on_conflict_do_update(
            index_elements=[MapCounted.id], set_={"points_at": now, "points_keys": hub_keys}
        )
    )
    return {
        "points": "all" if full else "changed",
        "points_rows": int(done or 0),
        "points_changed": changed,
        "points_seconds": round(time.perf_counter() - started, 2),
    }


def run_count(db: Session, progress: Progress = _quiet) -> dict[str, Any]:
    """시간마다의 집계 — 판의 허브 칸으로. 다른 지도 작업이 돌면 건너뛴다(그것이 함께 센다)."""
    if not lock(db, wait=False):
        return {"skipped": "다른 지도 작업이 돌고 있습니다 — 그 작업이 함께 셉니다."}
    _cap_temp(db)
    layout = current(db)
    if layout is None:
        return {"skipped": "지도 판이 아직 없습니다."}
    progress("세기", 0, 2)
    counts = count_records(db, layout.hub_keys)
    _store_counts(db, counts, layout.id)
    progress("점 표", 1, 2)
    points = refresh_points(db, layout.hub_keys)
    progress("점 표", 2, 2)
    return {"layout": layout.id, "rows": len(counts.rows), "seconds": counts.seconds, **points}


# --- 기준정보 그래프 ----------------------------------------------------------------


class Refused(AppError):
    """판을 만들지 않는다 — 기준정보가 상한을 넘는다."""


def refuse_if_too_many(
    db: Session, master_types: list[uuid.UUID], records: list[uuid.UUID]
) -> int:
    """계산하기 전에 센다(계수표 — 즉시). 넘으면 아무것도 읽지 않고 거절한다."""
    counted = typecounts.counts(db)
    total = sum(counted.get(one, 0) for one in master_types)
    if total > MAX_MASTERS:
        raise Refused(
            code("GRAPH", 10),
            f"기준정보가 {total:,}개로 지도의 상한({MAX_MASTERS:,}개)을 넘어 "
            "판을 만들지 않습니다 — 이 규모는 기준정보에도 자리 표와 집계가 필요합니다"
            "(ADR 0046 「다음」). 정적 그리기와 탐색은 그대로 쓸 수 있습니다.",
            status=409,
        )
    cap = get_settings().graph_map_max_records
    many = sum(counted.get(one, 0) for one in records)
    if cap > 0 and many > cap:
        raise Refused(
            code("GRAPH", 18),
            f"기록이 {many:,}건으로 지도의 상한({cap:,}건, GRAPH_MAP_MAX_RECORDS)을 넘어 "
            "판을 만들지 않습니다 — 확대 점 표가 기록 한 건에 한 줄이고, 계산이 서버의 "
            "메모리 · 디스크를 넘을 수 있습니다(ADR 0049). 정적 그리기와 탐색은 그대로 쓸 수 "
            "있습니다.",
            status=409,
        )
    return total


def _cap_temp(db: Session) -> None:
    """지도 작업의 임시 파일 상한 — 걸 수 있을 때만(`querycap.temp_limit_allowed`)."""
    from app.shared import querycap

    limit = get_settings().graph_map_temp_limit
    if limit and querycap.temp_limit_allowed(limit):
        db.execute(text("SELECT set_config('temp_file_limit', :v, true)"), {"v": limit})


@dataclass
class Masters:
    ids: list[uuid.UUID]
    type_ids: list[uuid.UUID]
    index: dict[uuid.UUID, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {one: at for at, one in enumerate(self.ids)}


def _masters(db: Session, master_types: list[uuid.UUID]) -> Masters:
    rows = db.execute(
        select(ObjectInstance.id, ObjectInstance.type_id)
        .where(ObjectInstance.type_id.in_(master_types), ObjectInstance.deleted_at.is_(None))
        .order_by(ObjectInstance.id)
    ).all()
    return Masters([one[0] for one in rows], [one[1] for one in rows])


def _direct_edges(
    db: Session, masters: Masters, master_types: list[uuid.UUID], only: set[uuid.UUID] | None
) -> set[tuple[int, int]]:
    """기준정보끼리의 선 — 참조 칸과 오늘 살아 있는 관계. `only` 가 있으면 그 노드에 닿는
    것만."""
    out: set[tuple[int, int]] = set()
    refs = text("""
        SELECT src_id, dst_id FROM object_refs WHERE src_type_id = ANY(CAST(:types AS uuid[]))
    """)
    for src, dst in db.execute(refs, {"types": master_types}):
        a, b = masters.index.get(src), masters.index.get(dst)
        touches = only is None or src in only or dst in only
        if a is not None and b is not None and a != b and touches:
            out.add((min(a, b), max(a, b)))
    rel = ObjectRelation
    src_obj = select(ObjectInstance.id).where(
        ObjectInstance.type_id.in_(master_types), ObjectInstance.deleted_at.is_(None)
    )
    query = select(rel.src_object_id, rel.dst_object_id).where(
        edge_rule.alive(rel, edge_rule.at(None)),
        rel.src_object_id.in_(src_obj),
    )
    if only is not None:
        query = query.where(
            rel.src_object_id.in_(list(only)) | rel.dst_object_id.in_(list(only))
        )
    for src, dst in db.execute(query):
        a, b = masters.index.get(src), masters.index.get(dst)
        if a is not None and b is not None and a != b:
            out.add((min(a, b), max(a, b)))
    return out


def by_workspace() -> bool:
    """공동 참조 선을 부서마다 따로 세나 — 조회를 부서로 좁힌 설치(`VIEW_SCOPE=department`)만.
    따로 세려면 기록마다 객체 표를 이어야 해서 2,000만 건 DB 에서 20초가 129초가 됐다(측정)."""
    return get_settings().view_scope == "department"


@dataclass
class Coref:
    rows: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, int]]
    """(a, b, 부서, 기록 수) — 덜어 냈으면 수는 몫으로 나눈 어림이다."""
    share: float = 1.0
    """짝지은 기록의 몫 — 1 이면 전부."""
    estimated_pairs: int = 0


def _coref_share(db: Session, records: list[uuid.UUID]) -> tuple[float, int]:
    """(짝지을 기록의 몫, 어림한 짝 줄) — 참조 표를 블록 표본으로 조금 읽어 기록의 참조 줄과
    기록마다의 짝(참조 수 C 2)을 어림한다. 둘 다 상한(`COREF_REF_ROWS` · `COREF_PAIR_ROWS`)
    안이면 1."""
    refs, pairs_seen = db.execute(
        text("""
            SELECT coalesce(sum(c), 0), coalesce(sum(c * (c - 1) / 2), 0) FROM (
              SELECT count(*) AS c
                FROM object_refs TABLESAMPLE SYSTEM (:pct) REPEATABLE (7)
               WHERE src_type_id = ANY(CAST(:records AS uuid[]))
               GROUP BY src_id) s
        """),
        {"pct": COREF_PROBE_PERCENT, "records": records},
    ).one()
    scale = 100 / COREF_PROBE_PERCENT
    ref_rows = float(refs or 0) * scale
    pairs = int(float(pairs_seen or 0) * scale)
    share = min(
        1.0,
        COREF_PAIR_ROWS / pairs if pairs else 1.0,
        COREF_REF_ROWS / ref_rows if ref_rows else 1.0,
    )
    return share, pairs


def _hash_cut(share: float) -> int:
    """`sp_map_hash`(부호 있는 64비트, 고르게)가 이것보다 작은 기록이 몫 `share` 다."""
    return min(int(-(2**63) + share * 2**64), 2**63 - 1)


def _coref(
    db: Session, records: list[uuid.UUID], only: list[uuid.UUID] | None, *, split: bool
) -> Coref:
    """공동 참조 — 기록 하나가 함께 가리킨 짝(a, b)과 기록 수(`split` 이면 부서별, 아니면 부서
    None 한 줄). 전량이면 참조 색인을 통째로 짝짓는다(2,000만 건 DB 20 ~ 63초). `only` 가
    있으면 그 기준정보를 가리키는 기록에서만(덧붙임).

    **상한(ADR 0049)** — 짝 줄이 `COREF_PAIR_ROWS` 를 넘을 것 같으면 기록을 id 해시로 고르게
    덜어 내고(수는 몫으로 나눠 어림), 전량이면 기준정보마다 위 `COREF_KEEP` 개 짝만 받는다."""
    if not records:
        return Coref([])
    db.execute(text("SET LOCAL work_mem = '256MB'"))
    share, estimated = _coref_share(db, records)
    params: dict[str, Any] = {"records": records}
    cut = ""
    if share < 1.0:
        cut = "AND sp_map_hash(a.src_id) < :cut"
        params["cut"] = _hash_cut(share)
        log.warning(
            "지도 공동 참조: 짝 줄 어림 %s — 기록의 %.1f%% 만 짝짓는다",
            f"{estimated:,}",
            100 * share,
        )
    ws = "o.owner_workspace_id" if split else "NULL::uuid"
    join = "JOIN objects o ON o.id = a.src_id" if split else ""
    if only is None:
        params["keep"] = COREF_KEEP
        sql = f"""
            WITH pairs AS (
                SELECT a.dst_id AS a, b.dst_id AS b, {ws} AS ws, count(*) AS n
                  FROM object_refs a
                  JOIN object_refs b
                    ON b.src_id = a.src_id AND (b.key, b.dst_id) > (a.key, a.dst_id)
                   AND b.dst_id <> a.dst_id
                  {join}
                 WHERE a.src_type_id = ANY(CAST(:records AS uuid[])) {cut}
                 GROUP BY 1, 2, 3
            ), ranked AS (
                SELECT a, b,
                       row_number() OVER (PARTITION BY a ORDER BY sum(n) DESC, b) AS ra,
                       row_number() OVER (PARTITION BY b ORDER BY sum(n) DESC, a) AS rb
                  FROM pairs GROUP BY a, b
            )
            SELECT p.a, p.b, p.ws, p.n
              FROM pairs p JOIN ranked r ON r.a = p.a AND r.b = p.b
             WHERE r.ra <= :keep OR r.rb <= :keep
        """
    else:
        sql = f"""
            SELECT a.dst_id, b.dst_id, {ws}, count(*)
              FROM object_refs a
              JOIN object_refs b ON b.src_id = a.src_id AND b.dst_id <> a.dst_id
              {join}
             WHERE a.dst_id = ANY(CAST(:only AS uuid[]))
               AND a.src_type_id = ANY(CAST(:records AS uuid[])) {cut}
             GROUP BY 1, 2, 3
        """
        params["only"] = only
    rows = [
        (a, b, w, int(n) if share >= 1.0 else max(1, round(int(n) / share)))
        for a, b, w, n in db.execute(text(sql), params)
    ]
    return Coref(rows, share, estimated)


@dataclass
class Graph:
    ea: np.ndarray
    eb: np.ndarray
    ew: np.ndarray
    links: list[tuple[int, int, uuid.UUID | None, int]]
    """남긴 공동 참조 짝의 부서별 수 — 판에 적는다."""
    direct: int
    coref_all: int


def _graph(
    n: int,
    direct: set[tuple[int, int]],
    coref_rows: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, int]],
    masters: Masters,
) -> Graph:
    per_pair: dict[tuple[int, int], int] = defaultdict(int)
    per_ws: dict[tuple[int, int], dict[uuid.UUID | None, int]] = defaultdict(dict)
    for a_id, b_id, ws, count in coref_rows:
        a, b = masters.index.get(a_id), masters.index.get(b_id)
        if a is None or b is None or a == b:
            continue
        key = (min(a, b), max(a, b))
        per_pair[key] += count
        per_ws[key][ws] = per_ws[key].get(ws, 0) + count
    pairs = list(direct) + [pair for pair in per_pair if pair not in direct]
    ea = np.array([one[0] for one in pairs], dtype=np.int64)
    eb = np.array([one[1] for one in pairs], dtype=np.int64)
    ew = np.array(
        [1.0] * len(direct) + [float(per_pair[pair]) for pair in pairs[len(direct) :]],
        dtype=np.float64,
    )
    is_coref = np.zeros(len(pairs), bool)
    is_coref[len(direct) :] = True
    ea2, eb2, w2, kept = engine.normalized_weights(n, ea, eb, ew, is_coref, TOPK)
    links: list[tuple[int, int, uuid.UUID | None, int]] = []
    for at in kept:
        if is_coref[at]:
            pair = pairs[at]
            for ws, count in per_ws[pair].items():
                links.append((pair[0], pair[1], ws, count))
    return Graph(ea2, eb2, w2, links, len(direct), int(is_coref.sum()))


# --- 원반 ------------------------------------------------------------------------


def _frozen_totals(
    counts: Counts, index: dict[uuid.UUID, int], n: int
) -> tuple[np.ndarray, dict[uuid.UUID, int]]:
    """허브마다 얼릴 수(부서 합)와 기록 타입마다 미지정 수."""
    hub_n = np.zeros(n, dtype=np.float64)
    unassigned: dict[uuid.UUID, int] = defaultdict(int)
    for hub, type_id, _ws, count in counts.rows:
        if hub is None:
            unassigned[type_id] += count
        elif hub in index:
            hub_n[index[hub]] += count
    return hub_n, unassigned


def _scale(pos: np.ndarray, hub_n: np.ndarray) -> float:
    """원반 반지름의 비례 상수 — 허브끼리 가장 가까운 거리의 가운데값이 가운데 반지름의
    `DISC_SPACING` 배가 되게. 허브가 하나뿐이면 틀의 1%."""
    hubs = np.where(hub_n > 0)[0]
    if len(hubs) == 0:
        return SPAN * 0.01
    sq = np.sqrt(hub_n[hubs])
    if len(hubs) < 2:
        return SPAN * 0.01 / float(sq[0])
    nearest = float(np.median(engine.nearest_other(pos[hubs])))
    return max(nearest, SPAN * 1e-4) / (DISC_SPACING * float(np.median(sq)))


def _lone_ring(ids: list[uuid.UUID], center: np.ndarray, radius: float) -> np.ndarray:
    """이어진 것이 없는 기준정보 — 바깥 띠에. 각도는 **id 해시**로(다른 기준정보가 늘거나
    줄어도 제 자리가 그대로다)."""
    out = np.zeros((len(ids), 2))
    for at, one in enumerate(ids):
        _u, v = maphash.uv(maphash.hash_of(one))
        angle = 2 * np.pi * v
        out[at] = center + radius * np.array([np.cos(angle), np.sin(angle)])
    return out


def _slots(
    records: list[uuid.UUID],
    unassigned: dict[uuid.UUID, int],
    reserve: dict[uuid.UUID, int],
    scale: float,
    center: np.ndarray,
    inner: float,
    kept: dict[str, list[float]] | None = None,
) -> dict[str, list[float]]:
    """기록 타입마다 「미지정」 자리 — 가장 바깥 띠. 잡아 두는 반지름은 미지정 수 + 부서 소유
    허브의 수(그 허브가 안 보이는 사람에게는 그 기록이 여기 온다). 덧붙임은 있던 자리를 그대로
    두고 새 타입만 더한다."""
    out = dict(kept or {})
    fresh = [one for one in records if str(one) not in out]
    if not fresh:
        return out
    sizes = np.array(
        [
            scale * np.sqrt(max(unassigned.get(one, 0) + reserve.get(one, 0), 1))
            for one in fresh
        ]
    )
    taken = [value[2] for value in out.values()]
    start = inner + (max(taken) if taken else 0.0)
    pos, _r = engine.ring(center, start, sizes, gap=float(sizes.max()) * 0.5)
    angle0 = len(out) * 0.37
    for at, one in enumerate(fresh):
        dx, dy = pos[at] - center
        c, s = np.cos(angle0), np.sin(angle0)
        x, y = center[0] + dx * c - dy * s, center[1] + dx * s + dy * c
        out[str(one)] = [float(x), float(y), float(sizes[at])]
    return out


def _reserve(db: Session, counts: Counts, records: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """기록 타입마다 부서 소유 허브를 가리키는 수 — 그 허브가 안 보이는 사람의 미지정 자리."""
    hubs = {hub for hub, _t, _w, _n in counts.rows if hub is not None}
    if not hubs:
        return {}
    owned = set(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.id.in_(list(hubs)),
                ObjectInstance.owner_workspace_id.is_not(None),
            )
        )
    )
    out: dict[uuid.UUID, int] = defaultdict(int)
    for hub, type_id, _w, count in counts.rows:
        if hub in owned and type_id in records:
            out[type_id] += count
    return out


def _bbox(pos: np.ndarray, radius: np.ndarray, slots: dict[str, list[float]]) -> list[float]:
    xs = [pos[:, 0] - radius, pos[:, 0] + radius]
    ys = [pos[:, 1] - radius, pos[:, 1] + radius]
    for x, y, r in slots.values():
        xs += [np.array([x - r, x + r])]
        ys += [np.array([y - r, y + r])]
    x0, x1 = float(np.min(np.concatenate(xs))), float(np.max(np.concatenate(xs)))
    y0, y1 = float(np.min(np.concatenate(ys))), float(np.max(np.concatenate(ys)))
    pad = max(x1 - x0, y1 - y0) * 0.03 + 1e-6
    return [x0 - pad, y0 - pad, x1 + pad, y1 + pad]


def _mass(n: int, ea: np.ndarray, eb: np.ndarray, hub_n: np.ndarray) -> np.ndarray:
    deg = np.bincount(ea, minlength=n) + np.bincount(eb, minlength=n)
    sq = np.sqrt(hub_n)
    middle = float(np.median(sq[sq > 0])) if (sq > 0).any() else 1.0
    return 1.0 + deg + sq / max(middle, 1.0) * 4


# --- 전체 배치 ---------------------------------------------------------------------


def run_full(
    db: Session, *, user_id: uuid.UUID | None, progress: Progress = _quiet
) -> dict[str, Any]:
    """전체 배치 — 처음 판이거나 관리자의 「전체 다시 배치」. 앞 판이 있으면 그 자리에서
    출발한다."""
    lock(db, wait=True)
    _cap_temp(db)
    started = time.perf_counter()
    master_types = maphubs.master_type_ids(db)
    hub_keys = maphubs.chosen_keys(db)
    records = [uuid.UUID(one) for one in hub_keys]
    refuse_if_too_many(db, master_types, records)
    progress("기준정보", 0, 5)
    masters = _masters(db, master_types)
    n = len(masters.ids)
    progress("선", 1, 5)
    t_edges = time.perf_counter()
    direct = _direct_edges(db, masters, master_types, None)
    split = by_workspace()
    coref = _coref(db, records, None, split=split)
    graph = _graph(n, direct, coref.rows, masters)
    edge_seconds = time.perf_counter() - t_edges
    progress("수", 2, 5)
    counts = count_records(db, hub_keys)
    hub_n, unassigned = _frozen_totals(counts, masters.index, n)
    progress("배치", 3, 5)
    t_layout = time.perf_counter()
    previous = current(db)
    before = (
        {row.object_id: (row.x, row.y) for row in db.scalars(select(MapMaster))}
        if previous is not None
        else {}
    )
    deg = np.bincount(graph.ea, minlength=n) + np.bincount(graph.eb, minlength=n)
    connected = np.where(deg > 0)[0]
    lone = np.where(deg == 0)[0]
    remap = -np.ones(n, np.int64)
    remap[connected] = np.arange(len(connected))
    mass = _mass(n, graph.ea, graph.eb, hub_n)
    sub_ea, sub_eb = remap[graph.ea], remap[graph.eb]
    known = np.array([masters.ids[one] in before for one in connected], dtype=bool)
    moved: dict[str, float] = {}
    if previous is not None and known.any():
        init = _warm_start(masters, connected, known, before, sub_ea, sub_eb)
        pos_c, stats = engine.layout(
            len(connected),
            sub_ea,
            sub_eb,
            graph.ew,
            mass=mass[connected],
            init=init,
            iters=RELAYOUT_ITERS,
            start_temp=RELAYOUT_TEMP,
        )
        # 앞 판의 틀에 맞춘다 — 지도가 통째로 돌거나 커지지 않게.
        old = np.array([before[masters.ids[one]] for one in connected[known]])
        order = np.r_[np.where(known)[0], np.where(~known)[0]]
        aligned = engine.align(old, pos_c[order])
        pos_c[order] = aligned
        shift = np.linalg.norm(pos_c[known] - old, axis=1) / SPAN
        moved = {
            "p50": round(float(np.median(shift)), 4),
            "p95": round(float(np.percentile(shift, 95)), 4),
            "max": round(float(shift.max()), 4),
        }
    else:
        pos_c, stats = engine.layout(
            len(connected), sub_ea, sub_eb, graph.ew, mass=mass[connected], iters=FIRST_ITERS
        )
        if len(connected):
            lo, span = engine.robust_box(pos_c)
            pos_c = (pos_c - (lo + span / 2)) / span * SPAN
    pos = np.zeros((n, 2))
    pos[connected] = pos_c
    center = np.median(pos_c, axis=0) if len(connected) else np.zeros(2)
    extent = (
        float(np.percentile(np.linalg.norm(pos_c - center, axis=1), 99))
        if len(connected)
        else SPAN * 0.3
    )
    scale = _scale(pos, hub_n)
    radius = scale * np.sqrt(hub_n)
    ring_r = extent + 2 * float(radius.max() if n else 0.0) + SPAN * 0.05
    pos[lone] = _lone_ring([masters.ids[one] for one in lone], center, ring_r)
    pos, fix = engine.disc_overlap_fix(pos, radius)
    reserve = _reserve(db, counts, records)
    slots = _slots(
        records,
        unassigned,
        reserve,
        scale,
        center,
        ring_r + float(radius.max() if n else 0) * 2 + SPAN * 0.05,
    )
    layout_seconds = time.perf_counter() - t_layout
    progress("쓰기", 4, 5)
    layout = MapLayout(
        kind="full",
        base_id=0,
        created_by=user_id,
        scale=float(scale),
        bbox=_bbox(pos, radius, slots) if n else [-SPAN / 2, -SPAN / 2, SPAN / 2, SPAN / 2],
        hub_keys=hub_keys,
        slots=slots,
        stats={},
    )
    db.add(layout)
    db.flush()
    layout.base_id = layout.id
    db.execute(delete(MapMaster))
    db.execute(delete(MapFrozen))
    db.execute(delete(MapLink))
    _insert(
        db,
        MapMaster,
        [
            {
                "object_id": masters.ids[at],
                "type_id": masters.type_ids[at],
                "x": float(pos[at, 0]),
                "y": float(pos[at, 1]),
                "placed_in": layout.id,
            }
            for at in range(n)
        ],
    )
    _freeze(db, counts, set(masters.index), layout.id)
    _insert(
        db,
        MapLink,
        [
            {"a_id": masters.ids[a], "b_id": masters.ids[b], "workspace_id": ws, "n": count}
            for a, b, ws, count in graph.links
        ],
    )
    _store_counts(db, counts, layout.id)
    points = refresh_points(db, hub_keys)
    layout.stats = {
        "masters": n,
        "hubs": int((hub_n > 0).sum()),
        "lone": len(lone),
        "direct_edges": graph.direct,
        "coref_pairs": graph.coref_all,
        "edges": len(graph.ea),
        "records": int(sum(row[3] for row in counts.rows)),
        "edge_seconds": round(edge_seconds, 2),
        "count_seconds": counts.seconds,
        "layout_seconds": round(layout_seconds, 2),
        "engine": stats,
        "overlap_rounds": fix["rounds"],
        "overlap_pairs": engine.overlapping(pos, radius),
        "moved": moved,
        "ring_radius": round(ring_r, 3),
        "links_by_workspace": split,
        "coref_share": round(coref.share, 4),
        "coref_pairs_estimated": coref.estimated_pairs,
        **points,
        "seconds": round(time.perf_counter() - started, 2),
    }
    progress("쓰기", 5, 5)
    return {"layout": layout.id, "kind": "full", **layout.stats}


def _warm_start(
    masters: Masters,
    connected: np.ndarray,
    known: np.ndarray,
    before: dict[uuid.UUID, tuple[float, float]],
    ea: np.ndarray,
    eb: np.ndarray,
) -> np.ndarray:
    """앞 판의 자리에서 출발 — 새 노드는 놓인 이웃들의 가운데(+ id 로 정한 작은 흔들림)."""
    m = len(connected)
    init = np.zeros((m, 2))
    for at in np.where(known)[0]:
        init[at] = before[masters.ids[connected[at]]]
    acc = np.zeros((m, 2))
    cnt = np.zeros(m)
    for a, b in ((ea, eb), (eb, ea)):
        hit = ~known[a] & known[b]
        np.add.at(acc, a[hit], init[b[hit]])
        np.add.at(cnt, a[hit], 1)
    center = init[known].mean(0)
    for at in np.where(~known)[0]:
        u, v = maphash.uv(maphash.hash_of(masters.ids[connected[at]]))
        jitter = SPAN * 0.004 * np.array([np.cos(2 * np.pi * v), np.sin(2 * np.pi * v)]) * u
        base = acc[at] / cnt[at] if cnt[at] else center
        init[at] = base + jitter
    return init


def _freeze(db: Session, counts: Counts, placed: set[uuid.UUID], layout_id: int) -> None:
    """원반 크기를 정할 수를 얼린다 — 판에 놓인 허브와 미지정만(판 밖 기준정보를 가리키는
    기록은 다음 덧붙임에서)."""
    _insert(
        db,
        MapFrozen,
        [
            {"hub_id": h, "type_id": t, "workspace_id": w, "n": n, "layout_id": layout_id}
            for h, t, w, n in counts.rows
            if h is None or h in placed
        ],
    )


# --- 매일 덧붙임 ---------------------------------------------------------------------


def run_append(db: Session, *, progress: Progress = _quiet) -> dict[str, Any]:
    """새 기준정보만 빈자리에 놓는다 — **기존 자리는 고정**(움직임 0). 판이 없으면 전체
    배치."""
    lock(db, wait=True)
    _cap_temp(db)
    started = time.perf_counter()
    base = current(db)
    if base is None:
        return run_full(db, user_id=None, progress=progress)
    master_types = maphubs.master_type_ids(db)
    refuse_if_too_many(db, master_types, [uuid.UUID(one) for one in base.hub_keys])
    progress("기준정보", 0, 4)
    masters = _masters(db, master_types)
    placed = {row.object_id: row for row in db.scalars(select(MapMaster))}
    fresh = [one for one in masters.ids if one not in placed]
    counts = count_records(db, base.hub_keys)
    if not fresh:
        # 새 기준정보가 없다 — 판은 그대로, 집계 · 점 표만 새로.
        _store_counts(db, counts, base.id)
        points = refresh_points(db, base.hub_keys)
        return {
            "layout": base.id,
            "kind": "append",
            "new": 0,
            "seconds": round(time.perf_counter() - started, 2),
            **points,
        }
    # 판에 있던 기준정보 + 새 것으로 노드를 세운다(지운 기준정보의 자리도 반발에 넣는다 — 그
    # 자리에 새 것이 겹치지 않게).
    gone = [one for one in placed if one not in masters.index]
    everyone = Masters(
        masters.ids + gone, masters.type_ids + [placed[one].type_id for one in gone]
    )
    n = len(everyone.ids)
    is_new = np.array([one not in placed for one in everyone.ids], dtype=bool)
    progress("선", 1, 4)
    records = [uuid.UUID(one) for one in base.hub_keys]
    direct = _direct_edges(db, everyone, master_types, set(fresh))
    split = bool(base.stats.get("links_by_workspace"))
    coref_rows = _coref(db, records, fresh, split=split).rows
    # 짝이 (새, 새) 면 두 번 온다(양쪽에서) — 반만 센다.
    halved: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None], int] = defaultdict(int)
    fresh_set = set(fresh)
    for a, b, ws, count in coref_rows:
        key = (min(a, b, key=str), max(a, b, key=str), ws)
        halved[key] += count
    coref_once = [
        (a, b, ws, count // 2 if (a in fresh_set and b in fresh_set) else count)
        for (a, b, ws), count in halved.items()
    ]
    graph = _graph(n, direct, coref_once, everyone)
    progress("배치", 2, 4)
    hub_n, unassigned = _frozen_totals(counts, everyone.index, n)
    pos = np.zeros((n, 2))
    for at, one in enumerate(everyone.ids):
        if one in placed:
            pos[at] = (placed[one].x, placed[one].y)
    deg = np.bincount(graph.ea, minlength=n) + np.bincount(graph.eb, minlength=n)
    acc = np.zeros((n, 2))
    cnt = np.zeros(n)
    for near, far in ((graph.ea, graph.eb), (graph.eb, graph.ea)):
        hit = is_new[near] & ~is_new[far]
        np.add.at(acc, near[hit], pos[far[hit]])
        np.add.at(cnt, near[hit], 1)
    bbox = base.bbox
    center = np.array([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2])
    old_pos = pos[~is_new]
    old_center = np.median(old_pos, axis=0) if len(old_pos) else center
    extent = (
        float(np.percentile(np.linalg.norm(old_pos - old_center, axis=1), 90))
        if len(old_pos)
        else SPAN * 0.3
    )
    lone_new = is_new & (deg == 0)
    linked_new = is_new & (deg > 0)
    for node in np.where(linked_new)[0]:
        u, v = maphash.uv(maphash.hash_of(everyone.ids[node]))
        jitter = SPAN * 0.004 * np.array([np.cos(2 * np.pi * v), np.sin(2 * np.pi * v)]) * u
        pos[node] = (acc[node] / cnt[node] if cnt[node] else old_center) + jitter
    ring_r = float(base.stats.get("ring_radius") or 0.0) or extent * 1.15
    lone_at = np.where(lone_new)[0]
    pos[lone_at] = _lone_ring([everyone.ids[one] for one in lone_at], old_center, ring_r)
    fixed = ~linked_new
    mass = _mass(n, graph.ea, graph.eb, hub_n)
    pos, stats = engine.layout(
        n,
        graph.ea,
        graph.eb,
        graph.ew,
        mass=mass,
        init=pos,
        iters=APPEND_ITERS,
        fixed=fixed,
        start_temp=APPEND_TEMP,
    )
    # 기존 원반은 판의 크기(얼린 수) 그대로 — 새 원반만 지금 수로.
    frozen_old = _frozen_hub_totals(db)
    radius = np.array(
        [
            base.scale * np.sqrt(frozen_old.get(one, 0) if one in placed else hub_n[at])
            for at, one in enumerate(everyone.ids)
        ]
    )
    pos, fix = engine.disc_overlap_fix(pos, radius, fixed=~is_new)
    slots = _slots(
        records,
        unassigned,
        _reserve(db, counts, records),
        base.scale,
        old_center,
        ring_r + float(radius.max()) * 2 + SPAN * 0.05,
        kept=base.slots,
    )
    progress("쓰기", 3, 4)
    layout = MapLayout(
        kind="append",
        base_id=base.base_id,
        created_by=None,
        scale=base.scale,
        bbox=_grow_bbox(base.bbox, pos[is_new], radius[is_new], slots),
        hub_keys=base.hub_keys,
        slots=slots,
        stats={},
    )
    db.add(layout)
    db.flush()
    new_at = [at for at in range(n) if is_new[at]]
    _insert(
        db,
        MapMaster,
        [
            {
                "object_id": everyone.ids[at],
                "type_id": everyone.type_ids[at],
                "x": float(pos[at, 0]),
                "y": float(pos[at, 1]),
                "placed_in": layout.id,
            }
            for at in new_at
        ],
    )
    fresh_ids = {everyone.ids[at] for at in new_at}
    _insert(
        db,
        MapFrozen,
        [
            {"hub_id": h, "type_id": t, "workspace_id": w, "n": c, "layout_id": layout.id}
            for h, t, w, c in counts.rows
            if h in fresh_ids
        ],
    )
    _insert(
        db,
        MapLink,
        [
            {
                "a_id": everyone.ids[a],
                "b_id": everyone.ids[b],
                "workspace_id": ws,
                "n": count,
            }
            for a, b, ws, count in graph.links
            if is_new[a] or is_new[b]
        ],
    )
    _store_counts(db, counts, layout.id)
    points = refresh_points(db, base.hub_keys)
    layout.stats = {
        **points,
        **{
            key: base.stats.get(key)
            for key in ("ring_radius", "links_by_workspace")
            if key in base.stats
        },
        "masters": len(masters.ids),
        "new": len(new_at),
        "new_hubs": int(sum(1 for at in new_at if hub_n[at] > 0)),
        "engine": stats,
        "overlap_rounds": fix["rounds"],
        "overlap_pairs": engine.overlapping(pos, radius),
        "seconds": round(time.perf_counter() - started, 2),
    }
    progress("쓰기", 4, 4)
    return {"layout": layout.id, "kind": "append", **layout.stats}


def _frozen_hub_totals(db: Session) -> dict[uuid.UUID, int]:
    return {
        hub: int(n)
        for hub, n in db.execute(
            select(MapFrozen.hub_id, func.sum(MapFrozen.n))
            .where(MapFrozen.hub_id.is_not(None))
            .group_by(MapFrozen.hub_id)
        )
    }


def _grow_bbox(
    bbox: list[float], pos: np.ndarray, radius: np.ndarray, slots: dict[str, list[float]]
) -> list[float]:
    """덧붙임은 틀을 넓히기만 한다(줄이면 화면의 고정 틀이 움찔한다)."""
    if not len(pos) and not slots:
        return bbox
    grown = (
        _bbox(pos, radius, slots) if len(pos) else _bbox(np.zeros((0, 2)), np.zeros(0), slots)
    )
    return [
        min(bbox[0], grown[0]),
        min(bbox[1], grown[1]),
        max(bbox[2], grown[2]),
        max(bbox[3], grown[3]),
    ]


# --- 타이머 -----------------------------------------------------------------------


#: 지도 작업의 종류 이름 — 화면 · 멈춘 까닭에 쓴다.
_KIND_LABEL = {
    "graph_map_full": "전체 배치",
    "graph_map_append": "덧붙임",
    "graph_map_count": "집계",
}


def _last_finished(db: Session) -> Any:
    """마지막으로 끝난 지도 작업(끝남 · 실패 · 취소) — 없으면 None."""
    from app.modules.jobs.models import Job

    return db.execute(
        select(Job.kind, Job.status, Job.finished_at, Job.created_at)
        .where(Job.kind.in_(KINDS), Job.status.in_(("done", "failed", "cancelled")))
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()


def paused(db: Session) -> str | None:
    """타이머가 지도 작업을 **넣지 않는** 까닭(ADR 0049) — 넣으면 None.

    - 설정으로 껐다(`GRAPH_MAP_AUTO=false`).
    - 마지막 전체 배치 · 덧붙임이 실패하거나 취소됐다 — 같은 일을 5분마다 다시 넣으면 큰
      설치에서 서버를 끝없이 버겁게 한다(운영의 고장 추적 DB: 강제 종료 → 판이 없음 → 다시
      넣음). 사람이 「전체 다시 배치」 를 눌러 끝나면 다시 돈다.

    집계의 실패는 멈추지 않는다 — `COUNT_RETRY` 뒤에 다시 본다(`schedule`)."""
    if not get_settings().graph_map_auto:
        return (
            "자동 계산이 비활성입니다(GRAPH_MAP_AUTO=false) — 시스템 관리자의 「전체 다시 "
            "배치」 만 돕니다."
        )
    last = _last_finished(db)
    if last is not None and last.status != "done" and last.kind != "graph_map_count":
        what = "실패했습니다" if last.status == "failed" else "취소됐습니다"
        label = _KIND_LABEL.get(last.kind, last.kind)
        return (
            f"지난 지도 작업({label})이 {what} — 같은 일을 다시 넣지 않게 자동 계산을 "
            "멈췄습니다. 시스템 관리자가 「전체 다시 배치」 로 다시 시작합니다."
        )
    return None


def schedule(db: Session, now: datetime | None = None) -> str | None:
    """**워커가 5분마다 부른다** — 차례가 된 지도 작업을 넣는다(넣었으면 그 종류). 처음 판 ·
    매일 밤 덧붙임 · 한 시간마다 집계. 지도 작업이 기다리거나 돌면 아무것도 안 넣는다(겹치지
    않게). 두 워커가 같은 때 봐도 하나만 넣는다(자문 잠금). 껐거나 지난 작업이 실패 ·
    취소됐으면 넣지 않는다(`paused`)."""
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    if not db.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": LOCK + ":schedule"}
    ):
        return None
    if db.scalar(
        select(Job.id).where(Job.kind.in_(KINDS), Job.status.in_(("queued", "running")))
    ):
        return None
    if paused(db) is not None:
        return None
    now = now or datetime.now(UTC)
    layout = current(db)
    kind: str | None = None
    if layout is None:
        masters = maphubs.master_type_ids(db)
        counted = typecounts.counts(db)
        if any(counted.get(one, 0) for one in masters):
            kind = "graph_map_full"
    else:
        since = now - layout.created_at
        night = now.astimezone().hour == get_settings().graph_map_night_hour
        counted_row = db.get(MapCounted, 1)
        if (night and since >= APPEND_EVERY) or since >= APPEND_MISSED:
            kind = "graph_map_append"
        elif (
            counted_row is None
            or counted_row.layout_id != layout.id
            or now - counted_row.counted_at >= COUNT_EVERY
        ):
            kind = "graph_map_count"
            last = _last_finished(db)
            if (
                last is not None
                and last.kind == "graph_map_count"
                and last.status != "done"
                and now - (last.finished_at or last.created_at) < COUNT_RETRY
            ):
                kind = None
    if kind is None:
        return None
    job_services.enqueue(
        db, kind=kind, params={"reason": "timer"}, user=None, workspace_id=None
    )
    db.commit()
    return kind
