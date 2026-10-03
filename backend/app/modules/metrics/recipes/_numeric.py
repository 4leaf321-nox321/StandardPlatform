"""수치 계산의 문 — **scipy 는 이 파일에서만 부른다**(구조 시험이 지킨다, ADR 0014).

scipy 는 타입 표시가 없다(mypy 는 Any 로 본다). 그래서 결과를 여기서 `float` · `ndarray` 로
바꿔 내보낸다 — Any 가 레시피로 번지지 않게. import 는 함수 안에서 한다: 분석을 안 묻는
프로세스(작업 워커)는 scipy 를 메모리에 올리지 않는다(올리는 데 0.3~0.6초 · 약 50MB).

scipy 를 빼야 하는 날이 오면 이 파일만 numpy 로 다시 쓰면 된다 — 레시피는 이 파일의 이름만
안다.
"""

from __future__ import annotations

from collections.abc import Callable
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
