"""RDF/OWL 내보내기 — 정의 · 데이터가 형식 온톨로지가 되고, **추론기가 상속 · 역관계 · 이행을
푼다.**"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rdflib import OWL, RDF, RDFS, Graph, URIRef

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_type


def _turtle(client: TestClient, admin: Signed, path: str) -> Graph:
    response = client.get(path, headers=admin.headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/turtle")
    return Graph().parse(data=response.text, format="turtle")


def test_정의와_데이터가_OWL_RDF_로_나가고_추론이_된다(
    client: TestClient, admin: Signed
) -> None:
    # 제품 ⊃ 개발모델. 모델은 과제를 참조하고(역방향 이름 있음), 과제끼리 「선행」 관계(이행).
    product = _make_type(client, admin, label="제품")
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    task = _make_type(client, admin, label="과제", key_policy="required")
    precedes = f"precedes_{task[-6:]}"
    assert (
        client.patch(
            f"/api/ontology/types/{model}",
            json={"parent_slug": product},
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
    # 저장된 것만 — 추론은 여기 없다.
    assert (iri_m1, RDF.type, cls_product) not in data
    assert (iri_t1, rel, iri_t3) not in data

    inferred = _turtle(client, admin, f"/api/rdf/inferred?type={model}&type={task}")
    # 상속: 개발모델이면 제품이다. 역관계: 과제 2 의 개발모델은 모델 A. 이행: 1 → 3.
    assert (iri_m1, RDF.type, cls_product) in inferred
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


def test_상위_타입은_있어야_하고_고리를_이루지_않는다(
    client: TestClient, admin: Signed
) -> None:
    a = _make_type(client, admin, label="A")
    b = _make_type(client, admin, label="B")
    assert (
        client.patch(
            f"/api/ontology/types/{a}", json={"parent_slug": "nope"}, headers=admin.headers
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/ontology/types/{a}", json={"parent_slug": a}, headers=admin.headers
        ).status_code
        == 409
    )
    client.patch(f"/api/ontology/types/{b}", json={"parent_slug": a}, headers=admin.headers)
    # A 의 상위를 B 로 하면 A → B → A.
    assert (
        client.patch(
            f"/api/ontology/types/{a}", json={"parent_slug": b}, headers=admin.headers
        ).status_code
        == 409
    )
    # 스키마에도 실린다 — 묶음 가져오기 · 내보내기가 이것을 그대로 쓴다.
    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/schema", headers=admin.headers).json()["types"]
    }
    assert types[b]["parent_slug"] == a


def test_SPARQL_로_묻는다(client: TestClient, admin: Signed) -> None:
    """MCP 로 들어온 AI 가 쓰는 자리 — 여러 타입을 건너뛰어 잇는 물음. 읽기만 한다."""
    project = _make_type(client, admin, label="프로젝트", key_policy="required")
    task = _make_type(client, admin, label="과제", key_policy="required")
    client.post(
        f"/api/ontology/types/{task}/properties",
        json={
            "key": "project",
            "label": "프로젝트",
            "data_type": "object_ref",
            "ref_type_slug": project,
        },
        headers=admin.headers,
    ).raise_for_status()
    p1 = _make_object(client, admin, project, label="프로젝트 1", key="P1")
    for n in (1, 2):
        _make_object(
            client,
            admin,
            task,
            label=f"과제 {n}",
            key=f"T{n}",
            properties={"project": p1["id"]},
        )

    ask = client.post(
        "/api/rdf/query",
        json={
            "query": f"""PREFIX sp: <http://testserver/ns#>
                PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
                SELECT ?name (COUNT(?t) AS ?n) WHERE {{
                  ?t a sp:{task} ; sp:{task}.project ?p . ?p rdfs:label ?name
                }} GROUP BY ?name""",
            "types": [project, task],
        },
        headers=admin.headers,
    )
    assert ask.status_code == 200, ask.text
    body = ask.json()
    assert body["columns"] == ["name", "n"]
    assert body["rows"] == [{"name": "프로젝트 1", "n": 2}]
    assert body["truncated"] is False and body["triples"] > 0
    assert body["prefixes"]["sp"] == "http://testserver/ns#"

    # 잘리면 잘렸다고 말한다 — 「전부 이것뿐」 으로 읽으면 안 된다.
    cut = client.post(
        "/api/rdf/query",
        json={
            "query": f"SELECT ?t WHERE {{ ?t a <http://testserver/ns#{task}> }}",
            "types": [task],
            "limit": 1,
        },
        headers=admin.headers,
    ).json()
    assert len(cut["rows"]) == 1 and cut["truncated"] is True

    # 쓰기 · 바깥 호출은 막는다.
    for bad in (
        "INSERT DATA { <http://x> <http://y> <http://z> }",
        "SELECT ?x WHERE { SERVICE <http://evil.example/sparql> { ?x ?y ?z } }",
    ):
        refused = client.post(
            "/api/rdf/query", json={"query": bad, "types": [task]}, headers=admin.headers
        )
        assert refused.status_code == 409, bad
    broken = client.post(
        "/api/rdf/query",
        json={"query": "SELECT ?x WHERE {", "types": [task]},
        headers=admin.headers,
    )
    assert broken.status_code == 409 and "이해하지" in broken.json()["error"]["message"]
