"""밤 그래프 계산의 경로(ADR 0067) — 지금 판 · 지금 계산 · 순위 · 객체 하나 · 무리.

값은 **밤에 센 사본**이다. 응답마다 `run.computed_at`(언제 셌나)을 싣는다 — 그 뒤에 바뀐
관계는 안 들었다. 셈은 웹 워커가 하지 않는다(작업 `graph_calc` 를 워커가 돌린다).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.graph import calc
from app.modules.graph.calcschemas import (
    CalcClusterMembersOut,
    CalcClustersOut,
    CalcObjectOut,
    CalcStatusOut,
    CalcTopOut,
)
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs.schemas import JobOut
from app.shared.auth import current_user, require_system_admin

router = APIRouter(prefix="/graph/calc", tags=["graph"])


@router.get("", response_model=CalcStatusOut)
def calc_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Any:
    """지금 판(언제 · 엔진 · 수 · 단계별 시간) · 기다리는 계산 · 자동 계산이 멈췄나 · 설정.

    「GPU 가 보이나」 는 **이 웹 프로세스** 기준이다 — 셈은 워커가 하므로(워커만 `--nv`) 판의
    `engine` 이 실제로 무엇으로 셌는지다."""
    calc.require_visible(user)
    return calc.status(db)


@router.post("/run", response_model=JobOut, status_code=202)
def calc_run(
    user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> JobOut:
    """「지금 계산」(시스템 관리자) — 이미 기다리거나 돌면 **그 작업을 돌려준다**(겹쳐 돌지
    않는다). 멈췄던 자동 계산은 이것이 끝나면 다시 돈다."""
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    waiting = db.scalar(
        select(Job)
        .where(Job.kind == calc.KIND, Job.status.in_(("queued", "running")))
        .limit(1)
    )
    job = waiting or job_services.enqueue(
        db, kind=calc.KIND, params={"reason": "admin"}, user=user, workspace_id=None
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


@router.get("/top", response_model=CalcTopOut)
def calc_top(
    metric: str = Query(..., description="`depends_top`(기대는 수) · `bridge`(다리 역할)"),
    relation: str = Query("", description="기대는 수의 이어지는 관계 slug"),
    type: str | None = Query(None, description="이 타입만(slug)"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Any:
    """순위 — 「가장 많은 모델이 기대는 부품」 · 「다리 역할이 큰 것」."""
    return calc.top(
        db, user, metric=metric, relation=relation, type_slug=type, limit=limit, offset=offset
    )


@router.get("/object/{object_id}", response_model=CalcObjectOut)
def calc_object(
    object_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Any:
    """객체 하나 — 지표(순위 · 몇 개 중) · 무리 · 비슷한 것 위 10."""
    return calc.object_metrics(db, user, object_id)


@router.get("/clusters", response_model=CalcClustersOut)
def calc_clusters(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Any:
    """무리 목록 — 큰 것부터."""
    return calc.clusters(db, user, limit=limit, offset=offset)


@router.get("/clusters/{cluster}", response_model=CalcClusterMembersOut)
def calc_cluster(
    cluster: int,
    type: str | None = Query(None, description="이 타입만(slug)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Any:
    """무리 하나의 객체 — 무리 안에서 이음이 많은 것부터."""
    return calc.cluster_members(db, user, cluster, type_slug=type, limit=limit, offset=offset)
