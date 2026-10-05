"""수치 계산의 문 — **scipy 는 이 파일에서만 부른다**(구조 시험이 지킨다, ADR 0014).

scipy 는 타입 표시가 없다(mypy 는 Any 로 본다). 그래서 결과를 여기서 `float` · `ndarray` 로
바꿔 내보낸다 — Any 가 레시피로 번지지 않게. import 는 함수 안에서 한다: 분석을 안 묻는
프로세스(작업 워커)는 scipy 를 메모리에 올리지 않는다(올리는 데 0.3~0.6초 · 약 50MB).

scipy 를 빼야 하는 날이 오면 이 파일만 numpy 로 다시 쓰면 된다 — 레시피는 이 파일의 이름만
안다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
from numpy.typing import NDArray

Vector = NDArray[np.float64]


@dataclass
class Optimum:
    x: Vector
    fun: float
    success: bool
    message: str


def minimize(fun: Callable[[Vector], float], x0: Vector) -> Optimum:
    """넬더-미드로 찾고 BFGS 로 다듬는다 — 우도 면이 울퉁불퉁한 초기에는 넬더-미드가 덜 빠지고,
    끝에서는 기울기 방법이 정밀하다. 다듬은 것이 나쁘면 첫 결과를 쓴다."""
    from scipy import optimize

    first = optimize.minimize(
        fun,
        x0,
        method="Nelder-Mead",
        options={"xatol": 1e-9, "fatol": 1e-11, "maxiter": 6000, "maxfev": 12000},
    )
    best = first
    try:
        second = optimize.minimize(fun, first.x, method="BFGS", options={"gtol": 1e-9})
        if np.isfinite(second.fun) and second.fun <= first.fun:
            best = second
    except (ValueError, FloatingPointError):  # pragma: no cover - 수치가 무너지면 첫 결과
        pass
    return Optimum(
        x=np.asarray(best.x, dtype=np.float64),
        fun=float(best.fun),
        success=bool(first.success or best.success),
        message=str(best.message),
    )


def chi2_sf(x: float, df: float) -> float:
    """카이제곱 꼬리 확률 P(X ≥ x)."""
    from scipy import stats

    return float(stats.chi2.sf(x, df))


def chi2_ppf(q: float, df: float) -> float:
    """카이제곱 분위수 — 포아송 정확 구간(가우드)에 쓴다."""
    from scipy import stats

    return float(stats.chi2.ppf(q, df))


def norm_ppf(q: float) -> float:
    """표준정규 분위수 — 표준 라이브러리로 충분하다(scipy 를 안 깨운다)."""
    return NormalDist().inv_cdf(q)


def hypergeom_sf(k: int, total: int, successes: int, draws: int) -> float:
    """초기하 꼬리 확률 P(X ≥ k) — 연관(향상도)의 유의성."""
    from scipy import stats

    return float(stats.hypergeom.sf(k - 1, total, successes, draws))


def binom_two_sided(ks: Sequence[int], n: int, ps: Sequence[float]) -> list[float]:
    """이항 정확 검정의 양쪽 p 를 **한 번에** — 집단 비교에서 집단마다 「그 집단 대 나머지」
    (조건부 포아송). 하나씩 부르면 집단 2천 개에 2.4초, 배열로 한 번이면 0.01초(값은 같다)."""
    from scipy import stats

    if n <= 0 or not ks:
        return [1.0] * len(ks)
    found = stats.binomtest(
        np.asarray(ks, dtype=np.int64), n, np.clip(np.asarray(ps, dtype=np.float64), 0.0, 1.0)
    ).pvalue
    return [float(one) for one in np.atleast_1d(found)]


def gamma_ppf(q: float, shapes: Sequence[float], rates: Sequence[float]) -> list[float]:
    """감마(모양, 비율) 분위수를 한 번에 — 줄인 비율의 구간."""
    from scipy import stats

    rate = np.asarray(rates, dtype=np.float64)
    found = stats.gamma.ppf(q, np.asarray(shapes, dtype=np.float64), scale=1.0 / rate)
    return [float(one) for one in np.atleast_1d(found)]


def beta_ppf(q: float, a: float, b: float) -> float:
    """베타 분위수 — 클로퍼-피어슨 구간(전후 비교의 조건부 이항). 모수는 실수여도
    된다(과분산이면 건수를 φ 로 나눈 「실효 건수」)."""
    from scipy import stats

    return float(stats.beta.ppf(q, a, b))
