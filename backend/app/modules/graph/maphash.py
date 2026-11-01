"""지도의 기록 자리 — **id 의 해시에서 정한다. 저장하지 않는다**(ADR 0046).

기록(수천만)은 자리를 표에 적지 않는다. 기록은 그 「주 참조」 가 가리키는 기준정보(허브)의
원반 안에 놓이고, 원반 안의 자리는 기록 id 의 해시가 정한다 — 같은 id 는 늘 같은 자리다.
파이썬(`place`)과 SQL(`mapread._xy` · DB 함수 `sp_map_hash`)이 **같은 식**이다.

    h = md5(id 의 글자)의 앞 64비트(부호 있는 bigint) — **영구 고정.** 바꾸면 모든 기록의
        자리가 바뀌고 확대 점 표(`graph_map_points`)를 다시 채워야 한다.
    비트: [u 줄 6][v 칸 6][u 나머지 26][v 나머지 26]
    u = (u줄 + u나머지 / 2^26) / 64,  v = (v칸 + v나머지 / 2^26) / 64
    자리 = 허브 + R·√u·(cos 2πv, sin 2πv)   — 원반 위에 고르게(넓이 보존)

비트를 이렇게 놓으면 원반을 64 x 64 칸(고리 u줄 x 부채꼴 v칸)으로 나눈 칸 하나가 h 의 **이어진
구간** 하나다 — (허브, 칸, h) 색인이 있으면 상자에 닿는 칸만 구간으로 읽는다(`cells_for_box`).
원반 안의 자리는 **뜻이 없다**(고른 잡음) — 뜻은 허브의 자리와 원반의 넓이에만 있다.

PG 의 `uuid_hash_extended` 는 두 배 빠르지만 판(버전)끼리 같은 값을 준다는 약속이 없다 —
그래서 md5 다(측정 보고 2026-11-01).
"""

from __future__ import annotations

import hashlib
import math
import uuid

import numpy as np

#: 원반을 나누는 고리 수 · 부채꼴 수(각 6비트).
K = 64
_M26 = (1 << 26) - 1
_TWO26 = float(1 << 26)

#: DB 함수 — 색인과 질의가 **같은 식**을 써야 색인을 탄다. 바꾸면 새 이름으로 만든다(0093 이
#: 운영의 사본이다).
HASH_SQL = """
CREATE OR REPLACE FUNCTION sp_map_hash(id uuid) RETURNS bigint
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT ('x' || substr(md5(id::text), 1, 16))::bit(64)::bigint
$$
"""


def hash_of(object_id: uuid.UUID | str) -> int:
    """md5(id 글자)의 앞 64비트 — 부호 있는 정수(SQL 의 bigint 와 같다)."""
    value = int(hashlib.md5(str(object_id).encode()).hexdigest()[:16], 16)
    return value - (1 << 64) if value >= (1 << 63) else value


def uv(h: int) -> tuple[float, float]:
    """해시 → 원반 위의 (u, v) — u 는 넓이 몫(반지름은 √u), v 는 각도 몫."""
    raw = h & ((1 << 64) - 1)
    u = (((raw >> 58) & 63) + ((raw >> 26) & _M26) / _TWO26) / K
    v = (((raw >> 52) & 63) + (raw & _M26) / _TWO26) / K
    return u, v


def place(h: int, cx: float, cy: float, r: float) -> tuple[float, float]:
    """원반(cx, cy, r) 안의 자리."""
    u, v = uv(h)
    rad = r * math.sqrt(u)
    angle = 2 * math.pi * v
    return cx + rad * math.cos(angle), cy + rad * math.sin(angle)


def uv_array(h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    raw = h.astype(np.int64).view(np.uint64)
    u = (
        ((raw >> np.uint64(58)) & np.uint64(63)).astype(np.float64)
        + ((raw >> np.uint64(26)) & np.uint64(_M26)).astype(np.float64) / _TWO26
    ) / K
    v = (
        ((raw >> np.uint64(52)) & np.uint64(63)).astype(np.float64)
        + (raw & np.uint64(_M26)).astype(np.float64) / _TWO26
    ) / K
    return u, v


def _signed(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def _sector_box(
    cx: float, cy: float, r: float, i: int, j: int
) -> tuple[float, float, float, float]:
    r0, r1 = r * math.sqrt(i / K), r * math.sqrt((i + 1) / K)
    a0, a1 = 2 * math.pi * j / K, 2 * math.pi * (j + 1) / K
    xs: list[float] = []
    ys: list[float] = []
    for rr in (r0, r1):
        for a in (a0, a1):
            xs.append(cx + rr * math.cos(a))
            ys.append(cy + rr * math.sin(a))
    for q in range(5):
        a = q * math.pi / 2
        if a0 <= a <= a1:
            xs.append(cx + r1 * math.cos(a))
            ys.append(cy + r1 * math.sin(a))
    return min(xs), max(xs), min(ys), max(ys)


Box = tuple[float, float, float, float]
"""(x0, y0, x1, y1)."""


def cells_for_box(cx: float, cy: float, r: float, box: Box) -> list[tuple[int, int]]:
    """원반(cx, cy, r)에서 상자에 닿을 수 있는 칸 → h 구간 [lo, hi] 목록(이웃한 구간은 합친다).

    원반이 상자 안에 통째로 들면 구간 하나(전부)다. 줄(i)마다 v 칸의 이어진 덩어리가 구간
    하나다 — 뜨거운 원반 안의 작은 상자는 구간 16개, 0.3R 상자는 46개였다(측정 보고)."""
    x0, y0, x1, y1 = box
    if cx - r >= x0 and cx + r <= x1 and cy - r >= y0 and cy + r <= y1:
        return [(-(1 << 63), (1 << 63) - 1)]
    dx = max(x0 - cx, 0.0, cx - x1)
    dy = max(y0 - cy, 0.0, cy - y1)
    if math.hypot(dx, dy) > r:
        return []
    runs: list[tuple[int, int, int]] = []
    for i in range(K):
        r1 = r * math.sqrt((i + 1) / K)
        if math.hypot(dx, dy) > r1:
            continue
        run: tuple[int, int] | None = None
        for j in range(K):
            bx0, bx1, by0, by1 = _sector_box(cx, cy, r, i, j)
            hit = not (bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1)
            if hit:
                run = (run[0], j) if run else (j, j)
            elif run:
                runs.append((i, run[0], run[1]))
                run = None
        if run:
            runs.append((i, run[0], run[1]))
    ranges = sorted(
        (_signed((i << 58) | (j0 << 52)), _signed((i << 58) + ((j1 + 1) << 52) - 1))
        for i, j0, j1 in runs
    )
    merged: list[tuple[int, int]] = []
    for lo, hi in ranges:
        if merged and lo == merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return merged


#: 원반 ∩ 상자 넓이를 셀 때 x 방향 조각 수 — 조각마다 그 x 의 원 높이(√(r² - dx²))와 상자의 y
#: 범위가 겹치는 길이를 더한다. 원반 위 표본(2,304점)으로 세면 원반에 견줘 아주 작은
#: 상자(뜨거운 원반 안 변 0.1R)에서 표본 몇 개로 갈려 기대 수가 25% 틀렸다(4,340 vs 실제 3,260
#: — 2,000만 DB 실측).
SLICES = 256


def box_fractions(cx: np.ndarray, cy: np.ndarray, r: np.ndarray, box: Box) -> np.ndarray:
    """원반마다 넓이 중 상자 안의 몫(0 ~ 1). 통째로 들거나 안 닿는 원반은 바로, 걸친 원반만 x
    조각 적분으로(조각 256 — 1% 안쪽). 기대 수는 이것 x 집계로 센다(기록을 안 읽는다)."""
    x0, y0, x1, y1 = box
    out = np.zeros(len(cx))
    inside = (cx - r >= x0) & (cx + r <= x1) & (cy - r >= y0) & (cy + r <= y1)
    out[inside] = 1.0
    near_x = np.maximum.reduce([x0 - cx, np.zeros_like(cx), cx - x1])
    near_y = np.maximum.reduce([y0 - cy, np.zeros_like(cy), cy - y1])
    touch = ~inside & (np.hypot(near_x, near_y) <= r) & (r > 0)
    at = np.where(touch)[0]
    t = (np.arange(SLICES) + 0.5) / SLICES
    for start in range(0, len(at), 512):
        part = at[start : start + 512]
        a = np.maximum(cx[part] - r[part], x0)
        b = np.minimum(cx[part] + r[part], x1)
        width = np.maximum(b - a, 0.0)
        xs = a[:, None] + width[:, None] * t[None, :]
        dx = xs - cx[part, None]
        half = np.sqrt(np.maximum(r[part, None] ** 2 - dx * dx, 0.0))
        top = np.minimum(cy[part, None] + half, y1)
        bottom = np.maximum(cy[part, None] - half, y0)
        area = np.maximum(top - bottom, 0.0).sum(axis=1) * width / SLICES
        out[part] = np.minimum(area / (np.pi * r[part] ** 2), 1.0)
    return out
