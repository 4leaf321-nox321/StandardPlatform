"""인터페이스의 규칙 — **화면과 가져오기가 같은 함수를 부른다**(ADR 0006).

인터페이스는 공통 속성의 묶음이고, 구현한 타입은 **같은 키 · 같은 모양**의 자기 속성을
가진다. 그 규칙이 두 길(관리 화면 · 정의 가져오기)에서 따로 적히면 한쪽만 고쳐지고, 그때
화면으로는 막히는 것이 파일로는 들어온다. 그래서 규칙은 여기 한 곳이고, 두 길은 **바뀌기
전(`before`)과 바뀐 뒤(`after`)의 정의**를 만들어 `plan_bindings` 에 넘기기만 한다.

## 같아야 하는 것과 달라도 되는 것

    같아야 한다   종류 · 여러 값 · 고를 값(집합으로 — 순서는 인터페이스의 것) · 참조 대상 ·
                  단위 · 최소 · 최대 · 소수 자릿수 · 패턴
    한 방향      필수 — 인터페이스가 필수면 타입도 필수(타입이 더 엄격한 것은 된다)
    타입마다     이름 · 도움말 · 묶음(section) · 순서 · 기본값 · 유일 · 역방향 이름

같아야 하는 것이 같아야 **여러 타입을 한 번에 물을 수 있다** — 한 타입에서 「enum」 인 것이
다른 타입에서 「text」 면, 같은 조건이 한쪽에서는 고를 값을 보고 다른 쪽에서는 글자를 본다.

## 할 일 셋 — create · sync · conflict

    create     구현 타입에 그 키가 없다 → 인터페이스의 모양으로 만든다
    sync       있고 맞는다(또는 이미 묶여 있던 것이다) → 모양을 인터페이스에 맞춘다
    conflict   있는데 모양이 다르다 → **거절하고 무엇이 다른지 말한다**

**이미 묶여 있던 속성**(바뀌기 전에도 그 인터페이스의 공통 속성이었던 것)은 인터페이스를 따라
바뀐다 — 공통 속성을 고친 것이 곧 전파다. **새로 묶이는 속성**은 모양이 같아야 채택한다 —
짐작으로 맞추지 않는다. 가져오기가 타입 속성의 모양을 **직접 보냈으면**(`Prop.explicit`) 전파로
덮지 않고 견준다: 파일에 적힌 모양이 조용히 사라지면 적은 쪽은 적용된 줄 안다.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.ontology.models import ObjectInterface, ObjectType, PropertyDef, RelationType

#: 같아야 하는 칸. **필수(`required`)는 따로 본다** — 한 방향이라서.
SHAPE_FIELDS: tuple[str, ...] = (
    "data_type",
    "multi",
    "enum_options",
    "ref_type_slug",
    "unit",
    "min_value",
    "max_value",
    "decimals",
    "pattern",
)

#: 화면에 쓰는 이름 — 차이를 사람의 말로 적을 때.
FIELD_LABELS = {
    "data_type": "종류",
    "multi": "여러 값",
    "enum_options": "고를 값",
    "ref_type_slug": "참조 대상",
    "unit": "단위",
    "min_value": "최소",
    "max_value": "최대",
    "decimals": "소수 자릿수",
    "pattern": "패턴",
    "required": "필수",
}

#: 그 종류에서만 뜻이 있는 칸. 다른 종류의 속성에 남은 값은 비교에서 뺀다 — 안 빼면 쓰지도
#: 않는 칸 하나 때문에 「모양이 다르다」 가 나오고, 사람은 무엇을 고쳐야 할지 모른다.
_ONLY_FOR: dict[str, tuple[str, ...]] = {
    "enum_options": ("enum",),
    "ref_type_slug": ("object_ref",),
    "min_value": ("number",),
    "max_value": ("number",),
    "decimals": ("number",),
    "pattern": ("text", "text_long", "url"),
}


@dataclass(frozen=True)
class Shape:
    """속성의 **모양** — 같아야 하는 칸들과 필수."""

    data_type: str = "text"
    multi: bool = False
    enum_options: tuple[str, ...] | None = None
    ref_type_slug: str | None = None
    unit: str = ""
    min_value: float | None = None
    max_value: float | None = None
    decimals: int | None = None
    pattern: str | None = None
    required: bool = False

    def written(self) -> dict[str, Any]:
        """행에 적을 값 — 고를 값은 목록으로."""
        out: dict[str, Any] = {name: getattr(self, name) for name in SHAPE_FIELDS}
        out["enum_options"] = list(self.enum_options) if self.enum_options else None
        out["required"] = self.required
        return out


def _normalized(values: dict[str, Any]) -> Shape:
    kind = str(values.get("data_type") or "text")
    options = values.get("enum_options")
    shape: dict[str, Any] = {
        "data_type": kind,
        "multi": bool(values.get("multi")),
        "enum_options": tuple(str(one) for one in options) if options else None,
        "ref_type_slug": values.get("ref_type_slug") or None,
        "unit": str(values.get("unit") or ""),
        "min_value": None if values.get("min_value") is None else float(values["min_value"]),
        "max_value": None if values.get("max_value") is None else float(values["max_value"]),
        "decimals": None if values.get("decimals") is None else int(values["decimals"]),
        "pattern": values.get("pattern") or None,
        "required": bool(values.get("required")),
    }
    for name, kinds in _ONLY_FOR.items():
        if kind not in kinds:
            shape[name] = None
    return Shape(**shape)


def shape_of(src: PropertyDef | Mapping[str, Any], base: Shape | None = None) -> Shape:
    """행이나 보낸 값에서 모양을 읽는다. 보낸 값에 없는 칸은 `base`(지금 모양)에서 온다 —
    **안 보낸 것은 안 바뀐 것이다.**"""
    names = (*SHAPE_FIELDS, "required")
    if isinstance(src, PropertyDef):
        return _normalized({name: getattr(src, name) for name in names})
    start = base.written() if base is not None else Shape().written()
    return _normalized({**start, **{name: src[name] for name in names if name in src}})


def _say(value: Any) -> str:
    if value is None or value == "" or value == ():
        return "(없음)"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def shape_diff(want: Shape, have: Shape) -> list[str]:
    """인터페이스(`want`)와 타입(`have`)이 **어디가 다른가** — 사람이 읽을 말로.

    필수는 안 본다(한 방향이라 맞출 수 있다). 고를 값은 **집합으로** 본다 — 순서만 다르면
    같은 것이고, 순서는 인터페이스의 것으로 맞춘다.

    **종류가 다르면 종류만 말한다** — 나머지(고를 값 · 범위 …)는 종류를 따라 붙는 칸이라, 함께
    적으면 고칠 곳 하나가 여러 줄에 묻힌다.
    """
    if want.data_type != have.data_type:
        return [
            f"{FIELD_LABELS['data_type']}: 인터페이스는 {want.data_type}, "
            f"이 타입은 {have.data_type}"
        ]
    out: list[str] = []
    for name in SHAPE_FIELDS:
        mine, theirs = getattr(want, name), getattr(have, name)
        if name == "enum_options":
            wanted, had = set(mine or ()), set(theirs or ())
            if wanted == had:
                continue
            parts = []
            if wanted - had:
                parts.append(f"인터페이스에만 {', '.join(sorted(wanted - had))}")
            if had - wanted:
                parts.append(f"이 타입에만 {', '.join(sorted(had - wanted))}")
            out.append(f"{FIELD_LABELS[name]} — {' · '.join(parts)}")
            continue
        if mine != theirs:
            out.append(
                f"{FIELD_LABELS[name]}: 인터페이스는 {_say(mine)}, 이 타입은 {_say(theirs)}"
            )
    return out


def _changed(have: Shape, target: Shape) -> list[str]:
    """글자 그대로 달라지는 칸 — 고를 값의 순서 · 필수까지."""
    return [
        name
        for name in (*SHAPE_FIELDS, "required")
        if getattr(have, name) != getattr(target, name)
    ]


def interface_property_error(key: str, values: Mapping[str, Any]) -> str | None:
    """공통 속성이 **될 수 없는 것** — 아니면 None.

    타입마다 정하는 칸을 인터페이스에 적으면, 적은 사람은 모든 구현 타입에 걸린 줄 안다.
    """
    kind = values.get("data_type")
    if kind == "file":
        return (
            f"공통 속성 {key}: 파일 속성은 공통 속성이 될 수 없습니다 — 값이 속성 칸에 없어서 "
            "여러 타입을 한 목록으로 묻지 못합니다."
        )
    if kind == "object_ref" and not values.get("ref_type_slug"):
        return (
            f"공통 속성 {key}: 참조 대상을 정해야 합니다 — 구현 타입마다 다른 것을 가리키면 "
            "같은 속성이 아닙니다."
        )
    if values.get("unique"):
        return (
            f"공통 속성 {key}: 유일은 타입마다 정합니다(범위가 타입의 식별자 범위라서) — "
            "구현 타입의 속성에서 켜세요."
        )
    if values.get("default_value") is not None:
        return f"공통 속성 {key}: 기본값은 타입마다 정합니다 — 구현 타입의 속성에 적으세요."
    if str(values.get("inverse_label") or "").strip():
        return (
            f"공통 속성 {key}: 역방향 이름은 타입마다 정합니다 — 구현 타입의 속성에 적으세요."
        )
    return None


def normalized_slugs(values: Iterable[str] | None) -> list[str]:
    """정렬하고 겹친 것을 뺀다 — 순서만 다른 목록이 「바뀌었다」 로 읽히지 않게."""
    return sorted({str(one).strip() for one in values or [] if str(one).strip()})


def namespace_error(
    slug: str, *, as_kind: str, types: Iterable[str], interfaces: Iterable[str]
) -> str | None:
    """타입과 인터페이스는 **slug 를 함께 쓴다** — 주소(`/o/<slug>`) · 참조 대상 · 관계 끝 ·
    RDF 클래스가 전부 slug 하나를 받으므로, 겹치면 어느 쪽인지 말할 수 없다. DB 제약은 두
    표에 걸 수 없어 여기서 막는다(쓰기는 시스템 관리자만이라 동시에 겹칠 일은 드물다)."""
    if as_kind == "type" and slug in set(interfaces):
        return (
            f"{slug}: 같은 slug 의 인터페이스가 있습니다 — 타입과 인터페이스는 slug 를 "
            "함께 씁니다(주소 · 참조 대상 · 관계 끝이 slug 하나를 받습니다)."
        )
    if as_kind == "interface" and slug in set(types):
        return (
            f"{slug}: 같은 slug 의 타입이 있습니다 — 타입과 인터페이스는 slug 를 "
            "함께 씁니다(주소 · 참조 대상 · 관계 끝이 slug 하나를 받습니다)."
        )
    return None


# --- 끝에 적힌 slug — 타입 또는 인터페이스 ---------------------------------------


@dataclass
class Ends:
    """관계 끝 · 참조 대상에 적힌 slug 를 **타입으로 편다.**

    끝에는 타입과 인터페이스가 섞여 적힌다. 「설비」 는 그것을 구현한 타입 전부(상위
    인터페이스를 거쳐 구현한 것까지)다. 정의 한 벌을 한 번 읽어 여러 줄에 쓴다 — 일괄 입력이
    줄마다 부른다.

    **비어 있는 것과 아무것도 없는 것은 다르다.** 끝을 안 적었으면(`None` · 빈 목록) 제약이
    없고, 구현 타입이 없는 인터페이스만 적었으면 **아무 타입도 안 된다**(`set()`). 둘을 섞으면
    구현 타입이 없는 인터페이스를 끝으로 둔 관계가 아무것이나 잇는다.
    """

    labels: dict[str, str] = field(default_factory=dict)
    """타입 · 인터페이스 slug → 이름 — 오류 문구가 쓴다."""
    interfaces: set[str] = field(default_factory=set)
    reach: dict[str, set[str]] = field(default_factory=dict)
    """타입 slug → 그 타입이 설 수 있는 끝의 slug(자기 · 구현한 인터페이스와 그 위)."""

    def reach_of(self, type_slug: str) -> set[str]:
        return self.reach.get(type_slug, {type_slug})

    def expand(self, slugs: Iterable[str] | None) -> set[str] | None:
        """적힌 끝 → 설 수 있는 타입 slug. 안 적었으면 `None`(제약 없음)."""
        wanted = set(slugs or ())
        if not wanted:
            return None
        return {slug for slug, reach in self.reach.items() if reach & wanted}

    def types_of(self, slugs: Iterable[str]) -> list[str]:
        """적힌 순서대로 펴서 — 인터페이스 자리에 그 구현 타입들이 선다. 모르는 slug 는 그대로
        둔다(부르는 쪽이 「없는 타입」 을 말한다)."""
        out: list[str] = []
        for one in slugs:
            if one in self.interfaces:
                out += sorted(slug for slug, reach in self.reach.items() if one in reach)
            else:
                out.append(one)
        return list(dict.fromkeys(out))

    def allows(self, slugs: Iterable[str] | None, type_slug: str) -> bool:
        wanted = set(slugs or ())
        return not wanted or bool(self.reach_of(type_slug) & wanted)

    def describe(self, slugs: Iterable[str]) -> str:
        """사람이 읽는 끝 — 인터페이스는 「설비를 구현한 타입」 으로."""
        return ", ".join(
            f"{self.labels.get(one, one)}을(를) 구현한 타입"
            if one in self.interfaces
            else self.labels.get(one, one)
            for one in slugs
        )


def load_ends(db: Session) -> Ends:
    """`Ends` 를 한 벌 — 속성은 안 읽는다(`load` 보다 가볍다). 관계를 맺을 때마다 부른다."""
    out = Ends()
    extends_of: dict[str, list[str]] = {}
    for slug, label, extends in db.execute(
        select(ObjectInterface.slug, ObjectInterface.label, ObjectInterface.extends_slugs)
    ):
        out.labels[slug] = label
        out.interfaces.add(slug)
        extends_of[slug] = list(extends or [])
    for slug, label, declared in db.execute(
        select(ObjectType.slug, ObjectType.label, ObjectType.interface_slugs)
    ):
        out.labels[slug] = label
        out.reach[slug] = {slug, *closure(declared or [], extends_of)}
    return out


# --- 상위 인터페이스 ------------------------------------------------------------


def closure(slugs: Iterable[str], extends_of: Mapping[str, Sequence[str]]) -> list[str]:
    """이 인터페이스들과 그 위로 이어받는 것 전부. 모르는 slug 는 건너뛴다."""
    seen: set[str] = set()
    stack = [one for one in slugs if one in extends_of]
    while stack:
        one = stack.pop()
        if one in seen:
            continue
        seen.add(one)
        stack.extend(up for up in extends_of.get(one, ()) if up in extends_of)
    return sorted(seen)


def extends_error(
    slug: str,
    wanted: Sequence[str],
    extends_of: Mapping[str, Sequence[str]],
    known: set[str],
) -> str | None:
    """상위 인터페이스가 **될 수 있는가** — 자기 자신 · 없는 것 · 고리."""
    if slug in wanted:
        return f"인터페이스 {slug}: 자기 자신을 상위 인터페이스로 가리킵니다"
    missing = sorted(set(wanted) - known)
    if missing:
        return f"인터페이스 {slug}: 없는 상위 인터페이스를 가리킵니다: {', '.join(missing)}"
    graph = {name: list(ups) for name, ups in extends_of.items()}
    graph[slug] = list(wanted)
    # 위로 올라가다 자기에게 돌아오면 고리다 — 공통 속성을 모으는 셈이 끝나지 않는다.
    stack: list[tuple[str, list[str]]] = [(up, [slug, up]) for up in wanted]
    seen: set[str] = set()
    while stack:
        one, path = stack.pop()
        if one == slug:
            return f"인터페이스 {slug}: 상위 인터페이스가 고리를 이룹니다: {' → '.join(path)}"
        if one in seen:
            continue
        seen.add(one)
        stack.extend((up, [*path, up]) for up in graph.get(one, ()))
    return None


# --- 정의 한 벌 ---------------------------------------------------------------


@dataclass
class Prop:
    key: str
    shape: Shape
    label: str = ""
    help: str = ""
    section: str = ""
    sort_order: int = 0
    explicit: bool = False
    """가져오기가 이 속성의 **모양 칸을 직접 보냈나** — 보냈으면 전파로 덮지 않고 견준다."""


@dataclass
class Iface:
    slug: str
    label: str
    extends: list[str] = field(default_factory=list)
    props: dict[str, Prop] = field(default_factory=dict)
    managed_by: str = ""


@dataclass
class TypeDef:
    slug: str
    label: str
    kind_class: str = "record"
    interfaces: list[str] = field(default_factory=list)
    props: dict[str, Prop] = field(default_factory=dict)
    managed_by: str = ""


@dataclass
class Catalog:
    """정의 한 벌 — 인터페이스와 타입의 속성. 바뀌기 전과 뒤를 이것 둘로 견준다."""

    interfaces: dict[str, Iface] = field(default_factory=dict)
    types: dict[str, TypeDef] = field(default_factory=dict)

    def clone(self) -> Catalog:
        return copy.deepcopy(self)

    def extends_of(self) -> dict[str, list[str]]:
        return {slug: list(one.extends) for slug, one in self.interfaces.items()}


def contract(
    catalog: Catalog, interface_slugs: Iterable[str]
) -> tuple[dict[str, tuple[Prop, str]], list[str]]:
    """이 인터페이스들을 구현하면 **가져야 할 공통 속성** — 키 → (속성, 그것을 정한
    인터페이스).

    두 인터페이스가 같은 키를 다른 모양으로 정하면 충돌이다(둘 다 따를 수는 없다). 같은
    모양이면 하나로 친다 — 필수는 더 엄격한 쪽을 따른다.
    """
    out: dict[str, tuple[Prop, str]] = {}
    collisions: list[str] = []
    for slug in closure(interface_slugs, catalog.extends_of()):
        for key, prop in sorted(catalog.interfaces[slug].props.items()):
            if key not in out:
                out[key] = (prop, slug)
                continue
            prev, owner = out[key]
            diffs = shape_diff(prev.shape, prop.shape)
            if diffs:
                collisions.append(
                    f"공통 속성 {key} 를 인터페이스 {owner} 와 {slug} 가 다른 모양으로 "
                    "정합니다 — " + "; ".join(diffs)
                )
            elif prop.shape.required and not prev.shape.required:
                out[key] = (replace(prev, shape=replace(prev.shape, required=True)), owner)
    return out, collisions


@dataclass(frozen=True)
class FieldRef:
    """뷰 검증에 건넬 공통 속성 — 키와 종류(`views.FieldDef`)."""

    key: str
    data_type: str


def interface_fields(catalog: Catalog, interface_slug: str) -> list[FieldRef]:
    """인터페이스 목록이 쓸 수 있는 속성 — 상위 인터페이스에서 이어받은 것까지."""
    wanted, _ = contract(catalog, [interface_slug])
    return [FieldRef(key, prop.shape.data_type) for key, (prop, _) in sorted(wanted.items())]


def bound_keys(catalog: Catalog, type_slug: str) -> dict[str, str]:
    """이 타입의 속성 중 **공통 속성인 것** — 키 → 그것을 정한 인터페이스."""
    one = catalog.types.get(type_slug)
    if one is None or not one.interfaces:
        return {}
    wanted, _ = contract(catalog, one.interfaces)
    return {key: owner for key, (_, owner) in wanted.items() if key in one.props}


def implementers(catalog: Catalog, interface_slug: str) -> list[str]:
    """이 인터페이스를 (상위 인터페이스를 거쳐서라도) 구현한 타입들."""
    extends_of = catalog.extends_of()
    return sorted(
        slug
        for slug, one in catalog.types.items()
        if interface_slug in closure(one.interfaces, extends_of)
    )


def sub_interfaces(catalog: Catalog, interface_slug: str) -> list[str]:
    """이 인터페이스를 이어받는 인터페이스들(자기 자신은 빼고)."""
    extends_of = catalog.extends_of()
    return sorted(
        slug
        for slug in catalog.interfaces
        if slug != interface_slug and interface_slug in closure([slug], extends_of)
    )


# --- 할 일 ----------------------------------------------------------------------


@dataclass
class Binding:
    type_slug: str
    key: str
    action: str
    """`create` · `adopt`(있는 것을 그대로) · `sync`(모양을 맞춤) · `conflict`."""
    interface: str
    shape: Shape | None = None
    defaults: Prop | None = None
    """새로 만들 때 옮겨 적을 이름 · 도움말 · 묶음 · 순서."""
    changed: list[str] = field(default_factory=list)
    """`sync` 에서 실제로 바뀌는 칸."""
    conflict: str = ""
    fresh: bool = True
    """이번에 새로 묶이나(아니면 이미 묶여 있던 것이 인터페이스를 따라 바뀌는 것)."""


def plan_bindings(
    before: Catalog, after: Catalog, *, source: str = "", only: set[str] | None = None
) -> list[Binding]:
    """바뀌기 전과 뒤를 견주어 **구현 타입마다 할 일**을 낸다. 아무것도 안 바꾼다.

    `only` 를 주면 그 타입들만 본다(없으면 전부 — 공통 속성 하나를 고쳐도 구현 타입 전부가
    걸리므로 대개 전부다). `source` 는 허브 묶음이 적는 값이다 — 주인이 다른 타입은 여기서
    바꾸지 않는다(그 타입의 주인이 구현을 맞춘다).
    """
    out: list[Binding] = []
    for slug, one in sorted(after.types.items()):
        if only is not None and slug not in only:
            continue
        if not one.interfaces:
            continue
        unknown = sorted(set(one.interfaces) - set(after.interfaces))
        if unknown:
            out.append(
                Binding(
                    slug,
                    "",
                    "conflict",
                    ", ".join(unknown),
                    conflict=(
                        f"타입 {slug}: 없는 인터페이스를 구현합니다: {', '.join(unknown)}"
                    ),
                )
            )
            continue
        if one.kind_class == "system":
            out.append(
                Binding(
                    slug,
                    "",
                    "conflict",
                    ", ".join(one.interfaces),
                    conflict=(
                        f"타입 {slug}: 다른 표를 비추는 타입(system)은 인터페이스를 구현하지 "
                        "않습니다 — 속성도 행도 없는 타입이라 공통 속성을 가질 자리가 "
                        "없습니다."
                    ),
                )
            )
            continue
        wanted, collisions = contract(after, one.interfaces)
        if collisions:
            out.extend(
                Binding(slug, "", "conflict", "", conflict=f"타입 {slug}: {message}")
                for message in collisions
            )
            continue

        was_type = before.types.get(slug)
        was = set(contract(before, was_type.interfaces)[0]) if was_type is not None else set()
        owner = one.managed_by
        for key, (iprop, iface) in sorted(wanted.items()):
            have = one.props.get(key)
            fresh = key not in was
            if have is None:
                todo = Binding(
                    slug, key, "create", iface, shape=iprop.shape, defaults=iprop, fresh=fresh
                )
            else:
                target = replace(
                    iprop.shape, required=iprop.shape.required or have.shape.required
                )
                if fresh or have.explicit:
                    diffs = shape_diff(iprop.shape, have.shape)
                    if diffs:
                        reason = (
                            "모양을 맞추거나 이 속성을 다른 키로 옮긴 뒤 구현하세요"
                            if fresh
                            else "모양은 인터페이스에서 수정합니다"
                        )
                        out.append(
                            Binding(
                                slug,
                                key,
                                "conflict",
                                iface,
                                conflict=(
                                    f"타입 {slug}.{key}: 인터페이스 {iface} 의 공통 속성과 "
                                    f"모양이 다릅니다 — {'; '.join(diffs)}. {reason}."
                                ),
                                fresh=fresh,
                            )
                        )
                        continue
                changed = _changed(have.shape, target)
                if not changed:
                    if fresh:
                        # 바꿀 것 없이 그대로 채택 — 미리 보기가 「이것은 채택된다」 고
                        # 말하게 낸다.
                        out.append(
                            Binding(slug, key, "adopt", iface, shape=target, fresh=True)
                        )
                    continue
                todo = Binding(
                    slug, key, "sync", iface, shape=target, changed=changed, fresh=fresh
                )
            if owner and owner != source:
                out.append(
                    Binding(
                        slug,
                        key,
                        "conflict",
                        iface,
                        fresh=fresh,
                        conflict=(
                            f"타입 {slug}: {owner} 가 관리하는 타입이라 공통 속성 {key} 를 "
                            f"여기서 바꾸지 않습니다 — {owner} 에서 구현을 맞춘 뒤 받으세요."
                        ),
                    )
                )
                continue
            out.append(todo)
    return out


def conflicts(bindings: Iterable[Binding]) -> list[str]:
    return [one.conflict for one in bindings if one.action == "conflict"]


# --- DB ---------------------------------------------------------------------------


def _prop(row: PropertyDef) -> Prop:
    return Prop(
        key=row.key,
        shape=shape_of(row),
        label=row.label,
        help=row.help,
        section=row.section,
        sort_order=row.sort_order,
    )


def load(db: Session) -> Catalog:
    """지금 정의를 한 벌로. 타입 · 인터페이스는 수십 개라 통째로 읽어도 된다."""
    props: dict[tuple[str, Any], dict[str, Prop]] = {}
    for row in db.scalars(
        select(PropertyDef).where(PropertyDef.owner_kind.in_(("type", "interface")))
    ):
        props.setdefault((row.owner_kind, row.owner_id), {})[row.key] = _prop(row)
    catalog = Catalog()
    for iface in db.scalars(select(ObjectInterface)):
        catalog.interfaces[iface.slug] = Iface(
            slug=iface.slug,
            label=iface.label,
            extends=list(iface.extends_slugs or []),
            props=props.get(("interface", iface.id), {}),
            managed_by=iface.managed_by or "",
        )
    for kind in db.scalars(select(ObjectType)):
        catalog.types[kind.slug] = TypeDef(
            slug=kind.slug,
            label=kind.label,
            kind_class=kind.kind_class,
            interfaces=list(kind.interface_slugs or []),
            props=props.get(("type", kind.id), {}),
            managed_by=kind.managed_by or "",
        )
    return catalog


def apply_bindings(db: Session, bindings: Iterable[Binding]) -> None:
    """할 일을 행에 옮긴다 — **충돌이 하나라도 있으면 부르지 않는다**(부르는 쪽이 본다).

    새로 만드는 속성은 그 타입의 속성들 **뒤에** 선다 — 앞에 끼우면 사람이 맞춰 둔 폼의
    순서가 조용히 밀린다.
    """
    todo = [one for one in bindings if one.action in ("create", "sync")]
    if not todo:
        return
    wanted = {one.type_slug for one in todo}
    types = {
        row.slug: row
        for row in db.scalars(select(ObjectType).where(ObjectType.slug.in_(wanted)))
    }
    creates = sorted(
        (one for one in todo if one.action == "create"),
        key=lambda one: (
            one.type_slug,
            one.defaults.sort_order if one.defaults else 0,
            one.key,
        ),
    )
    tail: dict[str, int] = {}
    for one in creates:
        owner = types[one.type_slug]
        if one.type_slug not in tail:
            tail[one.type_slug] = int(
                db.scalar(
                    select(func.coalesce(func.max(PropertyDef.sort_order), 0)).where(
                        PropertyDef.owner_kind == "type", PropertyDef.owner_id == owner.id
                    )
                )
                or 0
            )
        tail[one.type_slug] += 10
        defaults = one.defaults or Prop(key=one.key, shape=Shape())
        row = PropertyDef(
            owner_kind="type",
            owner_id=owner.id,
            key=one.key,
            label=defaults.label or one.key,
            help=defaults.help,
            section=defaults.section,
            sort_order=tail[one.type_slug],
            data_type="text",
        )
        _write_shape(row, one.shape)
        db.add(row)
    for one in todo:
        if one.action != "sync":
            continue
        owner = types[one.type_slug]
        found = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == owner.id,
                PropertyDef.key == one.key,
            )
        )
        if found is not None:
            _write_shape(found, one.shape)
    db.flush()


def _write_shape(row: PropertyDef, shape: Shape | None) -> None:
    if shape is None:
        return
    for name, value in shape.written().items():
        setattr(row, name, value)


def risks(db: Session, bindings: Iterable[Binding]) -> list[str]:
    """**적용은 되지만 조용히 무언가를 잃는 것** — 이미 저장된 값이 새 모양에 안 맞게 되는 일.

    정의 가져오기의 경고(`importer._warn_property_risks`)와 같은 말이다. 구현 타입의 속성이
    인터페이스를 따라 바뀔 때 나온다.
    """
    out: list[str] = []
    todo = [one for one in bindings if one.action == "sync" and one.shape is not None]
    if not todo:
        return out
    types = {
        row.slug: row
        for row in db.scalars(
            select(ObjectType).where(ObjectType.slug.in_({one.type_slug for one in todo}))
        )
    }
    for one in todo:
        owner = types.get(one.type_slug)
        if owner is None or one.shape is None:
            continue
        name = f"{one.type_slug}.{one.key}"
        if "required" in one.changed and one.shape.required:
            without = int(
                db.scalar(
                    select(func.count())
                    .select_from(ObjectInstance)
                    .where(
                        ObjectInstance.type_id == owner.id,
                        ObjectInstance.deleted_at.is_(None),
                        ~ObjectInstance.properties.has_key(one.key),
                    )
                )
                or 0
            )
            out.append(
                f"속성 {name}: 인터페이스 {one.interface} 를 따라 필수가 됩니다. 값이 없는 "
                f"객체 {without}개는 그대로 두지만, 그 객체의 속성을 고칠 때 걸립니다."
            )
        if "multi" in one.changed:
            out.append(
                f"속성 {name}: 인터페이스 {one.interface} 를 따라 여러 값 설정이 바뀝니다. "
                "**이미 저장된 값이 새 모양에 안 맞아** 그 객체는 고칠 때 거절됩니다."
            )
        if "enum_options" in one.changed:
            row = db.scalar(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type",
                    PropertyDef.owner_id == owner.id,
                    PropertyDef.key == one.key,
                )
            )
            removed = sorted(
                set((row.enum_options if row is not None else None) or [])
                - set(one.shape.enum_options or ())
            )
            if removed:
                out.append(
                    f"속성 {name}: 인터페이스 {one.interface} 를 따라 고를 값에서 "
                    f"{', '.join(removed)} 을(를) 뺍니다. 그 값을 가진 객체는 "
                    "**고칠 때 거절**됩니다."
                )
    return out


def unimplement_risks(
    db: Session, type_slug: str, before: Iterable[str], after: Iterable[str]
) -> list[str]:
    """구현을 해제하면 **관계 끝에서 빠지는 것** — 이미 이은 선은 남지만 새로 잇지 못한다.

    끝에 인터페이스를 적은 관계는 그 인터페이스를 구현한 타입을 받는다. 구현을 해제한 타입의
    선은 지우지 않는다(그 선이 틀렸다는 뜻이 아니다) — 대신 그 사실을 저장 전에 말한다.
    """
    extends_of = {
        slug: list(extends or [])
        for slug, extends in db.execute(
            select(ObjectInterface.slug, ObjectInterface.extends_slugs)
        )
    }
    reach_after = {type_slug, *closure(after, extends_of)}
    lost = {type_slug, *closure(before, extends_of)} - reach_after
    if not lost:
        return []
    mine = select(ObjectInstance.id).join(ObjectType, ObjectType.id == ObjectInstance.type_id)
    mine = mine.where(ObjectType.slug == type_slug)
    out: list[str] = []
    for kind in db.scalars(select(RelationType).order_by(RelationType.slug)):
        for side, ends, edge_end, link_type in (
            ("출발", kind.src_type_slugs, ObjectRelation.src_object_id, ObjectLink.src_type),
            ("도착", kind.dst_type_slugs, ObjectRelation.dst_object_id, ObjectLink.dst_type),
        ):
            named = set(ends or ())
            if not named & lost or named & reach_after:
                continue
            count = int(
                db.scalar(
                    select(func.count())
                    .select_from(ObjectRelation)
                    .where(ObjectRelation.relation == kind.slug, edge_end.in_(mine))
                )
                or 0
            ) + int(
                db.scalar(
                    select(func.count())
                    .select_from(ObjectLink)
                    .where(ObjectLink.relation == kind.slug, link_type == type_slug)
                )
                or 0
            )
            out.append(
                f"관계 {kind.slug}: 구현을 해제하면 타입 {type_slug} 은(는) {side} 끝"
                f"({', '.join(sorted(named & lost))})에 더는 안 맞습니다 — 이미 이은 "
                f"{count}건은 남지만 새로 잇지는 못합니다."
            )
    return out
