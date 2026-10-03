"""방문 — 같은 시리얼의 기록을 날짜순으로 센 차례와 「정한 일수 안에 다시 왔나」(ADR 0014).

지표 정의에 `visits: {key: "properties.<시리얼 칸>", within_days: 90}` 을 두면 기준 주소로 둘을
쓸 수 있다.

    visit.number   1 · 2 · 3 · 4+   — 그 시리얼의 몇 번째 기록인가(시간 칸의 날짜순)
    visit.repeat   yes · no · open  — 이 기록 뒤 within_days 안에 같은 시리얼이 다시 왔나.
                                      창이 계산의 닫힘선(워터마크 - 닫힘 일수)을 넘으면 「아직
                                      열림」 — 더 올 수 있으므로 「아니오」 로 세지 않는다.

## 계산 문장 안의 창 함수

시리얼마다 날짜순 `row_number()` 와 다음 기록의 날짜(`lead`)를 **거르기를 통과한 기록만으로**
한 번 훑어 세고, 그 결과를 기록 id 로 바깥 조인해 기준처럼 묶는다. 여러 값 기준의 펼침보다
**먼저** 세야 한다 — 펼친 줄 위에서 세면 부품 셋인 기록이 세 번째 방문이 된다.

시리얼이 비었거나 날짜를 못 읽은 기록은 두 기준 모두 「(비어 있음)」 이다. 이 기준은 목록
조건으로 못 적어 건 보기에 「≈」 가 붙는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Date, String, and_, bindparam, case, cast, func, select

from app.modules.objects import axes, conditions
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType, PropertyDef

NUMBER = "visit.number"
REPEAT = "visit.repeat"
ADDRESSES = (NUMBER, REPEAT)
#: 기준마다 값의 가짓수 — 계획의 셀 어림에 쓴다(창 함수를 표본마다 돌리지 않는다).
CARDINALITY = {NUMBER: 4, REPEAT: 3}
NUMBER_LABELS = {"1": "첫 방문", "2": "두 번째", "3": "세 번째", "4+": "네 번째 이상"}
REPEAT_LABELS = {"yes": "예", "no": "아니오", "open": "아직 열림"}
#: 닫힘선 — 계산할 때 워터마크 - 닫힘 일수로 바꿔 넣는다(`compute.statement`).
CUTOFF = "visit_cutoff"
#: 시리얼로 쓸 수 있는 칸의 종류 — 값 하나로 같은 제품을 가리키는 것.
KEY_KINDS = ("text", "number", "enum")


class VisitsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    """같은 제품을 가리키는 자기 칸 — `properties.<시리얼 칸>`."""
    within_days: int = Field(default=90, ge=1, le=3650)
    """이 일수 안에 다시 오면 「재방문」."""


@dataclass
class Visits:
    key: PropertyDef
    within_days: int
    table: Any
    """(id, d, n, next_d) — 기록마다 날짜 · 차례 · 같은 시리얼의 다음 날짜."""
    cutoff: Any
    """닫힘선 bindparam — 기본값은 오늘, 계산이 워터마크로 바꾼다."""


class VisitError(ValueError):
    """정의가 방문을 못 세는 이유 — 지표 정의가 422 로 바꾼다."""


def build(
    source: ObjectType,
    defs: list[PropertyDef],
    visits_in: VisitsIn,
    time_key: str | None,
    conds: list[conditions.Condition],
    resolver: Any,
) -> Visits:
    if time_key is None:
        raise VisitError("방문은 시간 칸(접수일)의 날짜순으로 셉니다 — 시간 칸이 필요합니다.")
    if not visits_in.key.startswith("properties."):
        raise VisitError(
            f"방문의 시리얼 칸은 이 타입 자신의 칸(properties.<칸>)이어야 합니다: "
            f"{visits_in.key}"
        )
    key = axes.own_property(defs, visits_in.key)
    if key.data_type not in KEY_KINDS or key.multi:
        raise VisitError(
            f"「{key.label}」 은 값 하나로 같은 제품을 가리키는 칸(글자 · 숫자 · 고를 "
            "값)이어야 합니다."
        )
    serial = ObjectInstance.properties[key.key].astext
    when = axes.date_or_null(ObjectInstance.properties[time_key].astext)
    base = select(ObjectInstance.id.label("id"), when.label("d"), serial.label("s")).where(
        ObjectInstance.type_id == source.id,
        ObjectInstance.deleted_at.is_(None),
        serial.is_not(None),
        serial != "",
        when.is_not(None),
    )
    base = conditions.apply(base, defs, conds, resolver)
    rows = base.subquery("vb")
    window = {"partition_by": rows.c.s, "order_by": (rows.c.d, rows.c.id)}
    table = select(
        rows.c.id,
        rows.c.d,
        func.row_number().over(**window).label("n"),
        func.lead(rows.c.d).over(**window).label("next_d"),
    ).subquery("vv")
    cutoff = bindparam(CUTOFF, value=date.today(), type_=Date)
    return Visits(key, visits_in.within_days, table, cutoff)


def _namer(labels: dict[str, str]) -> Callable[[list[str]], dict[str, str]]:
    return lambda keys: {one: labels.get(one, one) for one in keys}


def axis(visits: Visits, address: str) -> axes.Axis:
    """`visit.*` 주소 → 기준. 두 기준이 같은 조인(창 함수 결과)을 함께 쓴다."""
    table = visits.table
    join = (table, table.c.id == ObjectInstance.id)
    if address == NUMBER:
        expr: Any = case(
            (table.c.n.is_(None), cast(None, String)),
            (table.c.n >= 4, "4+"),
            else_=cast(table.c.n, String),
        )
        return axes.Axis(
            expr,
            "방문 차례",
            "visit",
            joins=[join],
            namer=_namer(NUMBER_LABELS),
            address=address,
        )
    within = visits.within_days
    expr = case(
        (table.c.id.is_(None), cast(None, String)),
        (and_(table.c.next_d.is_not(None), table.c.next_d - table.c.d <= within), "yes"),
        (table.c.d + within > visits.cutoff, "open"),
        else_="no",
    )
    return axes.Axis(
        expr,
        f"{within}일 안 재방문",
        "visit",
        joins=[join],
        namer=_namer(REPEAT_LABELS),
        address=address,
    )
