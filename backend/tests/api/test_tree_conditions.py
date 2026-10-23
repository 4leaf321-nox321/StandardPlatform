"""조건의 「하위 전체」(`under`, ADR 0035 2단계) — 그 객체이거나 트리로 그 아래 전부.

트리는 타입마다 정한 관계(`list_view.tree`)다. 「하위 포함」 은 그 타입의 목록에서만
됐다 — 「이 어셈블리 아래 전부에 달린 서비스 기록」 처럼 **다른 타입이 트리를 가리키는**
물음은 못 했다.
조건 연산이면 목록 · 통계 · 지표 · 저장된 뷰 · MCP 가 같은 말로 묻는다.

    TOP ← MID ← LEAF      (부품, part_of — 자식이 부모를 가리킨다)
    OTHER                 (따로 선 부품)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
    _tree_type,
)


def _ids(client: TestClient, who: Signed, type_slug: str, **params: Any) -> set[str]:
    got = client.get(f"/api/objects/{type_slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return {one["label"] for one in got.json()["items"]}


@pytest.fixture
def world(client: TestClient, admin: Signed) -> dict[str, Any]:
    part, part_of = _tree_type(client, admin)
    made = {
        name: _make_object(client, admin, part, label=name)
        for name in ("TOP", "MID", "LEAF", "OTHER")
    }
    links: dict[str, str] = {}
    for child, parent in (("MID", "TOP"), ("LEAF", "MID")):
        linked = _link(client, admin, part, made[child]["id"], part_of, made[parent]["id"])
        assert linked.status_code == 201, linked.text
        links[child] = linked.json()["relation_id"]

    model = _make_type(client, admin, label="모델")
    _make_property(
        client,
        admin,
        model,
        key="part",
        label="주 부품",
        data_type="object_ref",
        ref_type_slug=part,
    )
    models = {
        name: _make_object(
            client, admin, model, label=name, properties={"part": made[target]["id"]}
        )
        for name, target in (("M-LEAF", "LEAF"), ("M-OTHER", "OTHER"))
    }

    svc = _make_type(client, admin, label="서비스 기록", usage="log")
    _make_property(
        client,
        admin,
        svc,
        key="part",
        label="교체 부품",
        data_type="object_ref",
        ref_type_slug=part,
    )
    _make_property(
        client,
        admin,
        svc,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
    )
    _make_property(client, admin, svc, key="kind", label="구분", data_type="text")
    records = {}
    for name, target, by_model in (
        ("R-LEAF", "LEAF", "M-LEAF"),
        ("R-MID", "MID", "M-OTHER"),
        ("R-OTHER", "OTHER", "M-OTHER"),
        ("R-TOP", "TOP", "M-LEAF"),
    ):
        records[name] = _make_object(
            client,
            admin,
            svc,
            label=name,
            properties={
                "part": made[target]["id"],
                "model": models[by_model]["id"],
                "kind": "교체",
            },
        )
    return {
        "part": part,
        "part_of": part_of,
        "model": model,
        "svc": svc,
        "parts": made,
        "links": links,
        "records": records,
    }


def test_참조_칸이_그_객체이거나_아래_전부를_가리키나(
    client: TestClient, member: Signed, world: dict[str, Any]
) -> None:
    p, svc = world["parts"], world["svc"]
    assert _ids(client, member, svc, **{"f.part.under": p["TOP"]["id"]}) == {
        "R-LEAF",
        "R-MID",
        "R-TOP",
    }
    assert _ids(client, member, svc, **{"f.part.under": p["MID"]["id"]}) == {"R-LEAF", "R-MID"}
    # 여럿이면 어느 하나의 아래.
    both = f"{p['MID']['id']}|{p['OTHER']['id']}"
    assert _ids(client, member, svc, **{"f.part.under": both}) == {
        "R-LEAF",
        "R-MID",
        "R-OTHER",
    }
    # 잎이면 자기 자신뿐.
    assert _ids(client, member, svc, **{"f.part.under": p["LEAF"]["id"]}) == {"R-LEAF"}


def test_목록의_객체_자신에도_건다(
    client: TestClient, member: Signed, world: dict[str, Any]
) -> None:
    p = world["parts"]
    assert _ids(client, member, world["part"], **{"f.id.under": p["MID"]["id"]}) == {
        "MID",
        "LEAF",
    }
    bad = client.get(
        f"/api/objects/{world['part']}",
        params={"f.id.eq": p["MID"]["id"]},
        headers=member.headers,
    )
    assert bad.status_code == 422 and "하위 전체" in bad.text


def test_걸음_끝의_참조_칸에도_건다(
    client: TestClient, member: Signed, world: dict[str, Any]
) -> None:
    """기록 → 모델 → 주 부품이 TOP 아래 — 모델을 거쳐 트리로."""
    p, svc = world["parts"], world["svc"]
    assert _ids(client, member, svc, **{"f.ref.model.part.under": p["TOP"]["id"]}) == {
        "R-LEAF",
        "R-TOP",
    }


def test_관계로_이어진_것에도_건다(
    client: TestClient, admin: Signed, member: Signed, world: dict[str, Any]
) -> None:
    p, svc = world["parts"], world["svc"]
    uses = _make_relation(
        client, admin, base="uses", src_type_slugs=[svc], dst_type_slugs=[world["part"]]
    )
    linked = _link(
        client, admin, svc, world["records"]["R-OTHER"]["id"], uses, p["LEAF"]["id"]
    )
    assert linked.status_code == 201, linked.text
    assert _ids(client, member, svc, **{f"f.out.{uses}.under": p["TOP"]["id"]}) == {"R-OTHER"}


def test_트리가_없는_타입을_가리키면_까닭과_함께_거절한다(
    client: TestClient, member: Signed, world: dict[str, Any]
) -> None:
    got = client.get(
        f"/api/objects/{world['svc']}",
        params={"f.model.under": world["records"]["R-TOP"]["id"]},
        headers=member.headers,
    )
    assert got.status_code == 422
    assert "트리가 정의된 타입" in got.json()["error"]["message"]


def test_통계에도_같은_조건이_걸린다(
    client: TestClient, member: Signed, world: dict[str, Any]
) -> None:
    got = client.get(
        f"/api/objects/{world['svc']}/summary",
        params={"group_by": "properties.kind", "f.part.under": world["parts"]["MID"]["id"]},
        headers=member.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["total"] == 2


def test_지표에도_같은_조건이_걸린다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """지표는 조건식을 `INSERT … SELECT` 안에 넣는다 — 재귀가 그 안에서도 돌아야 한다."""
    made = client.post(
        "/api/metrics",
        params={"recompute": True},
        json={
            "slug": f"under_{world['svc'][-6:]}",
            "label": "TOP 아래 교체",
            "source_type_slug": world["svc"],
            "spec": {
                "measure": "count",
                "dimensions": [{"name": "kind", "address": "properties.kind"}],
                "filters": [
                    {"field": "part", "op": "under", "value": world["parts"]["TOP"]["id"]}
                ],
            },
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    done = finish_job(client, admin, made.json()["job"])
    assert done["status"] == "done", done
    slug = made.json()["metric"]["slug"]
    got = client.get(
        f"/api/metrics/{slug}/values", params={"dims": "kind"}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["total_count"] == 3


def test_트리가_바뀌면_지표의_증분이_전량으로_센다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """「하위 전체」 거르기는 트리를 본다 — 부품이 다른 가지로 옮겨 가도 기록의 시각은 안
    오른다. 증분이 그것을 놓치면 셀이 낡은 채 남는다."""
    from tests.api.test_metric_incremental import _recount

    part, svc, p = world["part"], world["svc"], world["parts"]
    _make_property(client, admin, svc, key="received", label="접수일", data_type="date")
    for record in world["records"].values():
        patched = client.patch(
            f"/api/objects/{svc}/{record['id']}",
            json={"properties": {**record["properties"], "received": "2026-01-15"}},
            headers=admin.headers,
        )
        assert patched.status_code == 200, patched.text
    made = client.post(
        "/api/metrics",
        params={"recompute": True},
        json={
            "slug": f"tree_{svc[-6:]}",
            "label": "TOP 아래 교체",
            "source_type_slug": svc,
            "spec": {
                "measure": "count",
                "time": {"address": "properties.received", "grain": "month"},
                "filters": [{"field": "part", "op": "under", "value": p["TOP"]["id"]}],
            },
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    assert finish_job(client, admin, made.json()["job"])["status"] == "done"
    slug = made.json()["metric"]["slug"]

    def total() -> int:
        got = client.get(f"/api/metrics/{slug}/values", headers=admin.headers)
        assert got.status_code == 200, got.text
        return int(got.json()["total_count"])

    assert total() == 3
    assert _recount(client, admin, slug)["mode"] == "incremental"

    # LEAF 를 MID(→ TOP) 아래에서 OTHER 아래로 옮긴다 — 기록은 손대지 않는다.
    gone = client.delete(
        f"/api/objects/{part}/{p['LEAF']['id']}/relations/{world['links']['LEAF']}",
        headers=admin.headers,
    )
    assert gone.status_code == 204, gone.text
    moved = _link(client, admin, part, p["LEAF"]["id"], world["part_of"], p["OTHER"]["id"])
    assert moved.status_code == 201, moved.text

    redone = _recount(client, admin, slug)
    assert redone["mode"] == "full", redone
    assert "트리" in redone["note"]
    assert total() == 2  # R-MID · R-TOP — R-LEAF 는 이제 OTHER 아래다
