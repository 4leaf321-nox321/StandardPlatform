"""④ 순차 검정 — 새 모델이 전작보다 나빠졌나, 출시 뒤 매 기간 물어도 되게(ADR 0014).

## 기대 건수 — 경과를 맞춰서

새 모델과 전작은 팔린 시점이 달라 같은 달력 시점에 경과 구성이 다르다 — 새 모델은 아직 대부분
갓 팔린 것이다. 그래서 전작의 **경과별 비율** r_a(경과 a 의 건수 / 그 경과까지 본 대수)로 새
모델의 기대 건수를 낸다(간접 표준화): 코호트 c 의 경과 a 셀이면 N_c · r_a.

## 포아송 SPRT

출시(새 모델의 첫 코호트)부터 기간 k 마다 닫힌 셀을 더해 관측 O_k 와 기대 E_k 를 쌓는다.
「비율이 전작의 ρ 배」 대 「같다」 의 로그 우도비

    Λ_k = O_k · ln ρ - (ρ - 1) · E_k

가 위 경계 A = ln((1-β)/α) 를 넘으면 「나쁨」, 아래 경계 B = ln(β/(1-α)) 밑이면 「나쁘지
않음」, 사이면 「아직」. 매 기간 봐도 잘못 「나쁨」 이라 할 확률이 α 언저리로 묶인다(왈드).
처음 경계를 넘은 때 결론이 서고, 그 뒤의 기간은 참고로만 그린다.

매 기간 표준화 비 SMR = O/E 와 정확 포아송 구간(가우드), 결론까지 남은 기대 건수(같다면 · ρ
배라면)를 함께 낸다. 「나아졌나」 는 묻지 않는다 — 표준화 비의 구간으로 읽는다.

## 과분산 — 준-포아송

기록은 포아송보다 크게 흔들린다(부품 로트 · 판매 묶음 · 코호트마다 몰림). 포아송 그대로면 그
흔들림을 차이로 읽어 잘못 「나쁨」 이 α 보다 훨씬 잦다(규모 자료에서 보통끼리 60쌍 중 7).
전작의 **코호트마다**(닫힌 경과까지의 합) 피어슨 φ = Σ (O - E)² / E / (코호트 수 - 1) 를 재고,
φ > 1 이면 Λ 를 φ 로 나눈다(준-우도). 셀(코호트 x 경과) 단위로 재면 한 셀의 기대가 작아
코호트째 몰리는 흔들림이 안 보인다. 결론까지 남은 기대 건수는 φ 배, 표준화 비의 구간은 로그
척도에서 √φ 배 넓어진다.

## 값마다 훑기 — 「전작보다 빨리 늘고 있는 **증상**은?」

기준 하나(증상 등)의 값마다 그 값으로 걸러 같은 검정을 한다 — 새 모델의 그 증상 건수를 전작의
그 증상의 경과별 비율로 낸 기대와 견준다(대수는 모델의 판매 대수 그대로). 값 K 개를 함께 보므로
값마다 유의수준을 α/K 로 나눈다(본페로니 — 새 모델 훑기와 같다). 「나쁨」 이 선 값부터, 표준화
비가 큰 것부터 세운다. 전작에 없던 값은 기대가 0 이라 비 없이 건수로만 「나쁨」 에 이른다.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, life, registry
from app.modules.metrics.recipes.schemas import (
    ReferenceRateOut,
    SprtCohortOut,
    SprtLookOut,
    SprtOut,
    SprtScanItemOut,
    SprtScanOut,
)
from app.modules.objects import axes
from app.modules.objects.models import ObjectInstance
from app.shared.errors import AppError
from app.shared.permissions import visible_owner_clause

NAME = "sprt"
LABEL = "순차 검정(전작 대비)"
METHOD = "포아송 SPRT(과분산이면 준-포아송 Λ/φ) · 전작의 경과별 비율로 간접 표준화 v2"
RHO = 1.5
ALPHA = 0.05
BETA = 0.10
COMPACT_LOOKS = 12
#: 과분산을 재려면 전작의 코호트가 이만큼은 있어야 한다.
DISPERSION_MIN_COHORTS = 5
#: 값마다 훑기 — 새 모델의 건수가 많은 값부터 몇 개.
SCAN_TOP = 20
SCAN_MAX = 40
Decision = Literal["continue", "worse", "not_worse"]


# --- 순수 함수 -----------------------------------------------------------------------


def boundaries(alpha: float, beta: float) -> tuple[float, float]:
    """(위, 아래) — 왈드의 근사 경계."""
    return math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))


def llr(observed: float, expected: float, rho: float) -> float:
    return observed * math.log(rho) - (rho - 1) * expected


@dataclass
class Look:
    observed: float
    expected: float
    llr: float
    decision: Decision


def walk(
    observed: Sequence[float],
    expected: Sequence[float],
    *,
    rho: float,
    alpha: float,
    beta: float,
    dispersion: float = 1.0,
) -> tuple[list[Look], int | None]:
    """기간마다 더한 관측 · 기대 → 쌓은 줄과 결론이 선 자리(처음 경계를 넘은 기간).
    `dispersion` φ > 1 이면 Λ 를 φ 로 나눈다(준-포아송)."""
    upper, lower = boundaries(alpha, beta)
    total_o = total_e = 0.0
    decided: int | None = None
    state: Decision = "continue"
    out: list[Look] = []
    for k, (o, e) in enumerate(zip(observed, expected, strict=True)):
        total_o += o
        total_e += e
        value = llr(total_o, total_e, rho) / max(dispersion, 1.0)
        if decided is None:
            if value >= upper:
                state, decided = "worse", k
            elif value <= lower:
                state, decided = "not_worse", k
        out.append(Look(total_o, total_e, value, state))
    return out, decided


def garwood(observed: float, expected: float) -> tuple[float | None, float | None]:
    """SMR = O / E 의 95% 정확 포아송 구간."""
    if expected <= 0:
        return None, None
    low = 0.0 if observed <= 0 else _numeric.chi2_ppf(0.025, 2 * observed) / 2
    high = _numeric.chi2_ppf(0.975, 2 * (observed + 1)) / 2
    return low / expected, high / expected


def remaining(
    value: float, *, rho: float, alpha: float, beta: float, dispersion: float = 1.0
) -> tuple[float, float]:
    """결론까지 남은 **기대 건수**(전작 비율로) — (같다면 「나쁘지 않음」 까지, ρ 배라면
    「나쁨」 까지). 기대 건수 하나마다 Λ 가 평균으로 움직이는 만큼으로 나눈다(과분산이면 φ
    배)."""
    upper, lower = boundaries(alpha, beta)
    phi = max(dispersion, 1.0)
    toward_same = ((rho - 1) - math.log(rho)) / phi
    toward_worse = (rho * math.log(rho) - (rho - 1)) / phi
    return max(0.0, (value - lower) / toward_same), max(0.0, (upper - value) / toward_worse)


def overdispersion(cohorts: Sequence[life.Cohort], rates: dict[int, Rate]) -> float:
    """전작의 **코호트마다** 피어슨 φ — 닫힌 경과까지의 관측 합이 기대 합(대수 x 경과별
    비율의 합)보다 얼마나 더 흔들리나. 셀(코호트 x 경과) 단위로 재면 한 셀의 기대가 작아
    코호트째 몰리는 흔들림(한 코호트에 수십 건 · 이웃은 0 건)이 안 보인다 — 순차 검정은
    코호트를 더해 가므로 그 단위로 잰다. 코호트가 `DISPERSION_MIN_COHORTS` 보다 적으면 1(잴 수
    없다)."""
    total = 0.0
    used = 0
    for cohort in cohorts:
        expected = sum(
            cohort.units * rates[a].rate for a in range(cohort.horizon + 1) if a in rates
        )
        if expected <= 0:
            continue
        observed = sum(
            c for a, c in cohort.counts.items() if a <= cohort.horizon and a in rates
        )
        total += (observed - expected) ** 2 / expected
        used += 1
    if used < DISPERSION_MIN_COHORTS:
        return 1.0
    return total / (used - 1)


def widened(
    smr: float | None, low: float | None, high: float | None, dispersion: float
) -> tuple[float | None, float | None]:
    """표준화 비의 구간을 로그 척도에서 √φ 배 넓힌다(과분산)."""
    phi = max(dispersion, 1.0)
    if smr is None or smr <= 0 or phi == 1.0:
        return low, high
    root = math.sqrt(phi)
    out_low = smr * (low / smr) ** root if low is not None and low > 0 else low
    out_high = smr * (high / smr) ** root if high is not None else high
    return out_low, out_high


@dataclass
class Rate:
    rate: float
    units: float
    records: int


def reference_rates(cohorts: Sequence[life.Cohort]) -> dict[int, Rate]:
    """경과 a 마다 — 그 경과까지 본 코호트들의 건수 합 / 대수 합."""
    top = max((one.horizon for one in cohorts), default=-1)
    out: dict[int, Rate] = {}
    for a in range(top + 1):
        seen = [one for one in cohorts if one.horizon >= a]
        units = sum(one.units for one in seen)
        records = sum(one.counts.get(a, 0) for one in seen)
        if units > 0:
            out[a] = Rate(records / units, units, records)
    return out


# --- 어댑터 --------------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 순차 검정은 건수를 기대 건수와 견줍니다."
    if built.cohort is None or built.time is None:
        return "코호트 칸(판매일)과 시간 칸(접수일)이 있는 지표에서만 됩니다."
    den = built.spec.denominator
    if den is None or den.time != "cohort" or not den.on:
        return (
            "분모(판매 대수)를 코호트로, 모델 기준(on)과 짝지은 지표에서만 됩니다 — "
            "모델마다의 대수를 알아야 전작과 견줍니다."
        )
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def _follow(db: Session, user: User, target: str, via: str) -> str:
    """새 모델 객체의 `via` 칸이 가리키는 전작."""
    try:
        key = uuid.UUID(target)
    except ValueError as caught:
        raise common.refuse(
            29, f"reference_via 는 객체를 값으로 갖는 기준에서만 됩니다: {target}"
        ) from caught
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == key,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    found = (row.properties or {}).get(via) if row is not None else None
    if not isinstance(found, str) or not found:
        raise common.refuse(
            29,
            f"새 모델의 「{via}」 칸이 비었거나 그 객체가 안 보입니다 — 전작을 "
            "reference=<값> 으로 줍니다.",
        )
    return found


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    target: str,
    reference: str | None = None,
    reference_via: str | None = None,
    dim: str | None = None,
    rho: float = RHO,
    alpha: float = ALPHA,
    beta: float = BETA,
    compact: bool = False,
) -> SprtOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(28, reason)
    den_in = built.spec.denominator
    assert den_in is not None and built.cohort is not None
    grain = built.cohort.grain
    dim = dim or den_in.on[0]
    target_dim = common.dim_of(built, dim)
    if dim not in den_in.on:
        raise common.refuse(
            29,
            f"「{target_dim.axis.label}」 은 분모 짝(on)에 없어 모델마다의 대수를 모릅니다 — "
            f"{', '.join(den_in.on)} 중 하나로 봅니다.",
        )
    if dim in ask.filters:
        raise common.refuse(29, f"d.{dim} 대신 target · reference 로 새 모델과 전작을 줍니다.")
    if reference is None:
        if reference_via is None:
            raise common.refuse(
                29,
                "전작을 reference=<값> 으로 주거나, reference_via=<전작을 가리키는 칸> 으로 "
                "새 모델 객체에서 찾게 합니다.",
            )
        reference = _follow(db, user, target, reference_via)
    if reference == target:
        raise common.refuse(29, "새 모델과 전작이 같습니다.")

    def frame_of(value: str) -> query.Frame:
        one = replace(
            ask, dims=[], by=("cohort", "age"), age_from=0, filters={**ask.filters, dim: value}
        )
        common.require_exact_counts(built, one)
        found = query.frame(db, user, metric, built, one)
        common.require_whole(found)
        return found

    target_frame = frame_of(target)
    reference_frame = frame_of(reference)
    excluded: dict[str, int] = {}
    # 대수당 건수를 견준다 — 기록이 대수보다 많은 코호트(재방문)도 그대로 쓴다.
    target_cohorts = life.cohorts_of(target_frame, grain, excluded, within_units=False)
    reference_excluded: dict[str, int] = {}
    reference_cohorts = life.cohorts_of(
        reference_frame, grain, reference_excluded, within_units=False
    )
    rates = reference_rates(reference_cohorts)
    if not rates:
        raise common.refuse(
            29, "전작의 닫힌 코호트가 없습니다 — 판매 대수와 닫힌 경과가 있어야 견줍니다."
        )
    reach = max(rates)
    phi = max(1.0, overdispersion(reference_cohorts, rates))
    caveats = common.Caveats()
    labels = query.labels_for(
        db,
        built,
        [dim],
        [
            query.Cell({dim: value}, None, None, None, 0, 0, None, None, None)
            for value in (target, reference)
        ],
    )

    looks: list[SprtLookOut] = []
    rows: list[SprtCohortOut] = []
    decision: Decision = "continue"
    decided_label: str | None = None
    final: Look | None = None
    left: tuple[float, float] | None = None
    looks_left: tuple[float | None, float | None] = (None, None)
    if target_cohorts:
        launch = target_cohorts[0].start
        last = max(query.advance(one.start, one.horizon, grain) for one in target_cohorts)
        timeline = query.dense(launch, axes.next_period(last, grain), grain)
        index = {when: k for k, when in enumerate(timeline)}
        observed = [0.0] * len(timeline)
        expected = [0.0] * len(timeline)
        beyond = 0
        for cohort in target_cohorts:
            base = index[cohort.start]
            used_o = 0
            used_e = 0.0
            for a in range(cohort.horizon + 1):
                count = cohort.counts.get(a, 0)
                if a > reach:
                    beyond += count
                    continue
                observed[base + a] += count
                expected[base + a] += cohort.units * rates[a].rate
                used_o += count
                used_e += cohort.units * rates[a].rate
            stop = query.advance(cohort.start, min(cohort.horizon, reach) + 1, grain)
            cell = query.Cell(
                {dim: target}, None, cohort.start, None, used_o, 0, None, None, None
            )
            rows.append(
                SprtCohortOut(
                    cohort=cohort.start.isoformat(),
                    label=axes.period_label(cohort.start.isoformat(), grain),
                    units=cohort.units,
                    observed=used_o,
                    expected=used_e,
                    drill=query.drill(
                        built,
                        cell,
                        by=("cohort",),
                        ask=replace(ask, period_from=cohort.start, period_to=stop),
                    ),
                )
            )
        excluded["beyond_reference"] = beyond
        walked, decided = walk(
            observed, expected, rho=rho, alpha=alpha, beta=beta, dispersion=phi
        )
        for k, (when, one) in enumerate(zip(timeline, walked, strict=True)):
            smr_k = one.observed / one.expected if one.expected > 0 else None
            low, high = widened(smr_k, *garwood(one.observed, one.expected), phi)
            looks.append(
                SprtLookOut(
                    k=k,
                    when=when.isoformat(),
                    label=axes.period_label(when.isoformat(), grain),
                    observed=one.observed,
                    expected=one.expected,
                    llr=one.llr,
                    smr=one.observed / one.expected if one.expected > 0 else None,
                    smr_low=low,
                    smr_high=high,
                    decision=one.decision,
                    after_decision=decided is not None and k > decided,
                )
            )
        final = walked[-1] if walked else None
        if decided is not None:
            decision = walked[decided].decision
            decided_label = looks[decided].label
        elif final is not None:
            left = remaining(final.llr, rho=rho, alpha=alpha, beta=beta, dispersion=phi)
            recent = expected[-3:]
            pace = sum(recent) / len(recent) if recent else 0.0
            if pace > 0:
                looks_left = (left[0] / pace, left[1] / pace)
        if compact:
            keep = {decided} if decided is not None else set()
            tail = len(looks) - COMPACT_LOOKS
            looks = [one for one in looks if one.k >= tail or one.k in keep]
            rows = []
    _caveats(caveats, excluded, reference_excluded, final, rho)
    if phi > 1.5:
        caveats.add(
            "overdispersion",
            f"전작의 건수가 포아송의 {phi:.1f}배로 흔들립니다 — 우도비를 그만큼 나눴습니다"
            "(준-포아송). 결론이 늦게 서는 대신 흔들림을 차이로 읽지 않습니다.",
            level="info",
        )
    head = common.header(
        db,
        user,
        metric,
        built,
        target_frame,
        recipe=NAME,
        method=METHOD,
        params={
            "dim": dim,
            "target": target,
            "reference": reference,
            "reference_via": reference_via,
            "rho": rho,
            "alpha": alpha,
            "beta": beta,
            "compact": compact,
        },
        caveats=caveats,
        excluded={
            **excluded,
            **{f"reference_{key}": value for key, value in reference_excluded.items()},
        },
    )
    upper, lower = boundaries(alpha, beta)
    smr_final = final.observed / final.expected if final and final.expected > 0 else None
    smr_low, smr_high = (
        widened(smr_final, *garwood(final.observed, final.expected), phi)
        if final
        else (None, None)
    )
    return SprtOut(
        **head,
        dim=dim,
        dim_label=target_dim.axis.label,
        target=target,
        target_label=query.label_of(labels, dim, target),
        reference=reference,
        reference_label=query.label_of(labels, dim, reference),
        rho=rho,
        alpha=alpha,
        beta=beta,
        upper=upper,
        lower=lower,
        dispersion=phi,
        decision=decision,
        decided_at=decided_label,
        observed=final.observed if final else 0.0,
        expected=final.expected if final else 0.0,
        llr=final.llr if final else 0.0,
        smr=final.observed / final.expected if final and final.expected > 0 else None,
        smr_low=smr_low,
        smr_high=smr_high,
        to_not_worse=left[0] if left else None,
        to_worse=left[1] if left else None,
        periods_to_not_worse=looks_left[0],
        periods_to_worse=looks_left[1],
        reference_reach=reach,
        reference_rates=[]
        if compact
        else [
            ReferenceRateOut(age=a, rate=one.rate, units=one.units, records=one.records)
            for a, one in sorted(rates.items())
        ],
        looks=looks,
        cohort_rows=rows,
    )


def _caveats(
    caveats: common.Caveats,
    excluded: dict[str, int],
    reference_excluded: dict[str, int],
    final: Look | None,
    rho: float,
) -> None:
    if final is None:
        caveats.add(
            "too_early",
            "새 모델의 닫힌 코호트가 없습니다 — 판매 대수와 닫힌 경과가 쌓이면 봅니다.",
        )
    elif final.expected < 5:
        caveats.add(
            "small_expected",
            f"기대 건수가 {final.expected:.1f}건뿐입니다 — 결론이 서기 전이면 「아직」 이고, "
            "표준화 비의 구간이 넓습니다.",
            level="info",
        )
    if excluded.get("beyond_reference"):
        caveats.add(
            "beyond_reference",
            "전작이 그 경과까지 안 가 견주지 못한 새 모델의 기록은 뺐습니다.",
            level="info",
            count=excluded["beyond_reference"],
        )
    missing = excluded.get("missing_denominator", 0) + reference_excluded.get(
        "missing_denominator", 0
    )
    if missing:
        caveats.add(
            "missing_denominator", "판매 대수가 없는 코호트를 뺐습니다.", count=missing
        )
    if excluded.get("open_cells", 0) + reference_excluded.get("open_cells", 0):
        caveats.add(
            "open_cells_excluded",
            "아직 닫히지 않은 경과의 기록은 뺐습니다 — 더 들어올 수 있습니다.",
            level="info",
        )
    caveats.add(
        "one_sided",
        f"「전작보다 {rho:g}배 나쁜가」 만 묻습니다 — 나아졌는지는 표준화 비(관측 / 기대)의 "
        "구간으로 읽습니다.",
        level="info",
    )
    caveats.add(
        "records_not_units",
        "기록 수를 고장 대수로 봅니다 — 재방문이 두 모델에서 다르게 섞이면 그만큼 틀립니다.",
        level="info",
    )


def scan(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    by: str,
    target: str,
    reference: str | None = None,
    reference_via: str | None = None,
    dim: str | None = None,
    rho: float = RHO,
    alpha: float = ALPHA,
    beta: float = BETA,
    top: int = SCAN_TOP,
) -> SprtScanOut:
    """기준 `by` 의 값마다 새 모델 vs 전작 — 「출시 N주차, 전작보다 빨리 늘고 있는
    증상은?」."""
    reason = available(built)
    if reason is not None:
        raise common.refuse(28, reason)
    den_in = built.spec.denominator
    assert den_in is not None
    dim = dim or den_in.on[0]
    model_dim = common.dim_of(built, dim)
    by_dim = common.dim_of(built, by)
    if by == dim:
        raise common.refuse(
            29, "훑을 기준이 모델 기준과 같습니다 — 증상처럼 다른 기준을 줍니다."
        )
    if by in ask.filters:
        raise common.refuse(
            29, f"d.{by} 로 거른 기준으로는 훑을 수 없습니다 — 거르기를 뺍니다."
        )
    if reference is None and reference_via is not None:
        reference = _follow(db, user, target, reference_via)
    if reference is None:
        raise common.refuse(
            29,
            "전작을 reference=<값> 으로 주거나, reference_via=<전작을 가리키는 칸> 으로 "
            "새 모델 객체에서 찾게 합니다.",
        )
    # 값 고르기 — 새 모델의 기록이 많은 값부터. 전작에만 있는 값은 「늘고 있나」 의 물음이
    # 아니다.
    pick = replace(ask, dims=[by], by=(), filters={**ask.filters, dim: target})
    frame = query.frame(db, user, metric, built, pick, with_denominator=False)
    common.require_whole(frame)
    totals: dict[str, int] = {}
    for cell in frame.cells:
        key = cell.dims.get(by)
        if key is not None:
            totals[key] = totals.get(key, 0) + cell.count
    limit = max(1, min(top, SCAN_MAX))
    values = sorted(totals, key=lambda key: (-totals[key], key))[:limit]
    each = alpha / max(len(values), 1)
    labels = query.labels_for(db, built, [by], frame.cells)
    names = query.labels_for(
        db,
        built,
        [dim],
        [
            query.Cell({dim: value}, None, None, None, 0, 0, None, None, None)
            for value in (target, reference)
        ],
    )
    items: list[SprtScanItemOut] = []
    skipped: list[str] = []
    for value in values:
        label = query.label_of(labels, by, value)
        try:
            out = run(
                db,
                user,
                metric,
                built,
                replace(ask, filters={**ask.filters, by: value}),
                target=target,
                reference=reference,
                dim=dim,
                rho=rho,
                alpha=each,
                beta=beta,
                compact=True,
            )
        except AppError as caught:
            skipped.append(f"{label}: {caught.message}")
            continue
        items.append(
            SprtScanItemOut(
                key=value,
                label=label,
                decision=out.decision,
                decided_at=out.decided_at,
                observed=out.observed,
                expected=out.expected,
                llr=out.llr,
                smr=out.smr,
                smr_low=out.smr_low,
                smr_high=out.smr_high,
                periods_to_worse=out.periods_to_worse,
                periods_to_not_worse=out.periods_to_not_worse,
                new=out.expected <= 0 and out.observed > 0,
            )
        )
    rank = {"worse": 0, "continue": 1, "not_worse": 2}
    items.sort(
        key=lambda one: (
            rank[one.decision],
            -(one.smr if one.smr is not None else (math.inf if one.new else 0.0)),
            -one.llr,
        )
    )
    caveats = common.Caveats()
    if len(values) > 1:
        caveats.add(
            "multiple_testing",
            f"값 {len(values)}개를 함께 보므로 잘못 「나쁨」 이라 할 확률을 값마다 "
            f"{each:.4f}(α/{len(values)})로 나눴습니다 — 보수적이라 작은 차이는 놓칠 수 "
            "있습니다(하나만 보려면 d.<기준>=값 으로 단건 분석).",
            level="info",
        )
    if by not in den_in.on:
        caveats.add(
            "shared_exposure",
            f"대수는 「{by_dim.axis.label}」 와 상관없이 모델의 판매 대수입니다 — 모든 기기가 "
            "모든 값에 노출된다고 봅니다.",
            level="info",
        )
    if any(one.new for one in items):
        caveats.add(
            "new_values",
            "전작에 없던 값은 기대가 0 이라 표준화 비가 없습니다 — 건수로만 「나쁨」 에 "
            "이릅니다.",
            level="info",
        )
    if skipped:
        caveats.add(
            "skipped_values",
            f"견주지 못한 값 {len(skipped)}개 — {skipped[0]}",
            level="info",
            count=len(skipped),
        )
    if len(totals) > limit:
        caveats.add(
            "other_values",
            f"새 모델의 건수가 적은 {len(totals) - limit}개 값은 훑지 않았습니다.",
            level="info",
            count=len(totals) - limit,
        )
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=f"{METHOD} · 값마다 훑기(본페로니)",
        params={
            "by": by,
            "dim": dim,
            "target": target,
            "reference": reference,
            "reference_via": reference_via,
            "rho": rho,
            "alpha": alpha,
            "beta": beta,
            "top": top,
        },
        caveats=caveats,
        excluded={},
    )
    return SprtScanOut(
        **head,
        dim=dim,
        dim_label=model_dim.axis.label,
        by=by,
        by_label=by_dim.axis.label,
        target=target,
        target_label=query.label_of(names, dim, target),
        reference=reference,
        reference_label=query.label_of(names, dim, reference),
        rho=rho,
        alpha=alpha,
        alpha_each=each,
        beta=beta,
        scanned=len(values),
        other_values=max(0, len(totals) - limit),
        items=items,
        skipped=skipped,
    )
