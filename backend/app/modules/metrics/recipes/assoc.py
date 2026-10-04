"""⑨ 연관 · 묶음 — 어떤 증상과 부품(원인)이 함께 나오나, 원인을 나누는 증상 묶음(ADR 0014).

기준 둘(행 A — 증상, 열 B — 교체 부품 · 원인)의 셀이 함께 나온 수의 표 N[a, b] 다. 여러 값
기준이 끼면 한 기록이 값마다 한 번씩 들어 **나온 횟수** 기준이다(`basis`).

## 짝 — 향상도

    기대 E[a, b] = (a 의 합) x (b 의 합) / 전체,   향상도 = N[a, b] / E[a, b]

「a 의 기록에서 b 가 나오는 몫이 전체에서 b 의 몫의 몇 배인가」. 우연이면 1 근처. 유의성은
초기하 꼬리 확률 P(X ≥ N[a, b])(여백을 고정한 정확 검정)이고, 짝이 수백이라 **BH 로 거짓
발견율**을 맞춘다(q). 건수가 `min_count` 보다 적은 짝은 검정하지 않는다 — 몇 건짜리 짝의
향상도는 잡음이다.

## 묶음 — 원인 모양이 닮은 증상끼리

행마다 열 쪽의 PPMI(양의 점별 상호정보 — 우연보다 자주 함께 나오는 정도) 벡터를 만들고, 코사인
거리로 평균 연결 계층 묶음을 한다. 묶음 수는 실루엣이 가장 큰 것(2 ~ 10). 실루엣이 작으면
「뚜렷한 묶음이 없다」 고 말한다. 묶음마다 PPMI 가 큰 열(그 묶음을 가르는 원인)을 함께 낸다.

## 원인분산도 — 증상마다 원인이 얼마나 갈렸나

행 a 의 열 몫 p_b = N[a, b] / (a 의 합)으로 **유효 원인 수** 1 / Σ p_b² 를 낸다(역 심프슨).
원인 하나에 몰리면 1, 넷에 고르게 갈리면 4 다. 가장 많은 원인과 그 몫을 함께 싣고, 많이 갈린
증상부터 세운다 — 「원인이 여럿에 걸친 증상」 이 앞에 선다. 건수가 적은 행은 몇 건의 우연으로
값이 흔들려 뺀다(`DISPERSION_MIN`). 견줄 기준으로 전체(모든 행을 합친 열 몫)의 유효 원인 수도
낸다.

## 지도 — 대응 분석

표준화 잔차 행렬의 특이값 분해로 행과 열을 같은 평면에 놓는다(첫 두 축과 그 몫). 가까운 행과
열이 함께 자주 나온다.

**함께 나옴이지 원인이 아니다.** 같은 기록의 여러 값끼리는 독립이 아니라 나온 횟수 기준의
검정은 근사다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import _numeric, common, registry
from app.modules.metrics.recipes.schemas import (
    AssocClusterOut,
    AssocDispersionOut,
    AssocLabelOut,
    AssocOut,
    AssocPairOut,
    AssocPointOut,
)

NAME = "assoc"
LABEL = "연관 · 묶음"
METHOD = "향상도 · 초기하 정확 검정 · BH · PPMI 코사인 평균 연결 · 대응 분석 · 유효 원인 수 v2"
MIN_COUNT = 5
TOP_PAIRS = 50
COMPACT_PAIRS = 15
#: 묶음 · 지도에 쓰는 행 · 열의 상한(합이 큰 것부터) — 표가 커도 계산과 그림이 읽히게.
MAX_ROWS = 60
MAX_COLS = 80
WEAK_SILHOUETTE = 0.15
#: 원인분산도를 낼 행의 최소 건수 — 몇 건짜리 행의 유효 원인 수는 우연이 정한다.
DISPERSION_MIN = 20
COMPACT_DISPERSION = 15

Matrix = NDArray[np.float64]


# --- 순수 함수 -----------------------------------------------------------------------


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """BH q 값 — 거짓 발견율을 맞춘 p. 순서를 지키고 단조로 만든다."""
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * m / rank)
        q[index] = min(running, 1.0)
    return q


@dataclass
class Pair:
    row: int
    col: int
    count: float
    expected: float
    lift: float
    p_value: float
    q_value: float = 1.0


def pairs(table: Matrix, min_count: float = MIN_COUNT) -> list[Pair]:
    """건수가 `min_count` 이상인 짝마다 향상도 · 초기하 p · BH q."""
    total = float(table.sum())
    if total <= 0:
        return []
    rows = table.sum(axis=1)
    cols = table.sum(axis=0)
    out: list[Pair] = []
    for i, j in zip(*np.nonzero(table >= min_count), strict=True):
        count = float(table[i, j])
        expected = float(rows[i] * cols[j] / total)
        p_value = _numeric.hypergeom_sf(int(count), int(total), int(cols[j]), int(rows[i]))
        out.append(Pair(int(i), int(j), count, expected, count / expected, p_value))
    for one, q in zip(out, benjamini_hochberg([one.p_value for one in out]), strict=True):
        one.q_value = q
    return out


def effective_count(values: NDArray[np.float64]) -> float:
    """유효 개수 1 / Σ p² — 몫이 고르면 값의 수, 하나에 몰리면 1. 합이 0 이면 0."""
    total = float(values.sum())
    if total <= 0:
        return 0.0
    shares = values / total
    return float(1.0 / np.square(shares).sum())


@dataclass
class Dispersion:
    row: int
    count: float
    causes: int
    """나온 원인(열 값) 수."""
    effective: float
    top: int
    top_share: float


def dispersion(table: Matrix, minimum: float = DISPERSION_MIN) -> list[Dispersion]:
    """행마다 원인이 얼마나 갈렸나 — 많이 갈린 것부터. 건수가 `minimum` 보다 적은 행은 뺀다."""
    out: list[Dispersion] = []
    for i in range(table.shape[0]):
        row = table[i]
        total = float(row.sum())
        if total < minimum or total <= 0:
            continue
        top = int(np.argmax(row))
        out.append(
            Dispersion(
                row=i,
                count=total,
                causes=int(np.count_nonzero(row)),
                effective=effective_count(row),
                top=top,
                top_share=float(row[top] / total),
            )
        )
    out.sort(key=lambda one: (-one.effective, -one.count))
    return out


def ppmi(table: Matrix) -> Matrix:
    """양의 점별 상호정보 — log(P(a, b) / (P(a) P(b))) 의 양수 부분, 안 나온 칸은 0."""
    total = table.sum()
    if total <= 0:
        return np.zeros_like(table)
    joint = table / total
    rows = joint.sum(axis=1, keepdims=True)
    cols = joint.sum(axis=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        pmi = np.log(joint / (rows @ cols))
    pmi[~np.isfinite(pmi)] = 0.0
    return np.asarray(np.maximum(pmi, 0.0), dtype=np.float64)


def cosine_distance(vectors: Matrix) -> Matrix:
    norms = np.linalg.norm(vectors, axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    unit = vectors / safe[:, None]
    similarity = np.clip(unit @ unit.T, -1.0, 1.0)
    distance = 1.0 - similarity
    # 벡터가 0 인 행(우연보다 자주 나오는 열이 없음)은 모두와 가장 멀다.
    empty = norms == 0
    distance[empty, :] = 1.0
    distance[:, empty] = 1.0
    np.fill_diagonal(distance, 0.0)
    return np.asarray(distance, dtype=np.float64)


def average_linkage(distance: Matrix) -> list[tuple[int, int]]:
    """평균 연결 계층 묶음 — 합친 순서 [(남는 묶음, 사라지는 묶음)]. n 이 수십이라 O(n³)."""
    n = distance.shape[0]
    sizes = {i: 1 for i in range(n)}
    current = distance.astype(np.float64).copy()
    np.fill_diagonal(current, np.inf)
    alive = set(range(n))
    merges: list[tuple[int, int]] = []
    while len(alive) > 1:
        members = sorted(alive)
        sub = current[np.ix_(members, members)]
        flat = int(np.argmin(sub))
        a, b = members[flat // len(members)], members[flat % len(members)]
        keep, gone = min(a, b), max(a, b)
        for other in alive:
            if other in (keep, gone):
                continue
            merged = (
                current[keep, other] * sizes[keep] + current[gone, other] * sizes[gone]
            ) / (sizes[keep] + sizes[gone])
            current[keep, other] = current[other, keep] = merged
        sizes[keep] += sizes[gone]
        alive.remove(gone)
        current[gone, :] = np.inf
        current[:, gone] = np.inf
        merges.append((keep, gone))
    return merges


def cut(merges: Sequence[tuple[int, int]], n: int, k: int) -> list[int]:
    """합친 순서를 k 묶음에서 멈춘다 — 행마다 묶음 번호(0..k-1, 처음 나온 순)."""
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for keep, gone in merges[: max(0, n - k)]:
        parent[root(gone)] = root(keep)
    names: dict[int, int] = {}
    return [names.setdefault(root(i), len(names)) for i in range(n)]


def silhouette(distance: Matrix, labels: Sequence[int]) -> float:
    """평균 실루엣 — 자기 묶음과의 평균 거리 a, 가장 가까운 남의 묶음과의 b, (b - a) / max."""
    n = len(labels)
    groups = sorted(set(labels))
    if len(groups) < 2 or len(groups) >= n:
        return 0.0
    scores: list[float] = []
    for i in range(n):
        same = [j for j in range(n) if labels[j] == labels[i] and j != i]
        if not same:
            scores.append(0.0)
            continue
        a = float(np.mean(distance[i, same]))
        b = min(
            float(np.mean(distance[i, [j for j in range(n) if labels[j] == g]]))
            for g in groups
            if g != labels[i]
        )
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(scores))


@dataclass
class Clusters:
    labels: list[int]
    k: int
    silhouette: float


def clusters(table: Matrix, *, max_k: int = 10) -> Clusters | None:
    """행을 열 쪽 PPMI 모양으로 묶는다 — 실루엣이 가장 큰 k."""
    n = table.shape[0]
    if n < 3:
        return None
    distance = cosine_distance(ppmi(table))
    merges = average_linkage(distance)
    best: Clusters | None = None
    for k in range(2, min(max_k, n - 1) + 1):
        labels = cut(merges, n, k)
        score = silhouette(distance, labels)
        if best is None or score > best.silhouette + 1e-12:
            best = Clusters(labels, k, score)
    return best


def correspondence(table: Matrix) -> tuple[Matrix, Matrix, float] | None:
    """대응 분석 — (행 좌표, 열 좌표, 첫 두 축이 설명하는 몫). 주좌표(특이값을 곱한 것)."""
    total = table.sum()
    if total <= 0 or min(table.shape) < 3:
        return None
    joint = table / total
    rows = joint.sum(axis=1)
    cols = joint.sum(axis=0)
    keep_rows, keep_cols = rows > 0, cols > 0
    joint = joint[np.ix_(keep_rows, keep_cols)]
    r, c = rows[keep_rows], cols[keep_cols]
    residual = (joint - np.outer(r, c)) / np.sqrt(np.outer(r, c))
    u, sigma, vt = np.linalg.svd(residual, full_matrices=False)
    inertia = float((sigma**2).sum())
    if inertia <= 0 or sigma.size < 2:
        return None
    row_xy = (u[:, :2] * sigma[:2]) / np.sqrt(r)[:, None]
    col_xy = (vt.T[:, :2] * sigma[:2]) / np.sqrt(c)[:, None]
    full_rows = np.full((table.shape[0], 2), np.nan)
    full_cols = np.full((table.shape[1], 2), np.nan)
    full_rows[keep_rows] = row_xy
    full_cols[keep_cols] = col_xy
    return full_rows, full_cols, float((sigma[:2] ** 2).sum() / inertia)


# --- 어댑터 --------------------------------------------------------------------------


def available(built: spec_module.Built) -> str | None:
    if built.spec.measure != "count":
        return "건수 지표에서만 됩니다 — 함께 나온 수를 셉니다."
    if len(built.dims) < 2:
        return (
            "기준이 둘 이상 있는 지표에서만 됩니다 — 증상과 부품처럼 둘이 함께 나오는지 "
            "봅니다."
        )
    return None


registry.register(registry.Recipe(NAME, LABEL, available))


def run(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    rows: str,
    cols: str,
    min_count: int = MIN_COUNT,
    compact: bool = False,
) -> AssocOut:
    reason = available(built)
    if reason is not None:
        raise common.refuse(33, reason)
    row_dim = common.dim_of(built, rows)
    col_dim = common.dim_of(built, cols)
    if rows == cols:
        raise common.refuse(34, "행과 열이 같은 기준입니다 — 서로 다른 기준 둘을 고릅니다.")
    for one in (row_dim, col_dim):
        if one.name in ask.filters:
            raise common.refuse(
                34, f"「{one.axis.label}」 으로 걸렀으니 행 · 열이 될 수 없습니다."
            )
    ask = replace(ask, dims=[rows, cols], by=())
    common.require_exact_counts(built, ask)
    frame = query.frame(db, user, metric, built, ask, with_denominator=False)
    common.require_whole(frame)
    caveats = common.Caveats()
    excluded = {"no_value": 0}
    counts: dict[tuple[str, str], float] = {}
    for cell in frame.cells:
        a, b = cell.dims.get(rows), cell.dims.get(cols)
        if a is None or b is None:
            excluded["no_value"] += cell.count
            continue
        counts[(a, b)] = counts.get((a, b), 0.0) + cell.count
    row_totals: dict[str, float] = {}
    col_totals: dict[str, float] = {}
    for (a, b), value in counts.items():
        row_totals[a] = row_totals.get(a, 0.0) + value
        col_totals[b] = col_totals.get(b, 0.0) + value
    row_keys = sorted(row_totals, key=lambda a: (-row_totals[a], a))
    col_keys = sorted(col_totals, key=lambda b: (-col_totals[b], b))
    table = np.zeros((len(row_keys), len(col_keys)))
    row_index = {key: i for i, key in enumerate(row_keys)}
    col_index = {key: j for j, key in enumerate(col_keys)}
    for (a, b), value in counts.items():
        table[row_index[a], col_index[b]] = value
    labels = query.labels_for(db, built, [rows, cols], frame.cells)
    found_pairs = pairs(table, min_count)
    significant = sorted(
        (one for one in found_pairs if one.lift > 1 and one.q_value < 0.05),
        key=lambda one: (-one.lift, -one.count),
    )
    limit = COMPACT_PAIRS if compact else TOP_PAIRS
    out_pairs = [
        _pair_out(one, built, ask, rows, cols, row_keys, col_keys, labels, table)
        for one in significant[:limit]
    ]
    shown_rows = list(range(min(len(row_keys), MAX_ROWS)))
    shown_cols = list(range(min(len(col_keys), MAX_COLS)))
    small = table[np.ix_(shown_rows, shown_cols)] if shown_rows and shown_cols else table
    grouped = clusters(small)
    out_clusters: list[AssocClusterOut] = []
    silhouette_value: float | None = None
    if grouped is not None:
        silhouette_value = grouped.silhouette
        weights = ppmi(small)
        for g in range(grouped.k):
            members = [i for i, label in enumerate(grouped.labels) if label == g]
            profile = weights[members].mean(axis=0)
            top = [int(j) for j in np.argsort(-profile)[:5] if profile[j] > 0]
            out_clusters.append(
                AssocClusterOut(
                    members=[_label(labels, rows, row_keys[i]) for i in members],
                    top=[_label(labels, cols, col_keys[j]) for j in top],
                    count=float(small[members].sum()),
                )
            )
        if grouped.silhouette < WEAK_SILHOUETTE:
            caveats.add(
                "weak_clusters",
                f"묶음의 실루엣이 {grouped.silhouette:.2f} 라 뚜렷한 묶음이 없습니다 — 묶음은 "
                "참고로만 봅니다.",
                level="info",
            )
    rows_xy: list[AssocPointOut] = []
    cols_xy: list[AssocPointOut] = []
    explained: float | None = None
    if not compact:
        mapped = correspondence(small)
        if mapped is not None:
            row_points, col_points, explained = mapped
            total = small.sum()
            rows_xy = [
                AssocPointOut(
                    **_label(labels, rows, row_keys[i]).model_dump(),
                    x=float(row_points[i, 0]),
                    y=float(row_points[i, 1]),
                    mass=float(small[i].sum() / total),
                )
                for i in range(small.shape[0])
                if np.isfinite(row_points[i]).all()
            ]
            cols_xy = [
                AssocPointOut(
                    **_label(labels, cols, col_keys[j]).model_dump(),
                    x=float(col_points[j, 0]),
                    y=float(col_points[j, 1]),
                    mass=float(small[:, j].sum() / total),
                )
                for j in range(small.shape[1])
                if np.isfinite(col_points[j]).all()
            ]
    spread = dispersion(table, max(DISPERSION_MIN, 4 * min_count))  # 아래 caveat 와 같은 값
    out_dispersion = [
        AssocDispersionOut(
            row=_label(labels, rows, row_keys[one.row]),
            count=one.count,
            causes=one.causes,
            effective=one.effective,
            top=_label(labels, cols, col_keys[one.top]),
            top_share=one.top_share,
        )
        for one in spread[: COMPACT_DISPERSION if compact else MAX_ROWS]
    ]
    overall = effective_count(table.sum(axis=0)) if table.size else None
    if spread:
        floor = max(DISPERSION_MIN, 4 * min_count)
        caveats.add(
            "dispersion",
            "원인분산도는 증상마다 원인이 몇 개에 고르게 퍼진 것과 같은가(유효 원인 수 "
            f"1/Σ몫²)입니다 — 1 이면 원인 하나에 몰림. 건수가 {floor}건보다 적은 증상은 "
            "뺐습니다.",
            level="info",
        )
    multi = row_dim.axis.multi or col_dim.axis.multi
    if multi:
        caveats.add(
            "overlap_basis",
            "한 기록이 여러 값을 가질 수 있어 기록이 아니라 그 값이 나온 횟수 기준입니다 — "
            "같은 기록의 값끼리는 독립이 아니라 검정은 근사입니다.",
            level="info",
        )
    if excluded["no_value"]:
        caveats.add(
            "no_value_excluded",
            "행이나 열의 값이 빈 기록은 뺐습니다.",
            level="info",
            count=excluded["no_value"],
        )
    if len(row_keys) > MAX_ROWS or len(col_keys) > MAX_COLS:
        caveats.add(
            "trimmed_map",
            f"묶음 · 지도는 합이 큰 행 {MAX_ROWS}개 · 열 {MAX_COLS}개로 봅니다 — 짝은 "
            "전부에서 찾았습니다.",
            level="info",
        )
    caveats.add(
        "multiple_testing",
        f"짝 {len(found_pairs)}개를 함께 검정해 BH 로 거짓 발견율을 맞췄습니다(q < 0.05 만 "
        "싣습니다).",
        level="info",
    )
    caveats.add(
        "association",
        "향상도는 함께 나옴이지 원인이 아닙니다.",
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
        params={"rows": rows, "cols": cols, "min_count": min_count, "compact": compact},
        caveats=caveats,
        excluded=excluded,
    )
    return AssocOut(
        **head,
        rows=rows,
        rows_label=row_dim.axis.label,
        cols=cols,
        cols_label=col_dim.axis.label,
        basis="occurrences" if multi else "records",
        total=float(table.sum()),
        row_values=len(row_keys),
        col_values=len(col_keys),
        tested=len(found_pairs),
        pairs=out_pairs,
        dispersion=out_dispersion,
        overall_effective=overall,
        clusters=out_clusters,
        silhouette=silhouette_value,
        map_rows=rows_xy,
        map_cols=cols_xy,
        map_explained=explained,
    )


def _label(labels: dict[str, dict[str, str]], name: str, key: str) -> AssocLabelOut:
    return AssocLabelOut(key=key, label=query.label_of(labels, name, key))


def _pair_out(
    one: Pair,
    built: spec_module.Built,
    ask: query.Ask,
    rows: str,
    cols: str,
    row_keys: list[str],
    col_keys: list[str],
    labels: dict[str, dict[str, str]],
    table: Matrix,
) -> AssocPairOut:
    a, b = row_keys[one.row], col_keys[one.col]
    cell = query.Cell(
        {rows: a, cols: b}, None, None, None, int(one.count), 0, None, None, None
    )
    row_total = float(table[one.row].sum())
    return AssocPairOut(
        row=_label(labels, rows, a),
        col=_label(labels, cols, b),
        count=one.count,
        expected=one.expected,
        lift=one.lift,
        share=one.count / row_total if row_total else 0.0,
        p_value=one.p_value,
        q_value=one.q_value,
        drill=query.drill(built, cell, by=(), ask=ask),
    )
