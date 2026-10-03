"""분석 경로 — `/api/metrics/{slug}/analysis/<레시피>`. 읽기다(로그인한 누구나, 보이는 것만).

모든 경로가 먼저 `query.snapshot` 으로 한 스냅샷을 잡는다 — 분석은 여러 번 읽는다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.metrics import compute, params, query, services
from app.modules.metrics.recipes import changes, control, life, pareto
from app.modules.metrics.recipes.schemas import ChangesOut, ControlOut, LifeOut, ParetoOut
from app.shared.auth import current_user

router = APIRouter(prefix="/{slug}/analysis")


@router.get("/pareto", response_model=ParetoOut)
def pareto_analysis(
    slug: str,
    request: Request,
    dim: str = Query(description="몫을 볼 기준 이름"),
    top: int = Query(
        default=30, ge=1, le=200, description="줄로 보일 값 수 — 나머지는 「그 밖」"
    ),
    include_empty: bool = Query(default=False, description="값이 빈 기록을 몫에 넣을지"),
    by_period: bool = Query(default=False, description="기간마다의 집중도 추이"),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="줄을 10개로 줄이고 추이를 뺀다(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ParetoOut:
    """⑦ 파레토 · 집중도 — 기준 하나의 값별 몫 · 누적 · ABC, HHI · 유효 개수 · 지니 · CR.
    거르기는 `d.<기준>=<값>`."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(
        request,
        period_from=period_from,
        period_to=period_to,
        cohort_from=cohort_from,
        cohort_to=cohort_to,
    )
    return pareto.run(
        db,
        user,
        metric,
        built,
        ask,
        dim=dim,
        top=top,
        include_empty=include_empty,
        by_period=by_period,
        compact=compact,
    )


@router.get("/life", response_model=LifeOut)
def life_analysis(
    slug: str,
    request: Request,
    model: Literal["auto", "weibull", "defective"] = Query(
        default="auto",
        description="auto 는 표준 · 결함 와이블을 함께 맞추고 LR 검정으로 고른다",
    ),
    max_age: int | None = Query(
        default=None, ge=1, le=600, description="경과 몇 개까지 쓸지 — 24 면 경과 0~23"
    ),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    compact: bool = Query(default=False, description="곡선 · 코호트 줄을 빼고 요약만(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> LifeOut:
    """② 수명 · B수명 — 판매월 코호트의 경과별 인입으로 와이블(표준 · 결함)을 맞추고
    B1 · B5 · B10 을 상태(관측 안 · 외삽 · 이르지 않음 · 불확실)와 함께 낸다. 거르기는
    `d.<기준>=<값>`(분모에도 걸린다)."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(request, cohort_from=cohort_from, cohort_to=cohort_to)
    return life.run(
        db, user, metric, built, ask, model=model, max_age=max_age, compact=compact
    )


@router.get("/control", response_model=ControlOut)
def control_analysis(
    slug: str,
    request: Request,
    axis: Literal["period", "cohort"] | None = Query(
        default=None,
        description="부분군의 축 — 비우면 분모가 코호트와 짝일 때 cohort, 아니면 period",
    ),
    window: int = Query(
        default=3, ge=1, le=120, description="코호트 축 — 출고 뒤 몇 기간 안의 건수인가"
    ),
    split: str | None = Query(
        default=None, description="이 기준의 값마다 차트를 나눈다(분모 짝에 있어야)"
    ),
    baseline_to: str | None = Query(
        default=None, description="한계를 이 날 **앞의** 부분군으로만 잡는다"
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="차트 6개 · 점은 끝 12개와 신호만(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ControlOut:
    """③ 관리도 — 라니 보정 u-관리도(분모가 없으면 건수 관리도)와 넬슨 규칙 1 · 2 · 3 · 5.
    거르기는 `d.<기준>=<값>`."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(
        request,
        period_from=period_from,
        period_to=period_to,
        cohort_from=cohort_from,
        cohort_to=cohort_to,
    )
    return control.run(
        db,
        user,
        metric,
        built,
        ask,
        axis=axis,
        window=window,
        split=split,
        baseline_to=params.parse_date(baseline_to, "baseline_to"),
        compact=compact,
    )


@router.get("/changes", response_model=ChangesOut)
def changes_analysis(
    slug: str,
    request: Request,
    axis: Literal["period", "cohort"] | None = Query(
        default=None,
        description="부분군의 축 — 비우면 분모가 코호트와 짝일 때 cohort, 아니면 period",
    ),
    window: int = Query(
        default=3, ge=1, le=120, description="코호트 축 — 출고 뒤 몇 기간 안의 건수인가"
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="점은 끝 24개만(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ChangesOut:
    """⑩ 계절 · 변화점 — 중앙값 계절 지수와 준-포아송 최적 분할을 번갈아 맞춰 수준이 바뀐
    곳을 찾는다. 거르기는 `d.<기준>=<값>`."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(
        request,
        period_from=period_from,
        period_to=period_to,
        cohort_from=cohort_from,
        cohort_to=cohort_to,
    )
    return changes.run(db, user, metric, built, ask, axis=axis, window=window, compact=compact)
