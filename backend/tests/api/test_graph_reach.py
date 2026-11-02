"""들어오는 선 — 이웃의 방향과 영향 범위(ADR 0047).

    A ← B ← C ← D      (`uses` — B 가 A 를 쓴다)

「A 를 바꾸면 무엇이 흔들리나」 는 A 로 **들어오는** 선을 끝까지 거꾸로 따라간 것이다
(B · C · D). 트리 「위 전부」 · BOM 「어디에 쓰였나」 · 계보는 정해 둔 관계 하나만 따라가므로,
아무 관계 종류 · 참조 칸에서 같은 물음이 되는지 · 고리에서 멈추는지 · 잘리면 말하는지 · 남의
것을 길로 드러내지 않는지를 본다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_graph import _hidden_workspace, _neighborhood
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)

MAR = "2026-03-01"


def _reach(client: TestClient, who: Signed, start: str, **params: Any) -> Any:
    response = client.get(
        "/api/graph/reach", params={"start": start, **params}, headers=who.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _chain(client: TestClient, admin: Signed) -> dict[str, Any]:
    part = _make_type(client, admin, label="부품")
    uses = _make_relation(client, admin, "uses", label="씀", inverse_label="쓰임")
    made = {name: _make_object(client, admin, part, label=name) for name in "ABCD"}
    for user_, used in (("B", "A"), ("C", "B"), ("D", "C")):
        got = _link(client, admin, part, made[user_]["id"], uses, made[used]["id"])
        assert got.status_code == 201, got.text
    return {"part": part, "uses": uses, "made": made}


def _depths(body: Any) -> dict[str, int]:
    return {node["label"]: node["depth"] for node in body["nodes"]}


def test_들어오는_선을_끝까지_거꾸로_따라간다(client: TestClient, admin: Signed) -> None:
    chain = _chain(client, admin)
    made = chain["made"]
    body = _reach(client, admin, made["A"]["id"], relations=chain["uses"])
    assert _depths(body) == {"A": 0, "B": 1, "C": 2, "D": 3}
    by_id = {node["id"]: node for node in body["nodes"]}
    # 길 하나 — D 는 C 를 거쳐, C 는 B 를 거쳐 닿았다.
    assert by_id[made["D"]["id"]]["parent"] == made["C"]["id"]
    assert by_id[made["D"]["id"]]["via"] == chain["uses"]
    assert by_id[made["A"]["id"]]["parent"] is None
    assert body["levels"] == [1, 1, 1, 1]
    assert body["reached_depth"] == 3
    assert body["by_type"] == [{"type_slug": chain["part"], "type_label": "부품", "count": 3}]
    assert body["exhausted"] is True and body["truncated"] is False
    assert body["direction"] == "in"


def test_나가는_선이면_이것이_기대는_것(client: TestClient, admin: Signed) -> None:
    chain = _chain(client, admin)
    body = _reach(
        client, admin, chain["made"]["D"]["id"], relations=chain["uses"], direction="out"
    )
    assert _depths(body) == {"D": 0, "C": 1, "B": 2, "A": 3}


def test_고리는_한_번만_돈다(client: TestClient, admin: Signed) -> None:
    """트리가 아닌 관계는 돌아올 수 있다 — 한 번 닿은 것은 다시 펼치지 않는다."""
    part = _make_type(client, admin, label="부품")
    uses = _make_relation(client, admin, "uses", label="씀")
    made = {name: _make_object(client, admin, part, label=name) for name in "ABC"}
    for src, dst in (("A", "B"), ("B", "C"), ("C", "A")):
        assert (
            _link(client, admin, part, made[src]["id"], uses, made[dst]["id"]).status_code
            == 201
        )

    body = _reach(client, admin, made["A"]["id"], relations=uses)
    assert _depths(body) == {"A": 0, "C": 1, "B": 2}
    assert body["exhausted"] is True


def test_방향_없는_종류는_어느_쪽으로든_잇는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    near = _make_relation(client, admin, "near", label="이웃함", directed=False)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    assert _link(client, admin, part, a["id"], near, b["id"]).status_code == 201

    assert _depths(_reach(client, admin, a["id"], relations=near)) == {"A": 0, "B": 1}
    assert _depths(_reach(client, admin, b["id"], relations=near, direction="out")) == {
        "B": 0,
        "A": 1,
    }


def test_참조_칸도_고른_방향으로(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    _make_property(
        client,
        admin,
        part,
        key="maker",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    acme = _make_object(client, admin, vendor, label="ACME")
    bolt = _make_object(client, admin, part, label="볼트", properties={"maker": acme["id"]})
    _make_object(client, admin, part, label="너트", properties={"maker": acme["id"]})
    maker = f"ref:{part}.maker"

    pointed = _reach(client, admin, acme["id"], relations=maker)
    assert _depths(pointed) == {"ACME": 0, "볼트": 1, "너트": 1}
    assert {node["via"] for node in pointed["nodes"][1:]} == {maker}
    assert _depths(_reach(client, admin, bolt["id"], relations=maker, direction="out")) == {
        "볼트": 0,
        "ACME": 1,
    }
    # 고르지 않은 종류는 따라가지 않는다.
    other = _make_relation(client, admin, "uses", label="씀")
    assert _depths(_reach(client, admin, acme["id"], relations=other)) == {"ACME": 0}


def test_깊이와_상한에서_멈추고_잘렸다고_말한다(client: TestClient, admin: Signed) -> None:
    chain = _chain(client, admin)
    start = chain["made"]["A"]["id"]
    shallow = _reach(client, admin, start, relations=chain["uses"], depth=1)
    assert _depths(shallow) == {"A": 0, "B": 1}
    # 깊이에서 멈췄을 뿐 끝은 아니다 — 「이게 전부」 로 읽지 않게.
    assert shallow["exhausted"] is False and shallow["truncated"] is False
    capped = _reach(client, admin, start, relations=chain["uses"], limit=2)
    assert len(capped["nodes"]) == 2
    assert capped["truncated"] is True and capped["exhausted"] is False


def test_기준일에_살아_있는_선만_따라간다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    uses = _make_relation(client, admin, "uses", label="씀", effectivity=True)
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    got = _link(client, admin, part, b["id"], uses, a["id"], valid_to=MAR)
    assert got.status_code == 201, got.text

    before = _reach(client, admin, a["id"], relations=uses, as_of="2026-02-15")
    assert _depths(before) == {"A": 0, "B": 1}
    assert before["as_of"] == "2026-02-15"
    assert _depths(_reach(client, admin, a["id"], relations=uses, as_of="2026-04-15")) == {
        "A": 0
    }


def test_없는_종류는_무엇이_없는지_말한다(client: TestClient, admin: Signed) -> None:
    chain = _chain(client, admin)
    start = chain["made"]["A"]["id"]
    missing = client.get(
        "/api/graph/reach",
        params={"start": start, "relations": f"{chain['uses']},nope,ref:nope.x"},
        headers=admin.headers,
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["details"]["unknown"] == ["nope", "ref:nope.x"]
    empty = client.get(
        "/api/graph/reach", params={"start": start, "relations": " , "}, headers=admin.headers
    )
    assert empty.status_code == 422
    gone = client.get(
        "/api/graph/reach",
        params={"start": str(uuid.uuid4()), "relations": chain["uses"]},
        headers=admin.headers,
    )
    assert gone.status_code == 404


@pytest.mark.usefixtures("department_scope")
def test_안_보이는_객체에서_멈춘다(client: TestClient, admin: Signed, member: Signed) -> None:
    """그 너머를 따라가면 안 보이는 객체가 길로 드러난다 — 이웃 그림과 같은 규칙."""
    part = _make_type(client, admin, label="부품")
    uses = _make_relation(client, admin, "uses", label="씀")
    other = _hidden_workspace(client, admin)
    mine = _make_object(client, admin, part, label="내 것", workspace_slug=member.workspace)
    theirs = _make_object(client, admin, part, label="남의 것", workspace_slug=other)
    beyond = _make_object(
        client, admin, part, label="그 너머", workspace_slug=member.workspace
    )
    assert _link(client, admin, part, theirs["id"], uses, mine["id"]).status_code == 201
    assert _link(client, admin, part, beyond["id"], uses, theirs["id"]).status_code == 201

    body = _reach(client, member, mine["id"], relations=uses)
    assert _depths(body) == {"내 것": 0}
    assert _depths(_reach(client, admin, mine["id"], relations=uses)) == {
        "내 것": 0,
        "남의 것": 1,
        "그 너머": 2,
    }
    hidden = client.get(
        "/api/graph/reach",
        params={"start": theirs["id"], "relations": uses},
        headers=member.headers,
    )
    assert hidden.status_code == 404


def test_이웃은_한쪽_선으로만_넓힐_수_있다(client: TestClient, admin: Signed) -> None:
    """`direction` — 관계 · 참조 칸 모두. 방향 없는 종류는 양쪽에서 다 보인다."""
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    _make_property(
        client,
        admin,
        part,
        key="maker",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    kind = _make_relation(client, admin, "part_of", label="속함")
    near = _make_relation(client, admin, "near", label="이웃함", directed=False)
    acme = _make_object(client, admin, vendor, label="ACME")
    hub = _make_object(client, admin, part, label="구동부", properties={"maker": acme["id"]})
    child = _make_object(client, admin, part, label="모터")
    parent = _make_object(client, admin, part, label="차체")
    twin = _make_object(client, admin, part, label="쌍둥이")
    assert _link(client, admin, part, child["id"], kind, hub["id"]).status_code == 201
    assert _link(client, admin, part, hub["id"], kind, parent["id"]).status_code == 201
    assert _link(client, admin, part, twin["id"], near, hub["id"]).status_code == 201

    def labels(**params: Any) -> set[str]:
        return {
            node["label"]
            for node in _neighborhood(client, admin, hub["id"], **params)["nodes"]
        }

    assert labels() == {"구동부", "모터", "차체", "쌍둥이", "ACME"}
    assert labels(direction="in") == {"구동부", "모터", "쌍둥이"}
    assert labels(direction="out") == {"구동부", "차체", "쌍둥이", "ACME"}
    # 참조 칸 — 공급사에서 들어오는 선은 그것을 가리키는 부품, 나가는 선은 없다.
    acme_in = _neighborhood(client, admin, acme["id"], direction="in")
    assert {node["label"] for node in acme_in["nodes"]} == {"ACME", "구동부"}
    acme_out = _neighborhood(client, admin, acme["id"], direction="out")
    assert {node["label"] for node in acme_out["nodes"]} == {"ACME"}
