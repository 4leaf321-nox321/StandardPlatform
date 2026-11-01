"""롤업 — **「아래 전부」 의 숫자를 트리 관계로 모은다.**

어셈블리의 무게는 부품 무게의 합이고, 과제의 예산은 하위 과제 예산의 합이다. 그것을
객체에 **저장하지 않는다** — 저장하면 부품 하나를 고친 날 어셈블리는 옛 값을 보여 주고,
그 차이는 아무 데도 안 뜬다. 볼 때마다 센다.

무엇을 모을지는 타입의 `list_view.rollups` 가 정한다(`{property, fn, label, weighted,
leaves_only}`): 트리 관계(`list_view.tree`)로 아래 전부를 펼쳐, 그 객체들의 숫자 속성을 `sum` ·
`min` · `max` · `avg` · `count` · `product` 로 모은다. 여러 값 칸이면 값 하나하나가 든다.

**기준일이 있다**(ADR 0039) — 그날 살아 있는 선으로 펼친다(기본 오늘). 3월에 빠진 부품은 4월
기준의 무게에 안 든다.

## 수량 반영 · 말단만(ADR 0040)

- `weighted`(「수량 반영」) — 아래 객체마다 **총수량** Q(전개가 경로 따라 곱해 더한 것, 같은
  기준일)만큼 센다: sum → Σ v·Q, count → Σ Q(총 개수), avg → Σ v·Q / Σ Q, product → Π v^Q
  (부품마다 신뢰도 v 를 Q 개 직렬로 — 직렬 신뢰도), min · max 는 그대로. 수량 칸이 빈 선은 1 로
  치고 그 수를 `quantity_missing` 으로 함께 준다. 트리에 순환이 있으면 총수량이 없다 — 그
  롤업만 값 없이 까닭(`note`)을 단다.
- `leaves_only`(「말단만」) — 그 기준일의 트리에서 아래 선이 없는 객체만 모은다. **중간
  조립품에도 값이 있으면 두 번 센다** — 어셈블리의 무게(부품 무게의 합으로 적어 둔 값)와 그
  부품의 무게가 함께 더해진다. 무게 · 원가처럼 말단에만 원값이 있는 숫자는 「말단만」 으로
  모은다.
- 둘 다 꺼진 롤업은 예전과 같은 값이다(`product` 는 Π v).

**빠진 것을 말한다.** 아래 30개 중 4개가 무게가 비어 있으면 합계 옆에 「4개 값 없음」 이
붙는다 — 안 붙이면 그 합계는 「전부의 합」 으로 읽히고, 그것은 틀린 수다. 남의 부서
것은 보이지 않으므로 **세지도 않는다** — 그러면 합계가 사람마다 다를 수 있는데, 그것이
이 틀의 규칙(보이는 것만)이다. 다만 남의 부서 · 지운 객체도 **지나가기는 한다** — 그 아래의
보이는 객체는 세고, 총수량도 그 경로의 몫을 받는다(`graph.descendant_ids` · `explode` 와 같은
뜻).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import explode, graph
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared.permissions import visible_owner_clause


@dataclass(frozen=True)
class Rollup:
    property: str
    label: str
    fn: str
    value: float | None
    """모은 값. 값이 하나도 없으면 None — 0 으로 두면 「합이 0」 으로 읽힌다."""
    count: int
    """값이 든 객체 수."""
    missing: int
    """아래에 있지만 값이 빈 객체 수."""
    descendants: int
    """모은 대상 객체 수 — 「말단만」 이면 말단의 수."""
    weighted: bool = False
    leaves_only: bool = False
    quantity_missing: int = 0
    """「수량 반영」 — 아래 선 중 수량 칸이 빈 것(1 로 셌다)."""
    note: str = ""
    """값을 못 낸 까닭(트리의 순환 · 음수의 거듭제곱) — 비었으면 없다."""


def specs_of(object_type: ObjectType) -> list[dict[str, Any]]:
    return [
        one
        for one in (object_type.list_view or {}).get("rollups") or []
        if isinstance(one, dict) and one.get("property")
    ]


def _numbers(raw: Any) -> list[float]:
    items = raw if isinstance(raw, list) else [raw]
    out: list[float] = []
    for item in items:
        if isinstance(item, bool) or item is None or item == "":
            continue
        if isinstance(item, int | float):
            out.append(float(item))
    return out


def _fold(fn: str, values: list[float]) -> float | None:
    if fn == "count":
        return float(len(values))
    if not values:
        return None
    if fn == "sum":
        return sum(values)
    if fn == "min":
        return min(values)
    if fn == "max":
        return max(values)
    if fn == "avg":
        return sum(values) / len(values)
    if fn == "product":
        return math.prod(values)
    return None


class _NoValue(Exception):
    """수량 반영으로 셀 수 없다 — 까닭을 롤업의 `note` 로."""


def _fold_weighted(fn: str, pairs: list[tuple[float, float]]) -> float | None:
    """(값, 총수량) 짝을 모은다 — 값 하나하나가 그 객체의 총수량만큼 든다."""
    if fn == "count":
        return float(sum(weight for _, weight in pairs))
    if not pairs:
        return None
    if fn == "sum":
        return sum(value * weight for value, weight in pairs)
    if fn == "min":
        return min(value for value, _ in pairs)
    if fn == "max":
        return max(value for value, _ in pairs)
    if fn == "avg":
        weights = sum(weight for _, weight in pairs)
        if weights == 0:
            return None
        return sum(value * weight for value, weight in pairs) / weights
    if fn == "product":
        if any(value < 0 and weight != int(weight) for value, weight in pairs):
            raise _NoValue("음수 값을 정수가 아닌 총수량만큼 거듭제곱할 수 없습니다")
        try:
            return math.prod(math.pow(value, weight) for value, weight in pairs)
        except (ValueError, OverflowError) as caught:
            raise _NoValue(f"총수량만큼 거듭제곱할 수 없습니다({caught})") from caught
    return None


def compute(
    db: Session,
    user: User,
    object_type: ObjectType,
    row: ObjectInstance,
    defs: list[PropertyDef],
    *,
    relation: str,
    parent_end: graph.ParentEnd,
    at: date | None,
) -> list[Rollup]:
    specs = specs_of(object_type)
    if not specs:
        return []
    deep: explode.Below | None = None
    if any(spec.get("weighted") or spec.get("leaves_only") for spec in specs):
        # 총수량 · 말단은 전개의 선 집합에서 — 닿는 노드는 `descendant_ids` 와 같다.
        tree = graph.Tree(relation, parent_end, object_type.id, object_type.label)
        deep = explode.below(db, tree, row.id, at=at)
        ids = list(deep.nodes)
    else:
        ids = graph.descendant_ids(
            db, relation=relation, root_id=row.id, parent_end=parent_end, at=at
        )
    below = (
        list(
            db.scalars(
                select(ObjectInstance).where(
                    graph.any_of(ObjectInstance.id, ids),
                    ObjectInstance.deleted_at.is_(None),
                    visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                )
            )
        )
        if ids
        else []
    )
    labels = {d.key: d.label for d in defs}
    out: list[Rollup] = []
    for spec in specs:
        key = str(spec["property"])
        fn = str(spec.get("fn") or "sum")
        weighted = bool(spec.get("weighted"))
        leaves_only = bool(spec.get("leaves_only"))
        chosen = below
        if leaves_only and deep is not None:
            chosen = [one for one in below if one.id in deep.leaves]
        out.append(
            _one(
                spec,
                key=key,
                fn=fn,
                label=labels.get(key, key),
                chosen=chosen,
                weighted=weighted,
                leaves_only=leaves_only,
                deep=deep,
            )
        )
    return out


def _one(
    spec: dict[str, Any],
    *,
    key: str,
    fn: str,
    label: str,
    chosen: list[ObjectInstance],
    weighted: bool,
    leaves_only: bool,
    deep: explode.Below | None,
) -> Rollup:
    values: list[float] = []
    pairs: list[tuple[float, float]] = []
    holders = 0
    totals = deep.total if deep is not None else None
    for one in chosen:
        found = _numbers((one.properties or {}).get(key))
        if found:
            holders += 1
            values.extend(found)
            if totals is not None:
                weight = totals.get(one.id, 0)
                pairs.extend((value, weight) for value in found)
    value: float | None
    note = ""
    if not weighted:
        value = _fold(fn, values)
    elif totals is None:
        value = None
        note = (
            "트리가 순환해 총수량을 셀 수 없습니다 — 수량 반영 없이 보거나 순환을 해제하세요"
        )
    else:
        try:
            value = _fold_weighted(fn, pairs)
        except _NoValue as caught:
            value, note = None, str(caught)
    return Rollup(
        property=key,
        label=str(spec.get("label") or f"{label} {_FN_LABEL.get(fn, fn)}"),
        fn=fn,
        value=value,
        count=holders,
        missing=len(chosen) - holders,
        descendants=len(chosen),
        weighted=weighted,
        leaves_only=leaves_only,
        quantity_missing=deep.missing if weighted and deep is not None else 0,
        note=note,
    )


_FN_LABEL = {
    "sum": "합계",
    "min": "최소",
    "max": "최대",
    "avg": "평균",
    "count": "개수",
    "product": "곱",
}
