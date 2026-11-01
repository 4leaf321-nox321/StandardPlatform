"""통째 그림 — 지식 그래프 탐색의 **정적 그리기**(ADR 0045).

탐색 그림(라우터의 neighborhood · subgraph)은 노드마다 이웃 30개, 그림에 300개까지로
자르고 잘린 자리에 「+N」 을 붙인다 — 한 걸음씩 펼쳐 읽는 그림이다. 정적 그리기는
「구조를 한눈에」 를 묻는 사람의 그림이라 **자르지 않는다**: 씨앗(객체 하나 또는 타입
전부)에서 몇 단계 안의 노드와 그 사이의 선을 전부 준다.

자르지 않는 대신 상한(`MAX_NODES` · `MAX_EDGES`)이 크고, **넘으면 반쯤 주지 않고 센 수와
함께 거절한다**(`Over`). 반쯤 그린 그림은 「이게 전부」 로 읽힌다 — 탐색 그림의 「+N」 이
막던 바로 그 거짓말이다. 넘은 것을 끝까지 세지는 않는다(넘은 자리에서 멈춘다 — 그래서
「N 개 이상」).

## 집합으로 넓힌다

노드마다 이웃을 묻지 않는다(그러면 질의가 20만 번이다). 모은 노드를 **임시 표**
(`graph_whole_nodes`, 트랜잭션이 끝나면 사라진다)에 두고, 한 단계마다 선 종류(관계 ·
원 표 링크 · 참조 칸, 각각 양방향)별로 `INSERT … SELECT` 한 번씩 넓힌다 — 질의 수는
단계 x 여섯이다. 넣는 질의마다 남은 자리만큼만 읽고(`LIMIT`), 그 자리가 다 차면 상한을
넘은 것이다.

선은 다 모은 뒤 **노드 표끼리 이어 한 번에** 읽는다 — 양 끝이 다 그림 안인 선 전부.
탐색 그림이 「이미 실린 노드끼리의 선」 을 마저 긋는 것과 같은 뜻이고, 넓히며 지나온 선도
모두 여기 든다. 선의 양 끝은 번호로 받는다(번호 표 `graph_whole_index`) — uuid 두 개를
백만 줄 받으면 그것만으로 초 단위다.

## 탐색 그림과 같은 뜻

보이는 범위(`visible_owner_clause`) · 지운 것 · 기준일(`edges.alive`, ADR 0039) ·
기록(ADR 0011 — 나를 가리키는 기록은 `records` 일 때만) · 관계와 이웃 타입 거르기 ·
원 표 링크의 규칙은 이웃 그림(`graph.neighbor_edges` · `neighbor_link_edges` ·
`refedges.neighbor_edges`)과 같다. 다른 것은 노드마다 자르지 않는다는 것 하나다.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    literal_column,
    null,
    select,
    text,
    true,
    tuple_,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import ColumnElement, Select

from app.modules.accounts.models import User
from app.modules.objects import edges as edge_rule
from app.modules.objects import refedges, system, typecounts
from app.modules.objects.graph import _link_other_visible
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRef, ObjectRelation
from app.modules.ontology.models import ObjectType
from app.shared.permissions import sees_every_department, visible_owner_clause
from app.shared.system_sources import SystemRef

#: 상한 — 정적 그리기의 **한 자리**(라우터 · 화면 안내가 이 값을 응답으로 받는다).
#:
#: 노드 20만: 브라우저(WebGL)가 그리고 배치(ForceAtlas2, 작업자 스레드)가 수십 초 안에 식는
#: 크기다(ADR 0045 실측). 선 70만: 브라우저에서 **잰 것이 선 62만까지**다(시장 서비스 전부 ·
#: 1단계 — 힙 369MB · 15.9초). 기록은 참조 칸이 넷 다섯이라 50만이면 그 그림이 선 때문에
#: 거절되고, 100만은 재지 않은 추정(약 0.6GB)이었다 — 잰 범위에 여유를 조금 더한 값으로
#: 둔다(2026-11-01 사용자 결정). 넘는 범위는 전체 지도(ADR 0046)가 받는다 — 거절 안내의
#: 「지도에서 보기」. **넘으면 자르지 않고 거절한다** — 그것이 이 크기를 감당하는 방법이다.
MAX_NODES = 200_000
MAX_EDGES = 700_000
MAX_DEPTH = 6

#: 앞 단계의 노드가 이 수 이하면 노드마다 색인을 찾게 묶는다(`_candidates`) — 그 위로는
#: 노드마다 도는 비용이 쌓여 거꾸로 느리다(앞 단계 13.6만: 묶으면 9.7초, 맡기면 0.8초).
FENCE_UP_TO = 20_000

_META = MetaData()

#: 모은 노드 — 트랜잭션 동안만 있는 임시 표. 타입을 **넣을 때 함께 적는다**(`type_id`) —
#: 거절의 타입별 수를 세려고 20만 노드를 객체 표와 다시 이으면 2,000만 건 DB 에서 그것만
#: 20초였다(ADR 0045 실측). `sys_type` 은 원 표 행(부서 · 계정)의 타입 slug — 그 행은
#: `objects` 에 없다.
NODES = Table(
    "graph_whole_nodes",
    _META,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("depth", SmallInteger, nullable=False),
    Column("parent", PgUUID(as_uuid=True)),
    Column("type_id", PgUUID(as_uuid=True)),
    Column("sys_type", String),
    prefixes=["TEMPORARY"],
    postgresql_on_commit="DROP",
)

#: 다 모은 뒤 번호를 매긴 표(`CREATE … AS` 로 만든다) — 선을 번호로 받는 데 쓴다.
INDEX = Table(
    "graph_whole_index",
    _META,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("idx", Integer),
    Column("depth", SmallInteger),
    Column("parent", PgUUID(as_uuid=True)),
    Column("type_id", PgUUID(as_uuid=True)),
    Column("sys_type", String),
)


@dataclass(frozen=True)
class Filters:
    """넓히는 규칙 — 이웃 그림의 거르기와 같은 뜻."""

    at: date | None
    relations: list[str] | None
    """관계 slug(참조 칸은 `ref:…`). None 이면 전부."""
    type_ids: list[uuid.UUID] | None
    """이웃 타입(객체) — 씨앗의 타입은 부르는 쪽이 넣어 둔다."""
    type_slugs: list[str] | None
    """같은 거르기를 원 표 링크의 끝(slug)에."""
    skip_logs: bool
    """나를 가리키는 기록(ADR 0011)을 안 따라간다."""


@dataclass(frozen=True)
class Seed:
    """어디서 시작하나 — 객체(또는 원 표 행) 하나, 또는 타입(들)의 인스턴스 전부."""

    focus: uuid.UUID | None = None
    focus_type: uuid.UUID | None = None
    focus_system: str | None = None
    """시작점이 원 표 행이면 그 타입 slug."""
    type_ids: list[uuid.UUID] = field(default_factory=list)
    system_rows: list[tuple[str, SystemRef]] = field(default_factory=list)
    """씨앗 타입 중 원 표를 비추는 것의 행 — (타입 slug, 행)."""


@dataclass
class Over:
    """상한을 넘었다 — 그림 대신 이것."""

    what: str
    """`nodes` · `edges`."""
    count: int
    exact: bool
    by_type: dict[str, int]
    layers: list[int]
    by_relation: dict[str, int] = field(default_factory=dict)


@dataclass
class Whole:
    """통째 그림 — 노드는 열 배열(같은 자리가 같은 노드), 선은 노드 번호 둘과 종류."""

    ids: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    """노드의 타입 slug."""
    labels: list[str] = field(default_factory=list)
    depth: list[int] = field(default_factory=list)
    parent: list[int] = field(default_factory=list)
    src: list[int] = field(default_factory=list)
    dst: list[int] = field(default_factory=list)
    rel: list[str] = field(default_factory=list)
    """선의 종류 slug(관계 · `ref:…` · 링크의 관계)."""
    layers: list[int] = field(default_factory=list)
    over: Over | None = None


def seed_count(
    db: Session, *, user: User, type_ids: list[uuid.UUID], systems: int
) -> tuple[int, bool, dict[uuid.UUID, int]]:
    """타입 씨앗의 노드 수를 **먼저** 센다 — (수, 정확한가, 타입별 수).

    모든 부서를 보는 사람이면 계수표(ADR 0038)라 큰 타입도 즉시다. 부서로 조회를 좁힌 설치는
    보이는 것만 세야 해서 객체 표를 세되, 상한 + 1 에서 멈춘다(넘으면 「이상」)."""
    if sees_every_department(user):
        counted = typecounts.counts(db)
        by_type = {one: counted.get(one, 0) for one in type_ids}
        return sum(by_type.values()) + systems, True, by_type
    obj = ObjectInstance
    capped = (
        select(obj.type_id)
        .where(
            obj.type_id.in_(type_ids),
            obj.deleted_at.is_(None),
            visible_owner_clause(user, obj.owner_workspace_id),
        )
        .limit(MAX_NODES + 1)
        .subquery()
    )
    rows = db.execute(select(capped.c.type_id, func.count()).group_by(capped.c.type_id))
    by_type = {type_id: int(n) for type_id, n in rows}
    total = sum(by_type.values())
    return total + systems, total <= MAX_NODES, by_type


def gather(
    db: Session,
    *,
    user: User,
    seed: Seed,
    depth: int,
    systems: list[str],
    filters: Filters,
    type_slug_of: dict[uuid.UUID, str],
) -> Whole:
    """씨앗에서 `depth` 단계 안의 노드와 그 사이의 선 전부 — 상한을 넘으면 `over` 만 채운다.

    `systems` 는 원 표를 비추는 타입 slug(링크의 저쪽 끝이 늘 보이는 것), `type_slug_of` 는
    타입 id → slug(노드의 타입과 거절의 타입별 수가 쓴다)."""
    _fresh(db)
    _plant(db, user, seed)
    total = int(db.scalar(select(func.count()).select_from(NODES)) or 0)
    layers = [total]
    if total > MAX_NODES:
        return Whole(layers=layers, over=_over_nodes(db, total, layers, type_slug_of))
    kinds = refedges.kinds(db)
    ref_out = _ref_pairs(kinds, filters.relations, skip_logs=False)
    ref_in = _ref_pairs(kinds, filters.relations, skip_logs=filters.skip_logs)
    # 칸이 가리킬 수 있는 타입 — 대상이 인터페이스면 구현 타입 전부.
    targets = {
        (kind.src_type.id, kind.key): {one.id for one in kind.dst_types}
        for kind in kinds.values()
    }
    for k in range(depth):
        if layers[-1] == 0:
            break
        db.execute(text("ANALYZE graph_whole_nodes"))
        before = total
        # **앞 단계 노드의 타입에 닿는 칸만** 묻는다(이웃 그림의 `refedges.neighbor_edges` 와
        # 같다). 안 거르면 모델 6천 개에서 「모델을 가리키는 기록 아닌 칸」 을 찾느라 모델마다
        # 그 모델을 가리키는 기록 색인(모델당 3천여 줄)을 다 훑었다 — 2,000만 건 DB 에서 60초
        # 상한에 걸렸다(ADR 0045).
        here_types = set(
            db.scalars(select(NODES.c.type_id).where(NODES.c.depth == k).distinct())
        )
        out_k = {pair: slug for pair, slug in ref_out.items() if pair[0] in here_types}
        in_k = {pair: slug for pair, slug in ref_in.items() if targets[pair] & here_types}
        fence = layers[-1] <= FENCE_UP_TO
        for stmt in _candidates(k, user, systems, out_k, in_k, filters, fence=fence):
            total, full = _grow_all(db, stmt, total)
            if full:
                layers.append(total - before)
                return Whole(layers=layers, over=_over_nodes(db, total, layers, type_slug_of))
        layers.append(total - before)
    while layers and layers[-1] == 0 and len(layers) > 1:
        layers.pop()

    _number(db)
    whole = Whole(layers=layers)
    too_many = _edges(db, whole, filters, _ref_pairs(kinds, filters.relations))
    if too_many is not None:
        whole.over = Over(
            what="edges",
            count=too_many[0],
            exact=False,
            by_type=_count_types(db, INDEX, type_slug_of),
            layers=layers,
            by_relation=too_many[1],
        )
        whole.src, whole.dst, whole.rel = [], [], []
        return whole
    _nodes(db, whole)
    return whole


# --- 넓히기 ---------------------------------------------------------------------


def _fresh(db: Session) -> None:
    """임시 표를 새로 — 같은 트랜잭션에서 두 번 불려도 앞의 것이 남지 않게."""
    db.execute(text("DROP TABLE IF EXISTS pg_temp.graph_whole_nodes"))
    db.execute(text("DROP TABLE IF EXISTS pg_temp.graph_whole_index"))
    NODES.create(db.connection())


def _plant(db: Session, user: User, seed: Seed) -> None:
    """씨앗을 0단계로 심는다."""
    if seed.focus is not None:
        db.execute(
            pg_insert(NODES).values(
                id=seed.focus,
                depth=0,
                parent=None,
                type_id=seed.focus_type,
                sys_type=seed.focus_system,
            )
        )
        return
    if seed.type_ids:
        obj = ObjectInstance
        db.execute(
            pg_insert(NODES)
            .from_select(
                ["id", "depth", "parent", "type_id", "sys_type"],
                select(
                    obj.id,
                    literal_column("0"),
                    cast(null(), PgUUID(as_uuid=True)),
                    obj.type_id,
                    cast(null(), String),
                ).where(
                    obj.type_id.in_(seed.type_ids),
                    obj.deleted_at.is_(None),
                    visible_owner_clause(user, obj.owner_workspace_id),
                ),
            )
            .on_conflict_do_nothing(index_elements=[NODES.c.id])
        )
    if seed.system_rows:
        db.execute(
            pg_insert(NODES)
            .values(
                [
                    {
                        "id": ref.id,
                        "depth": 0,
                        "parent": None,
                        "type_id": None,
                        "sys_type": slug,
                    }
                    for slug, ref in seed.system_rows
                ]
            )
            .on_conflict_do_nothing(index_elements=[NODES.c.id])
        )


def _ref_pairs(
    kinds: dict[str, refedges.RefKind], relations: list[str] | None, *, skip_logs: bool = False
) -> dict[tuple[uuid.UUID, str], str]:
    """참조 칸 — (가리키는 타입 id, 칸 키) → 선의 slug. 관계 거르기에 참조 칸이 없으면
    빈 것."""
    wanted = None if relations is None else {one for one in relations if refedges.is_ref(one)}
    return {
        (kind.src_type.id, kind.key): kind.slug
        for kind in kinds.values()
        if (wanted is None or kind.slug in wanted)
        and not (skip_logs and kind.src_type.usage == "log")
    }


def _candidates(
    k: int,
    user: User,
    systems: list[str],
    ref_out: dict[tuple[uuid.UUID, str], str],
    ref_in: dict[tuple[uuid.UUID, str], str],
    filters: Filters,
    *,
    fence: bool,
) -> list[Select[Any]]:
    """k 단계의 노드에서 한 걸음 — 아직 안 모은 저쪽 끝(id · 단계 · 데려온 노드 · 타입).

    선 종류마다 하나, 양방향이라 여섯이다. 저쪽 끝의 규칙은 이웃 그림과 같다: 객체면 지우지
    않았고 보이는 것, 원 표 행이면 늘(원 표의 행은 부서 소유가 아니다).

    **노드마다 색인을 찾게 묶는다**(`JOIN LATERAL`). 그냥 이으면 계획기가 `LIMIT` 를 믿고
    선 표를 처음부터 읽는 길을 고른다 — 기본 모델 2천 개를 가리키는 기록을 찾느라 참조 색인
    5,000만 줄을 다 읽었다(2,000만 건 DB, 12.5초 → ADR 0045). 이 모양이면 드는 일이 표의 크기가
    아니라 앞 단계의 노드 수에 묶인다. 앞 단계가 크면(`FENCE_UP_TO` 초과) 묶지 않는다 — 그때는
    선 표를 한 번 읽는 편이 노드마다 찾는 것보다 빠르고, 계획기도 그 길을 고른다."""
    here = NODES.alias("here")
    seen = NODES.alias("seen")
    step: ColumnElement[int] = literal_column(str(k + 1), SmallInteger)
    no_type = cast(null(), String)
    far_sides: list[Select[Any]] = []

    for near, far in (("src_object_id", "dst_object_id"), ("dst_object_id", "src_object_id")):
        rel = aliased(ObjectRelation)
        obj = aliased(ObjectInstance)
        far_col = getattr(rel, far)
        side = (
            select(
                far_col.label("id"), obj.type_id.label("type_id"), no_type.label("sys_type")
            )
            .select_from(rel)
            .join(obj, obj.id == far_col)
            .where(
                getattr(rel, near) == here.c.id,
                edge_rule.alive(rel, filters.at),
                obj.deleted_at.is_(None),
                visible_owner_clause(user, obj.owner_workspace_id),
            )
        )
        if filters.relations:
            side = side.where(rel.relation.in_(filters.relations))
        if filters.type_ids:
            side = side.where(obj.type_id.in_(filters.type_ids))
        far_sides.append(side)

    for near, far, far_kind in (
        ("src_id", "dst_id", "dst_type"),
        ("dst_id", "src_id", "src_type"),
    ):
        link = aliased(ObjectLink)
        far_col, far_type = getattr(link, far), getattr(link, far_kind)
        # 저쪽 끝이 객체면 그 타입 — 링크는 드물어 줄마다 묻는다(원 표 행이면 비어 있다).
        far_obj = aliased(ObjectInstance)
        far_type_id = select(far_obj.type_id).where(far_obj.id == far_col).scalar_subquery()
        side = select(
            far_col.label("id"),
            far_type_id.label("type_id"),
            case((far_type.in_(systems), far_type), else_=no_type).label("sys_type"),
        ).where(
            getattr(link, near) == here.c.id,
            _link_other_visible(user, far_type, far_col, systems),
        )
        if filters.relations:
            side = side.where(link.relation.in_(filters.relations))
        if filters.type_slugs:
            side = side.where(far_type.in_(filters.type_slugs))
        far_sides.append(side)

    if ref_out:
        ref = aliased(ObjectRef)
        obj = aliased(ObjectInstance)
        side = (
            select(
                ref.dst_id.label("id"), obj.type_id.label("type_id"), no_type.label("sys_type")
            )
            .select_from(ref)
            .join(obj, obj.id == ref.dst_id)
            .where(
                ref.src_id == here.c.id,
                # 칸 키를 따로 건다 — 색인(`src_id, key, …`)의 둘째 칸으로 좁힌다.
                ref.key.in_(sorted({key for _type, key in ref_out})),
                _pair_filter(ref, ref_out),
                obj.deleted_at.is_(None),
                visible_owner_clause(user, obj.owner_workspace_id),
            )
        )
        if filters.type_ids:
            side = side.where(obj.type_id.in_(filters.type_ids))
        far_sides.append(side)

    if ref_in:
        ref = aliased(ObjectRef)
        side = select(
            ref.src_id.label("id"), ref.src_type_id.label("type_id"), no_type.label("sys_type")
        ).where(
            ref.dst_id == here.c.id,
            # 칸 키를 따로 건다 — 색인(`dst_id, key`)의 둘째 칸으로 좁힌다.
            ref.key.in_(sorted({key for _type, key in ref_in})),
            _pair_filter(ref, ref_in),
        )
        # 색인에는 살아 있는 출발점만 있다(`refindex`) — 모두를 보는 사람이고 타입으로 안
        # 거르면 객체 표를 안 거친다(인기 모델 하나를 기록 10만 건이 가리킨다).
        if filters.type_ids or not sees_every_department(user):
            obj = aliased(ObjectInstance)
            side = side.join(obj, obj.id == ref.src_id).where(
                visible_owner_clause(user, obj.owner_workspace_id)
            )
            if filters.type_ids:
                side = side.where(obj.type_id.in_(filters.type_ids))
        far_sides.append(side)

    out: list[Select[Any]] = []
    for side in far_sides:
        # `OFFSET 0` 은 펴기 막이다 — 없으면 계획기가 LATERAL 을 보통 조인으로 펴고 다시
        # 선 표를 처음부터 읽는다(같은 12초). 막지 않으면 보통 조인과 같다.
        far_end = (side.offset(literal_column("0")) if fence else side).lateral("far_end")
        out.append(
            select(
                far_end.c.id.label("id"),
                step.label("depth"),
                here.c.id.label("parent"),
                far_end.c.type_id.label("type_id"),
                far_end.c.sys_type.label("sys_type"),
            )
            .select_from(here)
            .join(far_end, true())
            .where(here.c.depth == k, ~exists().where(seen.c.id == far_end.c.id))
        )
    return out


def _pair_filter(ref: Any, pairs: dict[tuple[uuid.UUID, str], str]) -> ColumnElement[bool]:
    """(가리키는 타입, 칸) 짝 — **색인을 못 쓰는 식으로** 건다.

    `(src_type_id, key) IN (…)` 로 걸면 계획기가 그것을 「칸 종류 색인」(`src_type_id,
    key`)의 조건으로 읽고, 노드마다 그 칸 전부(기록 칸이면 10만 줄)를 훑어 `dst_id` 색인과
    교집합을 냈다 — 기본 모델 2천 개에서 6.5초(2,000만 건 DB). 짝은 이미 노드의 색인
    (`dst_id` · `src_id`)으로 고른 줄을 거르는 데만 쓴다."""
    return func.concat(cast(ref.src_type_id, String), ":", ref.key).in_(
        sorted(f"{type_id}:{key}" for type_id, key in pairs)
    )


def _grow_all(db: Session, stmt: Select[Any], total: int) -> tuple[int, bool]:
    """한 종류의 선으로 넓힌다 — (모은 수, 상한을 넘었나).

    남은 자리 + 1 줄까지만 읽는다. 읽은 줄이 그만큼이면 더 있을 수 있다 — 같은 노드가 두 길로
    닿아 겹친 줄이 있었으면 자리가 남으므로 다시 읽는다(이미 넣은 것은 `seen` 이 거른다)."""
    while True:
        budget = MAX_NODES + 1 - total
        fetched, inserted = _grow(db, stmt, budget)
        total += inserted
        if total > MAX_NODES:
            return total, True
        if fetched < budget:
            return total, False


def _grow(db: Session, stmt: Select[Any], budget: int) -> tuple[int, int]:
    """(읽은 줄, 넣은 노드) — 읽기와 넣기를 **한 문장**으로(읽은 줄을 두 번 계산하지
    않는다)."""
    found = stmt.limit(budget).cte("found")
    added = (
        pg_insert(NODES)
        .from_select(
            ["id", "depth", "parent", "type_id", "sys_type"],
            select(
                found.c.id, found.c.depth, found.c.parent, found.c.type_id, found.c.sys_type
            ),
        )
        .on_conflict_do_nothing(index_elements=[NODES.c.id])
        .returning(NODES.c.id)
        .cte("added")
    )
    fetched, inserted = db.execute(
        select(
            select(func.count()).select_from(found).scalar_subquery(),
            select(func.count()).select_from(added).scalar_subquery(),
        )
    ).one()
    return int(fetched), int(inserted)


def _count_types(
    db: Session, table: Table, type_slug_of: dict[uuid.UUID, str]
) -> dict[str, int]:
    """모은 노드의 타입별 수 — 큰 것부터. 넣을 때 적어 둔 타입으로 센다(객체 표를 안
    잇는다)."""
    rows = db.execute(
        select(table.c.sys_type, table.c.type_id, func.count()).group_by(
            table.c.sys_type, table.c.type_id
        )
    )
    out: Counter[str] = Counter()
    for sys_type, type_id, n in rows:
        slug = sys_type or (type_slug_of.get(type_id, "") if type_id else "")
        out[slug] += int(n)
    return dict(out.most_common())


def _over_nodes(
    db: Session, total: int, layers: list[int], type_slug_of: dict[uuid.UUID, str]
) -> Over:
    return Over(
        what="nodes",
        count=total,
        exact=False,
        by_type=_count_types(db, NODES, type_slug_of),
        layers=layers,
    )


# --- 다 모은 뒤 -------------------------------------------------------------------


def _number(db: Session) -> None:
    """노드에 번호를 매긴다 — 단계 순(씨앗이 앞). 선의 양 끝을 이 번호로 받는다."""
    db.execute(
        text("""
            CREATE TEMPORARY TABLE graph_whole_index ON COMMIT DROP AS
            SELECT id, (row_number() OVER (ORDER BY depth, id) - 1)::int AS idx,
                   depth, parent, type_id, sys_type
              FROM graph_whole_nodes
        """)
    )
    db.execute(text("ALTER TABLE graph_whole_index ADD PRIMARY KEY (id)"))
    db.execute(text("ANALYZE graph_whole_index"))


def _edges(
    db: Session,
    whole: Whole,
    filters: Filters,
    ref_pairs: dict[tuple[uuid.UUID, str], str],
) -> tuple[int, dict[str, int]] | None:
    """양 끝이 다 그림 안인 선 전부. 상한을 넘으면 (센 수, 종류별 수) — 그때 그림은 없다.

    **줄이 아니라 종류마다 배열 한 벌로 받는다**(`array_agg`). 62만 줄을 줄로 받으면 줄마다
    파이썬 객체(uuid 하나까지)를 만드느라 질의(0.5초)의 다섯 배가 들었다(ADR 0045 실측).
    두 집계는 같은 차례(출발 → 도착)로 세운다 — `src[k]` 와 `dst[k]` 가 한 선이고, 차례로 선
    수는 gzip 이 잘 줄인다(시장 서비스 전부 6.0 → 5.6MB)."""
    a = INDEX.alias("a")
    b = INDEX.alias("b")
    rel = ObjectRelation
    link = ObjectLink
    ref = ObjectRef
    relations = (
        select(rel.relation.label("kind"), a.c.idx.label("src"), b.c.idx.label("dst"))
        .select_from(rel)
        .join(a, a.c.id == rel.src_object_id)
        .join(b, b.c.id == rel.dst_object_id)
        .where(edge_rule.alive(rel, filters.at))
    )
    links = (
        select(link.relation.label("kind"), a.c.idx.label("src"), b.c.idx.label("dst"))
        .select_from(link)
        .join(a, a.c.id == link.src_id)
        .join(b, b.c.id == link.dst_id)
    )
    if filters.relations:
        relations = relations.where(rel.relation.in_(filters.relations))
        links = links.where(link.relation.in_(filters.relations))
    queries: list[Select[Any]] = [relations, links]
    if ref_pairs:
        # 칸의 slug 는 SQL 에서 정한다 — 줄마다 (타입 id, 칸 키)를 받아 풀지 않게.
        slug = case(
            *[
                (and_(ref.src_type_id == type_id, ref.key == key), literal(name))
                for (type_id, key), name in ref_pairs.items()
            ]
        )
        queries.append(
            select(slug.label("kind"), a.c.idx.label("src"), b.c.idx.label("dst"))
            .select_from(ref)
            .join(a, a.c.id == ref.src_id)
            .join(b, b.c.id == ref.dst_id)
            .where(tuple_(ref.src_type_id, ref.key).in_(list(ref_pairs)))
        )
    src: list[int] = []
    dst: list[int] = []
    kinds: list[str] = []
    counted: Counter[str] = Counter()
    for stmt in queries:
        taken = stmt.limit(MAX_EDGES + 1 - len(src)).subquery()
        for kind, srcs, dsts in db.execute(
            select(
                taken.c.kind,
                func.array_agg(aggregate_order_by(taken.c.src, taken.c.src, taken.c.dst)),
                func.array_agg(aggregate_order_by(taken.c.dst, taken.c.src, taken.c.dst)),
            ).group_by(taken.c.kind)
        ):
            src.extend(srcs)
            dst.extend(dsts)
            kinds.extend([kind] * len(srcs))
            counted[kind] += len(srcs)
        if len(src) > MAX_EDGES:
            return len(src), dict(counted.most_common())
    whole.src, whole.dst, whole.rel = src, dst, kinds
    return None


def _nodes(db: Session, whole: Whole) -> None:
    """노드의 열 — id · 타입 · 이름 · 단계 · 데려온 노드. 열마다 배열 하나로(선과 같은 까닭).
    원 표 행의 이름은 원 표에서."""
    g = INDEX.alias("g")
    p = INDEX.alias("p")
    obj = aliased(ObjectInstance)
    kind = aliased(ObjectType)

    def column(value: Any) -> Any:
        return func.array_agg(aggregate_order_by(value, g.c.idx))

    ids, depth, parent, types, labels, sys_types = db.execute(
        select(
            column(cast(g.c.id, String)),
            column(g.c.depth),
            column(func.coalesce(p.c.idx, -1)),
            column(func.coalesce(g.c.sys_type, kind.slug, "")),
            column(func.coalesce(obj.label, "")),
            column(g.c.sys_type),
        )
        .select_from(g)
        .outerjoin(p, p.c.id == g.c.parent)
        .outerjoin(obj, obj.id == g.c.id)
        .outerjoin(kind, kind.id == g.c.type_id)
    ).one()
    whole.ids, whole.depth, whole.parent = (
        list(ids or []),
        list(depth or []),
        list(parent or []),
    )
    whole.types, whole.labels = list(types or []), list(labels or [])
    system_at: dict[str, list[int]] = {}
    for at, sys_type in enumerate(sys_types or []):
        if sys_type:
            system_at.setdefault(sys_type, []).append(at)
    if system_at:
        _system_labels(db, whole, system_at)


def _system_labels(db: Session, whole: Whole, system_at: dict[str, list[int]]) -> None:
    """원 표 행의 이름 — 타입마다 한 번에 찾는다. 원 표에서 사라진 행은 「알 수 없음」 으로
    둔다(번호가 선의 끝이라 빼면 선이 어긋난다)."""
    by_slug = {row.slug: row for row in db.scalars(select(ObjectType))}
    for slug, places in system_at.items():
        object_type = by_slug.get(slug)
        source = system.source_or_none(object_type) if object_type is not None else None
        found = (
            source.lookup(db, [uuid.UUID(whole.ids[at]) for at in places]) if source else {}
        )
        for at in places:
            ref = found.get(uuid.UUID(whole.ids[at]))
            whole.labels[at] = ref.label if ref is not None else "알 수 없음"
