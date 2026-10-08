"""지표 — 정의(시스템 관리자) · 계획 · 읽기(로그인한 누구나, 보이는 것만) · 다시 계산."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs.schemas import JobOut
from app.modules.metrics import alert_routes, compute, params, query, services
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef, MetricRun
from app.modules.metrics.recipes import routes as recipes_routes
from app.modules.metrics.schemas import (
    CohortOut,
    DimValuesOut,
    HomePinIn,
    HomePinOut,
    MetricIn,
    MetricOut,
    MetricPatch,
    MetricRunOut,
    MetricSavedOut,
    PlanIn,
    PlanOut,
    SeriesOut,
    TableOut,
)
from app.modules.workspaces.models import Workspace
from app.shared.auth import current_user, require_system_admin
from app.shared.permissions import workspace_by_slug

router = APIRouter(prefix="/metrics", tags=["metrics"])

RECENT_RUNS = 30


def _built(db: Session, metric: MetricDef) -> spec_module.Built:
    return compute.built_of(db, metric)


@router.get("", response_model=list[MetricOut])
def list_metrics(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MetricOut]:
    """정의 전부 — 값은 보이는 것만 더하므로 정의 자체는 누구나 본다."""
    rows = db.scalars(select(MetricDef).order_by(MetricDef.label, MetricDef.slug))
    return [services.out(db, one) for one in rows]


@router.post("/plan", response_model=PlanOut)
def plan_metric(
    payload: PlanIn, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> PlanOut:
    """저장하지 않고 **지어만 본다** — 오류 전부 · 경고 · 어림한 셀 수 · 기준의 종류."""
    return services.plan(db, payload)


@router.post("", response_model=MetricSavedOut, status_code=201)
def create_metric(
    payload: MetricIn,
    recompute: bool = Query(default=False, description="저장하자마자 계산 작업을 넣는다"),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> MetricSavedOut:
    row = services.create(db, user, payload)
    job = (
        services.enqueue_recompute(db, row, user=user, reason="defined") if recompute else None
    )
    db.commit()
    db.refresh(row)
    if job is not None:
        db.refresh(job)
    return MetricSavedOut(
        metric=services.out(db, row), job=jobs_routes._out(db, job) if job else None
    )


@router.get("/{slug}", response_model=MetricOut)
def get_metric(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> MetricOut:
    return services.out(db, services.get(db, slug))


@router.patch("/{slug}", response_model=MetricSavedOut)
def update_metric(
    slug: str,
    payload: MetricPatch,
    recompute: bool = Query(default=False),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> MetricSavedOut:
    row = services.update(db, user, services.get(db, slug), payload)
    job = (
        services.enqueue_recompute(db, row, user=user, reason="updated") if recompute else None
    )
    db.commit()
    db.refresh(row)
    if job is not None:
        db.refresh(job)
    return MetricSavedOut(
        metric=services.out(db, row), job=jobs_routes._out(db, job) if job else None
    )


@router.delete("/{slug}", status_code=204)
def delete_metric(
    slug: str, user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> None:
    services.delete(db, user, services.get(db, slug))
    db.commit()


@router.get("/{slug}/home", response_model=list[HomePinOut])
def metric_home_pins(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[HomePinOut]:
    """이 지표가 올라간 부서 홈들."""
    pins = services.home_pins(db, services.get(db, slug))
    spaces = {
        one.id: one
        for one in db.scalars(
            select(Workspace).where(Workspace.id.in_([pin.workspace_id for pin in pins]))
        )
    }
    return [
        HomePinOut(
            id=pin.id,
            workspace_slug=spaces[pin.workspace_id].slug,
            workspace_name=spaces[pin.workspace_id].name,
            split=pin.split,
            home_order=pin.home_order,
        )
        for pin in sorted(pins, key=lambda one: spaces[one.workspace_id].name)
    ]


@router.put("/{slug}/home", response_model=HomePinOut)
def pin_metric_home(
    slug: str,
    payload: HomePinIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> HomePinOut:
    """부서 홈에 올린다 — **그 부서의 관리자만**(뷰를 홈에 올리는 것과 같다). 홈에서는 지표의
    추이가 그림으로 서고, 누르면 지표 화면으로 간다."""
    workspace = workspace_by_slug(db, payload.workspace_slug)
    pin = services.pin_home(
        db,
        user,
        services.get(db, slug),
        workspace=workspace,
        split=payload.split,
        position=payload.position,
    )
    db.commit()
    return HomePinOut(
        id=pin.id,
        workspace_slug=workspace.slug,
        workspace_name=workspace.name,
        split=pin.split,
        home_order=pin.home_order,
    )


@router.delete("/{slug}/home", status_code=204)
def unpin_metric_home(
    slug: str,
    workspace: str = Query(description="부서 slug"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.unpin_home(
        db, user, services.get(db, slug), workspace=workspace_by_slug(db, workspace)
    )
    db.commit()


@router.post("/{slug}/recompute", response_model=JobOut, status_code=202)
def recompute_metric(
    slug: str, user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> JobOut:
    """다시 센다 — **작업이 된다**(202). 같은 지표의 작업이 줄에 있으면 그것을 돌려준다."""
    row = services.get(db, slug)
    job = services.enqueue_recompute(db, row, user=user, reason="manual")
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


@router.get("/{slug}/runs", response_model=list[MetricRunOut])
def list_runs(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MetricRunOut]:
    row = services.get(db, slug)
    runs = db.scalars(
        select(MetricRun)
        .where(MetricRun.metric_id == row.id)
        .order_by(MetricRun.started_at.desc(), MetricRun.id.desc())
        .limit(RECENT_RUNS)
    )
    return [MetricRunOut.model_validate(one) for one in runs]


@router.get("/{slug}/values", response_model=TableOut)
def metric_values(
    slug: str,
    request: Request,
    dims: str | None = Query(default=None, description="묶을 기준 이름들, 쉼표로"),
    by: str | None = Query(
        default=None, description="period · cohort · age 중 묶을 것, 쉼표로"
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TableOut:
    """표 — 요청한 기준(과 기간 · 코호트)별 셀. 기준 값으로 거르기는 `d.<기준>=<값>`(빈 값은
    「(비어 있음)」). 셀마다 비율 · 닫힘 · **건 보기 조건**(`drill`)이 붙는다."""
    row = services.get(db, slug)
    built = _built(db, row)
    ask = params.ask_from_request(
        request,
        dims=dims,
        by=by,
        period_from=period_from,
        period_to=period_to,
        cohort_from=cohort_from,
        cohort_to=cohort_to,
    )
    return query.table(db, user, row, built, ask)


@router.get("/{slug}/series", response_model=SeriesOut)
def metric_series(
    slug: str,
    request: Request,
    split: str | None = Query(default=None, description="선을 나눌 기준 이름"),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SeriesOut:
    """추이 — 기간순, 빈 기간은 0, 전기 · 전년 동기 · 닫힘."""
    row = services.get(db, slug)
    built = _built(db, row)
    ask = params.ask_from_request(
        request,
        dims=None,
        by=None,
        period_from=period_from,
        period_to=period_to,
        cohort_from=None,
        cohort_to=None,
    )
    return query.series(db, user, row, built, ask, split=split)


@router.get("/{slug}/cohort", response_model=CohortOut)
def metric_cohort(
    slug: str,
    request: Request,
    cumulative: bool = Query(default=False, description="코호트마다 경과순 누적"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CohortOut:
    """코호트 x 경과 행렬 — 음수 경과와 못 읽은 날짜는 빼고 그 수를 말한다."""
    row = services.get(db, slug)
    built = _built(db, row)
    ask = params.ask_from_request(
        request,
        dims=None,
        by=None,
        period_from=None,
        period_to=None,
        cohort_from=cohort_from,
        cohort_to=cohort_to,
    )
    return query.cohort(db, user, row, built, ask, cumulative=cumulative)


@router.get("/{slug}/dims", response_model=DimValuesOut)
def metric_dim_values(
    slug: str,
    name: str = Query(description="기준 이름"),
    q: str | None = Query(default=None, description="이름에 들어간 글자"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DimValuesOut:
    """기준 하나의 값과 이름 — 화면 고르개."""
    row = services.get(db, slug)
    built = _built(db, row)
    return query.dim_values(db, user, row, built, name, q=q)


# 분석(ADR 0014) — `/metrics/{slug}/analysis/<레시피>`. 지표 모듈 안의 하위 경로다.
router.include_router(recipes_routes.router)
router.include_router(alert_routes.router)
