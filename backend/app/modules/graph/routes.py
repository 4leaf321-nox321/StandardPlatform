"""그래프 라우터 — **큰 데이터를 통째로 안 준다.**

객체가 만 개, 관계가 십만 개인 설치에서 「전부 그려 달라」 는 요청은 서버도 브라우저도
못 받는다. 그래서 길이 셋이다:

    overview       타입과 관계 종류만 — 객체가 백만 개여도 (타입 수) 노드
    search         이름으로 시작점 찾기
    neighborhood   시작점에서 depth 단계 — fanout · node_limit 상한을 서버가 강제
    subgraph       한 타입의 인스턴스 전부 — 쪽 단위, 상한 안에서. 「전부」 를 묻는
                   사람에게 "N개 중 M개" 라고 답하는 길

「전부」 는 없다. 전부를 보려는 사람은 overview 로 모양을 보고, 궁금한 곳에서
neighborhood 로 들어간다. 그림 하나가 모든 것을 담으려 하면 아무것도 안 보인다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.graph.schemas import (
    EdgeOut,
    NeighborhoodOut,
    NodeOut,
    OverviewOut,
    SearchHitOut,
    SubgraphOut,
    TypeEdgeOut,
    TypeNodeOut,
)
from app.modules.objects import graph, system
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import NavGroup, ObjectType, RelationType
from app.modules.workspaces.models import Workspace
from app.shared import system_sources
from app.shared.auth import current_user
from app.shared.errors import NotFound, code
from app.shared.permissions import visible_owner_clause
from app.shared.system_sources import SystemRef

router = APIRouter(prefix="/graph", tags=["graph"])

#: 상한 — **클라이언트가 보낸 값을 그대로 믿지 않는다.** 화면이 「더 보기」 를
#: 구현하면서 큰 수를 넣는 날 서버가 죽는 것은 악의가 없어도 일어난다.
MAX_DEPTH = 3
DEFAULT_DEPTH = 1
MAX_FANOUT = 100
DEFAULT_FANOUT = 30
MAX_NODES = 500
DEFAULT_NODES = 200
#: 노드 500개 사이의 선은 이론상 12만 개다. 그만큼은 아무도 못 읽으니 여기서 끊는다.
MAX_EDGES = 3000
SEARCH_LIMIT = 20


def _clamp(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(value, maximum))


def _workspace_slugs(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.slug for row in db.scalars(select(Workspace))}


def _edge_out(edge: graph.Edge, kinds: dict[str, RelationType]) -> EdgeOut:
    kind = kinds.get(edge.relation)
    return EdgeOut(
        id=edge.id,
        relation=edge.relation,
        label=kind.label if kind else edge.relation,
        inverse_label=(kind.inverse_label or "") if kind else "",
        directed=kind.directed if kind else True,
        src=edge.src,
        dst=edge.dst,
    )


def _csv(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [one.strip() for one in raw.split(",") if one.strip()]
    return items or None


def _system_types(types: dict[uuid.UUID, ObjectType]) -> dict[str, ObjectType]:
    """원 표를 비추는 타입들 — slug 로. 그래프에서 이 타입의 노드는 `objects` 가 아니라
    원 표에서 온다.

    **원 표가 등록 안 된 타입은 뺀다.** 그 타입의 목록은 무엇이 빠졌는지 말하며 409 를
    내지만, 그래프는 여러 타입을 한 그림에 담으므로 하나 때문에 전체가 안 뜨면 안 된다 —
    그 타입만 그림에서 빠지고, 이유는 그 타입의 목록에서 드러난다."""
    return {
        row.slug: row
        for row in types.values()
        if system.is_system(row) and system_sources.system_source(row.system_source)
    }


def _slug_of(edges: list[graph.Edge], extra: dict[uuid.UUID, str]) -> dict[uuid.UUID, str]:
    """원 표 행의 타입 — 링크의 끝 타입에서. 같은 표를 비추는 타입이 둘일 수 있어서
    id 만으로는 못 정한다."""
    out = dict(extra)
    for edge in edges:
        if edge.src_type:
            out.setdefault(edge.src, edge.src_type)
        if edge.dst_type:
            out.setdefault(edge.dst, edge.dst_type)
    return out


def _system_nodes(
    db: Session,
    ids: list[uuid.UUID],
    systems: dict[str, ObjectType],
    degrees: dict[uuid.UUID, int],
    shown: dict[uuid.UUID, int],
    slug_of: dict[uuid.UUID, str],
) -> dict[uuid.UUID, NodeOut]:
    """ids 중 원 표의 행인 것을 노드로. 타입은 `slug_of`(링크의 끝 타입)가 말한다."""
    out: dict[uuid.UUID, NodeOut] = {}
    by_type: dict[str, list[uuid.UUID]] = {}
    for one in ids:
        slug = slug_of.get(one)
        if slug in systems:
            by_type.setdefault(slug, []).append(one)
    for slug, wanted in by_type.items():
        object_type = systems[slug]
        found = system.source_of(object_type).lookup(db, wanted)
        for ref in found.values():
            degree = degrees.get(ref.id, 0)
            out[ref.id] = NodeOut(
                id=ref.id,
                label=ref.label,
                key=ref.key,
                type_slug=object_type.slug,
                type_label=object_type.label,
                status="active" if ref.active else "deprecated",
                owner_workspace_slug=None,
                degree=degree,
                truncated=degree > shown.get(ref.id, 0),
            )
    return out


def _degrees(
    db: Session, ids: list[uuid.UUID], user: User, systems: dict[str, ObjectType]
) -> dict[uuid.UUID, int]:
    """관계와 링크를 합친 차수 — 「+N 더」 의 근거."""
    out = graph.degree_counts(db, ids=ids, user=user)
    for node_id, n in graph.link_degree_counts(
        db, ids=ids, user=user, systems=list(systems)
    ).items():
        out[node_id] = out.get(node_id, 0) + n
    return out


@router.get("/overview", response_model=OverviewOut)
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)) -> OverviewOut:
    """정의 그래프 — 타입이 노드, 관계 종류가 선.

    **첫 화면이 이것이다.** 객체를 하나도 안 그리고 전체 모양을 보여 준다. 선의
    굵기는 실제로 걸린 관계 수라서, 정의만 있고 비어 있는 관계는 점선으로 드러난다.
    """
    types = list(db.scalars(select(ObjectType).where(ObjectType.is_active.is_(True))))
    groups = {row.id: row.slug for row in db.scalars(select(NavGroup))}
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    counts = graph.object_counts_by_type(db, user=user)
    by_id = {row.id: row for row in types}
    by_slug = {row.slug: row for row in types}
    systems = _system_types(by_id)
    # 원 표를 비추는 타입은 행이 없다 — 노드 크기는 원 표의 행 수다.
    for projected in systems.values():
        counts[projected.id] = len(system.source_of(projected).list_all(db))

    nodes = [
        TypeNodeOut(
            slug=row.slug,
            label=row.label,
            icon=row.icon,
            group_slug=groups.get(row.nav_group_id) if row.nav_group_id else None,
            count=counts.get(row.id, 0),
        )
        for row in sorted(types, key=lambda one: (one.sort_order, one.label))
    ]

    # 실제로 걸린 것이 먼저다. 정의에 적힌 (출발 타입 x 도착 타입) 쌍 중 아직 하나도
    # 안 이어진 것은 뒤에 0 으로 더한다 — **정의만 있고 비어 있는 관계**도 그림에
    # 있어야 「왜 이 선이 없지」 를 묻지 않는다.
    seen: set[tuple[str, str, str]] = set()
    edges: list[TypeEdgeOut] = []
    for found in graph.type_edge_counts(db, user=user):
        src = by_id.get(found.src_type_id)
        dst = by_id.get(found.dst_type_id)
        if src is None or dst is None:
            continue
        kind = kinds.get(found.relation)
        seen.add((found.relation, src.slug, dst.slug))
        edges.append(
            TypeEdgeOut(
                relation=found.relation,
                label=kind.label if kind else found.relation,
                directed=kind.directed if kind else True,
                src_type=src.slug,
                dst_type=dst.slug,
                count=found.count,
            )
        )
    # 원 표와 이은 선도 굵기에 든다 — 안 그러면 「사용 부서」 선이 정의만 있는 점선으로 보인다.
    for link in graph.type_link_counts(db, user=user, systems=list(systems)):
        if link.src_type_slug not in by_slug or link.dst_type_slug not in by_slug:
            continue
        kind = kinds.get(link.relation)
        seen.add((link.relation, link.src_type_slug, link.dst_type_slug))
        edges.append(
            TypeEdgeOut(
                relation=link.relation,
                label=kind.label if kind else link.relation,
                directed=kind.directed if kind else True,
                src_type=link.src_type_slug,
                dst_type=link.dst_type_slug,
                count=link.count,
            )
        )
    active_slugs = {row.slug for row in types}
    for kind in sorted(kinds.values(), key=lambda one: (one.sort_order, one.slug)):
        if not kind.is_active or not kind.src_type_slugs or not kind.dst_type_slugs:
            continue
        for src_slug in kind.src_type_slugs:
            for dst_slug in kind.dst_type_slugs:
                if src_slug not in active_slugs or dst_slug not in active_slugs:
                    continue
                if (kind.slug, src_slug, dst_slug) in seen:
                    continue
                seen.add((kind.slug, src_slug, dst_slug))
                edges.append(
                    TypeEdgeOut(
                        relation=kind.slug,
                        label=kind.label,
                        directed=kind.directed,
                        src_type=src_slug,
                        dst_type=dst_slug,
                        count=0,
                    )
                )

    return OverviewOut(
        nodes=nodes,
        edges=edges,
        object_count=sum(counts.values()),
        edge_count=sum(edge.count for edge in edges),
    )


@router.get("/search", response_model=list[SearchHitOut])
def search(
    q: str = Query(min_length=1, description="이름·식별자의 일부"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[SearchHitOut]:
    """타입을 가리지 않고 시작점을 찾는다.

    목록 API 는 타입마다 따로다 — 그래프에서 「어디서 시작하지」 를 물을 때는 타입을
    먼저 고르게 하면 안 된다. 무엇이 어느 타입인지 모르는 사람이 찾는 자리다.
    """
    needle = f"%{q.strip()}%"
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    rows = db.scalars(
        select(ObjectInstance)
        .where(
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            or_(ObjectInstance.label.ilike(needle), ObjectInstance.key.ilike(needle)),
        )
        .order_by(ObjectInstance.label)
        .limit(SEARCH_LIMIT)
    )
    out: list[SearchHitOut] = []
    for row in rows:
        object_type = types.get(row.type_id)
        out.append(
            SearchHitOut(
                id=row.id,
                label=row.label,
                key=row.key,
                type_slug=object_type.slug if object_type else "",
                type_label=object_type.label if object_type else "알 수 없음",
            )
        )
    # 원 표의 행(부서·계정)에서도 시작할 수 있어야 한다 — 「해석팀이 쓰는 툴」 은
    # 부서에서 출발한다.
    for object_type in _system_types(types).values():
        if not object_type.is_active:
            continue
        refs, _total = system.source_of(object_type).search(
            db, user, q.strip(), SEARCH_LIMIT, 0
        )
        out.extend(
            SearchHitOut(
                id=ref.id,
                label=ref.label,
                key=ref.key,
                type_slug=object_type.slug,
                type_label=object_type.label,
            )
            for ref in refs
        )
    return out[:SEARCH_LIMIT]


@router.get("/neighborhood", response_model=NeighborhoodOut)
def neighborhood(
    focus: uuid.UUID = Query(description="시작 객체"),
    depth: int | None = Query(default=None, description=f"몇 단계까지. 최대 {MAX_DEPTH}"),
    fanout: int | None = Query(
        default=None, description=f"노드 하나가 데려오는 이웃 수. 최대 {MAX_FANOUT}"
    ),
    limit: int | None = Query(default=None, description=f"노드 상한. 최대 {MAX_NODES}"),
    relations: str | None = Query(default=None, description="관계 slug, 쉼표로"),
    types: str | None = Query(default=None, description="이웃 타입 slug, 쉼표로"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NeighborhoodOut:
    """시작점에서 depth 단계까지의 이웃.

    **한 단계씩, 노드마다 fanout 개까지, 전체 node_limit 개까지.** 셋 다 서버가
    상한을 강제한다. 잘리면 `truncated` 와 노드의 `degree` 로 **잘렸다고 말한다** —
    화면은 그 노드에 「+N 더」 를 적고, 사람은 거기서 다시 펼친다.
    """
    depth_n = _clamp(depth, default=DEFAULT_DEPTH, maximum=MAX_DEPTH)
    fanout_n = _clamp(fanout, default=DEFAULT_FANOUT, maximum=MAX_FANOUT)
    node_limit = _clamp(limit, default=DEFAULT_NODES, maximum=MAX_NODES)
    wanted_relations = _csv(relations)

    all_types = {row.id: row for row in db.scalars(select(ObjectType))}
    systems = _system_types(all_types)
    start = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == focus,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    start_type: ObjectType | None = all_types.get(start.type_id) if start else None
    if start is None:
        # 원 표의 행(부서 등)에서도 출발한다.
        for candidate in systems.values():
            if system.find(db, candidate, focus) is not None:
                start_type = candidate
                break
    if start_type is None:
        # 없는 것과 안 보이는 것을 같은 말로 답한다 — objects 와 같은 규칙.
        raise NotFound(code("GRAPH", 1), "객체를 찾을 수 없습니다.")

    type_ids: list[uuid.UUID] | None = None
    type_slugs: list[str] | None = None
    if wanted := _csv(types):
        type_ids = [row.id for row in all_types.values() if row.slug in wanted]
        type_slugs = list(wanted)
        # 시작점의 타입은 거르기와 무관하게 늘 들어간다 — 안 그러면 「부품만」 을
        # 고른 순간 시작점인 공급사에서 아무것도 안 나온다.
        if start_type.id not in type_ids:
            type_ids.append(start_type.id)
            type_slugs.append(start_type.slug)

    seen: set[uuid.UUID] = {focus}
    order: list[uuid.UUID] = [focus]
    edges: dict[uuid.UUID, graph.Edge] = {}
    frontier = [focus]
    truncated = False

    for _ in range(depth_n):
        if not frontier:
            break
        frontier_set = set(frontier)
        found = graph.neighbor_edges(
            db,
            frontier=frontier,
            user=user,
            fanout=fanout_n,
            relations=wanted_relations,
            type_ids=type_ids,
        ) + graph.neighbor_link_edges(
            db,
            frontier=frontier,
            user=user,
            fanout=fanout_n,
            systems=list(systems),
            relations=wanted_relations,
            type_slugs=type_slugs,
        )
        next_frontier: list[uuid.UUID] = []
        for edge in found:
            other = edge.dst if edge.src in frontier_set else edge.src
            if other not in seen:
                if len(seen) >= node_limit:
                    truncated = True
                    continue
                seen.add(other)
                order.append(other)
                next_frontier.append(other)
            edges[edge.id] = edge
        frontier = next_frontier

    # 이미 실린 노드끼리의 선을 마저 긋는다 — fanout 에 밀린 관계도 양 끝이 화면에
    # 있으면 그려야 「관계없음」 으로 안 읽힌다.
    for edge in graph.induced_edges(
        db, ids=order, limit=MAX_EDGES, relations=wanted_relations
    ) + graph.induced_link_edges(db, ids=order, limit=MAX_EDGES, relations=wanted_relations):
        edges[edge.id] = edge
    if len(edges) > MAX_EDGES:
        truncated = True
        edges = dict(list(edges.items())[:MAX_EDGES])

    rows = {
        row.id: row
        for row in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(order)))
    }
    degrees = _degrees(db, order, user, systems)
    shown: dict[uuid.UUID, int] = {}
    for edge in edges.values():
        shown[edge.src] = shown.get(edge.src, 0) + 1
        if edge.dst != edge.src:
            shown[edge.dst] = shown.get(edge.dst, 0) + 1
    system_nodes = _system_nodes(
        db,
        [one for one in order if one not in rows],
        systems,
        degrees,
        shown,
        _slug_of(list(edges.values()), {focus: start_type.slug} if start is None else {}),
    )

    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    workspaces = _workspace_slugs(db)
    nodes: list[NodeOut] = []
    for node_id in order:
        row = rows.get(node_id)
        if row is None:
            if node_id in system_nodes:
                node = system_nodes[node_id]
                truncated = truncated or node.truncated
                nodes.append(node)
            continue
        object_type = all_types.get(row.type_id)
        degree = degrees.get(node_id, 0)
        # 거르기(relations/types)로 안 실린 것도 「더 있음」 이다 — 사람이 거른 것을
        # 잊고 「이 노드는 이웃이 셋뿐」 으로 읽는 것을 막는다.
        node_truncated = degree > shown.get(node_id, 0)
        truncated = truncated or node_truncated
        nodes.append(
            NodeOut(
                id=row.id,
                label=row.label,
                key=row.key,
                type_slug=object_type.slug if object_type else "",
                type_label=object_type.label if object_type else "알 수 없음",
                status=row.status,
                owner_workspace_slug=(
                    workspaces.get(row.owner_workspace_id) if row.owner_workspace_id else None
                ),
                degree=degree,
                truncated=node_truncated,
            )
        )

    return NeighborhoodOut(
        focus=focus,
        nodes=nodes,
        edges=[_edge_out(edge, kinds) for edge in edges.values()],
        depth=depth_n,
        fanout=fanout_n,
        node_limit=node_limit,
        truncated=truncated,
    )


@router.get("/subgraph", response_model=SubgraphOut)
def subgraph(
    types: str = Query(description="타입 slug, 쉼표로"),
    relations: str | None = Query(default=None, description="관계 slug, 쉼표로"),
    q: str | None = Query(default=None, description="이름·식별자의 일부"),
    limit: int | None = Query(default=None, description=f"노드 상한. 최대 {MAX_NODES}"),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SubgraphOut:
    """한 타입(들)의 인스턴스를 **쪽 단위로** 전부, 그 사이의 관계와 함께.

    「이 타입에 뭐가 있나」 를 그림으로 답하는 길이다. 상한(limit)을 넘으면 `total`
    과 함께 잘렸다고 말한다 — 화면은 「1,234개 중 500개」 라고 적고 다음 쪽으로 넘긴다.
    다른 타입으로 나가는 선은 여기 없다(노드의 `degree` 로 「+N」 만 붙는다) — 그것은
    노드를 눌러 펼친다.
    """
    node_limit = _clamp(limit, default=DEFAULT_NODES, maximum=MAX_NODES)
    wanted_relations = _csv(relations)
    all_types = {row.id: row for row in db.scalars(select(ObjectType))}
    systems = _system_types(all_types)
    wanted = _csv(types) or []
    wanted_systems = [row for slug, row in systems.items() if slug in wanted]
    type_ids = [
        row.id
        for row in all_types.values()
        if row.slug in wanted and not system.is_system(row)
    ]
    if not type_ids and not wanted_systems:
        raise NotFound(code("GRAPH", 2), f"타입을 찾을 수 없습니다: {types}")
    if not type_ids:
        # 원 표만 골랐다 — 행 없이 원 표에서 쪽을 뜬다.
        return _system_subgraph(
            db,
            user,
            wanted_systems,
            systems,
            q=q,
            limit=node_limit,
            offset=offset,
            relations=wanted_relations,
        )

    conditions = [
        ObjectInstance.type_id.in_(type_ids),
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    ]
    if q and q.strip():
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(ObjectInstance.label.ilike(needle), ObjectInstance.key.ilike(needle))
        )
    total = int(
        db.scalar(select(func.count()).select_from(ObjectInstance).where(*conditions)) or 0
    )
    rows = list(
        db.scalars(
            select(ObjectInstance)
            .where(*conditions)
            .order_by(ObjectInstance.label, ObjectInstance.id)
            .offset(offset)
            .limit(node_limit)
        )
    )
    ids = [row.id for row in rows]
    # 함께 고른 원 표 타입의 행은 뒤에 붙는다 — 쪽은 객체 쪽 기준이다. **이 쪽의 객체와
    # 이어진 행이 먼저** — 「툴 + 부서」 를 골랐으면 그 툴을 쓰는 부서가 상한 안에 들어야
    # 선이 보인다. 이어지지 않은 행은 남는 자리에.
    linked = graph.link_partner_ids(db, ids=ids) if ids and wanted_systems else set()
    candidates = [
        (object_type, ref)
        for object_type in wanted_systems
        for ref in system.source_of(object_type).list_all(db)
        if not q or q.strip().lower() in f"{ref.label} {ref.key}".lower()
    ]
    candidates.sort(key=lambda pair: pair[1].id not in linked)
    extra_refs = candidates[: max(0, node_limit - len(ids))]
    ids += [ref.id for _t, ref in extra_refs]
    edges = graph.induced_edges(
        db, ids=ids, limit=MAX_EDGES, relations=wanted_relations
    ) + graph.induced_link_edges(db, ids=ids, limit=MAX_EDGES, relations=wanted_relations)
    degrees = _degrees(db, ids, user, systems)
    shown: dict[uuid.UUID, int] = {}
    for edge in edges:
        shown[edge.src] = shown.get(edge.src, 0) + 1
        if edge.dst != edge.src:
            shown[edge.dst] = shown.get(edge.dst, 0) + 1

    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    workspaces = _workspace_slugs(db)
    nodes: list[NodeOut] = []
    for system_type, ref in extra_refs:
        degree = degrees.get(ref.id, 0)
        nodes.append(
            NodeOut(
                id=ref.id,
                label=ref.label,
                key=ref.key,
                type_slug=system_type.slug,
                type_label=system_type.label,
                status="active" if ref.active else "deprecated",
                owner_workspace_slug=None,
                degree=degree,
                truncated=degree > shown.get(ref.id, 0),
            )
        )
    for row in rows:
        object_type = all_types.get(row.type_id)
        degree = degrees.get(row.id, 0)
        nodes.append(
            NodeOut(
                id=row.id,
                label=row.label,
                key=row.key,
                type_slug=object_type.slug if object_type else "",
                type_label=object_type.label if object_type else "알 수 없음",
                status=row.status,
                owner_workspace_slug=(
                    workspaces.get(row.owner_workspace_id) if row.owner_workspace_id else None
                ),
                degree=degree,
                truncated=degree > shown.get(row.id, 0),
            )
        )
    return SubgraphOut(
        nodes=nodes,
        edges=[_edge_out(edge, kinds) for edge in edges],
        total=total + len(extra_refs),
        limit=node_limit,
        offset=offset,
        truncated=offset + len(rows) < total or len(edges) >= MAX_EDGES,
    )


def _system_subgraph(
    db: Session,
    user: User,
    wanted: list[ObjectType],
    systems: dict[str, ObjectType],
    *,
    q: str | None,
    limit: int,
    offset: int,
    relations: list[str] | None,
) -> SubgraphOut:
    """원 표 타입만 고른 그림 — 행은 원 표에서, 선은 링크에서."""
    refs: list[tuple[ObjectType, SystemRef]] = []
    total = 0
    for object_type in wanted:
        found, n = system.source_of(object_type).search(db, user, q, limit, offset)
        total += n
        refs.extend((object_type, ref) for ref in found)
    refs = refs[:limit]
    ids = [ref.id for _t, ref in refs]
    edges = graph.induced_link_edges(db, ids=ids, limit=MAX_EDGES, relations=relations)
    degrees = _degrees(db, ids, user, systems)
    shown: dict[uuid.UUID, int] = {}
    for edge in edges:
        shown[edge.src] = shown.get(edge.src, 0) + 1
        if edge.dst != edge.src:
            shown[edge.dst] = shown.get(edge.dst, 0) + 1
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    nodes = [
        NodeOut(
            id=ref.id,
            label=ref.label,
            key=ref.key,
            type_slug=object_type.slug,
            type_label=object_type.label,
            status="active" if ref.active else "deprecated",
            owner_workspace_slug=None,
            degree=degrees.get(ref.id, 0),
            truncated=degrees.get(ref.id, 0) > shown.get(ref.id, 0),
        )
        for object_type, ref in refs
    ]
    return SubgraphOut(
        nodes=nodes,
        edges=[_edge_out(edge, kinds) for edge in edges],
        total=total,
        limit=limit,
        offset=offset,
        truncated=offset + len(refs) < total or len(edges) >= MAX_EDGES,
    )
