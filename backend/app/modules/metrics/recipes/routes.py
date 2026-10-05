"""분석 경로 — `/api/metrics/{slug}/analysis/<레시피>`. 읽기다(로그인한 누구나, 보이는 것만).

모든 경로가 먼저 `query.snapshot` 으로 한 스냅샷을 잡는다 — 분석은 여러 번 읽는다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.metrics import compute, params, query, services
from app.modules.metrics.recipes import (
    assoc,
    changes,
    control,
    coverage,
    cutin,
    forecast,
    groups,
    life,
    logit,
    pareto,
    profile,
    recurrence,
    sprt,
    ways,
)
from app.modules.metrics.recipes.schemas import (
    AssocOut,
    ChangesOut,
    ChangesScanOut,
    ControlOut,
    CoverageOut,
    CutinOut,
    ForecastOut,
    GroupsOut,
    LifeOut,
    LogitOut,
    ParetoOut,
    ProfileOut,
    RecurrenceOut,
    SprtOut,
    SprtScanOut,
    WayOut,
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
    compare_from: str | None = Query(
        default=None, description="이 기간과 몫을 견준다 — 시작(지금 범위가 앞 기간)"
    ),
    compare_to: str | None = Query(default=None, description="견줄 기간의 끝(앞까지)"),
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
        compare_from=params.parse_date(compare_from, "compare_from"),
        compare_to=params.parse_date(compare_to, "compare_to"),
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


@router.get("/changes/scan", response_model=ChangesScanOut)
def changes_scan(
    slug: str,
    request: Request,
    by: str = Query(description="값마다 훑을 기준 이름(증상 등)"),
    axis: Literal["period", "cohort"] | None = Query(default=None),
    window: int = Query(
        default=3, ge=1, le=120, description="코호트 축 — 출고 뒤 몇 기간 안의 건수인가"
    ),
    top: int = Query(
        default=changes.SCAN_TOP,
        ge=1,
        le=changes.SCAN_MAX,
        description="건수 많은 값부터 몇 개",
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ChangesScanOut:
    """⑩ 값마다 훑기 — 「계절을 빼면 실제로 늘고 있는 증상은?」 값마다 계절 · 변화점을 맞추고
    (여럿을 보니 변화점 벌점에 2·ln K), 마지막 변화가 오름인 값부터."""
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
    return changes.scan(
        db, user, metric, built, ask, by=by, axis=axis, window=window, top=top, compact=compact
    )


@router.get("/sprt/scan", response_model=SprtScanOut)
def sprt_scan(
    slug: str,
    request: Request,
    by: str = Query(description="값마다 훑을 기준 이름(증상 등)"),
    target: str = Query(description="새 모델 — 그 기준의 값(관계 기준이면 객체 id)"),
    reference: str | None = Query(default=None, description="전작 — 그 기준의 값"),
    reference_via: str | None = Query(
        default=None, description="전작을 새 모델 객체의 이 칸(참조)에서 찾는다"
    ),
    dim: str | None = Query(default=None, description="모델 기준 — 비우면 분모 짝의 첫 기준"),
    rho: float = Query(default=sprt.RHO, gt=1.0, le=10.0),
    alpha: float = Query(default=sprt.ALPHA, gt=0.0, lt=0.5),
    beta: float = Query(default=sprt.BETA, gt=0.0, lt=0.5),
    top: int = Query(
        default=sprt.SCAN_TOP, ge=1, le=sprt.SCAN_MAX, description="새 모델의 건수 많은 값부터"
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SprtScanOut:
    """④ 값마다 훑기 — 「출시 N주차, 전작보다 빨리 늘고 있는 증상은?」 값마다 그 값으로 걸러
    새 모델 vs 전작 순차 검정(유의수준은 α/K), 「나쁨」 이 선 값부터."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(request)
    return sprt.scan(
        db,
        user,
        metric,
        built,
        ask,
        by=by,
        target=target,
        reference=reference,
        reference_via=reference_via,
        dim=dim,
        rho=rho,
        alpha=alpha,
        beta=beta,
        top=top,
    )


@router.get("/groups", response_model=GroupsOut)
def groups_analysis(
    slug: str,
    request: Request,
    dim: str | None = Query(
        default=None, description="견줄 집단의 기준 — 분모 짝(on)에 있어야 한다(비우면 첫 짝)"
    ),
    axis: Literal["period", "cohort"] | None = Query(default=None),
    window: int = Query(
        default=3, ge=1, le=120, description="코호트 축 — 출고 뒤 몇 기간 안의 건수인가"
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="「다르다」 인 집단과 위쪽 15개만(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> GroupsOut:
    """⑤ 집단 비교 — 집단마다 비율, 이질성 χ², 집단 대 나머지 정확 검정(BH), 작은 집단의 과장을
    줄인 비율(감마-포아송 경험적 베이즈). 거르기는 `d.<기준>=<값>`."""
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
    return groups.run(
        db, user, metric, built, ask, dim=dim, axis=axis, window=window, compact=compact
    )


@router.get("/cutin", response_model=CutinOut)
def cutin_analysis(
    slug: str,
    request: Request,
    at: date = Query(description="적용일 — 이 날 뒤에 만든(판) 것부터를 「뒤」 로 본다"),
    axis: Literal["period", "cohort"] | None = Query(default=None),
    window: int = Query(
        default=3, ge=1, le=120, description="코호트 축 — 출고 뒤 몇 기간 안의 건수인가"
    ),
    skip_first: int = Query(
        default=0, ge=0, le=60, description="앞쪽에서 뺄 처음 부분군 수(출시 초기)"
    ),
    effect: float = Query(
        default=cutin.EFFECT, gt=0, lt=1, description="의미 있는 차이(0.2 = 20%)"
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CutinOut:
    """전후 비교 — 적용일 앞(첫 부분군부터)과 뒤(창이 닫힌 것)의 비율과 그 비 · 구간, 「줄었다
    · 늘었다 · 차이 없음 · 아직 이르다」, 앞쪽 추세. 모델은 `d.<기준>=<값>` 으로 거른다."""
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
    return cutin.run(
        db,
        user,
        metric,
        built,
        ask,
        at=at,
        axis=axis,
        window=window,
        skip_first=skip_first,
        effect=effect,
    )


@router.get("/forecast", response_model=ForecastOut)
def forecast_analysis(
    slug: str,
    request: Request,
    horizon: int = Query(default=forecast.HORIZON, ge=1, le=60, description="앞으로 몇 기간"),
    warranty: int | None = Query(
        default=None,
        ge=1,
        le=240,
        description=(
            "보증 기간(코호트 기간 수) — 주면 그 경과부터는 세지 않고 끝까지 남은 총량도"
        ),
    ),
    model: Literal["auto", "weibull", "defective"] = Query(default="auto"),
    basis: Literal["records", "first_visits"] = Query(default="records"),
    cost: float | None = Query(default=None, ge=0, description="건당 비용 — 주면 금액도"),
    backtest: int = Query(
        default=forecast.BACKTEST, ge=0, le=24, description="되짚어 보기 기간(0 이면 안 함)"
    ),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ForecastOut:
    """클레임 예측 — 수명 분석과 같은 맞춤으로 이미 판 코호트의 앞으로 기간마다 예상 건수와
    80 · 95% 구간, 보증 끝까지의 총량, 되짚어 보기. 모델은 `d.<기준>=<값>` 으로 거른다."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(request, cohort_from=cohort_from, cohort_to=cohort_to)
    return forecast.run(
        db,
        user,
        metric,
        built,
        ask,
        horizon=horizon,
        warranty=warranty,
        model=model,
        basis=basis,
        cost=cost,
        backtest=backtest,
    )


@router.get("/coverage", response_model=CoverageOut)
def coverage_analysis(
    slug: str,
    request: Request,
    levels: str = Query(
        description="축인 기준을 차례로, 쉼표로(예: model,part,mechanism,method) — 2~4개"
    ),
    via: str | None = Query(
        default=None,
        description=(
            "기준 사이마다의 길, 쉼표로(비운 자리는 하나뿐인 길) — 예: out.bom,,ref.methods"
        ),
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(
        default=False, description="빈 칸 30 · 기대 밖 10 · 뿌리 15 만(MCP)"
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CoverageOut:
    """커버리지 — 온톨로지의 길로 편 「다뤄야 할 조합」 과 기록이 다룬 조합을 견준다. 단계마다
    다룬 몫, 첫 기준 값마다의 몫, 빈 칸, 기대 밖 조합. 첫 기준은 `d.<기준>=<값>` 으로
    거른다."""
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
    return coverage.run(
        db,
        user,
        metric,
        built,
        ask,
        levels=[one.strip() for one in levels.split(",") if one.strip()],
        via=[one.strip() or None for one in via.split(",")] if via else [],
        compact=compact,
    )


@router.get("/coverage/ways", response_model=list[WayOut])
def coverage_ways(
    slug: str,
    source: str = Query(alias="from", description="앞 기준"),
    target: str = Query(alias="to", description="다음 기준"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[WayOut]:
    """두 기준의 축 타입 사이의 한 걸음 길 후보 — 화면의 길 고르개."""
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    found = ways.ways(db, ways.axis_type(built, source), ways.axis_type(built, target))
    return [WayOut(address=one.address, label=one.label) for one in found]


@router.get("/recurrence", response_model=RecurrenceOut)
def recurrence_analysis(
    slug: str,
    request: Request,
    generation: str = Query(description="세대 기준(축 — 예: 모델)"),
    signature: str = Query(description="서명 기준, 쉼표로(예: part,mechanism) — 1~3개"),
    via: str | None = Query(
        default=None, description="세대 축에서 전작으로 가는 길(비우면 하나뿐인 길)"
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="쌍 15 · 쌍마다 5 · 서명 10 만(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecurrenceOut:
    """재발 — 세대마다 나온 서명(축 조합)을 전작의 것과 견준다. 다시 나온 것 · 재발률 · 새로
    나온 것, 여러 세대에 걸쳐 되풀이된 서명."""
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
    return recurrence.run(
        db,
        user,
        metric,
        built,
        ask,
        generation=generation,
        signature=[one.strip() for one in signature.split(",") if one.strip()],
        via=via,
        compact=compact,
    )


@router.get("/profile", response_model=ProfileOut)
def profile_analysis(
    slug: str,
    request: Request,
    dim: str = Query(description="기준"),
    value: str = Query(description="그 기준의 값(참조면 객체 id)"),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="함께 나온 값을 기준마다 5개만(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    """한 장 요약 — 기준 값 하나의 기록 수 · 몫, 기간 추이, 다른 기준마다 함께 나온
    값(향상도)."""
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
    return profile.run(db, user, metric, built, ask, dim=dim, value=value, compact=compact)


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


@router.get("/assoc", response_model=AssocOut)
def assoc_analysis(
    slug: str,
    request: Request,
    rows: str = Query(description="행 기준(증상 등)"),
    cols: str = Query(description="열 기준(교체 부품 · 원인 등) — 여러 값이어도 된다"),
    min_count: int = Query(
        default=assoc.MIN_COUNT,
        ge=1,
        le=100_000,
        description="이보다 적은 짝은 검정하지 않는다",
    ),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="짝 15개 · 지도 없이(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AssocOut:
    """⑨ 연관 · 묶음 — 기준 둘이 함께 나온 수에서 향상도 · 정확 검정(BH), 행의 묶음(PPMI ·
    평균 연결 · 실루엣), 대응 분석 지도. 거르기는 `d.<기준>=<값>`."""
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
    return assoc.run(
        db,
        user,
        metric,
        built,
        ask,
        rows=rows,
        cols=cols,
        min_count=min_count,
        compact=compact,
    )
