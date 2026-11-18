"""트리의 **차례**와 **앞머리** — 타입의 `list_view.tree.sort` 와 `label_prefix`
(ADR 0086 · 0088).

문서의 절(「3.2 측정 방법」)을 객체로 넣을 때, 번호를 이름에 넣으면 이름으로 찾기 · 별칭 · 다른
판과의 대응이 번호에 묶인다(절 하나가 끼면 뒤의 이름이 다 바뀐다). 그래서 번호는 칸
(`properties.section_no`)에 두고, 트리가 그 칸으로 **차례를 세우고**(`sort`) 이름 앞에 **붙여
보인다**(`prefix`).

## 차례

같은 부모 아래(뿌리도 같다) — `sort` 의 칸을 앞에서부터, 마지막에 이름과 id 로 한 번 더(값이
같아도 차례가 흔들리지 않게 — 그렇지 않으면 펼칠 때마다 줄이 움직인다). `sort` 가 없으면
이름순.

- 일반 속성 칸 — 목록 정렬(`services.apply_sort`)과 같다: **숫자로 저장된 값은 숫자로**, 아니면
  글자로. 다만 그 사이에 하나를 더 둔다: **번호 모양의 글자**(`3` · `3.2` · `3.10` · `3.2.`)는
  마디마다 숫자로. 절 번호는 글자로 저장된다 — 글자로만 세우면 「3.10」 이 「3.2」 앞에
  선다(절이 열 개를 넘는 장마다 틀린다).
- 참조 칸 — **참조 대상의 이름**으로(여럿이면 가장 앞의 이름). 값은 상대의 id 라 그대로 세우면
  아무 뜻이 없다. 참조 색인(`object_refs`)으로 찾는다 — 저장된 글자를 id 로 바꾸지 않아도 된다
  (바꿀 수 없는 값에서 터지지 않는다).
- 기본 칸 — 식별자 · 이름 · 상태 · 만든 때 · 고친 때 · 소유 부서(부서의 이름).

## 앞머리

타입의 `label_prefix`(옛 자리 `tree.prefix`)로 정한 칸의 값을 글자로(참조면 상대의 이름,
여럿이면 쉼표로). 비었으면 없음. 볼 수 없는 상대의 이름은 싣지 않는다. 트리 밖(목록 · 상세 ·
참조 링크 · 검색 · 검토함)도 같은 값을 붙인다 — `objects/display.py`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectRef
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType
from app.modules.workspaces.models import Workspace
from app.shared.permissions import visible_owner_clause

#: 번호 모양의 글자 — 마디마다 숫자로 세운다(끝의 점 하나는 받는다: 「3.2.」).
DOTTED = r"^[0-9]+(\.[0-9]+)*\.?$"


def spec(list_view: dict[str, Any] | None) -> list[str]:
    """`sort` 칸들 — 정해 두지 않았으면 []."""
    tree = (list_view or {}).get("tree") or {}
    return [str(one) for one in tree.get("sort") or [] if isinstance(one, str) and one]


def prefix_field(object_type: ObjectType) -> str | None:
    """앞머리 칸 — 타입의 `label_prefix`(ADR 0088). 비었으면 옛 자리(`list_view.tree.prefix`,
    0.4.95)를 본다 — 마이그레이션 0106 과 쓰는 길이 옛 값을 옮기지만, 옮기기 전의 값이 남은
    곳(복원한 스냅샷 등)에서도 같은 이름이 보이게."""
    if object_type.label_prefix:
        return object_type.label_prefix
    tree = (object_type.list_view or {}).get("tree") or {}
    old = tree.get("prefix") if isinstance(tree, dict) else None
    return old if isinstance(old, str) and old else None


def order_by(db: Session, object_type: ObjectType, fields: list[str]) -> list[Any]:
    """트리 한 단계의 차례 — `fields` 를 앞에서부터, 마지막에 이름 · id."""
    kinds = {one.key: one.data_type for one in properties_of(db, object_type.id)}
    out: list[Any] = []
    for field in fields:
        out.extend(_keys(field, kinds))
    return [*out, ObjectInstance.label.asc(), ObjectInstance.id.asc()]


def _keys(field: str, kinds: dict[str, str]) -> list[Any]:
    if field == "label":
        return [ObjectInstance.label.asc()]
    if field == "key":
        return [ObjectInstance.key.asc().nulls_last()]
    if field == "status":
        return [ObjectInstance.status.asc()]
    if field == "updated_at":
        return [ObjectInstance.updated_at.asc()]
    if field == "created_at":
        return [ObjectInstance.created_at.asc()]
    if field == "owner_workspace":
        name = (
            select(Workspace.name)
            .where(Workspace.id == ObjectInstance.owner_workspace_id)
            .scalar_subquery()
        )
        return [name.asc().nulls_last()]
    if not field.startswith("properties."):
        return []  # 검증이 막는다 — 모르는 자리는 차례에 안 넣는다
    key = field.split(".", 1)[1]
    if kinds.get(key) == "object_ref":
        target = aliased(ObjectInstance)
        name = (
            select(func.min(target.label))
            .select_from(ObjectRef)
            .join(target, target.id == ObjectRef.dst_id)
            .where(ObjectRef.src_id == ObjectInstance.id, ObjectRef.key == key)
            .correlate(ObjectInstance)
            .scalar_subquery()
        )
        return [name.asc().nulls_last()]
    raw = ObjectInstance.properties[key]
    text: ColumnElement[str] = raw.astext
    number = case((func.jsonb_typeof(raw) == "number", cast(text, Numeric)), else_=None)
    dotted = case(
        (
            text.op("~")(DOTTED),
            cast(func.string_to_array(func.rtrim(text, "."), "."), ARRAY(Numeric)),
        ),
        else_=None,
    )
    return [number.asc().nulls_last(), dotted.asc().nulls_last(), text.asc().nulls_last()]


def prefixes(
    db: Session,
    user: User,
    object_type: ObjectType,
    field: str | None,
    rows: list[ObjectInstance],
) -> dict[uuid.UUID, str | None]:
    """노드마다 앞머리 — `field` 의 값을 글자로. 없으면 비운다."""
    if not field or not rows:
        return {}
    if field in ("key", "label", "status"):
        return {row.id: _text(getattr(row, field)) for row in rows}
    if field in ("updated_at", "created_at"):
        return {row.id: getattr(row, field).date().isoformat() for row in rows}
    if field == "owner_workspace":
        names = dict(
            db.execute(
                select(Workspace.id, Workspace.name).where(
                    Workspace.id.in_({row.owner_workspace_id for row in rows})
                )
            ).tuples()
        )
        return {
            row.id: names.get(row.owner_workspace_id) if row.owner_workspace_id else None
            for row in rows
        }
    if not field.startswith("properties."):
        return {}
    key = field.split(".", 1)[1]
    kinds = {one.key: one.data_type for one in properties_of(db, object_type.id)}
    values = {row.id: (row.properties or {}).get(key) for row in rows}
    if kinds.get(key) != "object_ref":
        return {object_id: _text(value) for object_id, value in values.items()}
    wanted: set[uuid.UUID] = set()
    for value in values.values():
        for one in value if isinstance(value, list) else [value]:
            try:
                wanted.add(uuid.UUID(str(one)))
            except ValueError:
                continue
    labels = {
        str(object_id): label
        for object_id, label in db.execute(
            select(ObjectInstance.id, ObjectInstance.label).where(
                ObjectInstance.id.in_(wanted),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        ).tuples()
    }
    out: dict[uuid.UUID, str | None] = {}
    for object_id, value in values.items():
        shown = [
            labels[str(one)]
            for one in (value if isinstance(value, list) else [value])
            if str(one) in labels
        ]
        out[object_id] = ", ".join(shown) or None
    return out


def _text(value: Any) -> str | None:
    """앞머리로 보일 글자 — 비면 None, 정수인 실수는 소수점 없이(「3.0」 이 아니라 「3」)."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(one for one in (_text(item) for item in value) if one) or None
    return str(value)
