"""영향 범위 — **고른 선(관계 · 참조 칸)을 한 방향으로 끝까지** 따라간다.

트리 「위 전부」(ADR 0035) · BOM 「어디에 쓰였나」(ADR 0040) · 계보 「원천 사슬」(ADR 0041)은
정해 둔 관계 하나만 따라간다. 이것은 아무 관계 종류 · 참조 칸을 골라 들어오는 선(기본 —
「이것에 기대는 것 전부」)이나 나가는 선(「이것이 기대는 것 전부」)으로 20단까지 넓힌다.

- **고리** — 트리가 아닌 관계는 돌아올 수 있다. 한 번 닿은 객체는 다시 펼치지 않고, 처음 닿은
  단계와 그 길(`parent` · `via`) 하나만 적는다(가장 가까운 길).
- **방향 없는 관계 종류**는 어느 쪽으로 보든 이어진 것으로 친다.
- **보이지 않거나 지운 객체에서는 멈춘다** — 이웃 그림(`graph.neighbor_edges`)과 같은 규칙이다.
  그 너머를 따라가면 안 보이는 객체가 길로 드러난다.
- **기준일** — 관계는 그날 살아 있는 선만(ADR 0039). 참조 칸은 기간이 없다.
- **원 표(부서 · 계정)와 잇는 선은 따라가지 않는다** — 그 끝은 객체가 아니다.

질의는 닿은 객체마다 색인을 찾게 묶는다(LATERAL) — 여러 id 를 한꺼번에 도착 칸으로 물으면
플래너가 「id 하나에 수천 줄」 로 어림해 참조 표를 통째로 훑었다(ADR 0046 「선」).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sqlalchemy import ARRAY, Select, and_, cast, func, or_, select, true
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import edges as edge_rule
from app.modules.objects import refedges
from app.modules.objects.models import ObjectInstance, ObjectRef, ObjectRelation
from app.modules.ontology.models import RelationType
from app.shared.batches import chunks
from app.shared.permissions import visible_owner_clause

Direction = Literal["in", "out"]

MAX_DEPTH = 20
#: 닿은 객체 상한 — 넘으면 거기서 멈추고 잘렸다고 말한다.
MAX_NODES = 20_000
DEFAULT_NODES = 2_000
#: 한 단계가 읽는 선의 상한 — 인기 모델은 기록 100만 건이 가리킨다. 단계마다 남은 객체
#: 자리만큼만 읽는다(상한 2천에 6만 줄씩 읽으니 2,000만 DB 에서 처음 21초였다). 넘으면 잘렸다고
#: 말한다.
MAX_ROWS = 60_000
#: 한 번에 묻는 닿은 객체 수.
BATCH = 500


class UnknownKinds(ValueError):
    """없는 관계 종류 · 참조 칸 — 무엇이 없는지 든다."""

    def __init__(self, slugs: list[str]) -> None:
        super().__init__(", ".join(slugs))
        self.slugs = slugs


@dataclass
class Reached:
    id: uuid.UUID
    depth: int
    parent: uuid.UUID | None
    """처음 닿을 때 거쳐 온 객체 — 시작점은 None."""
    via: str | None
    """그때 따라온 선의 종류(관계 slug · `ref:<타입>.<칸>`)."""


@dataclass
class Reach:
    nodes: list[Reached] = field(default_factory=list)
    """시작점(단계 0)부터 닿은 차례."""
    truncated: bool = False
    """객체 상한이나 선 상한에 닿았다 — 「이게 전부」 가 아니다."""
    exhausted: bool = False
    """더 갈 곳이 없어 멈췄다(깊이 상한 전에)."""


@dataclass(frozen=True)
class _Kinds:
    directed: list[str]
    undirected: list[str]
    refs: list[refedges.RefKind]


def _kinds(db: Session, relations: list[str]) -> _Kinds:
    wanted = list(dict.fromkeys(relations))
    rel_rows = {
        row.slug: row.directed
        for row in db.scalars(
            select(RelationType).where(
                RelationType.slug.in_([one for one in wanted if not refedges.is_ref(one)])
            )
        )
    }
    ref_kinds = refedges.kinds(db)
    unknown = [
        one
        for one in wanted
        if (one not in ref_kinds if refedges.is_ref(one) else one not in rel_rows)
    ]
    if unknown:
        raise UnknownKinds(unknown)
    return _Kinds(
        directed=[one for one in wanted if rel_rows.get(one) is True],
        undirected=[one for one in wanted if rel_rows.get(one) is False],
        refs=[ref_kinds[one] for one in wanted if refedges.is_ref(one)],
    )


def _lateral(
    db: Session, batch: list[uuid.UUID], hit: Callable[[Any], Select[Any]], cap: int
) -> list[Any]:
    """닿은 객체(`batch`)마다 `hit`(그 객체 하나의 선 — LATERAL)를 묻는다 — `cap` 줄까지."""
    q = (
        func.unnest(cast(batch, ARRAY(PgUUID(as_uuid=True))))
        .table_valued("id")
        .render_derived(name="q")
    )
    lateral = hit(q.c.id).limit(cap).lateral("r")
    return list(db.execute(select(lateral).select_from(q).join(lateral, true()).limit(cap)))


Found = tuple[str, uuid.UUID, uuid.UUID]
"""(선의 종류, 이쪽 끝 — 닿아 있던 객체, 저쪽 끝 — 새로 닿는 객체)."""


def _step(
    db: Session,
    frontier: list[uuid.UUID],
    kinds_: _Kinds,
    *,
    direction: Direction,
    user: User,
    day: date | None,
    budget: int,
) -> tuple[list[Found], bool]:
    """한 단계 — 찾은 선(`budget` 줄까지)과 그 상한에 닿았는지. 저쪽 끝은 보이고 지우지 않은
    객체만."""
    rel, ref, obj = ObjectRelation, ObjectRef, ObjectInstance
    seen_by = (obj.deleted_at.is_(None), visible_owner_clause(user, obj.owner_workspace_id))
    asks: list[tuple[Callable[[Any], Select[Any]], Callable[[Any], Found | None]]] = []

    def relation_hops(slugs: list[str], here: Any, other: Any) -> None:
        def hit(at: Any) -> Select[Any]:
            return (
                select(rel.relation, here.label("here"), other.label("other"))
                .join(obj, obj.id == other)
                .where(
                    here == at, rel.relation.in_(slugs), edge_rule.alive(rel, day), *seen_by
                )
            )

        asks.append((hit, lambda row: (row[0], row[1], row[2])))

    if kinds_.directed:
        if direction == "in":
            relation_hops(kinds_.directed, rel.dst_object_id, rel.src_object_id)
        else:
            relation_hops(kinds_.directed, rel.src_object_id, rel.dst_object_id)
    if kinds_.undirected:
        relation_hops(kinds_.undirected, rel.dst_object_id, rel.src_object_id)
        relation_hops(kinds_.undirected, rel.src_object_id, rel.dst_object_id)
    if kinds_.refs:
        slug_of = {(kind.src_type.id, kind.key): kind.slug for kind in kinds_.refs}
        here, other = (
            (ref.dst_id, ref.src_id) if direction == "in" else (ref.src_id, ref.dst_id)
        )
        # (가리키는 타입, 칸) 짝마다. 칸은 따로도 준다 — 짝의 OR 만 주면 색인(도착 · 칸)의 칸을
        # 못 써서, 기록 100만 건이 가리키는 모델에서 기준정보 칸만 물어도 100만 줄을 다 읽었다
        # (2,000만 DB 처음 47초 · 0건).
        pairs = and_(
            ref.key.in_(sorted({kind.key for kind in kinds_.refs})),
            or_(
                *(
                    and_(ref.src_type_id == kind.src_type.id, ref.key == kind.key)
                    for kind in kinds_.refs
                )
            ),
        )

        def ref_hit(at: Any) -> Select[Any]:
            return (
                select(ref.src_type_id, ref.key, here.label("here"), other.label("other"))
                .join(obj, obj.id == other)
                .where(here == at, pairs, *seen_by)
            )

        def ref_row(row: Any) -> Found | None:
            slug = slug_of.get((row[0], row[1]))
            return (slug, row[2], row[3]) if slug else None

        asks.append((ref_hit, ref_row))
    found: list[Found] = []
    read = 0
    for batch in chunks(frontier, BATCH):
        for hit, decode in asks:
            rows = _lateral(db, batch, hit, budget - read + 1)
            if read + len(rows) > budget:
                found.extend(one for one in map(decode, rows[: budget - read]) if one)
                return found, True
            read += len(rows)
            found.extend(one for one in map(decode, rows) if one is not None)
    return found, False


def walk(
    db: Session,
    *,
    start: uuid.UUID,
    user: User,
    relations: list[str],
    direction: Direction = "in",
    depth: int = MAX_DEPTH,
    limit: int = DEFAULT_NODES,
    day: date | None,
) -> Reach:
    """`start` 에서 `relations` 를 `direction` 으로 `depth` 단까지 — 닿은 객체가 `limit` 을
    넘으면 멈춘다. 시작점이 보이는지는 부르는 쪽이 본다. 없는 종류면 `UnknownKinds`."""
    kinds_ = _kinds(db, relations)
    out = Reach(nodes=[Reached(start, 0, None, None)])
    seen = {start}
    frontier = [start]
    for level in range(1, depth + 1):
        if not frontier:
            out.exhausted = True
            break
        # 남은 자리만큼 읽는다. 온 길로 돌아오는 선(방향 없는 종류)이 노드마다 하나씩 섞이므로
        # 그만큼 더 — 그래도 모자라면 잘렸다고 말한다.
        found, capped = _step(
            db,
            frontier,
            kinds_,
            direction=direction,
            user=user,
            day=day,
            budget=min(MAX_ROWS, limit - len(seen) + len(frontier)),
        )
        out.truncated = out.truncated or capped
        next_frontier: list[uuid.UUID] = []
        # 같은 답을 같은 차례로 — 종류 · 이쪽 · 저쪽 차례.
        for kind, here, other in sorted(
            found, key=lambda one: (one[0], str(one[1]), str(one[2]))
        ):
            if other in seen:
                continue
            if len(seen) >= limit:
                out.truncated = True
                break
            seen.add(other)
            out.nodes.append(Reached(other, level, here, kind))
            next_frontier.append(other)
        frontier = next_frontier
        if out.truncated and len(seen) >= limit:
            break
    else:
        out.exhausted = not frontier
    return out
