"""롤업 — **「아래 전부」 의 숫자를 트리 관계로 모은다.**

어셈블리의 무게는 부품 무게의 합이고, 과제의 예산은 하위 과제 예산의 합이다. 그것을
객체에 **저장하지 않는다** — 저장하면 부품 하나를 고친 날 어셈블리는 옛 값을 보여 주고,
그 차이는 아무 데도 안 뜬다. 볼 때마다 센다.

무엇을 모을지는 타입의 `list_view.rollups` 가 정한다(`{property, fn, label}`):
트리 관계(`list_view.tree`)로 아래 전부를 펼쳐, 그 객체들의 숫자 속성을 `sum` · `min` ·
`max` · `avg` · `count` 로 모은다. 여러 값 칸이면 값 하나하나가 든다.

**빠진 것을 말한다.** 아래 30개 중 4개가 무게가 비어 있으면 합계 옆에 「4개 값 없음」 이
붙는다 — 안 붙이면 그 합계는 「전부의 합」 으로 읽히고, 그것은 틀린 수다. 남의 부서
것은 보이지 않으므로 **세지도 않는다** — 그러면 합계가 사람마다 다를 수 있는데, 그것이
이 틀의 규칙(보이는 것만)이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import graph
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
) -> list[Rollup]:
    specs = specs_of(object_type)
    if not specs:
        return []
    ids = graph.descendant_ids(db, relation=relation, root_id=row.id, parent_end=parent_end)
    below = (
        list(
            db.scalars(
                select(ObjectInstance).where(
                    ObjectInstance.id.in_(ids),
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
        values: list[float] = []
        holders = 0
        for one in below:
            found = _numbers((one.properties or {}).get(key))
            if found:
                holders += 1
                values.extend(found)
        out.append(
            Rollup(
                property=key,
                label=str(
                    spec.get("label") or f"{labels.get(key, key)} {_FN_LABEL.get(fn, fn)}"
                ),
                fn=fn,
                value=_fold(fn, values),
                count=holders,
                missing=len(below) - holders,
                descendants=len(below),
            )
        )
    return out


_FN_LABEL = {"sum": "합계", "min": "최소", "max": "최대", "avg": "평균", "count": "개수"}
