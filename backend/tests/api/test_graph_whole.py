"""정적 그리기(`/api/graph/whole`, ADR 0045) — **자르지 않나, 넘으면 거절하나, 탐색 그림과 같은
뜻인가.**

탐색 그림(neighborhood)은 노드마다 이웃 30개로 자르고 「+N」 을 붙인다. 정적 그리기는
자르지 않는 대신 상한을 넘으면 그림을 반쯤 주지 않고 수를 말한다 — 여기서 보는 것은 그 두
약속과, 보이는 범위 · 기준일 · 기록의 뜻이 탐색 그림과 같은가다. 상한은 시험에서 낮춘다
(`graphwhole.MAX_*`).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.objects import graphwhole
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed
from tests.api.test_graph import _hidden_workspace
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)
from tests.api.test_system_types import _world as _dept_world
from tests.api.test_type_usage import _world as _log_world


def _whole(client: TestClient, who: Signed, **params: Any) -> Any:
    response = client.get("/api/graph/whole", params=params, headers=who.headers)
    assert response.status_code == 200, response.text
    return response.json()


def _labels(body: Any) -> set[str]:
    return set(body["nodes"]["label"])


def _edges(body: Any) -> set[tuple[str, str, str]]:
    """(출발 이름, 도착 이름, 선의 종류 slug) — 번호를 이름으로 풀어 본다."""
    labels = body["nodes"]["label"]
    kinds = body["relations"]
    edges = body["edges"]
    return {
        (labels[s], labels[d], kinds[r]["slug"])
        for s, d, r in zip(edges["src"], edges["dst"], edges["rel"], strict=True)
    }


def _star(client: TestClient, admin: Signed, leaves: int) -> tuple[str, str, dict[str, Any]]:
    """허브 하나와 잎 `leaves` 개 — (타입, 관계, 허브)."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    hub = _make_object(client, admin, part, label="허브")
    for i in range(leaves):
        leaf = _make_object(client, admin, part, label=f"잎{i}")
        assert _link(client, admin, part, hub["id"], kind, leaf["id"]).status_code == 201
    return part, kind, hub


def test_노드는_열_배열이고_선은_번호로_잇는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    near = _make_relation(client, admin, "near", label="가까움")
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
    nut = _make_object(client, admin, part, label="너트")
    assert _link(client, admin, part, bolt["id"], near, nut["id"]).status_code == 201

    body = _whole(client, admin, focus=bolt["id"], depth=1)
    nodes, edges = body["nodes"], body["edges"]
    n = len(nodes["ids"])
    assert n == 3
    assert all(len(nodes[column]) == n for column in ("type", "label", "depth", "parent"))
    # 자리 열은 열어만 둔다 — 지금은 화면이 배치한다(ADR 0045 「다음」).
    assert nodes["x"] is None and nodes["y"] is None
    m = len(edges["src"])
    assert m == 2 and len(edges["dst"]) == m and len(edges["rel"]) == m
    assert all(0 <= one < n for one in edges["src"] + edges["dst"])
    assert all(0 <= one < len(body["types"]) for one in nodes["type"])
    assert all(0 <= one < len(body["relations"]) for one in edges["rel"])
    # 표의 수는 실린 것의 수다.
    assert sum(one["count"] for one in body["types"]) == n
    assert sum(one["count"] for one in body["relations"]) == m
    assert _edges(body) == {("볼트", "너트", near), ("볼트", "ACME", f"ref:{part}.maker")}
    assert next(one for one in body["relations"] if one["slug"] == near)["label"] == "가까움"
    assert body["over"] is None
    assert (body["seed"], body["focus"], body["depth"]) == ("focus", bolt["id"], 1)
    assert (body["max_nodes"], body["max_edges"]) == (
        graphwhole.MAX_NODES,
        graphwhole.MAX_EDGES,
    )


def test_노드마다_자르지_않는다(client: TestClient, admin: Signed) -> None:
    """**탐색 그림이 「+N」 을 붙이는 자료에서 정적 그리기는 전부를 준다** — 그것이 이 길의
    까닭이다(「일부만 보여 구조를 모르겠다」)."""
    _part, _kind, hub = _star(client, admin, 40)
    explore = client.get(
        "/api/graph/neighborhood",
        params={"focus": hub["id"], "fanout": 30},
        headers=admin.headers,
    ).json()
    assert explore["truncated"] is True and len(explore["nodes"]) == 31

    body = _whole(client, admin, focus=hub["id"], depth=1)
    assert len(body["nodes"]["ids"]) == 41 and len(body["edges"]["src"]) == 40
    assert body["over"] is None


def test_단계와_데려온_노드는_넓히기_차례와_같다(client: TestClient, admin: Signed) -> None:
    """`depth` 는 처음 닿은 단계, `parent` 는 그 노드를 데려온 바로 앞 단계의 노드다 — 화면의
    「단계」 배치가 이것으로 동심원을 그린다. 선의 방향과 상관없이 넓힌다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    made = {name: _make_object(client, admin, part, label=name) for name in "abcde"}
    for src, dst in (("a", "b"), ("c", "b"), ("c", "d"), ("a", "e"), ("e", "c")):
        assert (
            _link(client, admin, part, made[src]["id"], kind, made[dst]["id"]).status_code
            == 201
        )

    body = _whole(client, admin, focus=made["a"]["id"], depth=3)
    labels = body["nodes"]["label"]
    depth = dict(zip(labels, body["nodes"]["depth"], strict=True))
    parent = {
        label: (labels[at] if at >= 0 else None)
        for label, at in zip(labels, body["nodes"]["parent"], strict=True)
    }
    assert depth == {"a": 0, "b": 1, "e": 1, "c": 2, "d": 3}
    assert parent["a"] is None and parent["b"] == "a" and parent["e"] == "a"
    assert parent["c"] in {"b", "e"} and parent["d"] == "c"
    # 선은 넓히며 지나온 것과 그림 안끼리의 것 전부다.
    assert len(body["edges"]["src"]) == 5

    assert _labels(_whole(client, admin, focus=made["a"]["id"], depth=2)) == set("abce")
    assert _labels(_whole(client, admin, focus=made["a"]["id"], depth=0)) == {"a"}


def test_노드가_상한을_넘으면_자르지_않고_센_수와_함께_거절한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**반쯤 그린 그림은 「이게 전부」 로 읽힌다** — 넘으면 그림을 비우고 타입별 · 단계별 수를
    준다. 넘은 것을 끝까지 세지 않으므로 「이상」(`exact` 거짓)이다."""
    part, _kind, hub = _star(client, admin, 5)
    monkeypatch.setattr(graphwhole, "MAX_NODES", 3)
    body = _whole(client, admin, focus=hub["id"], depth=1)
    over = body["over"]
    assert over["what"] == "nodes" and over["limit"] == 3
    assert over["count"] > 3 and over["exact"] is False
    assert over["layers"][0] == 1 and sum(over["layers"]) == over["count"]
    assert {one["slug"]: one["count"] for one in over["types"]} == {part: over["count"]}
    assert body["nodes"]["ids"] == [] and body["edges"]["src"] == []
    assert body["types"] == [] and body["relations"] == []


def test_타입_씨앗은_먼저_세어_넓히기_전에_거절한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기록 2,000만 건 타입을 고르면 넓히기 전에 계수표(ADR 0038)로 거절한다 — 그때의 수는
    정확하다."""
    part = _make_type(client, admin, label="부품")
    for i in range(3):
        _make_object(client, admin, part, label=f"부품{i}")
    monkeypatch.setattr(graphwhole, "MAX_NODES", 2)
    over = _whole(client, admin, types=part, depth=1)["over"]
    assert over["what"] == "nodes" and over["exact"] is True
    assert over["count"] == 3 and over["layers"] == [3]
    assert [(one["slug"], one["count"]) for one in over["types"]] == [(part, 3)]


def test_선이_상한을_넘어도_거절한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _part, kind, hub = _star(client, admin, 3)
    monkeypatch.setattr(graphwhole, "MAX_EDGES", 2)
    body = _whole(client, admin, focus=hub["id"], depth=1)
    over = body["over"]
    assert over["what"] == "edges" and over["limit"] == 2 and over["count"] == 3
    assert [(one["slug"], one["count"]) for one in over["relations"]] == [(kind, 3)]
    assert body["nodes"]["ids"] == [] and body["edges"]["src"] == []


@pytest.mark.usefixtures("department_scope")
def test_남의_부서_객체는_싣지도_세지도_않는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    other = _hidden_workspace(client, admin)
    mine = _make_object(client, admin, part, label="내 것", workspace_slug=member.workspace)
    theirs = _make_object(client, admin, part, label="남의 것", workspace_slug=other)
    assert _link(client, admin, part, mine["id"], kind, theirs["id"]).status_code == 201

    body = _whole(client, member, focus=mine["id"], depth=2)
    assert body["nodes"]["label"] == ["내 것"] and body["edges"]["src"] == []
    assert _labels(_whole(client, member, types=part, depth=1)) == {"내 것"}
    # 없는 것과 안 보이는 것을 같은 말로 — 이웃 그림과 같다.
    hidden = client.get(
        "/api/graph/whole", params={"focus": theirs["id"]}, headers=member.headers
    )
    assert hidden.status_code == 404
    # 관리자에게는 둘 다 보인다.
    assert _labels(_whole(client, admin, focus=mine["id"], depth=1)) == {"내 것", "남의 것"}


def test_기준일에_살아_있는_선만_따라가고_그린다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client,
        admin,
        "bom_of",
        label="구성",
        effectivity=True,
        src_type_slugs=[part],
        dst_type_slugs=[part],
    )
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    linked = _link(
        client,
        admin,
        part,
        a["id"],
        kind,
        b["id"],
        valid_from="2026-01-01",
        valid_to="2026-03-01",
    )
    assert linked.status_code == 201, linked.text

    inside = _whole(client, admin, focus=a["id"], depth=1, as_of="2026-02-01")
    assert _labels(inside) == {"A", "B"} and len(inside["edges"]["src"]) == 1
    assert inside["as_of"] == "2026-02-01"
    after = _whole(client, admin, focus=a["id"], depth=1, as_of="2026-04-01")
    assert _labels(after) == {"A"} and after["edges"]["src"] == []


def test_나를_가리키는_기록은_고를_때만_따라간다(client: TestClient, admin: Signed) -> None:
    """ADR 0011 — 인기 모델은 기록 10만 건이 가리킨다. 이웃 그림처럼 `records` 거나 이웃
    타입으로 기록 타입을 고를 때만 싣는다. 기록에서 출발하면 그 기록이 가리키는 축은 늘
    온다."""
    w = _log_world(client, admin)
    assert len(_whole(client, admin, focus=w["hot"], depth=1)["nodes"]["ids"]) == 1
    with_records = _whole(client, admin, focus=w["hot"], depth=1, records="true")
    assert len(with_records["nodes"]["ids"]) == 4 and len(with_records["edges"]["src"]) == 3
    chosen = _whole(client, admin, focus=w["hot"], depth=1, neighbor_types=w["case"])
    assert len(chosen["nodes"]["ids"]) == 4

    from_cases = _whole(client, admin, types=w["case"], depth=1)
    assert w["hot"] in from_cases["nodes"]["ids"]
    assert len(from_cases["edges"]["src"]) == 3
    assert from_cases["seed"] == "types" and from_cases["seed_types"] == [w["case"]]


def test_원_표의_행도_노드다(client: TestClient, admin: Signed, workspace: Workspace) -> None:
    """「담당 부서」 로만 이어진 객체가 외톨이로 그려지면 「관계없음」 으로 읽힌다."""
    w = _dept_world(client, admin)
    made = _link(client, admin, w["part"], w["bolt"]["id"], w["kind"], str(workspace.id))
    assert made.status_code == 201, made.text

    body = _whole(client, admin, focus=w["bolt"]["id"], depth=1)
    assert _labels(body) == {"볼트", workspace.name}
    assert {one["slug"] for one in body["types"]} == {w["part"], w["dept"]}
    assert _edges(body) == {("볼트", workspace.name, w["kind"])}

    from_dept = _whole(client, admin, focus=str(workspace.id), depth=1)
    assert "볼트" in _labels(from_dept)


def test_씨앗은_하나다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    one = _make_object(client, admin, part, label="하나")
    neither = client.get("/api/graph/whole", headers=admin.headers)
    assert neither.status_code == 422
    both = client.get(
        "/api/graph/whole", params={"focus": one["id"], "types": part}, headers=admin.headers
    )
    assert both.status_code == 422
    unknown = client.get(
        "/api/graph/whole",
        params={"types": f"nope_{uuid.uuid4().hex[:6]}"},
        headers=admin.headers,
    )
    assert unknown.status_code == 404


def test_큰_응답은_gzip_으로_준다(client: TestClient, admin: Signed) -> None:
    """통째 그림은 수 MB 다 — 앞단이 JSON 을 줄인다는 보장이 없어 서버가 줄인다."""
    part = _make_type(client, admin, label="부품")
    for i in range(60):
        _make_object(client, admin, part, label=f"이름이 긴 부품 {i:03d} — {'가' * 30}")
    zipped = client.get(
        "/api/graph/whole",
        params={"types": part, "depth": 0},
        headers={**admin.headers, "Accept-Encoding": "gzip"},
    )
    assert zipped.status_code == 200
    assert zipped.headers["content-encoding"] == "gzip"
    assert len(zipped.json()["nodes"]["ids"]) == 60
    plain = client.get(
        "/api/graph/whole",
        params={"types": part, "depth": 0},
        headers={**admin.headers, "Accept-Encoding": "identity"},
    )
    assert "content-encoding" not in plain.headers
    assert plain.json() == zipped.json()
