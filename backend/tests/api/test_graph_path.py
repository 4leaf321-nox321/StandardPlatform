"""두 객체 사이의 길(`GET /api/graph/path`) — 「A 와 B 는 어떻게 이어져 있나」.

이웃 그림은 노드마다 fanout 개까지만 데려오므로, 그것을 이어 붙여 길을 찾으면 잘린 곳의 길을
「없다」 로 답한다. 길 찾기는 자르지 않고 찾고, 상한에 닿으면 「찾다가 멈췄다」 고 말한다.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _path(client: TestClient, who: Signed, source: str, target: str, **params: Any) -> Any:
    got = client.get(
        "/api/graph/path",
        params={"source": source, "target": target, **params},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return got.json()


def _chain(client: TestClient, admin: Signed, n: int) -> tuple[str, str, list[dict[str, Any]]]:
    """부품 n 개를 한 줄로 — P0 -속함→ P1 -속함→ … ."""
    part = _make_type(client, admin, label="부품")
    rel = _make_relation(
        client, admin, base="part_of", src_type_slugs=[part], dst_type_slugs=[part]
    )
    made = [_make_object(client, admin, part, label=f"P{i}") for i in range(n)]
    for low, high in itertools.pairwise(made):
        linked = _link(client, admin, part, low["id"], rel, high["id"])
        assert linked.status_code == 201, linked.text
    return part, rel, made


def test_가장_짧은_길을_차례대로_준다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    _part, rel, made = _chain(client, admin, 5)
    got = _path(client, member, made[0]["id"], made[4]["id"])
    assert got["found"] is True and got["length"] == 4 and got["truncated"] is False
    (only,) = got["paths"]
    assert only["nodes"] == [one["id"] for one in made]
    by_id = {edge["id"]: edge for edge in got["edges"]}
    assert [by_id[e]["relation"] for e in only["edges"]] == [rel] * 4
    # 선의 방향은 그대로 실린다 — 길은 방향을 안 보지만 답은 방향을 말한다.
    first = by_id[only["edges"][0]]
    assert (first["src"], first["dst"]) == (made[0]["id"], made[1]["id"])
    assert {node["label"] for node in got["nodes"]} == {f"P{i}" for i in range(5)}


def test_거슬러_가는_길도_길이다(client: TestClient, admin: Signed, member: Signed) -> None:
    """「이 부품이 들어간 제품」 은 관계를 거슬러 간다 — 방향 때문에 못 찾으면 안 된다."""
    _part, _rel, made = _chain(client, admin, 3)
    got = _path(client, member, made[2]["id"], made[0]["id"])
    assert got["length"] == 2
    assert got["paths"][0]["nodes"] == [made[2]["id"], made[1]["id"], made[0]["id"]]


def test_같은_길이의_길이_여럿이면_여럿을_준다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    rel = _make_relation(
        client, admin, base="near", src_type_slugs=[part], dst_type_slugs=[part]
    )
    a, b, c, d = (_make_object(client, admin, part, label=name) for name in "ABCD")
    for src, dst in ((a, b), (a, c), (b, d), (c, d)):
        assert _link(client, admin, part, src["id"], rel, dst["id"]).status_code == 201
    got = _path(client, member, a["id"], d["id"])
    assert got["length"] == 2
    middles = {tuple(one["nodes"]) for one in got["paths"]}
    assert middles == {(a["id"], b["id"], d["id"]), (a["id"], c["id"], d["id"])}
    assert len(_path(client, member, a["id"], d["id"], limit=1)["paths"]) == 1


def test_참조_칸도_선이다(client: TestClient, admin: Signed, member: Signed) -> None:
    """모델 → 과제는 칸(참조)에 저장한 관계다 — 저장 자리가 달라도 길이다."""
    task = _make_type(client, admin, label="과제", key_policy="optional")
    model = _make_type(client, admin, label="모델")
    _make_property(
        client,
        admin,
        model,
        key="task",
        label="과제",
        data_type="object_ref",
        ref_type_slug=task,
    )
    t = _make_object(client, admin, task, label="과제1", key=f"T-{uuid.uuid4().hex[:6]}")
    m1 = _make_object(client, admin, model, label="모델1", properties={"task": t["id"]})
    m2 = _make_object(client, admin, model, label="모델2", properties={"task": t["id"]})
    got = _path(client, member, m1["id"], m2["id"])
    assert got["length"] == 2
    assert got["paths"][0]["nodes"] == [m1["id"], t["id"], m2["id"]]
    assert all(edge["relation"].startswith("ref:") for edge in got["edges"])


def test_길이_상한_너머는_못_찾았다고_하되_멈췄다고는_안_한다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    _part, _rel, made = _chain(client, admin, 5)
    got = _path(client, member, made[0]["id"], made[4]["id"], max_depth=3)
    assert got["found"] is False and got["length"] is None and got["paths"] == []
    assert got["truncated"] is False  # 상한은 사람이 고른 길이다 — 「찾다가 멈춤」 이 아니다
    # 못 찾아도 출발 · 도착은 그림에 선다.
    assert {node["id"] for node in got["nodes"]} == {made[0]["id"], made[4]["id"]}


def test_관계를_거르면_그_관계로만_간다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part, rel, made = _chain(client, admin, 3)
    other = _make_relation(
        client, admin, base="shortcut", src_type_slugs=[part], dst_type_slugs=[part]
    )
    assert _link(client, admin, part, made[0]["id"], other, made[2]["id"]).status_code == 201
    assert _path(client, member, made[0]["id"], made[2]["id"])["length"] == 1
    only_chain = _path(client, member, made[0]["id"], made[2]["id"], relations=rel)
    assert only_chain["length"] == 2


def test_지운_객체는_길이_되지_않는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part, _rel, made = _chain(client, admin, 3)
    gone = client.delete(
        f"/api/objects/{part}/{made[1]['id']}",
        params={"mode": "detach"},
        headers=admin.headers,
    )
    assert gone.status_code in (200, 204), gone.text
    got = _path(client, member, made[0]["id"], made[2]["id"])
    assert got["found"] is False
    # 지운 쪽 끝으로는 묻지 못한다 — 없는 것과 같은 말.
    missing = client.get(
        "/api/graph/path",
        params={"source": made[0]["id"], "target": made[1]["id"]},
        headers=member.headers,
    )
    assert missing.status_code == 404


def test_같은_객체면_길이_0(client: TestClient, admin: Signed, member: Signed) -> None:
    _part, _rel, made = _chain(client, admin, 1)
    got = _path(client, member, made[0]["id"], made[0]["id"])
    assert (
        got["found"] is True
        and got["length"] == 0
        and got["paths"][0]["nodes"] == [made[0]["id"]]
    )


def test_노드당_상한은_관계와_참조_칸이_따로_센다(
    client: TestClient, admin: Signed, member: Signed, monkeypatch: Any
) -> None:
    """X 에 관계 둘 · 참조 칸 둘 — 어느 쪽도 상한(셋)에 안 닿았다. 둘을 합쳐 넷으로 세던
    때는 잘리지 않았는데 「찾다가 멈춤」(`truncated`)이라 했다(2026-10-24 점검). 관계가
    셋이면 닿는다. 한 층에서 읽은 선이 `PATH_EDGE_BUDGET` 에 닿아도 멈춘다."""
    from app.modules.objects import graph

    part = _make_type(client, admin, label="부품")
    near = _make_relation(
        client, admin, base="near", src_type_slugs=[part], dst_type_slugs=[part]
    )
    model = _make_type(client, admin, label="모델")
    _make_property(
        client,
        admin,
        model,
        key="part",
        label="부품",
        data_type="object_ref",
        ref_type_slug=part,
    )
    x = _make_object(client, admin, part, label="X")
    for name in ("R1", "R2"):
        other = _make_object(client, admin, part, label=name)
        assert _link(client, admin, part, x["id"], near, other["id"]).status_code == 201
    for name in ("M1", "M2"):
        _make_object(client, admin, model, label=name, properties={"part": x["id"]})
    alone = _make_object(client, admin, part, label="외딴 것")

    monkeypatch.setattr(graph, "PATH_FANOUT", 3)
    got = _path(client, member, x["id"], alone["id"])
    assert got["found"] is False and got["truncated"] is False

    third = _make_object(client, admin, part, label="R3")
    assert _link(client, admin, part, x["id"], near, third["id"]).status_code == 201
    assert _path(client, member, x["id"], alone["id"])["truncated"] is True

    monkeypatch.setattr(graph, "PATH_FANOUT", 100)
    assert _path(client, member, x["id"], alone["id"])["truncated"] is False
    monkeypatch.setattr(graph, "PATH_EDGE_BUDGET", 2)
    assert _path(client, member, x["id"], alone["id"])["truncated"] is True
