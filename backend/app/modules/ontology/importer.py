"""정의를 통째로 받아 적용한다 — **기계가 만들 때 필요한 셋.**

사람은 타입 하나를 5분에 만들고, 에이전트는 **타입 20개·속성 200개를 5초에**
만든다. 그러면 사람이 만들 때는 없던 것이 필요해진다
([ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md)):

    1. 일괄 적용   한 칸씩이면 200번 왕복하고, 중간에 실패하면
                   **반쯤 만들어진 온톨로지가 남는다**
    2. 미리 보기   무엇이 바뀌는지, **그리고 위험한 것**
    3. 되돌리기    감사 로그는 누가 뭘 했는지는 알려 주지만 되돌려 주지 않는다

## 지우지는 않는다

가져오기는 **더하고 고치기만** 한다. 「스키마에 없으니 지운다」 로 만들면, 부분
스키마를 한 번 보낸 날 **그 타입의 객체가 통째로 갈 곳을 잃는다.** 지우기는
사람이 한 건씩 누르는 일로 남긴다 — 그 자리에는 무엇이 걸렸는지 보여 주는
확인 창이 이미 있다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology import views
from app.modules.ontology.models import (
    CARDINALITIES,
    DATA_TYPES,
    ENTRY_POLICIES,
    KEY_POLICIES,
    KEY_SCOPES,
    KIND_CLASSES,
    NAV_AUDIENCES,
    TEMPORAL_KINDS,
    NavGroup,
    ObjectType,
    PropertyDef,
    RelationType,
)
from app.modules.ontology.services import require_key, require_slug

#: 스키마가 담을 수 있는 것. **모르는 것이 오면 거절한다** — 조용히 무시하면
#: 보낸 쪽은 적용된 줄 안다.
TOP_KEYS = {"groups", "types", "relation_types"}

GROUP_FIELDS = {"slug", "label", "icon", "audience", "sort_order", "is_active"}
TYPE_FIELDS = {
    "slug",
    "label",
    "icon",
    "description",
    "sort_order",
    "nav_group_slug",
    "kind_class",
    "entry_policy",
    "key_policy",
    "key_scope",
    "temporal_kind",
    "list_view",
    "form_view",
    "detail_view",
    "title_template",
    "is_active",
    "properties",
}
PROPERTY_FIELDS = {
    "key",
    "label",
    "data_type",
    "unit",
    "help",
    "required",
    "multi",
    "enum_options",
    "ref_type_slug",
    "min_value",
    "max_value",
    "decimals",
    "pattern",
    "default_value",
    "unique",
    "section",
    "sort_order",
}
RELATION_FIELDS = {
    "slug",
    "label",
    "inverse_label",
    "description",
    "directed",
    "transitive",
    "acyclic",
    "cardinality",
    "src_type_slugs",
    "dst_type_slugs",
    "sort_order",
    "is_active",
}

CHOICES = {
    "audience": NAV_AUDIENCES,
    "kind_class": KIND_CLASSES,
    "entry_policy": ENTRY_POLICIES,
    "key_policy": KEY_POLICIES,
    "key_scope": KEY_SCOPES,
    "temporal_kind": TEMPORAL_KINDS,
    "cardinality": CARDINALITIES,
    "data_type": DATA_TYPES,
}


@dataclass
class Change:
    kind: str
    """`group` · `type` · `property` · `relation_type`."""
    slug: str
    action: str
    """`create` · `update` · `unchanged`."""
    fields: list[str] = field(default_factory=list)


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """**적용은 되지만 조용히 무언가를 잃는 것.** 사람이 읽고 판단할 자리다."""
    errors: list[str] = field(default_factory=list)
    """적용하면 실패할 것. 하나라도 있으면 안 적용한다."""


def _reject_unknown(payload: dict[str, Any], allowed: set[str], *, what: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{what}에 모르는 항목이 있습니다: {', '.join(unknown)}")


def _check_choices(payload: dict[str, Any], *, what: str, errors: list[str]) -> None:
    for name, allowed in CHOICES.items():
        value = payload.get(name)
        if value is not None and value not in allowed:
            errors.append(
                f"{what}: {name} 은 {', '.join(allowed)} 중 하나여야 합니다 ({value})."
            )


def _diff(
    row: Any,
    payload: dict[str, Any],
    fields: set[str],
    *,
    current: dict[str, Any] | None = None,
) -> list[str]:
    """무엇이 **실제로** 바뀌는가.

    **안 바뀌는 것을 계획에 적으면 사람은 그 목록을 안 읽게 되고, 그때 진짜
    하나가 묻힌다.**

    `current` 는 ORM 에 그 이름의 칸이 없는 것들을 위해서다 — `nav_group_slug` 가
    그렇다(행에는 `nav_group_id` 가 있다). 안 넘겨주면 **늘 바뀐다고 나오고**,
    그것이 정확히 위에서 경계한 그 상태다(실측으로 확인했다).
    """
    changed = []
    for name in sorted(fields):
        if name not in payload or name in ("slug", "key", "properties"):
            continue
        before = (current or {}).get(name, getattr(row, name, None))
        if before != payload[name]:
            changed.append(name)
    return changed


def plan(db: Session, payload: dict[str, Any]) -> Plan:
    """적용하면 무엇이 바뀌는지 — **적용하지 않고** 돌려준다."""
    _reject_unknown(payload, TOP_KEYS, what="스키마")
    out = Plan()

    groups = {row.slug: row for row in db.scalars(select(NavGroup))}
    types = {row.slug: row for row in db.scalars(select(ObjectType))}
    relations = {row.slug: row for row in db.scalars(select(RelationType))}
    #: 행에는 `nav_group_id` 가 있고 스키마에는 slug 가 온다 — 비교할 값을 만든다.
    group_slug_of = {row.id: row.slug for row in groups.values()}

    for one in payload.get("groups") or []:
        _reject_unknown(one, GROUP_FIELDS, what="묶음")
        slug = require_slug(one.get("slug", ""), what="묶음 slug")
        _check_choices(one, what=f"묶음 {slug}", errors=out.errors)
        group = groups.get(slug)
        if group is None:
            out.changes.append(Change("group", slug, "create"))
        else:
            fields = _diff(group, one, GROUP_FIELDS)
            out.changes.append(
                Change("group", slug, "update" if fields else "unchanged", fields)
            )

    for one in payload.get("types") or []:
        _reject_unknown(one, TYPE_FIELDS, what="타입")
        slug = require_slug(one.get("slug", ""), what="타입 slug")
        _check_choices(one, what=f"타입 {slug}", errors=out.errors)
        object_type = types.get(slug)

        # 같은 스키마 안에서 함께 만들어지는 묶음도 인정한다 — **한 번에 보내는
        # 것이 이 엔드포인트의 요점**이라, 순서를 사람이 맞추게 하면 안 된다.
        incoming = {g.get("slug") for g in payload.get("groups") or []}
        wanted = one.get("nav_group_slug")
        if wanted and wanted not in groups and wanted not in incoming:
            out.errors.append(f"타입 {slug}: 없는 묶음을 가리킵니다: {wanted}")

        if object_type is None:
            out.changes.append(Change("type", slug, "create"))
        else:
            fields = _diff(
                object_type,
                one,
                TYPE_FIELDS,
                current={
                    "nav_group_slug": group_slug_of.get(object_type.nav_group_id)
                    if object_type.nav_group_id
                    else None
                },
            )
            out.changes.append(
                Change("type", slug, "update" if fields else "unchanged", fields)
            )
            _warn_type_risks(db, object_type, one, out)

        _plan_properties(db, slug, object_type, one.get("properties") or [], out)

    for one in payload.get("relation_types") or []:
        _reject_unknown(one, RELATION_FIELDS, what="관계 종류")
        slug = require_slug(one.get("slug", ""), what="관계 slug")
        _check_choices(one, what=f"관계 {slug}", errors=out.errors)
        if one.get("transitive") and not one.get("acyclic", one.get("transitive")):
            out.errors.append(
                f"관계 {slug}: 재귀로 펼치는 관계는 순환을 막아야 합니다 — "
                "안 그러면 트리가 무한히 돕니다."
            )
        relation = relations.get(slug)
        if relation is None:
            out.changes.append(Change("relation_type", slug, "create"))
        else:
            fields = _diff(relation, one, RELATION_FIELDS)
            out.changes.append(
                Change("relation_type", slug, "update" if fields else "unchanged", fields)
            )
            _warn_relation_risks(db, relation, one, out)

    return out


def _plan_properties(
    db: Session,
    type_slug: str,
    owner: ObjectType | None,
    payloads: list[dict[str, Any]],
    out: Plan,
) -> None:
    existing: dict[str, PropertyDef] = {}
    if owner is not None:
        existing = {
            row.key: row
            for row in db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type", PropertyDef.owner_id == owner.id
                )
            )
        }

    for one in payloads:
        _reject_unknown(one, PROPERTY_FIELDS, what="속성")
        key = require_key(one.get("key", ""))
        _check_choices(one, what=f"속성 {type_slug}.{key}", errors=out.errors)
        found = existing.get(key)
        name = f"{type_slug}.{key}"

        if found is None:
            out.changes.append(Change("property", name, "create"))
            continue

        if one.get("data_type") and one["data_type"] != found.data_type:
            # **이미 저장된 값이 새 종류에 안 맞아도 화면은 아무 말도 안 한다.**
            out.errors.append(
                f"속성 {name}: 종류는 바꿀 수 없습니다 "
                f"({found.data_type} -> {one['data_type']}). 새 속성을 만들어 옮기세요."
            )
            continue

        fields = _diff(found, one, PROPERTY_FIELDS)
        out.changes.append(
            Change("property", name, "update" if fields else "unchanged", fields)
        )
        _warn_property_risks(db, owner, found, one, out)


def _count_with_value(db: Session, type_id: uuid.UUID, key: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id == type_id,
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.properties.has_key(key),
            )
        )
        or 0
    )


def _warn_property_risks(
    db: Session,
    owner: ObjectType | None,
    found: PropertyDef,
    one: dict[str, Any],
    out: Plan,
) -> None:
    """**적용은 되지만 조용히 무언가를 잃는 것**을 사람이 읽을 말로 적는다."""
    if owner is None:
        return
    name = f"{owner.slug}.{found.key}"

    if "enum_options" in one and found.enum_options:
        removed = sorted(set(found.enum_options) - set(one["enum_options"] or []))
        if removed:
            out.warnings.append(
                f"속성 {name}: 고를 값에서 {', '.join(removed)} 을(를) 뺍니다. "
                "그 값을 가진 객체는 **고칠 때 거절**됩니다."
            )

    if one.get("required") and not found.required:
        count = _count_with_value(db, owner.id, found.key)
        out.warnings.append(
            f"속성 {name}: 필수로 바꿉니다. 지금 값이 있는 객체는 {count}개입니다 — "
            "없는 객체는 그대로 두지만, 그 객체의 속성을 고칠 때 걸립니다."
        )

    if one.get("multi") is not None and one["multi"] != found.multi:
        out.warnings.append(
            f"속성 {name}: 여러 값 설정을 바꿉니다. **이미 저장된 값이 새 모양에 안 맞아** "
            "그 객체는 고칠 때 거절됩니다."
        )

    if one.get("unique") and not found.unique:
        out.warnings.append(
            f"속성 {name}: 유일하게 바꿉니다. **이미 겹친 값이 있어도 지금은 안 막습니다** — "
            "다음에 그 객체를 고칠 때 걸립니다."
        )


def _warn_type_risks(db: Session, found: ObjectType, one: dict[str, Any], out: Plan) -> None:
    if one.get("is_active") is False and found.is_active:
        count = int(
            db.scalar(
                select(func.count())
                .select_from(ObjectInstance)
                .where(ObjectInstance.type_id == found.id, ObjectInstance.deleted_at.is_(None))
            )
            or 0
        )
        out.warnings.append(
            f"타입 {found.slug}: 사용 안 함으로 바꿉니다. 객체 {count}개는 남지만 "
            "메뉴와 만들기에서 빠집니다."
        )
    if one.get("key_policy") == "required" and found.key_policy != "required":
        out.warnings.append(
            f"타입 {found.slug}: 식별자를 필수로 바꿉니다. **식별자가 없는 기존 객체는 "
            "고칠 때 걸립니다.**"
        )


def _warn_relation_risks(
    db: Session, found: RelationType, one: dict[str, Any], out: Plan
) -> None:
    tightening = {
        "many_to_many": 0,
        "one_to_many": 1,
        "many_to_one": 1,
        "one_to_one": 2,
    }
    new = one.get("cardinality")
    if new and tightening.get(new, 0) > tightening.get(found.cardinality, 0):
        count = int(
            db.scalar(
                select(func.count())
                .select_from(ObjectRelation)
                .where(ObjectRelation.relation == found.slug)
            )
            or 0
        )
        out.warnings.append(
            f"관계 {found.slug}: 개수 제약을 조입니다({found.cardinality} -> {new}). "
            f"이미 맺힌 {count}개는 **그대로 남고, 새로 맺을 때만** 걸립니다 — "
            "이미 어긴 것이 있는지 먼저 보세요."
        )


# --- 적용 -------------------------------------------------------------------


def capture(db: Session) -> dict[str, Any]:
    """지금 정의 전부를 한 덩어리로 — **되돌릴 자리에 담을 것.**"""
    groups = [
        {name: getattr(row, name) for name in sorted(GROUP_FIELDS)}
        for row in db.scalars(select(NavGroup).order_by(NavGroup.sort_order))
    ]
    group_slugs = {row.id: row.slug for row in db.scalars(select(NavGroup))}

    types = []
    for row in db.scalars(select(ObjectType).order_by(ObjectType.sort_order)):
        one = {
            name: getattr(row, name)
            for name in sorted(TYPE_FIELDS - {"nav_group_slug", "properties"})
        }
        one["nav_group_slug"] = group_slugs.get(row.nav_group_id) if row.nav_group_id else None
        one["properties"] = [
            {name: getattr(prop, name) for name in sorted(PROPERTY_FIELDS)}
            for prop in db.scalars(
                select(PropertyDef)
                .where(PropertyDef.owner_kind == "type", PropertyDef.owner_id == row.id)
                .order_by(PropertyDef.sort_order)
            )
        ]
        types.append(one)

    relations = [
        {name: getattr(row, name) for name in sorted(RELATION_FIELDS)}
        for row in db.scalars(select(RelationType).order_by(RelationType.sort_order))
    ]
    return {"groups": groups, "types": types, "relation_types": relations}


def _assign(
    row: Any, payload: dict[str, Any], fields: set[str], *, position: int | None = None
) -> None:
    """**보낸 것만 바꾼다.** 안 보낸 칸은 그대로다.

    `position` 은 **새로 만들 때만** 쓴다: 순서를 안 적었으면 **적은 차례를 그대로
    쓴다.** 안 그러면 전부 0 이 되어 이름순으로 서고, 스키마에 적어 둔 순서(대개
    사람이 읽는 차례)가 조용히 뒤집힌다 — 기계가 200개를 보내면서 매번 번호를
    매기게 하는 것도 답이 아니다.
    """
    for name in fields:
        if name in payload and name not in ("slug", "key", "properties", "nav_group_slug"):
            setattr(row, name, payload[name])
    if position is not None and "sort_order" not in payload:
        row.sort_order = position * 10


def apply(db: Session, payload: dict[str, Any]) -> Plan:
    """**한 트랜잭션으로** 적용한다. 부르는 쪽이 커밋한다.

    중간에 실패하면 반쯤 만들어진 온톨로지가 남지 않는다 — 그것이 이 함수가
    존재하는 이유다.
    """
    prepared = plan(db, payload)
    if prepared.errors:
        return prepared

    for index, one in enumerate(payload.get("groups") or []):
        slug = one["slug"]
        group_row = db.scalar(select(NavGroup).where(NavGroup.slug == slug))
        fresh = group_row is None
        if group_row is None:
            group_row = NavGroup(slug=slug, label=one.get("label", slug))
            db.add(group_row)
        _assign(group_row, one, GROUP_FIELDS, position=index if fresh else None)
    db.flush()

    groups = {row.slug: row for row in db.scalars(select(NavGroup))}

    for index, one in enumerate(payload.get("types") or []):
        slug = one["slug"]
        object_type = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
        fresh = object_type is None
        if object_type is None:
            object_type = ObjectType(slug=slug, label=one.get("label", slug))
            db.add(object_type)
        _assign(object_type, one, TYPE_FIELDS, position=index if fresh else None)
        if "nav_group_slug" in one:
            group = groups.get(one["nav_group_slug"]) if one["nav_group_slug"] else None
            object_type.nav_group_id = group.id if group else None
        db.flush()

        # **속성을 먼저 세우고 뷰를 검증한다** — 뷰가 그 속성을 가리키기 때문이다.
        for at, prop in enumerate(one.get("properties") or []):
            key = prop["key"]
            prop_row = db.scalar(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type",
                    PropertyDef.owner_id == object_type.id,
                    PropertyDef.key == key,
                )
            )
            if prop_row is None:
                prop_row = PropertyDef(
                    owner_kind="type",
                    owner_id=object_type.id,
                    key=key,
                    label=prop.get("label", key),
                    data_type=prop.get("data_type", "text"),
                )
                db.add(prop_row)
                _assign(prop_row, prop, PROPERTY_FIELDS, position=at)
            else:
                _assign(prop_row, prop, PROPERTY_FIELDS)
        db.flush()

        defs = list(
            db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type", PropertyDef.owner_id == object_type.id
                )
            )
        )
        object_type.list_view = views.validate_list_view(object_type.list_view or {}, defs)
        object_type.form_view = views.validate_form_view(
            object_type.form_view or {}, defs, what="폼 화면"
        )
        object_type.detail_view = views.validate_form_view(
            object_type.detail_view or {}, defs, what="상세 화면"
        )

    for index, one in enumerate(payload.get("relation_types") or []):
        slug = one["slug"]
        relation = db.scalar(select(RelationType).where(RelationType.slug == slug))
        fresh = relation is None
        if relation is None:
            relation = RelationType(slug=slug, label=one.get("label", slug))
            db.add(relation)
        _assign(relation, one, RELATION_FIELDS, position=index if fresh else None)

    db.flush()
    return prepared
