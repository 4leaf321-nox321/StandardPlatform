"""지도의 기준정보 배치 — **numpy · scipy 만**(새 의존성 없음, ADR 0046).

ForceAtlas2 와 같은 힘(선형 끌림 w·d, 반발 k·m_i·m_j/d, 거리에 비례하는 중력)을 쓰되 반발을
노드 짝마다 세지 않고 **입자-격자(PM)** 로 센다: 질량을 격자에 뿌리고(CIC), 2차원 쿨롱 핵
(r/|r|²)과 FFT 로 합성곱해 힘의 장을 얻고, 노드 자리에서 다시 읽는다. 격자 칸보다 가까운 짝은
KD-tree 로 짝마다 더한다(P3M). scipy 는 수치 문(`metrics/recipes/_numeric`)으로만 부른다. 반복
한 번이 O(N + G² log G) — 기준정보 1.3만 개(2,000만 건 DB) 300번에 5.7초, 10만 개 37초였다(측정
보고 2026-11-01). igraph(DrL)는 GPL 이라 쓰지 않는다.

`disc_overlap_fix` 는 허브 원반(넓이 = 기록 수)이 겹치면 밀어낸다 — **고정한 원반은 안
움직인다** (매일 덧붙임은 기존 자리를 0 움직인다).

같은 입력 · 같은 씨앗이면 같은 자리다(난수는 씨앗으로만, 같은 자리의 짝은 번호로 정한 방향).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from app.modules.metrics.recipes import _numeric as numeric

_KERNELS: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _kernel(grid: int) -> tuple[np.ndarray, np.ndarray]:
    """2G x 2G 의 r/|r|² 핵(칸 단위)의 rfft — 비주기(0 덧대기)."""
    if grid in _KERNELS:
        return _KERNELS[grid]
    n = 2 * grid
    ax = np.arange(n, dtype=np.float32)
    ax[ax >= grid] -= n
    gx, gy = np.meshgrid(ax, ax, indexing="xy")
    r2 = gx * gx + gy * gy
    r2[0, 0] = 1.0
    kx = gx / r2
    ky = gy / r2
    kx[0, 0] = 0.0
    ky[0, 0] = 0.0
    out = (numeric.rfft2(kx), numeric.rfft2(ky))
    _KERNELS[grid] = out
    return out


def robust_box(pos: np.ndarray) -> tuple[np.ndarray, float]:
    """바깥값을 뺀(0.5 ~ 99.5%) 정사각 범위 — (왼쪽 아래, 한 변)."""
    lo = np.percentile(pos, 0.5, axis=0)
    hi = np.percentile(pos, 99.5, axis=0)
    span = float(max(hi[0] - lo[0], hi[1] - lo[1])) * 1.1 + 1e-9
    mid = (lo + hi) / 2
    return mid - span / 2, span


def _repulsion(pos: np.ndarray, mass: np.ndarray, grid: int) -> tuple[np.ndarray, float]:
    # 격자 범위는 바깥값을 빼고 — 멀리 튕긴 몇 개가 격자를 늘이면 가운데가 한 칸에 뭉친다.
    lo, span = robust_box(pos)
    h = span / (grid - 2)
    g = np.clip((pos - lo) / h + 0.5, 0, grid - 2 - 1e-6)
    i0 = np.floor(g).astype(np.int64)
    f = (g - i0).astype(np.float32)
    i0 = np.clip(i0, 0, grid - 2)
    n = 2 * grid
    rho = np.zeros(n * n, dtype=np.float32)
    w00 = (1 - f[:, 0]) * (1 - f[:, 1])
    w10 = f[:, 0] * (1 - f[:, 1])
    w01 = (1 - f[:, 0]) * f[:, 1]
    w11 = f[:, 0] * f[:, 1]
    base = i0[:, 1] * n + i0[:, 0]
    m = mass.astype(np.float32)
    for off, w in ((0, w00), (1, w10), (n, w01), (n + 1, w11)):
        rho += np.bincount(base + off, weights=m * w, minlength=n * n).astype(np.float32)
    kx, ky = _kernel(grid)
    spectrum = numeric.rfft2(rho.reshape(n, n))
    fx_grid = numeric.irfft2(spectrum * kx, (n, n)).ravel()
    fy_grid = numeric.irfft2(spectrum * ky, (n, n)).ravel()
    fx = fx_grid[base] * w00 + fx_grid[base + 1] * w10 + fx_grid[base + n] * w01
    fx = fx + fx_grid[base + n + 1] * w11
    fy = fy_grid[base] * w00 + fy_grid[base + 1] * w10 + fy_grid[base + n] * w01
    fy = fy + fy_grid[base + n + 1] * w11
    return np.stack([fx, fy], axis=1) / h, h


def _near(pos: np.ndarray, mass: np.ndarray, reach: float) -> np.ndarray:
    """가까운 짝(격자 두 칸 안)의 반발을 짝마다 — CIC 가 칸 안에서 뭉개는 몫."""
    pairs = numeric.close_pairs(pos, reach)
    n = len(pos)
    if len(pairs) == 0:
        return np.zeros_like(pos)
    i, j = pairs[:, 0], pairs[:, 1]
    d = pos[i] - pos[j]
    d2 = (d * d).sum(1)
    tiny = d2 < 1e-12
    if tiny.any():  # 같은 자리 — 번호로 정한 방향으로 떼어 놓는다(결정적)
        angle = (i[tiny] * 2654435761 % 6283) / 1000.0
        d[tiny] = np.stack([np.cos(angle), np.sin(angle)], 1) * reach * 1e-3
        d2[tiny] = (d[tiny] ** 2).sum(1)
    fac = mass[i] * mass[j] * (1.0 / d2 - 1.0 / (reach * reach))
    fx = d[:, 0] * fac
    fy = d[:, 1] * fac
    return np.stack(
        [
            np.bincount(i, fx, n) - np.bincount(j, fx, n),
            np.bincount(i, fy, n) - np.bincount(j, fy, n),
        ],
        1,
    )


def layout(
    n: int,
    ea: np.ndarray,
    eb: np.ndarray,
    ew: np.ndarray,
    *,
    mass: np.ndarray,
    init: np.ndarray | None = None,
    iters: int = 300,
    fixed: np.ndarray | None = None,
    seed: int = 7,
    start_temp: float = 1.0,
    gravity: float = 1.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """힘 배치 — (자리 n x 2, 통계). `fixed` 가 참인 노드는 움직이지 않는다(덧붙임)."""
    started = time.perf_counter()
    rng = np.random.default_rng(seed)
    grid = 256 if n <= 5_000 else 512 if n <= 200_000 else 1024
    mass = mass.astype(np.float64)
    if init is None:
        scale = np.sqrt(max(n, 1)) * 10
        pos = rng.uniform(-scale, scale, size=(n, 2))
    else:
        pos = init.astype(np.float64).copy()
    movable = np.ones(n, bool) if fixed is None else ~fixed
    # 반발 세기를 끌림 세기에 맞춘다(FA2 의 scalingRatio — 평균 무게로).
    k = float(np.mean(ew)) if len(ew) else 1.0
    if n < 2 or not movable.any():
        return pos, {"iters": 0, "grid": grid, "seconds": 0.0}
    for it in range(iters):
        mesh, h = _repulsion(pos, mass, grid)
        force = mesh * (k * mass)[:, None] + _near(pos, mass, 2 * h) * k
        # 끌림 — 선마다 w·(상대 - 나), 양 끝에 반대로(선형 끌림, FA2).
        diff = (pos[eb] - pos[ea]) * ew[:, None]
        force[:, 0] += np.bincount(ea, diff[:, 0], n) - np.bincount(eb, diff[:, 0], n)
        force[:, 1] += np.bincount(ea, diff[:, 1], n) - np.bincount(eb, diff[:, 1], n)
        center = np.median(pos, axis=0)
        _lo, span = robust_box(pos)
        # 거리에 비례하는 중력(FA2 strongGravity) — 고립 노드가 멀리 튕겨 나가지 않는다.
        force -= gravity * mass[:, None] * (pos - center) / (span / 2)
        # 식히기: 한 번에 움직이는 상한 = 그림 폭의 일부.
        temp = start_temp * (0.05 + 0.95 * (1 - it / iters) ** 2)
        cap = 0.02 * span * temp
        norm = np.linalg.norm(force, axis=1) / mass + 1e-12
        step = force / mass[:, None]
        step *= np.minimum(1.0, cap / norm)[:, None]
        pos[movable] += step[movable]
    return pos, {
        "iters": iters,
        "grid": grid,
        "seconds": round(time.perf_counter() - started, 3),
    }


def normalized_weights(
    n: int, ea: np.ndarray, eb: np.ndarray, ew: np.ndarray, coref: np.ndarray, topk: int = 8
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """공동 참조 선의 무게를 정규화 — w_ab / √(W_a · W_b)(코사인), **노드마다 위 k 개만**.
    직접 선(참조 칸 · 관계)은 1. 안 거르면 공동 참조는 거의 완전 그래프다(개발 DB 274개에
    짝 8,653 — 측정 보고). 돌려주는 것: (ea, eb, w, 남긴 선의 원래 번호)."""
    w = ew.astype(np.float64).copy()
    keep = ~coref
    if coref.any():
        total = np.bincount(ea[coref], ew[coref], n) + np.bincount(eb[coref], ew[coref], n)
        w[coref] = ew[coref] / np.sqrt(
            np.maximum(total[ea[coref]], 1) * np.maximum(total[eb[coref]], 1)
        )
        idx = np.where(coref)[0]
        order = idx[np.lexsort((idx, -w[idx]))]
        taken = np.zeros(n, np.int64)
        for e in order:
            a, b = ea[e], eb[e]
            if taken[a] < topk or taken[b] < topk:
                keep[e] = True
                taken[a] += 1
                taken[b] += 1
        kept = coref & keep
        if kept.any():
            w[kept] = w[kept] / float(np.median(w[kept]))
    w[~coref] = 1.0
    at = np.where(keep)[0]
    return ea[at], eb[at], w[at], at


def disc_overlap_fix(
    pos: np.ndarray,
    radius: np.ndarray,
    *,
    fixed: np.ndarray | None = None,
    rounds: int = 120,
    margin: float = 1.02,
) -> tuple[np.ndarray, dict[str, Any]]:
    """원반(radius > 0)끼리 겹치면 밀어낸다 — 큰 원반은 덜 움직인다(넓이 무게). 고정한 원반은
    움직이지 않고, 고정한 것과 겹친 쪽이 그만큼을 다 움직인다."""
    started = time.perf_counter()
    pos = pos.copy()
    has = np.where(radius > 0)[0]
    if len(has) < 2:
        return pos, {"seconds": 0.0, "rounds": 0}
    r = radius[has]
    still = np.zeros(len(has), bool) if fixed is None else fixed[has]
    rmax = float(r.max())
    moved_rounds = 0
    big = np.where(r > np.percentile(r, 99))[0]
    for _ in range(rounds):
        p = pos[has]
        # 큰 원반은 따로 — 짝 찾기 반경을 (r_i + rmax) 로 두면 짝이 너무 많다.
        pairs = numeric.close_pairs(p, 2 * float(np.percentile(r, 99)) * margin)
        near_big = numeric.around(p, p[big], (r[big] + rmax) * margin)
        extra = [
            (b, j) for b, found in zip(big, near_big, strict=True) for j in found if j != b
        ]
        if extra:
            more = np.array(extra, dtype=np.int64)
            pairs = np.vstack([pairs, more]) if len(pairs) else more
        if len(pairs) == 0:
            break
        i, j = pairs[:, 0], pairs[:, 1]
        d = p[j] - p[i]
        dist = np.linalg.norm(d, axis=1)
        same = dist < 1e-9
        if same.any():  # 같은 자리 — 번호로 정한 방향
            angle = (np.minimum(i[same], j[same]) * 2654435761 % 6283) / 1000.0
            d[same] = np.stack([np.cos(angle), np.sin(angle)], 1)
            dist[same] = 1.0
        need = (r[i] + r[j]) * margin - dist
        hit = (need > 0) & ~(still[i] & still[j])
        if not hit.any():
            break
        moved_rounds += 1
        i, j, d, dist, need = i[hit], j[hit], d[hit], dist[hit], need[hit]
        unit = d / dist[:, None]
        ai, aj = r[i] ** 2, r[j] ** 2
        share_i = np.where(still[i], 0.0, np.where(still[j], 1.0, aj / (ai + aj)))
        share_j = 1.0 - share_i
        delta = np.zeros_like(p)
        # 짝마다 필요한 거리의 0.75 — 다 밀면(1.0) 이웃끼리 서로 튕겨 흔들리고, 절반이면
        # 60번으로도 몇 짝이 남았다(개발 DB 4,753 짝 중 1).
        np.add.at(delta, i, -unit * (need * share_i * 0.75)[:, None])
        np.add.at(delta, j, unit * (need * share_j * 0.75)[:, None])
        delta[still] = 0.0
        pos[has] += delta
    return pos, {"seconds": round(time.perf_counter() - started, 3), "rounds": moved_rounds}


def nearest_other(points: np.ndarray) -> np.ndarray:
    """점마다 가장 가까운 다른 점까지의 거리."""
    return numeric.nearest_other(points)


def overlapping(pos: np.ndarray, radius: np.ndarray) -> int:
    """아직 겹친 원반 짝의 수(0.1% 너그럽게) — 판의 통계에 적는다."""
    has = np.where(radius > 0)[0]
    if len(has) < 2:
        return 0
    p, r = pos[has], radius[has]
    pairs = numeric.close_pairs(p, 2 * float(r.max()))
    if len(pairs) == 0:
        return 0
    dist = np.linalg.norm(p[pairs[:, 0]] - p[pairs[:, 1]], axis=1)
    return int(((r[pairs[:, 0]] + r[pairs[:, 1]]) * 0.999 > dist).sum())


def align(old: np.ndarray, new: np.ndarray) -> np.ndarray:
    """`new` 를 `old` 에 맞춘 변환(이동 · 회전 · 크기, 뒤집기 없음)을 `new` 전체에 — 전체 다시
    배치가 지도를 통째로 돌리거나 키우지 않게. `old` 와 `new` 의 앞 len(old) 줄이 같은
    노드다."""
    m = len(old)
    if m < 3:
        return new
    a, b = old, new[:m]
    ma, mb = a.mean(0), b.mean(0)
    u, s, vt = np.linalg.svd((b - mb).T @ (a - ma))
    rot = u @ vt
    if np.linalg.det(rot) < 0:  # 뒤집기는 하지 않는다 — 지도가 거울상이 되면 낯설다
        u[:, -1] *= -1
        s[-1] *= -1
        rot = u @ vt
    scale = float(s.sum() / max(((b - mb) ** 2).sum(), 1e-12))
    return (new - mb) @ rot * scale + ma


def ring(
    center: np.ndarray, inner: float, sizes: np.ndarray, gap: float
) -> tuple[np.ndarray, float]:
    """바깥 띠 — 원 하나에 크기(반지름) 차례대로 늘어놓는다. 둘레가 모자라면 반지름을 키운다.
    (자리 n x 2, 쓴 반지름)."""
    n = len(sizes)
    if n == 0:
        return np.zeros((0, 2)), inner
    need = float((2 * sizes + gap).sum())
    radius = max(inner + float(sizes.max()), need / (2 * np.pi))
    arcs = np.cumsum(2 * sizes + gap) - (sizes + gap / 2)
    angle = arcs / max(need, 1e-12) * 2 * np.pi
    out = center + np.stack([np.cos(angle), np.sin(angle)], 1) * radius
    return out, radius
