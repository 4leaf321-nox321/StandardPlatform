"""그래프 사본(4단계) — 싣는 파일의 모양 · 질의 경로의 지키기.

사본(Neo4j)은 시험 DB 에 없다 — 사본에 실제로 묻는 시험은 `NEO4J_TEST_URL` ·
`NEO4J_TEST_PASSWORD` 가 있을 때만 돈다(개발 PC 의 견주기 사본 등). 나머지는 사본 없이 —
없는 설치가 무엇을 말하는지, 무엇을 막는지, 내보낸 CSV 가 `neo4j-admin import` 의 모양인지.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.graph import copyexport, cypher
from app.shared.errors import AppError
from tests.api.conftest import Signed
from tests.api.test_edge_fields import _bom_kind
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type


@pytest.fixture
def world(client: TestClient, admin: Signed) -> dict[str, Any]:
    vendor = _make_type(client, admin, label="업체", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(
        client, admin, part, key="tags", label="꼬리표", data_type="text", multi=True
    )
    _make_property(client, admin, part, key="label", label="이름 칸", data_type="text")
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
    kr = _make_object(client, admin, vendor, label="한국 업체")
    x = _make_object(
        client,
        admin,
        part,
        label="X",
        properties={
            "weight": 2.5,
            "tags": ["a", "b"],
            "label": "겹치는 이름",
            "vendor": kr["id"],
        },
    )
    y = _make_object(client, admin, part, label="Y")
    p1 = _make_object(client, admin, product, label="P1")
    for target, qty in ((x, 3), (y, 1)):
        got = _link(
            client, admin, product, p1["id"], kind, target["id"], properties={"qty": qty}
        )
        assert got.status_code == 201, got.text
    return {
        "vendor": vendor,
        "part": part,
        "product": product,
        "kind": kind,
        "ids": {"X": x["id"], "Y": y["id"], "P1": p1["id"], "KR": kr["id"]},
    }


def _rows(root: Path, name: str) -> tuple[list[str], list[list[str]]]:
    header = (root / f"{name}.h.csv").read_text(encoding="utf-8").strip().split(",")
    with (root / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
        return header, list(csv.reader(handle))


def test_싣는_파일은_import_의_모양이다(
    db: Session, world: dict[str, Any], tmp_path: Path
) -> None:
    """노드는 타입마다 · 관계는 slug 마다 · 참조 칸은 `ref_<칸>` — 이름 규칙은 `copyschema` 와
    같다. 못 읽는 값(숫자 칸의 「모름」 — 옛 자료 · 손으로 고친 값)은 비운다 — 가져오기가 그
    줄에서 멈추지 않게."""
    db.execute(
        text(
            "UPDATE objects SET properties = properties || CAST(:raw AS jsonb) WHERE id = :id"
        ),
        {"raw": '{"weight": "모름"}', "id": world["ids"]["Y"]},
    )
    manifest = copyexport.export(db, tmp_path)
    copyexport.write_args(manifest, tmp_path, Path("/data/graphdb/import"))
    db.rollback()
    ids = world["ids"]
    header, rows = _rows(tmp_path, f"n_{world['part']}")
    assert header[:5] == ["id:ID", "key", "label", "status", "ws"]
    assert set(header[5:]) == {"weight:double", "tags:string[]", "p_label:string"}
    at = {name.split(":")[0]: n for n, name in enumerate(header)}
    by_id = {row[0]: row for row in rows}
    x, y = by_id[ids["X"]], by_id[ids["Y"]]
    assert x[at["label"]] == "X" and x[at["weight"]] == "2.5"
    assert x[at["tags"]] == f"a{copyexport.ARRAY_DELIMITER}b"
    assert x[at["p_label"]] == "겹치는 이름"
    assert y[at["weight"]] == ""  # 「모름」 은 숫자가 아니다
    header, rows = _rows(tmp_path, f"r_{world['kind']}")
    assert header[:4] == [":START_ID", ":END_ID", "valid_from", "valid_to"]
    assert "qty:double" in header
    qty = header.index("qty:double")
    assert sorted((row[1], row[qty]) for row in rows) == sorted(
        [(ids["X"], "3"), (ids["Y"], "1")]
    )
    _header, refs = _rows(tmp_path, f"f_{world['part']}__vendor")
    assert refs == [[ids["X"], ids["KR"]]]
    args = (tmp_path / "import.args").read_text(encoding="utf-8").splitlines()
    assert (
        f"--relationships=ref_vendor=/data/graphdb/import/f_{world['part']}__vendor.h.csv,"
        f"/data/graphdb/import/f_{world['part']}__vendor.csv" in args
    )
    assert any(one.startswith(f"--nodes=Obj:{world['part']}=") for one in args)
    assert any(one.startswith("--nodes=_SPCopy=") for one in args)
    assert manifest.nodes >= 4 and manifest.relationships >= 3


def test_모양은_SP_의_정의에서_그대로(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    got = client.get("/api/graph/cypher/schema", headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    labels = {one["label"]: one for one in body["labels"]}
    part = labels[world["part"]]
    assert {(one["name"], one["kind"]) for one in part["props"]} >= {
        ("weight", "double"),
        ("tags", "string[]"),
        ("p_label", "string"),
    }
    rels = {one["type"]: one for one in body["relationships"] if one["kind"] == "relation"}
    assert [one["name"] for one in rels[world["kind"]]["props"]] == ["memo", "opts", "qty"]
    # 참조 칸은 타입마다 한 줄 — 관계 종류 이름(`ref_<칸>`)은 같은 키의 칸끼리 같다.
    refs = [
        one
        for one in body["relationships"]
        if one["kind"] == "ref" and world["part"] in one["start"]
    ]
    assert [(one["type"], one["end"]) for one in refs] == [("ref_vendor", [world["vendor"]])]
    assert any("오늘 유효한 선만" in one for one in body["rules"])


def test_사본이_없는_설치는_그렇다고_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "graphdb_url", "")
    cypher.close()
    status = client.get("/api/graph/cypher", headers=admin.headers).json()
    assert status["enabled"] is False and status["reachable"] is False
    asked = client.post(
        "/api/graph/cypher", json={"query": "MATCH (n) RETURN n"}, headers=admin.headers
    )
    assert asked.status_code == 503
    assert "그래프 사본" in asked.json()["error"]["message"]


def test_닿지_않으면_503_이고_상태는_까닭을_적는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """새벽에 다시 싣는 중 · 꺼짐 — 질의는 503 으로 SP 도구를 권하고, 상태는 200 으로
    까닭을."""
    monkeypatch.setattr(get_settings(), "graphdb_url", "bolt://127.0.0.1:1")
    cypher.close()
    status = client.get("/api/graph/cypher", headers=admin.headers).json()
    assert status["enabled"] is True and status["reachable"] is False
    assert "닿지 않습니다" in status["error"]
    asked = client.post(
        "/api/graph/cypher", json={"query": "MATCH (n) RETURN n"}, headers=admin.headers
    )
    assert asked.status_code == 503
    cypher.close()


def test_부서로_좁힌_설치에서는_모든_부서를_보는_사람만(
    client: TestClient, member: Signed, department_scope: None
) -> None:
    denied = client.get("/api/graph/cypher/schema", headers=member.headers)
    assert denied.status_code == 403
    assert "모든 부서" in denied.json()["error"]["message"]


@pytest.mark.parametrize(
    "query",
    [
        "LOAD CSV FROM 'file:///etc/passwd' AS line RETURN line",
        "CALL dbms.listConfig() YIELD name RETURN name",
        "CALL apoc.cypher.run('MATCH (n) DETACH DELETE n', {}) YIELD value RETURN value",
        "CALL apoc.load.json('http://example.local/x') YIELD value RETURN value",
        "USE system SHOW USERS",
        "MATCH (n) RETURN n; MATCH (m) RETURN m",
        "   ",
    ],
)
def test_읽기_밖의_것은_사본에_닿기_전에_막는다(query: str) -> None:
    with pytest.raises(AppError) as caught:
        cypher.check(query)
    assert caught.value.status == 422


def test_읽기_질의는_그대로_간다() -> None:
    text = "MATCH (m:gb_model)-[:gb_bom*1..20]->(p) WHERE p.label CONTAINS $q RETURN count(p);"
    assert cypher.check(text) == text.rstrip(";")
    assert cypher.check(
        "CALL apoc.path.expand($s, 'gb_bom>', null, 1, 5) YIELD path RETURN path"
    )
    # 앞의 PROFILE · EXPLAIN 은 뗀다 — 늘 EXPLAIN 으로 먼저 본다.
    assert cypher.check("PROFILE MATCH (n) RETURN n") == "MATCH (n) RETURN n"
    assert cypher.check("  explain MATCH (n) RETURN n") == "MATCH (n) RETURN n"


_LIVE = os.environ.get("NEO4J_TEST_URL")


@pytest.mark.skipif(not _LIVE, reason="사본(Neo4j)이 없다 — NEO4J_TEST_URL 이 있을 때만")
def test_실제_사본에_읽기만_묻는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "graphdb_url", _LIVE)
    monkeypatch.setattr(get_settings(), "graphdb_password", os.environ["NEO4J_TEST_PASSWORD"])
    cypher.close()
    got = client.post(
        "/api/graph/cypher",
        json={"query": "MATCH (n:Obj) RETURN n LIMIT 5", "limit": 2},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["truncated"] is True and len(body["rows"]) == 2
    assert body["rows"][0][0]["_node"] is True and body["copy"]["copied_at"]
    wrote = client.post(
        "/api/graph/cypher",
        json={"query": "MATCH (n:Obj) SET n.label = 'x' RETURN n"},
        headers=admin.headers,
    )
    assert wrote.status_code == 422 and "읽기만" in wrote.json()["error"]["message"]
    cypher.close()
