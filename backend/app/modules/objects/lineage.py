"""파생 · 계보 — 「무엇에서 만들어졌나」 와 **낡음의 판정 한 곳**(ADR 0041).

CAE · 제품 개발에서 도면 → 메시 → 해석 조건 → 해석 실행 → 결과 → 보고서처럼 「무엇에서
만들어졌나」 를 잇는 관계가 **계보 관계**다(`RelationType.lineage_source` — 어느 끝이
원천인가). 선은 맺는 순간 원천의 개정 값과 바뀐 때를 박는다(고정 — DB 트리거, `pinning.py`).
그래서 「이 시험은 도면 Rev.C 기준」 이 선에 남고, 도면이 Rev.D 가 되면 그 위의 결과가 낡았다고
말할 수 있다.

## 판정

- **바로 낡음** — 계보 선의 원천이 고정 뒤에 바뀌었다. 원천 타입에 개정 칸이 있으면 「지금 개정
  값 ≠ 고정한 개정」, 없으면 「원천의 바뀐 때 > 고정한 때」. 식은 DB 함수
  하나(`sp_lineage_stale` — 박는 트리거 · 개정 칸 옮김 · 아래 질의가 모두 그것을 부른다).
- **위에서 낡음** — 원천들 중 하나가 (바로든 위에서든) 낡았다. 계보를 따라 아래로 번진다 — 깊이
  `MAX_DEPTH`(20, 재귀 질의와 같은 상한) 안에서, 여러 계보 종류를 함께, 종류마다 방향을 맞춰
  (원천이 출발인 종류와 도착인 종류가 한 사슬에 섞인다).
- **선은 오늘 살아 있는 것만** — `edges.alive` · `alive_sql` 을 거친다(유효 기간 ADR 0039).
  제안된 관계(ADR 0042)는 따로 둔 표라 선을 고르는 판정에 들지 않는다 — 계보 종류의 제안은
  확정해야 선이 되고, 그때 고정이 박힌다(넣을 때의 트리거).
- **지운 객체는 원천으로 치지 않는다** — 원천이 지워진 선은 낡음을 만들지도 번지지도
  않는다(지운 것이 「바뀌었다」 고 말하면 사람은 되살릴 것인지 다시 만들 것인지 모른다). 지운
  파생물은 목록 · 계보에 안 나오고 아래로 번지지도 않는다.
- 낡음은 **지금의 판정**이다 — 목록의 기준일(`as_of`)과 상관없이 오늘의 선 · 지금 값으로 본다.

## 두 길 — 같은 판정

    stale_sql      전체에서 낡은 것의 집합(SQL 하나 — 낡은 선에서 아래로 재귀). 목록 거르기
                   「낡은 것만」(조건 `f.lineage.eq=stale`) · 통계 · 내보내기 · 영향 범위의 수
    fold           주어진 객체들의 위 사슬을 가져와 파이썬에서 접는다. 계보 화면(위 · 아래 줄의
                   까닭) · 목록의 낡음 표시(쪽에 든 줄만)

둘이 같은 답을 내는지는 시험이 섞인 사슬로 맞춰 본다(`test_lineage.py` 의
`test_두_판정이_같은_것을_낡음으로_본다`). 전체 집합을 늘 세면 목록 한 쪽(줄 50)의 표시에도
모든 계보 선을 훑고, 쪽마다 위로 걸으면 「낡은 것만」 거르기가 줄마다 걸어야 해서 둘을 둔다.

## 다시 고정

「영향 없음 확인」 · 「다시 만들었음」 — 그 파생물의 원천 선(또는 고른 선)의 고정을 지금 값으로
바꾼다(`repin`). 원천 객체는 안 바뀌므로 **그 위의 낡음에는 영향이 없다**. 원천을 고쳐 다시
만들면(원천 객체를 수정) 그 아래가 바로 낡음이 되는 것이 정상이다. 감사(`lineage.repin`, 선마다
앞뒤 개정)와 양 끝의 이력에 남는다. 선의 바뀐 때(`updated_at`)는 올리지 않는다 — 고정은 이 설치
안의 판정이라 코어 창구로 나가지 않고, 올리면 받는 쪽에 안 바뀐 선이 다시 간다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.modules.accounts.models import User
from app.modules.objects import access, edges, graph, system
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.objects.schemas import (
    LineageEndOut,
    LineageImpactOut,
    LineageLineOut,
    LineageOut,
)
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType, RelationType
from app.modules.ontology.services import SLUG_RE
from app.shared import audit
from app.shared.errors import AppError, NotFound, code

MAX_DEPTH = graph.MAX_DEPTH
#: 계보 화면 · API 의 줄 상한(위 · 아래 각각) — 넘으면 `truncated` 와 전체 수를 준다.
LINES_MAX = 500

#: 낡음의 이름 — 화면 · API · MCP 가 같은 글자를 쓴다.
StateName = Literal["fresh", "direct", "upstream"]
#: 다시 고정의 근거.
Basis = Literal["checked", "rebuilt"]
BASIS_LABELS: dict[str, str] = {"checked": "영향 없음 확인", "rebuilt": "다시 만들었음"}


# --- 계보 종류 -------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    slug: str
    label: str
    inverse_label: str
    source: str
    """원천 끝 — `src` · `dst`."""


def kinds(db: Session) -> dict[str, Kind]:
    """계보 관계 종류 — slug → 종류. 없으면 빈 표(대부분의 설치)."""
    return {
        row.slug: Kind(
            slug=row.slug,
            label=row.label,
            inverse_label=row.inverse_label,
            source=row.lineage_source,
        )
        for row in db.scalars(
            select(RelationType).where(RelationType.lineage_source.in_(("src", "dst")))
        )
    }


def _by_end(found: dict[str, Kind]) -> tuple[list[str], list[str]]:
    """(원천이 출발인 종류, 원천이 도착인 종류)."""
    return (
        sorted(one.slug for one in found.values() if one.source == "src"),
        sorted(one.slug for one in found.values() if one.source == "dst"),
    )


def _rows(db: Session, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    """계보 질의를 **JIT 없이** 돌려 줄을 다 읽는다.

    재귀 CTE 의 줄 수를 플래너가 수백만으로 어림해(실제는 수천) 비용이 JIT
    문턱(`jit_above_cost` 100,000)을 넘고, 그러면 식을 기계어로 옮기는 데 0.7초를 쓴다 — 전체
    낡음 집합이 끄면 0.10초 · 켜면 0.80 ~ 0.94초였다(2026-10-30, 결과 5만 · 계보 선 6만). 이
    트랜잭션 안에서만 끄고 되돌린다(`set_config(…, true)`)."""
    before = db.scalar(text("SELECT current_setting('jit')"))
    db.execute(text("SELECT set_config('jit', 'off', true)"))
    try:
        return list(db.execute(text(sql), params or {}).all())
    finally:
        db.execute(text("SELECT set_config('jit', :value, true)"), {"value": before or "on"})


# --- 선 집합 ---------------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    """계보 선 하나 — **원천 → 파생물**로 돌려 세운 것(종류의 방향과 상관없이)."""

    id: uuid.UUID
    relation: str
    source_id: uuid.UUID
    derived_id: uuid.UUID
    depth: int
    """시작 객체에서 몇 걸음(1 이 바로 붙은 선)."""
    pinned_revision: str | None
    pinned_at: datetime | None
    revision_key: str
    """원천 타입의 개정 칸 — 비면 바뀐 때로 판정한 선이다."""
    revision: str | None
    """원천의 지금 개정 값."""
    source_updated_at: datetime
    stale: bool
    """바로 낡음 — `sp_lineage_stale`."""
    src_id: uuid.UUID
    dst_id: uuid.UUID
    """선의 원래 끝(감사 기록 · 이력이 출발 · 도착으로 찾는다)."""


def _step(node: str, *, up: bool, day: date | None) -> str:
    """노드 `node` 에서 한 걸음 — 위로면 그 노드의 원천들, 아래로면 그 파생물들. 종류마다
    방향을 맞춘 두 갈래를 붙인다(각자 출발 · 도착 색인을 탄다). **지운 객체로는 가지 않는다**
    (지운 원천은 원천으로 치지 않는다 · 지운 파생물은 안 나온다). 돌려주는 칸: id(선) ·
    far(다음 노드) · source_id · derived_id.

    ⚠️ 부르는 쪽은 `LATERAL (… OFFSET 0)` 으로 감싼다 — 감싸지 않으면 플래너가 이 갈래를 바깥
       조인으로 펴고, 재귀 CTE 의 줄 수를 못 어림해 객체 표(200만 줄)를 통째로 해시로 읽는다
       (목록 한 쪽 50줄의 낡음 표시가 0.85초였다, 2026-10-30 실측)."""
    alive = edges.alive_sql("e", day)
    if up:
        # 원천이 출발인 종류: 나는 도착, 다음은 출발 / 원천이 도착인 종류: 나는 출발, 다음은
        # 도착.
        return f"""
            SELECT e.id, e.src_object_id AS far, e.src_object_id AS source_id,
                   e.dst_object_id AS derived_id
              FROM object_relations e
              JOIN objects o ON o.id = e.src_object_id AND o.deleted_at IS NULL
             WHERE e.dst_object_id = {node} AND e.relation = ANY(CAST(:by_src AS text[]))
               AND {alive}
            UNION ALL
            SELECT e.id, e.dst_object_id, e.dst_object_id, e.src_object_id
              FROM object_relations e
              JOIN objects o ON o.id = e.dst_object_id AND o.deleted_at IS NULL
             WHERE e.src_object_id = {node} AND e.relation = ANY(CAST(:by_dst AS text[]))
               AND {alive}
        """
    return f"""
        SELECT e.id, e.dst_object_id AS far, e.src_object_id AS source_id,
               e.dst_object_id AS derived_id
          FROM object_relations e
          JOIN objects o ON o.id = e.dst_object_id AND o.deleted_at IS NULL
         WHERE e.src_object_id = {node} AND e.relation = ANY(CAST(:by_src AS text[]))
           AND {alive}
        UNION ALL
        SELECT e.id, e.src_object_id, e.dst_object_id, e.src_object_id
          FROM object_relations e
          JOIN objects o ON o.id = e.src_object_id AND o.deleted_at IS NULL
         WHERE e.dst_object_id = {node} AND e.relation = ANY(CAST(:by_dst AS text[]))
           AND {alive}
    """


#: 계보 선 한 줄에 원천의 지금 값과 판정을 붙인다 — 선 하나에 한 번씩 색인으로(`LIMIT 1` 이
#: 플래너가 바깥 조인으로 펴지 못하게 막는다).
_DETAIL = """
    SELECT r.relation, r.src_object_id, r.dst_object_id, r.pinned_revision, r.pinned_at,
           t.revision_key, sp_revision(s.properties, t.revision_key) AS revision,
           s.updated_at AS source_updated_at, s.deleted_at IS NOT NULL AS source_deleted,
           sp_lineage_stale(t.revision_key, s.properties, s.updated_at,
                            r.pinned_revision, r.pinned_at) AS stale
      FROM object_relations r
      JOIN objects s ON s.id = {source}
      JOIN object_types t ON t.id = s.type_id
     WHERE r.id = {relation}
     LIMIT 1
"""


def links(
    db: Session,
    start: Iterable[uuid.UUID],
    *,
    up: bool,
    found: dict[str, Kind] | None = None,
    limit: int | None = None,
) -> tuple[list[Link], int]:
    """시작 객체들에서 계보를 따라 닿는 **선 전부**(한 번씩) — 위로(원천 사슬) 또는
    아래로(파생물 사슬). (선들 — 얕은 것부터, 전체 선 수).

    노드를 (id, 깊이)로 UNION 해 걷고(`graph.tree_links` 와 같은 무늬), 깊이 `MAX_DEPTH` 보다
    얕은 노드에서 나간 선을 모은다. **지운 객체에서는 멈춘다** — 위로면 지운 원천의 선을 빼고,
    아래로면 지운 파생물로 가는 선을 뺀다(원천으로 치지 않는다)."""
    wanted = sorted(set(start))
    found = kinds(db) if found is None else found
    by_src, by_dst = _by_end(found)
    if not wanted or not (by_src or by_dst):
        return [], 0
    day = edges.today()
    cap = "" if limit is None else "LIMIT :limit"
    rows = _rows(
        db,
        f"""
            WITH RECURSIVE walk(id, depth) AS (
                SELECT s.id, 0 FROM unnest(CAST(:start AS uuid[])) AS s(id)
                UNION
                SELECT n.far, w.depth + 1
                  FROM walk w
                  CROSS JOIN LATERAL (
                      SELECT * FROM ({_step("w.id", up=up, day=day)}) u OFFSET 0
                  ) n
                 WHERE w.depth < :max_depth
            ), nodes AS (
                SELECT id, min(depth) AS depth FROM walk WHERE depth < :max_depth GROUP BY id
            ), found AS (
                SELECT DISTINCT ON (l.id) l.id, l.source_id, l.derived_id, n.depth + 1 AS depth
                  FROM nodes n
                  CROSS JOIN LATERAL (
                      SELECT * FROM ({_step("n.id", up=up, day=day)}) u OFFSET 0
                  ) l
                 ORDER BY l.id, n.depth
            )
            SELECT f.id, f.source_id, f.derived_id, f.depth, x.*, count(*) OVER () AS total
              FROM found f
              CROSS JOIN LATERAL ({_DETAIL.format(source="f.source_id", relation="f.id")}) x
             ORDER BY f.depth, f.id
             {cap}
        """,
        {
            "start": [str(one) for one in wanted],
            "by_src": by_src,
            "by_dst": by_dst,
            "max_depth": MAX_DEPTH,
            "limit": limit,
        },
    )
    total = int(rows[0].total) if rows else 0
    return [
        Link(
            id=row.id,
            relation=row.relation,
            source_id=row.source_id,
            derived_id=row.derived_id,
            depth=int(row.depth),
            pinned_revision=row.pinned_revision,
            pinned_at=row.pinned_at,
            revision_key=row.revision_key or "",
            revision=row.revision,
            source_updated_at=row.source_updated_at,
            stale=bool(row.stale),
            src_id=row.src_object_id,
            dst_id=row.dst_object_id,
        )
        for row in rows
    ], total


# --- 접기 -------------------------------------------------------------------------


@dataclass
class State:
    """객체 하나의 낡음 — 바로(그 원천 선이 낡음) · 위에서(원천이 낡음)."""

    direct: bool = False
    upstream: bool = False
    stale_links: list[Link] = field(default_factory=list)
    """바로 낡게 만든 선."""
    stale_sources: list[uuid.UUID] = field(default_factory=list)
    """낡은 원천(위에서 낡음의 까닭)."""

    @property
    def stale(self) -> bool:
        return self.direct or self.upstream

    @property
    def name(self) -> StateName:
        if self.direct:
            return "direct"
        if self.upstream:
            return "upstream"
        return "fresh"


def fold(found: list[Link], nodes: Iterable[uuid.UUID]) -> dict[uuid.UUID, State]:
    """위 사슬의 선들로 노드마다 낡음을 접는다 — 낡음(n) = 바로(n) 또는 원천 중 하나가 낡음.
    순환(계보를 순환 금지로 두지 않은 종류)은 도는 길에서 낡지 않은 것으로 친다(끝나게)."""
    incoming: dict[uuid.UUID, list[Link]] = defaultdict(list)
    for one in found:
        incoming[one.derived_id].append(one)
    done: dict[uuid.UUID, State] = {}
    walking: set[uuid.UUID] = set()

    def visit(node: uuid.UUID) -> State:
        if node in done:
            return done[node]
        if node in walking:
            return State()
        walking.add(node)
        state = State()
        for one in incoming.get(node, []):
            if one.stale:
                state.direct = True
                state.stale_links.append(one)
            if visit(one.source_id).stale:
                state.upstream = True
                if one.source_id not in state.stale_sources:
                    state.stale_sources.append(one.source_id)
        walking.discard(node)
        done[node] = state
        return state

    for node in {*nodes, *(one.derived_id for one in found)}:
        visit(node)
    return done


def states_of(
    db: Session, ids: Iterable[uuid.UUID], *, found: dict[str, Kind] | None = None
) -> dict[uuid.UUID, State]:
    """그 객체들의 낡음 — 위 사슬을 한 번에 가져와 접는다."""
    wanted = list(set(ids))
    above, _total = links(db, wanted, up=True, found=found)
    folded = fold(above, wanted)
    return {one: folded.get(one, State()) for one in wanted}


def page_states(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, StateName]:
    """목록 한 쪽의 낡음 표시 — **낡은 줄만**(대부분의 설치는 계보 종류가 없어 질의도 없다)."""
    found = kinds(db)
    if not found or not ids:
        return {}
    return {
        one: state.name
        for one, state in states_of(db, ids, found=found).items()
        if state.stale
    }


# --- 전체 집합(SQL) ------------------------------------------------------------------


def _slugs(slugs: list[str]) -> str:
    """종류 slug 목록을 SQL 상수로 — 상수라야 플래너가 그 종류의 선 수를 어림한다. slug 는
    `[a-z][a-z0-9_]*` 라(`SLUG_RE`) 따옴표가 샐 일이 없지만, 아닌 것은 버린다."""
    safe = [one for one in slugs if SLUG_RE.match(one)]
    if not safe:
        return "CAST(ARRAY[] AS text[])"
    return "ARRAY[" + ", ".join(f"'{one}'" for one in safe) + "]::text[]"


def stale_sql(found: dict[str, Kind], *, direct_only: bool = False) -> str:
    """낡은 객체 전부의 id(`SELECT id`) — 바로 낡은 선의 파생물에서 계보를 따라 아래로
    재귀한다. 판정은 `sp_lineage_stale`, 선은 오늘 살아 있는 것만, 지운 원천은 치지 않는다 —
    `links` · `fold` 와 같은 규칙이다. 자리표가 없는 글이라 다른 질의에 그대로 끼운다.

    ⚠️ 객체를 찾는 자리는 모두 `LATERAL … LIMIT 1`(기본 키 하나씩)이다 — 조인으로 두면 재귀의
       줄 수를 못 어림한 플래너가 재귀 한 바퀴마다 객체 표(227만 줄)를 해시로 읽었다(한 바퀴
       1초, 2026-10-30 EXPLAIN)."""
    by_src, by_dst = (_slugs(one) for one in _by_end(found))
    day = edges.today()
    alive = edges.alive_sql("r", day)
    alive_e = edges.alive_sql("e", day)
    stale = """
        CROSS JOIN LATERAL (
            SELECT 1 FROM objects s JOIN object_types t ON t.id = s.type_id
             WHERE s.id = {source} AND s.deleted_at IS NULL
               AND sp_lineage_stale(t.revision_key, s.properties, s.updated_at,
                                    r.pinned_revision, r.pinned_at)
             LIMIT 1
        ) x"""
    seed = f"""
        SELECT r.dst_object_id AS id
          FROM object_relations r {stale.format(source="r.src_object_id")}
         WHERE r.relation = ANY({by_src}) AND {alive}
        UNION
        SELECT r.src_object_id
          FROM object_relations r {stale.format(source="r.dst_object_id")}
         WHERE r.relation = ANY({by_dst}) AND {alive}
    """
    if direct_only:
        return seed
    return f"""
        WITH RECURSIVE seed AS ({seed}), down(id, depth) AS (
            SELECT id, 0 FROM seed
            UNION
            SELECT n.id, d.depth + 1
              FROM down d
              CROSS JOIN LATERAL (
                  SELECT 1 FROM objects o WHERE o.id = d.id AND o.deleted_at IS NULL LIMIT 1
              ) alive_node
              CROSS JOIN LATERAL (
                  SELECT * FROM (
                      SELECT e.dst_object_id AS id
                        FROM object_relations e
                       WHERE e.src_object_id = d.id AND e.relation = ANY({by_src})
                         AND {alive_e}
                      UNION ALL
                      SELECT e.src_object_id
                        FROM object_relations e
                       WHERE e.dst_object_id = d.id AND e.relation = ANY({by_dst})
                         AND {alive_e}
                  ) u OFFSET 0
              ) n
             WHERE d.depth < {MAX_DEPTH}
        )
        SELECT DISTINCT id FROM down
    """


#: 조건 `f.lineage.<연산>=<값>` 의 값 — 낡음(바로 · 위에서) · 바로 낡음 · 낡지 않음.
CONDITION_VALUES: dict[str, str] = {
    "stale": "낡음(바로 · 위에서)",
    "direct": "바로 낡음",
    "fresh": "낡지 않음",
}


#: 한 요청(세션) 안에서 전체 낡음 집합을 한 번만 — 목록은 같은 조건으로 세고(수) 읽고(쪽)
#: 진단은 조건 하나씩 빼 보며 여러 번 짓는다. 다시 고정 · 복원이 지운다.
_STALE_CACHE = "lineage_stale_ids"


def stale_ids(db: Session, *, direct_only: bool = False) -> list[uuid.UUID]:
    """낡은 객체 전부의 id — `stale_sql` 을 JIT 없이 한 번(세션마다 기억)."""
    cache: dict[tuple[bool, date], list[uuid.UUID]] = db.info.setdefault(_STALE_CACHE, {})
    key = (direct_only, edges.today())
    if key not in cache:
        found = kinds(db)
        cache[key] = (
            [row[0] for row in _rows(db, stale_sql(found, direct_only=direct_only))]
            if found
            else []
        )
    return cache[key]


def forget(db: Session) -> None:
    """고정이 바뀌었다 — 이 세션이 기억한 낡음 집합을 버린다."""
    db.info.pop(_STALE_CACHE, None)


def condition(db: Session, entity: Any, op: str, value: str) -> ColumnElement[bool]:
    """목록 · 통계 · 내보내기 · 저장된 뷰 · 지표의 조건 「낡은 것만」 — 그 객체가 낡은 것의
    집합에 드나. `eq`(값 하나) · `ne`(아님). 값은 `CONDITION_VALUES`.

    집합은 **먼저 따로 센다**(`stale_ids`) — 바깥 질의에 재귀 CTE 를 그대로 넣으면 그 질의
    전체의 비용 어림이 부풀어(재귀의 줄 수) JIT 가 켜지고, 목록 한 번에 0.7초를 컴파일에
    쓴다."""
    wanted = value.strip()
    if wanted not in CONDITION_VALUES:
        raise AppError(
            code("OBJECTS", 132),
            f"계보 조건의 값은 {', '.join(CONDITION_VALUES)} 중 하나입니다: {value!r}",
            status=422,
        )
    if op not in ("eq", "ne"):
        raise AppError(
            code("OBJECTS", 132),
            f"계보 조건에는 같음(eq) · 다름(ne)만 걸립니다: {op}",
            status=422,
        )
    hit = graph.any_of(entity.id, stale_ids(db, direct_only=wanted == "direct"))
    positive = (op == "eq") != (wanted == "fresh")
    return hit if positive else ~hit


# --- 영향 범위 -------------------------------------------------------------------


@dataclass(frozen=True)
class Impact:
    derived: int
    """이것으로 만든 것(아래 사슬 — 지우지 않은 것, 깊이 상한 안)."""
    stale: int
    """그중 낡은 것(바로 · 위에서 — 다른 원천 때문이어도)."""


#: 영향 범위를 파이썬으로 접는 상한(아래 선 수) — 넘으면 전체 집합(SQL)으로 센다.
IMPACT_FOLD_MAX = 2000


def impact(
    db: Session,
    object_id: uuid.UUID,
    *,
    found: dict[str, Kind] | None = None,
    below: list[Link] | None = None,
) -> Impact:
    """원천 하나에서 「이것으로 만든 것 n개(낡음 m개)」.

    아래 사슬이 작으면(선 `IMPACT_FOLD_MAX` 이하 — 거의 모두) 그 파생물들의 위 사슬을 접어
    세고(`below` 를 받았으면 다시 안 읽는다), 크면 전체 집합(`stale_sql`)과 견준다. 두 길은
    같은 판정이다(시험이 맞춰 본다). 전체 집합은 모든 계보 선을 훑으므로 작은 사슬에 쓰면 원천
    하나의 화면이 그 값을 다 치른다."""
    found = kinds(db) if found is None else found
    by_src, by_dst = _by_end(found)
    if not (by_src or by_dst):
        return Impact(derived=0, stale=0)
    if below is None:
        below, total = links(db, [object_id], up=False, found=found, limit=IMPACT_FOLD_MAX)
        if total > IMPACT_FOLD_MAX:
            below = None
    if below is not None:
        lower = {one.derived_id for one in below} - {object_id}
        states = states_of(db, lower, found=found)
        return Impact(
            derived=len(lower), stale=sum(1 for state in states.values() if state.stale)
        )
    day = edges.today()
    row = _rows(
        db,
        f"""
            WITH RECURSIVE walk(id, depth) AS (
                SELECT CAST(:start AS uuid), 0
                UNION
                SELECT n.far, w.depth + 1
                  FROM walk w
                  CROSS JOIN LATERAL (
                      SELECT * FROM ({_step("w.id", up=False, day=day)}) u OFFSET 0
                  ) n
                 WHERE w.depth < :max_depth
            ), below AS (
                SELECT DISTINCT id FROM walk WHERE depth > 0 AND id <> CAST(:start AS uuid)
            )
            SELECT count(*) AS derived,
                   count(*) FILTER (WHERE id IN ({stale_sql(found)})) AS stale
              FROM below
        """,
        {
            "start": str(object_id),
            "by_src": by_src,
            "by_dst": by_dst,
            "max_depth": MAX_DEPTH,
        },
    )[0]
    return Impact(derived=int(row.derived or 0), stale=int(row.stale or 0))


# --- 사람이 읽을 것 ----------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    """줄에 실을 객체 하나 — 못 보는 것은 가린다."""

    id: uuid.UUID
    label: str
    key: str | None
    type_slug: str
    type_label: str
    revision_key: str
    hidden: bool = False

    @property
    def name(self) -> str:
        """「메시 M-12」 — 까닭 글에 쓴다."""
        if self.hidden:
            return system.HIDDEN_LABEL
        return f"{self.type_label} {self.label}".strip()


def nodes(db: Session, viewer: User | None, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Node]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    hidden = system.hidden_objects(db, viewer, wanted)
    rows = db.execute(
        select(
            ObjectInstance.id,
            ObjectInstance.label,
            ObjectInstance.key,
            ObjectType.slug,
            ObjectType.label,
            ObjectType.revision_key,
        )
        .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
        .where(graph.any_of(ObjectInstance.id, wanted))
    ).all()
    return {
        row[0]: Node(
            id=row[0],
            label=row[1],
            key=row[2],
            type_slug=row[3],
            type_label=row[4],
            revision_key=row[5] or "",
            hidden=row[0] in hidden,
        )
        for row in rows
    }


def _when(at: datetime | None) -> str:
    return at.astimezone().strftime("%Y-%m-%d %H:%M") if at else "(모름)"


def _value(raw: str | None) -> str:
    if raw is None:
        return "(없음)"
    return raw if raw.strip() else "(빈 값)"


def reason(one: Link, source: Node | None) -> str:
    """바로 낡은 선 하나의 까닭 — 「메시 M-12 의 개정 Rev.B → Rev.C」."""
    name = source.name if source is not None else system.HIDDEN_LABEL
    if one.revision_key:
        return f"{name} 의 개정 {_value(one.pinned_revision)} → {_value(one.revision)}"
    return f"{name} 이(가) 고정 뒤에 수정됨({_when(one.source_updated_at)})"


def reasons(state: State, by_id: dict[uuid.UUID, Node]) -> list[str]:
    out = [reason(one, by_id.get(one.source_id)) for one in state.stale_links]
    for source in state.stale_sources:
        node = by_id.get(source)
        out.append(f"원천 {node.name if node else system.HIDDEN_LABEL} 이(가) 낡음")
    return out


# --- 다시 고정 ---------------------------------------------------------------------


@dataclass(frozen=True)
class Pin:
    revision: str | None
    at: datetime | None

    def out(self) -> dict[str, Any]:
        return {"revision": self.revision, "at": self.at.isoformat() if self.at else None}


@dataclass(frozen=True)
class Repinned:
    relation_id: uuid.UUID
    relation: str
    source_id: uuid.UUID
    before: Pin
    after: Pin


def source_links(
    db: Session, derived_id: uuid.UUID, *, found: dict[str, Kind] | None = None
) -> list[Link]:
    """그 파생물의 원천 선(한 걸음) — 다시 고정할 수 있는 선."""
    above, _total = links(db, [derived_id], up=True, found=found)
    return [one for one in above if one.derived_id == derived_id]


def blocker(
    db: Session, user: User, derived: ObjectInstance, chosen: list[Link]
) -> AppError | None:
    """다시 고정할 수 없으면 그 까닭 — 파생물을 고칠 수 있어야 하고(그 선이 파생물의 것이다),
    허브가 관리하는 계보 종류는 허브에서만 바뀐다."""
    object_type = db.get(ObjectType, derived.type_id)
    if object_type is None:  # pragma: no cover - FK 가 막는다
        return NotFound(code("OBJECTS", 133), "타입이 없습니다.")
    try:
        access.require_edit(db, user, object_type, derived, code_value=code("OBJECTS", 134))
        for slug in sorted({one.relation for one in chosen}):
            kind = db.scalar(select(RelationType).where(RelationType.slug == slug))
            if kind is not None:
                managed.require_relation_editable(kind)
    except AppError as denied:
        return denied
    return None


def repin(
    db: Session,
    user: User,
    derived: ObjectInstance,
    *,
    relation_ids: list[uuid.UUID] | None = None,
    basis: Basis = "checked",
    note: str = "",
    reason_text: str | None = None,
) -> list[Repinned]:
    """그 파생물의 원천 선(`relation_ids` 를 주면 그것만)의 고정을 **지금 값으로** — 바뀐 선만
    쓰고 감사에 남긴다. **커밋하지 않는다.** 원천 객체는 안 바뀐다 — 그 위의 낡음은
    그대로다."""
    found = kinds(db)
    mine = source_links(db, derived.id, found=found)
    if relation_ids is not None:
        by_id = {one.id: one for one in mine}
        strangers = [str(one) for one in relation_ids if one not in by_id]
        if strangers:
            raise NotFound(
                code("OBJECTS", 135),
                f"{derived.label}의 원천 계보 선이 아닙니다: {', '.join(strangers)} — "
                "계보 화면의 위(원천) 줄에서 고르세요.",
            )
        mine = [by_id[one] for one in dict.fromkeys(relation_ids)]
    denied = blocker(db, user, derived, mine)
    if denied is not None:
        raise denied
    changed = [
        one
        for one in mine
        if (one.pinned_revision, one.pinned_at) != (one.revision, one.source_updated_at)
    ]
    labels = nodes(db, None, [derived.id, *(one.source_id for one in changed)])
    said = BASIS_LABELS.get(basis, basis)
    forget(db)
    out: list[Repinned] = []
    for one in changed:
        _write_pin(db, one.id, one.revision, one.source_updated_at)
        before = Pin(one.pinned_revision, one.pinned_at)
        after = Pin(one.revision, one.source_updated_at)
        _audit(
            db,
            user,
            one,
            labels,
            derived,
            before=before,
            after=after,
            reason=reason_text or (f"다시 고정 — {said}" + (f": {note}" if note else "")),
        )
        out.append(
            Repinned(
                relation_id=one.id,
                relation=one.relation,
                source_id=one.source_id,
                before=before,
                after=after,
            )
        )
    return out


def restore(
    db: Session,
    user: User,
    derived: ObjectInstance,
    pins: dict[uuid.UUID, Pin],
    *,
    apply: bool,
    reason_text: str,
) -> tuple[int, list[str]]:
    """고정을 앞 값으로 되돌린다(검토함의 되돌리기) — 그 사이 사라진 선은 건너뛰고 말한다.
    (되돌린 선 수, 건너뛴 까닭들). `apply` 가 아니면 세기만 한다. **커밋하지 않는다.**"""
    mine = {one.id: one for one in source_links(db, derived.id)}
    skipped = [f"선 {one} 이(가) 없거나 계보 선이 아닙니다" for one in pins if one not in mine]
    chosen = [mine[one] for one in pins if one in mine]
    denied = blocker(db, user, derived, chosen)
    if denied is not None:
        raise denied
    if not apply:
        return len(chosen), skipped
    labels = nodes(db, None, [derived.id, *(one.source_id for one in chosen)])
    forget(db)
    for one in chosen:
        pin = pins[one.id]
        _write_pin(db, one.id, pin.revision, pin.at)
        _audit(
            db,
            user,
            one,
            labels,
            derived,
            before=Pin(one.pinned_revision, one.pinned_at),
            after=pin,
            reason=reason_text,
        )
    return len(chosen), skipped


def _write_pin(
    db: Session, relation_id: uuid.UUID, revision: str | None, at: datetime | None
) -> None:
    """고정 두 칸만 — 선의 바뀐 때(`updated_at`)는 그대로 둔다(`onupdate` 를 막는다)."""
    db.execute(
        update(ObjectRelation)
        .where(ObjectRelation.id == relation_id)
        .values(
            pinned_revision=revision,
            pinned_at=at,
            updated_at=ObjectRelation.updated_at,
        )
    )


def _audit(
    db: Session,
    user: User,
    one: Link,
    labels: dict[uuid.UUID, Node],
    derived: ObjectInstance,
    *,
    before: Pin,
    after: Pin,
    reason: str,
) -> None:
    def label(object_id: uuid.UUID) -> str:
        node = labels.get(object_id)
        return node.label if node is not None else ""

    changes = audit.relation_ends(
        one.relation, one.src_id, one.dst_id, label(one.src_id), label(one.dst_id)
    )
    changes["changed"] = {
        "pinned_revision": {"before": before.revision, "after": after.revision},
        "pinned_at": {
            "before": before.at.isoformat() if before.at else None,
            "after": after.at.isoformat() if after.at else None,
        },
    }
    audit.record(
        db,
        action="lineage.repin",
        actor=user,
        target_table="object_relations",
        target_id=one.id,
        target_label=f"{derived.label} ← {label(one.source_id)}",
        workspace_id=derived.owner_workspace_id,
        changes=changes,
        reason=reason,
    )


# --- 검토함이 읽는 것 ----------------------------------------------------------------


def direct_links(
    db: Session,
    *,
    derived_ids: list[uuid.UUID] | None = None,
    limit: int = 5000,
) -> tuple[list[Link], int]:
    """**바로 낡은** 계보 선 전부(파생물이 지워지지 않은 것) — 검토함이 파생물마다 묶는다.
    (선들 — 파생물 차례, 전체 수)."""
    found = kinds(db)
    by_src, by_dst = _by_end(found)
    if not (by_src or by_dst):
        return [], 0
    alive = edges.alive_sql("r", edges.today())
    narrow = "AND l.derived_id = ANY(CAST(:ids AS uuid[]))" if derived_ids is not None else ""
    # 선마다 원천 · 파생물을 색인으로 하나씩(`LATERAL … LIMIT 1`) — 조인으로 두면 플래너가 객체
    # 표 기본 키 색인을 통째로 훑는 병합 조인을 골랐다(계보 선 6만에 6.9초, 2026-10-30 실측).
    rows = _rows(
        db,
        f"""
            WITH l AS (
                SELECT r.id, r.src_object_id AS source_id, r.dst_object_id AS derived_id
                  FROM object_relations r
                 WHERE r.relation = ANY(CAST(:by_src AS text[])) AND {alive}
                UNION ALL
                SELECT r.id, r.dst_object_id, r.src_object_id
                  FROM object_relations r
                 WHERE r.relation = ANY(CAST(:by_dst AS text[])) AND {alive}
            )
            SELECT l.id, l.source_id, l.derived_id, x.*, count(*) OVER () AS total
              FROM l
              CROSS JOIN LATERAL ({_DETAIL.format(source="l.source_id", relation="l.id")}) x
              CROSS JOIN LATERAL (
                  SELECT 1 FROM objects d
                   WHERE d.id = l.derived_id AND d.deleted_at IS NULL LIMIT 1
              ) alive_derived
             WHERE x.stale AND NOT x.source_deleted {narrow}
             ORDER BY l.derived_id, l.id
             LIMIT :limit
        """,
        {
            "by_src": by_src,
            "by_dst": by_dst,
            "ids": [str(one) for one in derived_ids or []],
            "limit": limit,
        },
    )
    total = int(rows[0].total) if rows else 0
    return [
        Link(
            id=row.id,
            relation=row.relation,
            source_id=row.source_id,
            derived_id=row.derived_id,
            depth=1,
            pinned_revision=row.pinned_revision,
            pinned_at=row.pinned_at,
            revision_key=row.revision_key or "",
            revision=row.revision,
            source_updated_at=row.source_updated_at,
            stale=True,
            src_id=row.src_object_id,
            dst_id=row.dst_object_id,
        )
        for row in rows
    ], total


# --- 화면 · API 의 한 벌 ------------------------------------------------------------


def _end(node: Node | None, object_id: uuid.UUID, state: State | None) -> LineageEndOut:
    name = state.name if state is not None else "fresh"
    if node is None or node.hidden:
        return LineageEndOut(id=None, label=system.HIDDEN_LABEL, state=name, hidden=True)
    return LineageEndOut(
        id=object_id,
        label=node.label,
        key=node.key,
        type_slug=node.type_slug,
        type_label=node.type_label,
        state=name,
    )


def _lines(
    found: list[Link],
    by_id: dict[uuid.UUID, Node],
    states: dict[uuid.UUID, State],
    labels: dict[str, str],
) -> list[LineageLineOut]:
    out: list[LineageLineOut] = []
    for one in found:
        source = by_id.get(one.source_id)
        out.append(
            LineageLineOut(
                relation_id=one.id,
                relation=one.relation,
                relation_label=labels.get(one.relation, one.relation),
                depth=one.depth,
                source=_end(source, one.source_id, states.get(one.source_id)),
                derived=_end(
                    by_id.get(one.derived_id), one.derived_id, states.get(one.derived_id)
                ),
                revision_key=one.revision_key,
                pinned_revision=one.pinned_revision,
                pinned_at=one.pinned_at,
                revision=one.revision,
                source_updated_at=one.source_updated_at,
                stale=one.stale,
                reason=reason(one, source) if one.stale else "",
            )
        )
    return out


def view(db: Session, user: User, row: ObjectInstance, object_type: ObjectType) -> LineageOut:
    """객체 하나의 계보 — 위 · 아래 줄(상한 `LINES_MAX`), 줄마다 양 끝의 낡음, 이 객체의 낡음과
    까닭, 영향 범위. 위 사슬은 다 가져와 접고(조상은 대개 적다), 아래 사슬은 상한까지 가져와 그
    줄들의 위 사슬로 다시 접는다(아래 객체의 다른 원천 때문에 낡은 것도 맞게)."""
    found = kinds(db)
    # 개정 값은 판정과 같은 식으로(`sp_revision` — 숫자 3 은 「3」).
    revision_value = (
        db.scalar(
            text("SELECT sp_revision(properties, :key) FROM objects WHERE id = :id"),
            {"key": object_type.revision_key, "id": row.id},
        )
        if object_type.revision_key
        else None
    )
    if not found:
        return LineageOut(
            object_id=row.id,
            type_slug=object_type.slug,
            label=row.label,
            state="fresh",
            reasons=[],
            revision_key=object_type.revision_key or "",
            revision=revision_value,
            up=[],
            up_total=0,
            down=[],
            down_total=0,
            impact=LineageImpactOut(derived=0, stale=0),
            kinds=[],
            note="이 설치에는 계보 관계 종류가 없습니다 — 관계 종류 편집에서 「계보 관계」 를 "
            "켜면 「무엇에서 만들어졌나」 를 잇습니다.",
        )
    above, up_total = links(db, [row.id], up=True, found=found)
    below, down_total = links(db, [row.id], up=False, found=found, limit=LINES_MAX)
    lower = {one.derived_id for one in below}
    states = fold(above, [row.id])
    if lower:
        states.update(states_of(db, lower, found=found))
    shown_above = above[:LINES_MAX]
    by_id = nodes(
        db,
        user,
        {
            row.id,
            *(one.source_id for one in [*shown_above, *below]),
            *(one.derived_id for one in [*shown_above, *below]),
            *(src for state in states.values() for src in state.stale_sources),
        },
    )
    labels = {slug: one.label for slug, one in found.items()}
    mine = states.get(row.id, State())
    # 아래 사슬을 다 가져왔으면 이미 접은 것으로 센다 — 잘렸으면 영향 범위가 따로 센다.
    counted = (
        Impact(
            derived=len(lower - {row.id}),
            stale=sum(1 for one in lower - {row.id} if states.get(one, State()).stale),
        )
        if down_total <= LINES_MAX
        else impact(db, row.id, found=found)
    )
    can = blocker(db, user, row, [one for one in above if one.derived_id == row.id]) is None
    return LineageOut(
        object_id=row.id,
        type_slug=object_type.slug,
        label=row.label,
        state=mine.name,
        reasons=reasons(mine, by_id),
        revision_key=object_type.revision_key or "",
        revision=revision_value,
        up=_lines(shown_above, by_id, states, labels),
        up_total=up_total,
        down=_lines(below, by_id, states, labels),
        down_total=down_total,
        truncated=up_total > LINES_MAX or down_total > LINES_MAX,
        impact=LineageImpactOut(derived=counted.derived, stale=counted.stale),
        can_repin=can,
        kinds=sorted(found),
        note=""
        if above or below
        else "이 객체는 계보 관계로 이어진 원천도 파생물도 없습니다.",
    )


# --- 묶음이 싣는 고정 ----------------------------------------------------------------


@dataclass(frozen=True)
class PinState:
    """선 하나의 고정 판정 — 원천 타입의 개정 칸과 지금 바로 낡았나."""

    revision_key: str
    revision: str | None
    pinned_revision: str | None
    stale: bool


def pin_states(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, PinState]:
    """계보 선들의 지금 판정 — 묶음 내보내기(싣는 낡음)와 가져오기(실려 온 고정과 견주기)가
    쓴다. 계보 종류가 아닌 선은 안 든다."""
    wanted = sorted({str(one) for one in ids})
    if not wanted:
        return {}
    rows = db.execute(
        text("""
            SELECT r.id, t.revision_key, sp_revision(s.properties, t.revision_key) AS revision,
                   r.pinned_revision,
                   sp_lineage_stale(t.revision_key, s.properties, s.updated_at,
                                    r.pinned_revision, r.pinned_at) AS stale
              FROM object_relations r
              JOIN relation_types k
                ON k.slug = r.relation AND k.lineage_source IN ('src', 'dst')
              JOIN objects s ON s.id = CASE k.lineage_source WHEN 'src' THEN r.src_object_id
                                                             ELSE r.dst_object_id END
              JOIN object_types t ON t.id = s.type_id
             WHERE r.id = ANY(CAST(:ids AS uuid[]))
        """),
        {"ids": wanted},
    )
    return {
        row.id: PinState(
            revision_key=row.revision_key or "",
            revision=row.revision,
            pinned_revision=row.pinned_revision,
            stale=bool(row.stale),
        )
        for row in rows
    }


def settle_carried(db: Session, carried: dict[uuid.UUID, bool]) -> None:
    """묶음이 실어 온 고정 중 **원천 타입에 개정 칸이 없는 선**을 이 설치의 시계로 옮긴다.

    바뀐 때로 판정하는 고정은 그 설치의 시계다 — 허브의 고정 시각을 그대로 두면 받는 쪽 원천의
    바뀐 때(가져온 때)가 늘 그보다 뒤라 모든 선이 낡음이 된다(그리고 허브가 관리하는 종류라
    받는 쪽은 다시 고정할 수도 없다). 그래서 **판정을 옮긴다** — 허브에서 낡지 않았던 선은
    원천의 지금 바뀐 때로, 낡았던 선(`pinned_stale`)은 원천의 바뀐 때보다 앞으로. 개정 칸이
    있는 선은 개정 값이 설치를 건너도 같은 뜻이라 실린 그대로 둔다."""
    if not carried:
        return
    ids = sorted(carried)
    db.execute(
        text("""
            UPDATE object_relations r
               SET pinned_revision = NULL,
                   pinned_at = CASE
                       WHEN c.stale
                       THEN least(r.pinned_at, s.updated_at - interval '1 microsecond')
                       ELSE s.updated_at END
              FROM unnest(CAST(:ids AS uuid[]), CAST(:stale AS boolean[])) AS c(id, stale),
                   relation_types k, objects s, object_types t
             WHERE r.id = c.id AND k.slug = r.relation AND k.lineage_source IN ('src', 'dst')
               AND s.id = CASE k.lineage_source WHEN 'src' THEN r.src_object_id
                                                ELSE r.dst_object_id END
               AND t.id = s.type_id AND t.revision_key = ''
        """),
        {"ids": [str(one) for one in ids], "stale": [carried[one] for one in ids]},
    )
