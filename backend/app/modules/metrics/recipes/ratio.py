"""④-2 전작 대비 배율 — 새 모델의 「판매 대수당 건수」 가 전작의 몇 배인가, 점추정과 95% 구간
(ADR 0082).

순차 검정(`sprt`)은 「ρ 배 나쁜가 · 아닌가」 를 매 기간 묻는 **판정**이다. 사람이 먼저 묻는
것은 대개 **수**다 — 「전작의 몇 배인가, 얼마나 확실한가」. 그것을 시장마다 165줄로 늘어놓은
전수표가 보고의 단위다(`by` — 상한 없이 값 전부).

## 배율 — 경과를 맞춘다(간접 표준화)

새 모델은 갓 팔린 것이 많아, 같은 달력 시점의 「건수 / 판매 대수」 를 그대로 견주면 전작보다
좋아 보인다(경과가 짧다). 그래서 순차 검정과 같은 기대 건수를 쓴다 — 전작의 **경과별 비율**
r_a(경과 a 의 건수 / 그 경과까지 본 대수)로 새 모델의 코호트 c 마다 N_c · r_a 를 더한 E 가
「전작 같았다면」 의 건수다. 배율 = O / E(새 모델의 관측 건수 / 기대 건수). 1 이면 전작과 같다.

같은 셀에서 「경과를 맞추지 않은」 단순 배율(새 모델 건수/대수 ÷ 전작 건수/대수)도 함께 낸다 —
둘이 많이 다르면 경과 구성 차이가 큰 것이다.

## 95% 구간 — 두 모델의 흔들림을 함께

    ln(배율) ± 1.96 · √(φ · (1/O + V_E / E²))

- 1/O — 새 모델 건수의 포아송 흔들림.
- V_E / E² — 기대 건수가 전작의 비율에서 왔으므로 그 비율의 흔들림(델타법). V_E = Σ_a W_a² ·
  d_a / U_a² (W_a: 새 모델이 경과 a 까지 본 대수, d_a · U_a: 전작의 그 경과 건수 · 대수).
  전작이 작으면 구간이 넓어진다 — 표준화 비의 정확 구간(가우드)은 E 를 고정된 값으로 봐 이것을
  놓친다.
- φ — 전작의 코호트마다 잰 과분산(순차 검정과 같다, 1 보다 작으면 1).

O 가 0 이면 아래 끝 0 · 위 끝은 정확 포아송(0 건의 97.5% 위 끝 3.69 / E)에 같은 넓힘. E 가 0
이면 (전작에 그 값이 없었다) 배율이 없다 — `new`.

## 판정

구간의 아래 끝이 1 보다 크면 `higher`(전작보다 높다), 위 끝이 1 보다 작으면 `lower`, 아니면
`unclear`(구간이 1 을 품는다 — 같다는 뜻이 아니다). 값마다 따로 본 구간이다 — 165개를 함께 보면
몇 개는 우연히 1 을 벗어난다(다중 비교 보정은 하지 않는다, 주의로 말한다)."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, life, registry, sprt
from app.modules.metrics.recipes.schemas import RatioOut, RatioRowOut

NAME = "ratio"
LABEL = "전작 대비 배율"
METHOD = (
    "간접 표준화 배율 O/E(전작의 경과별 비율) · 95% 구간 ln 척도 √(φ(1/O + V_E/E²)) · "
    "단순 배율 v1"
)
Z = 1.959963984540054
Verdict = Literal["higher", "lower", "unclear"]


@dataclass
class Estimate:
    """한 값(또는 전체)의 배율."""

    observed: int
    expected: float
    target_units: float
    target_records: int
    """새 모델의 닫힌 경과의 건수 전부(전작이 못 간 경과까지) — 단순 배율의 분자."""
    reference_units: float
    reference_records: int
    ratio: float | None
    low: float | None
    high: float | None
    crude: float | None
    dispersion: float
    verdict: Verdict
    new: bool
    beyond: int
    """전작이 그 경과까지 안 가 배율에서 뺀 새 모델의 건수."""


def estimate(
    target: list[life.Cohort], reference: list[life.Cohort], *, dispersion: float | None = None
) -> Estimate:
    """코호트 → 배율 · 구간. `dispersion` 을 주면 그것을 쓴다(아니면 전작에서 잰다)."""
    rates = sprt.reference_rates(reference)
    reach = max(rates) if rates else -1
    phi = (
        max(1.0, dispersion)
        if dispersion is not None
        else max(1.0, sprt.overdispersion(reference, rates))
    )
    observed = 0
    expected = 0.0
    beyond = 0
    weight: dict[int, float] = {}
    for cohort in target:
        for a in range(cohort.horizon + 1):
            count = cohort.counts.get(a, 0)
            if a > reach or a not in rates:
                beyond += count
                continue
            observed += count
            expected += cohort.units * rates[a].rate
            weight[a] = weight.get(a, 0.0) + cohort.units
    variance_e = sum(
        weight[a] ** 2 * rates[a].records / rates[a].units ** 2
        for a in weight
        if rates[a].units > 0
    )
    target_units = sum(one.units for one in target)
    target_records = sum(one.inside for one in target)
    reference_units = sum(one.units for one in reference)
    reference_records = sum(one.inside for one in reference)
    crude = None
    if target_units > 0 and reference_units > 0 and reference_records > 0:
        crude = (target_records / target_units) / (reference_records / reference_units)
    ratio = low = high = None
    if expected > 0:
        relative_e = variance_e / expected**2
        if observed > 0:
            ratio = observed / expected
            spread = Z * math.sqrt(phi * (1.0 / observed + relative_e))
            low, high = ratio * math.exp(-spread), ratio * math.exp(spread)
        else:
            ratio, low = 0.0, 0.0
            upper = _numeric.chi2_ppf(0.975, 2) / 2 / expected
            high = upper * math.exp(Z * math.sqrt(phi * relative_e)) * math.sqrt(phi)
    verdict: Verdict = "unclear"
    if low is not None and low > 1.0:
        verdict = "higher"
    elif high is not None and high < 1.0:
        verdict = "lower"
    return Estimate(
        observed=observed,
        expected=expected,
        target_units=target_units,
        target_records=target_records,
        reference_units=reference_units,
        reference_records=reference_records,
        ratio=ratio,
        low=low,
        high=high,
        crude=crude,
        dispersion=phi,
        verdict=verdict,
        new=expected <= 0 and observed > 0,
        beyond=beyond,
    )


# --- 어댑터 --------------------------------------------------------------------------


registry.register(registry.Recipe(NAME, LABEL, sprt.available))


def _row(key: str | None, label: str, one: Estimate) -> RatioRowOut:
    return RatioRowOut(
        key=key,
        label=label,
        observed=one.observed,
        expected=one.expected,
        ratio=one.ratio,
        low=one.low,
        high=one.high,
        verdict=one.verdict,
        crude=one.crude,
        target_units=one.target_units,
        target_records=one.target_records,
        target_rate=(one.target_records / one.target_units if one.target_units else None),
        reference_units=one.reference_units,
        reference_records=one.reference_records,
        reference_rate=(
            one.reference_records / one.reference_units if one.reference_units else None
        ),
        new=one.new,
    )


def _split(frame: query.Frame, by: str, value: str | None, shared_units: bool) -> query.Frame:
    """`by` 로 묶어 읽은 틀에서 값 하나의 틀 — 셀은 그 값의 것만(기준을 떼고), 분모는 `by` 가
    분모 짝이면 그 값의 대수, 아니면 모델의 대수 그대로(모든 기기가 모든 값에 노출)."""
    cells = [
        replace(cell, dims={key: v for key, v in cell.dims.items() if key != by})
        for cell in frame.cells
        if cell.dims.get(by) == value
    ]
    den = frame.denominator
    if den is not None and not shared_units:
        at = den.on.index(by)
        den = replace(
            den,
            on=[name for name in den.on if name != by],
            values={
                (key[:at] + key[at + 1 :], when): units
                for (key, when), units in den.values.items()
                if key[at] == value
            },
        )
    return replace(frame, cells=cells, denominator=den)


def _values(frame: query.Frame, by: str, shared_units: bool) -> set[str | None]:
    found = {cell.dims.get(by) for cell in frame.cells}
    den = frame.denominator
    if den is not None and not shared_units and by in den.on:
        at = den.on.index(by)
        found |= {key[at] for (key, _when), units in den.values.items() if units}
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
    by: str | None = None,
) -> RatioOut:
    reason = sprt.available(built)
    if reason is not None:
        raise common.refuse(28, reason)
    den_in = built.spec.denominator
    assert den_in is not None and built.cohort is not None
    grain = built.cohort.grain
    dim = dim or den_in.on[0]
    model_dim = common.dim_of(built, dim)
    if dim not in den_in.on:
        raise common.refuse(
            29,
            f"「{model_dim.axis.label}」 은 분모 짝(on)에 없어 모델마다의 대수를 모릅니다 — "
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
        reference = sprt._follow(db, user, target, reference_via)
    if reference == target:
        raise common.refuse(29, "새 모델과 전작이 같습니다.")
    by_dim = None
    if by is not None:
        by_dim = common.dim_of(built, by)
        if by == dim:
            raise common.refuse(
                29,
                "값마다 볼 기준이 모델 기준과 같습니다 — 시장 · 증상처럼 다른 기준을 줍니다.",
            )
        if by in ask.filters:
            raise common.refuse(
                29, f"d.{by} 로 거른 기준으로는 값마다 볼 수 없습니다 — 거르기를 뺍니다."
            )
    # 분모가 `by` 와 짝이면(시장) 값마다의 대수, 아니면(증상) 모델의 대수를 모든 값이 함께
    # 쓴다.
    shared_units = by is None or by not in den_in.on

    def frame_of(value: str, dims: list[str]) -> query.Frame:
        one = replace(
            ask,
            dims=dims,
            by=("cohort", "age"),
            age_from=0,
            filters={**ask.filters, dim: value},
        )
        common.require_exact_counts(built, one)
        found = query.frame(db, user, metric, built, one)
        common.require_whole(found)
        return found

    excluded: dict[str, int] = {}
    reference_excluded: dict[str, int] = {}
    target_frame = frame_of(target, [])
    reference_frame = frame_of(reference, [])
    target_cohorts = life.cohorts_of(target_frame, grain, excluded, within_units=False)
    reference_cohorts = life.cohorts_of(
        reference_frame, grain, reference_excluded, within_units=False
    )
    if not sprt.reference_rates(reference_cohorts):
        raise common.refuse(
            29, "전작의 닫힌 코호트가 없습니다 — 판매 대수와 닫힌 경과가 있어야 견줍니다."
        )
    overall = estimate(target_cohorts, reference_cohorts)
    excluded["beyond_reference"] = overall.beyond
    names = common.given_labels(db, user, built, dim, (target, reference))
    caveats = common.Caveats()

    items: list[RatioRowOut] = []
    if by is not None:
        target_by = frame_of(target, [by])
        reference_by = frame_of(reference, [by])
        values = _values(target_by, by, shared_units) | _values(reference_by, by, shared_units)
        labels = query.labels_for(db, built, [by], [*target_by.cells, *reference_by.cells])
        scratch: dict[str, int] = {}
        for value in values:
            mine = life.cohorts_of(
                _split(target_by, by, value, shared_units), grain, scratch, within_units=False
            )
            theirs = life.cohorts_of(
                _split(reference_by, by, value, shared_units),
                grain,
                scratch,
                within_units=False,
            )
            # 두 모델 다 기록이 없고 새 모델이 팔리지도 않은 값은 줄이 아니다.
            if (
                not any(one.inside for one in mine)
                and not any(one.inside for one in theirs)
                and not any(one.units for one in mine)
            ):
                continue
            # 과분산은 전체에서 잰 것을 쓴다 — 값 하나의 전작 코호트는 대개 너무 적어 못 잰다.
            got = estimate(mine, theirs, dispersion=overall.dispersion)
            items.append(_row(value, query.label_of(labels, by, value), got))
        # 「높다」 와 전작에 없던 값(새로 생긴 것)이 먼저 — 순차 검정 훑기와 같은 차례.
        order = {"higher": 0, "unclear": 1, "lower": 2}
        items.sort(
            key=lambda one: (
                0 if one.new else order[one.verdict],
                -(one.ratio if one.ratio is not None else (math.inf if one.new else -1.0)),
                -one.observed,
            )
        )
        caveats.add(
            "per_value_intervals",
            f"값 {len(items)}개의 구간을 따로 냈습니다 — 함께 보면 몇 개는 우연히 1 을 "
            "벗어납니다(다중 비교 보정 없음). 「높다」 가 많지 않으면 하나하나를 결론으로 "
            "옮기지 않습니다.",
            level="info",
        )
        if shared_units:
            assert by_dim is not None
            caveats.add(
                "shared_exposure",
                f"대수는 「{by_dim.axis.label}」 와 상관없이 모델의 판매 대수입니다 — 모든 "
                "기기가 "
                "모든 값에 노출된다고 봅니다.",
                level="info",
            )
        if any(one.new for one in items):
            caveats.add(
                "new_values",
                "전작에 없던 값은 기대가 0 이라 배율이 없습니다(`new`).",
                level="info",
            )
    _caveats(caveats, overall, excluded, reference_excluded)
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
            "by": by,
        },
        caveats=caveats,
        excluded={
            **excluded,
            **{f"reference_{key}": value for key, value in reference_excluded.items()},
        },
    )
    return RatioOut(
        **head,
        dim=dim,
        dim_label=model_dim.axis.label,
        target=target,
        target_label=query.label_of(names, dim, target),
        reference=reference,
        reference_label=query.label_of(names, dim, reference),
        level=0.95,
        dispersion=overall.dispersion,
        overall=_row(None, "전체", overall),
        by=by,
        by_label=by_dim.axis.label if by_dim is not None else None,
        items=items,
    )


def _caveats(
    caveats: common.Caveats,
    overall: Estimate,
    excluded: dict[str, int],
    reference_excluded: dict[str, int],
) -> None:
    if overall.expected <= 0 and overall.observed == 0:
        caveats.add(
            "too_early",
            "새 모델의 닫힌 코호트가 없거나 전작과 겹치는 경과가 없습니다 — 판매 대수와 닫힌 "
            "경과가 쌓이면 봅니다.",
        )
    elif overall.expected < 5:
        caveats.add(
            "small_expected",
            f"기대 건수가 {overall.expected:.1f}건뿐입니다 — 구간이 넓습니다.",
            level="info",
        )
    if overall.dispersion > 1.5:
        caveats.add(
            "overdispersion",
            f"전작의 건수가 포아송의 {overall.dispersion:.1f}배로 흔들립니다 — 구간을 그만큼 "
            "넓혔습니다(준-포아송).",
            level="info",
        )
    if excluded.get("beyond_reference"):
        caveats.add(
            "beyond_reference",
            "전작이 그 경과까지 안 가 견주지 못한 새 모델의 기록은 배율에서 뺐습니다(단순 "
            "배율에는 "
            "든다).",
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
        "unclear_is_not_same",
        "「unclear」 는 구간이 1 을 품는다는 뜻이지 「전작과 같다」 가 아닙니다.",
        level="info",
    )
    caveats.add(
        "records_not_units",
        "기록 수를 고장 대수로 봅니다 — 재방문이 두 모델에서 다르게 섞이면 그만큼 틀립니다.",
        level="info",
    )
