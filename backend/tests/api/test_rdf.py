"""RDF/OWL 내보내기 — 정의 · 데이터가 형식 온톨로지가 되고, **추론기가 상속 · 역관계 · 이행을
푼다.**"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from rdflib import DCTERMS, OWL, RDF, RDFS, Graph, URIRef
from sqlalchemy.orm import Session

from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed
from tests.api.test_ontology import _import, _make_object, _make_type, _uniq


def _turtle(client: TestClient, admin: Signed, path: str) -> Graph:
    response = client.get(path, headers=admin.headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/turtle")
    return Graph().parse(data=response.text, format="turtle")


def test_정의와_데이터가_OWL_RDF_로_나가고_추론이_된다(
    client: TestClient, admin: Signed
) -> None:
    # 제품(인터페이스) ⊃ 개발모델. 모델은 과제를 참조하고(역방향 이름 있음), 과제끼리 「선행」
    # 관계(이행). 「개발모델은 제품이다」 는 인터페이스 구현이다(ADR 0006).
    product = _uniq("product")
    client.post(
        "/api/ontology/interfaces",
        json={"slug": product, "label": "제품"},
        headers=admin.headers,
    ).raise_for_status()
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    task = _make_type(client, admin, label="과제", key_policy="required")
    precedes = f"precedes_{task[-6:]}"
    assert (
        client.patch(
            f"/api/ontology/types/{model}",
            json={"interface_slugs": [product]},
            headers=admin.headers,
        ).status_code
        == 200
    )
    client.post(
        f"/api/ontology/types/{model}/properties",
        json={
            "key": "task",
            "label": "과제",
            "data_type": "object_ref",
            "ref_type_slug": task,
            "inverse_label": "개발모델",
        },
        headers=admin.headers,
    ).raise_for_status()
    made = client.post(
        "/api/ontology/relation-types",
        json={
            "slug": precedes,
            "label": "선행",
            "inverse_label": "후행",
            "transitive": True,
            "acyclic": True,
            "src_type_slugs": [task],
            "dst_type_slugs": [task],
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    t1 = _make_object(client, admin, task, label="과제 1", key="T1")
    t2 = _make_object(client, admin, task, label="과제 2", key="T2")
    t3 = _make_object(client, admin, task, label="과제 3", key="T3")
    _make_object(
        client, admin, model, label="모델 A", key="M-A", properties={"task": t2["id"]}
    )
    for src, dst in ((t1, t2), (t2, t3)):
        client.post(
            f"/api/objects/{task}/{src['id']}/relations",
            json={"relation": precedes, "dst_object_id": dst["id"]},
            headers=admin.headers,
        ).raise_for_status()

    schema = _turtle(client, admin, "/api/rdf/schema")
    ns = "http://testserver/ns#"
    cls_model, cls_product = URIRef(ns + model), URIRef(ns + product)
    assert (cls_model, RDF.type, OWL.Class) in schema
    assert (cls_model, RDFS.subClassOf, cls_product) in schema
    ref = URIRef(f"{ns}{model}.task")
    assert (ref, RDF.type, OWL.ObjectProperty) in schema
    assert (ref, RDFS.range, URIRef(ns + task)) in schema
    assert (URIRef(f"{ns}{model}.task.inverse"), OWL.inverseOf, ref) in schema
    rel = URIRef(f"{ns}rel.{precedes}")
    assert (rel, RDF.type, OWL.TransitiveProperty) in schema

    data = _turtle(client, admin, f"/api/rdf/data?type={model}&type={task}")
    iri_m1 = URIRef(f"http://testserver/o/{model}/M-A")
    iri_t1, iri_t2, iri_t3 = (URIRef(f"http://testserver/o/{task}/T{n}") for n in (1, 2, 3))
    assert (iri_m1, RDF.type, cls_model) in data
    assert (iri_m1, ref, iri_t2) in data
    assert (iri_t1, rel, iri_t2) in data and (iri_t2, rel, iri_t3) in data
    # 구현한 인터페이스는 **직접 실린다** — 추론 없이도 「제품인 것 전부」 가 답한다(ADR 0006).
    assert (iri_m1, RDF.type, cls_product) in data
    # 이행은 저장된 것만 — 추론은 여기 없다.
    assert (iri_t1, rel, iri_t3) not in data

    inferred = _turtle(client, admin, f"/api/rdf/inferred?type={model}&type={task}")
    # 역관계: 과제 2 의 개발모델은 모델 A. 이행: 1 → 3.
    # (제품인 것은 이미 실려 있어 추론이 되풀이하지 않는다.)
    assert (iri_m1, RDF.type, cls_product) not in inferred
    assert (iri_t2, URIRef(f"{ns}{model}.task.inverse"), iri_m1) in inferred
    assert (iri_t1, rel, iri_t3) in inferred
    assert (iri_m1, RDF.type, cls_model) not in inferred  # 이미 있던 것은 안 되풀이한다
    # **어휘 공리는 섞이지 않는다.** `rdf:HTML a rdfs:Datatype` 같은 것이 수백 줄 나오면 새로
    # 알게 된 사실이 그 속에 묻힌다(실측).
    vocab = (
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "http://www.w3.org/2000/01/rdf-schema#",
    )
    assert not [one for one in inferred.subjects() if str(one).startswith(vocab)]

    # 다른 형식도 같은 내용.
    jsonld = client.get("/api/rdf/schema?format=jsonld", headers=admin.headers)
    assert jsonld.headers["content-type"].startswith("application/ld+json")
    assert (cls_model, RDFS.subClassOf, cls_product) in Graph().parse(
        data=jsonld.text, format="json-ld"
    )


def test_상위_타입은_없어졌다_이유를_말하고_거절한다(
    client: TestClient, admin: Signed
) -> None:
    """**조용히 무시하지 않는다** — 무시하면 보낸 쪽은 계층이 적용된 줄 안다. 화면(타입 수정)과
    파일(정의 가져오기) 둘 다 인터페이스로 가라고 말한다."""
    a = _make_type(client, admin, label="A")
    refused = client.patch(
        f"/api/ontology/types/{a}", json={"parent_slug": "nope"}, headers=admin.headers
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"].endswith("ONTOLOGY-0028")
    assert "인터페이스" in refused.json()["error"]["message"]

    by_file = _import(client, admin, {"types": [{"slug": a, "parent_slug": "nope"}]})
    assert by_file.status_code == 409, by_file.text
    assert "interfaces" in by_file.json()["error"]["message"]

    # 스키마에는 상위 타입 대신 구현 인터페이스가 실린다.
    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/schema", headers=admin.headers).json()["types"]
    }
    assert "parent_slug" not in types[a]
    assert types[a]["interface_slugs"] == []


def test_인터페이스는_클래스이고_공통_속성은_구현_타입_속성의_상위다(
    client: TestClient, admin: Signed
) -> None:
    """**추론 없이도** 「설비인 것 전부」 와 「설비의 제조사」 가 답한다 — 객체에 인터페이스
    `rdf:type` 이 직접 실리고, 타입의 속성은 공통 속성의 `rdfs:subPropertyOf` 다(ADR 0006)."""
    equip = _uniq("equip")
    client.post(
        "/api/ontology/interfaces",
        json={"slug": equip, "label": "설비"},
        headers=admin.headers,
    ).raise_for_status()
    client.post(
        f"/api/ontology/interfaces/{equip}/properties",
        json={"key": "maker", "label": "제조사", "data_type": "text"},
        headers=admin.headers,
    ).raise_for_status()
    tester = _make_type(client, admin, label="시험장비", interface_slugs=[equip])
    meter = _make_type(client, admin, label="계측기", interface_slugs=[equip])
    _make_object(client, admin, tester, label="시험기 1", properties={"maker": "A사"})
    _make_object(client, admin, meter, label="계측기 1", properties={"maker": "B사"})

    ns = "http://testserver/ns#"
    schema = _turtle(client, admin, "/api/rdf/schema")
    assert (URIRef(ns + equip), RDF.type, OWL.Class) in schema
    assert (URIRef(f"{ns}{equip}.maker"), RDFS.domain, URIRef(ns + equip)) in schema
    for kind in (tester, meter):
        assert (URIRef(ns + kind), RDFS.subClassOf, URIRef(ns + equip)) in schema
        assert (
            URIRef(f"{ns}{kind}.maker"),
            RDFS.subPropertyOf,
            URIRef(f"{ns}{equip}.maker"),
        ) in schema

    # 인터페이스 slug 로 고르면 구현 타입 전부 — 추론 없이 `a sp:<인터페이스>`.
    asked = client.post(
        "/api/rdf/query",
        json={
            "query": (
                f"PREFIX sp: <{ns}>\n"
                "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
                "SELECT ?name ?maker WHERE { "
                f"?x a sp:{equip} ; rdfs:label ?name ; ?p ?maker . "
                f"?p rdfs:subPropertyOf sp:{equip}.maker }} ORDER BY ?name"
            ),
            "types": [equip],
        },
        headers=admin.headers,
    )
    assert asked.status_code == 200, asked.text
    rows = asked.json()["rows"]
    assert [(str(one["name"]), str(one["maker"])) for one in rows] == [
        ("계측기 1", "B사"),
        ("시험기 1", "A사"),
    ]

    # **이름을 바꾸면 다음 질의가 새 이름을 본다** — 캐시 판에 인터페이스가 들어 있다.
    client.patch(
        f"/api/ontology/interfaces/{equip}", json={"label": "장비"}, headers=admin.headers
    ).raise_for_status()
    renamed = client.post(
        "/api/rdf/query",
        json={
            "query": (
                f"PREFIX sp: <{ns}>\n"
                "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
                f"SELECT ?label WHERE {{ sp:{equip} rdfs:label ?label }}"
            ),
            "types": [equip],
        },
        headers=admin.headers,
    )
    assert [str(one["label"]) for one in renamed.json()["rows"]] == ["장비"]


def test_목록에서_안_보이는_남의_부서_객체는_RDF_로도_안_나간다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """**가린 것은 어느 길로도 가려져야 한다.** 예전에는 `/rdf/data` · `/rdf/query` 가 로그인만
    보고 전부 내서, 목록에서 안 보이는 남의 부서 객체가 RDF · SPARQL(MCP `rdf_query`)로는
    읽혔다."""
    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    part = _make_type(client, admin, label="부품", key_policy="required")
    for workspace, key in (
        (other.slug, "HIDDEN-1"),
        (member.workspace, "MINE-1"),
        (None, "ALL-1"),
    ):
        made = client.post(
            f"/api/objects/{part}",
            json={"workspace_slug": workspace, "key": key, "label": key},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text

    def keys(who: Signed) -> set[str]:
        graph = _turtle(client, who, f"/api/rdf/data?type={part}")
        return {str(one) for one in graph.objects(None, DCTERMS.identifier)}

    assert keys(member) == {"MINE-1", "ALL-1"}
    assert keys(admin) == {"HIDDEN-1", "MINE-1", "ALL-1"}

    query = {
        "query": "SELECT ?k WHERE { ?x <http://purl.org/dc/terms/identifier> ?k }",
        "types": [part],
    }

    def asked(who: Signed) -> set[str]:
        got = client.post("/api/rdf/query", json=query, headers=who.headers)
        assert got.status_code == 200, got.text
        return {row["k"] for row in got.json()["rows"]}

    # 관리자가 먼저 세운 그래프를 멤버가 받아 가지 않는다(캐시 열쇠에 보이는 범위가 있다).
    assert asked(admin) == {"HIDDEN-1", "MINE-1", "ALL-1"}
    assert asked(member) == {"MINE-1", "ALL-1"}
