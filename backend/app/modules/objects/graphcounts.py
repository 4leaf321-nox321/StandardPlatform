"""정의 그래프의 선 굵기 — **찍어 둔 값**(ADR 0038).

그래프 첫 화면(`/api/graph/overview`)은 타입 사이에 실제로 걸린 관계 · 참조 칸 · 원 표
링크의 수를 선 굵기로 그린다. 그것을 열 때마다 관계 표 · 참조 색인 · 링크 표 전부를 객체 표와
양 끝으로 이어 셌다 — 운영에서 3분 45초 ~ 4분 28초(2026-10-26). 선 굵기는 전체 모양을 보는
것이라 몇십 분 낡아도 뜻이 같다. 그래서 작업이 세어 한 줄에 찍어 두고, 화면은 그것을 기준
시각과 함께 바로 그린다. 없거나 낡았으면(`STALE_AFTER`) 열 때 다시 세는 작업을 넣는다(같은
작업이 기다리거나 도는 중이면 그것).

부서로 조회를 좁힌 설치(`VIEW_SCOPE=department`)는 사람마다 보이는 수가 달라 찍어 둔 값을 못
쓴다 — 그 설치는 지금처럼 그 자리에서 센다. 찍는 눈은 시스템 관리자(전부를 본다).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs.models import Job
from app.modules.objects import graph, refedges, system
from app.modules.objects.models import GraphEdgeSnapshot
from app.modules.ontology.models import ObjectType
from app.shared import system_sources
from app.shared.errors import AppError, code

JOB_KIND = "graph_edge_counts"
#: 이보다 오래된 값이면 열 때 다시 세는 작업을 넣는다(그동안은 그 값을 그대로 보인다).
STALE_AFTER = timedelta(hours=1)
_ROW = 1


@dataclass
class Snapshot:
    counted_at: datetime
    seconds: float
    relations: list[graph.TypeEdge]
    refs: refedges.RefCounts
    links: list[graph.TypeLinkEdge]


def read(db: Session) -> Snapshot | None:
    row = db.get(GraphEdgeSnapshot, _ROW)
    if row is None:
        return None
    return Snapshot(
        counted_at=row.counted_at,
        seconds=row.seconds,
        relations=[
            graph.TypeEdge(rel, uuid.UUID(src), uuid.UUID(dst), int(n))
            for rel, src, dst, n in row.relations or []
        ],
        refs={
            (uuid.UUID(src), key, uuid.UUID(dst)): int(n)
            for src, key, dst, n in row.refs or []
        },
        links=[
            graph.TypeLinkEdge(rel, src, dst, int(n)) for rel, src, dst, n in row.links or []
        ],
    )


def stale(snapshot: Snapshot | None, now: datetime | None = None) -> bool:
    if snapshot is None:
        return True
    return (now or datetime.now(UTC)) - snapshot.counted_at > STALE_AFTER


def request(db: Session) -> Job:
    """다시 세는 작업 — 기다리거나 도는 것이 있으면 그것. **부르는 쪽이 커밋한다.**"""
    from app.modules.jobs import services as job_services

    waiting = db.scalar(
        select(Job).where(Job.kind == JOB_KIND, Job.status.in_(("queued", "running")))
    )
    if waiting is not None:
        return waiting
    return job_services.enqueue(db, kind=JOB_KIND, params={}, user=None, workspace_id=None)


def _viewer(db: Session) -> User | None:
    """세는 눈 — 처음 만든 활성 시스템 관리자(전부를 본다)."""
    return db.scalar(
        select(User)
        .where(User.is_system_admin.is_(True), User.status == "active")
        .order_by(User.created_at)
        .limit(1)
    )


def take(db: Session) -> Snapshot:
    """세어 찍는다 — 관계 · 참조 칸 · 원 표 링크. **부르는 쪽이 커밋한다**(작업이면 워커)."""
    viewer = _viewer(db)
    if viewer is None:
        raise AppError(code("GRAPH", 30), "셀 눈(활성 시스템 관리자)이 없습니다.", status=409)
    started = monotonic()
    systems = [
        row.slug
        for row in db.scalars(select(ObjectType).where(ObjectType.is_active.is_(True)))
        if system.is_system(row) and system_sources.system_source(row.system_source)
    ]
    relations = graph.type_edge_counts(db, user=viewer)
    refs = refedges.ref_counts(db, user=viewer)
    links = graph.type_link_counts(db, user=viewer, systems=systems)
    snapshot = Snapshot(
        counted_at=datetime.now(UTC),
        seconds=round(monotonic() - started, 1),
        relations=relations,
        refs=refs,
        links=links,
    )
    row = db.get(GraphEdgeSnapshot, _ROW) or GraphEdgeSnapshot(id=_ROW)
    row.counted_at = snapshot.counted_at
    row.seconds = snapshot.seconds
    row.relations = [
        [one.relation, str(one.src_type_id), str(one.dst_type_id), one.count]
        for one in relations
    ]
    row.refs = [[str(src), key, str(dst), n] for (src, key, dst), n in refs.items()]
    row.links = [
        [one.relation, one.src_type_slug, one.dst_type_slug, one.count] for one in links
    ]
    db.add(row)
    return snapshot
