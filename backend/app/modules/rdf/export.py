"""정의 → OWL, 데이터 → RDF.

## 대응

| 플랫폼 | RDF/OWL |
| --- | --- |
| 타입 · 인터페이스 | `owl:Class` (+ 구현 · 상위 인터페이스는 `rdfs:subClassOf`) |
| 속성 정의(문자 · 숫자 · 날짜 · 선택값 …) | `owl:DatatypeProperty` + `rdfs:range` xsd 형 |
| 공통 속성(인터페이스) | 같은 모양, domain 은 인터페이스 — |
|  | 구현 타입의 속성은 그 `rdfs:subPropertyOf` |
| 참조 속성(`object_ref`) | `owl:ObjectProperty` + `rdfs:range` 대상 타입, 역방향 inverseOf |
| 관계 종류 | `owl:ObjectProperty` + domain/range(허용 타입이 하나일 때), 이행 → Transitive, |
|  | 무방향 → Symmetric, 역방향 이름 → `owl:inverseOf` |
| 관계의 유효 기간(ADR 0039) | **오늘 유효한 선만** 트리플로. 기간이 있는 선은 그 트리플을 |
|  | 가리키는 `rdf:Statement`(구체화)에 `sp:validFrom` · `sp:validTo`(xsd:date, 끝은 제외) |
| 계보 관계(ADR 0041) | W3C PROV 의 `prov:wasDerivedFrom`(파생물 → 원천)에 `rdfs:subPropertyOf`
— 원천이 |
|  | 도착이면 그 속성이, 출발이면 그 역방향 속성(`owl:inverseOf`)이 잇는다. 선의 고정은 |
|  | 구체화한 진술에 `sp:pinnedRevision`(글자) · `sp:pinnedAt`(xsd:dateTime) |
| 객체 | 개체(`rdf:type` 타입 + 구현 인터페이스), `rdfs:label` 이름, |
|  | `dcterms:identifier` 식별자, 별칭 altLabel |
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

from rdflib import (
    DCTERMS,
    OWL,
    RDF,
    RDFS,
    SKOS,
    XSD,
    BNode,
    Graph,
    Literal,
    Namespace,
    URIRef,
)
from sqlalchemy import select, union
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects import aliases, edges, typecounts
from app.modules.objects.models import ObjectInstance, ObjectRef, ObjectRelation
from app.modules.objects.services import properties_of
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectInterface, ObjectType, PropertyDef, RelationType
from app.modules.workspaces.models import WorkspaceMember
from app.shared.permissions import sees_every_department, visible_owner_clause

#: W3C PROV-O — 계보 관계를 잇는 표준 어휘(ADR 0041).
PROV = Namespace("http://www.w3.org/ns/prov#")

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

    @property
    def valid_from(self) -> URIRef:
        """선의 유효 기간 시작일(포함) — 구체화한 진술(`rdf:Statement`)에 붙는다."""
        return self.ns["validFrom"]

    @property
    def valid_to(self) -> URIRef:
        """선의 유효 기간 끝날(**제외**)."""
        return self.ns["validTo"]

    @property
    def pinned_revision(self) -> URIRef:
        """계보 선의 고정 개정 — 맺을 때(다시 고정할 때) 원천의 개정 값(ADR 0041)."""
        return self.ns["pinnedRevision"]

    @property
    def pinned_at(self) -> URIRef:
        """계보 선의 고정 시각 — 그 순간 원천의 바뀐 때."""
        return self.ns["pinnedAt"]

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
    graph.bind("prov", PROV)
    graph.bind("sp", names.ns)


def bound(names: Names) -> Graph:
    """접두어만 묶은 빈 그래프 — 그래프를 세우기 **전에** 질의를 풀어 보는 자리
    (`routes.query`)."""
    graph = Graph()
    _bind(graph, names)
    return graph


def record_types(db: Session) -> list[ObjectType]:
    """기록 타입(`usage="log"`) — 질의의 **기본 범위에서 빠진다**(ADR 0031). 기록은 수백만
    ~ 수천만 건이라 그래프에 올리면 5만 객체 상한에 늘 걸리고, 그러면 코어(축)를 건너뛰는
    물음까지 막힌다. 기록의 세기 · 나누기는 통계 · 지표가 DB 에서 바로 한다."""
    return list(db.scalars(select(ObjectType).where(ObjectType.usage == "log")))


def default_types(db: Session) -> list[str]:
    """`types` 를 안 줬을 때의 범위 — 기록이 아닌 타입 전부."""
    return [
        row.slug for row in db.scalars(select(ObjectType).where(ObjectType.usage != "log"))
    ]


def schema_graph(db: Session, names: Names) -> Graph:
    """정의 → OWL. 사용 안 함(`is_active=False`)인 것도 낸다 — 옛 데이터가 그것을 가리킨다."""
    graph = Graph()
    _bind(graph, names)
    ontology = URIRef(names.base + "ns")
    graph.add((ontology, RDF.type, OWL.Ontology))

    # **인터페이스는 클래스다** — 구현한 타입은 그 하위 클래스라, 추론기가 「시험장비는
    # 설비」 를 푼다(ADR 0006). 타입과 slug 를 함께 쓰므로 IRI 가 겹치지 않는다.
    ifaces = list(
        db.scalars(
            select(ObjectInterface).order_by(ObjectInterface.sort_order, ObjectInterface.slug)
        )
    )
    known_ifaces = {one.slug for one in ifaces}
    for iface in ifaces:
        cls = names.type_(iface.slug)
        graph.add((cls, RDF.type, OWL.Class))
        graph.add((cls, RDFS.label, Literal(iface.label, lang="ko")))
        if iface.description:
            graph.add((cls, RDFS.comment, Literal(iface.description, lang="ko")))
        for up in iface.extends_slugs or []:
            if up in known_ifaces:
                graph.add((cls, RDFS.subClassOf, names.type_(up)))
        for prop in db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "interface", PropertyDef.owner_id == iface.id)
            .order_by(PropertyDef.sort_order, PropertyDef.key)
        ):
            _add_property(graph, names, iface.slug, prop)
    catalog = interfaces.load(db)

    types = list(
        db.scalars(select(ObjectType).order_by(ObjectType.sort_order, ObjectType.slug))
    )
    for row in types:
        cls = names.type_(row.slug)
        graph.add((cls, RDF.type, OWL.Class))
        graph.add((cls, RDFS.label, Literal(row.label, lang="ko")))
        if row.description:
            graph.add((cls, RDFS.comment, Literal(row.description, lang="ko")))
        for iface_slug in row.interface_slugs or []:
            if iface_slug in known_ifaces:
                graph.add((cls, RDFS.subClassOf, names.type_(iface_slug)))
        for prop in properties_of(db, row.id):
            _add_property(graph, names, row.slug, prop)
        # **타입의 속성은 공통 속성의 하위 속성이다** — 같은 키라도 IRI 는 타입마다라
        # (`<타입>.<키>`), 이것이 있어야 「설비의 제조사」 를 구현 타입 전부에서 묻는다.
        for key, owner in interfaces.bound_keys(catalog, row.slug).items():
            graph.add((names.prop(row.slug, key), RDFS.subPropertyOf, names.prop(owner, key)))

    relations = list(db.scalars(select(RelationType).order_by(RelationType.slug)))
    for rel in relations:
        _add_relation(graph, names, rel)
    return graph


def _add_property(graph: Graph, names: Names, owner_slug: str, prop: PropertyDef) -> None:
    """타입의 속성이든 인터페이스의 공통 속성이든 같은 모양으로 — 주인이 domain 이다."""
    node = names.prop(owner_slug, prop.key)
    graph.add((node, RDFS.label, Literal(prop.label, lang="ko")))
    graph.add((node, RDFS.domain, names.type_(owner_slug)))
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
            inverse = names.prop_inverse(owner_slug, prop.key)
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
    if rel.lineage_source in ("src", "dst"):
        _add_lineage(graph, names, rel, node)
    if rel.effectivity:
        # 유효 기간을 받는 종류 — 선마다 기간이 구체화한 진술에 붙는다(ADR 0039).
        graph.add(
            (
                node,
                SKOS.note,
                Literal("유효 기간을 받는다 — 오늘 유효한 선만 실린다", lang="ko"),
            )
        )
        for term, label in (
            (names.valid_from, "유효 기간 시작일(포함)"),
            (names.valid_to, "유효 기간 끝날(제외 — 그날부터 무효)"),
        ):
            graph.add((term, RDF.type, OWL.DatatypeProperty))
            graph.add((term, RDFS.domain, RDF.Statement))
            graph.add((term, RDFS.range, XSD.date))
            graph.add((term, RDFS.label, Literal(label, lang="ko")))
    if rel.quantity_key:
        # 수량 칸(ADR 0040) — 관계 속성은 트리플로 안 나가므로 어느 칸이 수량인지만 적는다.
        graph.add(
            (
                node,
                SKOS.note,
                Literal(
                    f"수량 칸 {rel.quantity_key} — 관계 하나에 자식 몇 개"
                    "(전개가 경로 따라 곱한다)",
                    lang="ko",
                ),
            )
        )


def _add_lineage(graph: Graph, names: Names, rel: RelationType, node: URIRef) -> None:
    """계보 관계(ADR 0041) → `prov:wasDerivedFrom`(파생물 wasDerivedFrom 원천).

    원천이 **도착**인 종류는 선의 방향이 곧 「파생물 → 원천」 이라 그 속성이 바로 하위
    속성이다. 원천이 **출발**인 종류는 거꾸로라 역방향 속성을 세워(`owl:inverseOf`) 그것을
    잇는다 — 속성 자신을 이으면 추론기가 「원천이 파생물에서 만들어졌다」 로 읽는다."""
    if rel.lineage_source == "dst":
        graph.add((node, RDFS.subPropertyOf, PROV.wasDerivedFrom))
        said = "원천은 도착 — 출발이 도착에서 만들어졌다(prov:wasDerivedFrom)"
    else:
        inverse = names.relation_inverse(rel.slug)
        graph.add((inverse, RDF.type, OWL.ObjectProperty))
        if not rel.inverse_label:
            graph.add((inverse, RDFS.label, Literal(f"{rel.label}의 반대", lang="ko")))
        graph.add((inverse, OWL.inverseOf, node))
        graph.add((inverse, RDFS.subPropertyOf, PROV.wasDerivedFrom))
        said = "원천은 출발 — 도착이 출발에서 만들어졌다(역방향이 prov:wasDerivedFrom)"
    graph.add((node, SKOS.note, Literal(f"계보 관계: {said}", lang="ko")))
    for term, label, kind in (
        (names.pinned_revision, "고정 개정(맺을 때 원천의 개정)", XSD.string),
        (names.pinned_at, "고정 시각(그때 원천의 바뀐 때)", XSD.dateTime),
    ):
        graph.add((term, RDF.type, OWL.DatatypeProperty))
        graph.add((term, RDFS.domain, RDF.Statement))
        graph.add((term, RDFS.range, kind))
        graph.add((term, RDFS.label, Literal(label, lang="ko")))


#: 그래프를 세울 수 있는 객체 수 — 넘으면 **세우기 전에** 거절한다. 그래프는 메모리에 선다
#: (13만 트리플에 3.4초, 실측) — 기록 200만 건 타입을 그대로 올리면 질의 하나가 서버 메모리를
#: 다 쓴다. 넘으면 `types` 로 좁히거나 `/rdf/data` 를 트리플 스토어에 넣고 거기서 묻는다.
MAX_OBJECTS = 50_000


def _chosen_ids(db: Session, type_slugs: list[str] | None) -> set[uuid.UUID]:
    """고른 타입(인터페이스면 구현 타입 전부)의 id. 비우면 전부."""
    types = {row.id: row for row in db.scalars(select(ObjectType))}
    catalog = interfaces.load(db)
    chosen = set(type_slugs or [])
    for slug in list(chosen):
        if slug in catalog.interfaces:
            chosen |= set(interfaces.implementers(catalog, slug))
    return {row.id for row in types.values() if not type_slugs or row.slug in chosen}


def size_of(db: Session, type_slugs: list[str] | None, *, user: User) -> int:
    """그래프에 오를 객체 수 — 세우기 전에 상한을 본다."""
    from sqlalchemy import func

    return int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.merged_into_id.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                ObjectInstance.type_id.in_(_chosen_ids(db, type_slugs)),
            )
        )
        or 0
    )


def data_graph(
    db: Session, names: Names, type_slugs: list[str] | None = None, *, user: User
) -> Graph:
    """데이터 → RDF. 지워진 것 · 병합돼 사라진 것은 안 낸다. 관계는 양 끝이 나갈 때만.

    **목록과 같은 것만 낸다**(`visible_owner_clause`) — 남의 부서 객체가 목록에서는 안 보이는데
    RDF · SPARQL 로는 읽히면, 가린 것이 가린 게 아니다. 참조 · 관계의 상대도 보이는 것만 IRI 를
    받는다(IRI 에 식별자가 들어 있어 이름만 숨겨도 무엇이 있는지 드러난다)."""
    graph = Graph()
    _bind(graph, names)

    types = {row.id: row for row in db.scalars(select(ObjectType))}
    catalog = interfaces.load(db)
    extends_of = catalog.extends_of()
    # 인터페이스 slug 로 고르면 **구현 타입 전부**다(ADR 0006).
    chosen = set(type_slugs or [])
    for slug in list(chosen):
        if slug in catalog.interfaces:
            chosen |= set(interfaces.implementers(catalog, slug))
    wanted = {row.id for row in types.values() if not type_slugs or row.slug in chosen}
    props_of = {row.id: properties_of(db, row.id) for row in types.values()}
    #: 객체가 무엇이기도 한가 — 타입이 구현한 인터페이스(상위까지). **직접 싣는다**: 추론
    #: 없이도 `?x a sp:<인터페이스>` 가 답하게(추론은 트리플 상한이 있다).
    kinds_of = {
        row.id: interfaces.closure(row.interface_slugs or [], extends_of)
        for row in types.values()
    }

    seen = (
        ObjectInstance.deleted_at.is_(None),
        ObjectInstance.merged_into_id.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )
    # 타입은 **SQL 에서** 고른다 — 파이썬에서 거르면 범위를 좁혀도 표 전체를 읽는다.
    rows = list(
        db.scalars(select(ObjectInstance).where(*seen, ObjectInstance.type_id.in_(wanted)))
    )
    iri_of: dict[uuid.UUID, URIRef] = {}
    for row in rows:
        iri_of[row.id] = names.individual(types[row.type_id].slug, row.key, row.id)
    # 참조 · 관계가 가리키는 상대는 범위 밖 타입이어도 IRI 가 필요하다 — 보이는 것만, 그리고
    # **실제로 가리키는 것만** 읽는다. 예전에는 범위 밖 객체 전부를 읽어서, 축 몇 타입으로
    # 좁혀도 기록 2,000만 건의 id 를 다 올렸다(ADR 0031 — 기록을 기본 범위에서 빼면 늘 이
    # 길이다).
    all_iri: dict[uuid.UUID, URIRef] = dict(iri_of)
    alive = edges.alive(ObjectRelation, edges.today())
    mine = aliased(ObjectInstance)
    inside = select(mine.id).where(
        mine.deleted_at.is_(None),
        mine.merged_into_id.is_(None),
        visible_owner_clause(user, mine.owner_workspace_id),
        mine.type_id.in_(wanted),
    )
    if type_slugs:
        pointed = union(
            select(ObjectRef.dst_id.label("id")).where(ObjectRef.src_id.in_(inside)),
            select(ObjectRelation.dst_object_id.label("id")).where(
                ObjectRelation.src_object_id.in_(inside), alive
            ),
        ).subquery("pointed")
        others = db.execute(
            select(ObjectInstance.id, ObjectInstance.type_id, ObjectInstance.key).where(
                *seen,
                ObjectInstance.type_id.not_in(wanted),
                ObjectInstance.id.in_(select(pointed.c.id)),
            )
        )
        for other_id, other_type, other_key in others:
            if other_type in types:
                all_iri[other_id] = names.individual(
                    types[other_type].slug, other_key, other_id
                )

    alias_of = aliases.human_of(db, [row.id for row in rows]) if rows else {}
    for row in rows:
        object_type = types[row.type_id]
        node = iri_of[row.id]
        graph.add((node, RDF.type, names.type_(object_type.slug)))
        for iface_slug in kinds_of[row.type_id]:
            graph.add((node, RDF.type, names.type_(iface_slug)))
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

    # 선은 범위 안에서 나가는 것만 쓴다(아래) — 좁혔으면 그것만 읽는다. **오늘 유효한 선만**
    # (ADR 0039) — 트리플에는 기간을 담을 자리가 없어, 지난 선을 실으면 지금 선과 구별되지
    # 않는다. 기간이 있는 선은 그 트리플의 진술(구체화)에 기간을 단다.
    stmt = select(ObjectRelation).where(alive)
    if type_slugs:
        stmt = stmt.where(ObjectRelation.src_object_id.in_(inside))
    for edge in db.scalars(stmt):
        src = iri_of.get(edge.src_object_id)
        dst = iri_of.get(edge.dst_object_id) or all_iri.get(edge.dst_object_id)
        if src is None or dst is None:
            continue
        predicate = names.relation(edge.relation)
        graph.add((src, predicate, dst))
        if (
            edge.valid_from is not None
            or edge.valid_to is not None
            or edge.pinned_at is not None
        ):
            said = BNode()
            graph.add((said, RDF.type, RDF.Statement))
            graph.add((said, RDF.subject, src))
            graph.add((said, RDF.predicate, predicate))
            graph.add((said, RDF.object, dst))
            # 계보 선의 고정(ADR 0041) — 「이 결과는 도면 Rev.C 기준」.
            if edge.pinned_revision is not None:
                graph.add((said, names.pinned_revision, Literal(edge.pinned_revision)))
            if edge.pinned_at is not None:
                graph.add(
                    (said, names.pinned_at, Literal(edge.pinned_at, datatype=XSD.dateTime))
                )
            if edge.valid_from is not None:
                graph.add(
                    (said, names.valid_from, Literal(edge.valid_from, datatype=XSD.date))
                )
            if edge.valid_to is not None:
                graph.add((said, names.valid_to, Literal(edge.valid_to, datatype=XSD.date)))
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

#: (설치 주소, 타입들, 추론, **보이는 범위**) → (판, 세운 시각, 그래프, 저장된 트리플 수,
#: 추론이 더한 수). 수도 함께 둔다 — 그래프의 길이는 정의 트리플까지 세어, 처음과 캐시에서
#: 답의 `triples` 가 달랐다.
_CACHE: dict[
    tuple[str, tuple[str, ...], bool, str], tuple[tuple[Any, ...], float, Graph, int, int]
] = {}
_CACHE_MAX = 2
#: 판(version)이 못 잡는 변화도 있다 — 속성 정의에는 `updated_at` 이 없어 이름만 고치면 수가
#: 그대로다. 그래서 이 시간이 지나면 어차피 다시 세운다(질의는 3초 안이라 감당된다).
_CACHE_TTL_SECONDS = 120.0

#: 추론을 켤 수 있는 상한 — 5만 트리플에 20초(실측). 그 위는 범위를 좁히게 한다.
INFER_MAX_TRIPLES = 20_000


def version(db: Session, type_ids: set[uuid.UUID]) -> tuple[Any, ...]:
    """지금 데이터의 판 — 정의 · **범위 안 타입(`type_ids`)의 객체** · 관계가 바뀌면 값이
    달라진다(속성 이름만 고친 것은 못 잡는다 — 그것은 TTL 이 받는다).

    정의(타입 · 인터페이스)는 `updated_at` 의 **합**으로 본다 — 최댓값으로 보면 시계가 뒤로
    가는 순간(WSL 에서 실측: 「Time jumped backwards」) 고친 시각이 옛 최댓값보다 작아져 고친
    것이 판에 안 잡히고, 120초 동안 옛 이름이 나온다. 합은 어느 행이 어느 쪽으로 바뀌든
    달라진다. 행이 수십 개라 값싸다.

    객체는 **범위 안 타입만** 본다 — 타입마다 최근 수정 시각(색인 `ix_objects_type_updated` 를
    거꾸로 한 줄)과 계수표의 수(ADR 0038). 예전에는 객체 표 전체의 최근 수정 시각과 수를
    질의마다 셌다 — 200만 건 실측 DB 에서 0.45초(캐시가 식었으면 2.2초)였고, 범위에서 빠진
    기록이 한 건만 들어와도 판이 바뀌어 그래프를 처음부터 다시 세웠다(축 1.3만 객체에 5 ~ 8초,
    2026-10-30). 지금은 0.02초다. 범위 밖이지만 범위 안이 가리키는 객체(IRI 만 싣는다)의
    식별자 변경 · 지움은 판이 못 잡는다 — TTL 이 받는다. 관계는 축끼리 잇는 것이라(기록은
    참조 칸으로 잇는다) 전부 센다."""
    from sqlalchemy import func, text

    def stamp(column: Any) -> Any:
        return db.scalar(select(func.sum(func.extract("epoch", column))))

    wanted = sorted(type_ids)
    counts = typecounts.counts(db)
    latest = db.scalar(
        text("""
            SELECT max(x.latest)
              FROM unnest(CAST(:types AS uuid[])) AS t(id)
              CROSS JOIN LATERAL (
                  SELECT max(o.updated_at) AS latest FROM objects o WHERE o.type_id = t.id
              ) x
        """),
        {"types": [str(one) for one in wanted]},
    )
    return (
        stamp(ObjectType.updated_at),
        db.scalar(select(func.count()).select_from(ObjectType)),
        # 인터페이스 — 공통 속성만 고쳐도 `updated_at` 이 바뀐다(라우트 · 가져오기가 고친다).
        stamp(ObjectInterface.updated_at),
        db.scalar(select(func.count()).select_from(ObjectInterface)),
        db.scalar(select(func.count()).select_from(PropertyDef)),
        latest,
        tuple(counts.get(one, 0) for one in wanted),
        db.scalar(select(func.count()).select_from(ObjectRelation)),
        # 선의 기간을 고쳐도(ADR 0039) · 날짜가 넘어가도 실리는 선이 바뀐다.
        db.scalar(select(func.max(ObjectRelation.updated_at))),
        edges.today(),
        # 다시 고정(ADR 0041)은 선의 바뀐 때를 안 올린다 — 고정 시각의 합으로 본다(합은 어느
        # 선이 어느 쪽으로 바뀌어도 달라진다).
        stamp(ObjectRelation.pinned_at),
    )


def scope_of(db: Session, user: User) -> str:
    """이 사람이 **무엇을 볼 수 있나**를 한 글자로 — 캐시 열쇠다.

    모든 부서를 보면(시스템 관리자 · 조회가 전원에게 열린 설치 — 기본, ADR 0034) 전부, 그 밖은
    속한 부서의 id 들(전역 + 내 부서). 열쇠에 이것이 없으면 관리자가 세운 그래프를 다음 사람이
    받아 간다."""
    if sees_every_department(user):
        return "*"
    mine = db.scalars(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    return ",".join(sorted(str(one) for one in mine))


def graph_for(
    db: Session, names: Names, type_slugs: list[str] | None, infer: bool, *, user: User
) -> tuple[Graph, int, int]:
    """질의할 그래프 — (그래프, 저장된 트리플 수, 추론이 더한 수).

    같은 판 · 같은 보이는 범위면 다시 세우지 않는다."""
    key = (names.base, tuple(sorted(type_slugs or ())), infer, scope_of(db, user))
    now = version(db, _chosen_ids(db, type_slugs))
    hit = _CACHE.get(key)
    if hit and hit[0] == now and time.monotonic() - hit[1] < _CACHE_TTL_SECONDS:
        return hit[2], hit[3], hit[4]

    schema = schema_graph(db, names)
    data = data_graph(db, names, type_slugs, user=user)
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
    _CACHE[key] = (now, time.monotonic(), graph, stored, added)
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
