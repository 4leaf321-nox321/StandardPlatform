"""전후 비교 — 대책 적용일 뒤에 만든(판) 것부터 비율이 줄었나(ADR 0021).

## 무엇을 견주나

부분군의 줄(관리도와 같다 — 기간 축, 또는 코호트마다 출고 K 기간 안)을 적용일로 가른다.

- **앞** — 첫 부분군부터 적용일 직전까지. 첫 부분군은 거른 범위의 첫 기록이다 — 모델로 거르면
  그 모델의 **첫 출고**(사용자, 2026-10-05). 출시 초기는 원래 높아(초기 생산 · 초기 고장) 앞을
  끌어올리므로 `skip_first` 로 처음 몇 부분군을 뺄 수 있다.
- **뒤** — 적용일 뒤의 부분군 중 **창이 닫힌 것만**(늦게 만든 달이 덜 들어온 것을 개선으로
  읽지 않게).
- 적용일이 기간 중간이면 그 부분군은 앞뒤가 섞여 있어 뺀다.

## 비와 구간

앞 r0 = Σc/Σn, 뒤 r1, 비 R = r1/r0. 앞뒤의 건수를 합친 것을 조건으로 두면 뒤의 건수는
이항(c0 + c1, π), π = n1R / (n0 + n1R) — π 의 클로퍼-피어슨 구간을 R 로 옮긴다. 부분군끼리의
흔들림이 포아송보다 크면(φ — 양쪽 각자의 평균에서 잰 피어슨 χ² / 자유도, 순차 검정과 같은
생각) 건수를 φ 로 나눈 「실효 건수」 로 구간과 p 를 낸다 — 우연한 달 차이를 대책 효과로 읽지
않게.

## 판정

구간 위 끝 < 1 이면 줄었다, 아래 끝 > 1 이면 늘었다. 구간이 (1 - e, 1 / (1 - e)) 안이면 차이
없음 (e — 의미 있는 차이, 기본 20%). 아니면 **아직 이르다** — 그 차이를 가리려면 뒤의 닫힌
부분군이 몇 개 더 필요한지 어림한다(양쪽 5%, 검정력 80%).

## 앞쪽 추세

앞의 부분군에 로그 비율의 기울기를 잰다(포아송 점수 검정, 앞쪽 φ 반영). 적용 전부터 내려가고
있었으면 「줄었다」 가 대책이 아니라 그 흐름일 수 있다 — 그렇게 말한다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from statistics import NormalDist
from typing import Literal

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, registry, series
from app.modules.metrics.recipes.schemas import (
    CutinOut,
    CutinPointOut,
    CutinSideOut,
    CutinTrendOut,
)
from app.modules.metrics.schemas import DrillOut
from app.modules.objects import axes

NAME = "cutin"
LABEL = "전후 비교"
METHOD = (
    "적용일 전후 부분군 비 · 클로퍼-피어슨(조건부 이항) · 실효 건수(φ) · 앞쪽 추세 점수 "
    "검정 v1"
)
EFFECT = 0.2
ALPHA = 0.05
POWER = 0.8
#: 앞쪽 추세를 재려면 부분군이 이만큼은 있어야 한다.
TREND_MIN = 4
#: φ 를 재려면 자유도가 이만큼은 있어야 한다.
DISPERSION_MIN_DF = 3

Decision = Literal["reduced", "increased", "no_difference", "too_early"]
Side = Literal["before", "after", "skipped", "boundary"]


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class Compared:
    ratio: float | None
    low: float | None
    high: float | None
    p_value: float | None
    dispersion: float


def dispersion(
    before: Sequence[tuple[float, float]], after: Sequence[tuple[float, float]]
) -> float:
    """양쪽 각자의 평균에서 잰 피어슨 χ² / 자유도. 자유도가 작으면 1, 1 보다 작으면 1.

    자유도는 (부분군 수 - **잰 평균의 수**)다 — 건수가 있는 쪽마다 비율 하나를 쟀다. 양쪽이면
    n - 2, 한쪽만이면(앞쪽 추세 · 뒤가 0 건) n - 1. 늘 n - 2 로 나누면 한쪽만일 때 φ 가 부풀어
    (부분군 다섯이면 4/3 배) 구간이 까닭 없이 넓었다(2026-10-08)."""
    total = 0.0
    used = 0
    rates = 0
    for side in (before, after):
        count = sum(c for c, _ in side)
        exposure = sum(n for _, n in side)
        if count <= 0 or exposure <= 0:
            continue
        rate = count / exposure
        rates += 1
        for c, n in side:
            if n > 0:
                expected = n * rate
                total += (c - expected) ** 2 / expected
                used += 1
    df = used - rates
    if df < DISPERSION_MIN_DF:
        return 1.0
    return max(1.0, total / df)


def compare(
    before: Sequence[tuple[float, float]], after: Sequence[tuple[float, float]]
) -> Compared:
    """(건수, 대수) 부분군 → 비 · 구간 · p · φ. 앞에 건수가 없거나 한쪽 대수가 없으면 비가
    없다. 구간의 위 끝이 없으면(`high` None) 「상한 없음」 이다."""
    phi = dispersion(before, after)
    c0, n0 = sum(c for c, _ in before), sum(n for _, n in before)
    c1, n1 = sum(c for c, _ in after), sum(n for _, n in after)
    if c0 <= 0 or n0 <= 0 or n1 <= 0:
        return Compared(None, None, None, None, phi)
    ratio = (c1 / n1) / (c0 / n0)
    # 실효 건수 — 과분산이면 건수를 φ 로 나눠 구간 · p 를 넓힌다(φ = 1 이면 정확 검정 그대로).
    k0, k1 = c0 / phi, c1 / phi
    low_pi = 0.0 if k1 <= 0 else _numeric.beta_ppf(ALPHA / 2, k1, k0 + 1)
    high_pi = _numeric.beta_ppf(1 - ALPHA / 2, k1 + 1, k0)

    def to_ratio(pi: float) -> float | None:
        """π → 비. π 가 1(이나 NaN)이면 비에 끝이 없다 — 앞의 실효 건수가 아주 적고 φ 가
        크면(앞 1건 · φ 64 → 실효 0.016건) 위 끝의 π 가 1.0 으로 떨어져 0 으로 나눴고, 그
        요청은 500 이었다(2026-10-08)."""
        if not pi < 1:
            return None
        return pi / (1 - pi) * n0 / n1

    low, high = to_ratio(low_pi), to_ratio(high_pi)
    tested = _numeric.binom_two_sided([round(k1)], round(k0 + k1), [n1 / (n0 + n1)])
    return Compared(ratio, low, high, tested[0], phi)


def decide(found: Compared, effect: float) -> Decision:
    if found.high is not None and found.high < 1:
        return "reduced"
    if found.low is not None and found.low > 1:
        return "increased"
    if (
        found.low is not None
        and found.high is not None
        and found.low > 1 - effect
        and found.high < 1 / (1 - effect)
    ):
        return "no_difference"
    return "too_early"


def more_needed(
    before: Sequence[tuple[float, float]],
    after: Sequence[tuple[float, float]],
    *,
    effect: float,
    phi: float,
) -> int | None:
    """그 차이(1 - effect 배)를 양쪽 5% · 검정력 80% 로 가리려면 더 닫혀야 할 뒤의 부분군 수.
    로그 비의 분산 ≈ φ(1/c0 + 1/c1). 앞의 건수만으로 이미 모자라면 None."""
    c0, n0 = sum(c for c, _ in before), sum(n for _, n in before)
    if c0 <= 0 or n0 <= 0:
        return None
    sizes = [n for _, n in after if n > 0] or [n for _, n in before if n > 0]
    if not sizes:
        return None
    per_subgroup = sum(sizes) / len(sizes) * (c0 / n0) * (1 - effect)
    z = NormalDist().inv_cdf(1 - ALPHA / 2) + NormalDist().inv_cdf(POWER)
    room = math.log(1 - effect) ** 2 / (z * z * phi) - 1 / c0
    if room <= 0 or per_subgroup <= 0:
        return None
    needed = math.ceil(1 / (per_subgroup * room))
    return max(0, needed - len(after))


def trend(groups: Sequence[tuple[float, float]]) -> tuple[float | None, float | None]:
    """앞쪽 부분군(차례대로)의 로그 비율 기울기 — 포아송 점수 검정(φ 반영). (기간마다의 변화,
    p). 부분군이 적거나 건수가 없으면 (None, None)."""
    used = [(t, c, n) for t, (c, n) in enumerate(groups) if n > 0]
    if len(used) < TREND_MIN:
        return None, None
    count = sum(c for _, c, _ in used)
    exposure = sum(n for _, _, n in used)
    if count <= 0:
        return None, None
    rate = count / exposure
    mean_t = sum(t * n for t, _, n in used) / exposure
    score = sum(t * (c - n * rate) for t, c, n in used)
    info = rate * sum(n * (t - mean_t) ** 2 for t, _, n in used)
    if info <= 0:
        return None, None
    phi = dispersion([(c, n) for _, c, n in used], [])
    z = score / math.sqrt(phi * info)
    slope = score / info
    return math.exp(slope) - 1, 2 * (1 - NormalDist().cdf(abs(z)))


# --- 셀 어댑터 -----------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure not in ("count", spec_module.SHARE):
        return "건수 · 조건 비율 지표에서만 됩니다 — 적용일 앞뒤의 비율을 견줍니다."
    if built.time is None and built.cohort is None:
        return "시간 칸이나 코호트 칸이 있는 지표에서만 됩니다."
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    at: date,
    axis: series.Axis | None = None,
    window: int = 3,
    skip_first: int = 0,
    effect: float = EFFECT,
) -> CutinOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(37, reason)
    if not 0 < effect < 1:
        raise common.refuse(37, "의미 있는 차이(effect)는 0 과 1 사이입니다(예: 0.2 = 20%).")
    found = series.read(
        db, user, metric, built, ask, axis=axis, window=window, split=None, limit=1
    )
    caveats = common.Caveats()
    series.caveats(found, caveats)
    line = found.series[0].subgroups if found.series else []
    per = found.per
    sides: list[Side] = []
    started = 0
    for one in line:
        stop = axes.next_period(one.when, found.grain)
        if one.when < at < stop:
            sides.append("boundary")
        elif one.when >= at:
            sides.append("after")
        elif started < skip_first:
            sides.append("skipped")
            started += 1
        else:
            sides.append("before")
    before = [
        (float(one.count), one.exposure)
        for one, side in zip(line, sides, strict=True)
        if side == "before" and one.closed and one.exposure > 0
    ]
    after_groups = [
        one
        for one, side in zip(line, sides, strict=True)
        if side == "after" and one.closed and one.exposure > 0
    ]
    after = [(float(one.count), one.exposure) for one in after_groups]
    open_after = sum(
        1 for one, side in zip(line, sides, strict=True) if side == "after" and not one.closed
    )
    compared = compare(before, after)
    decision = decide(compared, effect)
    more = (
        more_needed(before, after, effect=effect, phi=compared.dispersion)
        if decision == "too_early"
        else None
    )
    change, trend_p = trend(before)

    if not before:
        caveats.add(
            "no_before",
            "적용일 앞에 닫힌 부분군이 없습니다 — 견줄 앞쪽이 없습니다(적용일 · 거르기 · 처음 "
            "빼기를 확인합니다).",
        )
    if not after:
        caveats.add(
            "no_after",
            f"적용일 뒤에 창이 닫힌 부분군이 아직 없습니다(열린 것 {open_after}개) — 결론은 "
            "뒤가 닫힌 다음입니다.",
            count=open_after,
        )
    elif open_after:
        caveats.add(
            "open_after",
            f"적용일 뒤 부분군 {open_after}개는 아직 열려 있어 넣지 않았습니다.",
            level="info",
            count=open_after,
        )
    if "boundary" in sides:
        caveats.add(
            "boundary",
            "적용일이 낀 부분군은 앞뒤가 섞여 있어 뺐습니다.",
            level="info",
        )
    if compared.dispersion > 1.5:
        caveats.add(
            "overdispersion",
            f"부분군끼리의 흔들림이 우연의 {compared.dispersion:.1f}배입니다 — 구간을 그만큼 "
            "넓혔습니다(우연한 달 차이를 효과로 읽지 않게).",
            level="info",
        )
    if compared.ratio is not None and compared.high is None:
        caveats.add(
            "unbounded_high",
            "앞쪽 건수가 흔들림(φ)에 견줘 너무 적어 비의 위 끝이 없습니다(상한 없음) — "
            "「늘었다 · 차이 없음」 은 가릴 수 없습니다. 앞쪽을 넓힙니다(적용일 · 처음 빼기 · "
            "거르기).",
        )
    if change is not None and trend_p is not None and trend_p < ALPHA:
        direction = "내려가고" if change < 0 else "올라가고"
        caveats.add(
            "pre_trend",
            f"적용 전부터 부분군마다 약 {abs(change):.1%}씩 {direction} 있었습니다 — "
            "「줄었다 · 늘었다」 가 대책이 아니라 그 흐름일 수 있습니다. 출시 초기 때문이면 "
            "처음 몇 개를 뺍니다(skip_first).",
        )
    if not ask.filters:
        caveats.add(
            "unfiltered",
            "거르지 않고 전체를 봤습니다 — 대책은 모델마다 들어가므로 그 모델로 거릅니다(출시 "
            "시기가 다른 모델이 섞이면 앞쪽이 달라집니다).",
        )
    caveats.add(
        "association",
        "이 결과는 「적용일 뒤에 줄었다 · 늘었다」 이지 「대책 때문에」 가 아닙니다 — 같은 때 "
        "바뀐 다른 것(판매 경로 · 계절 · 다른 대책)은 가를 수 없습니다.",
        level="info",
    )

    points = [
        CutinPointOut(
            when=one.when.isoformat(),
            label=axes.period_label(one.when.isoformat(), found.grain),
            count=one.count,
            exposure=one.exposure if found.den is not None else None,
            rate=found.rate(one),
            closed=one.closed,
            side=side,
            drill=series.drill(found, built, None, one),
        )
        for one, side in zip(line, sides, strict=True)
    ]
    head = common.header(
        db,
        user,
        metric,
        built,
        found.frame,
        recipe=NAME,
        method=METHOD,
        params={
            "at": at.isoformat(),
            "axis": found.axis,
            "window": window if found.axis == "cohort" else None,
            "skip_first": skip_first,
            "effect": effect,
        },
        caveats=caveats,
        excluded=found.excluded,
    )
    return CutinOut(
        **head,
        at=at.isoformat(),
        axis=found.axis,
        window=window if found.axis == "cohort" else None,
        per=per,
        kind="rate" if found.den is not None else "count",
        before=_side(found, built, line, sides, "before"),
        after=_side(found, built, line, sides, "after"),
        open_after=open_after,
        ratio=compared.ratio,
        ratio_low=compared.low,
        ratio_high=compared.high,
        p_value=compared.p_value,
        dispersion=compared.dispersion,
        effect=effect,
        decision=decision,
        more_subgroups=more,
        pre_trend=CutinTrendOut(change_per_period=change, p_value=trend_p),
        points=points,
    )


def _side(
    found: series.SeriesSet,
    built: spec_module.Built,
    line: list[series.Subgroup],
    sides: list[Side],
    which: Side,
) -> CutinSideOut:
    """한쪽의 닫힌 부분군 합과 그 근거(그 범위의 기록 — 코호트 축은 「출고 K 안」 을 조건으로
    못 적어 `age` 가 partial)."""
    used = [
        one
        for one, side in zip(line, sides, strict=True)
        if side == which and one.closed and one.exposure > 0
    ]
    if not used:
        return CutinSideOut(
            first=None, last=None, subgroups=0, count=0, exposure=None, rate=None, drill=None
        )
    count = sum(one.count for one in used)
    exposure = sum(one.exposure for one in used)
    start, stop = used[0].when, axes.next_period(used[-1].when, found.grain)
    return CutinSideOut(
        first=axes.period_label(start.isoformat(), found.grain),
        last=axes.period_label(used[-1].when.isoformat(), found.grain),
        subgroups=len(used),
        count=count,
        exposure=exposure if found.den is not None else None,
        rate=count / exposure * found.per if exposure > 0 else None,
        drill=_range_drill(found, built, start, stop),
    )


def _range_drill(
    found: series.SeriesSet, built: spec_module.Built, start: date, stop: date
) -> DrillOut:
    ask = found.ask
    if found.axis == "cohort":
        ranged = replace(ask, cohort_from=start, cohort_to=stop)
    else:
        ranged = replace(ask, period_from=start, period_to=stop)
    cell = query.Cell({}, None, None, None, 0, 0, None, None, None)
    out = query.drill(built, cell, by=(), ask=ranged, numerator=True)
    if found.axis == "cohort":
        out.partial.append("age")
    return out
