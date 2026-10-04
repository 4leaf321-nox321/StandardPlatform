"""이어진 것 너머의 칸 — **고르개는 두 걸음, 주소는 셋까지.**

「미국 기업이 만든 툴만」 「개발사 국가별 툴 수」 는 목록에서 바로 나와야 하는 물음이다. 칸이
그 타입 자신의 것뿐이면, 사람은 기업 목록에서 미국 기업을 찾아 적어 두고 툴 목록으로
돌아와 하나씩 넣는다 — 그러다 포기한다.

## 주소의 모양

    ref.<참조 칸>.<칸>      참조 칸이 가리키는 것의 칸              ref.developer.country
    out.<관계>             이 객체에서 나가는 관계로 이어진 것 자체   out.used_by
    out.<관계>.<칸>        그것의 칸                             out.used_by.label
    in.<관계>[.<칸>]       들어오는 관계(방향이 있을 때만 따로 선다)
    in.<타입>:<참조 칸>[.<칸>]
                           나를 가리키는 것 — 그 타입의 참조 칸이 나를 가리키는 객체(ADR 0017)

조건(`f.<주소>.<연산>=값`)과 통계 기준(`group_by=<주소>`)이 **같은 주소**를 쓴다 — 막대를
누르면 그 주소 그대로 조건이 된다. 속성 키에는 점이 없으므로 이 주소와 겹치지 않는다.

## 고르개는 두 걸음, 주소는 셋까지

화면의 고르개는 두 걸음까지 늘어놓는다 — 「서비스 기록 → 개발모델 → 과제 › 프로젝트」, 「→ 기본
모델 › 이름」. 한 걸음만 보이던 때는 기록에서 축의 축으로 가는 물음이 대부분 막혔고, 그때마다
칸을 복사해 두라고 하면 복사한 칸이 원본과 갈린다. 두 걸음이면 고르개가 수백 줄이 되므로 화면은
치거나 훑는 고르개(제목 아래로 모은)에 싣는다. **같은 관계를 되짚는 걸음은 늘어놓지 않는다** —
끝이 출발한 쪽이다. 주소로 적으면 셋까지 받는다(`parse_chain`, ADR 0013 — 지표의 기준). 걸음의
상대가 하나로 정해져 있어야 다음 걸음을 간다.

규모(기록 200만, 2026-10-04): 두 걸음 조건 0.04~0.46초, 두 걸음 통계 2.2초(과제 › 프로젝트)
· 4.1초(기본 모델 이름 2,001가지).

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

from app.modules.accounts.models import User
from app.modules.objects import refedges, system
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.objects.scope import Scope, as_scope
from app.modules.objects.scope import find as find_scope
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared.errors import AppError, code
from app.shared.permissions import visible_owner_clause

HOP_KINDS = ("ref", "out", "in")

#: 걸음을 몇 번까지 잇나. 넷부터는 조건을 화면에서 읽을 수 없고 질의 비용을 짐작할 수 없다.
MAX_HOPS = 3

#: 고르개가 몇 걸음까지 늘어놓나. 주소로는 `MAX_HOPS` 까지 받는다.
PICKER_HOPS = 2

#: 들어오는 참조 걸음의 이름에서 타입과 참조 칸을 가른다 — `in.svc_case:model`. 관계 slug 에는
#: `:` 가 못 들어가 겹치지 않고, 주소의 `.` 와도 안 겹친다.
BACK_SEP = ":"

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
    target: Scope | None
    """칸을 고를 수 있는 상대 — **하나로 정해지고 원 표를 비추지 않을 때만.** 여럿이면
    「국가」 가 어느 타입의 국가인지 정해지지 않는다. 인터페이스 하나면 그 공통 속성을 고른다 —
    구현 타입이 같은 키 · 같은 모양으로 가지므로 어느 타입의 「국가」 든 같은 칸이다."""
    target_defs: list[PropertyDef]
    many: bool
    """한 객체에 여럿이 이어질 수 있나. 그러면 한 행이 여러 막대에 든다."""
    ref_def: PropertyDef | None = None
    relation: RelationType | None = None
    back_type_id: uuid.UUID | None = None
    """들어오는 참조(ADR 0017) — 나를 가리키는 객체의 타입. `back_key` 가 그 참조 칸."""
    back_key: str | None = None

    @property
    def is_back(self) -> bool:
        """나를 가리키는 것 — `in.<타입>:<참조 칸>`. 참조 색인을 거꾸로 걷는다."""
        return self.back_key is not None

    @property
    def heading(self) -> str:
        where = f" ({self.target.label})" if self.target else ""
        if self.is_back:
            return f"가리키는 것 · {self.label}"
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
class Chain:
    """걸음을 이어 붙인 주소 — `ref.model.ref.base.series`, `ref.model.base`,
    `out.rel.label`."""

    path: str
    hops: list[Hop]
    owners: list[Resolver]
    """걸음마다 그 걸음을 가진 Resolver — 관계 걸음의 선(`edges`)과 이름 풀이가 여기서
    나온다."""
    field: str | None
    """None 이면 마지막 관계로 이어진 것 **자체**."""
    definition: PropertyDef | None
    label: str
    data_type: str
    many_hops: bool
    """걸음 중 하나라도 여럿과 이어지나."""
    multi: bool
    """`many_hops` 이거나 끝 칸이 여러 값 칸 — 한 행이 여러 막대에 든다."""

    @property
    def hop(self) -> Hop:
        return self.hops[-1]

    @property
    def ref_def(self) -> PropertyDef | None:
        return (
            self.definition
            if self.definition is not None and self.definition.data_type == "object_ref"
            else None
        )


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


def _no_target(hop: Hop) -> str:
    return (
        f"「{hop.label}」 너머의 칸은 고를 수 없습니다 — "
        "이어지는 타입이 하나로 정해져 있지 않거나, 원 표를 비추는 타입입니다."
    )


def _no_path(path: str, why: str) -> AppError:
    return AppError(
        code("OBJECTS", 86), f"이어진 칸을 쓸 수 없습니다: {path} — {why}", status=422
    )


class Resolver:
    """한 타입(또는 인터페이스의 구현 타입 전부)에서 한 걸음에 닿는 것들. 요청 하나에서 한 번
    만들어 여러 번 쓴다.

    인터페이스 목록이면 참조 칸은 **공통 속성**에서, 관계는 **구현 타입 중 하나라도** 끝에 설
    수 있는 것에서 나온다 — 관계로 이어진 것을 찾는 질의는 객체 id 로 묶이므로 타입을 안
    가린다.
    """

    def __init__(
        self,
        db: Session,
        target: ObjectType | Scope,
        *,
        viewer: User | None = None,
        _kinds: dict[str, refedges.RefKind] | None = None,
    ) -> None:
        self.db = db
        self.scope = as_scope(db, target)
        self.viewer = viewer
        """보는 사람 — 들어오는 참조 걸음은 이 사람이 볼 수 있는 객체만 잇는다(ADR 0017).
        없으면(지표 · 저장 검사) 가리지 않는다."""
        self._hops: list[Hop] | None = None
        self._children: dict[str, Resolver] = {}
        self._kinds = _kinds

    def ref_kinds(self) -> dict[str, refedges.RefKind]:
        """이 설치의 참조 칸 전부 — 걸음의 상대에서 다시 걸을 때도 한 번만 읽는다."""
        if self._kinds is None:
            self._kinds = refedges.kinds(self.db)
        return self._kinds

    def seen(self, entity: Any) -> list[Any]:
        """들어오는 참조로 이은 객체에 걸 가시성 — 보는 사람이 없거나 시스템 관리자면 없다."""
        if self.viewer is None or self.viewer.is_system_admin:
            return []
        return [visible_owner_clause(self.viewer, entity.owner_workspace_id)]

    def _touches(self, allowed: list[str] | None) -> bool:
        """이 범위의 객체가 그 끝에 설 수 있나. 비어 있으면(None) 제약이 없다."""
        return allowed is None or bool(self.scope.match_slugs & set(allowed))

    def hops(self) -> list[Hop]:
        if self._hops is None:
            self._hops = self._load()
        return self._hops

    def _load(self) -> list[Hop]:
        db = self.db
        targets: dict[str, Scope | None] = {}

        def fields_of(slug: str | None) -> tuple[Scope | None, list[PropertyDef]]:
            """상대 하나 — 타입이면 그 속성, 인터페이스면 공통 속성. 걸음마다 안 읽는다."""
            if not slug:
                return None, []
            if slug not in targets:
                found = find_scope(db, slug)
                one_type = found.object_type if found is not None else None
                # 원 표를 비추는 타입(부서 · 계정)은 칸 정의가 없다.
                if one_type is not None and system.is_system(one_type):
                    found = None
                targets[slug] = found
            target = targets[slug]
            return (target, target.defs) if target is not None else (None, [])

        out: list[Hop] = []
        for one in self.scope.defs:
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
                if self._touches(src):
                    sides.append(("out", dst, relation.label))
                if self._touches(dst):
                    sides.append(
                        ("in", src, relation.inverse_label or f"{relation.label} (반대)")
                    )
            elif self._touches(src):
                # 방향이 없으면 한 줄만 선다 — 양쪽이 같은 말로 읽힌다.
                sides.append(("out", dst, relation.label))
            elif self._touches(dst):
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

        # **나를 가리키는 것**(ADR 0017) — 다른 타입의 참조 칸이 이 범위의 타입을 가리키면
        # 그 칸을 거꾸로 걷는 걸음. 축에서 기록으로 내려가는 물음(「증상이 S07 인 기록이 있는
        # 모델」)이 이것이다. 언제나 여럿이다.
        mine = set(self.scope.type_slugs)
        for kind_ in self.ref_kinds().values():
            if not mine & {one.slug for one in kind_.dst_types}:
                continue
            owner = kind_.src_type
            target, defs = fields_of(owner.slug)
            if target is None:
                continue
            # 역방향 이름을 안 적었으면 「가리키는 타입(칸)」 — 같은 타입의 두 칸이 같은 이름이
            # 되지 않게.
            named = kind_.inverse_label != owner.label
            out.append(
                Hop(
                    "in",
                    f"{owner.slug}{BACK_SEP}{kind_.key}",
                    kind_.inverse_label if named else f"{owner.label}({kind_.label})",
                    [owner.slug],
                    target,
                    defs,
                    True,
                    back_type_id=owner.id,
                    back_key=kind_.key,
                )
            )
        return out

    def hop(self, kind: str, name: str) -> Hop | None:
        """이 범위에서 한 걸음 — 참조 칸 이름이나 관계 slug 로."""
        found = next(
            (one for one in self.hops() if one.kind == kind and one.name == name), None
        )
        if found is None and kind == "in":
            # 방향 없는 관계는 out 으로만 선다 — in 으로 적어도 같은 것으로 읽는다.
            found = next(
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
        return found

    def child(self, hop: Hop) -> Resolver:
        """걸음의 상대에서 다시 걷는 Resolver — 상대가 하나로 정해졌을 때만. 같은 상대는 한
        번만 만든다(걸음마다 관계 종류를 다시 읽지 않게)."""
        if hop.target is None:  # pragma: no cover - parse_chain 이 먼저 거른다
            raise _no_path(f"{hop.kind}.{hop.name}", "상대가 하나로 정해져 있지 않습니다.")
        slug = hop.target.slug
        if slug not in self._children:
            self._children[slug] = Resolver(
                self.db, hop.target, viewer=self.viewer, _kinds=self.ref_kinds()
            )
        return self._children[slug]

    def parse_chain(self, path: str, *, max_hops: int = MAX_HOPS) -> Chain:
        """주소 → 걸음들과 끝 칸. 걸음은 `ref.<칸>` · `out.<관계>` · `in.<관계>` 가 이어진
        것이고, 끝은 칸 하나(없으면 마지막 관계로 이어진 것 자체)."""
        tokens = path.split(".")
        hops: list[Hop] = []
        owners: list[Resolver] = []
        owner: Resolver = self
        index = 0
        while index + 1 < len(tokens) and tokens[index] in HOP_KINDS:
            kind, name = tokens[index], tokens[index + 1]
            if len(hops) >= max_hops:
                raise _no_path(path, f"걸음은 {max_hops}번까지 잇습니다.")
            hop = owner.hop(kind, name)
            if hop is None:
                where = "이 타입" if not hops else f"「{hops[-1].label}」"
                raise _no_path(
                    path,
                    f"참조 칸이나 관계 종류가 바뀌었거나, {where}과 이어져 있지 않습니다: "
                    f"{kind}.{name}",
                )
            hops.append(hop)
            owners.append(owner)
            index += 2
            if index < len(tokens):
                if hop.target is None:
                    raise _no_path(path, _no_target(hop))
                owner = owner.child(hop)
        if not hops:
            raise _no_path(path, "주소는 ref.<칸> · out.<관계> · in.<관계> 로 시작합니다.")
        rest = tokens[index:]
        if len(rest) > 1:
            raise _no_path(
                path, f"끝은 칸 하나여야 합니다 — 「{'.'.join(rest)}」 는 칸 이름이 아닙니다."
            )
        last = hops[-1]
        many_hops = any(one.many for one in hops)
        heading = SEP.join(one.label for one in hops)
        if not rest:
            if last.kind == "ref":
                # 고쳐 쓸 주소를 그대로 말한다 — 「걸음을 잇는다」 고 생각하면 끝까지
                # `ref.<칸>` 으로 적기 쉽다(`ref.model.ref.base` → `ref.model.base`).
                fixed = ".".join([*tokens[: index - 2], last.name]) if len(hops) > 1 else None
                raise _no_path(
                    path,
                    f"참조 칸 자체는 「{fixed}」 로 씁니다 — 주소는 칸 이름으로 끝납니다."
                    if fixed
                    else f"참조 칸 자체는 그 칸 이름({last.name})으로 씁니다.",
                )
            return Chain(
                path,
                hops,
                owners,
                None,
                None,
                heading,
                "object_ref" if len(last.target_slugs) == 1 else "relation",
                many_hops,
                many_hops,
            )
        field_name = rest[0]
        if last.target is None:
            raise _no_path(path, _no_target(last))
        if field_name in TARGET_FIXED:
            return Chain(
                path,
                hops,
                owners,
                field_name,
                None,
                f"{heading}{SEP}{TARGET_FIXED[field_name]}",
                "enum" if field_name == "status" else "text",
                many_hops,
                many_hops,
            )
        definition = next((one for one in last.target_defs if one.key == field_name), None)
        if definition is None:
            raise _no_path(path, f"「{last.target.label}」 에 없는 칸입니다: {field_name}")
        return Chain(
            path,
            hops,
            owners,
            field_name,
            definition,
            f"{heading}{SEP}{definition.label}",
            definition.data_type,
            many_hops,
            many_hops or definition.multi,
        )

    def parse(self, path: str) -> PathField:
        """한 걸음 주소 — 고르개 · 옛 호출자용. 걸음을 잇는 것은 `parse_chain`."""
        chain = self.parse_chain(path, max_hops=1)
        return PathField(
            chain.path,
            chain.hop,
            chain.field,
            chain.definition,
            chain.label,
            chain.data_type,
            chain.multi,
        )

    def edges(self, hop: Hop, name: str) -> Any:
        """(me, other) — 이 타입의 객체와 관계로 이어진 것의 id. 객체끼리의 관계와 원 표와
        이은 선(`object_links`)을 함께 본다: 「사용 부서」 는 대개 뒤쪽에 있다."""
        relation = hop.relation
        if relation is None:  # pragma: no cover - 참조 칸 걸음에는 부르지 않는다
            raise ValueError("관계가 아닌 걸음입니다")
        mine = self.scope.type_slugs
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
                ).where(ObjectLink.relation == relation.slug, ObjectLink.src_type.in_(mine)),
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
                ).where(ObjectLink.relation == relation.slug, ObjectLink.dst_type.in_(mine)),
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

    def options(self, *, for_group: bool, depth: int = PICKER_HOPS) -> list[FieldOption]:
        """고르개에 붙일 줄들 — 걸음마다 제목 아래로. 한 걸음 것을 모두 낸 뒤 두 걸음 것.

        둘째 걸음은 첫 걸음의 상대가 하나로 정해졌을 때만 간다(`parse_chain` 과 같은 규칙).
        **같은 관계를 되짚는 걸음은 뺀다** — 「부품 → 공급사 → (공급) → 부품」 의 끝은 출발한
        쪽이라, 칸 목록만 두 배로 길게 하고 물음은 거의 없다."""
        out: list[FieldOption] = []
        for hop in self.hops():
            out.extend(_hop_options(hop, for_group, hop.heading))
        if depth < 2:
            return out
        for first in self.hops():
            if first.target is None:
                continue
            for second in self.child(first).hops():
                if _reverses(first, second, self.scope):
                    continue
                out.extend(
                    _hop_options(
                        second,
                        for_group,
                        f"{first.label}{SEP}{second.heading}",
                        path=f"{first.kind}.{first.name}.",
                        label=f"{first.label}{SEP}",
                        many=first.many,
                    )
                )
        return out


def _reverses(first: Hop, second: Hop, mine: Scope) -> bool:
    """둘째 걸음이 첫 걸음을 되짚나 — 같은 관계를 반대 방향으로(방향 없는 관계면 같은
    쪽으로), 또는 참조 칸을 거꾸로(모델 → 가리키는 기록 → 그 기록의 모델)."""
    if first.kind == "ref" and second.is_back:
        return second.back_key == first.name and second.back_type_id in mine.type_ids
    if first.is_back and second.kind == "ref":
        return second.name == first.back_key and bool(
            set(second.target_slugs) & set(mine.type_slugs)
        )
    if first.relation is None or second.relation is None or first.name != second.name:
        return False
    return first.kind != second.kind or not first.relation.directed


def _hop_options(
    hop: Hop,
    for_group: bool,
    heading: str,
    *,
    path: str = "",
    label: str = "",
    many: bool = False,
) -> list[FieldOption]:
    """걸음 하나의 줄들 — 관계로 이어진 것 자체와 상대의 칸. `path` · `label` 은 앞 걸음."""
    out: list[FieldOption] = []
    prefix = f"{path}{hop.kind}.{hop.name}"
    many = many or hop.many
    # 이어진 것 **자체** — 관계는 조건 · 기준 둘 다, 나를 가리키는 것은 조건만(있음 · 없음 ·
    # 특정 객체). 그것으로 묶으면 가리키는 기록마다 막대 하나가 된다.
    if hop.relation is not None or (hop.is_back and not for_group):
        single = hop.target_slugs[0] if len(hop.target_slugs) == 1 else None
        out.append(
            FieldOption(
                prefix,
                f"{label}{hop.label}",
                heading,
                "object_ref" if single else "relation",
                many,
                None,
                single,
            )
        )
    if hop.target is None:
        return out
    for key, fixed in TARGET_FIXED.items():
        # 조건의 고정 칸은 목록과 같게 이름·식별자뿐이다(상태는 따로 거른다).
        if key == "status" and not for_group:
            continue
        if key == "key" and hop.target.key_policy == "none":
            continue
        out.append(
            FieldOption(
                f"{prefix}.{key}",
                f"{label}{hop.label}{SEP}{fixed}",
                heading,
                "enum" if key == "status" else "text",
                many,
            )
        )
    for one in hop.target_defs:
        if one.data_type == "file":
            continue
        out.append(
            FieldOption(
                f"{prefix}.{one.key}",
                f"{label}{hop.label}{SEP}{one.label}",
                heading,
                one.data_type,
                many or one.multi,
                list(one.enum_options) if one.enum_options else None,
                one.ref_type_slug,
            )
        )
    return out


def ends_at_hop(path: str) -> bool:
    """주소가 걸음으로 끝나나(관계로 이어진 것 **자체**) — 칸으로 끝나면 조각 수가 홀수다.
    `out.used_by` · `ref.model.out.used_by` 는 걸음, `ref.model.series` 는 칸."""
    return len(path.split(".")) % 2 == 0
