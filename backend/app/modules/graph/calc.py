"""밤 그래프 계산 — **세기 · 쓰기 · 넣기 · 읽기**(ADR 0067).

세기는 `calcengine`(CPU · GPU), 그래프 읽기는 `calcload` 가 한다. 여기서는 판을 만들고
(한 트랜잭션 — 커밋할 때 새 판이 한꺼번에 「지금」 이 된다), 앞 판을 지우고, 타이머가 넣을
차례를 보고, 화면 · MCP 가 읽을 모양으로 낸다.

**보이는 범위** — 셈은 그래프 전체로 한다. 조회가 부서로 좁혀진 설치
(`VIEW_SCOPE=department`)에서는 모든 부서를 보는 사람에게만 보인다 — 「기대는 수 4,856」 이
남의 부서 모델의 수를 드러낸다(ADR 0034 · 지표 결론과 같은 규칙).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.graph import calcengine, calcload
from app.modules.graph.calcengine import Ints
from app.modules.graph.models import (
    GraphCalcCluster,
    GraphCalcRun,
    GraphCalcScore,
    GraphCalcSimilar,
)
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared.errors import Forbidden, NotFound, code
from app.shared.permissions import sees_every_department, visible_owner_clause

log = logging.getLogger(__name__)

KIND = "graph_calc"
LOCK = "graph-calc"
#: 밤에 지난 판에서 이만큼 지났으면 넣는다. 밤을 놓쳤으면(서버가 꺼져 있었다) `MISSED` 뒤
#: 아무 때나.
EVERY = timedelta(hours=20)
MISSED = timedelta(hours=30)
METRICS = ("depends_top", "bridge", "cluster")

Progress = Callable[[str, int, int], None]


def _quiet(_stage: str, _done: int, _total: int) -> None:
    return None


# ── 세기 · 쓰기 ─────────────────────────────────────────────────────────────────────────


def _ranks(values: np.ndarray, keep: np.ndarray) -> tuple[Ints, Ints]:
    """(노드, 순위) — `keep` 인 노드를 값이 큰 것부터, 같으면 번호(id 차례)가 작은 것부터."""
    nodes = np.flatnonzero(keep).astype(np.int64)
    order = np.lexsort((nodes, -values[nodes]))
    return nodes[order], np.arange(1, len(nodes) + 1, dtype=np.int64)


def _copy(db: Session, table: str, columns: tuple[str, ...], rows: Any) -> int:
    """COPY 로 넣는다 — 같은 트랜잭션(작업의 트랜잭션)이다. 넣은 줄 수."""
    raw = db.connection().connection.driver_connection
    assert raw is not None
    count = 0
    with raw.cursor() as cursor:
        sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
        with cursor.copy(sql) as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
    return count


def _scores(
    run_id: int,
    metric: str,
    relation: str,
    ids: list[uuid.UUID],
    nodes: Ints,
    values: Any,
    ranks: Ints,
) -> Any:
    for node, rank in zip(nodes.tolist(), ranks.tolist(), strict=True):
        yield (run_id, metric, relation, ids[node], float(values[node]), rank)


def _stable_clusters(
    db: Session, previous: int | None, ids: list[uuid.UUID], cluster: Ints
) -> Ints:
    """무리 번호를 **앞 판과 이어 준다** — 새 무리와 앞 무리가 서로 반 넘게 겹치면 앞 번호를
    받는다. 나머지는 앞 판에 없던 번호(앞 판의 가장 큰 번호 다음부터). 「어제 군집 7」 이 오늘
    군집 3 이 되면 저장한 물음 · 대화가 다 틀어진다."""
    if previous is None or not len(cluster) or cluster.max() < 0:
        return cluster
    seat = {one: at for at, one in enumerate(ids)}
    old = np.full(len(ids), -1, np.int64)
    for object_id, value in db.execute(
        select(GraphCalcScore.object_id, GraphCalcScore.value).where(
            GraphCalcScore.run_id == previous, GraphCalcScore.metric == "cluster"
        )
    ):
        at = seat.get(object_id)
        if at is not None:
            old[at] = int(value)
    both = (cluster >= 0) & (old >= 0)
    new_size = np.bincount(cluster[cluster >= 0])
    old_ids, old_counts = np.unique(old[old >= 0], return_counts=True)
    old_size = dict(zip(old_ids.tolist(), old_counts.tolist(), strict=True))
    pairs, overlap = np.unique(
        np.stack([cluster[both], old[both]], axis=1), axis=0, return_counts=True
    )
    mapping: dict[int, int] = {}
    taken: set[int] = set()
    for (new_c, old_c), shared in sorted(
        zip(pairs.tolist(), overlap.tolist(), strict=True), key=lambda one: -one[1]
    ):
        if new_c in mapping or old_c in taken:
            continue
        if shared * 2 > new_size[new_c] and shared * 2 > old_size[old_c]:
            mapping[new_c] = old_c
            taken.add(old_c)
    nxt = (max(old_size) + 1) if old_size else 0
    out = np.full(len(cluster), -1, np.int64)
    for new_c in range(len(new_size)):
        if new_c not in mapping:
            mapping[new_c] = nxt
            nxt += 1
    linked = cluster >= 0
    out[linked] = np.asarray([mapping[c] for c in cluster[linked].tolist()], np.int64)
    return out


def run(
    db: Session, *, user_id: uuid.UUID | None = None, progress: Progress = _quiet
) -> dict[str, Any]:
    """판 하나 — 그래프를 읽고, 넷을 세고, 표에 쓰고, 앞 판을 지운다. **커밋하지
    않는다**(작업이 끝에서 한 번 — 그때 새 판이 한꺼번에 보인다)."""
    settings = get_settings()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK})
    started = time.monotonic()
    engine = calcengine.make(settings.graph_calc_engine, settings.graph_calc_threads)
    seen = calcengine.availability()
    previous = current_id(db)
    run_row = GraphCalcRun(engine=engine.name, status="running", created_by=user_id, stats={})
    db.add(run_row)
    db.flush()
    seconds: dict[str, float] = {}
    progress("그래프 읽기", 0, 4)
    mark = time.monotonic()
    loaded = calcload.load(db, whole=settings.graph_calc_whole_relations.split(","))
    seconds["load"] = round(time.monotonic() - mark, 2)
    n = loaded.n
    stats: dict[str, Any] = {
        "engine": engine.name,
        "gpu_name": seen.gpu_name if engine.name == "gpu" else None,
        "nodes": n,
        "pairs": len(loaded.a),
        # 무리 · 다리 역할을 지은 선 — 비면 전부(`GRAPH_CALC_WHOLE_RELATIONS`).
        "whole_relations": loaded.whole,
        "relations": {},
        "seconds": seconds,
        "skipped": {},
    }
    ids = loaded.ids

    progress("기대는 수 · 비슷한 것", 1, 4)
    for slug, (src, dst) in sorted(loaded.transitive.items()):
        mark = time.monotonic()
        result = engine.transitive(n, src, dst, loaded.type_index)
        nodes, ranks = _ranks(result.depends_top.astype(np.float64), result.depends_top > 0)
        wrote = _copy(
            db,
            "graph_calc_scores",
            ("run_id", "metric", "relation", "object_id", "value", "rank"),
            _scores(run_row.id, "depends_top", slug, ids, nodes, result.depends_top, ranks),
        )
        similar = _copy(
            db,
            "graph_calc_similar",
            ("run_id", "relation", "object_id", "rank", "other_id", "score"),
            (
                (run_row.id, slug, ids[a], r, ids[b], float(s))
                for a, b, s, r in zip(
                    result.similar_node.tolist(),
                    result.similar_other.tolist(),
                    result.similar_score.tolist(),
                    result.similar_rank.tolist(),
                    strict=True,
                )
            ),
        )
        stats["relations"][slug] = {
            "edges": len(src),
            "roots": len(result.roots),
            "depends": wrote,
            "similar": similar,
            "similar_skipped": result.similar_skipped,
            "reach_cells": result.reach_cells,
            "seconds": round(time.monotonic() - mark, 2),
        }

    progress("군집 · 다리 역할", 2, 4)
    mark = time.monotonic()
    whole = engine.whole(n, loaded.a, loaded.b)
    seconds["whole"] = round(time.monotonic() - mark, 2)
    stats["skipped"].update(whole.skipped)
    degree = (
        np.bincount(np.concatenate([loaded.a, loaded.b]), minlength=n) if n else np.zeros(0)
    )

    progress("쓰기", 3, 4)
    mark = time.monotonic()
    if whole.cluster is not None:
        cluster = _stable_clusters(db, previous, ids, whole.cluster)
        _write_clusters(db, run_row.id, loaded, cluster, degree)
        stats["clusters"] = len(np.unique(cluster[cluster >= 0]))
        stats["modularity"] = None if whole.modularity is None else round(whole.modularity, 4)
    if whole.bridge is not None:
        nodes, ranks = _ranks(whole.bridge, whole.bridge > 0)
        stats["bridge"] = _copy(
            db,
            "graph_calc_scores",
            ("run_id", "metric", "relation", "object_id", "value", "rank"),
            _scores(run_row.id, "bridge", "", ids, nodes, whole.bridge, ranks),
        )
    seconds["write"] = round(time.monotonic() - mark, 2)
    seconds["total"] = round(time.monotonic() - started, 2)
    run_row.status = "done"
    run_row.finished_at = datetime.now(UTC)
    run_row.stats = stats
    # 앞 판은 지운다 — 이 트랜잭션이 커밋되기 전까지 읽는 쪽은 앞 판을 본다.
    db.execute(delete(GraphCalcRun).where(GraphCalcRun.id != run_row.id))
    progress("끝", 4, 4)
    log.info("밤 그래프 계산 판 %s: %s", run_row.id, stats)
    return {"run": run_row.id, **stats}


def _write_clusters(
    db: Session, run_id: int, loaded: calcload.Loaded, cluster: Ints, degree: np.ndarray
) -> None:
    """무리마다 한 줄(크기 · 대표 · 타입별 수) + 객체마다 무리(`cluster` 지표 — 순위는 무리 안
    차례, 이음이 많은 것부터)."""
    ids = loaded.ids
    linked = np.flatnonzero(cluster >= 0).astype(np.int64)
    if not len(linked):
        return
    order = linked[np.lexsort((linked, -degree[linked], cluster[linked]))]
    groups = cluster[order]
    starts = np.flatnonzero(np.r_[True, groups[1:] != groups[:-1]])
    ends = np.r_[starts[1:], len(order)]
    ranks = np.zeros(len(order), np.int64)
    for s, e in zip(starts.tolist(), ends.tolist(), strict=True):
        ranks[s:e] = np.arange(1, e - s + 1)
    _copy(
        db,
        "graph_calc_scores",
        ("run_id", "metric", "relation", "object_id", "value", "rank"),
        (
            (run_id, "cluster", "", ids[node], float(cluster[node]), rank)
            for node, rank in zip(order.tolist(), ranks.tolist(), strict=True)
        ),
    )
    heads = order[starts]
    labels: dict[uuid.UUID, str] = dict(
        db.execute(
            select(ObjectInstance.id, ObjectInstance.label).where(
                ObjectInstance.id.in_([ids[h] for h in heads.tolist()])
            )
        )
        .tuples()
        .all()
    )
    slugs = [row.slug for row in loaded.types]
    rows = []
    for s, e, head in zip(starts.tolist(), ends.tolist(), heads.tolist(), strict=True):
        kinds, counts = np.unique(loaded.type_index[order[s:e]], return_counts=True)
        types = {
            slugs[k]: int(c) for k, c in zip(kinds.tolist(), counts.tolist(), strict=True)
        }
        rows.append(
            (
                run_id,
                int(cluster[head]),
                e - s,
                ids[head],
                (labels.get(ids[head]) or "")[:300],
                types,
            )
        )
    raw = db.connection().connection.driver_connection
    assert raw is not None
    from psycopg.types.json import Jsonb

    with (
        raw.cursor() as cursor,
        cursor.copy(
            "COPY graph_calc_clusters "
            "(run_id, cluster, size, representative_id, label, types) FROM STDIN"
        ) as copy,
    ):
        for row in rows:
            copy.write_row((*row[:5], Jsonb(row[5])))


# ── 넣기 ────────────────────────────────────────────────────────────────────────────────


def _last_finished(db: Session) -> Any:
    from app.modules.jobs.models import Job

    return db.execute(
        select(Job.status, Job.finished_at, Job.created_at)
        .where(Job.kind == KIND, Job.status.in_(("done", "failed", "cancelled")))
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()


def paused(db: Session) -> str | None:
    """타이머가 넣지 **않는** 까닭 — 넣으면 None. 껐거나, 지난 계산이 실패 · 취소됐다(같은 일을
    밤마다 다시 넣지 않는다 — 사람이 「지금 계산」 을 눌러 끝나면 다시 돈다, ADR 0049)."""
    if not get_settings().graph_calc_auto:
        return (
            "자동 계산이 비활성입니다(GRAPH_CALC_AUTO=false) — 시스템 관리자의 "
            "「지금 계산」 만 돕니다."
        )
    last = _last_finished(db)
    if last is not None and last.status != "done":
        what = "실패했습니다" if last.status == "failed" else "취소됐습니다"
        return (
            f"지난 그래프 계산이 {what} — 같은 일을 다시 넣지 않게 자동 계산을 멈췄습니다. "
            "시스템 관리자가 「지금 계산」 으로 다시 시작합니다."
        )
    return None


def schedule(db: Session, now: datetime | None = None) -> str | None:
    """**워커가 5분마다 부른다** — 차례면 계산을 넣는다(넣었으면 종류 이름). 처음 판(기준정보가
    있고 판이 없으면) · 매일 밤. 기다리거나 돌면 안 넣는다. 두 워커가 같은 때 봐도 하나만."""
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    if not db.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": LOCK + ":schedule"}
    ):
        return None
    if db.scalar(
        select(Job.id).where(Job.kind == KIND, Job.status.in_(("queued", "running")))
    ):
        return None
    if paused(db) is not None:
        return None
    now = now or datetime.now(UTC)
    last = current(db)
    if last is None:
        # 처음 판 — 기준정보가 하나라도 있으면(타입별 계수표로 본다 — 객체 표를 세지 않는다).
        from app.modules.graph import maphubs
        from app.modules.objects import typecounts

        counted = typecounts.counts(db)
        due = any(counted.get(one, 0) for one in maphubs.master_type_ids(db))
    else:
        since = now - (last.finished_at or last.created_at)
        night = now.astimezone().hour == get_settings().graph_calc_night_hour
        due = (night and since >= EVERY) or since >= MISSED
    if not due:
        return None
    job_services.enqueue(
        db, kind=KIND, params={"reason": "timer"}, user=None, workspace_id=None
    )
    db.commit()
    return KIND


# ── 읽기 ────────────────────────────────────────────────────────────────────────────────


def require_visible(user: User) -> None:
    """부서로 좁힌 설치에서는 모든 부서를 보는 사람에게만."""
    if not sees_every_department(user):
        raise Forbidden(
            code("GRAPH", 40),
            "그래프 계산 값은 모든 부서를 보는 사람에게만 보입니다 — 수가 남의 부서 객체를 "
            "드러냅니다(조회가 부서로 좁혀진 설치).",
        )


def current(db: Session) -> GraphCalcRun | None:
    return db.scalar(
        select(GraphCalcRun)
        .where(GraphCalcRun.status == "done")
        .order_by(GraphCalcRun.id.desc())
        .limit(1)
    )


def current_id(db: Session) -> int | None:
    found = current(db)
    return found.id if found else None


def _run_out(found: GraphCalcRun | None) -> dict[str, Any] | None:
    if found is None:
        return None
    return {
        "id": found.id,
        "computed_at": found.finished_at,
        "engine": found.engine,
        "stats": found.stats or {},
    }


def status(db: Session) -> dict[str, Any]:
    """지금 판 · 엔진이 보이나(이 프로세스 기준 — 웹은 GPU 를 안 열어 워커와 다를 수 있다) ·
    타이머가 멈췄나 · 설정."""
    from app.modules.jobs.models import Job

    settings = get_settings()
    seen = calcengine.availability()
    pending = db.execute(
        select(Job.id, Job.status)
        .where(Job.kind == KIND, Job.status.in_(("queued", "running")))
        .limit(1)
    ).first()
    return {
        "run": _run_out(current(db)),
        "pending": {"id": pending.id, "status": pending.status} if pending else None,
        "paused": paused(db),
        "engine_setting": settings.graph_calc_engine,
        "night_hour": settings.graph_calc_night_hour,
        "gpu_visible": seen.gpu,
        "gpu_name": seen.gpu_name,
        "gpu_reason": seen.reason,
        "networkit": seen.networkit,
    }


def _alive(user: User) -> list[Any]:
    return [
        ObjectInstance.deleted_at.is_(None),
        ObjectInstance.merged_into_id.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    ]


def _brief(row: Any) -> dict[str, Any]:
    return {"id": row.id, "label": row.label, "key": row.key, "type_slug": row.slug}


def _objects() -> Select[Any]:
    return select(
        ObjectInstance.id, ObjectInstance.label, ObjectInstance.key, ObjectType.slug
    ).join(ObjectType, ObjectType.id == ObjectInstance.type_id)


def top(
    db: Session,
    user: User,
    *,
    metric: str,
    relation: str = "",
    type_slug: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """순위 — 「가장 많은 모델이 기대는 부품」(`depends_top`) · 「다리 역할이 큰 것」
    (`bridge`)."""
    require_visible(user)
    if metric not in ("depends_top", "bridge"):
        raise NotFound(code("GRAPH", 41), f"순위를 낼 수 없는 지표입니다: {metric}")
    found = current(db)
    if found is None:
        return {"run": None, "total": 0, "items": []}
    if metric == "depends_top" and relation not in (found.stats or {}).get("relations", {}):
        known = ", ".join(sorted((found.stats or {}).get("relations", {}))) or "없음"
        raise NotFound(
            code("GRAPH", 42),
            f"이어지는(transitive) 관계 종류가 아닙니다: {relation or '(비움)'} — "
            f"센 관계: {known}",
        )
    if metric == "bridge":
        relation = ""
    stmt = (
        _objects()
        .add_columns(GraphCalcScore.value, GraphCalcScore.rank)
        .join(GraphCalcScore, GraphCalcScore.object_id == ObjectInstance.id)
        .where(
            GraphCalcScore.run_id == found.id,
            GraphCalcScore.metric == metric,
            GraphCalcScore.relation == relation,
            *_alive(user),
        )
    )
    if type_slug:
        stmt = stmt.where(ObjectType.slug == type_slug)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(GraphCalcScore.rank).limit(limit).offset(offset)).all()
    return {
        "run": _run_out(found),
        "total": int(total),
        "items": [{**_brief(row), "value": row.value, "rank": row.rank} for row in rows],
    }


def object_metrics(db: Session, user: User, object_id: uuid.UUID) -> dict[str, Any]:
    """객체 하나 — 지표 전부(순위 · 몇 개 중) · 무리 · 비슷한 것 위 10(관계마다)."""
    require_visible(user)
    found = current(db)
    if found is None:
        return {"run": None, "scores": [], "cluster": None, "similar": []}
    totals = dict(
        (
            (row.metric, row.relation),
            int(row.n),
        )
        for row in db.execute(
            select(GraphCalcScore.metric, GraphCalcScore.relation, func.count().label("n"))
            .where(GraphCalcScore.run_id == found.id, GraphCalcScore.metric != "cluster")
            .group_by(GraphCalcScore.metric, GraphCalcScore.relation)
        )
    )
    scores = []
    cluster_out: dict[str, Any] | None = None
    for row in db.execute(
        select(GraphCalcScore).where(
            GraphCalcScore.run_id == found.id, GraphCalcScore.object_id == object_id
        )
    ).scalars():
        if row.metric == "cluster":
            head = db.get(GraphCalcCluster, (found.id, int(row.value)))
            cluster_out = {
                "cluster": int(row.value),
                "rank": row.rank,
                "size": head.size if head else None,
                "label": head.label if head else None,
                "representative_id": head.representative_id if head else None,
                "types": head.types if head else {},
            }
            continue
        total = totals.get((row.metric, row.relation), 0)
        scores.append(
            {
                "metric": row.metric,
                "relation": row.relation,
                "value": row.value,
                "rank": row.rank,
                "of": total,
            }
        )
    other = select(GraphCalcSimilar).where(
        GraphCalcSimilar.run_id == found.id, GraphCalcSimilar.object_id == object_id
    )
    pairs = list(db.scalars(other.order_by(GraphCalcSimilar.relation, GraphCalcSimilar.rank)))
    briefs = {
        row.id: _brief(row)
        for row in db.execute(
            _objects().where(
                ObjectInstance.id.in_([one.other_id for one in pairs]), *_alive(user)
            )
        )
    }
    similar = [
        {
            **briefs[one.other_id],
            "relation": one.relation,
            "rank": one.rank,
            "score": one.score,
        }
        for one in pairs
        if one.other_id in briefs
    ]
    return {
        "run": _run_out(found),
        "scores": sorted(scores, key=lambda one: (one["metric"], one["relation"])),
        "cluster": cluster_out,
        "similar": similar,
    }


def clusters(db: Session, user: User, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """무리 목록 — 큰 것부터. 대표의 이름은 셀 때의 것(대표가 지워져도 이름이 남는다)."""
    require_visible(user)
    found = current(db)
    if found is None:
        return {"run": None, "total": 0, "items": []}
    base = select(GraphCalcCluster).where(GraphCalcCluster.run_id == found.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(GraphCalcCluster.size.desc(), GraphCalcCluster.cluster)
        .limit(limit)
        .offset(offset)
    )
    return {
        "run": _run_out(found),
        "total": int(total),
        "items": [
            {
                "cluster": row.cluster,
                "size": row.size,
                "label": row.label,
                "representative_id": row.representative_id,
                "types": row.types,
            }
            for row in rows
        ],
    }


def cluster_members(
    db: Session,
    user: User,
    cluster: int,
    *,
    type_slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """무리 하나의 객체 — 무리 안에서 이음이 많은 것부터."""
    require_visible(user)
    found = current(db)
    if found is None:
        return {"run": None, "cluster": None, "total": 0, "items": []}
    head = db.get(GraphCalcCluster, (found.id, cluster))
    if head is None:
        raise NotFound(code("GRAPH", 43), f"지금 판에 그런 무리가 없습니다: {cluster}")
    stmt = (
        _objects()
        .add_columns(GraphCalcScore.rank)
        .join(GraphCalcScore, GraphCalcScore.object_id == ObjectInstance.id)
        .where(
            GraphCalcScore.run_id == found.id,
            GraphCalcScore.metric == "cluster",
            GraphCalcScore.relation == "",
            GraphCalcScore.value == float(cluster),
            *_alive(user),
        )
    )
    if type_slug:
        stmt = stmt.where(ObjectType.slug == type_slug)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(GraphCalcScore.rank).limit(limit).offset(offset)).all()
    return {
        "run": _run_out(found),
        "cluster": {
            "cluster": head.cluster,
            "size": head.size,
            "label": head.label,
            "representative_id": head.representative_id,
            "types": head.types,
        },
        "total": int(total),
        "items": [{**_brief(row), "rank": row.rank} for row in rows],
    }
