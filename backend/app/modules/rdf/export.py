"""정의 → OWL, 데이터 → RDF.

## 대응

| 플랫폼 | RDF/OWL |
| --- | --- |
| 타입 | `owl:Class` (+ 상위 타입은 `rdfs:subClassOf`) |
| 속성 정의(문자 · 숫자 · 날짜 · 선택값 …) | `owl:DatatypeProperty` + `rdfs:range` xsd 형 |
| 참조 속성(`object_ref`) | `owl:ObjectProperty` + `rdfs:range` 대상 타입, 역방향 inverseOf |
| 관계 종류 | `owl:ObjectProperty` + domain/range(허용 타입이 하나일 때), 이행 → Transitive, |
|  | 무방향 → Symmetric, 역방향 이름 → `owl:inverseOf` |
| 객체 | 개체(`rdf:type` 타입), `rdfs:label` 이름, `dcterms:identifier` 식별자, 별칭 altLabel |
| 값 · 관계 | 트리플 |

## IRI

`<base>ns#<타입slug>` · `<base>ns#<타입>.<속성키>` · `<base>ns#rel.<관계slug>` 가 정의,
`<base>o/<타입>/<식별자>` 가 개체(식별자 없는 타입은 내부 id). `base` 는 이 설치의 주소
(`https://<호스트>/<slug>/`)라 두 설치의 IRI 가 안 겹친다 — 식별자는 바뀌지 않으므로 IRI 도
안정적이다.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from rdflib import DCTERMS, OWL, RDF, RDFS, SKOS, XSD, Graph, Literal, Namespace, URIRef
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects import aliases
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType

XSD_OF: dict[str, URIRef] = {
    "text": XSD.string,
    "text_long": XSD.string,
    "number": XSD.decimal,
    "date": XSD.date,
    "datetime": XSD.dateTime,
    "bool": XSD.boolean,
    "enum": XSD.string,
    "url": XSD.anyURI,
    "file": XSD.string,
}


class Names:
    """이 설치의 IRI 자리 — 정의(`ns#`)와 개체(`o/`)를 가른다."""

    def __init__(self, base: str) -> None:
        self.base = base if base.endswith("/") else base + "/"
        self.ns = Namespace(self.base + "ns#")

    def type_(self, slug: str) -> URIRef:
        return self.ns[slug]

    def prop(self, type_slug: str, key: str) -> URIRef:
        return self.ns[f"{type_slug}.{key}"]

    def prop_inverse(self, type_slug: str, key: str) -> URIRef:
        return self.ns[f"{type_slug}.{key}.inverse"]

    def relation(self, slug: str) -> URIRef:
        return self.ns[f"rel.{slug}"]

    def relation_inverse(self, slug: str) -> URIRef:
        return self.ns[f"rel.{slug}.inverse"]

    def individual(self, type_slug: str, key: str | None, object_id: uuid.UUID) -> URIRef:
        tail = key if key else str(object_id)
        return URIRef(f"{self.base}o/{type_slug}/{_iri_safe(tail)}")


def _iri_safe(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="-._~")


def _bind(graph: Graph, names: Names) -> None:
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)
    graph.bind("skos", SKOS)
    graph.bind("dcterms", DCTERMS)
    graph.bind("sp", names.ns)


def schema_graph(db: Session, names: Names) -> Graph:
    """정의 → OWL. 사용 안 함(`is_active=False`)인 것도 낸다 — 옛 데이터가 그것을 가리킨다."""
    graph = Graph()
    _bind(graph, names)
    ontology = URIRef(names.base + "ns")
    graph.add((ontology, RDF.type, OWL.Ontology))

    types = list(
        db.scalars(select(ObjectType).order_by(ObjectType.sort_order, ObjectType.slug))
    )
    by_slug = {row.slug: row for row in types}
    for row in types:
        cls = names.type_(row.slug)
        graph.add((cls, RDF.type, OWL.Class))
        graph.add((cls, RDFS.label, Literal(row.label, lang="ko")))
        if row.description:
            graph.add((cls, RDFS.comment, Literal(row.description, lang="ko")))
        if row.parent_slug and row.parent_slug in by_slug:
            graph.add((cls, RDFS.subClassOf, names.type_(row.parent_slug)))
        for prop in properties_of(db, row.id):
            _add_property(graph, names, row, prop)

    relations = list(db.scalars(select(RelationType).order_by(RelationType.slug)))
    for rel in relations:
        _add_relation(graph, names, rel)
    return graph


def _add_property(graph: Graph, names: Names, row: ObjectType, prop: PropertyDef) -> None:
    node = names.prop(row.slug, prop.key)
    graph.add((node, RDFS.label, Literal(prop.label, lang="ko")))
    graph.add((node, RDFS.domain, names.type_(row.slug)))
    if prop.help:
        graph.add((node, RDFS.comment, Literal(prop.help, lang="ko")))
    if prop.data_type == "object_ref":
        graph.add((node, RDF.type, OWL.ObjectProperty))
        if prop.ref_type_slug:
            graph.add((node, RDFS.range, names.type_(prop.ref_type_slug)))
        if not prop.multi:
            # 참조 칸은 「칸에 저장한 많대일 관계」 — 하나만 가리킨다.
            graph.add((node, RDF.type, OWL.FunctionalProperty))
        if prop.inverse_label:
            inverse = names.prop_inverse(row.slug, prop.key)
            graph.add((inverse, RDF.type, OWL.ObjectProperty))
            graph.add((inverse, RDFS.label, Literal(prop.inverse_label, lang="ko")))
            graph.add((inverse, OWL.inverseOf, node))
        return
    graph.add((node, RDF.type, OWL.DatatypeProperty))
    graph.add((node, RDFS.range, XSD_OF.get(prop.data_type, XSD.string)))
    if not prop.multi:
        graph.add((node, RDF.type, OWL.FunctionalProperty))
    if prop.data_type == "enum" and prop.enum_options:
        # 고를 값 목록은 주석으로 — 값은 문자열 그대로 나간다(코드표 개체화는 다음).
        graph.add((node, SKOS.note, Literal(", ".join(prop.enum_options))))


def _add_relation(graph: Graph, names: Names, rel: RelationType) -> None:
    node = names.relation(rel.slug)
    graph.add((node, RDF.type, OWL.ObjectProperty))
    graph.add((node, RDFS.label, Literal(rel.label, lang="ko")))
    if rel.description:
        graph.add((node, RDFS.comment, Literal(rel.description, lang="ko")))
    # 허용 타입이 하나일 때만 domain/range 로 — 여럿이면 OWL 의 domain 은 「교집합」 이라
    # 뜻이 틀려진다(합집합 클래스를 만드는 것은 다음).
    if rel.src_type_slugs and len(rel.src_type_slugs) == 1:
        graph.add((node, RDFS.domain, names.type_(rel.src_type_slugs[0])))
    if rel.dst_type_slugs and len(rel.dst_type_slugs) == 1:
        graph.add((node, RDFS.range, names.type_(rel.dst_type_slugs[0])))
    if rel.transitive:
        graph.add((node, RDF.type, OWL.TransitiveProperty))
    if not rel.directed:
        graph.add((node, RDF.type, OWL.SymmetricProperty))
    if rel.inverse_label:
        inverse = names.relation_inverse(rel.slug)
        graph.add((inverse, RDF.type, OWL.ObjectProperty))
        graph.add((inverse, RDFS.label, Literal(rel.inverse_label, lang="ko")))
        graph.add((inverse, OWL.inverseOf, node))


def data_graph(db: Session, names: Names, type_slugs: list[str] | None = None) -> Graph:
    """데이터 → RDF. 지워진 것 · 병합돼 사라진 것은 안 낸다. 관계는 양 끝이 나갈 때만."""
    graph = Graph()
    _bind(graph, names)

    types = {row.id: row for row in db.scalars(select(ObjectType))}
    wanted = {row.id for row in types.values() if not type_slugs or row.slug in type_slugs}
    props_of = {row.id: properties_of(db, row.id) for row in types.values()}

    stmt = select(ObjectInstance).where(
        ObjectInstance.deleted_at.is_(None), ObjectInstance.merged_into_id.is_(None)
    )
    rows = [row for row in db.scalars(stmt) if row.type_id in wanted]
    iri_of: dict[uuid.UUID, URIRef] = {}
    for row in rows:
        iri_of[row.id] = names.individual(types[row.type_id].slug, row.key, row.id)
    # 참조가 가리키는 상대는 범위 밖 타입이어도 IRI 가 필요하다 — 전부 미리 알아 둔다.
    all_iri: dict[uuid.UUID, URIRef] = dict(iri_of)
    if type_slugs:
        for other in db.scalars(stmt):
            if other.id not in all_iri and other.type_id in types:
                all_iri[other.id] = names.individual(
                    types[other.type_id].slug, other.key, other.id
                )

    alias_of = aliases.human_of(db, [row.id for row in rows]) if rows else {}
    for row in rows:
        object_type = types[row.type_id]
        node = iri_of[row.id]
        graph.add((node, RDF.type, names.type_(object_type.slug)))
        graph.add((node, RDFS.label, Literal(row.label, lang="ko")))
        if row.key:
            graph.add((node, DCTERMS.identifier, Literal(row.key)))
        if row.description:
            graph.add((node, RDFS.comment, Literal(row.description, lang="ko")))
        for alias in alias_of.get(row.id, []):
            graph.add((node, SKOS.altLabel, Literal(alias, lang="ko")))
        for prop in props_of[row.type_id]:
            _add_values(
                graph, names, object_type, prop, node, row.properties.get(prop.key), all_iri
            )

    relations = db.scalars(select(ObjectRelation))
    for edge in relations:
        src = iri_of.get(edge.src_object_id)
        dst = iri_of.get(edge.dst_object_id) or all_iri.get(edge.dst_object_id)
        if src is None or dst is None:
            continue
        graph.add((src, names.relation(edge.relation), dst))
    return graph


def _add_values(
    graph: Graph,
    names: Names,
    object_type: ObjectType,
    prop: PropertyDef,
    node: URIRef,
    value: Any,
    iri_of: dict[uuid.UUID, URIRef],
) -> None:
    if value is None or value == "" or value == []:
        return
    values = value if isinstance(value, list) else [value]
    pred = names.prop(object_type.slug, prop.key)
    for one in values:
        if prop.data_type == "object_ref":
            try:
                target = iri_of.get(uuid.UUID(str(one)))
            except ValueError:
                target = None
            if target is not None:
                graph.add((node, pred, target))
            continue
        graph.add((node, pred, _literal(prop.data_type, one)))


def _literal(data_type: str, value: Any) -> Literal:
    datatype = XSD_OF.get(data_type, XSD.string)
    if data_type == "bool":
        return Literal(bool(value), datatype=XSD.boolean)
    if data_type == "number":
        try:
            return Literal(value, datatype=XSD.decimal)
        except (TypeError, ValueError):
            return Literal(str(value))
    if data_type in ("text", "text_long"):
        return Literal(str(value), lang="ko")
    return Literal(str(value), datatype=datatype)


def serialize(graph: Graph, fmt: str) -> tuple[str, str]:
    """(본문, 미디어 타입). `ttl` · `jsonld` · `xml`."""
    if fmt == "jsonld":
        return graph.serialize(format="json-ld", indent=2), "application/ld+json"
    if fmt == "xml":
        return graph.serialize(format="pretty-xml"), "application/rdf+xml"
    return graph.serialize(format="turtle"), "text/turtle"


def inferred(schema: Graph, data: Graph) -> Graph:
    """OWL-RL 추론으로 **새로 생긴** 트리플만 — 상속으로 얻은 분류, 역관계, 이행 관계.

    플랫폼이 저장한 것과 추론이 더한 것을 가르는 자리다. 사람이 「이게 왜 여기 있지」 를 물을
    때 답은 늘 둘 중 하나여야 한다."""
    import owlrl  # type: ignore[import-untyped]

    before = Graph()
    for triple in schema:
        before.add(triple)
    for triple in data:
        before.add(triple)
    closed = Graph()
    for triple in before:
        closed.add(triple)
    # 공리 · 데이터형 공리는 넣지 않는다 — 알고 싶은 것은 이 데이터에서 새로 나온 사실이다.
    owlrl.DeductiveClosure(
        owlrl.OWLRL_Semantics, axiomatic_triples=False, datatype_axioms=False
    ).expand(closed)
    out = Graph()
    for prefix, ns in schema.namespaces():
        out.bind(prefix, ns)
    for triple in closed:
        if triple not in before and not _noise(triple):
            out.add(triple)
    return out


#: 표준 어휘의 자리 — 추론기가 여기 것들끼리 잇는 것은 도메인 사실이 아니다.
_VOCAB = (str(RDF), str(RDFS), str(OWL), str(XSD), str(SKOS), str(DCTERMS))


def _noise(triple: tuple[Any, Any, Any]) -> bool:
    """추론기가 공리로 덧붙이는 자명한 것들(`x owl:sameAs x`, `rdfs:Resource` 분류 …)은
    뺀다."""
    subject, predicate, obj = triple
    # 값(리터럴)에 「이것은 anyURI 다」 를 붙이는 것도 새로 안 것이 아니다.
    if isinstance(subject, Literal):
        return True
    # **어휘 자신에 대한 이야기는 뺀다.** `rdf:HTML a rdfs:Datatype` 같은 것이 수백 줄 나오면
    # 정작 새로 알게 된 도메인 사실(상속 분류 · 역관계 · 이행)이 그 속에 묻힌다(실측).
    if isinstance(subject, URIRef) and str(subject).startswith(_VOCAB):
        return True
    if predicate == OWL.sameAs and subject == obj:
        return True
    if predicate == RDF.type and obj in (RDFS.Resource, OWL.Thing, OWL.NamedIndividual):
        return True
    if predicate in (RDFS.subClassOf, RDFS.subPropertyOf) and subject == obj:
        return True
    if obj in (RDFS.Resource, OWL.Thing) and predicate == RDFS.subClassOf:
        return True
    builtins = (
        RDF.Property,
        RDFS.Class,
        OWL.Class,
        OWL.Thing,
        RDFS.Resource,
        RDFS.Literal,
    )
    return subject in builtins


# --- 질의 (SPARQL) ------------------------------------------------------------------
#
# **그래프를 캐시한다.** 전체 데이터가 13만 트리플에 3.4초라(실측) 질의마다 다시 세우면 MCP 로
# 들어온 AI 가 한 질문에 그만큼을 기다린다. 정의 · 객체가 바뀌면 판(version)이 달라져 저절로
# 버려진다. 메모리를 위해 두 벌까지만 둔다 — 더 두면 워커 넷이 각자 들고 있게 된다.

_CACHE: dict[tuple[str, tuple[str, ...], bool], tuple[tuple[Any, ...], float, Graph]] = {}
_CACHE_MAX = 2
#: 판(version)이 못 잡는 변화도 있다 — 속성 정의에는 `updated_at` 이 없어 이름만 고치면 수가
#: 그대로다. 그래서 이 시간이 지나면 어차피 다시 세운다(질의는 3초 안이라 감당된다).
_CACHE_TTL_SECONDS = 120.0

#: 추론을 켤 수 있는 상한 — 5만 트리플에 20초(실측). 그 위는 범위를 좁히게 한다.
INFER_MAX_TRIPLES = 20_000


def version(db: Session) -> tuple[Any, ...]:
    """지금 데이터의 판 — 정의 · 객체 · 관계가 바뀌면 값이 달라진다(속성 이름만 고친 것은
    못 잡는다 — 그것은 TTL 이 받는다)."""
    from sqlalchemy import func

    return (
        db.scalar(select(func.max(ObjectType.updated_at))),
        db.scalar(select(func.count()).select_from(ObjectType)),
        db.scalar(select(func.count()).select_from(PropertyDef)),
        db.scalar(select(func.max(ObjectInstance.updated_at))),
        db.scalar(select(func.count()).select_from(ObjectInstance)),
        db.scalar(select(func.count()).select_from(ObjectRelation)),
    )


def graph_for(
    db: Session, names: Names, type_slugs: list[str] | None, infer: bool
) -> tuple[Graph, int, int]:
    """질의할 그래프 — (그래프, 저장된 트리플 수, 추론이 더한 수).

    같은 판이면 다시 세우지 않는다."""
    key = (names.base, tuple(sorted(type_slugs or ())), infer)
    now = version(db)
    hit = _CACHE.get(key)
    if hit and hit[0] == now and time.monotonic() - hit[1] < _CACHE_TTL_SECONDS:
        graph = hit[2]
        return graph, len(graph), 0

    schema = schema_graph(db, names)
    data = data_graph(db, names, type_slugs)
    stored = len(data)
    graph = Graph()
    for prefix, ns in schema.namespaces():
        graph.bind(prefix, ns)
    for triple in schema:
        graph.add(triple)
    for triple in data:
        graph.add(triple)
    added = 0
    if infer:
        extra = inferred(schema, data)
        added = len(extra)
        for triple in extra:
            graph.add(triple)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (now, time.monotonic(), graph)
    return graph, stored, added


def shorten(graph: Graph, value: Any) -> Any:
    """읽을 모양으로 — IRI 는 접두어로 줄이고, 리터럴은 파이썬 값으로."""
    if isinstance(value, URIRef):
        try:
            return graph.namespace_manager.normalizeUri(value)
        except Exception:  # pragma: no cover - 접두어가 없는 IRI
            return str(value)
    if isinstance(value, Literal):
        return value.toPython()
    if value is None:
        return None
    return str(value)
