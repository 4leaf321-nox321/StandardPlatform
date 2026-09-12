"""이어진 것 너머의 칸 — **한 걸음만.**

「미국 기업이 만든 툴만」 「개발사 국가별 툴 수」 는 목록에서 바로 나와야 하는 물음이다. 칸이
그 타입 자신의 것뿐이면, 사람은 기업 목록에서 미국 기업을 찾아 적어 두고 툴 목록으로
돌아와 하나씩 넣는다 — 그러다 포기한다.

## 주소의 모양

    ref.<참조 칸>.<칸>      참조 칸이 가리키는 것의 칸              ref.developer.country
    out.<관계>             이 객체에서 나가는 관계로 이어진 것 자체   out.used_by
    out.<관계>.<칸>        그것의 칸                             out.used_by.label
    in.<관계>[.<칸>]       들어오는 관계(방향이 있을 때만 따로 선다)

조건(`f.<주소>.<연산>=값`)과 통계 기준(`group_by=<주소>`)이 **같은 주소**를 쓴다 — 막대를
누르면 그 주소 그대로 조건이 된다. 속성 키에는 점이 없으므로 이 주소와 겹치지 않는다.

## 한 걸음만인 이유

두 걸음(「개발사의 모회사의 국가」)부터는 화면에서 조건을 읽을 수 없고, 질의 비용도 예측이
안 된다. 그런 물음이 자주 나오면 그것은 대개 **칸이 하나 빠진** 것이다.

## 뜻 — 「이어진 것 중 하나라도」

이어진 것이 여럿이면(여러 값 참조, 여럿과 맺는 관계) 조건은 **그중 하나라도 맞으면** 걸린다.
이어진 것이 없는 객체는 이어진 것의 칸 조건에 안 걸린다 — 「개발사 › 국가 ≠ 미국」 에 개발사가
빈 툴은 안 나온다. 이어진 것이 없는 것은 그 참조 칸·관계의 「비어 있음」 으로 묻는다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, cast, select, union_all
from sqlalchemy.orm import Session

from app.modules.objects import system
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared.errors import AppError, code

HOP_KINDS = ("ref", "out", "in")

#: 이어진 것의 고정 칸.
TARGET_FIXED = {"label": "이름", "key": "식별자", "status": "상태"}

SEP = " › "

#: 이 방향에서 **여럿과** 이어질 수 있는 관계 종류의 개수 제약.
_MANY = {"out": ("one_to_many", "many_to_many"), "in": ("many_to_one", "many_to_many")}


def is_path(field_name: str) -> bool:
    head, _, rest = field_name.partition(".")
    return head in HOP_KINDS and bool(rest)


@dataclass
class Hop:
    """한 걸음 — 참조 칸 하나, 또는 관계 종류의 한 방향."""

    kind: str
    name: str
    label: str
    target_slugs: list[str]
    target: ObjectType | None
    """칸을 고를 수 있는 상대 타입 — **하나로 정해지고 원 표를 비추지 않을 때만.** 여럿이면
    「국가」 가 어느 타입의 국가인지 정해지지 않는다."""
    target_defs: list[PropertyDef]
    many: bool
    """한 객체에 여럿이 이어질 수 있나. 그러면 한 행이 여러 막대에 든다."""
    ref_def: PropertyDef | None = None
    relation: RelationType | None = None

    @property
    def heading(self) -> str:
        where = f" ({self.target.label})" if self.target else ""
        return f"{self.label}{where}" if self.kind == "ref" else f"관계 · {self.label}{where}"


@dataclass
class PathField:
    path: str
    hop: Hop
    field: str | None
    """None 이면 관계로 이어진 것 **자체**."""
    definition: PropertyDef | None
    label: str
    data_type: str
    """`relation` 이면 상대 타입이 하나로 정해지지 않아 있음/없음만 물을 수 있다."""
    multi: bool


@dataclass
class FieldOption:
    """고르개에 서는 줄 하나."""

    field: str
    label: str
    heading: str
    data_type: str
    multi: bool = False
    enum_options: list[str] | None = None
    ref_type_slug: str | None = None


def _no_path(path: str, why: str) -> AppError:
    return AppError(
        code("OBJECTS", 86), f"이어진 칸을 쓸 수 없습니다: {path} — {why}", status=422
    )


class Resolver:
    """한 타입에서 한 걸음에 닿는 것들. 요청 하나에서 한 번 만들어 여러 번 쓴다."""

    def __init__(self, db: Session, object_type: ObjectType) -> None:
        self.db = db
        self.object_type = object_type
        self._hops: list[Hop] | None = None

    def hops(self) -> list[Hop]:
        if self._hops is None:
            self._hops = self._load()
        return self._hops

    def _load(self) -> list[Hop]:
        db, me = self.db, self.object_type
        types = {row.slug: row for row in db.scalars(select(ObjectType))}

        def fields_of(slug: str | None) -> tuple[ObjectType | None, list[PropertyDef]]:
            target = types.get(slug or "")
            if target is None or system.is_system(target):
                return None, []
            return target, properties_of(db, target.id)

        out: list[Hop] = []
        for one in properties_of(db, me.id):
            if one.data_type != "object_ref":
                continue
            target, defs = fields_of(one.ref_type_slug)
            # 원 표를 비추는 타입(부서·계정)은 칸 정의가 없다 — 참조 칸 자체가 이미 기준이다.
            if target is None:
                continue
            out.append(
                Hop(
                    "ref",
                    one.key,
                    one.label,
                    [target.slug],
                    target,
                    defs,
                    one.multi,
                    ref_def=one,
                )
            )

        relations = db.scalars(
            select(RelationType)
            .where(RelationType.is_active.is_(True))
            .order_by(RelationType.label)
        )
        for relation in relations:
            src, dst = relation.src_type_slugs, relation.dst_type_slugs
            sides: list[tuple[str, list[str] | None, str]] = []
            if relation.directed:
                if src is None or me.slug in src:
                    sides.append(("out", dst, relation.label))
                if dst is None or me.slug in dst:
                    sides.append(
                        ("in", src, relation.inverse_label or f"{relation.label} (반대)")
                    )
            elif src is None or me.slug in src:
                # 방향이 없으면 한 줄만 선다 — 양쪽이 같은 말로 읽힌다.
                sides.append(("out", dst, relation.label))
            elif dst is None or me.slug in dst:
                sides.append(("out", src, relation.label))
            for kind, theirs, label in sides:
                slugs = list(theirs or [])
                target, defs = fields_of(slugs[0]) if len(slugs) == 1 else (None, [])
                many = (
                    relation.cardinality in _MANY[kind]
                    if relation.directed
                    else relation.cardinality != "one_to_one"
                )
                out.append(
                    Hop(
                        kind,
                        relation.slug,
                        label,
                        slugs,
                        target,
                        defs,
                        many,
                        relation=relation,
                    )
                )
        return out

    def parse(self, path: str) -> PathField:
        head, _, rest = path.partition(".")
        name, _, field_name = rest.partition(".")
        hop = next((one for one in self.hops() if one.kind == head and one.name == name), None)
        if hop is None and head == "in":
            # 방향 없는 관계는 out 으로만 선다 — in 으로 적어도 같은 것으로 읽는다.
            hop = next(
                (
                    one
                    for one in self.hops()
                    if one.kind == "out"
                    and one.name == name
                    and one.relation is not None
                    and not one.relation.directed
                ),
                None,
            )
        if hop is None:
            raise _no_path(
                path, "참조 칸이나 관계 종류가 바뀌었거나, 이 타입과 이어져 있지 않습니다."
            )
        if not field_name:
            if hop.kind == "ref":
                raise _no_path(path, f"참조 칸 자체는 그 칸 이름({hop.name})으로 씁니다.")
            return PathField(
                path,
                hop,
                None,
                None,
                hop.label,
                "object_ref" if len(hop.target_slugs) == 1 else "relation",
                hop.many,
            )
        if hop.target is None:
            raise _no_path(
                path,
                f"「{hop.label}」 너머의 칸은 고를 수 없습니다 — "
                "이어지는 타입이 하나로 정해져 있지 않거나, 원 표를 비추는 타입입니다.",
            )
        if field_name in TARGET_FIXED:
            return PathField(
                path,
                hop,
                field_name,
                None,
                f"{hop.label}{SEP}{TARGET_FIXED[field_name]}",
                "enum" if field_name == "status" else "text",
                hop.many,
            )
        definition = next((one for one in hop.target_defs if one.key == field_name), None)
        if definition is None:
            raise _no_path(path, f"「{hop.target.label}」 에 없는 칸입니다: {field_name}")
        return PathField(
            path,
            hop,
            field_name,
            definition,
            f"{hop.label}{SEP}{definition.label}",
            definition.data_type,
            hop.many or definition.multi,
        )

    def edges(self, hop: Hop, name: str) -> Any:
        """(me, other) — 이 타입의 객체와 관계로 이어진 것의 id. 객체끼리의 관계와 원 표와
        이은 선(`object_links`)을 함께 본다: 「사용 부서」 는 대개 뒤쪽에 있다."""
        relation = hop.relation
        if relation is None:  # pragma: no cover - 참조 칸 걸음에는 부르지 않는다
            raise ValueError("관계가 아닌 걸음입니다")
        me = self.object_type.slug
        parts: list[Any] = []
        if hop.kind == "out" or not relation.directed:
            parts += [
                select(
                    ObjectRelation.src_object_id.label("me"),
                    cast(ObjectRelation.dst_object_id, String).label("other"),
                ).where(ObjectRelation.relation == relation.slug),
                select(
                    ObjectLink.src_id.label("me"),
                    cast(ObjectLink.dst_id, String).label("other"),
                ).where(ObjectLink.relation == relation.slug, ObjectLink.src_type == me),
            ]
        if hop.kind == "in" or not relation.directed:
            parts += [
                select(
                    ObjectRelation.dst_object_id.label("me"),
                    cast(ObjectRelation.src_object_id, String).label("other"),
                ).where(ObjectRelation.relation == relation.slug),
                select(
                    ObjectLink.dst_id.label("me"),
                    cast(ObjectLink.src_id, String).label("other"),
                ).where(ObjectLink.relation == relation.slug, ObjectLink.dst_type == me),
            ]
        return union_all(*parts).subquery(name)

    def names(self, hop: Hop, keys: list[str]) -> dict[str, str]:
        """관계로 이어진 것들의 이름 — 원 표(부서 등)에서 먼저, 나머지는 객체에서."""
        wanted: set[uuid.UUID] = set()
        for one in keys:
            try:
                wanted.add(uuid.UUID(one))
            except ValueError:
                continue
        out: dict[str, str] = {}
        if not wanted:
            return out
        types = system.types_by_slug(self.db)
        for slug in hop.target_slugs:
            target = types.get(slug)
            if target is not None and system.is_system(target):
                for key, ref in (
                    system.source_of(target).lookup(self.db, sorted(wanted)).items()
                ):
                    out[str(key)] = ref.label
        rest = [one for one in wanted if str(one) not in out]
        if rest:
            for row in self.db.scalars(
                select(ObjectInstance).where(ObjectInstance.id.in_(rest))
            ):
                out[str(row.id)] = (
                    row.label if row.deleted_at is None else f"{row.label} (지워짐)"
                )
        return out

    def options(self, *, for_group: bool) -> list[FieldOption]:
        """고르개에 붙일 줄들 — 걸음마다 제목 아래로."""
        out: list[FieldOption] = []
        for hop in self.hops():
            prefix = f"{hop.kind}.{hop.name}"
            if hop.relation is not None:
                single = hop.target_slugs[0] if len(hop.target_slugs) == 1 else None
                out.append(
                    FieldOption(
                        prefix,
                        hop.label,
                        hop.heading,
                        "object_ref" if single else "relation",
                        hop.many,
                        None,
                        single,
                    )
                )
            if hop.target is None:
                continue
            for key, label in TARGET_FIXED.items():
                # 조건의 고정 칸은 목록과 같게 이름·식별자뿐이다(상태는 따로 거른다).
                if key == "status" and not for_group:
                    continue
                if key == "key" and hop.target.key_policy == "none":
                    continue
                out.append(
                    FieldOption(
                        f"{prefix}.{key}",
                        f"{hop.label}{SEP}{label}",
                        hop.heading,
                        "enum" if key == "status" else "text",
                        hop.many,
                    )
                )
            for one in hop.target_defs:
                if one.data_type == "file":
                    continue
                out.append(
                    FieldOption(
                        f"{prefix}.{one.key}",
                        f"{hop.label}{SEP}{one.label}",
                        hop.heading,
                        one.data_type,
                        hop.many or one.multi,
                        list(one.enum_options) if one.enum_options else None,
                        one.ref_type_slug,
                    )
                )
        return out
