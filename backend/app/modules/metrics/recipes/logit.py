"""⑥ 재방문 위험 요인 — 「정한 일수 안에 다시 왔나」 를 요인별로 견준다(ADR 0014).

## 셀 위의 묶인 로지스틱

지표에 방문 기준(`visit.repeat`, `visits.py`)이 있으면 셀이 (재방문 예 · 아니오, 요인
값들)마다의 건수다. 요인 값이 같은 기록은 예측 확률이 같으므로 **묶인 이항 로지스틱**을 셀
위에서 그대로 맞춘다(IRLS) — 기록 하나하나를 다시 읽지 않는다.

    logit P(재방문) = b0 + Σ 요인마다 (기준 수준이 아닌 값의 더미) · b

- 요인마다 기준 수준(기록이 가장 많은 값) 대비 **오즈비**와 95% 구간(왈드).
- 요인을 뺐을 때 이탈도가 얼마나 느나 — LR 검정(자유도 = 그 요인의 더미 수).
- **AUC** — 예측 확률이 재방문 기록과 아닌 기록을 가르는 정도(0.5 면 못 가름).

기록이 적은 수준(`min_count` 미만)과 예 · 아니오 중 하나가 없는 수준은 「그 밖」 으로 모은다 —
몇 건짜리 수준의 오즈비는 흔들리고, 한쪽이 0 이면 무한대가 된다. 그래도 갈라지면(분리) 그
오즈비는 구간 없이 「불안정」 으로 적는다.

## 읽는 법

오즈비는 **함께 나옴**이지 원인이 아니다. 같은 시리얼의 방문끼리는 독립이 아니라 구간이
실제보다 좁다. 「아직 열림」(창이 닫히지 않음)은 뺀다 — 넣으면 늦게 들어온 기록이 「아니오」
로 센다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics import visits as visits_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, registry
from app.modules.metrics.recipes.schemas import LogitFactorOut, LogitLevelOut, LogitOut

NAME = "logit"
LABEL = "재방문 위험 요인"
METHOD = "묶인 이항 로지스틱(IRLS) · 왈드 구간 · 요인별 LR 검정 v1"
MAX_FACTORS = 4
MIN_COUNT = 30
OTHER = "__other__"
OTHER_LABEL = "그 밖"
Z95 = 1.959963984540054
#: 이보다 큰 계수는 분리(한쪽이 0 인 조합)로 본다 — 오즈비가 e^10 ≈ 2만 배.
UNSTABLE = 10.0

Vector = NDArray[np.float64]


# --- 순수 함수 -----------------------------------------------------------------------


@dataclass
class Group:
    """요인 값들의 조합 하나 — 재방문 예 · 아니오 건수."""

    levels: tuple[str | None, ...]
    yes: float
    no: float


def pooled_levels(
    groups: Sequence[Group], index: int, min_count: int
) -> tuple[dict[str | None, str | None], list[tuple[str | None, int]]]:
    """요인 하나의 수준 → 쓰는 수준. 적거나 한쪽이 0 인 수준은 「그 밖」 으로.

    (바꾸는 표, [(모은 수준, 모인 원래 수준 수)])."""
    yes: dict[str | None, float] = {}
    no: dict[str | None, float] = {}
    for one in groups:
        level = one.levels[index]
        yes[level] = yes.get(level, 0.0) + one.yes
        no[level] = no.get(level, 0.0) + one.no
    kept = {
        level
        for level in yes
        if yes[level] + no[level] >= min_count and yes[level] > 0 and no[level] > 0
    }
    mapping: dict[str | None, str | None] = {
        level: level if level in kept else OTHER for level in yes
    }
    moved = sum(1 for level in yes if level not in kept)
    sizes = [(level, 1) for level in kept]
    if moved:
        sizes.append((OTHER, moved))
    return mapping, sizes


def irls(
    x: Vector, yes: Vector, total: Vector, *, iterations: int = 60
) -> tuple[Vector, Vector | None, float, bool]:
    """묶인 이항 로지스틱 — (계수, 공분산, 이탈도, 수렴). 정보 행렬이 안 서면 공분산 None."""
    rate = float(yes.sum() / max(total.sum(), 1e-300))
    rate = min(max(rate, 1e-6), 1 - 1e-6)
    beta = np.zeros(x.shape[1])
    beta[0] = math.log(rate / (1 - rate))
    previous = math.inf
    converged = False
    for _ in range(iterations):
        eta = np.clip(x @ beta, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-eta))
        weight = np.maximum(total * p * (1 - p), 1e-12)
        working = eta + (yes - total * p) / weight
        info = x.T @ (weight[:, None] * x)
        try:
            beta = np.linalg.solve(info, x.T @ (weight * working))
        except np.linalg.LinAlgError:
            beta = np.linalg.lstsq(info, x.T @ (weight * working), rcond=None)[0]
        current = deviance(yes, total, x @ beta)
        if abs(previous - current) < 1e-10 * (1 + abs(current)):
            converged = True
            break
        previous = current
    eta = np.clip(x @ beta, -30.0, 30.0)
    p = 1.0 / (1.0 + np.exp(-eta))
    info = x.T @ ((total * p * (1 - p))[:, None] * x)
    covariance: Vector | None
    try:
        eigen = np.linalg.eigvalsh(info)
        covariance = np.linalg.inv(info) if eigen.min() > 1e-10 * eigen.max() else None
    except np.linalg.LinAlgError:
        covariance = None
    return beta, covariance, deviance(yes, total, x @ beta), converged


def deviance(yes: Vector, total: Vector, eta: Vector) -> float:
    """이항 이탈도 — 2 Σ [y ln(y/μ) + (n-y) ln((n-y)/(n-μ))]."""
    p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30.0, 30.0)))
    mu = total * p
    out = 0.0
    for y, n, m in zip(yes, total, mu, strict=True):
        if y > 0:
            out += y * math.log(y / max(m, 1e-300))
        if n - y > 0:
            out += (n - y) * math.log((n - y) / max(n - m, 1e-300))
    return 2.0 * out


def auc(yes: Sequence[float], no: Sequence[float], score: Sequence[float]) -> float | None:
    """같은 점수를 가진 묶음들 — 재방문 기록이 아닌 기록보다 점수가 높을 확률(같으면 반)."""
    positives = float(sum(yes))
    negatives = float(sum(no))
    if positives <= 0 or negatives <= 0:
        return None
    order = sorted(range(len(score)), key=lambda i: score[i])
    below = 0.0
    total = 0.0
    k = 0
    while k < len(order):
        j = k
        while j < len(order) and score[order[j]] == score[order[k]]:
            j += 1
        tie_yes = sum(yes[order[i]] for i in range(k, j))
        tie_no = sum(no[order[i]] for i in range(k, j))
        total += tie_yes * (below + 0.5 * tie_no)
        below += tie_no
        k = j
    return total / (positives * negatives)


@dataclass
class Column:
    factor: int
    level: str | None


@dataclass
class Fitted:
    columns: list[Column]
    beta: Vector
    covariance: Vector | None
    deviance: float
    null_deviance: float
    converged: bool
    lr: list[tuple[float, int, float]]
    """요인마다 (이탈도 차, 자유도, p 값)."""
    auc: float | None
    references: list[str | None]
    sizes: list[list[tuple[str | None, int]]]
    groups: list[Group]
    aliased: bool = False
    """요인끼리 완전히 겹쳐(어떤 값이 한 요인의 값에서만 나온다) 오즈비를 가를 수 없다."""


def design(
    groups: Sequence[Group],
    references: Sequence[str | None],
    keep: Sequence[int] | None = None,
) -> tuple[Vector, list[Column]]:
    """절편 + 요인마다 기준 수준이 아닌 값의 더미.

    `keep` 은 넣을 요인 — 하나를 빼고 맞춰 LR 을 볼 때."""
    factors = range(len(references)) if keep is None else keep
    columns: list[Column] = []
    for f in factors:
        seen = sorted(
            {one.levels[f] for one in groups if one.levels[f] != references[f]},
            key=lambda v: (v is None, v == OTHER, str(v)),
        )
        columns.extend(Column(f, level) for level in seen)
    x = np.ones((len(groups), len(columns) + 1))
    for i, one in enumerate(groups):
        for c, column in enumerate(columns, start=1):
            x[i, c] = 1.0 if one.levels[column.factor] == column.level else 0.0
    return x, columns


def fit(groups: Sequence[Group], factors: int, min_count: int = MIN_COUNT) -> Fitted:
    """요인 값 조합별 예 · 아니오 → 수준을 모으고 맞춘다."""
    pooled = list(groups)
    sizes: list[list[tuple[str | None, int]]] = []
    for f in range(factors):
        mapping, size = pooled_levels(pooled, f, min_count)
        sizes.append(size)
        merged: dict[tuple[str | None, ...], Group] = {}
        for one in pooled:
            levels = tuple(
                mapping[value] if index == f else value
                for index, value in enumerate(one.levels)
            )
            found = merged.get(levels)
            if found is None:
                merged[levels] = Group(levels, one.yes, one.no)
            else:
                found.yes += one.yes
                found.no += one.no
        pooled = list(merged.values())
    pooled = [one for one in pooled if one.yes + one.no > 0]
    references: list[str | None] = []
    for f in range(factors):
        totals: dict[str | None, float] = {}
        for one in pooled:
            totals[one.levels[f]] = totals.get(one.levels[f], 0.0) + one.yes + one.no
        named = [level for level in totals if level != OTHER] or list(totals)
        # 기록이 가장 많은 값 — 같으면 이름순으로 앞의 것.
        ordered = sorted(named, key=lambda level: (level is None, str(level)))
        references.append(max(ordered, key=lambda level: totals[level]))
    yes = np.asarray([one.yes for one in pooled], dtype=np.float64)
    total = np.asarray([one.yes + one.no for one in pooled], dtype=np.float64)
    x, columns = design(pooled, references)
    aliased = int(np.linalg.matrix_rank(x)) < x.shape[1]
    beta, covariance, dev, converged = irls(x, yes, total)
    if aliased:
        # 계수 하나하나는 정해지지 않는다 — 이탈도 · LR 은 그대로 뜻이 있다.
        covariance = None
    null_x = np.ones((len(pooled), 1))
    _, _, null_dev, _ = irls(null_x, yes, total)
    lr: list[tuple[float, int, float]] = []
    for f in range(factors):
        others = [g for g in range(factors) if g != f]
        reduced, _ = design(pooled, references, keep=others)
        _, _, reduced_dev, _ = irls(reduced, yes, total)
        df = sum(1 for one in columns if one.factor == f)
        statistic = max(0.0, reduced_dev - dev)
        p_value = _numeric.chi2_sf(statistic, df) if df > 0 and statistic > 0 else 1.0
        lr.append((statistic, df, p_value))
    score = (x @ beta).tolist()
    found_auc = auc([one.yes for one in pooled], [one.no for one in pooled], score)
    return Fitted(
        columns,
        beta,
        covariance,
        dev,
        null_dev,
        converged,
        lr,
        found_auc,
        references,
        sizes,
        pooled,
        aliased,
    )


# --- 어댑터 --------------------------------------------------------------------------


def _repeat_dim(built: spec_module.Built) -> spec_module.Dim | None:
    return next((one for one in built.dims if one.address == visits_module.REPEAT), None)


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 재방문 기록과 아닌 기록을 셉니다."
    if _repeat_dim(built) is None:
        return (
            "방문 기준 「재방문」(visit.repeat)이 있는 지표에서만 됩니다 — 정의에 "
            "visits(시리얼 칸 · 일수)를 두고 그 기준을 더합니다."
        )
    factors = [
        one for one in built.dims if one.address != visits_module.REPEAT and not one.axis.multi
    ]
    if not factors:
        return "요인으로 쓸 기준(여러 값이 아닌 것)이 하나 이상 있어야 합니다."
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    factors: list[str],
    min_count: int = MIN_COUNT,
    compact: bool = False,
) -> LogitOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(31, reason)
    repeat = _repeat_dim(built)
    assert repeat is not None
    # 같은 요인을 두 번 주면(`factors=a,a`) 설계 행렬의 열이 겹친다 — 한 번만(뜻이 같다).
    factors = list(dict.fromkeys(factors))
    if not factors:
        raise common.refuse(
            32, "요인(factors)을 하나 이상 고릅니다 — 값이 적은 기준이 좋습니다."
        )
    if len(factors) > MAX_FACTORS:
        raise common.refuse(32, f"요인은 {MAX_FACTORS}개까지입니다.")
    dims = [common.dim_of(built, name) for name in factors]
    for one in dims:
        if one.address == visits_module.REPEAT:
            raise common.refuse(32, "재방문 기준은 결과라서 요인이 될 수 없습니다.")
        if one.axis.multi:
            raise common.refuse(
                32,
                f"「{one.axis.label}」 은 여러 값 기준이라 한 기록이 여러 줄에 들어 건수가 "
                "부풉니다 — 요인으로 못 씁니다(그 기준으로 거르기는 됩니다).",
            )
        if one.name in ask.filters:
            raise common.refuse(
                32, f"「{one.axis.label}」 으로 걸렀으니 요인이 될 수 없습니다."
            )
    ask = replace(ask, dims=[repeat.name, *factors], by=())
    common.require_exact_counts(built, ask)
    frame = query.frame(db, user, metric, built, ask, with_denominator=False)
    common.require_whole(frame)
    caveats = common.Caveats()
    excluded = {"open": 0, "no_visit": 0}
    by_levels: dict[tuple[str | None, ...], Group] = {}
    for cell in frame.cells:
        state = cell.dims.get(repeat.name)
        if state == "open":
            excluded["open"] += cell.count
            continue
        if state not in ("yes", "no"):
            excluded["no_visit"] += cell.count
            continue
        levels = tuple(cell.dims.get(name) for name in factors)
        group = by_levels.setdefault(levels, Group(levels, 0.0, 0.0))
        if state == "yes":
            group.yes += cell.count
        else:
            group.no += cell.count
    groups = list(by_levels.values())
    labels = query.labels_for(db, built, factors, frame.cells)
    total_yes = sum(one.yes for one in groups)
    total_no = sum(one.no for one in groups)
    within = built.spec.visits.within_days if built.spec.visits is not None else 0
    out_factors: list[LogitFactorOut] = []
    found: Fitted | None = None
    if total_yes > 0 and total_no > 0:
        found = fit(groups, len(factors), min_count)
        out_factors = _factors(found, factors, dims, labels, compact)
        if found.aliased:
            caveats.add(
                "aliased",
                "요인끼리 겹쳐(어떤 값이 다른 요인의 몇 값에서만 나온다) 오즈비를 가를 수 "
                "없습니다 — 요인을 하나씩 봅니다. 요인별 LR 검정은 그대로 읽습니다.",
            )
        elif any(level.unstable for one in out_factors for level in one.levels):
            caveats.add(
                "separation",
                "예 · 아니오 중 한쪽이 거의 없는 조합이 있어 일부 오즈비가 불안정합니다 — "
                "구간 없이 적었습니다. 요인을 줄이거나 min_count 를 올립니다.",
            )
        if any(level.pooled > 0 for one in out_factors for level in one.levels):
            caveats.add(
                "pooled_levels",
                f"기록이 {min_count}건 안 되거나 예 · 아니오 중 하나가 없는 값은 "
                f"「{OTHER_LABEL}」 로 모았습니다.",
                level="info",
            )
    else:
        caveats.add(
            "no_contrast",
            "재방문 기록과 아닌 기록이 함께 있어야 견줍니다 — 한쪽뿐입니다.",
        )
    if excluded["open"]:
        caveats.add(
            "open_excluded",
            f"재방문 창({within}일)이 아직 닫히지 않은 기록은 뺐습니다 — 넣으면 늦게 올 "
            "재방문이 「아니오」 로 셉니다.",
            level="info",
            count=excluded["open"],
        )
    if excluded["no_visit"]:
        caveats.add(
            "no_visit",
            "시리얼이 비었거나 날짜를 못 읽어 방문을 못 센 기록은 뺐습니다.",
            level="info",
            count=excluded["no_visit"],
        )
    caveats.add(
        "not_independent",
        "같은 시리얼의 방문끼리는 독립이 아닙니다 — 구간이 실제보다 좁습니다.",
        level="info",
    )
    caveats.add(
        "association",
        "오즈비는 함께 나옴이지 원인이 아닙니다 — 요인끼리 얽혀 있으면 그 몫이 나뉩니다.",
        level="info",
    )
    head = common.header(
        db,
        user,
        metric,
        built,
        frame,
        recipe=NAME,
        method=METHOD,
        params={"factors": factors, "min_count": min_count, "compact": compact},
        caveats=caveats,
        excluded=excluded,
    )
    records = total_yes + total_no
    return LogitOut(
        **head,
        within_days=within,
        repeat_dim=repeat.name,
        records=int(records),
        yes=int(total_yes),
        no=int(total_no),
        rate=total_yes / records if records else None,
        baseline_rate=_baseline(found),
        factors=out_factors,
        deviance=found.deviance if found is not None else None,
        null_deviance=found.null_deviance if found is not None else None,
        auc=found.auc if found is not None else None,
        converged=found.converged if found is not None else None,
    )


def _baseline(found: Fitted | None) -> float | None:
    """모든 요인이 기준 수준일 때의 재방문 확률."""
    if found is None:
        return None
    return 1.0 / (1.0 + math.exp(-float(found.beta[0])))


def _factors(
    found: Fitted,
    names: list[str],
    dims: list[spec_module.Dim],
    labels: dict[str, dict[str, str]],
    compact: bool,
) -> list[LogitFactorOut]:
    out: list[LogitFactorOut] = []
    for f, (name, dim) in enumerate(zip(names, dims, strict=True)):
        totals: dict[str | None, tuple[float, float]] = {}
        for group in found.groups:
            level = group.levels[f]
            yes, no = totals.get(level, (0.0, 0.0))
            totals[level] = (yes + group.yes, no + group.no)
        pooled = dict(found.sizes[f])
        levels: list[LogitLevelOut] = []
        for level, (yes, no) in sorted(totals.items(), key=lambda item: -(sum(item[1]))):
            reference = level == found.references[f]
            odds: float | None = 1.0 if reference else None
            band: list[float] | None = None
            unstable = False
            if not reference:
                index = next(
                    (
                        c
                        for c, column in enumerate(found.columns, start=1)
                        if column.factor == f and column.level == level
                    ),
                    None,
                )
                if index is not None:
                    coefficient = float(found.beta[index])
                    variance = (
                        float(found.covariance[index, index])
                        if found.covariance is not None
                        else math.inf
                    )
                    unstable = abs(coefficient) > UNSTABLE or not math.isfinite(variance)
                    if not unstable:
                        odds = math.exp(coefficient)
                        spread = Z95 * math.sqrt(max(variance, 0.0))
                        band = [math.exp(coefficient - spread), math.exp(coefficient + spread)]
            label = OTHER_LABEL if level == OTHER else query.label_of(labels, name, level)
            levels.append(
                LogitLevelOut(
                    key=level,
                    label=label,
                    count=int(yes + no),
                    yes=int(yes),
                    rate=yes / (yes + no) if yes + no else None,
                    odds_ratio=odds,
                    ci=band,
                    reference=reference,
                    pooled=pooled.get(level, 1) if level == OTHER else 0,
                    unstable=unstable,
                )
            )
        statistic, df, p_value = found.lr[f]
        if compact:
            levels = levels[:12]
        out.append(
            LogitFactorOut(
                name=name,
                label=dim.axis.label,
                chi2=statistic,
                df=df,
                p_value=p_value,
                levels=levels,
            )
        )
    return out
