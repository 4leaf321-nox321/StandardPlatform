"""SPARQL 사본(ADR 0070) — **메모리 그래프와 같은 트리플**, 묻는 길이 맞게 갈린다.

사본은 밤에 DB 에서 N-Triples 를 흘려 쓰고(`copyexport`) Oxigraph 에 싣는다(`copycli`). 메모리
그래프(`export.data_graph`)와 모양이 어긋나면 같은 SPARQL 이 길에 따라 다른 답을 낸다 — 그래서
같은 세계를 둘로 만들어 견준다.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rdflib import RDF, BNode, Graph, URIRef
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.objects.models import ObjectRelation
from app.modules.rdf import copycli, copyexport, export, store
from tests.api.conftest import Signed
from tests.api.test_aliases import _aliases
from tests.api.test_edge_fields import _bom_kind
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type


@pytest.fixture
def world(client: TestClient, admin: Signed) -> dict[str, Any]:
    vendor = _make_type(client, admin, label="업체", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    for key, kind, extra in (
        ("weight", "number", {}),
        ("note", "text", {}),
        ("grade", "enum", {"enum_options": ["A", "B"]}),
        ("made", "date", {}),
        ("ok", "bool", {}),
        ("tags", "text", {"multi": True}),
    ):
        _make_property(client, admin, part, key=key, label=key, data_type=kind, **extra)
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    product = _make_type(client, admin, label="제품", key_policy="optional")
    kind = _bom_kind(client, admin, product, part)
    kr = _make_object(client, admin, vendor, label='한국 "업체"', key="KR-1")
    x = _make_object(
        client,
        admin,
        part,
        label="X 부품\n둘째 줄",
        key="X/1 한",
        properties={
            "weight": 2.5,
            "note": "메모",
            "grade": "A",
            "made": "2026-03-01",
            "ok": True,
            "tags": ["a", "b"],
            "vendor": kr["id"],
        },
    )
    y = _make_object(client, admin, part, label="Y", properties={"weight": 3})
    p1 = _make_object(client, admin, product, label="P1", key="P-1")
    assert _aliases(client, admin, part, x["id"], ["엑스"]).status_code == 200
    plain = _link(client, admin, product, p1["id"], kind, x["id"], properties={"qty": 1})
    dated = _link(
        client,
        admin,
        product,
        p1["id"],
        kind,
        y["id"],
        properties={"qty": 2},
        valid_from="2026-01-01",
    )
    assert plain.status_code == dated.status_code == 201, dated.text
    return {
        "types": [vendor, part, product],
        "kind": kind,
        "ids": {"X": x["id"], "Y": y["id"], "P1": p1["id"], "KR": kr["id"]},
        "edges": {"dated": dated.json()["relation_id"]},
    }


def _ours(graph: Graph, slugs: list[str]) -> tuple[set[Any], set[frozenset[Any]]]:
    """이 세계의 트리플 — 객체 IRI 가 주어인 것, 그리고 구체화한 진술(빈 노드)은 그 몸통의
    모양."""
    prefixes = [f"{store.COPY_BASE}o/{slug}/" for slug in slugs]

    def mine(node: Any) -> bool:
        return isinstance(node, URIRef) and any(str(node).startswith(one) for one in prefixes)

    plain = {(s, p, o) for s, p, o in graph if mine(s)}
    said = set()
    for statement in graph.subjects(RDF.type, RDF.Statement):
        assert isinstance(statement, BNode)
        body = frozenset((p, o) for p, o in graph.predicate_objects(statement))
        if any(mine(o) for _p, o in body):
            said.add(body)
    return plain, said


def test_사본은_메모리_그래프와_같은_트리플을_낸다(
    db: Session, admin: Signed, world: dict[str, Any]
) -> None:
    """객체 · 인터페이스 · 이름 · 식별자 · 값(형마다) · 별칭 · 참조 · 오늘 유효한 관계 ·
    기간 · 고정(구체화한 진술). 이름의 따옴표 · 줄바꿈, 식별자의 `/` · 한글(IRI 에서 퍼센트
    인코딩)도."""
    db.execute(
        update(ObjectRelation)
        .where(ObjectRelation.id == uuid.UUID(world["edges"]["dated"]))
        .values(pinned_revision="B", pinned_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC))
    )
    user = db.scalar(select(User).where(User.email == admin.email))
    assert user is not None
    memory = export.data_graph(db, export.Names(store.COPY_BASE), None, user=user)
    out = io.StringIO()
    written = copyexport.write(db, out)
    db.rollback()
    copied = Graph().parse(data=out.getvalue(), format="nt")
    want_plain, want_said = _ours(memory, world["types"])
    got_plain, got_said = _ours(copied, world["types"])
    assert got_plain == want_plain
    assert got_said == want_said and len(got_said) == 1
    assert written.triples == len(copied)
    # 메모리 그래프가 내는 것을 사본도 낸다 — 몇 가지를 눈으로도.
    texts = {str(o) for _s, _p, o in got_plain}
    assert {"X 부품\n둘째 줄", "엑스", "메모", "2.5", "2026-03-01"} <= texts


def test_사본이_있으면_사본으로_묻고_없거나_추론이면_메모리로(
    client: TestClient,
    admin: Signed,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    part = world["types"][1]
    asked = {
        "query": (
            "PREFIX sp: <http://testserver/ns#> SELECT ?l WHERE { ?p a sp:"
            + part
            + " ; <http://www.w3.org/2000/01/rdf-schema#label> ?l } ORDER BY ?l"
        ),
        "types": [part],
    }
    memory = client.post("/api/rdf/query", json=asked, headers=admin.headers).json()
    assert memory["source"] == "memory"

    assert copycli.load(tmp_path) == 0
    monkeypatch.setattr(get_settings(), "rdf_store_dir", str(tmp_path))
    copied = client.post("/api/rdf/query", json=asked, headers=admin.headers).json()
    assert copied["source"] == "copy" and copied["copied_at"]
    assert copied["rows"] == memory["rows"] == [{"l": "X 부품\n둘째 줄"}, {"l": "Y"}]
    # IRI 는 요청의 바탕으로 돌아온다(사본의 바탕이 안 보인다).
    iri = client.post(
        "/api/rdf/query",
        json={"query": asked["query"].replace("SELECT ?l", "SELECT ?p ?l")},
        headers=admin.headers,
    ).json()
    assert all(row["p"].startswith("http://testserver/o/") for row in iri["rows"])
    # 추론은 메모리로(사본에는 추론이 없다).
    inferred = client.post(
        "/api/rdf/query", json={**asked, "infer": True}, headers=admin.headers
    ).json()
    assert inferred["source"] == "memory"
    # 두 번째 싣기는 반대쪽 폴더 — 바꿔 끼운다.
    assert copycli.load(tmp_path) == 0
    assert (tmp_path / store.ACTIVE).resolve().name == "slot-b"


def test_표준_SPARQL_창구는_사본에_묻고_JSON_결과를_준다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = (
        "PREFIX sp: <http://testserver/ns#> SELECT ?k WHERE { ?p a sp:"
        + world["types"][2]
        + " ; <http://purl.org/dc/terms/identifier> ?k }"
    )
    monkeypatch.setattr(get_settings(), "rdf_store_dir", str(tmp_path / "없음"))
    missing = client.get("/api/rdf/sparql", params={"query": query}, headers=admin.headers)
    assert (
        missing.status_code == 503 and "사본이 없습니다" in missing.json()["error"]["message"]
    )

    assert copycli.load(tmp_path) == 0
    monkeypatch.setattr(get_settings(), "rdf_store_dir", str(tmp_path))
    got = client.get("/api/rdf/sparql", params={"query": query}, headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("application/sparql-results+json")
    assert got.headers["x-sp-copied-at"] and got.headers["x-sp-truncated"] == "false"
    assert got.json() == {
        "head": {"vars": ["k"]},
        "results": {"bindings": [{"k": {"type": "literal", "value": "P-1"}}]},
    }
    body = client.post(
        "/api/rdf/sparql",
        content=query.encode(),
        headers={**admin.headers, "Content-Type": "application/sparql-query"},
    )
    form = client.post(
        "/api/rdf/sparql",
        data={"query": query},
        headers=admin.headers,
    )
    assert body.json() == form.json() == got.json()
    ask = client.get(
        "/api/rdf/sparql", params={"query": "ASK { ?s ?p ?o }"}, headers=admin.headers
    )
    assert ask.json() == {"head": {}, "boolean": True}
    for bad in (
        "DELETE WHERE { ?s ?p ?o }",
        "SELECT * WHERE { SERVICE <http://x> { ?s ?p ?o } }",
    ):
        refused = client.get("/api/rdf/sparql", params={"query": bad}, headers=admin.headers)
        assert refused.status_code == 409, bad
    assert client.get("/api/rdf/sparql", params={"query": query}).status_code == 401
    # 일반 사용자도 묻는다(보는 범위가 전부인 설치).
    assert (
        client.get(
            "/api/rdf/sparql", params={"query": query}, headers=member.headers
        ).status_code
        == 200
    )


def test_부서로_좁힌_설치에서는_사본에_못_묻고_메모리로_간다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    department_scope: None,
) -> None:
    assert copycli.load(tmp_path) == 0
    monkeypatch.setattr(get_settings(), "rdf_store_dir", str(tmp_path))
    part = world["types"][1]
    asked = {
        "query": f"PREFIX sp: <http://testserver/ns#> SELECT ?p WHERE {{ ?p a sp:{part} }}",
        "types": [part],
    }
    answered = client.post("/api/rdf/query", json=asked, headers=member.headers)
    assert answered.status_code == 200 and answered.json()["source"] == "memory"
    denied = client.get(
        "/api/rdf/sparql", params={"query": asked["query"]}, headers=member.headers
    )
    assert denied.status_code == 403 and "모든 부서" in denied.json()["error"]["message"]


def test_사본의_질의는_시간_상한에서_끊긴다(
    client: TestClient,
    admin: Signed,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oxigraph 의 질의에는 시간 상한이 없다 — 따로 도는 프로세스로 돌려 끊는다."""
    assert copycli.load(tmp_path) == 0
    monkeypatch.setattr(get_settings(), "rdf_store_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "rdf_query_seconds", 1)
    endless = (
        "SELECT (COUNT(*) AS ?n) WHERE "
        "{ ?a ?b ?c . ?d ?e ?f . ?g ?h ?i . ?j ?k ?l . ?m ?n2 ?o }"
    )
    cut = client.post("/api/rdf/query", json={"query": endless}, headers=admin.headers)
    assert cut.status_code == 504, cut.text
    assert "초 안에 끝나지 않았습니다" in cut.json()["error"]["message"]
