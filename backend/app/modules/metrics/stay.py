"""머무는 기간 — 기록 하나를 그 기간부터 N기간 동안 계속 센다(ADR 0023).

지표 정의에 `stay: {periods: 12}` 를 두면 기간 P 의 값은 **최근 N기간(P-N+1 ~ P)의 합**이다.
판매 대수를 보증 기간 동안 세면 「보증 중 대수」, 3년 동안 세면 「쓰이는 대수」 — 접수월 기준
인입률의 분모가 된다. 판매 집계를 따로 가공해 넣지 않아도 된다.

    periods        머무는 기간 수(시간 칸의 단위) — 기본이자 최대
    periods_from   기록마다 기간 수를 읽을 숫자 칸(선택) — 자기 칸이나 참조 너머의 칸.
                   「국가의 보증 기간」 처럼 기록마다 다르면. 비었거나 못 읽으면 `periods`,
                   `periods` 보다 크면 `periods` 로 자른다

## 계산 문장 안의 펼침

계산은 먼저 (부서, 기간, 기준 값들, 머무는 기간 수)로 묶고, 그 셀을 0 ~ N-1 기간 뒤로 펼친 뒤
다시 묶는다 — 기록이 아니라 **묶은 셀**을 펼쳐 싸다. 건수 · 합 · 값 수는 더하고 최소 · 최대는
다시 최소 · 최대라 집계 다섯이 모두 맞는다(평균 · 조건 비율도 따라 맞는다). 계산 시점
(워터마크)의 기간을 넘는 미래 기간은 만들지 않는다. 날짜를 못 읽은 기록은 펼치지 않는다.

코호트 · 방문과는 함께 두지 않는다 — 경과(기간 - 코호트)와 방문 차례가 펼친 기간에서 뜻을
잃는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Date, Integer, bindparam, cast, func, literal

from app.modules.objects import axes

#: 머무는 기간 수의 상한 — 월이면 10년.
MAX_PERIODS = 120
#: 미래 기간을 자르는 선 — 계산할 때 워터마크 날짜로 바꿔 넣는다(`compute.statement`).
UNTIL = "stay_until"


class StayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periods: int = Field(ge=1, le=MAX_PERIODS)
    """머무는 기간 수(시간 칸의 단위) — `periods_from` 이 있으면 기본이자 최대."""
    periods_from: str | None = Field(default=None, max_length=200)
    """기록마다 기간 수를 읽을 숫자 칸 — `properties.<칸>` 이나 참조 너머(`ref.country.
    warranty_months`). 값은 시간 칸의 단위로 읽는다."""


@dataclass
class Stay:
    periods: int
    source: axes.Axis | None
    """`periods_from` 의 기준(숫자 칸) — 없으면 모두 `periods`."""
    until: Any
    """미래를 자르는 bindparam — 기본값은 오늘, 계산이 워터마크로 바꾼다."""

    @property
    def varies(self) -> bool:
        """기록마다 기간 수가 다른가 — 그러면 건 보기의 범위가 「≈」 다."""
        return self.source is not None


class StayError(ValueError):
    """정의가 머무는 기간을 못 세는 이유 — 지표 정의가 422 로 바꾼다."""


def build(stay_in: StayIn, plan: axes.JoinPlan, *, cohort: bool, visits: bool) -> Stay:
    if cohort:
        raise StayError(
            "머무는 기간과 코호트는 함께 둘 수 없습니다 — 펼친 기간에서 경과(기간 - 코호트)가 "
            "뜻을 잃습니다."
        )
    if visits:
        raise StayError("머무는 기간과 방문은 함께 둘 수 없습니다.")
    source: axes.Axis | None = None
    if stay_in.periods_from:
        source = plan.axis(stay_in.periods_from)
        if source.kind != "number":
            raise StayError(
                f"기간 수를 읽을 칸 「{source.label}」 은 숫자 칸이어야 합니다({source.kind})."
            )
        if source.multi:
            raise StayError(
                f"기간 수를 읽을 칸 「{source.label}」 은 값이 하나여야 합니다 — 여러 값 "
                "칸이나 여럿과 이어진 걸음은 안 됩니다."
            )
    until = bindparam(UNTIL, value=date.today(), type_=Date)
    return Stay(stay_in.periods, source, until)


def length(stay: Stay) -> Any:
    """기록마다의 머무는 기간 수(1 ~ periods) — 계산 문장의 안쪽이 이것으로도 묶는다."""
    if stay.source is None:
        return literal(stay.periods, Integer)
    found = cast(func.round(axes.numeric(stay.source.expr)), Integer)
    return func.least(func.greatest(func.coalesce(found, stay.periods), 1), stay.periods)


def shifted(period: Any, steps: Any, grain: str) -> Any:
    """기간 시작일에서 `steps` 기간 뒤의 시작일(date) — `query.advance` 의 SQL 짝."""
    zero = literal(0, Integer)
    interval = {
        "day": func.make_interval(zero, zero, zero, steps),
        "week": func.make_interval(zero, zero, steps),
        "month": func.make_interval(zero, steps),
        "quarter": func.make_interval(zero, steps * 3),
        "year": func.make_interval(steps),
    }[grain]
    return cast(period + interval, Date)


def earliest(period: date, periods: int, grain: str) -> date:
    """기간 P 의 값에 든 기록의 가장 이른 기간 — P 에서 N-1 기간 앞. 건 보기의 범위."""
    if grain == "day":
        return period - timedelta(days=periods - 1)
    if grain == "week":
        return period - timedelta(days=7 * (periods - 1))
    months = {"month": 1, "quarter": 3, "year": 12}[grain] * (periods - 1)
    total = period.year * 12 + (period.month - 1) - months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)
