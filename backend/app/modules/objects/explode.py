"""BOM 전개 — **관계의 수량을 경로 따라 곱한다**(ADR 0040).

제품 하나에 어셈블리 2개, 어셈블리 하나에 서브 3개, 서브 하나에 볼트 4개면 제품 하나에 볼트는
2 곱하기 3 곱하기 4 = 24개다. 같은 서브를 다른 어셈블리도 쓰면 그 경로의 몫이 **더해진다** —
노드의 총수량 = Σ(경로마다 Π 수량).

## 선 집합을 받아 파이썬에서 전파한다

SQL 은 닿는 **선**을 한 번씩만 가져온다(`graph.tree_links`, 재귀 CTE). 경로를 SQL 에서
늘어놓으면 공유 하위 조립품이 있는 BOM 에서 줄이 경로 수만큼(곱으로) 늘어난다 — 선은 수만이어도
경로는 수백만이 된다. 파이썬은 선 집합을 **위상 순서**로 한 번 훑어 노드마다 총수량 · 경로 수 ·
가장 얕은 · 깊은 단계를 접는다(선 수에 비례). 「단계별」 보기만 경로를 늘어놓고, 그것은 줄 상한
(`LINES_MAX`)에서 멈춘다 — 전체 줄 수는 경로 수(위의 접은 값)에서 센다.

## 수량

- 관계 종류의 **수량 칸**(`RelationType.quantity_key`, `ontology/quantity.py`)이 선마다 「부모
  하나에 자식 몇 개」 다. 칸이 비었거나 숫자가 아니면 **1 로 치고** 「수량 없음 n개(선)」 로
  함께 말한다 — 롤업의 「n개 값 없음」 과 같은 결이다(말 안 하면 그 총수량은 맞는 수로 읽힌다).
  0 은 0 이다(그 아래가 하나도 안 든다).
- 수량 칸을 정하지 않은 종류와 참조 칸 트리(`refedges` — 칸에는 선의 칸이 없다)는 모든 선이
  1개다. 그때는 「수량 없음」 으로 세지 않는다(수량을 적을 자리가 없는 것이지 빠진 것이
  아니다).

## 보이는 범위 — 롤업 · 하위 포함과 같은 뜻

**지나가기는 전부, 내보이는 것은 보이고 지우지 않은 객체만.** `graph.descendant_ids` 가 그렇다
— 재귀가 객체 표를 안 보고 선만 따라가고, 맨 끝에서 지운 객체를 거른다(지운 어셈블리 아래의
부품도 닿는다). 롤업은 그 위에서 보이는 것만 센다. 전개도 같다:

- 남의 부서 객체(조회를 부서로 좁힌 설치)와 지운 객체를 **지나가며 수량을 곱한다** — 그 아래의
  보이는 부품은 제 몫의 총수량을 받는다.
- 「정리」 에는 줄로 안 나온다. 「단계별」 · 「어디에 쓰였나」 에는 자리를 지키는 가린 줄(「(볼
  수 없는 객체)」 · 「(삭제된 객체)」, id 없음)로 나온다 — 빼면 그 아래 줄의 단계가 위 줄 바로
  아래처럼 읽힌다.
- 몇 개를 지나갔는지 합계에 싣는다(`hidden` · `deleted`).

## 순환

수량을 곱하는 일은 순환이 있으면 끝이 없다(돌 때마다 커진다). 위상 순서를 못 세우면 멈추고
**어느 객체들에서 돌았는지** 말한다(OBJECTS-0130). 순환 금지(`acyclic`) 종류에는 순환이 없다
— 트리를 순환을 막지 않는 종류로 정한 타입에서만 만난다.

## 줄 순서

트리 화면과 같은 기준 — 형제는 **이름순**(그다음 식별자 · id). 「정리」 는 가장 얕은 단계,
그다음 이름순.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import graph, refedges, system
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared.batches import chunks
from app.shared.errors import Conflict, code
from app.shared.permissions import visible_owner_clause

Mode = Literal["summary", "indented", "where_used"]
MODES: tuple[Mode, ...] = ("summary", "indented", "where_used")

#: 한 번에 내는 줄의 상한 — 「단계별」 은 공유 하위 조립품마다 줄이 곱으로 는다(같은 서브를
#: 열 곳에서 쓰고 그 아래가 백 줄이면 천 줄). 넘으면 `truncated` 와 전체 줄 수를 함께 준다 — 말
#: 안 하면 잘린 목록이 「이게 전부」 로 읽힌다.
LINES_MAX = 5_000

DELETED_LABEL = "(삭제된 객체)"


def mode_of(raw: str) -> Mode:
    """쿼리의 보기 이름 → 보기. 모르는 이름은 「정리」(라우트가 먼저 거른다)."""
    return next((one for one in MODES if one == raw), "summary")


class Cycle(Exception):
    """선 집합에 순환이 있다 — 돈 객체들(처음과 끝이 같다)."""

    def __init__(self, ids: list[uuid.UUID]) -> None:
        super().__init__("순환")
        self.ids = ids


def amount(raw: Any) -> float | None:
    """선에 적힌 수량 — 숫자면 그 값, 비었거나 숫자가 아니면 None(1 로 센다). **정수는 정수로
    둔다** — 2 곱하기 3 곱하기 4 가 24.0000…1 로 번지지 않게."""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    return raw


@dataclass(frozen=True)
class Link:
    """걷는 방향의 선 하나 — `near` 에서 `far` 로(아래로면 부모 → 자식, 위로면 자식 → 부모)."""

    near: uuid.UUID
    far: uuid.UUID
    factor: float
    """곱할 값 — 수량이 빈 선은 1."""
    written: float | None
    """선에 적힌 수량 — 비었거나 숫자가 아니면 None."""


@dataclass
class Links:
    """start 에서 닿는 선 전부 — 걷는 방향으로 적은 것."""

    start: uuid.UUID
    links: list[Link]
    quantity_key: str
    """수량 칸 — 비었으면 모든 선이 1개이고 「수량 없음」 을 세지 않는다."""
    out: dict[uuid.UUID, list[Link]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        grouped: dict[uuid.UUID, list[Link]] = defaultdict(list)
        for one in self.links:
            grouped[one.near].append(one)
        self.out = dict(grouped)

    @property
    def nodes(self) -> set[uuid.UUID]:
        """닿는 노드 — start 를 뺀다."""
        return {one.far for one in self.links} - {self.start}

    @property
    def missing(self) -> int:
        """수량 없음인 선 수 — 수량 칸이 없으면 0."""
        if not self.quantity_key:
            return 0
        return sum(1 for one in self.links if one.written is None)

    def ends(self) -> set[uuid.UUID]:
        """걷는 방향으로 더 나갈 선이 없는 노드 — 아래로면 말단, 위로면 맨 위."""
        return {node for node in self.nodes if not self.out.get(node)}


def fetch(
    db: Session,
    tree: graph.Tree,
    start: uuid.UUID,
    *,
    up: bool,
    at: date | None,
    quantity_key: str,
) -> Links:
    """선 집합을 한 번에 — `graph.tree_links`(재귀 CTE, 깊이 ≤ `graph.MAX_DEPTH`)."""
    key = "" if refedges.is_ref(tree.relation) else quantity_key
    found = graph.tree_links(db, tree, start, up=up, at=at, quantity_key=key)
    links: list[Link] = []
    for one in found:
        written = amount(one.quantity) if key else None
        near, far = (one.child, one.parent) if up else (one.parent, one.child)
        links.append(
            Link(near=near, far=far, factor=1 if written is None else written, written=written)
        )
    return Links(start=start, links=links, quantity_key=key)


@dataclass
class Folded:
    """위상 순서로 접은 값 — 노드마다."""

    order: list[uuid.UUID]
    """start 에서 시작하는 위상 순서."""
    total: dict[uuid.UUID, float]
    """총수량 — 아래로면 「start 하나에 이것 몇 개」, 위로면 「이것 하나에 start 몇 개」."""
    paths: dict[uuid.UUID, int]
    shallow: dict[uuid.UUID, int]
    deep: dict[uuid.UUID, int]


def fold(found: Links) -> Folded:
    """Kahn 의 위상 정렬로 한 번 훑는다 — 총수량 = Σ(앞 노드의 총수량 곱하기 선의 수량).

    순환이 있으면 `Cycle`. start 로 돌아오는 선도 순환이다(start 는 앞에 아무것도 없이 먼저
    처리되므로 들어오는 수로는 못 잡는다)."""
    start = found.start
    waiting: dict[uuid.UUID, int] = defaultdict(int)
    for one in found.links:
        waiting[one.far] += 1
    if waiting.get(start):
        raise Cycle(_cycle(found))
    total: dict[uuid.UUID, float] = {start: 1}
    paths: dict[uuid.UUID, int] = {start: 1}
    shallow: dict[uuid.UUID, int] = {start: 0}
    deep: dict[uuid.UUID, int] = {start: 0}
    order: list[uuid.UUID] = []
    queue = deque([start])
    while queue:
        node = queue.popleft()
        order.append(node)
        for one in found.out.get(node, []):
            far = one.far
            total[far] = total.get(far, 0) + total[node] * one.factor
            paths[far] = paths.get(far, 0) + paths[node]
            level = shallow[node] + 1
            shallow[far] = min(shallow.get(far, level), level)
            deep[far] = max(deep.get(far, 0), deep[node] + 1)
            waiting[far] -= 1
            if waiting[far] == 0:
                queue.append(far)
    if len(order) < len(found.nodes) + 1:
        raise Cycle(_cycle(found))
    return Folded(order=order, total=total, paths=paths, shallow=shallow, deep=deep)


def _cycle(found: Links) -> list[uuid.UUID]:
    """순환 하나 — 깊이 우선으로 돌다 지금 걷는 길 위의 노드를 다시 만난 자리(처음과 끝이
    같다)."""
    state: dict[uuid.UUID, int] = {}  # 1 = 걷는 길 위, 2 = 끝남
    trail: list[uuid.UUID] = []
    stack: list[tuple[uuid.UUID, int]] = [(found.start, 0)]
    while stack:
        node, index = stack.pop()
        if index == 0:
            state[node] = 1
            trail.append(node)
        nexts = found.out.get(node, [])
        if index < len(nexts):
            stack.append((node, index + 1))
            far = nexts[index].far
            if state.get(far) == 1:
                return [*trail[trail.index(far) :], far]
            if far not in state:
                stack.append((far, 0))
            continue
        state[node] = 2
        trail.pop()
    return []


# --- 객체 -------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """줄에 실을 객체 하나 — 못 보거나 지운 것은 가린다(`shown`)."""

    id: uuid.UUID
    label: str
    key: str | None
    type_slug: str | None
    status: str | None
    hidden: bool = False
    deleted: bool = False

    @property
    def shown(self) -> bool:
        return not self.hidden and not self.deleted

    @property
    def order(self) -> tuple[str, str, str]:
        """형제 차례 — 트리 화면과 같은 이름순."""
        return (self.label, self.key or "", str(self.id))


def items(db: Session, user: User, ids: set[uuid.UUID]) -> dict[uuid.UUID, Item]:
    """객체 정보를 한 번에 — 보는 사람이 못 보는 것은 이름을 가리고(`system.HIDDEN_LABEL`),
    지운 것은 「(삭제된 객체)」."""
    out: dict[uuid.UUID, Item] = {}
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id).label("seen")
    for batch in chunks(list(ids)):
        rows = db.execute(
            select(
                ObjectInstance.id,
                ObjectInstance.label,
                ObjectInstance.key,
                ObjectInstance.status,
                ObjectInstance.deleted_at,
                ObjectType.slug,
                seen,
            )
            .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
            .where(graph.any_of(ObjectInstance.id, batch))
        )
        for row in rows:
            if row.deleted_at is not None:
                out[row.id] = Item(row.id, DELETED_LABEL, None, None, None, deleted=True)
            elif not row.seen:
                out[row.id] = Item(row.id, system.HIDDEN_LABEL, None, None, None, hidden=True)
            else:
                out[row.id] = Item(row.id, row.label, row.key, row.slug, row.status)
    for missing in ids - set(out):
        # 선은 있는데 객체가 없다(참조 칸 색인의 남은 줄) — 지운 것으로 친다.
        out[missing] = Item(missing, DELETED_LABEL, None, None, None, deleted=True)
    return out


# --- 보기 -------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """전개의 줄 하나."""

    level: int
    """단계 — 「단계별」 · 「어디에 쓰였나」 는 그 줄의 깊이(시작 객체가 0), 「정리」 는 가장
    얕은 단계."""
    item: Item
    total: float
    """누적 수량(단계별 — 시작에서 이 줄까지 곱한 것) · 총수량(정리) · 이 객체 하나에 시작
    객체가 몇 개(어디에 쓰였나 — 이 줄까지의 경로)."""
    quantity: float | None = None
    """그 줄로 오는 선에 적힌 수량(단계별 · 어디에 쓰였나). None 이면 1 로 셌다."""
    quantity_missing: bool = False
    depth_max: int | None = None
    paths: int | None = None
    missing: int | None = None
    """「정리」 — 이 객체로 오는 선 중 수량 없음인 것."""


@dataclass(frozen=True)
class Top:
    """맨 위 객체 하나 — 「그것 하나에 시작 객체가 총 몇 개」."""

    item: Item
    total: float
    paths: int
    level: int
    """가장 먼 단계."""


@dataclass
class Explosion:
    mode: Mode
    root: Item
    relation: str
    relation_label: str
    parent_end: graph.ParentEnd
    quantity_key: str
    quantity_label: str
    lines: list[Line]
    tops: list[Top]
    objects: int
    """서로 다른 보이는 객체 수(시작 객체를 뺀다)."""
    lines_total: int
    """자르기 전의 전체 줄 수."""
    missing: int
    hidden: int
    deleted: int
    links: int
    truncated: bool


def quantity_of(db: Session, relation: str) -> tuple[str, str]:
    """그 트리 관계의 수량 칸 — (키, 이름). 참조 칸 트리 · 수량 칸 없는 종류는 ("", "")."""
    if refedges.is_ref(relation):
        return "", ""
    kind = db.scalar(select(RelationType).where(RelationType.slug == relation))
    if kind is None or not kind.quantity_key:
        return "", ""
    label = db.scalar(
        select(PropertyDef.label).where(
            PropertyDef.owner_kind == "relation",
            PropertyDef.owner_id == kind.id,
            PropertyDef.key == kind.quantity_key,
        )
    )
    return kind.quantity_key, label or kind.quantity_key


def relation_label(db: Session, relation: str) -> str:
    if refedges.is_ref(relation):
        kind = refedges.kinds(db).get(relation)
        return kind.label if kind else relation
    found = db.scalar(select(RelationType.label).where(RelationType.slug == relation))
    return found or relation


def cycle_conflict(db: Session, user: User, ids: list[uuid.UUID]) -> Conflict:
    """순환을 사람의 말로 — 「A → B → C → A」. 못 보는 객체는 이름을 가린다."""
    names = items(db, user, set(ids))
    chain = " → ".join(names[one].label for one in ids) if ids else "(알 수 없음)"
    return Conflict(
        code("OBJECTS", 130),
        f"트리가 순환합니다 — {chain}. 순환이 있으면 수량을 곱할 수 없습니다(돌 때마다 "
        "커집니다). 그중 관계 하나를 해제하거나, 트리를 순환을 막는 관계 종류로 정하세요.",
        details={"cycle": [str(one) for one in ids]},
    )


def explode(
    db: Session,
    user: User,
    tree: graph.Tree,
    root: ObjectInstance,
    *,
    mode: Mode,
    at: date | None,
    limit: int = LINES_MAX,
) -> Explosion:
    """뿌리 하나를 전개한다 — 보기(`mode`)마다 줄을 낸다. 뿌리는 부르는 쪽이 볼 수 있는 것으로
    골라 온다(`routes._visible`)."""
    limit = max(1, min(limit, LINES_MAX))
    key, label = quantity_of(db, tree.relation)
    up = mode == "where_used"
    found = fetch(db, tree, root.id, up=up, at=at, quantity_key=key)
    try:
        folded = fold(found)
    except Cycle as caught:
        raise cycle_conflict(db, user, caught.ids) from caught
    nodes = found.nodes
    known = items(db, user, nodes | {root.id})
    shown = [node for node in nodes if known[node].shown]

    lines: list[Line]
    tops: list[Top] = []
    if mode == "summary":
        missing_in: dict[uuid.UUID, int] = defaultdict(int)
        if key:
            for one in found.links:
                if one.written is None:
                    missing_in[one.far] += 1
        ordered = sorted(shown, key=lambda node: (folded.shallow[node], known[node].order))
        lines_total = len(ordered)
        lines = [
            Line(
                level=folded.shallow[node],
                item=known[node],
                total=folded.total[node],
                depth_max=folded.deep[node],
                paths=folded.paths[node],
                missing=missing_in.get(node, 0),
            )
            for node in ordered[:limit]
        ]
    else:
        lines_total = sum(folded.paths[node] for node in nodes)
        lines = _walk(found, known, limit=limit)
        if up:
            tops = sorted(
                (
                    Top(
                        item=known[node],
                        total=folded.total[node],
                        paths=folded.paths[node],
                        level=folded.deep[node],
                    )
                    for node in found.ends()
                    if known[node].shown
                ),
                key=lambda one: one.item.order,
            )
    return Explosion(
        mode=mode,
        root=known[root.id],
        relation=tree.relation,
        relation_label=relation_label(db, tree.relation),
        parent_end=tree.parent_end,
        quantity_key=key,
        quantity_label=label,
        lines=lines,
        tops=tops,
        objects=len(shown),
        lines_total=lines_total,
        missing=found.missing,
        hidden=sum(1 for node in nodes if known[node].hidden),
        deleted=sum(1 for node in nodes if known[node].deleted),
        links=len(found.links),
        truncated=lines_total > len(lines),
    )


def _walk(found: Links, known: dict[uuid.UUID, Item], *, limit: int) -> list[Line]:
    """경로마다 한 줄 — 깊이 우선, 형제는 이름순. 상한에서 멈춘다(순환은 `fold` 가 먼저
    막았다)."""
    out: list[Line] = []
    siblings = {
        node: sorted(links, key=lambda one: known[one.far].order)
        for node, links in found.out.items()
    }
    # (선, 단계, 그 줄까지의 누적) — 뒤에서 꺼내므로 형제를 거꾸로 쌓는다.
    stack: list[tuple[Link, int, float]] = [
        (one, 1, one.factor) for one in reversed(siblings.get(found.start, []))
    ]
    while stack and len(out) < limit:
        link, level, running = stack.pop()
        out.append(
            Line(
                level=level,
                item=known[link.far],
                total=running,
                quantity=link.written,
                quantity_missing=bool(found.quantity_key) and link.written is None,
            )
        )
        for one in reversed(siblings.get(link.far, [])):
            stack.append((one, level + 1, running * one.factor))
    return out


# --- 롤업이 쓰는 것 -----------------------------------------------------------


@dataclass
class Below:
    """롤업이 아래를 셀 때 쓰는 것 — 닿는 노드, 말단, 총수량(순환이면 None)."""

    nodes: set[uuid.UUID]
    leaves: set[uuid.UUID]
    total: dict[uuid.UUID, float] | None
    missing: int


def below(db: Session, tree: graph.Tree, root_id: uuid.UUID, *, at: date | None) -> Below:
    """뿌리 아래 전부와 그 총수량 — 롤업의 「수량 반영」 · 「말단만」. 순환이면 총수량 없이
    말단만(말단은 순환이 있어도 정해진다)."""
    key, _ = quantity_of(db, tree.relation)
    found = fetch(db, tree, root_id, up=False, at=at, quantity_key=key)
    try:
        total: dict[uuid.UUID, float] | None = fold(found).total
    except Cycle:
        total = None
    return Below(
        nodes=found.nodes,
        leaves=found.ends(),
        total=total,
        missing=found.missing,
    )
