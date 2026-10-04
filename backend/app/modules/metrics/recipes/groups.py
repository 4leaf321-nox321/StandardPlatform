"""⑤ 집단 비교 — 색상 · 용량 · 통신사(SKU) · 공장 · 기본 모델마다 불량률이 다른가(ADR 0020).

## 집단마다의 비율

기준 하나(분모 짝에 있어야 한다 — 집단마다 대수를 안다)의 값마다 닫힌 부분군의 건수 c_g 와 대수
n_g 를 더한다 — 관리도와 같은 줄이다(기간 축, 또는 코호트마다 출고 K 기간 안). 전체 비율
r̄ = Σc / Σn.

## 다른가 — 이질성과 집단마다의 검정

- 이질성: 포아송 χ² = Σ (c_g - n_g r̄)² / (n_g r̄), 자유도 G - 1 — 「집단 사이에 우연보다 큰
  차이가 있나」.
- 집단마다: 그 집단 대 나머지. 둘을 합친 건수 중 그 집단의 몫이 대수의 몫 n_g / N 을
  따르나(조건부 이항 정확 검정, 양쪽). 집단이 여럿이라 BH 로 거짓 발견율을 맞춘다(q). q < 0.05
  만 「다르다」.

## 작은 집단 과장 줄이기 — 경험적 베이즈(감마-포아송)

참 비율 θ_g 가 감마(a, b) 를 따른다고 보고, 집단 사이의 참 흔들림 τ² 를 적률로 잰다:

    τ² = max(0, (Σ n_g (r_g - r̄)² - (G - 1) r̄) / (N - Σ n_g² / N)),   a = r̄² / τ²,  b = r̄ / τ²

줄인 비율 = (c_g + a) / (n_g + b) — 대수가 작은 집단일수록 전체 쪽으로 끌린다(줄인 정도
b / (n_g + b)). 95% 구간은 감마(c_g + a, n_g + b) 의 분위수. τ² 가 0 이면 차이가 우연의
흔들림보다 크지 않다 — 모두 전체 비율로 줄인다.

순위는 줄인 비율로 세운다 — 대수 100 대에 2 건인 집단의 그대로 비율(2%)을 대수 10 만 대에 1,000
건(1%)보다 「나쁘다」 고 읽지 않게.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, registry, series
from app.modules.metrics.recipes.assoc import benjamini_hochberg
from app.modules.metrics.recipes.schemas import GroupRowOut, GroupsOut

NAME = "groups"
LABEL = "집단 비교"
METHOD = "포아송 이질성 χ² · 조건부 이항 정확 검정 · BH · 감마-포아송 경험적 베이즈(적률) v1"
MAX_GROUPS = 500
COMPACT_ROWS = 15
Q_LIMIT = 0.05


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class Compared:
    rate: float
    shrunk: float
    low: float | None
    high: float | None
    shrinkage: float
    p_value: float
    q_value: float = 1.0


@dataclass
class Comparison:
    pooled: float
    chi2: float
    df: int
    p_value: float
    tau2: float
    rows: list[Compared]


def compare(counts: Sequence[float], exposures: Sequence[float]) -> Comparison | None:
    """집단마다 건수 · 대수 → 이질성, 집단마다 정확 검정(BH), 줄인 비율. 대수가 0 인 집단은
    부르는 쪽이 뺀다. 건수가 전부 0 이면 견줄 것이 없다(None)."""
    c = [float(one) for one in counts]
    n = [float(one) for one in exposures]
    total_c, total_n = sum(c), sum(n)
    if total_n <= 0 or total_c <= 0 or len(c) < 2:
        return None
    pooled = total_c / total_n
    chi2 = sum((ci - ni * pooled) ** 2 / (ni * pooled) for ci, ni in zip(c, n, strict=True))
    df = len(c) - 1
    p_value = _numeric.chi2_sf(chi2, df)
    spread = sum(ni * (ci / ni - pooled) ** 2 for ci, ni in zip(c, n, strict=True))
    bottom = total_n - sum(ni * ni for ni in n) / total_n
    tau2 = max(0.0, (spread - df * pooled) / bottom) if bottom > 0 else 0.0
    rows: list[Compared] = []
    for ci, ni in zip(c, n, strict=True):
        tested = _numeric.binom_two_sided(round(ci), round(total_c), ni / total_n)
        if tau2 > 0:
            a, b = pooled * pooled / tau2, pooled / tau2
            rows.append(
                Compared(
                    rate=ci / ni,
                    shrunk=(ci + a) / (ni + b),
                    low=_numeric.gamma_ppf(0.025, ci + a, ni + b),
                    high=_numeric.gamma_ppf(0.975, ci + a, ni + b),
                    shrinkage=b / (ni + b),
                    p_value=tested,
                )
            )
        else:
            rows.append(
                Compared(
                    rate=ci / ni,
                    shrunk=pooled,
                    low=None,
                    high=None,
                    shrinkage=1.0,
                    p_value=tested,
                )
            )
    for one, q in zip(rows, benjamini_hochberg([one.p_value for one in rows]), strict=True):
        one.q_value = q
    return Comparison(pooled, chi2, df, p_value, tau2, rows)


# --- 셀 어댑터 -----------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    den = built.spec.denominator
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 집단마다 건수 / 대수를 견줍니다."
    if den is None or not den.on:
        return (
            "분모(대수)를 기준과 짝지은(on) 지표에서만 됩니다 — 집단마다 대수를 알아야 비율을 "
            "견줍니다."
        )
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
    dim: str | None = None,
    axis: series.Axis | None = None,
    window: int = 3,
    compact: bool = False,
) -> GroupsOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(35, reason)
    den_in = built.spec.denominator
    assert den_in is not None
    dim = dim or den_in.on[0]
    target = common.dim_of(built, dim)
    if dim not in den_in.on:
        raise common.refuse(
            35,
            f"「{target.axis.label}」 은 분모 짝(on)에 없어 집단마다의 대수를 모릅니다 — "
            f"{', '.join(den_in.on)} 중 하나로 견줍니다(SKU 로 견주려면 SKU 별 판매 대수를 "
            "분모에 넣습니다).",
        )
    if dim in ask.filters:
        raise common.refuse(
            35, f"d.{dim} 로 거른 기준으로는 견줄 수 없습니다 — 거르기를 뺍니다."
        )
    found = series.read(
        db, user, metric, built, ask, axis=axis, window=window, split=dim, limit=MAX_GROUPS
    )
    if found.den is None:
        raise common.refuse(
            36,
            "분모가 이 축과 짝이 아니어서 집단마다의 대수가 없습니다 — 분모가 코호트와 짝이면 "
            "axis=cohort, 기간과 짝이면 axis=period 로 봅니다.",
        )
    caveats = common.Caveats()
    series.caveats(found, caveats)
    per = found.per
    totals: list[tuple[series.Series, float, float]] = []
    for line in found.series:
        closed = [one for one in line.subgroups if one.closed and one.exposure > 0]
        count = float(sum(one.count for one in closed))
        exposure = float(sum(one.exposure for one in closed))
        if exposure > 0:
            totals.append((line, count, exposure))
    compared = compare([one[1] for one in totals], [one[2] for one in totals])
    rows: list[GroupRowOut] = []
    if compared is not None:
        for (line, count, exposure), one in zip(totals, compared.rows, strict=True):
            flag: Literal["high", "low"] | None = None
            if one.q_value < Q_LIMIT:
                flag = "high" if one.rate > compared.pooled else "low"
            rows.append(
                GroupRowOut(
                    key=line.key,
                    label=line.label,
                    count=int(count),
                    exposure=exposure,
                    rate=one.rate * per,
                    shrunk=one.shrunk * per,
                    shrunk_low=one.low * per if one.low is not None else None,
                    shrunk_high=one.high * per if one.high is not None else None,
                    shrinkage=one.shrinkage,
                    ratio=one.shrunk / compared.pooled if compared.pooled > 0 else None,
                    p_value=one.p_value,
                    q_value=one.q_value,
                    flag=flag,
                    drill=query.drill(
                        built,
                        query.Cell(
                            {dim: line.key}, None, None, None, int(count), 0, None, None, None
                        ),
                        by=(),
                        ask=ask,
                    ),
                )
            )
        rows.sort(key=lambda row: (-(row.shrunk or 0.0), row.label))
        if compared.tau2 <= 0:
            caveats.add(
                "no_spread",
                "집단 사이 차이가 우연의 흔들림보다 크지 않습니다 — 모두 전체 비율로 "
                "줄였습니다.",
                level="info",
            )
        pulled = sum(1 for row in rows if row.shrinkage > 0.5)
        if compared.tau2 > 0 and pulled:
            caveats.add(
                "small_groups",
                f"대수가 작아 절반 넘게 전체 쪽으로 줄인 집단이 {pulled}개입니다 — 그대로 "
                "비율은 우연으로 크게 흔들리니 줄인 비율로 말합니다.",
                level="info",
                count=pulled,
            )
        caveats.add(
            "multiple_testing",
            f"집단 {len(rows)}개를 함께 검정해 BH 로 거짓 발견율을 맞췄습니다(q < {Q_LIMIT} "
            "만 「다르다」).",
            level="info",
        )
    else:
        caveats.add("nothing_to_compare", "견줄 집단이 둘 이상 · 건수가 있어야 합니다.")
    caveats.add(
        "association",
        "비율의 차이는 그 집단에 모인 것이지 원인이 아닙니다 — 판매 시기 · 쓰임이 다를 수 "
        "있습니다.",
        level="info",
    )
    shown = rows
    if compact and len(rows) > COMPACT_ROWS:
        flagged_rows = [row for row in rows if row.flag]
        rest = [row for row in rows if not row.flag][
            : max(0, COMPACT_ROWS - len(flagged_rows))
        ]
        shown = sorted(flagged_rows + rest, key=lambda row: (-(row.shrunk or 0.0), row.label))
    head = common.header(
        db,
        user,
        metric,
        built,
        found.frame,
        recipe=NAME,
        method=METHOD,
        params={
            "dim": dim,
            "axis": found.axis,
            "window": window if found.axis == "cohort" else None,
            "compact": compact,
        },
        caveats=caveats,
        excluded=found.excluded,
    )
    return GroupsOut(
        **head,
        dim=dim,
        dim_label=target.axis.label,
        axis=found.axis,
        window=window if found.axis == "cohort" else None,
        per=per,
        pooled=compared.pooled * per if compared is not None else None,
        groups=len(rows),
        heterogeneity_chi2=compared.chi2 if compared is not None else None,
        heterogeneity_df=compared.df if compared is not None else 0,
        heterogeneity_p=compared.p_value if compared is not None else None,
        spread=(compared.tau2**0.5) / compared.pooled
        if compared is not None and compared.pooled > 0
        else None,
        flagged=sum(1 for row in rows if row.flag),
        rows=shown,
        other_groups=found.other_groups,
    )
