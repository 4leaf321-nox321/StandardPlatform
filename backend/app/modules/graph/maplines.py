"""지도의 선 — 기준정보끼리(멀리 보기)와 확대한 점의 선(ADR 0046 「선」).

지도는 기록과 그 허브를 **자리**로 잇는다 — 기록은 주 참조가 가리키는 기준정보의 원반 안에
놓인다. 그 밖의 관계는 선으로 긋는다.

- **기준정보끼리** — 참조 칸과 오늘 살아 있는 관계 중 양 끝이 다 보이는 것. 판에 적지 않고
  멀리 보기를 만들 때 읽는다(2,000만 건 DB — 기준정보 1.3만 개 · 선 2.3만 줄 — 에서 참조
  5ms · 관계 18ms). 멀리 보기는 ETag(판 · 집계 시각)가 같으면 다시 만들지 않으므로 **새 선은
  다음 집계(한 시간 안)부터** 보인다 — 원반의 수와 같은 신선도다.
- **확대한 점** — 상자의 점이 `LINES_UP_TO` 이하일 때만, 그 점에서 나가거나 들어오는 참조 ·
  관계 중 다른 끝도 그림에 있는 것(상자의 점이거나 보이는 기준정보). 점마다 참조 색인을
  찾는데, 2,000만 건 DB 에서 점 5만 개면 캐시가 식었을 때 11.5초 · 데워졌을 때 0.28초였다.
  그래서 이름표와 같은 문턱(5천)에서 끊는다 — 선 수십만 줄은 그려도 읽을 수 없기도 하다.

선의 종류는 정적 그리기(ADR 0045)와 같은 이름을 쓴다 — 관계 종류의 slug, 참조 칸은
`refedges.slug_of`(`ref:<타입>.<칸>`). 원 표 링크(부서 · 계정)는 그 끝이 지도에 없어 긋지
않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import ARRAY, any_, cast, func, literal_column, select, true
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from app.modules.graph.models import MapMaster
from app.modules.objects import edges as edge_rule
from app.modules.objects import refedges
from app.modules.objects.models import ObjectRef, ObjectRelation
from app.modules.ontology.models import RelationType

#: 확대한 상자의 점이 이 수 이하일 때만 점의 선을 싣는다 — 이름표(`mapread.LABELS_UP_TO`)와
#: 같은 문턱이다(머리 주석의 실측).
LINES_UP_TO = 5_000
#: 점의 선이 이보다 많으면 싣지 않고 그렇다고 말한다 — 여러 값 칸이 수백 개씩 가리키는 기록.
MAX_LINES = 200_000


@dataclass
class Kind:
    slug: str
    label: str
    inverse_label: str
    directed: bool


def kinds(db: Session, type_ids: Iterable[uuid.UUID]) -> list[Kind]:
    """지도에 나올 수 있는 선의 종류 — 관계 종류 전부와 지도에 나오는 타입(`type_ids`)의 참조
    칸, slug 차례. 확대한 점의 선은 slug 만 싣고 이름은 이 표(멀리 보기)에서 찾는다."""
    wanted = set(type_ids)
    out = [
        Kind(row.slug, row.label, row.inverse_label or "", row.directed)
        for row in db.scalars(select(RelationType))
    ]
    out += [
        Kind(one.slug, one.label, one.inverse_label, one.directed)
        for one in refedges.kinds(db).values()
        if one.src_type.id in wanted
    ]
    return sorted(out, key=lambda one: one.slug)


@dataclass
class Lines:
    """선 — 열 배열. `src[k]` → `dst[k]` 가 `kind[k]`(slug) 종류의 선이다."""

    src: list[int] = field(default_factory=list)
    dst: list[int] = field(default_factory=list)
    kind: list[str] = field(default_factory=list)

    def add(self, a: int | None, b: int | None, slug: str) -> None:
        if a is not None and b is not None and a != b:
            self.src.append(a)
            self.dst.append(b)
            self.kind.append(slug)


def _ref_slugs(db: Session) -> dict[tuple[uuid.UUID, str], str]:
    """(타입 id, 칸) → 참조 칸의 선 종류 — 양 끝이 객체 타입인 칸만(`refedges.kinds`)."""
    return {(one.src_type.id, one.key): one.slug for one in refedges.kinds(db).values()}


def masters(db: Session, at: dict[uuid.UUID, int], type_ids: list[uuid.UUID]) -> Lines:
    """기준정보끼리의 선 — `at` 은 멀리 보기에 실은 기준정보(보이고 살아 있는 것)의 번호,
    `type_ids` 는 판의 기준정보 타입. 양 끝이 다 `at` 에 있는 것만."""
    out = Lines()
    if not at or not type_ids:
        return out
    slugs = _ref_slugs(db)
    ref = ObjectRef
    # 타입은 목록으로 준다 — 부분 질의(`IN (SELECT …)`)로 주면 참조 표를 통째로 훑었다
    # (2,000만 건 DB 4.3초 → 5ms).
    for src, key, dst, type_id in db.execute(
        select(ref.src_id, ref.key, ref.dst_id, ref.src_type_id).where(
            ref.src_type_id.in_(type_ids)
        )
    ):
        slug = slugs.get((type_id, key))
        if slug is not None:
            out.add(at.get(src), at.get(dst), slug)
    rel = ObjectRelation
    placed = select(MapMaster.object_id)
    for slug, src, dst in db.execute(
        select(rel.relation, rel.src_object_id, rel.dst_object_id).where(
            edge_rule.alive(rel, edge_rule.at(None)),
            rel.src_object_id.in_(placed),
            rel.dst_object_id.in_(placed),
        )
    ):
        out.add(at.get(src), at.get(dst), slug)
    return out


@dataclass
class PointLines(Lines):
    """확대한 점의 선 — 끝 번호가 점 수보다 작으면 그 점, 아니면 `masters[번호 - 점 수]`."""

    masters: list[uuid.UUID] = field(default_factory=list)


def points(
    db: Session,
    ids: list[uuid.UUID],
    master_types: list[uuid.UUID],
    visible: Callable[[list[uuid.UUID]], set[uuid.UUID]],
) -> PointLines | None:
    """상자의 점(`ids`)에서 나가거나 들어오는 참조 · 관계 중 다른 끝도 그림에 있는 것 — 상자의
    점이거나 `visible` 이 돌려준 기준정보(판에 있고 보는 사람에게 보이는 것). 상자 밖 기록과
    잇는 선은 긋지 않는다. 선이 `MAX_LINES` 를 넘으면 None.

    들어오는 참조는 **기준정보(`master_types`)가 가리킨 것만** 찾는다 — 상자 밖 기록에서 오는
    선은 어차피 안 긋고, 상자 안 기록에서 오는 것은 나가는 쪽에서 이미 찾았다. 도착 칸으로만
    물으면 플래너가 「점 하나에 수천 줄」 로 어림해(도착 칸은 값 가짓수가 적다 — 거의
    기준정보를 가리킨다) 참조 표 5천만 줄을 통째로 훑었다(2,000만 DB 4.1초). 관계는 점마다
    색인을 찾게 묶는다(LATERAL · `OFFSET 0`) — 같은 어림이 관계 표에도 걸린다."""
    out = PointLines()
    if not ids:
        return out
    arr = cast(ids, ARRAY(PgUUID(as_uuid=True)))
    slugs = _ref_slugs(db)
    ref = ObjectRef
    rel = ObjectRelation
    columns = (ref.src_id, ref.key, ref.dst_id, ref.src_type_id)
    asked = [select(*columns).where(ref.src_id == any_(arr))]
    if master_types:
        asked.append(
            select(*columns).where(ref.src_type_id.in_(master_types), ref.dst_id == any_(arr))
        )
    found: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    for stmt in asked:
        rows = db.execute(stmt.limit(MAX_LINES + 1)).all()
        if len(rows) > MAX_LINES:
            return None
        for src, key, dst, type_id in rows:
            slug = slugs.get((type_id, key))
            if slug is not None:
                found.add((src, dst, slug))
    q = func.unnest(arr).table_valued("id").render_derived(name="q")
    for end in (rel.src_object_id, rel.dst_object_id):
        hit = (
            select(
                rel.relation.label("kind"),
                rel.src_object_id.label("src"),
                rel.dst_object_id.label("dst"),
            )
            .where(end == q.c.id, edge_rule.alive(rel, edge_rule.at(None)))
            .offset(literal_column("0"))
            .lateral("r")
        )
        related = db.execute(
            select(hit.c.kind, hit.c.src, hit.c.dst)
            .select_from(q)
            .join(hit, true())
            .limit(MAX_LINES + 1)
        ).all()
        if len(related) > MAX_LINES:
            return None
        found.update((src, dst, slug) for slug, src, dst in related)
    if len(found) > MAX_LINES:
        return None
    index = {one: k for k, one in enumerate(ids)}
    others = sorted({end for src, dst, _ in found for end in (src, dst)} - set(index), key=str)
    shown = visible(others) if others else set()
    out.masters = [one for one in others if one in shown]
    index.update({one: len(ids) + j for j, one in enumerate(out.masters)})
    for src, dst, slug in sorted(found, key=lambda one: (one[2], str(one[0]), str(one[1]))):
        out.add(index.get(src), index.get(dst), slug)
    return out
