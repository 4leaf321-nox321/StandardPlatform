"""⑩ 계절 · 변화점 — 계절을 빼고 보면 수준이 언제 바뀌었나(ADR 0014).

## 모형

부분군 t 의 건수 y_t, 대수 e_t(분모가 없으면 1), 계절 s(t)(월이면 12달, 분기면 4, 일이면
요일). 기대 건수 μ_t = λ_k · e_t · S_s(t) — λ_k 는 t 가 든 구간 k 의 수준, S 는 계절 지수
(로그 평균 0).

1. **변화점** — 계절을 안 상태에서 최적 분할. 구간의 비용은 포아송 이탈도
   2 Σ y log(y / μ̂) 를 과분산 φ 로 나눈 것, 변화점 하나에 벌점 3 · ln n, 구간은 3점 이상.
2. **계절 지수** — 구간 수준을 안 상태에서 log((y + 0.5) / (λ e + 0.5)) 의 계절별 **중앙값**.
3. 둘을 번갈아(분할이 안 바뀔 때까지). 계절 지수는 두 해 이상 볼 때만 잡고, 구간 수준을 안
   상태에서 계절이 이탈도를 유의하게 줄이지 않으면(χ², p ≥ 0.05) 버린다.

과분산 φ 는 표준 잔차의 **차분**의 MAD 로 잰다 — 수준이 바뀐 곳은 차분 하나에만 들어 φ 를
부풀리지 않는다. φ 는 1 아래로 줄이지 않는다. 큰 건수에서 포아송 그대로면 작은 흔들림이 모두
변화점이 된다.

STL 은 쓰지 않는다 — 추세를 매끈하게 맞추는 방법이라 계단을 비탈로 뭉개 변화점을 잃는다.

## 읽는 법

변화점마다 앞 · 뒤 수준(계절을 뺀 비율)과 그 비(準포아송 구간). 새 수준이 6점이 안 되면
「잠정」 — 끝의 몇 점은 우연으로도 튄다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, registry, series
from app.modules.metrics.recipes.schemas import (
    ChangeOut,
    ChangesOut,
    ChangesPointOut,
    SeasonOut,
    SegmentOut,
)
from app.modules.objects import axes

NAME = "changes"
LABEL = "계절 · 변화점"
METHOD = "중앙값 계절 지수 + 준-포아송 최적 분할(벌점 3·ln n, 최소 3점) v1"
MIN_SEGMENT = 3
#: 새 수준이 이만큼 안 되면 잠정.
SETTLED = 2 * MIN_SEGMENT
#: 이보다 짧으면 변화점을 찾지 않는다.
MIN_POINTS = 2 * MIN_SEGMENT + 2
MAX_ITERATIONS = 10
Z95 = 1.959963984540054
WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

Vector = NDArray[np.float64]
Numbers = Sequence[float] | Vector


# --- 순수 함수 -----------------------------------------------------------------------


def season_length(grain: str) -> int:
    return {"month": 12, "quarter": 4, "day": 7}.get(grain, 1)


def season_of(when: date, grain: str) -> int:
    if grain == "month":
        return when.month - 1
    if grain == "quarter":
        return (when.month - 1) // 3
    if grain == "day":
        return when.weekday()
    return 0


def season_label(index: int, grain: str) -> str:
    if grain == "month":
        return f"{index + 1}월"
    if grain == "quarter":
        return f"{index + 1}분기"
    if grain == "day":
        return WEEKDAYS[index]
    return ""


def _xlogy(y: Vector, x: Vector) -> Vector:
    """y · log(x), y = 0 이면 0."""
    out = np.zeros_like(y)
    positive = y > 0
    out[positive] = y[positive] * np.log(x[positive])
    return out


def partition(
    y: Numbers,
    x: Numbers,
    *,
    penalty: float,
    dispersion: float = 1.0,
    min_size: int = MIN_SEGMENT,
) -> list[int]:
    """최적 분할 — 구간의 끝(배타) 목록, 마지막은 n. 비용 = 포아송 이탈도 / φ + 벌점 x 변화점.

    x_t 는 기대 노출(대수 x 계절 지수) — 구간의 수준은 Σy / Σx. 앞쪽 합으로 구간 비용이
    O(1) 이라 전체가 O(n²) 이다(n 은 수백 기간)."""
    y_arr = np.asarray(y, dtype=np.float64)
    x_arr = np.asarray(x, dtype=np.float64)
    n = int(y_arr.size)
    if n < min_size:
        return [n] if n else []
    zero = np.zeros(1)
    total_y = np.concatenate((zero, np.cumsum(y_arr)))
    total_x = np.concatenate((zero, np.cumsum(x_arr)))
    ylogy = np.concatenate((zero, np.cumsum(_xlogy(y_arr, y_arr))))
    ylogx = np.concatenate((zero, np.cumsum(_xlogy(y_arr, x_arr))))

    best = np.full(n + 1, np.inf)
    back = np.zeros(n + 1, dtype=np.int64)
    best[0] = -penalty
    for b in range(min_size, n + 1):
        # 앞 구간의 끝 a — 0 이거나, 앞 구간도 최소 길이를 채운 자리.
        starts = np.concatenate(([0], np.arange(min_size, b - min_size + 1)))
        count = total_y[b] - total_y[starts]
        exposure = total_x[b] - total_x[starts]
        inside = (ylogy[b] - ylogy[starts]) - (ylogx[b] - ylogx[starts])
        safe = (count > 0) & (exposure > 0)
        level = np.zeros_like(count)
        level[safe] = count[safe] * np.log(count[safe] / exposure[safe])
        cost = np.maximum(0.0, 2.0 * (inside - level)) / dispersion
        values = best[starts] + cost + penalty
        pick = int(np.argmin(values))
        best[b] = values[pick]
        back[b] = starts[pick]
    ends: list[int] = []
    b = n
    while b > 0:
        ends.append(b)
        b = int(back[b])
    return ends[::-1]


def dispersion_of(y: Vector, mu: Vector) -> float:
    """φ — 표준 잔차의 차분의 MAD. 수준이 바뀐 곳은 차분 하나에만 든다. 1 아래로 안 줄인다."""
    keep = mu > 0
    z = (y[keep] - mu[keep]) / np.sqrt(mu[keep])
    if z.size < 3:
        return 1.0
    steps = np.diff(z)
    mad = float(np.median(np.abs(steps - np.median(steps))))
    sigma = 1.4826 * mad / math.sqrt(2.0)
    return max(1.0, sigma * sigma)


def pearson_of(y: Vector, mu: Vector, parameters: int) -> float:
    """φ — 맞춘 모형의 피어슨 χ² / 자유도. 차분의 MAD 는 짧은 줄에서 작게 흔들려(작게 나오면
    헛 변화점이 는다) 둘 중 큰 것을 쓴다. 헛 구간이 잔차를 줄여도 다음 차례에 MAD 쪽이
    받친다."""
    keep = mu > 0
    free = int(keep.sum()) - parameters
    if free < 1:
        return 1.0
    z = (y[keep] - mu[keep]) / np.sqrt(mu[keep])
    return max(1.0, float((z * z).sum()) / free)


@dataclass
class Fit:
    ends: list[int]
    levels: list[float]
    """구간마다의 수준 — 대수 하나당(계절을 뺀) 건수."""
    seasonal: list[float] | None
    seasonal_p_value: float | None
    dispersion: float
    penalty: float
    fitted: list[float]
    iterations: int

    @property
    def starts(self) -> list[int]:
        return [0, *self.ends[:-1]]

    def level_at(self) -> list[float]:
        out: list[float] = []
        for start, end, level in zip(self.starts, self.ends, self.levels, strict=True):
            out.extend([level] * (end - start))
        return out


def _levels(y: Vector, x: Vector, ends: list[int]) -> list[float]:
    out: list[float] = []
    start = 0
    for end in ends:
        exposure = float(x[start:end].sum())
        out.append(float(y[start:end].sum()) / exposure if exposure > 0 else 0.0)
        start = end
    return out


def _expand(levels: list[float], ends: list[int]) -> Vector:
    out = np.zeros(ends[-1] if ends else 0)
    start = 0
    for end, level in zip(ends, levels, strict=True):
        out[start:end] = level
        start = end
    return out


def _seasonal(y: Vector, e: Vector, level: Vector, seasons: list[int], m: int) -> Vector:
    ratio = np.log((y + 0.5) / (level * e + 0.5))
    index = np.zeros(m)
    for s in range(m):
        chosen = ratio[[i for i, one in enumerate(seasons) if one == s]]
        index[s] = float(np.median(chosen)) if chosen.size else 0.0
    index -= index.mean()
    return np.asarray(np.exp(index), dtype=np.float64)


def rolling_level(y: Vector, e: Vector, width: int) -> Vector:
    """점마다 그 둘레 `width` 점의 비율 중앙값 — 끝에서는 창을 안으로 민다. 중앙값이라
    계단 둘레에서도 비탈이 아니라 계단으로 남는다."""
    n = int(y.size)
    rate = np.where(e > 0, y / np.where(e > 0, e, 1.0), 0.0)
    width = min(width, n)
    half = width // 2
    out = np.zeros(n)
    for t in range(n):
        start = min(max(0, t - half), n - width)
        out[t] = float(np.median(rate[start : start + width]))
    return out


def _deviance(y: Vector, mu: Vector) -> float:
    return float(2.0 * (_xlogy(y, y) - _xlogy(y, np.maximum(mu, 1e-300)) - (y - mu)).sum())


@dataclass
class _Round:
    ends: list[int]
    index: Vector
    phi: float
    iterations: int


def _alternate(
    y: Vector,
    e: Vector,
    seasons: list[int],
    index: Vector,
    *,
    use_season: bool,
    penalty: float,
    min_size: int,
) -> _Round:
    """계절 지수와 변화점을 번갈아 — 분할이 안 바뀔 때까지."""
    m = int(index.size)
    ends = [int(y.size)]
    phi = 1.0
    iterations = 0
    previous: list[int] | None = None
    for iterations in range(1, MAX_ITERATIONS + 1):  # noqa: B007 - 몇 번 돌았나를 말한다
        x = e * index[seasons] if use_season else e
        mu = _expand(_levels(y, x, ends), ends) * x
        phi = max(dispersion_of(y, mu), pearson_of(y, mu, len(ends) + (m - 1) * use_season))
        ends = partition(y, x, penalty=penalty, dispersion=phi, min_size=min_size)
        if use_season:
            index = _seasonal(y, e, _expand(_levels(y, x, ends), ends), seasons, m)
        if ends == previous:
            break
        previous = ends
    return _Round(ends, index, phi, iterations)


def _objective(
    y: Vector, e: Vector, seasons: list[int], one: _Round, phi: float, penalty: float
) -> float:
    x = e * one.index[seasons]
    return _deviance(y, _expand(_levels(y, x, one.ends), one.ends) * x) / phi + penalty * (
        len(one.ends) - 1
    )


def detect(
    counts: Sequence[float],
    exposures: Sequence[float],
    seasons: Sequence[int],
    m: int,
    *,
    min_size: int = MIN_SEGMENT,
) -> Fit:
    """닫힌 부분군을 차례로 — 계절 지수와 변화점을 번갈아 맞춘다.

    계절이 있으면 처음 계절 지수를 두 가지로 잡아 각각 맞추고, 벌점 붙은 이탈도가 작은 쪽을
    고른다 — 한 주기 너비의 움직이는 중앙값을 수준으로 본 것(주기 한가운데의 계단에 강하다)과
    수준 하나로 본 것(주기 경계의 계단을 더 정확히 짚는다). 계절 없이 먼저 나누면 계절의
    오르내림을 수준으로 잘라 그 자리에 머문다."""
    y = np.asarray(counts, dtype=np.float64)
    e = np.asarray(exposures, dtype=np.float64)
    n = int(y.size)
    penalty = 3.0 * math.log(max(n, 2))
    season_list = list(seasons)
    use_season = m > 1 and n >= 2 * m
    flat = np.ones(max(m, 1))
    if use_season:
        rounds = [
            _alternate(
                y,
                e,
                season_list,
                _seasonal(y, e, level, season_list, m),
                use_season=True,
                penalty=penalty,
                min_size=min_size,
            )
            for level in (rolling_level(y, e, m + 1), _expand(_levels(y, e, [n]), [n]))
        ]
        common_phi = max(one.phi for one in rounds)
        chosen = min(
            rounds, key=lambda one: _objective(y, e, season_list, one, common_phi, penalty)
        )
    else:
        chosen = _alternate(
            y, e, season_list, flat, use_season=False, penalty=penalty, min_size=min_size
        )
    p_value: float | None = None
    if use_season:
        x = e * chosen.index[season_list]
        with_season = _expand(_levels(y, x, chosen.ends), chosen.ends) * x
        without = _expand(_levels(y, e, chosen.ends), chosen.ends) * e
        drop = (_deviance(y, without) - _deviance(y, with_season)) / chosen.phi
        p_value = _numeric.chi2_sf(drop, m - 1) if drop > 0 else 1.0
        if p_value >= 0.05:
            use_season = False
            chosen = _alternate(
                y, e, season_list, flat, use_season=False, penalty=penalty, min_size=min_size
            )
    x = e * chosen.index[season_list] if use_season else e
    levels = _levels(y, x, chosen.ends)
    fitted = _expand(levels, chosen.ends) * x
    return Fit(
        ends=chosen.ends,
        levels=levels,
        seasonal=[float(one) for one in chosen.index] if use_season else None,
        seasonal_p_value=p_value,
        dispersion=chosen.phi,
        penalty=penalty,
        fitted=[float(one) for one in fitted],
        iterations=chosen.iterations,
    )


def ratio_interval(
    before: float, after: float, count_before: float, count_after: float, phi: float
) -> tuple[float, float] | None:
    """뒤 수준 / 앞 수준의 구간 — 준-포아송, 로그 척도."""
    if before <= 0 or after <= 0 or count_before <= 0 or count_after <= 0:
        return None
    spread = Z95 * math.sqrt(phi * (1.0 / count_before + 1.0 / count_after))
    center = math.log(after / before)
    return math.exp(center - spread), math.exp(center + spread)


# --- 어댑터 --------------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 변화점은 건수 · 비율의 수준을 봅니다."
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
    axis: series.Axis | None = None,
    window: int = 3,
    compact: bool = False,
) -> ChangesOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(27, reason)
    found = series.read(
        db, user, metric, built, ask, axis=axis, window=window, split=None, limit=1
    )
    caveats = common.Caveats()
    series.caveats(found, caveats)
    grain = found.grain
    per = found.per
    groups = found.series[0].subgroups if found.series else []
    closed = [one for one in groups if one.closed and one.exposure > 0]
    m = season_length(grain)
    fit: Fit | None = None
    if len(closed) >= MIN_POINTS:
        fit = detect(
            [one.count for one in closed],
            [one.exposure for one in closed],
            [season_of(one.when, grain) for one in closed],
            m,
        )
    else:
        caveats.add(
            "too_short",
            f"닫힌 부분군이 {len(closed)}개라 변화점을 찾지 않았습니다 — {MIN_POINTS}개 "
            "이상이어야 합니다.",
        )
    if fit is not None and m > 1 and fit.seasonal is None:
        caveats.add(
            "no_seasonality",
            "계절 지수를 쓰지 않았습니다 — 두 해가 안 되거나, 계절이 수준을 유의하게 "
            "설명하지 않습니다.",
            level="info",
        )
    if fit is not None and fit.dispersion > 1.5:
        caveats.add(
            "overdispersion",
            f"건수의 흔들림이 포아송의 {fit.dispersion:.1f}배입니다 — 그만큼 작은 변화는 "
            "변화점으로 읽지 않습니다.",
            level="info",
        )
    changes: list[ChangeOut] = []
    segments: list[SegmentOut] = []
    level_of: dict[date, float] = {}
    season_index: dict[date, float] = {}
    if fit is not None:
        level_at = fit.level_at()
        for index, one in enumerate(closed):
            level_of[one.when] = level_at[index] * per
            season_index[one.when] = (
                fit.seasonal[season_of(one.when, grain)] if fit.seasonal else 1.0
            )
        counts = [one.count for one in closed]
        for k, (start, end) in enumerate(zip(fit.starts, fit.ends, strict=True)):
            first, last = closed[start].when, closed[end - 1].when
            segments.append(
                SegmentOut(
                    start=first.isoformat(),
                    stop=axes.next_period(last, grain).isoformat(),
                    label=f"{axes.period_label(first.isoformat(), grain)} ~ "
                    f"{axes.period_label(last.isoformat(), grain)}",
                    level=fit.levels[k] * per,
                    count=int(sum(counts[start:end])),
                    points=end - start,
                )
            )
            if k == 0:
                continue
            before, after = fit.levels[k - 1], fit.levels[k]
            band = ratio_interval(
                before,
                after,
                sum(counts[fit.starts[k - 1] : start]),
                sum(counts[start:end]),
                fit.dispersion,
            )
            provisional = end - start < SETTLED
            changes.append(
                ChangeOut(
                    at=first.isoformat(),
                    label=axes.period_label(first.isoformat(), grain),
                    before=before * per,
                    after=after * per,
                    ratio=after / before if before > 0 else None,
                    ratio_ci=list(band) if band is not None else None,
                    provisional=provisional,
                )
            )
        if any(one.provisional for one in changes):
            caveats.add(
                "provisional_change",
                f"끝 쪽의 변화점은 새 수준이 {SETTLED}점이 안 돼 잠정입니다 — 몇 달 더 보고 "
                "판단합니다.",
            )
    points: list[ChangesPointOut] = []
    shown = groups[-24:] if compact else groups
    for one in shown:
        rate = found.rate(one)
        index_value = season_index.get(one.when)
        points.append(
            ChangesPointOut(
                when=one.when.isoformat(),
                label=axes.period_label(one.when.isoformat(), grain),
                count=one.count,
                exposure=one.exposure if found.den is not None else None,
                rate=rate,
                seasonal=index_value,
                adjusted=rate / index_value if rate is not None and index_value else None,
                level=level_of.get(one.when),
                closed=one.closed,
                drill=series.drill(found, built, None, one),
            )
        )
    head = common.header(
        db,
        user,
        metric,
        built,
        found.frame,
        recipe=NAME,
        method=METHOD,
        params={
            "axis": found.axis,
            "window": window if found.axis == "cohort" else None,
            "compact": compact,
        },
        caveats=caveats,
        excluded=found.excluded,
    )
    return ChangesOut(
        **head,
        axis=found.axis,
        window=window if found.axis == "cohort" else None,
        kind="rate" if found.den is not None else "count",
        per=per,
        season_length=m,
        seasonal=[
            SeasonOut(season=s + 1, label=season_label(s, grain), index=value)
            for s, value in enumerate(fit.seasonal)
        ]
        if fit is not None and fit.seasonal is not None
        else [],
        seasonal_p_value=fit.seasonal_p_value if fit is not None else None,
        dispersion=fit.dispersion if fit is not None else None,
        penalty=fit.penalty if fit is not None else None,
        min_segment=MIN_SEGMENT,
        changes=changes,
        segments=segments,
        points=points,
    )
