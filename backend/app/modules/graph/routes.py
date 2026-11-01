"""그래프 라우터 — **큰 데이터를 통째로 안 준다.**

객체가 만 개, 관계가 십만 개인 설치에서 「전부 그려 달라」 는 요청은 서버도 브라우저도
못 받는다. 그래서 길이 셋이다:

    overview       타입과 관계 종류만 — 객체가 백만 개여도 (타입 수) 노드
    search         이름으로 시작점 찾기
    neighborhood   시작점에서 depth 단계 — fanout · node_limit 상한을 서버가 강제
    subgraph       한 타입의 인스턴스 전부 — 쪽 단위, 상한 안에서. 「전부」 를 묻는
                   사람에게 "N개 중 M개" 라고 답하는 길
    whole          정적 그리기(ADR 0045) — 씨앗 안의 노드와 선을 **자르지 않고 통째로**.
                   상한(노드 20만 · 선 70만)을 넘으면 반쯤 주지 않고 센 수와 함께 거절한다
    map            전체 지도(ADR 0046) — 서버가 미리 놓아 둔 자리. 멀리서는 원반 · 밀도(기록을
                   안 읽는다), 확대하면 상자 안의 점(5만까지, 넘으면 기록을 안 읽고 거절)

「전부」 는 없다 — **예외가 whole 과 map 이다.** 전부를 보려는 사람은 overview 로 모양을
보고, 궁금한 곳에서 neighborhood 로 들어간다. 그림 하나가 모든 것을 담으려 하면 아무것도
안 보인다. whole 이 그 예외를 감당하는 것은 상한의 크기가 아니라 정직함이다: 넘으면
자르지 않고 수를 말한다.
"""

from __future__ import annotations

import gzip
import uuid
from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.graph import mapbuild, maphubs, maplines, mapread
from app.modules.graph.models import MapLayout
from app.modules.graph.schemas import (
    EdgeOut,
    MapHubCandidateOut,
    MapHubKeyIn,
    MapHubKeyOut,
    MapLinkOut,
    MapLinksOut,
    MapLocateOut,
    MapOut,
    MapPointLinesOut,
    MapPointsOut,
    MapTypeOut,
    NeighborhoodOut,
    NodeOut,
    OverviewOut,
    PathOut,
    PathStepsOut,
    SearchHitOut,
    SubgraphOut,
    TypeEdgeOut,
    TypeNodeOut,
    WholeEdgesOut,
    WholeNodesOut,
    WholeOut,
    WholeOverOut,
    WholeRelationOut,
    WholeTypeOut,
)
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs.schemas import JobOut
from app.modules.objects import edges as edge_rule
from app.modules.objects import graph, graphcounts, graphwhole, refedges, system
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.objects.schemas import LogCountOut
from app.modules.ontology import interfaces
from app.modules.ontology.models import NavGroup, ObjectType, RelationType
from app.modules.workspaces.models import Workspace
from app.shared import querycap, system_sources
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, NotFound, code
from app.shared.permissions import sees_every_department, visible_owner_clause
from app.shared.system_sources import SystemRef

router = APIRouter(prefix="/graph", tags=["graph"])

#: 상한 — **클라이언트가 보낸 값을 그대로 믿지 않는다.** 화면이 「더 보기」 를
#: 구현하면서 큰 수를 넣는 날 서버가 죽는 것은 악의가 없어도 일어난다.
#:
#: **기본값은 낮게, 상한은 넉넉히.** 처음 뜨는 그림은 읽히는 크기여야 하고(그래서
#: 기본이 작다), 그 다음에 「더」 를 누르는 사람은 자기가 무엇을 하는지 안다 — 거기서
#: 막으면 그 사람은 답을 못 얻는다. 부품 구성처럼 깊은 사슬은 세 단계로는 안 닿는다.
#:
#: 넓힌 것을 감당하는 것은 **상한이 아니라 정직함**이다: 잘린 노드에 「+N」 이 붙고,
#: 몇 개 중 몇 개를 보고 있는지가 그림 아래에 늘 적힌다.
MAX_DEPTH = 6
DEFAULT_DEPTH = 1
MAX_FANOUT = 500
DEFAULT_FANOUT = 30
#: 「고른 타입 전부」 를 한 그림에 두려는 사람이 있다(기준정보 계층 1만여 개). 화면이
#: 느려지는 것은 화면이 미리 말하고, 여기서는 브라우저가 서지 않는 선에서 끊는다.
MAX_NODES = 20000
DEFAULT_NODES = 300
#: 노드 2만 개 사이의 선은 이론상 2억 개다. 그만큼은 아무도 못 읽고 브라우저가
#: 먼저 멈춘다 — 여기서 끊고, 끊었다는 말은 화면이 한다.
MAX_EDGES = 60000
SEARCH_LIMIT = 20

#: 기준일 칸의 설명 — 이웃 · 길 · 부분 그림이 같은 말로(ADR 0039).
AS_OF = (
    "기준일(YYYY-MM-DD) — 그날 유효한 관계만 그린다(지난 관계 · 예정 관계는 빠진다). "
    "비우면 오늘. 참조 칸 · 원 표와 이은 관계는 기간이 없어 늘 그린다."
)


def _clamp(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(value, maximum))


def _workspace_slugs(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.slug for row in db.scalars(select(Workspace))}


Kind = RelationType | refedges.RefKind


def _kinds(db: Session) -> dict[str, Kind]:
    """관계 종류와 참조 칸을 **한 목록으로** — 선의 라벨은 둘 다 여기서 읽는다."""
    out: dict[str, Kind] = {row.slug: row for row in db.scalars(select(RelationType))}
    out.update(refedges.kinds(db))
    return out


def _edge_out(edge: graph.Edge | refedges.RefEdge, kinds: dict[str, Kind]) -> EdgeOut:
    kind = kinds.get(edge.relation)
    return EdgeOut(
        id=edge.id,
        relation=edge.relation,
        label=kind.label if kind else edge.relation,
        inverse_label=(kind.inverse_label or "") if kind else "",
        directed=kind.directed if kind else True,
        src=edge.src,
        dst=edge.dst,
        valid_from=getattr(edge, "valid_from", None),
        valid_to=getattr(edge, "valid_to", None),
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
    db: Session,
    ids: list[uuid.UUID],
    user: User,
    systems: dict[str, ObjectType],
    *,
    at: date | None,
    skip_logs: bool = False,
) -> dict[uuid.UUID, int]:
    """관계와 링크를 합친 차수 — 「+N 더」 의 근거. 이웃에서 기록을 뺐으면 기록도 안 센다.
    관계는 그림과 같은 기준일의 것만 센다 — 안 그리는 지난 선을 「+N」 으로 세면 눌러도
    없다."""
    out = graph.degree_counts(db, ids=ids, user=user, at=at)
    for node_id, n in graph.link_degree_counts(
        db, ids=ids, user=user, systems=list(systems)
    ).items():
        out[node_id] = out.get(node_id, 0) + n
    for node_id, n in refedges.degree_counts(
        db, ids=ids, user=user, skip_logs=skip_logs
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
    kinds = _kinds(db)
    counts = graph.object_counts_by_type(db, user=user)
    # **선 굵기는 찍어 둔 값**(ADR 0038) — 열 때마다 관계 · 참조 색인 · 링크 전부를 세면 큰
    # 설치에서 분 단위(운영 4분)다. 없거나 낡았으면 다시 세는 작업을 넣고, 그동안은 있는 값을
    # 그린다. 부서로 조회를 좁힌 설치는 사람마다 보이는 수가 달라 지금처럼 센다.
    snapshot: graphcounts.Snapshot | None = None
    shared = sees_every_department(user)
    if shared:
        snapshot = graphcounts.read(db)
        if graphcounts.stale(snapshot):
            graphcounts.request(db)
            db.commit()
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
    relation_counts = (
        snapshot.relations
        if snapshot is not None
        else ([] if shared else graph.type_edge_counts(db, user=user, at=edge_rule.today()))
    )
    for found in relation_counts:
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
    # 참조 칸도 선이다 — 「개발모델의 과제」 는 칸에 저장한 관계라, 여기 안 그리면 계층이
    # 안 보인다.
    ref_counted = snapshot.refs if snapshot is not None else ({} if shared else None)
    for ref in refedges.type_edge_counts(db, user=user, counted=ref_counted):
        src = by_id.get(ref.src_type_id)
        dst = by_id.get(ref.dst_type_id)
        kind = kinds.get(ref.relation)
        if src is None or dst is None or kind is None:
            continue
        seen.add((ref.relation, src.slug, dst.slug))
        edges.append(
            TypeEdgeOut(
                relation=ref.relation,
                label=kind.label,
                directed=True,
                src_type=src.slug,
                dst_type=dst.slug,
                count=ref.count,
            )
        )
    # 원 표와 이은 선도 굵기에 든다 — 안 그러면 「사용 부서」 선이 정의만 있는 점선으로 보인다.
    link_counts = (
        snapshot.links
        if snapshot is not None
        else ([] if shared else graph.type_link_counts(db, user=user, systems=list(systems)))
    )
    for link in link_counts:
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
    # 끝에 인터페이스가 적힌 관계는 **구현 타입마다** 선을 긋는다 — 그림에는 타입만 선다.
    ends = interfaces.load_ends(db)
    for kind in sorted(
        (one for one in kinds.values() if isinstance(one, RelationType)),
        key=lambda one: (one.sort_order, one.slug),
    ):
        if not kind.is_active or not kind.src_type_slugs or not kind.dst_type_slugs:
            continue
        for src_slug in ends.types_of(kind.src_type_slugs):
            for dst_slug in ends.types_of(kind.dst_type_slugs):
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
        counted_at=snapshot.counted_at if snapshot is not None else None,
        counting=shared and snapshot is None,
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
            or_(
                ObjectInstance.label.ilike(needle),
                ObjectInstance.key.ilike(needle),
                ObjectInstance.id.in_(
                    select(ObjectAlias.object_id).where(ObjectAlias.value.ilike(needle))
                ),
            ),
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
    types: str | None = Query(
        default=None, description="이웃 타입 slug, 쉼표로. 인터페이스면 그 구현 타입 전부"
    ),
    records: bool = Query(
        default=False,
        description="나를 가리키는 기록(ADR 0011)도 이웃으로 싣는다. 기본은 수로만",
    ),
    as_of: date | None = Query(default=None, description=AS_OF),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NeighborhoodOut:
    """시작점에서 depth 단계까지의 이웃.

    **한 단계씩, 노드마다 fanout 개까지, 전체 node_limit 개까지.** 셋 다 서버가
    상한을 강제한다. 잘리면 `truncated` 와 노드의 `degree` 로 **잘렸다고 말한다** —
    화면은 그 노드에 「+N 더」 를 적고, 사람은 거기서 다시 펼친다.

    **나를 가리키는 기록은 기본으로 안 싣는다**(ADR 0011) — 인기 모델은 기록 10만 건이
    가리키고, 그중 30개를 늘어놓으면 축의 모양이 묻힌다. 시작점을 가리키는 기록은
    `log_counts` 에 수로 온다. `records=true` 거나 `types` 로 고르면 싣는다.
    """
    depth_n = _clamp(depth, default=DEFAULT_DEPTH, maximum=MAX_DEPTH)
    fanout_n = _clamp(fanout, default=DEFAULT_FANOUT, maximum=MAX_FANOUT)
    day = edge_rule.at(as_of)
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
    if asked_types := _csv(types):
        wanted = interfaces.load_ends(db).types_of(asked_types)
        type_ids = [row.id for row in all_types.values() if row.slug in wanted]
        type_slugs = list(wanted)
        # 시작점의 타입은 거르기와 무관하게 늘 들어간다 — 안 그러면 「부품만」 을
        # 고른 순간 시작점인 공급사에서 아무것도 안 나온다.
        if start_type.id not in type_ids:
            type_ids.append(start_type.id)
            type_slugs.append(start_type.slug)

    skip_logs = not records and type_ids is None
    seen: set[uuid.UUID] = {focus}
    order: list[uuid.UUID] = [focus]
    edges: dict[uuid.UUID, graph.Edge | refedges.RefEdge] = {}
    frontier = [focus]
    truncated = False

    for _ in range(depth_n):
        if not frontier:
            break
        frontier_set = set(frontier)
        found = (
            graph.neighbor_edges(
                db,
                frontier=frontier,
                user=user,
                fanout=fanout_n,
                at=day,
                relations=wanted_relations,
                type_ids=type_ids,
            )
            + graph.neighbor_link_edges(
                db,
                frontier=frontier,
                user=user,
                fanout=fanout_n,
                systems=list(systems),
                relations=wanted_relations,
                type_slugs=type_slugs,
            )
            + refedges.neighbor_edges(
                db,
                frontier=frontier,
                user=user,
                fanout=fanout_n,
                relations=wanted_relations,
                type_ids=type_ids,
                skip_logs=skip_logs,
            )
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
    for edge in (
        graph.induced_edges(db, ids=order, limit=MAX_EDGES, at=day, relations=wanted_relations)
        + graph.induced_link_edges(db, ids=order, limit=MAX_EDGES, relations=wanted_relations)
        + refedges.induced_edges(db, ids=order, limit=MAX_EDGES, relations=wanted_relations)
    ):
        edges[edge.id] = edge
    if len(edges) > MAX_EDGES:
        truncated = True
        edges = dict(list(edges.items())[:MAX_EDGES])

    rows = {
        row.id: row
        for row in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(order)))
    }
    degrees = _degrees(db, order, user, systems, at=day, skip_logs=skip_logs)
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
        _slug_of(
            [e for e in edges.values() if isinstance(e, graph.Edge)],
            {focus: start_type.slug} if start is None else {},
        ),
    )

    kinds = _kinds(db)
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
        as_of=day,
        log_counts=[
            LogCountOut(
                type_slug=one.kind.src_type.slug,
                type_label=one.kind.src_type.label,
                key=one.kind.key,
                label=one.kind.label,
                inverse_label=one.kind.inverse_label,
                count=one.count,
            )
            for one in (
                refedges.log_counts(db, target=focus, user=user)
                if skip_logs and start is not None
                else []
            )
        ],
    )


PATH_DEFAULT_DEPTH = 6
PATH_DEFAULT_LIMIT = 3
PATH_MAX_LIMIT = 10


@router.get("/path", response_model=PathOut)
def path(
    source: uuid.UUID = Query(description="출발 객체"),
    target: uuid.UUID = Query(description="도착 객체"),
    max_depth: int | None = Query(
        default=None,
        description=(
            f"길의 최대 길이(선 수). 기본 {PATH_DEFAULT_DEPTH}, 최대 {graph.PATH_MAX_DEPTH}"
        ),
    ),
    relations: str | None = Query(default=None, description="따라갈 관계 slug, 쉼표로"),
    types: str | None = Query(
        default=None,
        description=(
            "거쳐 갈 타입 slug, 쉼표로. 인터페이스면 구현 타입 전부. 출발 · 도착은 늘 든다"
        ),
    ),
    records: bool = Query(
        default=False, description="기록(ADR 0011)을 거쳐 가는 길도 찾는다. 기본은 안 거친다"
    ),
    limit: int | None = Query(
        default=None,
        description=(
            f"같은 길이의 길 몇 개까지. 기본 {PATH_DEFAULT_LIMIT}, 최대 {PATH_MAX_LIMIT}"
        ),
    ),
    as_of: date | None = Query(default=None, description=AS_OF),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PathOut:
    """두 객체 사이의 **가장 짧은 길들** — 「A 와 B 는 어떻게 이어져 있나」.

    관계와 참조 칸을 **방향과 상관없이** 따라간다(거슬러 가는 길도 길이다 — 선의 방향은 답에
    실린다). 이웃 그림과 달리 노드마다 30개로 자르지 않는다 — 잘린 곳의 길을 「없다」 로 답하지
    않으려고. 대신 들른 노드 수 · 노드당 선 수에 상한이 있고, 닿으면 `truncated` 다 — 그때
    못 찾은 것은 「없다」 가 아니다. 기록(ADR 0011)은 `records` 일 때만 거친다.
    """
    depth_n = _clamp(max_depth, default=PATH_DEFAULT_DEPTH, maximum=graph.PATH_MAX_DEPTH)
    limit_n = _clamp(limit, default=PATH_DEFAULT_LIMIT, maximum=PATH_MAX_LIMIT)
    day = edge_rule.at(as_of)
    ends = {
        row.id: row
        for row in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_([source, target]),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    }
    if source not in ends or target not in ends:
        # 없는 것과 안 보이는 것을 같은 말로 답한다 — objects 와 같은 규칙.
        raise NotFound(code("GRAPH", 1), "객체를 찾을 수 없습니다.")

    all_types = {row.id: row for row in db.scalars(select(ObjectType))}
    type_ids: list[uuid.UUID] | None = None
    if asked_types := _csv(types):
        wanted = interfaces.load_ends(db).types_of(asked_types)
        type_ids = [row.id for row in all_types.values() if row.slug in wanted]
        for end_row in ends.values():
            if end_row.type_id not in type_ids:
                type_ids.append(end_row.type_id)

    querycap.cap(db)
    found = graph.shortest_paths(
        db,
        source=source,
        target=target,
        user=user,
        at=day,
        max_depth=depth_n,
        relations=_csv(relations),
        type_ids=type_ids,
        records=records,
        limit=limit_n,
    )

    order = list(dict.fromkeys(node for nodes in found.nodes for node in nodes))
    for end in (source, target):
        if end not in order:
            order.append(end)
    edges = {edge.id: edge for one in found.edges for edge in one}
    rows = {
        row.id: row
        for row in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(order)))
    }
    degrees = _degrees(
        db, order, user, _system_types(all_types), at=day, skip_logs=not records
    )
    shown: dict[uuid.UUID, int] = {}
    for edge in edges.values():
        shown[edge.src] = shown.get(edge.src, 0) + 1
        shown[edge.dst] = shown.get(edge.dst, 0) + 1
    workspaces = _workspace_slugs(db)
    nodes: list[NodeOut] = []
    for node_id in order:
        row = rows.get(node_id)
        if row is None:
            continue
        object_type = all_types.get(row.type_id)
        degree = degrees.get(node_id, 0)
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
                truncated=degree > shown.get(node_id, 0),
            )
        )
    kinds = _kinds(db)
    return PathOut(
        source=source,
        target=target,
        found=found.length is not None,
        length=found.length,
        paths=[
            PathStepsOut(nodes=nodes_of, edges=[edge.id for edge in edges_of])
            for nodes_of, edges_of in zip(found.nodes, found.edges, strict=True)
        ],
        nodes=nodes,
        edges=[_edge_out(edge, kinds) for edge in edges.values()],
        max_depth=depth_n,
        visited=found.visited,
        truncated=found.truncated,
        as_of=day,
    )


@router.get("/subgraph", response_model=SubgraphOut)
def subgraph(
    types: str = Query(description="타입 slug, 쉼표로. 인터페이스면 그 구현 타입 전부"),
    relations: str | None = Query(default=None, description="관계 slug, 쉼표로"),
    q: str | None = Query(default=None, description="이름·식별자의 일부"),
    limit: int | None = Query(default=None, description=f"노드 상한. 최대 {MAX_NODES}"),
    offset: int = Query(default=0, ge=0),
    as_of: date | None = Query(default=None, description=AS_OF),
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
    day = edge_rule.at(as_of)
    all_types = {row.id: row for row in db.scalars(select(ObjectType))}
    systems = _system_types(all_types)
    wanted = _csv(types) or []
    # **부른 순서대로 쌓는다.** 타입 목록에는 순서가 없어(질의에 order by 가 없다) 같은
    # 물음에 쪽마다 다른 순서가 오고, 쪽을 타입마다 나누는 아래의 셈이 쪽 사이에서
    # 어긋난다 — 한 객체가 두 쪽에 나오거나 아무 쪽에도 안 나온다.
    asked = interfaces.load_ends(db).types_of(wanted)
    wanted_systems = [systems[slug] for slug in asked if slug in systems]
    rows_by_slug = {row.slug: row for row in all_types.values() if not system.is_system(row)}
    type_ids = [rows_by_slug[slug].id for slug in asked if slug in rows_by_slug]
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
            at=day,
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
    # **쪽을 타입마다 나눠 채운다.** 이름순으로 통째로 뜨면 한 타입이 첫 쪽을 다 차지하고
    # (「SM-…」 이 한글보다 앞), 다른 타입이 없는 그림은 선이 하나도 없어 「관계없음」 으로
    # 읽힌다.
    share = max(1, node_limit // len(type_ids))
    per_type_offset = offset // len(type_ids)
    rows: list[ObjectInstance] = []
    for type_id in type_ids:
        rows += list(
            db.scalars(
                select(ObjectInstance)
                .where(*conditions, ObjectInstance.type_id == type_id)
                .order_by(ObjectInstance.label, ObjectInstance.id)
                .offset(per_type_offset)
                .limit(share)
            )
        )
    rows = rows[:node_limit]
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
    edges: list[graph.Edge | refedges.RefEdge] = [
        *graph.induced_edges(db, ids=ids, limit=MAX_EDGES, at=day, relations=wanted_relations),
        *graph.induced_link_edges(db, ids=ids, limit=MAX_EDGES, relations=wanted_relations),
        *refedges.induced_edges(db, ids=ids, limit=MAX_EDGES, relations=wanted_relations),
    ]
    degrees = _degrees(db, ids, user, systems, at=day)
    shown: dict[uuid.UUID, int] = {}
    for edge in edges:
        shown[edge.src] = shown.get(edge.src, 0) + 1
        if edge.dst != edge.src:
            shown[edge.dst] = shown.get(edge.dst, 0) + 1

    kinds = _kinds(db)
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
        as_of=day,
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
    at: date | None,
) -> SubgraphOut:
    """원 표 타입만 고른 그림 — 행은 원 표에서, 선은 링크에서. 링크에는 기간이 없지만 차수의
    관계 쪽은 기준일을 따른다."""
    refs: list[tuple[ObjectType, SystemRef]] = []
    total = 0
    for object_type in wanted:
        found, n = system.source_of(object_type).search(db, user, q, limit, offset)
        total += n
        refs.extend((object_type, ref) for ref in found)
    refs = refs[:limit]
    ids = [ref.id for _t, ref in refs]
    edges = graph.induced_link_edges(db, ids=ids, limit=MAX_EDGES, relations=relations)
    degrees = _degrees(db, ids, user, systems, at=at)
    shown: dict[uuid.UUID, int] = {}
    for edge in edges:
        shown[edge.src] = shown.get(edge.src, 0) + 1
        if edge.dst != edge.src:
            shown[edge.dst] = shown.get(edge.dst, 0) + 1
    kinds = _kinds(db)
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
        as_of=at,
    )


# --- 정적 그리기 -------------------------------------------------------------------

#: 이보다 작은 응답은 줄이지 않는다 — 얻는 것이 없다.
_GZIP_FROM = 4096


def _compressed(request: Request, body: bytes) -> Response:
    """통째 그림은 수 MB 다 — 받는 쪽이 gzip 을 받으면 여기서 줄인다. 앞단(nginx)이 JSON 을
    줄인다는 보장이 없고(기본 설정은 text/html 만), 숫자 열이라 몇 배씩 준다(ADR 0045 실측)."""
    headers = {"Vary": "Accept-Encoding"}
    if len(body) >= _GZIP_FROM and "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        body = gzip.compress(body, compresslevel=5)
    return Response(body, media_type="application/json", headers=headers)


def _whole_relation(slug: str, count: int, kinds: dict[str, Kind]) -> WholeRelationOut:
    kind = kinds.get(slug)
    return WholeRelationOut(
        slug=slug,
        label=kind.label if kind else slug,
        inverse_label=(kind.inverse_label or "") if kind else "",
        directed=kind.directed if kind else True,
        count=count,
    )


@router.get("/whole", response_model=WholeOut)
def whole(
    request: Request,
    focus: uuid.UUID | None = Query(
        default=None, description="시작 객체 — focus 와 types 중 하나만 준다"
    ),
    types: str | None = Query(
        default=None,
        description=(
            "씨앗 타입 slug, 쉼표로 — 그 인스턴스 전부가 0단계다. 인터페이스면 구현 타입 전부"
        ),
    ),
    depth: int | None = Query(
        default=None,
        description=(
            f"씨앗에서 몇 단계까지. 0 이면 씨앗만. 기본 1, 최대 {graphwhole.MAX_DEPTH}"
        ),
    ),
    relations: str | None = Query(
        default=None, description="따라가고 그릴 관계 slug, 쉼표로(참조 칸은 ref:…)"
    ),
    neighbor_types: str | None = Query(
        default=None,
        description=(
            "이웃 타입 slug, 쉼표로 — 이웃 그림의 types 와 같은 뜻. 씨앗의 타입은 늘 든다"
        ),
    ),
    records: bool = Query(
        default=False,
        description="나를 가리키는 기록(ADR 0011)도 따라간다. 기본은 안 따라간다",
    ),
    as_of: date | None = Query(default=None, description=AS_OF),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """정적 그리기의 재료 — 씨앗에서 depth 단계 안의 노드와 **그 사이의 선 전부**(ADR 0045).

    씨앗 · 거르기 · 기준일 · 기록의 뜻은 이웃 그림(neighborhood)과 같고, 다른 것은 **노드마다
    자르지 않는다**는 것 하나다(fanout · limit 이 없다). 타입 씨앗(types)은 그 타입의 인스턴스
    전부가 0단계다 — depth 1 이면 그것들과 이어진 것까지.

    상한(노드 20만 · 선 70만, 응답의 `max_nodes` · `max_edges`)을 넘으면 그림을 반쯤 주지
    않는다: `over` 에 무엇이 얼마나 많은지(타입별 · 단계별 · 선 종류별)를 담고 nodes · edges 는
    비운다. 타입 씨앗은 계수표로 먼저 세어(ADR 0038) 넘으면 넓히기 전에 거절한다.

    노드는 열 배열이다(노드 사전 20만 개는 수십 MB) — 받는 쪽이 gzip 을 받으면 줄여서 준다.
    """
    seed_types_asked = _csv(types)
    if (focus is None) == (seed_types_asked is None):
        raise AppError(
            code("GRAPH", 3),
            "씨앗을 하나만 주세요 — focus(객체 하나) 또는 types(타입 전부) 중 하나입니다.",
            status=422,
        )
    depth_n = max(0, min(graphwhole.MAX_DEPTH, 1 if depth is None else depth))
    day = edge_rule.at(as_of)
    all_types = {row.id: row for row in db.scalars(select(ObjectType))}
    type_slug_of = {row.id: row.slug for row in all_types.values()}
    label_of = {row.slug: row.label for row in all_types.values()}
    systems = _system_types(all_types)
    ends = interfaces.load_ends(db)
    querycap.cap(db)

    seed_types: list[ObjectType] = []
    if focus is not None:
        start = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.id == focus,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        start_type = all_types.get(start.type_id) if start is not None else None
        focus_system: str | None = None
        if start is None:
            # 원 표의 행(부서 등)에서도 출발한다 — 이웃 그림과 같다.
            for candidate in systems.values():
                if system.find(db, candidate, focus) is not None:
                    start_type, focus_system = candidate, candidate.slug
                    break
        if start_type is None:
            raise NotFound(code("GRAPH", 1), "객체를 찾을 수 없습니다.")
        seed = graphwhole.Seed(
            focus=focus,
            focus_type=start.type_id if start is not None else None,
            focus_system=focus_system,
        )
        seed_types = [start_type]
    else:
        asked = ends.types_of(seed_types_asked or [])
        object_rows = {
            row.slug: row for row in all_types.values() if not system.is_system(row)
        }
        seed_types = [object_rows[slug] for slug in asked if slug in object_rows]
        system_types = [systems[slug] for slug in asked if slug in systems]
        if not seed_types and not system_types:
            raise NotFound(code("GRAPH", 2), f"타입을 찾을 수 없습니다: {types}")
        system_rows = [
            (object_type.slug, ref)
            for object_type in system_types
            for ref in system.source_of(object_type).search(
                db, user, None, graphwhole.MAX_NODES + 1, 0
            )[0]
        ]
        seed = graphwhole.Seed(
            type_ids=[row.id for row in seed_types], system_rows=system_rows
        )
        seed_types += system_types

    def answer(
        over: WholeOverOut | None = None,
        *,
        types_out: list[WholeTypeOut] | None = None,
        relations_out: list[WholeRelationOut] | None = None,
        nodes: WholeNodesOut | None = None,
        edges: WholeEdgesOut | None = None,
    ) -> Response:
        """응답 — 거절이면 표 · 열이 빈 채로 `over` 만."""
        out = WholeOut(
            seed="focus" if focus is not None else "types",
            focus=focus,
            seed_types=[row.slug for row in seed_types],
            depth=depth_n,
            records=records,
            as_of=day,
            max_nodes=graphwhole.MAX_NODES,
            max_edges=graphwhole.MAX_EDGES,
            types=types_out or [],
            relations=relations_out or [],
            nodes=nodes or WholeNodesOut(ids=[], type=[], label=[], depth=[], parent=[]),
            edges=edges or WholeEdgesOut(src=[], dst=[], rel=[]),
            over=over,
        )
        return _compressed(request, out.model_dump_json().encode())

    kinds = _kinds(db)

    if focus is None:
        # **먼저 센다** — 타입 씨앗만으로 넘으면 넓히지 않고 거절한다(계수표라 2,000만 건도
        # 즉시).
        count, exact, by_type_id = graphwhole.seed_count(
            db, user=user, type_ids=seed.type_ids, systems=len(seed.system_rows)
        )
        if count > graphwhole.MAX_NODES:
            by_slug: Counter[str] = Counter(
                {type_slug_of[type_id]: n for type_id, n in by_type_id.items() if n}
            )
            by_slug.update(slug for slug, _ref in seed.system_rows)
            over = WholeOverOut(
                what="nodes",
                count=count,
                exact=exact,
                limit=graphwhole.MAX_NODES,
                types=[
                    WholeTypeOut(slug=slug, label=label_of.get(slug, slug), count=n)
                    for slug, n in by_slug.most_common()
                ],
                layers=[count],
                relations=[],
            )
            return answer(over)

    near_ids: list[uuid.UUID] | None = None
    near_slugs: list[str] | None = None
    if asked_near := _csv(neighbor_types):
        wanted = ends.types_of(asked_near)
        near_ids = [row.id for row in all_types.values() if row.slug in wanted]
        near_slugs = list(wanted)
        # 씨앗의 타입은 거르기와 무관하게 늘 든다 — 이웃 그림의 시작점과 같다.
        for row in seed_types:
            if row.id not in near_ids:
                near_ids.append(row.id)
                near_slugs.append(row.slug)

    found = graphwhole.gather(
        db,
        user=user,
        seed=seed,
        depth=depth_n,
        systems=list(systems),
        filters=graphwhole.Filters(
            at=day,
            relations=_csv(relations),
            type_ids=near_ids,
            type_slugs=near_slugs,
            skip_logs=not records and near_ids is None,
        ),
        type_slug_of=type_slug_of,
    )
    if found.over is not None:
        over = WholeOverOut(
            what=found.over.what,
            count=found.over.count,
            exact=found.over.exact,
            limit=graphwhole.MAX_NODES if found.over.what == "nodes" else graphwhole.MAX_EDGES,
            types=[
                WholeTypeOut(slug=slug, label=label_of.get(slug, slug), count=n)
                for slug, n in found.over.by_type.items()
            ],
            layers=found.over.layers,
            relations=[
                _whole_relation(slug, n, kinds) for slug, n in found.over.by_relation.items()
            ],
        )
        return answer(over)

    # 표 — 큰 것부터. 노드 · 선은 이 표의 번호를 든다.
    type_counts = Counter(found.types)
    type_table = [slug for slug, _n in type_counts.most_common()]
    type_at = {slug: at for at, slug in enumerate(type_table)}
    rel_counts = Counter(found.rel)
    rel_table = [slug for slug, _n in rel_counts.most_common()]
    rel_at = {slug: at for at, slug in enumerate(rel_table)}
    # 큰 열은 검사 없이 싣는다(`model_construct`) — 20만 · 70만 칸을 다시 검사하면 그것만
    # 초 단위다.
    return answer(
        types_out=[
            WholeTypeOut(slug=slug, label=label_of.get(slug, slug), count=type_counts[slug])
            for slug in type_table
        ],
        relations_out=[_whole_relation(slug, rel_counts[slug], kinds) for slug in rel_table],
        nodes=WholeNodesOut.model_construct(
            ids=found.ids,
            type=[type_at[slug] for slug in found.types],
            label=found.labels,
            depth=found.depth,
            parent=found.parent,
            x=None,
            y=None,
        ),
        edges=WholeEdgesOut.model_construct(
            src=found.src, dst=found.dst, rel=[rel_at[slug] for slug in found.rel]
        ),
    )


# --- 전체 지도(ADR 0046) ------------------------------------------------------------
#
# 정적 그리기의 상한(노드 20만)을 넘는 범위 · 전체는 **서버가 미리 계산해 둔 지도**로 본다 —
# 멀리서는 원반과 밀도, 확대하면 상자 안의 점(5만까지), 자리는 판 안에서 늘 같다. 판은 작업이
# 만들고(`mapbuild` — 처음 판 · 매일 덧붙임 · 관리자의 전체 다시 배치), 여기는 읽기만 한다.


def _map_layout(db: Session) -> MapLayout:
    layout = mapbuild.current(db)
    if layout is None:
        raise AppError(
            code("GRAPH", 11),
            "지도 판이 아직 없습니다 — 처음 판은 워커가 만듭니다(시스템 관리자는 「전체 다시 "
            "배치」 로 바로 만들 수 있습니다).",
            status=409,
        )
    return layout


@router.get("/map", response_model=MapOut)
def graph_map(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """전체 지도 — 보이는 기준정보의 자리와 허브 원반(ADR 0046).

    원반의 반지름은 **보는 사람이 볼 수 있는 부서의 얼린 수**로, 수는 시간마다 센 것 중 볼 수
    있는 부서의 것만이다. 판 번호 · 집계 시각 · 보이는 부서로 ETag 를 단다 — 같으면 304."""
    tag = mapread.etag(db, user)
    if tag is not None and request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers={"ETag": tag})
    out = mapread.far(db, user)
    response = _compressed(request, out.model_dump_json().encode())
    if tag is not None:
        response.headers["ETag"] = tag
        response.headers["Cache-Control"] = "private, no-cache"
    return response


@router.get("/map/points", response_model=MapPointsOut)
def graph_map_points(
    request: Request,
    x0: float = Query(description="상자 왼쪽(지도 좌표)"),
    y0: float = Query(description="상자 아래"),
    x1: float = Query(description="상자 오른쪽"),
    y1: float = Query(description="상자 위"),
    types: str | None = Query(
        default=None, description="켠 기록 타입 slug, 쉼표로. 비우면 전부"
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """상자 안의 기록 점 — 기대 수(집계 x 원반 넓이 몫)가 상한을 넘으면 기록을 읽지 않고
    `over` 로 답한다(화면은 밀도와 「N건 — 더 확대」)."""
    if not (x1 > x0 and y1 > y0):
        raise AppError(
            code("GRAPH", 12), "상자가 비었습니다 — x0 < x1, y0 < y1 이어야 합니다."
        )
    layout = _map_layout(db)
    querycap.cap(db)
    keys = (layout.hub_keys or {}).keys()
    by_slug = {
        row.slug: row.id
        for row in db.scalars(select(ObjectType).where(ObjectType.id.in_(list(keys))))
    }
    wanted = _csv(types)
    chosen = (
        {by_slug[one] for one in wanted if one in by_slug} if wanted else set(by_slug.values())
    )
    found = mapread.points(db, user, layout, (x0, y0, x1, y1), chosen)
    slug_of = {value: key for key, value in by_slug.items()}
    table = sorted(
        {slug_of[one] for one in found.type_ids if one is not None and one in slug_of}
    )
    at = {slug: index for index, slug in enumerate(table)}
    labels = {row.id: row.label for row in db.scalars(select(ObjectType))}
    out = MapPointsOut.model_construct(
        box=[x0, y0, x1, y1],
        expected=found.expected,
        over=found.over,
        limit=mapread.MAX_POINTS,
        count=len(found.ids),
        types=table,
        ids=found.ids,
        x=found.x,
        y=found.y,
        type=[
            at.get(slug_of.get(one, ""), -1) if one is not None else -1
            for one in found.type_ids
        ],
        labels=found.labels,
        skipped=[
            MapTypeOut(slug=slug_of[one], label=labels.get(one, ""), record=True, count=n)
            for one, n in found.skipped.items()
            if one in slug_of
        ],
        lines=_point_lines(found.lines) if found.lines is not None else None,
        lines_up_to=maplines.LINES_UP_TO,
        lines_over=found.lines_over,
    )
    return _compressed(request, out.model_dump_json().encode())


def _point_lines(lines: maplines.PointLines) -> MapPointLinesOut:
    kinds = sorted(set(lines.kind))
    at = {slug: k for k, slug in enumerate(kinds)}
    return MapPointLinesOut.model_construct(
        src=lines.src,
        dst=lines.dst,
        rel=[at[slug] for slug in lines.kind],
        kinds=kinds,
        masters=[str(one) for one in lines.masters],
    )


@router.get("/map/links", response_model=MapLinksOut)
def graph_map_links(
    hub: uuid.UUID = Query(description="허브(기준정보) id"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MapLinksOut:
    """허브의 주 참조 밖의 연결 — 같은 기록이 함께 가리킨 기준정보(부품 · 센터 · 불량 …), 많은
    것부터 30개. 판을 만들 때 센 수이고 볼 수 있는 부서의 기록만 더한다."""
    layout = _map_layout(db)
    found = mapread.links(db, user, layout, hub)
    static = mapread._load_static(db, layout)
    shown = mapread.shown_masters(db, user, [one for one, _n in found.items])
    slugs = {row.id: row.slug for row in db.scalars(select(ObjectType))}
    base = db.get(MapLayout, layout.base_id) or layout
    return MapLinksOut(
        hub=str(hub),
        items=[
            MapLinkOut(
                id=str(one),
                label=shown[one][0],
                type_slug=slugs.get(shown[one][1], ""),
                count=n,
                x=float(static.x[static.index[one]]),
                y=float(static.y[static.index[one]]),
            )
            for one, n in found.items
            if one in shown and one in static.index
        ],
        withheld=found.withheld,
        counted_at=base.created_at,
    )


@router.get("/map/locate", response_model=MapLocateOut)
def graph_map_locate(
    id: uuid.UUID = Query(description="객체 id"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MapLocateOut:
    """객체 하나가 지도의 어디인가 — 기준정보면 그 점, 기록이면 허브 원반 안의 해시 자리."""
    layout = _map_layout(db)
    found = mapread.locate(db, user, layout, id)
    slug = ""
    if found.type_id is not None:
        row = db.get(ObjectType, found.type_id)
        slug = row.slug if row is not None else ""
    return MapLocateOut(
        id=str(id),
        kind=found.kind,
        x=found.x,
        y=found.y,
        r=found.r,
        label=found.label,
        type_slug=slug,
        reason=found.reason,
    )


@router.post("/map/relayout", response_model=JobOut, status_code=202)
def graph_map_relayout(
    user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> JobOut:
    """「전체 다시 배치」 — 판을 올린다(시스템 관리자). 앞 판의 자리에서 출발해 기존 자리가 1 ~
    5% 움직인다. 지도 작업이 이미 기다리거나 돌면 **그 작업을 돌려준다**(겹쳐 돌지 않는다)."""
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    waiting = db.scalar(
        select(Job)
        .where(Job.kind == "graph_map_full", Job.status.in_(("queued", "running")))
        .limit(1)
    )
    job = waiting or job_services.enqueue(
        db, kind="graph_map_full", params={"reason": "admin"}, user=user, workspace_id=None
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


def _hub_key_out(choice: maphubs.HubChoice, layout: MapLayout | None) -> MapHubKeyOut:
    used = (layout.hub_keys or {}) if layout is not None else {}
    return MapHubKeyOut(
        type_slug=choice.type.slug,
        type_label=choice.type.label,
        mode=choice.mode,
        key=choice.key,
        auto_key=choice.auto_key,
        used_key=used.get(str(choice.type.id)),
        in_layout=str(choice.type.id) in used,
        stale=choice.stale,
        candidates=[
            MapHubCandidateOut(
                key=one.key,
                label=one.label,
                target_label=one.target_label,
                target_types=list(one.target_types),
                required=one.required,
                hubs=one.hubs,
            )
            for one in choice.candidates
        ],
    )


@router.get("/map/hub-keys", response_model=list[MapHubKeyOut])
def graph_map_hub_keys(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MapHubKeyOut]:
    """기록 타입마다 허브 칸 — 자동 규칙 · 관리자 설정 · 후보 · 지금 판이 쓰는 칸."""
    layout = mapbuild.current(db)
    return [_hub_key_out(one, layout) for one in maphubs.choices(db).values()]


@router.put("/map/hub-keys/{type_slug}", response_model=MapHubKeyOut)
def graph_map_set_hub_key(
    type_slug: str,
    body: MapHubKeyIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> MapHubKeyOut:
    """허브 칸 설정(시스템 관리자) — `auto` · `key`(값 하나 · 기준정보를 가리키는 칸) · `none`.
    **다음 전체 배치부터** 지도에 반영된다(지금 판은 그 판의 칸으로 계속 센다)."""
    choices = maphubs.choices(db)
    choice = next((one for one in choices.values() if one.type.slug == type_slug), None)
    if choice is None:
        raise NotFound(code("GRAPH", 13), f"기록 타입을 찾을 수 없습니다: {type_slug}")
    if body.mode not in ("auto", "key", "none"):
        raise AppError(
            code("GRAPH", 14), "mode 는 auto · key · none 중 하나입니다.", status=422
        )
    if body.mode == "key" and not any(one.key == body.key for one in choice.candidates):
        names = ", ".join(one.key for one in choice.candidates) or "없음"
        raise AppError(
            code("GRAPH", 15),
            f"허브 칸이 될 수 없는 칸입니다: {body.key} — 값 하나이고 기준정보를 가리키는 "
            f"참조 칸만 됩니다(될 수 있는 칸: {names}).",
            status=422,
        )
    maphubs.set_key(db, choice.type, mode=body.mode, key=body.key, user_id=user.id)
    db.commit()
    fresh = maphubs.choices(db)[choice.type.id]
    return _hub_key_out(fresh, mapbuild.current(db))
