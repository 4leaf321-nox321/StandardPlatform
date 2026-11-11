"""SPARQL 사본에 실을 N-Triples(ADR 0070) — **메모리 그래프(`export.data_graph`)와 같은
트리플**을 DB 에서 흘려 쓴다.  메모리 그래프는 객체마다 rdflib 개체를 만들어 5만 개에서 멈춘다.
여기는 줄을 그대로 쓴다 — 객체 2,000만 개도 파이썬 사전 하나 없이 지나간다(IRI 는 줄마다 타입 ·
식별자로 짓는다). 모양이 같아야 한다: 시험이 둘을 견준다(`tests/api/test_rdf_copy.py`). 다른
점은 둘뿐이다:

- **보는 범위** — 사본은 전부다(부서 구분 없음). 부서로 좁힌 설치에서는 모든 부서를 보는 사람만
  사본에 묻는다(`routes`).
- **못 읽는 숫자**(숫자 칸의 「모름」)는 형 없는 글자로 — 메모리 그래프는 `xsd:decimal` 에 그
  글자를 그대로 붙여 ill-typed 리터럴이 됐다. 지수꼴 소수(`1e-07`)는 소수꼴로 편다.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

from rdflib import DCTERMS, RDF, RDFS, SKOS, XSD
from sqlalchemy.orm import Session

from app import version
from app.modules.objects import aliases
from app.modules.objects import edges as edge_rule
from app.modules.objects.services import properties_of
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType
from app.modules.rdf import export
from app.modules.rdf.store import COPY_BASE

#: 한 번에 DB 에서 받는 줄.
CHUNK = 20_000
#: `quote(…, safe="-._~")` 가 그대로 두는 글자뿐인 식별자.
_PLAIN_KEY = re.compile(r"[A-Za-z0-9._~-]+")

_TYPE = f"<{RDF.type}>"
_LABEL = f"<{RDFS.label}>"
_COMMENT = f"<{RDFS.comment}>"
_IDENT = f"<{DCTERMS.identifier}>"
_ALT = f"<{SKOS.altLabel}>"
_SUBJECT, _PREDICATE, _OBJECT = f"<{RDF.subject}>", f"<{RDF.predicate}>", f"<{RDF.object}>"
_STATEMENT = f"<{RDF.Statement}>"


@dataclass
class Written:
    objects: int = 0
    triples: int = 0
    seconds: float = 0.0
    copied_at: str = ""
    sp_version: str = ""
    base: str = COPY_BASE


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _ko(value: str) -> str:
    return f'"{_escape(value)}"@ko'


def _typed(value: str, datatype: Any) -> str:
    return f'"{_escape(value)}"^^<{datatype}>'


def _plain(value: str) -> str:
    return f'"{_escape(value)}"'


def _number(value: Any) -> str:
    """메모리 그래프(`Literal(value, datatype=xsd:decimal)`)의 글자 꼴 그대로 — 파이썬 `str`.
    지수꼴은 소수꼴로 펴고, 숫자가 아니면 형 없는 글자로."""
    if isinstance(value, bool):
        return _plain(str(value))
    if isinstance(value, int):
        return _typed(str(value), XSD.decimal)
    if isinstance(value, float):
        lexical = str(value)
        if "e" in lexical or "E" in lexical:
            lexical = format(Decimal(lexical), "f")
        return _typed(lexical, XSD.decimal)
    try:
        Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return _plain(str(value))
    return _typed(str(value).strip(), XSD.decimal)


def literal(data_type: str, value: Any) -> str:
    """`export._literal` 과 같은 뜻 — 글자는 `@ko`, 그 밖은 형을 단다."""
    if data_type == "bool":
        truth = value if isinstance(value, bool) else str(value).strip().lower() == "true"
        return _typed("true" if truth else "false", XSD.boolean)
    if data_type == "number":
        return _number(value)
    if data_type in ("text", "text_long"):
        return _ko(str(value))
    return _typed(str(value), export.XSD_OF.get(data_type, XSD.string))


class _Iris:
    """IRI 짓기 — `export.Names` 와 같은 글(바탕만 사본의 것). 줄마다 rdflib 개체를 만들지 않고
    글자로 잇는다 — 객체 수천만 개를 지난다."""

    def __init__(self) -> None:
        self.names = export.Names(COPY_BASE)
        self._objects = f"{self.names.base}o/"
        self._ns = str(self.names.ns)
        self._cache: dict[str, str] = {}

    def individual(self, type_slug: str, key: str | None, object_id: Any) -> str:
        if not key:
            tail = str(object_id)
        elif _PLAIN_KEY.fullmatch(key):
            tail = key  # 안전한 글자뿐 — `quote` 를 건너뛴다(대부분의 식별자)
        else:
            tail = quote(key, safe="-._~")
        return f"<{self._objects}{type_slug}/{tail}>"

    def _term(self, local: str) -> str:
        found = self._cache.get(local)
        if found is None:
            found = self._cache[local] = f"<{self._ns}{local}>"
        return found

    def type_(self, slug: str) -> str:
        return self._term(slug)

    def prop(self, type_slug: str, key: str) -> str:
        return self._term(f"{type_slug}.{key}")

    def relation(self, slug: str) -> str:
        return self._term(f"rel.{slug}")


def _stream(db: Session, sql: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
    """서버 쪽 커서로 줄을 흘린다(드라이버 그대로 — 줄마다 ORM 개체를 안 만든다)."""
    raw = db.connection().connection.driver_connection
    assert raw is not None
    with raw.cursor(name=f"rdfcopy_{uuid.uuid4().hex[:8]}") as cur:
        cur.itersize = CHUNK
        cur.execute(sql, params or {})
        yield from cur


def _alive_objects(alias: str) -> str:
    return f"{alias}.deleted_at IS NULL AND {alias}.merged_into_id IS NULL"


def write(db: Session, out: IO[str]) -> Written:
    """데이터 트리플을 `out` 에 — 객체 · 값 · 별칭 · 참조 · 오늘 유효한 관계(기간 · 고정이
    있으면 구체화한 진술). 정의(OWL)는 `schema` 가 따로.

    줄마다는 평범한 사전 · 튜플만 만진다(ORM 개체 · 이름 붙은 튜플은 줄마다 비싸다 — 견주기
    84만 객체에서 시간의 절반이었다)."""
    started = time.monotonic()
    iris = _Iris()
    names = iris.names
    done = Written(
        copied_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), sp_version=version.current()
    )
    rows = db.query(ObjectType).all()
    slug_of = {row.id: row.slug for row in rows}
    catalog = interfaces.load(db)
    extends_of = catalog.extends_of()
    #: 타입마다 처음 쓰는 줄들(타입 · 인터페이스) — 객체마다 같다.
    heads = {
        row.id: [iris.type_(row.slug)]
        + [
            iris.type_(slug)
            for slug in interfaces.closure(row.interface_slugs or [], extends_of)
        ]
        for row in rows
    }
    defs = {row.id: properties_of(db, row.id) for row in rows}
    #: 타입마다 (칸 키, 칸 종류, 술어) — 참조 칸은 참조 색인이 따로.
    values_of = {
        type_id: [
            (one.key, one.data_type, iris.prop(slug_of[type_id], one.key))
            for one in found
            if one.data_type != "object_ref"
        ]
        for type_id, found in defs.items()
    }
    #: 지금 정의에 있는 참조 칸만 — 지운 칸의 색인 줄이 남아 있어도 메모리 그래프처럼 안 낸다.
    ref_pred = {
        (type_id, one.key): iris.prop(slug_of[type_id], one.key)
        for type_id, found in defs.items()
        for one in found
        if one.data_type == "object_ref"
    }
    write_line = out.write
    count = 0
    individual = iris.individual

    # 1) 객체 — 타입 · 인터페이스 · 이름 · 식별자 · 설명 · 값
    for object_id, type_id, key, label, description, properties in _stream(
        db,
        "SELECT id, type_id, key, label, description, properties FROM objects o "
        f"WHERE {_alive_objects('o')}",
    ):
        slug = slug_of.get(type_id)
        if slug is None:
            continue
        node = individual(slug, key, object_id)
        lines = [f"{node} {_TYPE} {head} .\n" for head in heads[type_id]]
        lines.append(f"{node} {_LABEL} {_ko(label)} .\n")
        if key:
            lines.append(f"{node} {_IDENT} {_plain(key)} .\n")
        if description:
            lines.append(f"{node} {_COMMENT} {_ko(description)} .\n")
        if properties:
            for prop_key, data_type, predicate in values_of[type_id]:
                value = properties.get(prop_key)
                if value is None or value == "" or value == []:
                    continue
                for one in value if isinstance(value, list) else (value,):
                    lines.append(f"{node} {predicate} {literal(data_type, one)} .\n")
        write_line("".join(lines))
        count += len(lines)
        done.objects += 1

    # 2) 별칭(사람이 붙인 것) — 메모리 그래프의 `aliases.human_of` 와 같은 것
    for type_id, key, object_id, value in _stream(
        db,
        "SELECT o.type_id, o.key, o.id, a.value FROM object_aliases a "
        f"JOIN objects o ON o.id = a.object_id AND {_alive_objects('o')} "
        "WHERE a.kind = %(human)s",
        {"human": aliases.HUMAN},
    ):
        slug = slug_of.get(type_id)
        if slug is not None:
            write_line(f"{individual(slug, key, object_id)} {_ALT} {_ko(value)} .\n")
            count += 1

    # 3) 참조 칸 — 참조 색인(가리키는 것이 살아 있을 때만)
    for st, sk, si, dt, dk, di, field in _stream(
        db,
        "SELECT s.type_id, s.key, s.id, d.type_id, d.key, d.id, f.key FROM object_refs f "
        f"JOIN objects s ON s.id = f.src_id AND {_alive_objects('s')} "
        f"JOIN objects d ON d.id = f.dst_id AND {_alive_objects('d')}",
    ):
        ref = ref_pred.get((st, field))
        holder, pointed = slug_of.get(st), slug_of.get(dt)
        if ref is None or holder is None or pointed is None:
            continue
        write_line(f"{individual(holder, sk, si)} {ref} {individual(pointed, dk, di)} .\n")
        count += 1

    # 4) 관계 — 오늘 유효한 선만, 기간 · 고정이 있으면 구체화한 진술(빈 노드는 선의 id 로)
    alive = edge_rule.alive_sql("r", edge_rule.today())
    pinned_revision, pinned_at = f"<{names.pinned_revision}>", f"<{names.pinned_at}>"
    valid_from, valid_to = f"<{names.valid_from}>", f"<{names.valid_to}>"
    for rid, relation, vfrom, vto, prev, pat, st, sk, si, dt, dk, di in _stream(
        db,
        "SELECT r.id, r.relation, r.valid_from, r.valid_to, r.pinned_revision, r.pinned_at, "
        "s.type_id, s.key, s.id, d.type_id, d.key, d.id FROM object_relations r "
        f"JOIN objects s ON s.id = r.src_object_id AND {_alive_objects('s')} "
        f"JOIN objects d ON d.id = r.dst_object_id AND {_alive_objects('d')} "
        f"WHERE {alive}",
    ):
        source, target = slug_of.get(st), slug_of.get(dt)
        if source is None or target is None:
            continue
        src, dst = individual(source, sk, si), individual(target, dk, di)
        predicate = iris.relation(relation)
        write_line(f"{src} {predicate} {dst} .\n")
        count += 1
        if vfrom is None and vto is None and pat is None:
            continue
        said = f"_:r{rid.hex}"
        lines = [
            f"{said} {_TYPE} {_STATEMENT} .\n",
            f"{said} {_SUBJECT} {src} .\n",
            f"{said} {_PREDICATE} {predicate} .\n",
            f"{said} {_OBJECT} {dst} .\n",
        ]
        if prev is not None:
            lines.append(f"{said} {pinned_revision} {_plain(str(prev))} .\n")
        if pat is not None:
            lines.append(f"{said} {pinned_at} {_typed(pat.isoformat(), XSD.dateTime)} .\n")
        if vfrom is not None:
            lines.append(f"{said} {valid_from} {_typed(vfrom.isoformat(), XSD.date)} .\n")
        if vto is not None:
            lines.append(f"{said} {valid_to} {_typed(vto.isoformat(), XSD.date)} .\n")
        write_line("".join(lines))
        count += len(lines)
    done.triples = count
    done.seconds = round(time.monotonic() - started, 2)
    return done


def schema(db: Session, path: Path) -> int:
    """정의(OWL) — 메모리 그래프의 `schema_graph` 그대로(작다), 바탕만 사본의 것."""
    graph = export.schema_graph(db, export.Names(COPY_BASE))
    path.write_text(graph.serialize(format="nt"), encoding="utf-8")
    return len(graph)


def manifest(written: Written, path: Path) -> None:
    path.write_text(
        json.dumps(asdict(written), ensure_ascii=False, indent=1), encoding="utf-8"
    )
