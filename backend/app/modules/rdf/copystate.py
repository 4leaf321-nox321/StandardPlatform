"""SPARQL 사본의 **지문**(ADR 0071) — 무엇이 실렸는지를 사본 곁에 두고, 밤마다 지금 DB 와
견준다.

    <slot>/state.sqlite   objects(id · 지문 · IRI) · relations(id · 지문 · 끝 둘 · 종류) · meta

**왜 시각(`updated_at`)이 아니라 지문인가** — 사본에 실리는 것이 바뀌어도 시각이 안 바뀌는 길이
있다. 선의 고정(`pinned_*`)은 일부러 `updated_at` 을 그대로 두고(`lineage._write_pin` ·
허브에서 실어 온 선의 판정 옮기기), 기간이 있는 선은 **날짜만 지나** 유효해지거나 끝나고,
트리거 · 일괄 SQL 이 행을 고친다. 시각으로 고르면 그 하나하나가 사본에서 빠진다 — 그리고 빠진
것을 아무도 모른다. 지문은 **사본에 실리는 칸 그대로**에서 짓는다(DB 가 계산한다 —
`md5`). 무엇이 그 칸을 고쳤든 다르면 다르다.

- 객체: 타입 · 식별자 · 이름 · 설명 · 값(`properties`) · 사람이 붙인 별칭 — 참조 색인
  (`object_refs`)은 값 · 타입 · 삭제에서 트리거가 만든다(`refindex`)라 값에 이미 들어 있다.
  IRI 를 함께 둔다 — 식별자 · 타입이 바뀌면 옛 IRI 로 지워야 한다.
- 관계: 오늘 유효하고 두 끝이 살아 있는 선만 — 종류 · 두 끝 · 기간 · 고정. 날짜가 지나 유효해진
  선은 「새로 생긴 선」 으로, 끝난 선은 「사라진 선」 으로 저절로 잡힌다.
- 정의: 타입 · 칸 · 인터페이스 · 관계 종류의 행 전부(화면 배치 · 시각 칸은 뺀다 — `UI_ONLY`).
  다르면 그날은 통째로.

지문 · IRI 는 **사본을 내보낸 그 트랜잭션(REPEATABLE READ)에서** 짓는다 — 다른 때에 지으면 그
사이 바뀐 것이 사본에는 없는데 지문에는 있어, 다음 밤에 「안 바뀜」 으로 보고 영영 빠진다.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import version
from app.modules.objects import aliases
from app.modules.rdf.copyexport import (
    Shape,
    alias_lines,
    alive_objects,
    alive_relations,
    object_blocks,
    ref_lines,
    relation_blocks,
)

#: 지문 · 사본 모양의 판 — 짓는 법(칸 · 순서)이나 내보내는 글을 바꾸면 올린다(그날 통째로).
FORMAT = "1"
STATE = "state.sqlite"
#: 정의 표 — 이 행이 바뀌면 사본의 모양(술어 · 형 · 인터페이스 · OWL)이 바뀔 수 있다.
DEFINITION_TABLES = ("object_types", "property_defs", "object_interfaces", "relation_types")
#: 정의 지문에서 빼는 칸 — 화면 배치 · 시각. 사본(`export` · `copyexport`)이 이 칸을 안 읽는다
#: (시험이 지킨다). 넣어 두면 목록 칸 배치를 고칠 때마다 그날 밤이 통째로가 된다.
UI_ONLY = frozenset({"list_view", "form_view", "detail_view", "created_at", "updated_at"})
#: 한 번에 SQLite 에 넣는 줄.
CHUNK = 20_000
#: 지문을 id 차례로 세울 때 이 트랜잭션의 정렬 메모리(병렬 작업자마다) — 기본 4MB 면
#: 2,000만 줄이 디스크로 쏟아진다.
WORK_MEM = "256MB"

_SEP, _NULL = "chr(31)", "chr(30)"


def _copy(db: Session, sql: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
    """`COPY (…) TO STDOUT` 로 줄을 흘린다 — 서버 쪽 커서는 병렬 질의를 끄고 줄마다 값을 바꾼다
    (2,020만 객체의 지문이 커서로 580초, `COPY` 로 91초 — 2026-11-11). 값은 글자 그대로 온다
    (NULL 은 None). 정렬이 디스크로 쏟아지지 않게 이 트랜잭션의 작업 메모리를 올린다."""
    db.execute(text(f"SET LOCAL work_mem = '{WORK_MEM}'"))
    raw = db.connection().connection.driver_connection
    assert raw is not None
    with raw.cursor() as cur, cur.copy(f"COPY ({sql}) TO STDOUT", params) as copy:
        yield from copy.rows()


def definitions(db: Session) -> str:
    """정의 지문 — 정의 표 넷의 행(화면 배치 · 시각 칸 빼고) · 지문의 판 · SP 판."""
    digest = hashlib.sha256(f"{FORMAT}|{version.current()}|{aliases.HUMAN}".encode())
    for table in DEFINITION_TABLES:
        columns = [
            name
            for (name,) in db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :t "
                    "ORDER BY ordinal_position"
                ),
                {"t": table},
            )
            if name not in UI_ONLY
        ]
        picked = ", ".join(f't."{name}"' for name in columns)
        found = db.execute(
            text(
                f"SELECT count(*), md5(coalesce(string_agg(row({picked})::text, {_NULL} "
                f"ORDER BY t.id), '')) FROM \"{table}\" t"
            )
        ).one()
        digest.update(f"|{table}:{','.join(columns)}:{found[0]}:{found[1]}".encode())
    return digest.hexdigest()


def object_prints() -> str:
    """살아 있는 객체마다 (id, 타입, 식별자, 지문) — id 차례(uuid 는 바이트 차례 = SQLite 와
    같다)."""
    return (
        "SELECT o.id, o.type_id, o.key, md5(concat_ws("
        f"{_SEP}, o.type_id::text, coalesce(o.key, {_NULL}), coalesce(o.label, {_NULL}), "
        f"coalesce(o.description, {_NULL}), coalesce(o.properties::text, {_NULL}), "
        f"coalesce(a.names, {_NULL}))) "
        "FROM objects o LEFT JOIN ("
        f"SELECT object_id, string_agg(value, {_SEP} ORDER BY value) AS names "
        "FROM object_aliases WHERE kind = %(human)s GROUP BY object_id"
        ") a ON a.object_id = o.id "
        f"WHERE {alive_objects('o')} ORDER BY o.id"
    )


def relation_prints(today: Any) -> str:
    """오늘 유효하고 두 끝이 살아 있는 선마다 (id, 출발, 도착, 종류, 지문) — id 차례."""
    return (
        "SELECT r.id, r.src_object_id, r.dst_object_id, r.relation, md5(concat_ws("
        f"{_SEP}, r.relation, r.src_object_id::text, r.dst_object_id::text, "
        f"coalesce(r.valid_from::text, {_NULL}), coalesce(r.valid_to::text, {_NULL}), "
        f"coalesce(r.pinned_revision::text, {_NULL}), coalesce(r.pinned_at::text, {_NULL})"
        f")) {alive_relations(today)} ORDER BY r.id"
    )


def _uuid_bytes(value: str) -> bytes:
    return bytes.fromhex(value.replace("-", ""))


def _objects(db: Session, shape: Shape) -> Iterator[tuple[bytes, bytes, str]]:
    """(id, 지문, IRI — 꺾쇠 없이). 모르는 타입은 건너뛴다(내보내기와 같다)."""
    slug_of = {str(type_id): slug for type_id, slug in shape.slug_of.items()}
    individual = shape.iris.individual
    for object_id, type_id, key, digest in _copy(
        db, object_prints(), {"human": aliases.HUMAN}
    ):
        slug = slug_of.get(type_id)
        if slug is not None:
            iri = individual(slug, key, object_id)
            yield _uuid_bytes(object_id), bytes.fromhex(digest), iri[1:-1]


def _relations(db: Session, today: Any) -> Iterator[tuple[bytes, bytes, bytes, bytes, str]]:
    for rid, src, dst, relation, digest in _copy(db, relation_prints(today)):
        yield (
            _uuid_bytes(rid),
            bytes.fromhex(digest),
            _uuid_bytes(src),
            _uuid_bytes(dst),
            relation,
        )


_SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE objects (id BLOB PRIMARY KEY, h BLOB NOT NULL, iri TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE relations (
    id BLOB PRIMARY KEY, h BLOB NOT NULL,
    src BLOB NOT NULL, dst BLOB NOT NULL, rel TEXT NOT NULL
) WITHOUT ROWID;
"""


def _fast(con: sqlite3.Connection) -> None:
    """지문 파일은 사본과 함께 버리고 다시 짓는 것이라 일지 · 동기 쓰기를 끈다."""
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")


def _chunks(rows: Iterator[Any]) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= CHUNK:
            yield batch
            batch = []
    if batch:
        yield batch


@dataclass
class Built:
    objects: int = 0
    relations: int = 0


def build(db: Session, shape: Shape, path: Path, *, today: Any, meta: dict[str, str]) -> Built:
    """통째로 실은 사본의 지문 — 내보낸 그 트랜잭션에서 부른다."""
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    try:
        _fast(con)
        con.executescript(_SCHEMA)
        done = Built()
        for batch in _chunks(_objects(db, shape)):
            con.executemany("INSERT INTO objects VALUES (?, ?, ?)", batch)
            done.objects += len(batch)
        for batch in _chunks(_relations(db, today)):
            con.executemany("INSERT INTO relations VALUES (?, ?, ?, ?, ?)", batch)
            done.relations += len(batch)
        counts = {"objects": str(done.objects), "relations": str(done.relations)}
        con.executemany("INSERT INTO meta VALUES (?, ?)", sorted({**meta, **counts}.items()))
        con.commit()
    finally:
        con.close()
    return done


def read_meta(path: Path) -> dict[str, str] | None:
    """지문 파일의 meta — 없거나 못 읽으면 None."""
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return dict(con.execute("SELECT k, v FROM meta").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        return None


@dataclass
class ObjectChange:
    """바뀐 객체 — 옛 IRI(없으면 새로 생김) · 새 IRI(없으면 사라짐)."""

    id: bytes
    old: str | None
    new: str | None
    digest: bytes | None = None

    @property
    def moved(self) -> bool:
        """IRI 가 달라졌다(생김 · 사라짐 · 식별자나 타입이 바뀜) — 이것을 가리키는 줄까지
        고친다."""
        return self.old != self.new


#: 선의 열쇠 — (출발 id, 종류, 도착 id). 같은 열쇠의 선이 여럿이면 사본의 줄 하나를 함께 쓴다.
Key = tuple[bytes, str, bytes]


@dataclass
class RelationChange:
    id: bytes
    old: Key | None
    new: Key | None
    digest: bytes | None = None


class TooMany(Exception):
    """바뀐 것이 많다 — 통째로 싣는 편이 빠르고 깨끗하다."""


class OutOfOrder(Exception):
    """두 차례가 어긋났다(있어서는 안 된다) — 통째로."""


@dataclass
class Plan:
    objects: list[ObjectChange] = field(default_factory=list)
    relations: list[RelationChange] = field(default_factory=list)
    #: 지금 DB 의 수 — 견주기 · 판 기록.
    object_total: int = 0
    relation_total: int = 0
    #: 옛 판 · 지금의 선에 쓰인 관계 종류 — 관계 종류에는 외래키가 없어 정의 표만으로는 모른다.
    relation_slugs: set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not self.objects and not self.relations

    def keys(self) -> set[Key]:
        """고칠 선의 열쇠 — 바뀐 선의 옛 열쇠와 새 열쇠 모두."""
        out: set[Key] = set()
        for one in self.relations:
            if one.old is not None:
                out.add(one.old)
            if one.new is not None:
                out.add(one.new)
        return out


def _merge(new: Iterator[Any], old: Iterator[Any]) -> Iterator[tuple[Any, Any]]:
    """id 차례인 두 흐름을 짝짓는다 — (새 줄 | None, 옛 줄 | None). 차례가 어긋나면 멈춘다."""
    n, o = next(new, None), next(old, None)
    last_n = last_o = b""
    while n is not None or o is not None:
        if n is not None and last_n and n[0] <= last_n:
            raise OutOfOrder("DB 의 차례")
        if o is not None and last_o and o[0] <= last_o:
            raise OutOfOrder("지문 파일의 차례")
        if n is not None and (o is None or n[0] < o[0]):
            yield n, None
            last_n, n = n[0], next(new, None)
        elif o is not None and (n is None or o[0] < n[0]):
            yield None, o
            last_o, o = o[0], next(old, None)
        elif n is not None and o is not None:
            yield n, o
            last_n, last_o = n[0], o[0]
            n, o = next(new, None), next(old, None)


def diff(db: Session, shape: Shape, path: Path, *, today: Any, limit: int) -> Plan:
    """지난 판의 지문(`path`)과 지금 DB 를 견준다. 바뀐 것이 `limit` 을 넘으면 `TooMany`."""
    plan = Plan()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        old_objects = iter(con.execute("SELECT id, h, iri FROM objects ORDER BY id"))
        for now, then in _merge(_objects(db, shape), old_objects):
            if now is not None:
                plan.object_total += 1
            if (
                now is not None
                and then is not None
                and now[1] == then[1]
                and now[2] == then[2]
            ):
                continue
            either = now if now is not None else then
            assert either is not None
            plan.objects.append(
                ObjectChange(
                    id=either[0],
                    old=None if then is None else then[2],
                    new=None if now is None else now[2],
                    digest=None if now is None else now[1],
                )
            )
            if len(plan.objects) > limit:
                raise TooMany(f"바뀐 객체 {len(plan.objects):,}개 넘게")
        old_relations = iter(
            con.execute("SELECT id, h, src, rel, dst FROM relations ORDER BY id")
        )
        slugs = plan.relation_slugs
        for now_r, then_r in _merge(_relations(db, today), old_relations):
            if now_r is not None:
                plan.relation_total += 1
                slugs.add(now_r[4])
            if then_r is not None:
                slugs.add(then_r[3])
            if now_r is not None and then_r is not None and now_r[1] == then_r[1]:
                continue
            either_r = now_r if now_r is not None else then_r
            assert either_r is not None
            plan.relations.append(
                RelationChange(
                    id=either_r[0],
                    old=None if then_r is None else (then_r[2], then_r[3], then_r[4]),
                    new=None if now_r is None else (now_r[2], now_r[4], now_r[3]),
                    digest=None if now_r is None else now_r[1],
                )
            )
            if len(plan.objects) + len(plan.relations) > limit:
                raise TooMany(
                    f"바뀐 객체 · 선 {len(plan.objects) + len(plan.relations):,}개 넘게"
                )
    finally:
        con.close()
    return plan


def old_iris(path: Path, ids: set[bytes]) -> dict[bytes, str]:
    """지난 판에서 그 객체들의 IRI — 바뀐 선의 옛 줄을 지울 때 쓴다."""
    out: dict[bytes, str] = {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ordered = sorted(ids)
        for start in range(0, len(ordered), 900):
            part = ordered[start : start + 900]
            marks = ",".join("?" * len(part))
            out.update(con.execute(f"SELECT id, iri FROM objects WHERE id IN ({marks})", part))
    finally:
        con.close()
    return out


def advance(path: Path, plan: Plan, meta: dict[str, str]) -> None:
    """지난 판의 지문 파일(복사본)을 이번 판으로 — 바뀐 줄만 고친다."""
    con = sqlite3.connect(path)
    try:
        _fast(con)
        for batch in _chunks(iter(plan.objects)):
            con.executemany(
                "DELETE FROM objects WHERE id = ?",
                [(one.id,) for one in batch if one.new is None],
            )
            con.executemany(
                "INSERT OR REPLACE INTO objects VALUES (?, ?, ?)",
                [(one.id, one.digest, one.new) for one in batch if one.new is not None],
            )
        for batch_r in _chunks(iter(plan.relations)):
            con.executemany(
                "DELETE FROM relations WHERE id = ?",
                [(one.id,) for one in batch_r if one.new is None],
            )
            con.executemany(
                "INSERT OR REPLACE INTO relations VALUES (?, ?, ?, ?, ?)",
                [
                    (one.id, one.digest, one.new[0], one.new[2], one.new[1])
                    for one in batch_r
                    if one.new is not None
                ],
            )
        con.execute("DELETE FROM meta")
        con.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
        con.commit()
    finally:
        con.close()


#: 다시 낼 객체 · 선을 한 번에 DB 에 묻는 개수(`= ANY(…)`).
ASK = 5_000
#: 다시 낸 줄을 사본에 넣는 덩이(글자 수).
BATCH = 4_000_000


def _ids(raw: list[bytes]) -> Iterator[list[uuid.UUID]]:
    for start in range(0, len(raw), ASK):
        yield [uuid.UUID(bytes=one) for one in raw[start : start + ASK]]


def emit(db: Session, shape: Shape, plan: Plan, *, today: Any) -> Iterator[str]:
    """바뀐 것이 **지금 DB 에서** 낼 줄 — 통째로 내보내기와 같은 함수 · 같은 거르기.

    - 바뀐 객체(살아 있는 것): 객체의 줄 · 별칭 · 나가는 참조
    - IRI 가 달라진 객체(생김 · 식별자 · 타입): 들어오는 참조 · 그 객체에 닿는 관계 전부
    - 바뀐 선의 열쇠(옛 · 새): 그 열쇠의 오늘 유효한 선 전부(같은 열쇠의 안 바뀐 선까지 —
      지울 때 함께 지웠다)

    같은 선을 두 번 내지 않는다(`seen`) — 구체화한 진술이 둘이 된다."""
    fresh = [one.id for one in plan.objects if one.new is not None]
    moved = [one.id for one in plan.objects if one.new is not None and one.moved]
    out: list[str] = []
    size = 0

    def take(text_: str) -> Iterator[str]:
        nonlocal size
        out.append(text_)
        size += len(text_)
        if size >= BATCH:
            yield "".join(out)
            out.clear()
            size = 0

    for part in _ids(fresh):
        ask = {"ids": part}
        for block, _n in object_blocks(db, shape, (" AND o.id = ANY(%(ids)s)", ask)):
            yield from take(block)
        for line in alias_lines(db, shape, (" AND a.object_id = ANY(%(ids)s)", ask)):
            yield from take(line)
        for line in ref_lines(db, shape, (" AND f.src_id = ANY(%(ids)s)", ask)):
            yield from take(line)
    seen: set[Any] = set()
    for part in _ids(moved):
        ask = {"ids": part}
        for line in ref_lines(db, shape, (" AND f.dst_id = ANY(%(ids)s)", ask)):
            yield from take(line)
        touching = " AND (r.src_object_id = ANY(%(ids)s) OR r.dst_object_id = ANY(%(ids)s))"
        for block, _n in relation_blocks(db, shape, (touching, ask), today=today, seen=seen):
            yield from take(block)
    keys = sorted(plan.keys())
    for start in range(0, len(keys), ASK):
        part_k = keys[start : start + ASK]
        ask_k = {
            "s": [uuid.UUID(bytes=one[0]) for one in part_k],
            "k": [one[1] for one in part_k],
            "d": [uuid.UUID(bytes=one[2]) for one in part_k],
        }
        keyed = (
            " AND (r.src_object_id, r.relation, r.dst_object_id) IN (SELECT * FROM "
            "unnest(%(s)s::uuid[], %(k)s::text[], %(d)s::uuid[]))"
        )
        for block, _n in relation_blocks(db, shape, (keyed, ask_k), today=today, seen=seen):
            yield from take(block)
    if out:
        yield "".join(out)


@dataclass
class Expected:
    """DB 가 말하는 사본의 개수 — 바뀐 것만 고친 사본을 이것과 견준다(IRI 는 꺾쇠 없이)."""

    classes: dict[str, int] = field(default_factory=dict)
    relations: dict[str, int] = field(default_factory=dict)
    refs: dict[str, int] = field(default_factory=dict)
    statements: int = 0
    aliases: int = 0


def expected(db: Session, shape: Shape, *, today: Any) -> Expected:
    """타입 · 인터페이스마다 객체 수, 관계 종류마다 (출발, 도착) 짝 수, 참조 칸마다 줄 수,
    구체화한 진술 수, 별칭 수 — 내보내기와 같은 거르기."""
    out = Expected()
    for type_id, count in db.execute(
        text(
            f"SELECT o.type_id, count(*) FROM objects o WHERE {alive_objects('o')} "
            "GROUP BY o.type_id"
        )
    ):
        for head in shape.heads.get(type_id, []):
            out.classes[head[1:-1]] = out.classes.get(head[1:-1], 0) + count
    for relation, count in db.execute(
        text(
            "SELECT x.relation, count(*) FROM (SELECT DISTINCT r.src_object_id, r.relation, "
            f"r.dst_object_id {alive_relations(today)}) x GROUP BY x.relation"
        )
    ):
        out.relations[shape.iris.relation(relation)[1:-1]] = count
    out.statements = int(
        db.execute(
            text(
                f"SELECT count(*) {alive_relations(today)} AND (r.valid_from IS NOT NULL "
                "OR r.valid_to IS NOT NULL OR r.pinned_at IS NOT NULL)"
            )
        ).scalar_one()
    )
    for type_id, key, count in db.execute(
        text(
            "SELECT s.type_id, f.key, count(*) FROM object_refs f "
            f"JOIN objects s ON s.id = f.src_id AND {alive_objects('s')} "
            f"JOIN objects d ON d.id = f.dst_id AND {alive_objects('d')} "
            "GROUP BY s.type_id, f.key"
        )
    ):
        predicate = shape.ref_pred.get((type_id, key))
        if predicate is not None:
            out.refs[predicate[1:-1]] = out.refs.get(predicate[1:-1], 0) + count
    out.aliases = int(
        db.execute(
            text(
                "SELECT count(*) FROM object_aliases a "
                f"JOIN objects o ON o.id = a.object_id AND {alive_objects('o')} "
                "WHERE a.kind = :human"
            ),
            {"human": aliases.HUMAN},
        ).scalar_one()
    )
    return out
