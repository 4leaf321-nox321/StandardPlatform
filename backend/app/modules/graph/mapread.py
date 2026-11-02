"""지도 읽기 — 멀리 보기(기준정보 자리 · 허브 원반 · 기준정보끼리의 선), 확대(상자 안의 기록
점과 그 선), 허브의 공동 참조 선, 객체 하나의 자리(ADR 0046). 선은 `maplines` 가 읽는다.

## 보는 사람 기준

원반의 반지름은 **보는 사람이 볼 수 있는 부서의 얼린 수**로 정한다(`scale · √Σ`) — 남의 부서
기록 수가 원반 크기로 새지 않는다. 밀도(멀리 보기의 색)와 확대의 기대 수는 시간마다 센 수 중 볼
수 있는 부서의 것만 더한다. 응답 어디에도 남의 부서의 수는 없다.

- 안 보이는 기준정보(남의 부서 소유)는 지도에 없다 — 점도 이름도 원반도. 그 허브를 가리키는
  **내가 볼 수 있는** 기록은 그 기록 타입의 「미지정」 자리로 간다(허브가 지워졌을 때도 같다).
- 판 뒤에 생긴 기준정보를 가리키는 기록은 아직 자리가 없다 — 다음 덧붙임(매일 밤)까지 그리지
  않고 수만 말한다(`unplaced`).

조회가 전원에게 열린 설치(기본, ADR 0034)에서는 모두가 같은 지도를 본다.

## 확대

상자의 기대 수를 **집계로 먼저** 센다(원반 넓이 x 상자에 든 몫, ±2%) — 상한(5만)을 넘으면
기록을 읽지 않고 밀도로 답한다. 넘지 않으면 원반마다 상자에 닿는 해시 칸을 구간으로 바꿔 확대
점 표(`graph_map_points`)의 `(허브, 해시)` 색인으로 그 구간만 읽는다. 계획은 셋으로
고정한다(측정 보고 — 안 고정하면 같은 상자가 1.3 ~ 13초로 튀었다): 구간마다 LATERAL 로 색인을
찾게 묶고(`OFFSET 0`), 상자 안에 든 점만 객체 표를 기본키로 하나씩 찾고, 식의 상수는 모두
float8.

점 표는 시간마다의 집계가 맞춘다 — **새 기록은 다음 집계(한 시간 안)부터 점으로 보인다.** 지운
기록은 객체 표를 늘 보므로 바로 빠진다.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Float,
    and_,
    cast,
    func,
    literal_column,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.graph import mapbuild, maphash, maplines
from app.modules.graph.models import (
    MapCount,
    MapCounted,
    MapFrozen,
    MapLayout,
    MapLink,
    MapMaster,
    MapPoint,
)
from app.modules.objects.models import ObjectInstance
from app.modules.workspaces.models import WorkspaceMember
from app.shared.permissions import sees_every_department, visible_owner_clause

#: 상자 하나에 실을 점의 상한 — 넘으면 기록을 읽지 않고 밀도와 「N건 — 더 확대」 로 답한다.
#: 1단계(정적 그리기)의 노드 상한(20만)은 「그리기」 의 것이고, 이것은 「확대한 상자에 실을
#: 점」 의 것이다 — 점 5만이면 응답이 gzip 1.5MB 안팎, 그리기는 한 번이다.
MAX_POINTS = 50_000
#: 이 수 이하일 때만 점에 이름을 싣는다(객체 표를 점마다 한 번 더 읽는다).
LABELS_UP_TO = 5_000
#: 「미지정」(주 참조가 빈) 기록을 점으로 그릴 때는 색인이 없어 그 타입을 통째로 읽는다 — 그
#: 타입이 이보다 크면 점으로 그리지 않고 밀도로만(그렇다고 말한다).
UNASSIGNED_SCAN_MAX = 200_000

Box = maphash.Box


# --- 판 · 집계 캐시 -------------------------------------------------------------------


@dataclass
class Static:
    """지금 판의 자리와 얼린 수 — 판이 바뀔 때만 다시 읽는다."""

    layout_id: int
    ids: list[uuid.UUID]
    index: dict[uuid.UUID, int]
    type_ids: list[uuid.UUID]
    x: np.ndarray
    y: np.ndarray
    frozen: dict[uuid.UUID | None, list[tuple[uuid.UUID, uuid.UUID | None, int]]]
    """허브(None = 미지정) → [(기록 타입, 부서, 수)]."""
    frozen_total: np.ndarray
    """허브마다 얼린 수의 합(모든 부서) — 상자 근처 원반을 고르는 데 쓴다(반지름의 상한)."""


@dataclass
class Live:
    """시간마다 센 수."""

    counted_at: datetime | None
    rows: dict[uuid.UUID | None, list[tuple[uuid.UUID, uuid.UUID | None, int]]]


_lock = threading.Lock()
_static: dict[int, Static] = {}
_live: dict[tuple[int, datetime | None], Live] = {}


def _load_static(db: Session, layout: MapLayout) -> Static:
    with _lock:
        cached = _static.get(layout.id)
    if cached is not None:
        return cached
    rows = db.execute(
        select(MapMaster.object_id, MapMaster.type_id, MapMaster.x, MapMaster.y).order_by(
            MapMaster.object_id
        )
    ).all()
    ids = [one[0] for one in rows]
    index = {one: at for at, one in enumerate(ids)}
    frozen: dict[uuid.UUID | None, list[tuple[uuid.UUID, uuid.UUID | None, int]]] = (
        defaultdict(list)
    )
    total = np.zeros(len(ids))
    for hub, type_id, ws, n in db.execute(
        select(MapFrozen.hub_id, MapFrozen.type_id, MapFrozen.workspace_id, MapFrozen.n)
    ):
        frozen[hub].append((type_id, ws, int(n)))
        if hub in index:
            total[index[hub]] += int(n)
    out = Static(
        layout.id,
        ids,
        index,
        [one[1] for one in rows],
        np.array([one[2] for one in rows], dtype=np.float64),
        np.array([one[3] for one in rows], dtype=np.float64),
        dict(frozen),
        total,
    )
    with _lock:
        _static.clear()
        _static[layout.id] = out
    return out


def _load_live(db: Session, layout_id: int) -> Live:
    counted = db.get(MapCounted, 1)
    at = counted.counted_at if counted is not None else None
    key = (layout_id, at)
    with _lock:
        cached = _live.get(key)
    if cached is not None:
        return cached
    rows: dict[uuid.UUID | None, list[tuple[uuid.UUID, uuid.UUID | None, int]]] = defaultdict(
        list
    )
    for hub, type_id, ws, n in db.execute(
        select(MapCount.hub_id, MapCount.type_id, MapCount.workspace_id, MapCount.n)
    ):
        rows[hub].append((type_id, ws, int(n)))
    out = Live(at, dict(rows))
    with _lock:
        _live.clear()
        _live[key] = out
    return out


def forget() -> None:
    """캐시를 비운다 — 시험이 판을 갈아 쓴 뒤."""
    with _lock:
        _static.clear()
        _live.clear()


# --- 보는 사람 ----------------------------------------------------------------------


@dataclass(frozen=True)
class Viewer:
    """보는 사람이 볼 수 있는 부서 — `everything` 이면 전부(전역 + 모든 부서)."""

    everything: bool
    workspaces: frozenset[uuid.UUID]

    def sees(self, ws: uuid.UUID | None) -> bool:
        return self.everything or ws is None or ws in self.workspaces

    @property
    def key(self) -> str:
        return (
            "all" if self.everything else ",".join(sorted(str(one) for one in self.workspaces))
        )


def viewer_of(db: Session, user: User) -> Viewer:
    if sees_every_department(user):
        return Viewer(True, frozenset())
    mine = db.scalars(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    return Viewer(False, frozenset(mine))


# --- 무리(원반 · 미지정 자리) -----------------------------------------------------------


@dataclass
class Group:
    """그림의 원반 하나 — 허브 원반이거나 기록 타입의 미지정 자리."""

    x: float
    y: float
    r: float
    hub: uuid.UUID | None
    """허브 원반이면 그 기준정보, 미지정 자리면 None."""
    slot_type: uuid.UUID | None
    """미지정 자리면 그 기록 타입."""
    counts: dict[uuid.UUID, int] = field(default_factory=dict)
    """기록 타입 → 지금 보이는 수."""
    sources: list[tuple[uuid.UUID | None, uuid.UUID]] = field(default_factory=list)
    """이 원반에 드는 기록의 출처 — (허브 — None 이면 주 참조가 빈 것, 기록 타입)."""


def _sum(
    rows: list[tuple[uuid.UUID, uuid.UUID | None, int]], viewer: Viewer
) -> dict[uuid.UUID, int]:
    out: dict[uuid.UUID, int] = defaultdict(int)
    for type_id, ws, n in rows:
        if viewer.sees(ws):
            out[type_id] += n
    return out


@dataclass
class Plan:
    """보는 사람의 지도 — 원반들과 판 밖 기록 수."""

    groups: list[Group]
    unplaced: dict[uuid.UUID, int]
    """판 밖 기준정보를 가리키는 보이는 기록 — 기록 타입 → 수."""


def plan(
    db: Session,
    user: User,
    layout: MapLayout,
    static: Static,
    live: Live,
    *,
    shown: set[uuid.UUID],
    candidates: set[uuid.UUID] | None = None,
) -> Plan:
    """원반들 — `shown` 은 보는 사람에게 보이는(살아 있는) 판의 기준정보. `candidates` 가
    있으면 그 허브만 본다(확대 — 상자 근처). 그때 미지정 자리는 정확하지 않다(부르는 쪽이
    미지정 자리가 상자에 안 닿을 때만 그렇게 부른다)."""
    viewer = viewer_of(db, user)
    scale = layout.scale
    groups: list[Group] = []
    slot_frozen: dict[uuid.UUID, int] = defaultdict(int)
    slot_counts: dict[uuid.UUID, dict[uuid.UUID, int]] = defaultdict(lambda: defaultdict(int))
    slot_sources: dict[uuid.UUID, list[tuple[uuid.UUID | None, uuid.UUID]]] = defaultdict(list)
    unplaced: dict[uuid.UUID, int] = defaultdict(int)
    hubs = (set(static.frozen) | set(live.rows)) if candidates is None else candidates | {None}
    for hub in hubs:
        frozen = _sum(static.frozen.get(hub, []), viewer)
        counts = _sum(live.rows.get(hub, []), viewer)
        if hub is None:
            for type_id, n in frozen.items():
                slot_frozen[type_id] += n
            for type_id, n in counts.items():
                slot_counts[type_id][type_id] += n
            for type_id in set(frozen) | set(counts):
                slot_sources[type_id].append((None, type_id))
            continue
        at = static.index.get(hub)
        if at is None:
            for type_id, n in counts.items():
                unplaced[type_id] += n
            continue
        if hub in shown:
            if not counts and not frozen:
                continue
            group = Group(
                float(static.x[at]),
                float(static.y[at]),
                scale * float(np.sqrt(max(sum(frozen.values()), 1))),
                hub,
                None,
                dict(counts),
                [(hub, type_id) for type_id in sorted(set(frozen) | set(counts), key=str)],
            )
            groups.append(group)
            continue
        # 안 보이거나 지워진 허브 — 보이는 기록은 그 타입의 미지정 자리로.
        for type_id, n in frozen.items():
            slot_frozen[type_id] += n
        for type_id, n in counts.items():
            slot_counts[type_id][type_id] += n
        for type_id in set(frozen) | set(counts):
            slot_sources[type_id].append((hub, type_id))
    for type_text, (x, y, _reserved) in (layout.slots or {}).items():
        type_id = uuid.UUID(type_text)
        if not slot_sources.get(type_id):
            continue
        groups.append(
            Group(
                float(x),
                float(y),
                scale * float(np.sqrt(max(slot_frozen.get(type_id, 0), 1))),
                None,
                type_id,
                dict(slot_counts.get(type_id, {})),
                slot_sources[type_id],
            )
        )
    return Plan(groups, dict(unplaced))


def shown_masters(
    db: Session, user: User, ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, tuple[str, uuid.UUID]]:
    """판의 기준정보 중 보는 사람에게 보이고 살아 있는 것 — id → (이름, 타입). `ids` 가 있으면
    그것만 본다."""
    obj = ObjectInstance
    query = (
        select(obj.id, obj.label, obj.type_id)
        .join(MapMaster, MapMaster.object_id == obj.id)
        .where(obj.deleted_at.is_(None), visible_owner_clause(user, obj.owner_workspace_id))
    )
    if ids is not None:
        query = query.where(obj.id.in_(ids))
    return {row[0]: (row[1], row[2]) for row in db.execute(query)}


# --- 멀리 보기 ----------------------------------------------------------------------


def etag(db: Session, user: User) -> str | None:
    """멀리 보기의 ETag — 판 번호 · 집계 시각 · 보이는 부서 · 도는 지도 작업 · 자동 계산을
    멈춘 까닭(ADR 0049). 판이 없으면 None."""
    from app.modules.jobs.models import Job

    layout = mapbuild.current(db)
    if layout is None:
        return None
    counted = db.get(MapCounted, 1)
    pending = db.execute(
        select(Job.id, Job.status)
        .where(Job.kind.in_(mapbuild.KINDS), Job.status.in_(("queued", "running")))
        .order_by(Job.created_at)
        .limit(1)
    ).first()
    raw = "|".join(
        [
            str(layout.id),
            counted.counted_at.isoformat() if counted is not None else "-",
            viewer_of(db, user).key,
            f"{pending[0]}:{pending[1]}" if pending else "-",
            mapbuild.paused(db) or "-",
        ]
    )
    return '"' + hashlib.sha1(raw.encode()).hexdigest()[:20] + '"'


# --- 확대 ----------------------------------------------------------------------------


@dataclass
class Points:
    expected: int
    over: bool
    ids: list[str] = field(default_factory=list)
    x: list[float] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    type_ids: list[uuid.UUID | None] = field(default_factory=list)
    labels: list[str] | None = None
    skipped: dict[uuid.UUID, int] = field(default_factory=dict)
    """점으로 못 그린 미지정 기록(타입이 너무 커 색인 없이 못 읽는다) — 타입 → 상자 안 기대
    수."""
    lines: maplines.PointLines | None = None
    """점의 선 — 점이 `maplines.LINES_UP_TO` 이하일 때만."""
    lines_over: bool = False
    """점은 문턱 이하인데 선이 `maplines.MAX_LINES` 를 넘어 싣지 않았다."""


def expected_in(
    groups: list[Group], box: Box, types: set[uuid.UUID]
) -> tuple[float, np.ndarray]:
    """상자 안 기대 수(집계 x 넓이 몫)와 원반마다의 몫."""
    if not groups:
        return 0.0, np.zeros(0)
    gx = np.array([one.x for one in groups])
    gy = np.array([one.y for one in groups])
    gr = np.array([one.r for one in groups])
    share = maphash.box_fractions(gx, gy, gr, box)
    total = 0.0
    for at, group in enumerate(groups):
        if share[at] > 0:
            total += share[at] * sum(n for t, n in group.counts.items() if t in types)
    return total, share


def points(
    db: Session,
    user: User,
    layout: MapLayout,
    box: Box,
    types: set[uuid.UUID],
) -> Points:
    """상자 안의 기록 점 — 기대 수가 상한을 넘으면 읽지 않는다."""
    static = _load_static(db, layout)
    live = _load_live(db, layout.id)
    near_ids = _near_hubs(static, layout.scale, box)
    slots_hit = _slots_hit(layout, box)
    shown = set(shown_masters(db, user, None if slots_hit else near_ids))
    the_plan = plan(
        db,
        user,
        layout,
        static,
        live,
        shown=shown,
        candidates=None if slots_hit else set(near_ids),
    )
    groups = the_plan.groups
    expected, share = expected_in(groups, box, types)
    if expected > MAX_POINTS:
        return Points(expected=round(expected), over=True)
    hub_rows: list[tuple[uuid.UUID, int, int, float, float, float]] = []
    unassigned: list[tuple[uuid.UUID, Group]] = []
    skipped: dict[uuid.UUID, int] = {}
    keys = {uuid.UUID(t): k for t, k in (layout.hub_keys or {}).items()}
    for at, group in enumerate(groups):
        if share[at] <= 0:
            continue
        ranges = maphash.cells_for_box(group.x, group.y, group.r, box)
        if not ranges:
            continue
        hubs_here: set[uuid.UUID] = set()
        for hub, type_id in group.sources:
            if type_id not in types:
                continue
            if hub is None:
                if _type_size(live, type_id) > UNASSIGNED_SCAN_MAX:
                    skipped[type_id] = skipped.get(type_id, 0) + round(
                        share[at] * group.counts.get(type_id, 0)
                    )
                else:
                    unassigned.append((type_id, group))
                continue
            hubs_here.add(hub)
        # 한 허브를 여러 기록 타입이 가리켜도 점 표에서는 한 번 — 타입은 객체 표가 거른다.
        for hub in sorted(hubs_here, key=str):
            for lo, hi in ranges:
                hub_rows.append((hub, lo, hi, group.x, group.y, group.r))
    found = _read_hub_points(db, user, hub_rows, box, types)
    for type_id, group in unassigned:
        found += _read_unassigned(db, user, type_id, keys.get(type_id), group, box)
    if len(found) > MAX_POINTS:
        return Points(expected=max(round(expected), len(found)), over=True)
    labels: list[str] | None = None
    if len(found) <= LABELS_UP_TO:
        labels = _labels(db, [one[0] for one in found])
    lines: maplines.PointLines | None = None
    if len(found) <= maplines.LINES_UP_TO:
        lines = maplines.points(
            db,
            [one[0] for one in found],
            sorted(set(static.type_ids), key=str),
            lambda ids: set(
                shown_masters(db, user, [one for one in ids if one in static.index])
            ),
        )
    return Points(
        expected=round(expected),
        over=False,
        ids=[str(one[0]) for one in found],
        x=[round(one[1], 5) for one in found],
        y=[round(one[2], 5) for one in found],
        type_ids=[one[3] for one in found],
        labels=labels,
        skipped=skipped,
        lines=lines,
        lines_over=len(found) <= maplines.LINES_UP_TO and lines is None,
    )


def _type_size(live: Live, type_id: uuid.UUID) -> int:
    return sum(n for rows in live.rows.values() for t, _ws, n in rows if t == type_id)


def _near_hubs(static: Static, scale: float, box: Box) -> list[uuid.UUID]:
    """상자에 닿을 수 있는 허브 — 얼린 수의 합(모든 부서)으로 잡은 반지름이 상한이다."""
    if not static.ids:
        return []
    reach = scale * np.sqrt(np.maximum(static.frozen_total, 1.0))
    x0, y0, x1, y1 = box
    hit = (
        (static.x + reach >= x0)
        & (static.x - reach <= x1)
        & (static.y + reach >= y0)
        & (static.y - reach <= y1)
        & (static.frozen_total > 0)
    )
    return [static.ids[at] for at in np.where(hit)[0]]


def _slots_hit(layout: MapLayout, box: Box) -> bool:
    x0, y0, x1, y1 = box
    for x, y, r in (layout.slots or {}).values():
        if x + r >= x0 and x - r <= x1 and y + r >= y0 and y - r <= y1:
            return True
    return False


def _float8(value: float) -> Any:
    return cast(literal_column(repr(float(value))), Float(precision=53))


def _xy(h: Any, gx: Any, gy: Any, gr: Any) -> tuple[Any, Any]:
    """(x, y) 식 — `maphash.place` 와 같은 셈을 SQL 로, 상수는 float8. 비트 자리 수는 글자
    그대로 둔다 (bigint 로 묶으면 `bigint >> bigint` 연산자가 없다)."""
    f8 = Float(precision=53)

    def bits(shift: int, mask: int) -> Any:
        shifted = h.op(">>")(literal_column(str(shift))) if shift else h
        return cast(shifted.op("&")(literal_column(str(mask))), f8)

    m26 = (1 << 26) - 1
    u = (bits(58, 63) + bits(26, m26) / _float8(67108864.0)) / _float8(64.0)
    v = (bits(52, 63) + bits(0, m26) / _float8(67108864.0)) / _float8(64.0)
    rad = gr * func.sqrt(u)
    angle = _float8(2.0) * func.pi() * v
    return gx + rad * func.cos(angle), gy + rad * func.sin(angle)


def _read_hub_points(
    db: Session,
    user: User,
    rows: list[tuple[uuid.UUID, int, int, float, float, float]],
    box: Box,
    types: set[uuid.UUID],
) -> list[tuple[uuid.UUID, float, float, uuid.UUID | None]]:
    """허브 원반의 점 — 확대 점 표(`graph_map_points`)를 구간마다 색인으로 찾고(LATERAL ·
    OFFSET 0), 상자 안에 든 점만 객체 표를 기본키로 하나씩 찾는다(지운 것 · 안 보이는 것 · 끈
    타입을 거르고 타입을 얻는다). 점 표는 시간마다 맞추므로 지운 기록이 남아 있을 수 있다 —
    그래서 객체 표는 늘 본다."""
    if not rows:
        return []
    f8 = Float(precision=53)
    q = (
        func.unnest(
            cast([one[0] for one in rows], ARRAY(PgUUID(as_uuid=True))),
            cast([one[1] for one in rows], ARRAY(BigInteger)),
            cast([one[2] for one in rows], ARRAY(BigInteger)),
            cast([one[3] for one in rows], ARRAY(f8)),
            cast([one[4] for one in rows], ARRAY(f8)),
            cast([one[5] for one in rows], ARRAY(f8)),
        )
        .table_valued("hub", "lo", "hi", "gx", "gy", "gr")
        .render_derived(name="q")
    )
    point = aliased(MapPoint)
    found = (
        select(point.id.label("id"), point.h.label("h"))
        .where(point.hub_id == q.c.hub, point.h.between(q.c.lo, q.c.hi))
        .offset(literal_column("0"))
        .lateral("p")
    )
    x, y = _xy(found.c.h, q.c.gx, q.c.gy, q.c.gr)
    placed = (
        select(found.c.id.label("id"), x.label("x"), y.label("y"))
        .select_from(q)
        .join(found, true())
        .cte("placed")
    )
    x0, y0, x1, y1 = box
    inbox = (
        select(placed.c.id, placed.c.x, placed.c.y)
        .where(
            placed.c.x.between(_float8(x0), _float8(x1)),
            placed.c.y.between(_float8(y0), _float8(y1)),
        )
        .limit(MAX_POINTS + 1)
        .cte("inbox")
        .prefix_with("MATERIALIZED")
    )
    obj = aliased(ObjectInstance)
    look = (
        select(obj.type_id.label("type_id"))
        .where(
            obj.id == inbox.c.id,
            obj.deleted_at.is_(None),
            visible_owner_clause(user, obj.owner_workspace_id),
            obj.type_id.in_(list(types)),
        )
        .offset(literal_column("0"))
        .lateral("o")
    )
    return [
        (one[0], float(one[1]), float(one[2]), one[3])
        for one in db.execute(
            select(inbox.c.id, inbox.c.x, inbox.c.y, look.c.type_id)
            .select_from(inbox)
            .join(look, true())
        )
    ]


def _read_unassigned(
    db: Session, user: User, type_id: uuid.UUID, key: str | None, group: Group, box: Box
) -> list[tuple[uuid.UUID, float, float, uuid.UUID | None]]:
    """미지정 자리의 점 — 주 참조가 빈 기록(색인이 없어 그 타입을 읽는다 — 작은 타입만)."""
    obj = ObjectInstance
    h = func.sp_map_hash(obj.id)
    x, y = _xy(h, _float8(group.x), _float8(group.y), _float8(group.r))
    empty: Any = true()
    if key:
        raw = obj.properties[key].astext
        empty = or_(raw.is_(None), ~raw.op("~")(mapbuild._UUID.pattern))
    placed = (
        select(obj.id.label("id"), x.label("x"), y.label("y"))
        .where(
            obj.type_id == type_id,
            obj.deleted_at.is_(None),
            visible_owner_clause(user, obj.owner_workspace_id),
            empty,
        )
        .cte("placed")
    )
    x0, y0, x1, y1 = box
    return [
        (one[0], float(one[1]), float(one[2]), type_id)
        for one in db.execute(
            select(placed.c.id, placed.c.x, placed.c.y)
            .where(
                and_(
                    placed.c.x.between(_float8(x0), _float8(x1)),
                    placed.c.y.between(_float8(y0), _float8(y1)),
                )
            )
            .limit(MAX_POINTS + 1)
        )
    ]


def _labels(db: Session, ids: list[uuid.UUID]) -> list[str]:
    if not ids:
        return []
    names: dict[uuid.UUID, str] = {}
    for start in range(0, len(ids), 5000):
        chunk = ids[start : start + 5000]
        names.update(
            (row[0], row[1])
            for row in db.execute(
                select(ObjectInstance.id, ObjectInstance.label).where(
                    ObjectInstance.id.in_(chunk)
                )
            )
        )
    return [names.get(one, "") for one in ids]


# --- 허브의 공동 참조 선 ----------------------------------------------------------------


@dataclass
class Linked:
    hub: uuid.UUID
    items: list[tuple[uuid.UUID, int]]
    """(보이는 기준정보, 함께 가리킨 보이는 기록 수) — 많은 것부터."""
    by_workspace: bool
    withheld: bool
    """부서별로 센 선이 없는 판이라 부서로 좁힌 사람에게는 보이지 않는다."""


def links(
    db: Session, user: User, layout: MapLayout, hub: uuid.UUID, limit: int = 30
) -> Linked:
    split = bool((layout.stats or {}).get("links_by_workspace"))
    viewer = viewer_of(db, user)
    if not viewer.everything and not split:
        return Linked(hub, [], split, True)
    totals: dict[uuid.UUID, int] = defaultdict(int)
    for a, b, ws, n in db.execute(
        select(MapLink.a_id, MapLink.b_id, MapLink.workspace_id, MapLink.n).where(
            or_(MapLink.a_id == hub, MapLink.b_id == hub)
        )
    ):
        if viewer.everything or viewer.sees(ws):
            totals[b if a == hub else a] += int(n)
    shown = shown_masters(db, user, list(totals))
    items = sorted(
        ((one, n) for one, n in totals.items() if one in shown and n > 0),
        key=lambda pair: (-pair[1], str(pair[0])),
    )
    return Linked(hub, items[:limit], split, False)


# --- 멀리 보기의 응답 ---------------------------------------------------------------


def _last_error(db: Session) -> str | None:
    from app.modules.jobs.models import Job

    row = db.execute(
        select(Job.status, Job.error)
        .where(Job.kind.in_(mapbuild.KINDS), Job.status.in_(("done", "failed")))
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()
    return row[1] if row is not None and row[0] == "failed" else None


def _pending(db: Session) -> Any:
    from app.modules.jobs.models import Job

    return db.scalar(
        select(Job)
        .where(Job.kind.in_(mapbuild.KINDS), Job.status.in_(("queued", "running")))
        .order_by(Job.created_at)
        .limit(1)
    )


def far(db: Session, user: User) -> Any:
    """멀리 보기 — 보이는 기준정보의 자리와 원반(반지름 · 지금 수). `MapOut` 을 돌려준다."""
    from app.modules.graph import maphubs
    from app.modules.graph.schemas import (
        MapDiscsOut,
        MapJobOut,
        MapLayoutOut,
        MapMastersOut,
        MapOut,
        MapTypeOut,
        MapUnplacedOut,
        WholeEdgesOut,
        WholeRelationOut,
    )
    from app.modules.ontology.models import ObjectType

    pending = _pending(db)
    job = (
        MapJobOut(id=pending.id, kind=pending.kind, status=pending.status) if pending else None
    )
    layout = mapbuild.current(db)
    empty_masters = MapMastersOut(ids=[], type=[], label=[], x=[], y=[])
    empty_discs = MapDiscsOut(
        x=[], y=[], r=[], hub=[], slot=[], count_disc=[], count_type=[], count_n=[]
    )
    if layout is None:
        return MapOut(
            ready=False,
            bbox=[
                -mapbuild.SPAN / 2,
                -mapbuild.SPAN / 2,
                mapbuild.SPAN / 2,
                mapbuild.SPAN / 2,
            ],
            max_points=MAX_POINTS,
            types=[],
            masters=empty_masters,
            discs=empty_discs,
            unplaced=[],
            relations=[],
            lines=WholeEdgesOut(src=[], dst=[], rel=[]),
            job=job,
            last_error=_last_error(db),
            paused=mapbuild.paused(db),
            can_relayout=user.is_system_admin,
            drift=[],
        )
    static = _load_static(db, layout)
    live = _load_live(db, layout.id)
    shown = shown_masters(db, user)
    the_plan = plan(db, user, layout, static, live, shown=set(shown))
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    choices = maphubs.choices(db)
    hub_keys = {uuid.UUID(t): k for t, k in (layout.hub_keys or {}).items()}
    master_count: dict[uuid.UUID, int] = defaultdict(int)
    for _label, type_id in shown.values():
        master_count[type_id] += 1
    record_count: dict[uuid.UUID, int] = defaultdict(int)
    for group in the_plan.groups:
        for type_id, n in group.counts.items():
            record_count[type_id] += n
    table: list[MapTypeOut] = []
    order: list[uuid.UUID] = []
    for type_id in sorted(master_count, key=lambda one: types[one].slug):
        row = types[type_id]
        table.append(
            MapTypeOut(
                slug=row.slug, label=row.label, record=False, count=master_count[type_id]
            )
        )
        order.append(type_id)
    for type_id in sorted(hub_keys, key=lambda one: types[one].slug if one in types else ""):
        record = types.get(type_id)
        if record is None:
            continue
        key = hub_keys[type_id]
        choice = choices.get(type_id)
        label = next(
            (one.label for one in (choice.candidates if choice else []) if one.key == key), key
        )
        table.append(
            MapTypeOut(
                slug=record.slug,
                label=record.label,
                record=True,
                hub_key=key,
                hub_label=label,
                count=record_count.get(type_id, 0),
            )
        )
        order.append(type_id)
    type_at = {one: at for at, one in enumerate(order)}
    master_ids = [one for one in static.ids if one in shown]
    master_at = {one: at for at, one in enumerate(master_ids)}
    masters = MapMastersOut.model_construct(
        ids=[str(one) for one in master_ids],
        type=[type_at.get(shown[one][1], -1) for one in master_ids],
        label=[shown[one][0] for one in master_ids],
        x=[round(float(static.x[static.index[one]]), 4) for one in master_ids],
        y=[round(float(static.y[static.index[one]]), 4) for one in master_ids],
    )
    disc_cols: dict[str, list[Any]] = defaultdict(list)
    for at, group in enumerate(the_plan.groups):
        disc_cols["x"].append(round(group.x, 4))
        disc_cols["y"].append(round(group.y, 4))
        disc_cols["r"].append(round(group.r, 5))
        disc_cols["hub"].append(master_at.get(group.hub, -1) if group.hub else -1)
        disc_cols["slot"].append(type_at.get(group.slot_type, -1) if group.slot_type else -1)
        for type_id, n in sorted(group.counts.items(), key=lambda pair: str(pair[0])):
            if n and type_id in type_at:
                disc_cols["count_disc"].append(at)
                disc_cols["count_type"].append(type_at[type_id])
                disc_cols["count_n"].append(n)
    discs = MapDiscsOut.model_construct(
        x=disc_cols["x"],
        y=disc_cols["y"],
        r=disc_cols["r"],
        hub=disc_cols["hub"],
        slot=disc_cols["slot"],
        count_disc=disc_cols["count_disc"],
        count_type=disc_cols["count_type"],
        count_n=disc_cols["count_n"],
    )
    kinds = maplines.kinds(db, [*static.type_ids, *hub_keys])
    kind_at = {one.slug: at for at, one in enumerate(kinds)}
    found = maplines.masters(db, master_at, sorted(set(static.type_ids), key=str))
    lines = maplines.Lines()
    for a, b, slug in zip(found.src, found.dst, found.kind, strict=True):
        if slug in kind_at:  # 지운 관계 종류의 줄은 이름이 없다
            lines.add(a, b, slug)
    per_kind: dict[str, int] = defaultdict(int)
    for slug in lines.kind:
        per_kind[slug] += 1
    base = db.get(MapLayout, layout.base_id) or layout
    moved = (base.stats or {}).get("moved") or {}
    drift = sorted(
        choice.type.slug
        for type_id, choice in choices.items()
        if str(type_id) not in (layout.hub_keys or {})
        or (layout.hub_keys or {}).get(str(type_id)) != choice.key
    )
    return MapOut(
        ready=True,
        layout=MapLayoutOut(
            version=layout.id,
            kind=layout.kind,
            laid_at=layout.created_at,
            full_version=base.id,
            full_at=base.created_at,
            by_timer=base.created_by is None,
            masters=len(static.ids),
            moved_p50=moved.get("p50"),
            moved_p95=moved.get("p95"),
        ),
        counted_at=live.counted_at,
        scale=layout.scale,
        bbox=list(layout.bbox),
        max_points=MAX_POINTS,
        types=table,
        masters=masters,
        discs=discs,
        unplaced=[
            MapUnplacedOut(type=type_at[type_id], count=n)
            for type_id, n in sorted(the_plan.unplaced.items(), key=lambda pair: str(pair[0]))
            if type_id in type_at and n
        ],
        relations=[
            WholeRelationOut(
                slug=one.slug,
                label=one.label,
                inverse_label=one.inverse_label,
                directed=one.directed,
                count=per_kind.get(one.slug, 0),
            )
            for one in kinds
        ],
        lines=WholeEdgesOut.model_construct(
            src=lines.src, dst=lines.dst, rel=[kind_at[slug] for slug in lines.kind]
        ),
        job=job,
        last_error=_last_error(db),
        paused=mapbuild.paused(db),
        can_relayout=user.is_system_admin,
        drift=drift,
    )


# --- 객체 하나의 자리 ----------------------------------------------------------------


@dataclass
class Located:
    kind: str
    x: float | None = None
    y: float | None = None
    r: float | None = None
    label: str = ""
    type_id: uuid.UUID | None = None
    reason: str | None = None


def locate(db: Session, user: User, layout: MapLayout, object_id: uuid.UUID) -> Located:
    """주소의 `at` — 기준정보면 그 점, 기록이면 원반 안의 해시 자리."""
    obj = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if obj is None:
        return Located("none", reason="객체를 찾을 수 없습니다.")
    static = _load_static(db, layout)
    keys = {uuid.UUID(t): k for t, k in (layout.hub_keys or {}).items()}
    if obj.id in static.index:
        at = static.index[obj.id]
        return Located(
            "master", float(static.x[at]), float(static.y[at]), None, obj.label, obj.type_id
        )
    if obj.type_id not in keys:
        from app.modules.graph import maphubs

        record = {row.id for row in maphubs.record_types(db)}
        why = (
            "판 뒤에 생긴 기록 타입입니다 — 다음 전체 배치에서 지도에 놓입니다."
            if obj.type_id in record
            else "판 뒤에 생긴 기준정보입니다 — 다음 덧붙임(매일 밤)에 자리가 생깁니다."
        )
        return Located("none", label=obj.label, type_id=obj.type_id, reason=why)
    key = keys[obj.type_id]
    raw = (obj.properties or {}).get(key) if key else None
    hub: uuid.UUID | None = None
    if isinstance(raw, str) and mapbuild._UUID.match(raw):
        hub = uuid.UUID(raw)
    if hub is not None and hub not in static.index:
        return Located(
            "none",
            label=obj.label,
            type_id=obj.type_id,
            reason=(
                "가리키는 기준정보가 판에 없습니다 — 새 것이면 다음 덧붙임(매일 밤)에 "
                "놓입니다."
            ),
        )
    live = _load_live(db, layout.id)
    shown = set(shown_masters(db, user, [hub] if hub else []))
    the_plan = plan(
        db, user, layout, static, live, shown=shown, candidates={hub} if hub else set()
    )
    group = next((one for one in the_plan.groups if hub is not None and one.hub == hub), None)
    if group is None:
        group = next((one for one in the_plan.groups if one.slot_type == obj.type_id), None)
    if group is None:
        return Located("none", label=obj.label, type_id=obj.type_id, reason="자리가 없습니다.")
    x, y = maphash.place(maphash.hash_of(obj.id), group.x, group.y, group.r)
    return Located("record", x, y, group.r, obj.label, obj.type_id)
