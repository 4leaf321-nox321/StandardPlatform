"""지식 그래프 — **상한이 실제로 걸리나, 잘린 것을 말하나, 남의 것이 새나.**

그림이 맞는지는 눈으로 보는 것이고, 여기서 보는 것은 그 그림의 **재료가 거짓말을
안 하나** 다: 잘렸는데 「전부」 라고 하지 않나, 남의 부서 객체가 선의 저쪽 끝으로
세어지지 않나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _link, _make_object, _make_relation, _make_type


def _hidden_workspace(client: TestClient, admin: Signed) -> str:
    """시험 부서 사람이 멤버가 아닌 부서 하나."""
    slug = f"other-{uuid.uuid4().hex[:6]}"
    response = client.post(
        "/api/workspaces", json={"slug": slug, "name": "남의 부서"}, headers=admin.headers
    )
    assert response.status_code == 201, response.text
    return slug


def _neighborhood(client: TestClient, who: Signed, focus: str, **params: Any) -> Any:
    response = client.get(
        "/api/graph/neighborhood", params={"focus": focus, **params}, headers=who.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_정의_그래프는_타입과_관계_종류를_준다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    assert _link(client, admin, part, bolt["id"], kind, acme["id"]).status_code == 201

    body = client.get("/api/graph/overview", headers=admin.headers).json()
    slugs = {node["slug"]: node for node in body["nodes"]}
    assert slugs[part]["count"] == 1
    assert slugs[vendor]["count"] == 1
    found = [
        edge
        for edge in body["edges"]
        if edge["relation"] == kind and edge["src_type"] == part and edge["dst_type"] == vendor
    ]
    assert len(found) == 1
    assert found[0]["count"] == 1
    assert found[0]["label"] == "공급받음"


def test_정의만_있고_빈_관계도_0_으로_그린다(client: TestClient, admin: Signed) -> None:
    """**「왜 이 선이 없지」 를 묻지 않게.** 정의는 했는데 아직 하나도 안 이은
    관계는 점선으로라도 있어야 한다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client, admin, "part_of", label="속함", src_type_slugs=[part], dst_type_slugs=[part]
    )
    body = client.get("/api/graph/overview", headers=admin.headers).json()
    found = [edge for edge in body["edges"] if edge["relation"] == kind]
    assert found == [
        {
            "relation": kind,
            "label": "속함",
            "directed": True,
            "src_type": part,
            "dst_type": part,
            "count": 0,
        }
    ]


def test_이웃은_양방향으로_모은다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "part_of", label="속함", inverse_label="포함")
    hub = _make_object(client, admin, part, label="구동부")
    child = _make_object(client, admin, part, label="모터")
    parent = _make_object(client, admin, part, label="차체")
    assert _link(client, admin, part, child["id"], kind, hub["id"]).status_code == 201
    assert _link(client, admin, part, hub["id"], kind, parent["id"]).status_code == 201

    body = _neighborhood(client, admin, hub["id"])
    assert {node["label"] for node in body["nodes"]} == {"구동부", "모터", "차체"}
    assert len(body["edges"]) == 2
    assert body["truncated"] is False
    focus = next(node for node in body["nodes"] if node["id"] == hub["id"])
    assert focus["degree"] == 2
    assert focus["truncated"] is False


def test_fanout_을_넘으면_잘렸다고_말한다(client: TestClient, admin: Signed) -> None:
    """**잘렸는데 말 안 하면 그 그림은 「이게 전부」 로 읽힌다.** 허브 하나가
    이웃 5천 개를 끌고 오는 것을 막되, 막았다는 사실은 노드에 적혀야 한다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    hub = _make_object(client, admin, part, label="허브")
    for i in range(5):
        leaf = _make_object(client, admin, part, label=f"잎{i}")
        assert _link(client, admin, part, hub["id"], kind, leaf["id"]).status_code == 201

    body = _neighborhood(client, admin, hub["id"], fanout=3)
    assert len(body["nodes"]) == 4  # 허브 + 3
    assert body["truncated"] is True
    focus = next(node for node in body["nodes"] if node["id"] == hub["id"])
    assert focus["degree"] == 5
    assert focus["truncated"] is True


def test_노드_상한을_넘으면_거기서_멈춘다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    hub = _make_object(client, admin, part, label="허브")
    for i in range(4):
        leaf = _make_object(client, admin, part, label=f"잎{i}")
        assert _link(client, admin, part, hub["id"], kind, leaf["id"]).status_code == 201

    body = _neighborhood(client, admin, hub["id"], limit=2)
    assert len(body["nodes"]) == 2
    assert body["node_limit"] == 2
    assert body["truncated"] is True


def test_상한은_서버가_강제한다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    one = _make_object(client, admin, part, label="A")
    body = _neighborhood(client, admin, one["id"], depth=99, fanout=99999, limit=99999)
    assert body["depth"] == 3
    assert body["fanout"] == 100
    assert body["node_limit"] == 500


def test_깊이만큼만_간다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "next", label="다음")
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    c = _make_object(client, admin, part, label="C")
    assert _link(client, admin, part, a["id"], kind, b["id"]).status_code == 201
    assert _link(client, admin, part, b["id"], kind, c["id"]).status_code == 201

    one = _neighborhood(client, admin, a["id"], depth=1)
    assert {node["label"] for node in one["nodes"]} == {"A", "B"}
    # B 에는 C 가 더 있다 — 화면은 B 에 「+1 더」 를 적는다.
    b_node = next(node for node in one["nodes"] if node["label"] == "B")
    assert b_node["truncated"] is True

    two = _neighborhood(client, admin, a["id"], depth=2)
    assert {node["label"] for node in two["nodes"]} == {"A", "B", "C"}
    assert two["truncated"] is False


def test_이미_실린_노드끼리의_선은_마저_긋는다(client: TestClient, admin: Signed) -> None:
    """fanout 에 밀린 관계도 양 끝이 화면에 있으면 그려야 — 나란히 선 둘이
    「관계없음」 으로 안 읽힌다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    hub = _make_object(client, admin, part, label="허브")
    x = _make_object(client, admin, part, label="X")
    y = _make_object(client, admin, part, label="Y")
    assert _link(client, admin, part, hub["id"], kind, x["id"]).status_code == 201
    assert _link(client, admin, part, hub["id"], kind, y["id"]).status_code == 201
    assert _link(client, admin, part, x["id"], kind, y["id"]).status_code == 201

    body = _neighborhood(client, admin, hub["id"], depth=1)
    pairs = {(edge["src"], edge["dst"]) for edge in body["edges"]}
    assert (x["id"], y["id"]) in pairs


def test_관계와_타입으로_거른다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    near = _make_relation(client, admin, "near", label="가까움")
    supplied = _make_relation(client, admin, "supplied_by", label="공급받음")
    bolt = _make_object(client, admin, part, label="볼트")
    nut = _make_object(client, admin, part, label="너트")
    acme = _make_object(client, admin, vendor, label="ACME")
    assert _link(client, admin, part, bolt["id"], near, nut["id"]).status_code == 201
    assert _link(client, admin, part, bolt["id"], supplied, acme["id"]).status_code == 201

    only_supplied = _neighborhood(client, admin, bolt["id"], relations=supplied)
    assert {node["label"] for node in only_supplied["nodes"]} == {"볼트", "ACME"}
    only_parts = _neighborhood(client, admin, bolt["id"], types=part)
    assert {node["label"] for node in only_parts["nodes"]} == {"볼트", "너트"}
    # 거른 것도 「더 있음」 이다 — 사람이 거른 것을 잊고 「이웃이 하나뿐」 으로 읽지 않게.
    focus = next(node for node in only_parts["nodes"] if node["id"] == bolt["id"])
    assert focus["truncated"] is True


def test_남의_부서_객체는_이웃에도_굵기에도_안_샌다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**없는 것과 안 보이는 것을 같은 말로 답한다.** 이웃 목록은 물론이고 degree
    와 정의 그래프의 선 굵기에서도 남의 것은 세어지면 안 된다 — 수가 새면 남의
    부서에 무엇이 몇 개 있는지 짐작할 수 있다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    other = _hidden_workspace(client, admin)
    mine = _make_object(client, admin, part, label="내 것", workspace_slug=member.workspace)
    theirs = _make_object(client, admin, part, label="남의 것", workspace_slug=other)
    assert _link(client, admin, part, mine["id"], kind, theirs["id"]).status_code == 201

    body = _neighborhood(client, member, mine["id"])
    assert [node["label"] for node in body["nodes"]] == ["내 것"]
    assert body["nodes"][0]["degree"] == 0
    assert body["truncated"] is False

    overview = client.get("/api/graph/overview", headers=member.headers).json()
    found = [edge for edge in overview["edges"] if edge["relation"] == kind]
    assert all(edge["count"] == 0 for edge in found)

    hidden = client.get(
        "/api/graph/neighborhood", params={"focus": theirs["id"]}, headers=member.headers
    )
    assert hidden.status_code == 404


def test_검색은_타입을_가리지_않는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    stamp = uuid.uuid4().hex[:6]
    _make_object(client, admin, part, label=f"볼트-{stamp}")
    _make_object(client, admin, vendor, label=f"볼트공업-{stamp}")
    other = _hidden_workspace(client, admin)
    _make_object(client, admin, part, label=f"볼트-남의것-{stamp}", workspace_slug=other)

    hits = client.get("/api/graph/search", params={"q": stamp}, headers=member.headers).json()
    assert {hit["label"] for hit in hits} == {f"볼트-{stamp}", f"볼트공업-{stamp}"}
    assert {hit["type_slug"] for hit in hits} == {part, vendor}


def test_타입_전부는_쪽_단위로_주고_전체_수를_말한다(
    client: TestClient, admin: Signed
) -> None:
    """**「전부」 를 묻는 사람에게 "N개 중 M개" 라고 답한다.** 상한에서 조용히 자르면
    그 그림은 「이 타입에는 이만큼뿐」 으로 읽힌다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    made = [_make_object(client, admin, part, label=f"부품{i}") for i in range(5)]
    assert _link(client, admin, part, made[0]["id"], kind, made[1]["id"]).status_code == 201

    first = client.get(
        "/api/graph/subgraph", params={"types": part, "limit": 3}, headers=admin.headers
    ).json()
    assert first["total"] == 5
    assert [node["label"] for node in first["nodes"]] == ["부품0", "부품1", "부품2"]
    assert first["truncated"] is True
    assert len(first["edges"]) == 1

    rest = client.get(
        "/api/graph/subgraph",
        params={"types": part, "limit": 3, "offset": 3},
        headers=admin.headers,
    ).json()
    assert [node["label"] for node in rest["nodes"]] == ["부품3", "부품4"]
    assert rest["truncated"] is False


def test_타입_전부에서_다른_타입으로_나가는_선은_더_있음으로만_적힌다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(client, admin, "supplied_by", label="공급받음")
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    assert _link(client, admin, part, bolt["id"], kind, acme["id"]).status_code == 201

    body = client.get(
        "/api/graph/subgraph", params={"types": part}, headers=admin.headers
    ).json()
    assert [node["label"] for node in body["nodes"]] == ["볼트"]
    assert body["edges"] == []
    assert body["nodes"][0]["degree"] == 1
    assert body["nodes"][0]["truncated"] is True


def test_없는_타입_전부는_404(client: TestClient, admin: Signed) -> None:
    response = client.get(
        "/api/graph/subgraph",
        params={"types": "nope_" + uuid.uuid4().hex[:6]},
        headers=admin.headers,
    )
    assert response.status_code == 404
