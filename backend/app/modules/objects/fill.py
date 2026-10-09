"""채울 곳 — **어디부터 채우나.**

데이터 품질(`quality.py`)은 「무엇이 나쁜가」 다 — 필수값이 빈 것 · 깨진 참조 · 이름이 같은 것.
그것만으로는 「지금 무엇을 먼저 채워야 하나」 에 답하지 못한다: 빈 칸은 수천 개이고, 그중
지표가 묶는 칸 · 바깥에 공개한 칸 · 뷰가 거르는 칸이 빈 것과 아무도 안 쓰는 칸이 빈 것은 무게가
다르다. 여기서는 타입마다 비어 있는 것을 세고 **그것을 쓰는 곳**으로 가중해 줄을 세운다 —
「이것을 채우면 무엇이 좋아지나」 와 함께(ADR 0025).

## 세는 것 — 타입마다, 볼 수 있는 것만

    객체 수 · 필수 칸이 빈 객체 · 칸마다 채움률 · 관계 종류마다 선이 없는 객체(그 끝이 이
    타입으로 정해진 종류만) · 지워진 것을 가리키는 칸 · 데이터 소스가 끝점을 못 찾아
    기다리는 선 · 별칭 없는 객체(축만) · 객체가 하나도 없는 타입

끝을 정하지 않은 관계 종류는 세지 않는다 — 어느 타입이 그 선을 가져야 하는지 말할 수 없는데
세면, 모든 타입에 모든 관계가 「비었다」 로 뜬다.

## 가중 — 쓰는 곳

    필수 +3 · 지표(기간 · 기준 · 거르기 · 집계) 하나마다 +3(셋까지) · 코어 공개 +2 ·
    뷰(조건 · 통계 기준) 하나마다 +1(셋까지) · 개수 제약으로 「하나」 인 관계 끝 +2 · 트리 +2

점수 = 가중 * 빈 몫 * log2(1 + 빈 수). 빈 몫이 같으면 큰 타입이 위지만, 로그라 큰 타입 하나가
목록을 다 차지하지는 않는다.

## 큰 타입은 표본으로 — 그렇다고 말한다

타입 하나가 200만 건이면 칸마다 세는 일이 몇 초씩이다. 먼저 id 로 0.1% 를 훑어 타입마다 크기를
어림하고(한 질의), 5만 건이 넘는 타입은 **id 로 고른 2만 건 표본**에서 세어 늘린다 — id 가
uuid4 라 고르게 흩어진다(지표의 `sample_cut` 과 같은 무늬). 그 타입 줄에 `estimated` ·
`sample_rows` 가 서고 `notes` 가 말한다. 셈은 타입마다 질의 하나다(칸 · 관계 · 별칭 · 참조를
`count(*) FILTER` 로 한 번에).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, cast, exists, false, func, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.datasources.models import DataSource
from app.modules.metrics.models import MetricDef
from app.modules.objects import misses, system
from app.modules.objects.models import (
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    ObjectRef,
    ObjectRelation,
    SavedView,
)
from app.modules.objects.scope import find as find_scope
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared.permissions import visible_owner_clause

#: 크기를 어림할 때 훑는 몫의 역수 — id 가 이 몫 안인 행만 센다(0.1%).
PROBE_FRACTION = 1000
#: 이보다 큰(어림) 타입은 표본으로 센다.
BIG_ROWS = 50_000
#: 표본의 크기.
SAMPLE_ROWS = 20_000
#: 타입마다 칸을 몇 개까지 싣나(채움률이 낮은 것부터) — 타입을 하나로 좁히면 전부.
FIELDS_PER_TYPE = 8
#: 줄 세운 것을 몇 개까지.
LIMIT_DEFAULT = 20
LIMIT_MAX = 100

#: 가중 — 맨 위 설명과 같다.
W_REQUIRED = 3
W_METRIC = 3
W_CORE = 2
W_VIEW = 1
W_ONE = 2
W_TREE = 2
USE_CAP = 3

#: 주소의 걸음 머리 — `paths.HOP_KINDS` 와 같다.
_HOPS = ("ref", "out", "in")
#: 객체 자신의 고정 칸 — 쓰는 곳으로 세지 않는다(비어 있을 수 없다).
_FIXED = ("label", "key", "status", "workspace", "created_year", "type")


# --- 쓰는 곳 ---------------------------------------------------------------------


@dataclass
class Uses:
    required: bool = False
    core: bool = False
    metrics: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    one: bool = False
    """관계 끝 — 개수 제약으로 이 쪽은 「하나」 다(부품 → 공급사). 하나씩은 있어야 할
    자리다."""
    tree: bool = False
    """목록의 트리를 세우는 관계."""

    def weight(self) -> int:
        return (
            1
            + (W_REQUIRED if self.required else 0)
            + W_METRIC * min(len(self.metrics), USE_CAP)
            + (W_CORE if self.core else 0)
            + W_VIEW * min(len(self.views), USE_CAP)
            + (W_ONE if self.one else 0)
            + (W_TREE if self.tree else 0)
        )

    def words(self) -> list[str]:
        out: list[str] = []
        if self.required:
            out.append("필수")
        if self.metrics:
            out.append("지표 " + _names(self.metrics))
        if self.core:
            out.append("코어 공개")
        if self.views:
            out.append(f"뷰 {len(set(self.views))}개")
        if self.one:
            out.append("개수 제약(하나)")
        if self.tree:
            out.append("트리")
        return out


def _names(labels: list[str]) -> str:
    unique = list(dict.fromkeys(labels))
    head = " · ".join(f"「{one}」" for one in unique[:USE_CAP])
    return head + (f" 외 {len(unique) - USE_CAP}개" if len(unique) > USE_CAP else "")


@dataclass
class _Usage:
    fields: dict[tuple[uuid.UUID, str], Uses] = field(default_factory=dict)
    relations: dict[tuple[uuid.UUID, str, str], Uses] = field(default_factory=dict)
    sources: dict[uuid.UUID, list[str]] = field(default_factory=dict)
    """타입 → 그 타입을 세는 지표들(원천)."""
    views: dict[uuid.UUID, int] = field(default_factory=dict)

    def field_of(self, type_id: uuid.UUID, key: str) -> Uses:
        return self.fields.setdefault((type_id, key), Uses())

    def relation_of(self, type_id: uuid.UUID, slug: str, side: str) -> Uses:
        return self.relations.setdefault((type_id, slug, side), Uses())


def _targets(
    address: str,
    owner: ObjectType,
    defs: dict[uuid.UUID, dict[str, PropertyDef]],
    types: dict[str, ObjectType],
) -> list[tuple[str, uuid.UUID, str, str]]:
    """주소 하나가 닿는 칸 · 관계 — `(field, 타입 id, 키, "")` ·
    `(relation, 타입 id, 관계, 쪽)`.

    지표의 주소(`properties.grade` · `ref.model.base`)와 뷰 조건의 칸(`grade` ·
    `ref.vendor.country`)을 함께 읽는다. 첫 걸음의 칸과, 참조 너머 첫 칸까지만 — 그 너머는
    「이 칸을 채우면」 의 답이 흐려진다."""
    parts = [one for one in (address or "").split(".") if one]
    if not parts:
        return []
    head = parts[0]
    if head == "properties":
        return [("field", owner.id, parts[1], "")] if len(parts) >= 2 else []
    if head == "ref" and len(parts) >= 2:
        out = [("field", owner.id, parts[1], "")]
        rest = parts[2:]
        if rest and rest[0] == "properties":
            rest = rest[1:]
        ref_def = defs.get(owner.id, {}).get(parts[1])
        target = types.get(ref_def.ref_type_slug or "") if ref_def is not None else None
        if rest and rest[0] not in _HOPS and rest[0] not in _FIXED and target is not None:
            out.append(("field", target.id, rest[0], ""))
        return out
    if head in ("out", "in") and len(parts) >= 2:
        name = parts[1]
        if head == "in" and ":" in name:
            other_slug, _, key = name.partition(":")
            other = types.get(other_slug)
            return [("field", other.id, key, "")] if other is not None else []
        return [("relation", owner.id, name, head)]
    if len(parts) == 1 and head not in _FIXED:
        return [("field", owner.id, head, "")]
    return []


def _metric_addresses(spec: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for one in (spec.get("time"), spec.get("cohort")):
        if isinstance(one, dict):
            out.append(str(one.get("address") or ""))
    out += [str(one.get("address") or "") for one in spec.get("dimensions") or []]
    out += [str(one.get("field") or "") for one in spec.get("filters") or []]
    out += [str(one.get("field") or "") for one in spec.get("share_when") or []]
    if spec.get("measure_field"):
        out.append(str(spec["measure_field"]))
    return [one for one in out if one]


def _view_addresses(row: SavedView) -> list[str]:
    query = row.query or {}
    summary = row.summary or {}
    out = [str(one.get("field") or "") for one in query.get("conditions") or []]
    out += [str(summary.get(one) or "") for one in ("group_by", "split_by", "metric_field")]
    return [one for one in out if one]


def _usage(
    db: Session,
    types: dict[str, ObjectType],
    defs: dict[uuid.UUID, dict[str, PropertyDef]],
    kinds: list[RelationType],
) -> _Usage:
    """지표 · 뷰 · 코어 · 필수 · 트리 — **어디서 쓰나**를 한 번에 읽는다(지표 · 뷰는
    몇십 줄)."""
    out = _Usage()
    by_id = {one.id: one for one in types.values()}
    undirected = {one.slug for one in kinds if not one.directed}

    def touched(owner: ObjectType, addresses: list[str]) -> list[Uses]:
        found: list[Uses] = []
        for address in addresses:
            for what, type_id, name, side in _targets(address, owner, defs, types):
                if what == "field":
                    found.append(out.field_of(type_id, name))
                else:
                    shape = "both" if name in undirected else side
                    found.append(out.relation_of(type_id, name, shape))
        # 한 지표 · 뷰가 같은 칸을 두 번 써도 한 번으로.
        return list({id(one): one for one in found}.values())

    for metric in db.scalars(select(MetricDef).where(MetricDef.is_active.is_(True))):
        owner = by_id.get(metric.source_type_id)
        if owner is None:
            continue
        out.sources.setdefault(owner.id, []).append(metric.label)
        for uses in touched(owner, _metric_addresses(metric.spec or {})):
            uses.metrics.append(metric.label)
    for view in db.scalars(select(SavedView)):
        owner = by_id.get(view.type_id)
        if owner is None:
            continue
        out.views[owner.id] = out.views.get(owner.id, 0) + 1
        for uses in touched(owner, _view_addresses(view)):
            uses.views.append(view.name)
    for owner in types.values():
        for one in defs.get(owner.id, {}).values():
            uses = out.field_of(owner.id, one.key)
            uses.required = one.required
            uses.core = owner.core
        tree = (owner.list_view or {}).get("tree") or {}
        relation = tree.get("relation") if isinstance(tree, dict) else None
        if isinstance(relation, str) and relation:
            # 트리의 부모 쪽 끝 — 부모가 없는 것이 트리에서 「맨 위」 로 떨어진다.
            side = "out" if tree.get("parent") != "src" else "in"
            out.relation_of(owner.id, relation, side).tree = True
    return out


# --- 세기 ------------------------------------------------------------------------


@dataclass
class FieldFill:
    key: str
    label: str
    data_type: str
    required: bool
    filled: int
    missing: int
    rate: float
    """채움률 0~1."""
    uses: list[str]


@dataclass
class RelationFill:
    relation: str
    label: str
    side: str
    """`out`(이 타입이 출발) · `in`(도착) · `both`(방향 없음)."""
    one: bool
    missing: int
    uses: list[str]


@dataclass
class TypeFill:
    type_slug: str
    type_label: str
    usage: str
    core: bool
    objects: int
    estimated: bool = False
    sample_rows: int | None = None
    required_missing: int = 0
    fields: list[FieldFill] = field(default_factory=list)
    fields_more: int = 0
    """싣지 않은 칸의 수(채움률이 높은 것)."""
    relations: list[RelationFill] = field(default_factory=list)
    unresolved_refs: int = 0
    """지워진(또는 없는) 것을 가리키는 칸이 있는 객체."""
    waiting_relations: int = 0
    """데이터 소스가 끝점을 못 찾아 기다리는 선."""
    no_alias: int | None = None
    """별칭이 없는 객체 — 축만(기록은 None)."""
    alias_candidates: int = 0
    """이 타입에서 못 찾은 말(별칭 후보, 대기)."""
    uses: list[str] = field(default_factory=list)


@dataclass
class Priority:
    kind: str
    """`empty_type` · `field` · `relation` · `refs` · `waiting` · `aliases`."""
    type_slug: str
    type_label: str
    target: str
    target_label: str
    missing: int
    total: int
    score: float
    weight: int
    uses: list[str]
    gain: str
    """**이것을 채우면 무엇이 좋아지나.**"""
    link: str
    estimated: bool = False


@dataclass
class Report:
    types: list[TypeFill] = field(default_factory=list)
    priorities: list[Priority] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _filled(key: str) -> Any:
    """값이 있다 — 없는 키 · JSON null · 빈 글자 · 빈 배열이 아니다. **늘 참 · 거짓이다**
    (NULL 이 아니다) — `not_` 으로 뒤집어도 빈 것이 빠지지 않게."""
    raw = ObjectInstance.properties[key]
    return and_(func.coalesce(raw.astext, "") != "", raw != cast("[]", JSONB))


def _edge(relation: str, side: str) -> Any:
    """그 관계의 선이 이 객체에 하나라도 있나 — 객체끼리(`object_relations`)와 원 표 끝
    (`object_links`) 둘 다."""
    me = ObjectInstance.id
    parts = []
    if side in ("out", "both"):
        parts += [
            exists().where(
                ObjectRelation.src_object_id == me, ObjectRelation.relation == relation
            ),
            exists().where(ObjectLink.src_id == me, ObjectLink.relation == relation),
        ]
    if side in ("in", "both"):
        parts += [
            exists().where(
                ObjectRelation.dst_object_id == me, ObjectRelation.relation == relation
            ),
            exists().where(ObjectLink.dst_id == me, ObjectLink.relation == relation),
        ]
    return or_(*parts)


def _broken(keys: list[str]) -> Any:
    """참조 칸이 지워졌거나 없는 객체를 가리킨다 — 참조 색인으로(ADR 0010)."""
    if not keys:
        return false()
    dst = aliased(ObjectInstance, name="fill_dst")
    alive = (
        select(dst.id)
        .where(dst.id == ObjectRef.dst_id, dst.deleted_at.is_(None))
        .correlate(ObjectRef)
        .exists()
    )
    return exists().where(
        ObjectRef.src_id == ObjectInstance.id, ObjectRef.key.in_(keys), not_(alive)
    )


def _sides(
    object_type: ObjectType, kinds: list[RelationType], ends: interfaces.Ends
) -> list[tuple[RelationType, str]]:
    """이 타입이 끝이 되는 관계 종류의 쪽들 — **끝을 정한 종류만.**"""
    out: list[tuple[RelationType, str]] = []
    for kind in kinds:
        src = ends.expand(kind.src_type_slugs)
        dst = ends.expand(kind.dst_type_slugs)
        here_src = src is not None and object_type.slug in src
        here_dst = dst is not None and object_type.slug in dst
        if not kind.directed:
            if here_src or here_dst:
                out.append((kind, "both"))
            continue
        if here_src:
            out.append((kind, "out"))
        if here_dst:
            out.append((kind, "in"))
    return out


def _one(kind: RelationType, side: str) -> bool:
    if side == "out":
        return kind.cardinality in ("one_to_one", "many_to_one")
    if side == "in":
        return kind.cardinality in ("one_to_one", "one_to_many")
    return kind.cardinality == "one_to_one"


def _side_label(kind: RelationType, side: str) -> str:
    if side == "in":
        return kind.inverse_label or f"{kind.label}(받는 쪽)"
    return kind.label


def report(
    db: Session, user: User, *, only: str | None = None, limit: int = LIMIT_DEFAULT
) -> Report:
    """타입마다 세고, 쓰는 곳으로 가중해 줄을 세운다. `only` 는 타입이나 인터페이스 slug."""
    out = Report()
    every = list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.is_active.is_(True))
            .order_by(ObjectType.sort_order, ObjectType.label)
        )
    )
    # 원 표를 비추는 타입(부서 · 계정)은 행이 없다 — 세지 않고, 그것을 가리키는 칸은 참조
    # 색인으로 「지워짐」 을 볼 수 없다(품질이 원 표에 묻는다).
    projected = {one.slug for one in every if system.is_system(one)}
    all_types = {one.slug: one for one in every if not system.is_system(one)}
    defs: dict[uuid.UUID, dict[str, PropertyDef]] = {}
    for one in db.scalars(
        select(PropertyDef)
        .where(PropertyDef.owner_kind == "type")
        .order_by(PropertyDef.sort_order, PropertyDef.label)
    ):
        defs.setdefault(one.owner_id, {})[one.key] = one
    kinds = list(db.scalars(select(RelationType).where(RelationType.is_active.is_(True))))
    ends = interfaces.load_ends(db)
    uses = _usage(db, all_types, defs, kinds)
    waiting = {
        type_id: int(count or 0)
        for type_id, count in db.execute(
            select(
                DataSource.type_id,
                func.sum(func.jsonb_array_length(DataSource.relations_waiting)),
            )
            .where(DataSource.is_active.is_(True))
            .group_by(DataSource.type_id)
        )
    }
    candidates = misses.pending_by_scope(db)

    targets = list(all_types.values())
    if only:
        scope = find_scope(db, only)
        wanted = {one.id for one in scope.types} if scope is not None else set()
        targets = [one for one in targets if one.id in wanted]

    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    probe_cut = uuid.UUID(int=min((2**128) // PROBE_FRACTION, 2**128 - 1))
    probe = {
        type_id: int(count)
        for type_id, count in db.execute(
            select(ObjectInstance.type_id, func.count())
            .where(ObjectInstance.id < probe_cut, ObjectInstance.deleted_at.is_(None), seen)
            .group_by(ObjectInstance.type_id)
        )
    }
    sampled: list[str] = []
    hidden: list[str] = []
    for object_type in targets:
        fill = _count(
            db,
            object_type,
            seen,
            defs=list(defs.get(object_type.id, {}).values()),
            sides=_sides(object_type, kinds, ends),
            uses=uses,
            estimate=probe.get(object_type.id, 0) * PROBE_FRACTION,
            all_fields=bool(only),
            projected=projected,
        )
        fill.waiting_relations = waiting.get(object_type.id, 0)
        fill.alias_candidates = candidates.get(object_type.slug, 0)
        if fill.estimated:
            sampled.append(
                f"{object_type.label}(약 {fill.objects:,}건 — {fill.sample_rows:,}건 표본)"
            )
        out_of_sight = fill.objects == 0 and _exists(db, object_type)
        if out_of_sight:
            hidden.append(object_type.label)
            fill.uses.append("부서 밖이라 안 보임")
        out.types.append(fill)
        out.priorities += _priorities(object_type, fill, uses, hidden=out_of_sight)

    out.priorities.sort(key=lambda one: (-one.score, one.type_label, one.target_label))
    out.priorities = out.priorities[: max(1, min(limit, LIMIT_MAX))]
    if sampled:
        out.notes.append(
            "큰 타입은 id 로 고른 표본에서 세어 늘렸습니다(어림) — "
            + " · ".join(sampled)
            + "."
        )
    if hidden:
        out.notes.append(
            "객체가 있지만 부서 밖이라 안 보이는 타입: "
            + ", ".join(hidden)
            + " — 비어 있는 것이 아니라 볼 권한이 없는 것입니다."
        )
    out.notes.append(
        "관계는 끝이 그 타입으로 정해진 종류만 셉니다. 점수 = 가중(필수 · 지표 · 코어 공개 · "
        "뷰 · 개수 제약 · 트리) * 빈 몫 * log2(1 + 빈 수)."
    )
    return out


def _exists(db: Session, object_type: ObjectType) -> bool:
    return (
        db.scalar(
            select(ObjectInstance.id)
            .where(
                ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
            )
            .limit(1)
        )
        is not None
    )


def _count(
    db: Session,
    object_type: ObjectType,
    seen: Any,
    *,
    defs: list[PropertyDef],
    sides: list[tuple[RelationType, str]],
    uses: _Usage,
    estimate: int,
    all_fields: bool,
    projected: set[str],
) -> TypeFill:
    """한 타입을 **질의 하나로** 센다 — 크면 표본에서."""
    fields = [one for one in defs if one.data_type != "file"]
    required = [one for one in fields if one.required]
    ref_keys = [
        one.key
        for one in fields
        if one.data_type == "object_ref"
        and one.ref_type_slug
        and one.ref_type_slug not in projected
    ]
    axis = object_type.usage == "axis"
    cols: list[Any] = [func.count().label("n")]
    cols += [func.count().filter(_filled(one.key)) for one in fields]
    cols.append(
        func.count().filter(or_(*[not_(_filled(one.key)) for one in required]))
        if required
        else func.count().filter(false())
    )
    cols += [func.count().filter(not_(_edge(kind.slug, side))) for kind, side in sides]
    cols.append(func.count().filter(_broken(ref_keys)))
    cols.append(
        func.count().filter(
            not_(
                exists().where(
                    ObjectAlias.object_id == ObjectInstance.id, ObjectAlias.kind == "alias"
                )
            )
        )
        if axis
        else func.count().filter(false())
    )
    where = [
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.deleted_at.is_(None),
        seen,
    ]
    scale = 1.0
    if estimate > max(BIG_ROWS, SAMPLE_ROWS):
        fraction = SAMPLE_ROWS / estimate
        where.append(ObjectInstance.id < uuid.UUID(int=int(fraction * 2**128)))
        scale = 1 / fraction
    row = list(db.execute(select(*cols).where(*where)).one())
    n = int(row[0])
    at = 1

    def take(count: int) -> int:
        return round(count * scale)

    fill = TypeFill(
        type_slug=object_type.slug,
        type_label=object_type.label,
        usage=object_type.usage,
        core=object_type.core,
        objects=take(n),
        estimated=scale != 1.0,
        sample_rows=n if scale != 1.0 else None,
    )
    found: list[FieldFill] = []
    for one in fields:
        filled = int(row[at])
        at += 1
        found.append(
            FieldFill(
                key=one.key,
                label=one.label,
                data_type=one.data_type,
                required=one.required,
                filled=take(filled),
                missing=take(n - filled),
                rate=round(filled / n, 3) if n else 0.0,
                uses=uses.field_of(object_type.id, one.key).words(),
            )
        )
    fill.required_missing = take(int(row[at]))
    at += 1
    for kind, side in sides:
        relation_uses = uses.relation_of(object_type.id, kind.slug, side)
        relation_uses.one = relation_uses.one or _one(kind, side)
        fill.relations.append(
            RelationFill(
                relation=kind.slug,
                label=_side_label(kind, side),
                side=side,
                one=_one(kind, side),
                missing=take(int(row[at])),
                uses=relation_uses.words(),
            )
        )
        at += 1
    fill.unresolved_refs = take(int(row[at]))
    fill.no_alias = take(int(row[at + 1])) if axis else None
    found.sort(key=lambda one: (one.rate, one.label))
    lacking = [one for one in found if one.missing > 0]
    fill.fields = lacking if all_fields else lacking[:FIELDS_PER_TYPE]
    fill.fields_more = len(found) - len(fill.fields)
    words: list[str] = []
    if uses.sources.get(object_type.id):
        words.append("지표 " + _names(uses.sources[object_type.id]) + "의 원천")
    if object_type.core:
        words.append("코어 공개")
    if uses.views.get(object_type.id):
        words.append(f"뷰 {uses.views[object_type.id]}개")
    fill.uses = words
    return fill


# --- 줄 세우기 -------------------------------------------------------------------


def _score(weight: float, missing: int, total: int) -> float:
    if total <= 0 or missing <= 0:
        return 0.0
    return round(weight * (missing / total) * math.log2(1 + missing), 1)


def _field_gain(fill: TypeFill, one: FieldFill, uses: Uses) -> str:
    parts: list[str] = []
    if uses.required:
        parts.append("필수 칸이라 비어 있는 객체는 수정할 때 거절됩니다")
    if uses.metrics:
        parts.append(
            f"지표 {_names(uses.metrics)}가 이 칸을 씁니다 — 빈 {one.missing:,}건은 "
            "「(비어 있음)」 으로 묶이거나 셈에서 빠집니다"
        )
    if uses.core:
        parts.append("코어 공개 타입이라 수신 시스템이 받는 값이 빕니다")
    if uses.views:
        parts.append(
            f"뷰 {len(set(uses.views))}개가 이 칸으로 거르거나 묶습니다 — 빈 것은 안 나옵니다"
        )
    if not parts:
        parts.append(
            "아직 쓰는 곳이 없는 칸입니다 — 채우면 목록 · 통계에서 이 칸으로 거르고 묶을 수 "
            "있습니다. 채울 자료가 없으면 정의를 정리할지 정하세요"
        )
    return "; ".join(parts) + "."


def _priorities(
    object_type: ObjectType, fill: TypeFill, usage: _Usage, *, hidden: bool
) -> list[Priority]:
    slug, label = object_type.slug, object_type.label
    base = f"/o/{slug}"
    total = fill.objects
    out: list[Priority] = []
    if total == 0:
        if hidden:
            return out
        metrics = usage.sources.get(object_type.id, [])
        views = usage.views.get(object_type.id, 0)
        weight = (
            2
            + W_METRIC * min(len(metrics), USE_CAP)
            + (W_CORE if object_type.core else 0)
            + W_VIEW * min(views, USE_CAP)
        )
        gain = []
        if metrics:
            gain.append(f"지표 {_names(metrics)}의 원천이라 그 지표가 0 입니다")
        if object_type.core:
            gain.append("코어 공개 타입이라 수신 시스템이 빈 목록을 받습니다")
        if not gain:
            gain.append(
                "정의만 있고 객체가 없습니다 — 채울 자료가 있는지, 안 쓰는 정의인지 정하세요"
            )
        out.append(
            Priority(
                kind="empty_type",
                type_slug=slug,
                type_label=label,
                target="",
                target_label="객체",
                missing=0,
                total=0,
                score=float(weight * 3),
                weight=weight,
                uses=fill.uses,
                gain="; ".join(gain) + ".",
                link=base,
            )
        )
        return out
    for one in fill.fields:
        uses = usage.field_of(object_type.id, one.key)
        weight = uses.weight()
        out.append(
            Priority(
                kind="field",
                type_slug=slug,
                type_label=label,
                target=one.key,
                target_label=one.label,
                missing=one.missing,
                total=total,
                score=_score(weight, one.missing, total),
                weight=weight,
                uses=one.uses,
                gain=_field_gain(fill, one, uses),
                link=f"{base}?f.{one.key}.empty=",
                estimated=fill.estimated,
            )
        )
    for relation in fill.relations:
        if not relation.missing:
            continue
        uses = usage.relation_of(object_type.id, relation.relation, relation.side)
        weight = uses.weight()
        head = "in" if relation.side == "in" else "out"
        why = [
            f"「{relation.label}」 선이 없는 {relation.missing:,}건 — 그래프 · 이어진 칸 "
            "조건 · 롤업에서 빠집니다"
        ]
        if uses.metrics:
            why.append(f"지표 {_names(uses.metrics)}가 이 관계를 따라 셉니다")
        if uses.tree:
            why.append("목록의 트리에서 맨 위로 떨어집니다")
        out.append(
            Priority(
                kind="relation",
                type_slug=slug,
                type_label=label,
                target=relation.relation,
                target_label=relation.label,
                missing=relation.missing,
                total=total,
                score=_score(weight, relation.missing, total),
                weight=weight,
                uses=relation.uses,
                gain="; ".join(why) + ".",
                link=f"{base}?f.{head}.{relation.relation}.empty=",
                estimated=fill.estimated,
            )
        )
    if fill.unresolved_refs:
        weight = 3 + (W_CORE if object_type.core else 0)
        out.append(
            Priority(
                kind="refs",
                type_slug=slug,
                type_label=label,
                target="",
                target_label="지워진 것을 가리키는 칸",
                missing=fill.unresolved_refs,
                total=total,
                score=_score(weight, fill.unresolved_refs, total),
                weight=weight,
                uses=fill.uses,
                gain=(
                    f"{fill.unresolved_refs:,}건이 지워진(또는 없는) 객체를 가리킵니다 — "
                    "화면에 뜻 모를 값으로 보입니다. 다른 것으로 바꾸거나 비우세요."
                ),
                link="/quality#broken_ref",
                estimated=fill.estimated,
            )
        )
    if fill.waiting_relations:
        weight = 2
        out.append(
            Priority(
                kind="waiting",
                type_slug=slug,
                type_label=label,
                target="",
                target_label="끝점을 기다리는 선",
                missing=fill.waiting_relations,
                total=fill.waiting_relations,
                score=round(weight * math.log2(1 + fill.waiting_relations), 1),
                weight=weight,
                uses=[],
                gain=(
                    f"데이터 소스가 끝점을 못 찾아 기다리는 선 {fill.waiting_relations:,}줄 — "
                    "가리키는 객체가 들어오면 다음 동기화에서 섭니다. 오래 기다리면 그 객체가 "
                    "원천에 있는지 보세요."
                ),
                link="/admin/datasources",
            )
        )
    if fill.no_alias and (fill.alias_candidates or object_type.core):
        # 별칭 없는 축은 흔하다 — 그것만으로는 줄을 세우지 않는다. **못 찾은 말이 쌓인
        # 타입**(다른 표기로 찾는 사람이 실제로 있다)이나 바깥에 공개한 타입에서만.
        weight = 1 + min(fill.alias_candidates, USE_CAP) + (1 if object_type.core else 0)
        out.append(
            Priority(
                kind="aliases",
                type_slug=slug,
                type_label=label,
                target="",
                target_label="별칭",
                missing=fill.no_alias,
                total=total,
                score=_score(weight / 2, fill.no_alias, total),
                weight=weight,
                uses=fill.uses,
                gain=(
                    f"별칭이 없는 {fill.no_alias:,}건 — 다른 표기로 찾으면 못 찾습니다."
                    + (
                        f" 이 타입에서 못 찾은 말 {fill.alias_candidates:,}개가 별칭 후보로 "
                        "기다립니다."
                        if fill.alias_candidates
                        else ""
                    )
                ),
                link="/quality#alias_candidates",
                estimated=fill.estimated,
            )
        )
    return out
