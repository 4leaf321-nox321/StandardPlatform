"""계층 축(`…@<단계>`, ADR 0035 3단계) — 참조 칸의 값을 그 타입 트리의 한 단계로 접어 센다.

    TOP ← MID ← LEAF      (부품, part_of)
    OTHER
    기록: R-LEAF→LEAF · R-MID→MID · R-OTHER→OTHER · R-TOP→TOP

뿌리(@0)로 접으면 TOP 3 · OTHER 1. 1단 상위(@1)로 접으면 MID 2(MID · LEAF) · TOP 1 ·
OTHER 1 — TOP 과 OTHER 는 1단보다 얕아 **자기 자신**이 값이고, 누르면 「하위 전체」 가 아니라
「같음」 으로 돌아간다(안 그러면 TOP 칸을 누른 목록에 MID 칸의 기록까지 섞인다).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, finish_job
from tests.api.test_metric_incremental import _recount
from tests.api.test_ontology import _link, _make_property
from tests.api.test_tree_conditions import world  # noqa: F401 — 같은 세상을 쓴다


def _summary(client: TestClient, who: Signed, type_slug: str, group_by: str) -> dict[str, Any]:
    got = client.get(
        f"/api/objects/{type_slug}/summary", params={"group_by": group_by}, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _bars(body: dict[str, Any]) -> dict[str, tuple[int, str | None]]:
    return {
        one["label"]: (one["count"], one["drill"]["op"] if one["drill"] else None)
        for one in body["buckets"]
    }


def _listed(
    client: TestClient, who: Signed, type_slug: str, field: str, op: str, key: str
) -> int:
    got = client.get(
        f"/api/objects/{type_slug}", params={f"f.{field}.{op}": key}, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return int(got.json()["total"])


def test_뿌리로_접어_센다(client: TestClient, member: Signed, world: dict[str, Any]) -> None:  # noqa: F811
    body = _summary(client, member, world["svc"], "properties.part@0")
    assert _bars(body) == {"TOP": (3, "under"), "OTHER": (1, "under")}
    assert body["group_label"] == "교체 부품 (뿌리)"


def test_한_단계_아래로_접으면_얕은_것은_자기_자신이고_누르면_같음이다(
    client: TestClient,
    member: Signed,
    world: dict[str, Any],  # noqa: F811
) -> None:
    body = _summary(client, member, world["svc"], "properties.part@1")
    assert _bars(body) == {"MID": (2, "under"), "TOP": (1, "eq"), "OTHER": (1, "eq")}
    # 막대를 누른 조건으로 목록을 거르면 막대의 수와 같다.
    for one in body["buckets"]:
        drill = one["drill"]
        assert drill["field"] == "part"
        assert (
            _listed(client, member, world["svc"], drill["field"], drill["op"], one["key"])
            == one["count"]
        )


def test_걸음_끝의_참조_칸도_접는다(
    client: TestClient,
    member: Signed,
    world: dict[str, Any],  # noqa: F811
) -> None:
    """기록 → 모델 → 주 부품의 뿌리. M-LEAF 는 LEAF(→ TOP), M-OTHER 는 OTHER 를 가리킨다."""
    body = _summary(client, member, world["svc"], "ref.model.part@0")
    assert _bars(body) == {"TOP": (2, "under"), "OTHER": (2, "under")}
    assert {one["drill"]["field"] for one in body["buckets"]} == {"ref.model.part"}


def test_트리가_없는_타입을_가리키면_까닭과_함께_거절한다(
    client: TestClient,
    member: Signed,
    world: dict[str, Any],  # noqa: F811
) -> None:
    got = client.get(
        f"/api/objects/{world['svc']}/summary",
        params={"group_by": "properties.model@0"},
        headers=member.headers,
    )
    assert got.status_code == 422
    assert "트리가 정의된 타입" in got.json()["error"]["message"]


def test_고르개에_트리가_있는_참조_칸만_계층_축이_선다(
    client: TestClient,
    member: Signed,
    world: dict[str, Any],  # noqa: F811
) -> None:
    fields = {
        one["field"]
        for one in _summary(client, member, world["svc"], "label")["group_options"]
    }
    assert {"properties.part@0", "properties.part@1"} <= fields
    assert not any(one.startswith("properties.model@") for one in fields)


def _metric(
    client: TestClient, admin: Signed, svc: str, spec: dict[str, Any], suffix: str
) -> str:
    made = client.post(
        "/api/metrics",
        params={"recompute": True},
        json={
            "slug": f"{suffix}_{svc[-6:]}",
            "label": "계층",
            "source_type_slug": svc,
            "spec": spec,
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    assert finish_job(client, admin, made.json()["job"])["status"] == "done"
    return str(made.json()["metric"]["slug"])


def _cells(
    client: TestClient, admin: Signed, slug: str, dim: str
) -> dict[str, dict[str, Any]]:
    got = client.get(
        f"/api/metrics/{slug}/values", params={"dims": dim}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    return {one["labels"][dim]: one for one in got.json()["cells"]}


def test_지표의_기준으로_접고_건_보기는_칸마다_맞는_조건으로(
    client: TestClient,
    admin: Signed,
    world: dict[str, Any],  # noqa: F811
) -> None:
    p = world["parts"]
    slug = _metric(
        client,
        admin,
        world["svc"],
        {"measure": "count", "dimensions": [{"name": "line", "address": "properties.part@1"}]},
        "lv",
    )
    cells = _cells(client, admin, slug, "line")
    assert {label: one["count"] for label, one in cells.items()} == {
        "MID": 2,
        "TOP": 1,
        "OTHER": 1,
    }
    assert cells["MID"]["drill"]["params"] == {"f.part.under": p["MID"]["id"]}
    assert cells["TOP"]["drill"]["params"] == {"f.part.eq": p["TOP"]["id"]}
    assert cells["MID"]["drill"]["partial"] == []


def test_트리가_바뀌면_계층_축_지표도_전량으로_센다(
    client: TestClient,
    admin: Signed,
    world: dict[str, Any],  # noqa: F811
) -> None:
    part, svc, p = world["part"], world["svc"], world["parts"]
    _make_property(client, admin, svc, key="received", label="접수일", data_type="date")
    for record in world["records"].values():
        patched = client.patch(
            f"/api/objects/{svc}/{record['id']}",
            json={"properties": {**record["properties"], "received": "2026-01-15"}},
            headers=admin.headers,
        )
        assert patched.status_code == 200, patched.text
    slug = _metric(
        client,
        admin,
        svc,
        {
            "measure": "count",
            "time": {"address": "properties.received", "grain": "month"},
            "dimensions": [{"name": "root", "address": "properties.part@0"}],
        },
        "lvi",
    )
    assert {k: v["count"] for k, v in _cells(client, admin, slug, "root").items()} == {
        "TOP": 3,
        "OTHER": 1,
    }
    assert _recount(client, admin, slug)["mode"] == "incremental"
    gone = client.delete(
        f"/api/objects/{part}/{p['LEAF']['id']}/relations/{world['links']['LEAF']}",
        headers=admin.headers,
    )
    assert gone.status_code == 204, gone.text
    assert (
        _link(
            client, admin, part, p["LEAF"]["id"], world["part_of"], p["OTHER"]["id"]
        ).status_code
        == 201
    )
    redone = _recount(client, admin, slug)
    assert redone["mode"] == "full" and "트리" in redone["note"], redone
    assert {k: v["count"] for k, v in _cells(client, admin, slug, "root").items()} == {
        "TOP": 2,
        "OTHER": 2,
    }
