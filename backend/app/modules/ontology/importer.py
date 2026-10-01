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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology import interfaces, managed, views
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
    ObjectInterface,
    ObjectType,
    OntologySnapshot,
    PropertyDef,
    RelationType,
)
from app.modules.ontology.services import (
    InvalidValue,
    group_parent_error,
    require_key,
    require_slug,
    system_source_error,
)
from app.shared import audit

#: 스키마가 담을 수 있는 것. **모르는 것이 오면 거절한다** — 조용히 무시하면
#: 보낸 쪽은 적용된 줄 안다.
TOP_KEYS = {"groups", "interfaces", "types", "relation_types"}

GROUP_FIELDS = {
    "slug",
    "label",
    "icon",
    "audience",
    # **상위 묶음** — 사이드바를 두 단계로. 행에는 `parent_id` 가 있어 `_assign` 이 건드리지
    # 않고(`DERIVED`) 아래에서 slug 로 푼다. 뜻의 계층(「개발모델은 제품이다」)은 묶음이 아니라
    # 인터페이스가 말한다(ADR 0006).
    "parent_slug",
    "sort_order",
    "is_active",
}
TYPE_FIELDS = {
    "slug",
    "label",
    "icon",
    "description",
    "sort_order",
    "nav_group_slug",
    "interface_slugs",
    "kind_class",
    "system_source",
    "entry_policy",
    "key_policy",
    "key_scope",
    "temporal_kind",
    "list_view",
    "form_view",
    "detail_view",
    "title_template",
    "is_active",
    "core",
    "properties",
}
"""⚠️ `core` 가 여기 있는 이유: 코어로 열 타입이 열 개를 넘는 설치가 있고, 그때 화면에서
하나씩 켜게 하면 열두 번 누르는 동안 하나가 빠진다 — 그리고 **빠진 것은 아무 데도 안
적힌다**(바깥 시스템이 「그 타입이 없다」 를 볼 때까지)."""
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
    "inverse_label",
    "min_value",
    "max_value",
    "decimals",
    "pattern",
    "default_value",
    "unique",
    "section",
    "sort_order",
}
INTERFACE_FIELDS = {
    "slug",
    "label",
    "icon",
    "description",
    "sort_order",
    "extends_slugs",
    "list_view",
    "properties",
}
"""인터페이스 — 공통 속성의 묶음(ADR 0006). **타입보다 먼저 읽는다** — 타입이 구현하는
것이 같은 파일에 함께 올 수 있다."""
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
    # 관계 자체에 붙는 값(근거 건수 · 근거 종류 …). 타입의 `properties` 와 같은 모양이고,
    # `_diff` · `_assign` 은 이 키를 건너뛴다(목록이라 칸이 아니다).
    "properties",
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
    """`group` · `interface` · `interface_property` · `type` · `property` · `relation_type`."""
    slug: str
    action: str
    """`create` · `update` · `unchanged`."""
    fields: list[str] = field(default_factory=list)
    via: str = ""
    """이 변경을 부른 인터페이스 — 파일에 없던 타입의 속성이 **인터페이스를 따라** 바뀔 때.
    사람이 「이건 왜 바뀌지」 를 물을 자리다."""


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """**적용은 되지만 조용히 무언가를 잃는 것.** 사람이 읽고 판단할 자리다."""
    errors: list[str] = field(default_factory=list)
    """적용하면 실패할 것. 하나라도 있으면 안 적용한다."""
    bindings: list[interfaces.Binding] = field(default_factory=list)
    """구현 타입마다 할 일 — 적용이 계획을 **다시 세지 않고** 그대로 옮긴다."""


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


PARENT_GONE = (
    "타입 {slug}: parent_slug(상위 타입)는 없어졌습니다 — 「A 는 B 의 일종」 은 인터페이스로 "
    "적습니다: 최상위 interfaces 에 인터페이스를, 타입의 interface_slugs 에 구현을 적으세요."
)


def plan(db: Session, payload: dict[str, Any], *, source: str = "") -> Plan:
    """적용하면 무엇이 바뀌는지 — **적용하지 않고** 돌려준다.

    `source` 는 허브에서 받는 묶음만 적는다 — 그 허브가 관리하는 정의를 고칠 수 있고,
    새로 들이거나 고친 타입 · 관계 종류는 그 허브의 관리가 된다.

    **인터페이스는 타입보다 먼저 읽는다** — 타입이 구현하는 것이 같은 파일에 올 수 있다. 공통
    속성이 바뀌면 파일에 없던 구현 타입의 속성도 따라 바뀐다: 바뀌기 전과 뒤의 정의를 만들어
    `interfaces.plan_bindings` 가 할 일을 낸다(화면과 같은 함수다)."""
    _reject_unknown(payload, TOP_KEYS, what="스키마")
    out = Plan()

    groups = {row.slug: row for row in db.scalars(select(NavGroup))}
    types = {row.slug: row for row in db.scalars(select(ObjectType))}
    relations = {row.slug: row for row in db.scalars(select(RelationType))}
    iface_rows = {row.slug: row for row in db.scalars(select(ObjectInterface))}
    #: 행에는 `nav_group_id` 가 있고 스키마에는 slug 가 온다 — 비교할 값을 만든다.
    group_slug_of = {row.id: row.slug for row in groups.values()}

    #: 바뀌기 전과 뒤 — 뒤는 이 파일이 보내는 것을 덧씌워 만든다.
    before = interfaces.load(db)
    after = before.clone()
    incoming_types = {str(t["slug"]) for t in payload.get("types") or [] if t.get("slug")}
    incoming_ifaces = {
        str(i["slug"]) for i in payload.get("interfaces") or [] if i.get("slug")
    }
    known_ifaces = set(iface_rows) | incoming_ifaces
    #: 참조 대상이 될 수 있는 것 — 타입 · 인터페이스(있는 것과 이 파일이 보내는 것).
    ref_targets = set(types) | incoming_types | known_ifaces

    # 상위 묶음 검사에 쓸 지도 — **있는 것과 이 파일이 함께 보내는 것을 합쳐** 본다.
    incoming_groups = [one for one in payload.get("groups") or [] if one.get("slug")]
    known_groups = {row.slug for row in groups.values()} | {
        str(one["slug"]) for one in incoming_groups
    }
    parents_of: dict[str, str | None] = {row.slug: row.parent_slug for row in groups.values()}
    for one in incoming_groups:
        if "parent_slug" in one:
            parents_of[str(one["slug"])] = one["parent_slug"] or None
    children_of: dict[str, list[str]] = {}
    for child, parent in parents_of.items():
        if parent:
            children_of.setdefault(parent, []).append(child)

    for one in payload.get("groups") or []:
        _reject_unknown(one, GROUP_FIELDS, what="묶음")
        slug = require_slug(one.get("slug", ""), what="묶음 slug")
        _check_choices(one, what=f"묶음 {slug}", errors=out.errors)
        if "parent_slug" in one:
            wrong = group_parent_error(
                slug=slug,
                parent=str(one["parent_slug"] or ""),
                known=known_groups,
                parents_of=parents_of,
                children_of=children_of,
            )
            if wrong:
                out.errors.append(wrong)
        group = groups.get(slug)
        if group is None:
            out.changes.append(Change("group", slug, "create"))
        else:
            fields = _diff(
                group, one, GROUP_FIELDS, current={"parent_slug": group.parent_slug}
            )
            out.changes.append(
                Change("group", slug, "update" if fields else "unchanged", fields)
            )

    _plan_interfaces(
        db,
        payload.get("interfaces") or [],
        iface_rows,
        set(types) | incoming_types,
        known_ifaces,
        after,
        out,
    )

    for one in payload.get("types") or []:
        if "parent_slug" in one:
            # **조용히 무시하지 않는다** — 무시하면 보낸 쪽은 계층이 적용된 줄 안다.
            raise ValueError(PARENT_GONE.format(slug=one.get("slug", "?")))
        _reject_unknown(one, TYPE_FIELDS, what="타입")
        slug = require_slug(one.get("slug", ""), what="타입 slug")
        _check_choices(one, what=f"타입 {slug}", errors=out.errors)
        object_type = types.get(slug)
        clash = interfaces.namespace_error(
            slug, as_kind="type", types=(), interfaces=known_ifaces
        )
        if clash:
            out.errors.append(clash)

        # 같은 스키마 안에서 함께 만들어지는 묶음도 인정한다 — **한 번에 보내는
        # 것이 이 엔드포인트의 요점**이라, 순서를 사람이 맞추게 하면 안 된다.
        incoming = {g.get("slug") for g in payload.get("groups") or []}
        wanted = one.get("nav_group_slug")
        if wanted and wanted not in groups and wanted not in incoming:
            out.errors.append(f"타입 {slug}: 없는 묶음을 가리킵니다: {wanted}")

        # 투영 타입은 비출 표가 등록돼 있어야 한다 — 보낸 것과 있는 것을 합쳐 본다.
        kind = one.get("kind_class", object_type.kind_class if object_type else "record")
        source_of = one.get("system_source", object_type.system_source if object_type else "")
        source_error = system_source_error(str(kind or "record"), str(source_of or ""))
        if source_error:
            out.errors.append(f"타입 {slug}: {source_error}")

        normalized = dict(one)
        if "interface_slugs" in one:
            normalized["interface_slugs"] = interfaces.normalized_slugs(one["interface_slugs"])
        if object_type is None:
            out.changes.append(Change("type", slug, "create"))
        else:
            fields = _diff(
                object_type,
                normalized,
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

        _plan_properties(
            db,
            slug,
            object_type,
            one.get("properties") or [],
            out,
            ref_targets=ref_targets,
        )
        _overlay_type(after, slug, one, normalized, source)

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
        _plan_properties(
            db,
            slug,
            relation,
            one.get("properties") or [],
            out,
            owner_kind="relation",
            ref_targets=ref_targets,
        )

    # **인터페이스를 따라 바뀌는 구현 타입의 속성** — 파일에 없던 타입도 걸린다.
    out.bindings = interfaces.plan_bindings(before, after, source=source)
    out.errors.extend(interfaces.conflicts(out.bindings))
    planned = {change.slug for change in out.changes if change.kind == "property"}
    for todo in out.bindings:
        if todo.action not in ("create", "sync"):
            continue
        name = f"{todo.type_slug}.{todo.key}"
        if name in planned:
            continue
        out.changes.append(
            Change(
                "property",
                name,
                "create" if todo.action == "create" else "update",
                list(todo.changed),
                via=todo.interface,
            )
        )
    out.warnings.extend(interfaces.risks(db, out.bindings))

    _refuse_managed(out, types, relations, iface_rows, source)
    return out


def _plan_interfaces(
    db: Session,
    payloads: list[dict[str, Any]],
    rows: dict[str, ObjectInterface],
    type_slugs: set[str],
    known: set[str],
    after: interfaces.Catalog,
    out: Plan,
) -> None:
    """인터페이스의 계획 — 공통 속성 · 상위 인터페이스 · 목록 모양. `after` 에 덧씌운다."""
    for one in payloads:
        _reject_unknown(one, INTERFACE_FIELDS, what="인터페이스")
        slug = require_slug(one.get("slug", ""), what="인터페이스 slug")
        clash = interfaces.namespace_error(
            slug, as_kind="interface", types=type_slugs, interfaces=()
        )
        if clash:
            out.errors.append(clash)
        row = rows.get(slug)
        normalized = dict(one)
        if "extends_slugs" in one:
            normalized["extends_slugs"] = interfaces.normalized_slugs(one["extends_slugs"])
        if row is None:
            out.changes.append(Change("interface", slug, "create"))
        else:
            fields = _diff(row, normalized, INTERFACE_FIELDS)
            out.changes.append(
                Change("interface", slug, "update" if fields else "unchanged", fields)
            )
        _plan_properties(
            db,
            slug,
            row,
            one.get("properties") or [],
            out,
            owner_kind="interface",
            ref_targets=type_slugs | known,
        )

        existing = (
            {
                prop.key: prop
                for prop in db.scalars(
                    select(PropertyDef).where(
                        PropertyDef.owner_kind == "interface", PropertyDef.owner_id == row.id
                    )
                )
            }
            if row is not None
            else {}
        )
        entry = after.interfaces.get(slug)
        if entry is None:
            entry = interfaces.Iface(slug=slug, label=str(one.get("label", slug)))
            after.interfaces[slug] = entry
        if "label" in one:
            entry.label = str(one["label"])
        if "extends_slugs" in normalized:
            entry.extends = list(normalized["extends_slugs"])
        for prop in one.get("properties") or []:
            key = str(prop.get("key", ""))
            found = existing.get(key)
            merged = {
                **(
                    {name: getattr(found, name) for name in PROPERTY_FIELDS}
                    if found is not None
                    else {}
                ),
                **prop,
            }
            wrong = interfaces.interface_property_error(key, merged)
            if wrong:
                out.errors.append(f"인터페이스 {slug}: {wrong}")
            have = entry.props.get(key)
            entry.props[key] = interfaces.Prop(
                key=key,
                shape=interfaces.shape_of(prop, have.shape if have else None),
                label=str(merged.get("label") or key),
                help=str(merged.get("help") or ""),
                section=str(merged.get("section") or ""),
                sort_order=int(merged.get("sort_order") or 0),
            )

    # 상위 인터페이스와 목록 모양은 **다 읽은 뒤에** 본다 — 파일 안에서 뒤에 오는 인터페이스를
    # 가리킬 수 있다.
    extends_of = after.extends_of()
    for one in payloads:
        slug = str(one.get("slug", ""))
        if slug not in after.interfaces:
            continue
        if "extends_slugs" in one:
            wrong = interfaces.extends_error(
                slug, extends_of[slug], extends_of, set(after.interfaces)
            )
            if wrong:
                out.errors.append(wrong)
        if one.get("list_view"):
            try:
                views.validate_list_view(
                    one["list_view"],
                    interfaces.interface_fields(after, slug),
                    allowed=views.INTERFACE_LIST_KEYS,
                    extra_fields=views.INTERFACE_FIELDS,
                )
            except InvalidValue as caught:
                out.errors.append(f"인터페이스 {slug}: {caught.message}")


def _overlay_type(
    after: interfaces.Catalog,
    slug: str,
    one: dict[str, Any],
    normalized: dict[str, Any],
    source: str,
) -> None:
    """이 파일이 보내는 타입을 `after` 에 덧씌운다. 속성의 모양 칸을 **직접 보냈으면** 그
    속성은 인터페이스의 전파로 덮지 않고 견준다(`Prop.explicit`)."""
    entry = after.types.get(slug)
    if entry is None:
        entry = interfaces.TypeDef(slug=slug, label=str(one.get("label", slug)))
        after.types[slug] = entry
    if "kind_class" in one:
        entry.kind_class = str(one["kind_class"] or "record")
    if "interface_slugs" in normalized:
        entry.interfaces = list(normalized["interface_slugs"])
    if source:
        entry.managed_by = source
    shape_names = (*interfaces.SHAPE_FIELDS, "required")
    for prop in one.get("properties") or []:
        key = str(prop.get("key", ""))
        have = entry.props.get(key)
        entry.props[key] = interfaces.Prop(
            key=key,
            shape=interfaces.shape_of(prop, have.shape if have else None),
            label=str(prop.get("label", have.label if have else key)),
            help=str(prop.get("help", have.help if have else "")),
            section=str(prop.get("section", have.section if have else "")),
            sort_order=int(prop.get("sort_order", have.sort_order if have else 0) or 0),
            explicit=any(name in prop for name in shape_names),
        )


def _refuse_managed(
    out: Plan,
    types: dict[str, ObjectType],
    relations: dict[str, RelationType],
    ifaces: dict[str, ObjectInterface],
    source: str,
) -> None:
    """허브가 관리하는 정의는 **그 허브의 묶음으로만** 바뀐다 — 받는 쪽에서 고치면 다음 받기가
    덮어쓰거나, 덮어쓰지 못해 둘이 갈린다. 아무것도 안 바뀌는 줄(되돌리기 스냅샷)은 막지
    않는다.

    **인터페이스를 따라 바뀌는 속성(`via`)은 여기서 안 본다** — 그 타입은 파일에 없었고 주인도
    그대로다(구현한 쪽이 계약을 받아들인 것이다). 주인이 다른 구현 타입은
    `interfaces.plan_bindings` 가 이미 막았다."""
    warned: set[str] = set()
    for change in out.changes:
        if change.action == "unchanged" or change.via:
            continue
        row: ObjectType | RelationType | ObjectInterface | None
        if change.kind in ("type", "property"):
            row = types.get(change.slug.split(".", 1)[0])
        elif change.kind == "relation_type":
            row = relations.get(change.slug)
        elif change.kind in ("interface", "interface_property"):
            row = ifaces.get(change.slug.split(".", 1)[0])
        else:
            continue
        if row is None:
            continue
        owner = managed.owner_of(row)
        if owner and owner != source:
            out.errors.append(
                f"{change.slug}: {owner} 가 관리하는 정의라 여기서 바꾸지 않습니다 — "
                f"{owner} 에서 고친 뒤 받으세요."
            )
        elif source and not owner and row.slug not in warned:
            warned.add(row.slug)
            out.warnings.append(
                f"{row.slug}: 이 설치에서 만든 정의를 {source} 가 관리하게 됩니다 — "
                "그 뒤로 여기서는 못 고칩니다."
            )


def _plan_properties(
    db: Session,
    type_slug: str,
    owner: ObjectType | RelationType | ObjectInterface | None,
    payloads: list[dict[str, Any]],
    out: Plan,
    *,
    owner_kind: str = "type",
    ref_targets: set[str] | None = None,
) -> None:
    """속성 정의의 계획 — **타입 · 관계 종류 · 인터페이스가 같은 길을 쓴다.**

    관계에도 붙는 값이 있다(인과 관계의 근거 건수 · 근거 종류). 정의 자리를 따로 만들면
    두 벌이 되고, 언젠가 한쪽만 고쳐진다.
    """
    kind_name = {
        "type": "property",
        "relation": "property",
        "interface": "interface_property",
    }[owner_kind]
    existing: dict[str, PropertyDef] = {}
    if owner is not None:
        existing = {
            row.key: row
            for row in db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == owner_kind, PropertyDef.owner_id == owner.id
                )
            )
        }

    for one in payloads:
        _reject_unknown(one, PROPERTY_FIELDS, what="속성")
        key = require_key(one.get("key", ""))
        _check_choices(one, what=f"속성 {type_slug}.{key}", errors=out.errors)
        found = existing.get(key)
        name = f"{type_slug}.{key}"
        target = one.get("ref_type_slug")
        if ref_targets is not None and target and target not in ref_targets:
            # **경고만** — 옛 스냅샷을 되돌릴 때 사라진 대상 하나가 복원 전체를 막으면 안 된다.
            out.warnings.append(
                f"속성 {name}: 참조 대상 {target} 은(는) 없는 타입 · 인터페이스입니다 — "
                "이 칸은 아무것도 고르지 못합니다."
            )

        if found is None:
            out.changes.append(Change(kind_name, name, "create"))
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
            Change(kind_name, name, "update" if fields else "unchanged", fields)
        )
        if isinstance(owner, ObjectType):
            # 저장된 값이 걸리는 위험은 타입 속성에서만 센다(관계 속성은 셈이 다르다).
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
    if one.get("kind_class") == "system" and found.kind_class != "system":
        count = int(
            db.scalar(
                select(func.count())
                .select_from(ObjectInstance)
                .where(ObjectInstance.type_id == found.id, ObjectInstance.deleted_at.is_(None))
            )
            or 0
        )
        if count:
            # 행이 있는 타입을 투영으로 돌리면 그 행이 화면에서 사라진다 — 경고가
            # 아니라 오류다. 승격은 절차가 따로 있다.
            out.errors.append(
                f"타입 {found.slug}: 객체가 {count}개 있어 투영(system)으로 바꿀 수 없습니다. "
                "전용 표로 옮기는 절차(docs/승격-경로.md)를 따르세요."
            )
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
    if "interface_slugs" in one:
        out.warnings.extend(
            interfaces.unimplement_risks(
                db,
                found.slug,
                found.interface_slugs or [],
                interfaces.normalized_slugs(one["interface_slugs"]),
            )
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


def take_snapshot(db: Session, user: User, *, reason: str) -> OntologySnapshot:
    """지금 정의를 통째로 남긴다. **부르는 쪽이 커밋한다.**

    가져오기 화면과 묶음 가져오기가 같이 쓴다 — 따로 적으면 한쪽 스냅샷에만 빠지는 칸이
    생기고, 그 차이는 되돌리는 날에야 드러난다.
    """
    row = OntologySnapshot(
        actor_id=user.id,
        # **그때의 이름을 박는다.** 계정이 지워지면 누가 했는지 모르게 되는데,
        # 그건 되돌릴 자리가 존재하는 이유와 정면으로 어긋난다.
        actor_label=user.display_name or user.email,
        reason=reason,
        schema=capture(db),
    )
    db.add(row)
    db.flush()
    return row


def capture(db: Session) -> dict[str, Any]:
    """지금 정의 전부를 한 덩어리로 — **되돌릴 자리에 담을 것.**"""
    groups = [
        {name: getattr(row, name) for name in sorted(GROUP_FIELDS)}
        # `parent_slug` 는 모델의 property 라 위 한 줄로 함께 담긴다 — 되돌릴 때 그대로
        # 다시 읽힌다(`apply` 가 slug 로 푼다).
        for row in db.scalars(select(NavGroup).order_by(NavGroup.sort_order))
    ]
    group_slugs = {row.id: row.slug for row in db.scalars(select(NavGroup))}

    # 인터페이스는 **타입보다 앞에** 담는다 — 되돌릴 때 구현하는 쪽보다 먼저 서야 한다.
    ifaces: list[dict[str, Any]] = []
    for iface in db.scalars(
        select(ObjectInterface).order_by(ObjectInterface.sort_order, ObjectInterface.slug)
    ):
        one = {
            name: getattr(iface, name) for name in sorted(INTERFACE_FIELDS - {"properties"})
        }
        one["properties"] = _captured_properties(db, "interface", iface.id)
        ifaces.append(one)

    types = []
    for row in db.scalars(select(ObjectType).order_by(ObjectType.sort_order)):
        one = {
            name: getattr(row, name)
            for name in sorted(TYPE_FIELDS - {"nav_group_slug", "properties"})
        }
        one["nav_group_slug"] = group_slugs.get(row.nav_group_id) if row.nav_group_id else None
        one["properties"] = _captured_properties(db, "type", row.id)
        types.append(one)

    relations: list[dict[str, Any]] = []
    for kind in db.scalars(select(RelationType).order_by(RelationType.sort_order)):
        one = {name: getattr(kind, name) for name in sorted(RELATION_FIELDS - {"properties"})}
        # 관계에 붙은 속성 정의도 함께 담는다 — 안 담으면 되돌릴 때 그것만 안 돌아온다.
        one["properties"] = _captured_properties(db, "relation", kind.id)
        relations.append(one)
    return {
        "groups": groups,
        "interfaces": ifaces,
        "types": types,
        "relation_types": relations,
    }


def _captured_properties(
    db: Session, owner_kind: str, owner_id: uuid.UUID
) -> list[dict[str, Any]]:
    return [
        {name: getattr(prop, name) for name in sorted(PROPERTY_FIELDS)}
        for prop in db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == owner_kind, PropertyDef.owner_id == owner_id)
            .order_by(PropertyDef.sort_order)
        )
    ]


def upgrade_snapshot(schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """옛 스냅샷을 지금 모양으로 — **되돌리기만 쓴다.**

    인터페이스 전의 스냅샷은 타입마다 `parent_slug` 를 담고 있다. 가져오기는 그 칸을 거절하므로
    (조용히 무시하면 보낸 쪽은 계층이 적용된 줄 안다) 그대로 되돌리면 **옛 스냅샷이 전부 못
    쓰게 된다.** 되돌리기에서만 떼고, 뗀 것을 경고로 말한다 — 정의 파일 · 묶음은 여전히
    거절한다.
    """
    notes: list[str] = []
    types: list[dict[str, Any]] = []
    for one in schema.get("types") or []:
        if "parent_slug" in one:
            one = dict(one)
            parent = one.pop("parent_slug")
            if parent:
                notes.append(
                    f"타입 {one.get('slug')}: 옛 상위 타입 {parent} 는 되돌리지 않습니다 — "
                    "상위 타입은 없어졌습니다(인터페이스로 다시 적으세요)."
                )
        types.append(one)
    return {**schema, "types": types}, notes


def _assign(
    row: Any,
    payload: dict[str, Any],
    fields: set[str],
    *,
    position: int | None = None,
    derived: tuple[str, ...] = (),
) -> None:
    """**보낸 것만 바꾼다.** 안 보낸 칸은 그대로다.

    `position` 은 **새로 만들 때만** 쓴다: 순서를 안 적었으면 **적은 차례를 그대로
    쓴다.** 안 그러면 전부 0 이 되어 이름순으로 서고, 스키마에 적어 둔 순서(대개
    사람이 읽는 차례)가 조용히 뒤집힌다 — 기계가 200개를 보내면서 매번 번호를
    매기게 하는 것도 답이 아니다.
    """
    # `derived` 는 **그대로 대입하면 안 되는** 칸이다 — 스키마에는 slug 로 오고 행에는 id 로
    # 있거나(묶음의 `parent_slug`, 타입의 `nav_group_slug`), 담기 전에 정리해야 하는 것
    # (`interface_slugs` · `extends_slugs` 는 정렬해서 담는다).
    for name in fields:
        if name in payload and name not in ("slug", "key", "properties", *derived):
            setattr(row, name, payload[name])
    if position is not None and "sort_order" not in payload:
        row.sort_order = position * 10


def _apply_properties(
    db: Session, owner_kind: str, owner_id: uuid.UUID, payloads: list[dict[str, Any]]
) -> None:
    """속성 정의를 적는다 — 타입 · 관계 종류 · 인터페이스가 같은 길이다."""
    for at, prop in enumerate(payloads):
        key = prop["key"]
        prop_row = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == owner_kind,
                PropertyDef.owner_id == owner_id,
                PropertyDef.key == key,
            )
        )
        if prop_row is None:
            prop_row = PropertyDef(
                owner_kind=owner_kind,
                owner_id=owner_id,
                key=key,
                label=prop.get("label", key),
                data_type=prop.get("data_type", "text"),
            )
            db.add(prop_row)
            _assign(prop_row, prop, PROPERTY_FIELDS, position=at)
        else:
            _assign(prop_row, prop, PROPERTY_FIELDS)
    db.flush()


def apply(
    db: Session, payload: dict[str, Any], *, source: str = "", actor: User | None = None
) -> Plan:
    """**한 트랜잭션으로** 적용한다. 부르는 쪽이 커밋한다.

    중간에 실패하면 반쯤 만들어진 온톨로지가 남지 않는다 — 그것이 이 함수가
    존재하는 이유다.

    순서: 묶음 → 인터페이스 → 타입(과 파일에 적힌 속성) → **인터페이스를 따라 바뀌는 속성** →
    타입의 화면 모양 검증 → 관계 종류. 화면 모양은 구현으로 생긴 속성을 가리킬 수 있어 그 뒤다.
    """
    prepared = plan(db, payload, source=source)
    if prepared.errors:
        return prepared

    for index, one in enumerate(payload.get("groups") or []):
        slug = one["slug"]
        group_row = db.scalar(select(NavGroup).where(NavGroup.slug == slug))
        fresh = group_row is None
        if group_row is None:
            group_row = NavGroup(slug=slug, label=one.get("label", slug))
            db.add(group_row)
        _assign(
            group_row,
            one,
            GROUP_FIELDS,
            position=index if fresh else None,
            derived=("parent_slug",),
        )
    db.flush()

    groups = {row.slug: row for row in db.scalars(select(NavGroup))}
    # **상위는 두 번째 바퀴에 앉힌다** — 상위 묶음이 파일에서 뒤에 올 수 있다(한 번에 보내는
    # 것이 이 엔드포인트의 요점이라 순서를 사람이 맞추게 하지 않는다).
    for one in payload.get("groups") or []:
        if "parent_slug" not in one:
            continue
        row = groups[one["slug"]]
        wanted = one["parent_slug"]
        parent = groups.get(wanted) if wanted else None
        row.parent_id = parent.id if parent else None
    db.flush()

    written_ifaces: list[tuple[ObjectInterface, dict[str, Any]]] = []
    for index, one in enumerate(payload.get("interfaces") or []):
        slug = one["slug"]
        iface = db.scalar(select(ObjectInterface).where(ObjectInterface.slug == slug))
        fresh = iface is None
        if iface is None:
            iface = ObjectInterface(slug=slug, label=one.get("label", slug))
            db.add(iface)
        _assign(
            iface,
            one,
            INTERFACE_FIELDS,
            position=index if fresh else None,
            derived=("extends_slugs", "list_view"),
        )
        if "extends_slugs" in one:
            iface.extends_slugs = interfaces.normalized_slugs(one["extends_slugs"])
        if source:
            iface.managed_by = source
        # 공통 속성만 바뀌어도 인터페이스가 바뀐 것이다 — RDF 캐시가 이 시각으로 안다.
        iface.updated_at = datetime.now(UTC)
        db.flush()
        _apply_properties(db, "interface", iface.id, one.get("properties") or [])
        written_ifaces.append((iface, one))
    if written_ifaces:
        # 목록 모양은 공통 속성이 **다 선 뒤에** — 상위 인터페이스에서 이어받은 것도 가리킨다.
        catalog = interfaces.load(db)
        for iface, one in written_ifaces:
            if "list_view" in one:
                iface.list_view = views.validate_list_view(
                    one["list_view"] or {},
                    interfaces.interface_fields(catalog, iface.slug),
                    allowed=views.INTERFACE_LIST_KEYS,
                    extra_fields=views.INTERFACE_FIELDS,
                )

    written_types: list[ObjectType] = []
    for index, one in enumerate(payload.get("types") or []):
        slug = one["slug"]
        object_type = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
        fresh = object_type is None
        if object_type is None:
            object_type = ObjectType(slug=slug, label=one.get("label", slug))
            db.add(object_type)
        core_was = bool(object_type.core)
        _assign(
            object_type,
            one,
            TYPE_FIELDS,
            position=index if fresh else None,
            derived=("nav_group_slug", "interface_slugs"),
        )
        if "interface_slugs" in one:
            object_type.interface_slugs = interfaces.normalized_slugs(one["interface_slugs"])
        if "core" in one and bool(object_type.core) != core_was:
            # **공개를 켜고 끈 일은 화면과 같은 기록을 남긴다**(`ontology.type.core`).
            # 파일로 켠 것만 기록이 없으면, 「언제 누가 이 타입을 밖에 열었나」 를 물었을 때
            # 어떤 길로 열렸느냐에 따라 답이 있기도 없기도 하다.
            audit.record(
                db,
                action="ontology.type.core",
                actor=actor,
                target_table="object_types",
                target_id=object_type.id,
                target_label=slug,
                changes={"core": bool(object_type.core), "was": core_was},
                reason="정의 가져오기",
            )
        if source:
            object_type.managed_by = source
        if "nav_group_slug" in one:
            group = groups.get(one["nav_group_slug"]) if one["nav_group_slug"] else None
            object_type.nav_group_id = group.id if group else None
        db.flush()
        _apply_properties(db, "type", object_type.id, one.get("properties") or [])
        written_types.append(object_type)

    # **인터페이스를 따라 바뀌는 속성** — 계획이 낸 그대로. 파일에 없던 구현 타입도 여기서
    # 바뀐다.
    interfaces.apply_bindings(db, prepared.bindings)

    # **속성을 다 세우고 뷰를 검증한다** — 뷰가 그 속성(구현으로 생긴 것까지)을 가리킨다.
    for object_type in written_types:
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
        # **관계에도 속성이 붙는다** — 근거 건수 · 근거 종류처럼 선 자체에 딸린 값.
        _apply_properties(db, "relation", relation.id, one.get("properties") or [])
        if source:
            relation.managed_by = source

    db.flush()
    return prepared
