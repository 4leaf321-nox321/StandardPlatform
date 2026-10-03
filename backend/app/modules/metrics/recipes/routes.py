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
from app.modules.metrics.recipes import changes, control, life, logit, pareto, sprt
from app.modules.metrics.recipes.schemas import (
    ChangesOut,
    ControlOut,
    LifeOut,
    LogitOut,
    ParetoOut,
    SprtOut,
)
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
    basis: Literal["records", "first_visits"] = Query(
        default="records",
        description="first_visits 면 시리얼마다 첫 방문만 센다(방문 기준이 있는 지표)",
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
        db,
        user,
        metric,
        built,
        ask,
        model=model,
        max_age=max_age,
        basis=basis,
        compact=compact,
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


@router.get("/sprt", response_model=SprtOut)
def sprt_analysis(
    slug: str,
    request: Request,
    target: str = Query(description="새 모델 — 그 기준의 값(관계 기준이면 객체 id)"),
    reference: str | None = Query(default=None, description="전작 — 그 기준의 값"),
    reference_via: str | None = Query(
        default=None,
        description="전작을 새 모델 객체의 이 칸(참조)에서 찾는다 — reference 대신",
    ),
    dim: str | None = Query(
        default=None, description="모델 기준 — 비우면 분모 짝(on)의 첫 기준"
    ),
    rho: float = Query(default=sprt.RHO, gt=1.0, le=10.0, description="「나쁨」 의 비"),
    alpha: float = Query(default=sprt.ALPHA, gt=0.0, lt=0.5),
    beta: float = Query(default=sprt.BETA, gt=0.0, lt=0.5),
    compact: bool = Query(
        default=False, description="기간은 끝 12개와 결론이 선 자리만, 비율 · 코호트 줄 빼고"
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SprtOut:
    """④ 순차 검정 — 새 모델의 경과별 인입을 전작의 경과별 비율로 낸 기대 건수와 견주는
    포아송 SPRT. 매 기간 봐도 된다. 다른 거르기는 `d.<기준>=<값>`(두 모델에 함께)."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(request)
    return sprt.run(
        db,
        user,
        metric,
        built,
        ask,
        target=target,
        reference=reference,
        reference_via=reference_via,
        dim=dim,
        rho=rho,
        alpha=alpha,
        beta=beta,
        compact=compact,
    )


@router.get("/logit", response_model=LogitOut)
def logit_analysis(
    slug: str,
    request: Request,
    factors: str = Query(description="요인 기준 이름들(쉼표) — 값이 적은 기준, 넷까지"),
    min_count: int = Query(
        default=logit.MIN_COUNT,
        ge=1,
        le=100_000,
        description="기록이 이보다 적은 값은 「그 밖」 으로 모은다",
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="요인마다 값은 12개까지(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> LogitOut:
    """⑥ 재방문 위험 요인 — 방문 기준 「재방문」 을 요인별로 묶은 셀 위의 로지스틱. 요인마다
    오즈비 · 구간 · LR 검정, AUC. 거르기는 `d.<기준>=<값>`."""
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
    return logit.run(
        db,
        user,
        metric,
        built,
        ask,
        factors=params.names(factors),
        min_count=min_count,
        compact=compact,
    )
