"""허브 → 쌍둥이 — **내보낸 것을 그대로 받고, 받은 것은 받는 쪽에서 못 고친다.**

한 시험 DB 에 허브와 쌍둥이를 함께 둘 수 없어서, 허브가 내보낸 묶음의 slug 를 바꿔(`_hub_` →
`_twin_`) 쌍둥이 몫으로 받는다. 받는 길 · 막는 길은 slug 와 상관없다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed


def _names(tag: str, side: str) -> dict[str, str]:
    return {
        "group": f"g{side}{tag}",
        "project": f"p{side}{tag}",
        "task": f"t{side}{tag}",
        "model": f"m{side}{tag}",
        "derived": f"d{side}{tag}",
    }


def _ontology(n: dict[str, str]) -> dict[str, Any]:
    return {
        "groups": [{"slug": n["group"], "label": "PLM 시험"}],
        "types": [
            {
                "slug": n["model"],
                "label": "모델",
                "nav_group_slug": n["group"],
                "key_policy": "required",
                "properties": [
                    {
                        "key": "task",
                        "label": "과제",
                        "data_type": "object_ref",
                        "ref_type_slug": n["task"],
                    },
                    {"key": "region", "label": "지역", "data_type": "text"},
                ],
            },
            {
                "slug": n["task"],
                "label": "과제",
                "nav_group_slug": n["group"],
                "key_policy": "required",
                "properties": [
                    {
                        "key": "project",
                        "label": "프로젝트",
                        "data_type": "object_ref",
                        "ref_type_slug": n["project"],
                    },
                    {
                        "key": "status_text",
                        "label": "상태",
                        "data_type": "enum",
                        "enum_options": ["진행", "완료"],
                    },
                ],
            },
            {
                "slug": n["project"],
                "label": "프로젝트",
                "nav_group_slug": n["group"],
                "key_policy": "required",
            },
        ],
        "relation_types": [
            {
                "slug": n["derived"],
                "label": "원 모델",
                "src_type_slugs": [n["model"]],
                "dst_type_slugs": [n["model"]],
                "cardinality": "many_to_one",
            },
        ],
    }


def _hub(client: TestClient, admin: Signed, tag: str) -> dict[str, str]:
    """허브 — PLM 기준정보를 넣는다(허브에게는 제 것이라 source 가 없다)."""
    n = _names(tag, "hub")
    body = {
        "ontology": _ontology(n),
        "objects": [
            {"type_slug": n["project"], "rows": [{"key": "P-1", "label": "프로젝트 1"}]},
            {
                "type_slug": n["task"],
                "rows": [
                    {"key": "T-1", "label": "과제 1", "project": "P-1", "status_text": "진행"},
                    {"key": "T-2", "label": "과제 2", "project": "P-1"},
                ],
            },
            {
                "type_slug": n["model"],
                "rows": [
                    {"key": "M-1", "label": "모델 1", "task": "T-1", "region": "KOR"},
                    {"key": "M-2", "label": "모델 2", "task": "T-2", "region": "EUR"},
                ],
            },
        ],
        "relations": [
            {
                "type_slug": n["model"],
                "rows": [
                    {
                        "src": "M-2",
                        "relation": n["derived"],
                        "dst": "M-1",
                        "evidence_note": "PLM 원 모델",
                    }
                ],
            }
        ],
        "apply": True,
    }
    got = client.post("/api/bundles/import", json=body, headers=admin.headers)
    assert got.status_code == 200 and got.json()["applied"] is True, got.text
    return n


def _as_twin(exported: dict[str, Any], tag: str) -> dict[str, Any]:
    """허브의 묶음을 쌍둥이 몫으로 — slug 만 바꾼다."""
    raw = json.dumps(exported, ensure_ascii=False).replace(f"hub{tag}", f"twin{tag}")
    body = json.loads(raw)
    return {
        "ontology": body["ontology"],
        "objects": body["objects"],
        "relations": body["relations"],
    }


def _receive(
    client: TestClient, admin: Signed, bundle: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    got = client.post(
        "/api/bundles/import",
        json={**bundle, "source": "hub", "apply": True, **extra},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_허브는_참조되는_것부터_식별자로_내보낸다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    n = _hub(client, admin, tag)
    got = client.get(
        "/api/bundles/export", params={"group": n["group"]}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    body = got.json()
    # 적힌 차례는 모델 · 과제 · 프로젝트였지만, 참조되는 것이 먼저 간다.
    assert [one["type_slug"] for one in body["objects"]] == [
        n["project"],
        n["task"],
        n["model"],
    ]
    tasks = {row["key"]: row for row in body["objects"][1]["rows"]}
    assert tasks["T-1"]["project"] == "P-1" and tasks["T-1"]["status_text"] == "진행"
    # 비어 있는 칸도 간다 — 허브에서 지운 값이 받는 쪽에 남지 않게.
    assert "status_text" in tasks["T-2"] and tasks["T-2"]["status_text"] is None
    assert body["relations"] == [
        {
            "type_slug": n["model"],
            "rows": [
                {
                    "src": "M-2",
                    "relation": n["derived"],
                    "dst": "M-1",
                    "evidence_note": "PLM 원 모델",
                }
            ],
        }
    ]
    assert [one["slug"] for one in body["ontology"]["groups"]] == [n["group"]]
    assert {one["slug"] for one in body["ontology"]["types"]} == {
        n["project"],
        n["task"],
        n["model"],
    }
    assert body["counts"] == {"types": 3, "relation_types": 1, "objects": 5, "relations": 1}
    assert body["warnings"] == []

    missing = client.get(
        "/api/bundles/export", params={"group": "없는묶음"}, headers=admin.headers
    )
    assert missing.status_code == 404


def test_내보내기는_시스템_관리자만(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    n = _hub(client, admin, uuid.uuid4().hex[:6])
    got = client.get(
        "/api/bundles/export", params={"group": n["group"]}, headers=manager.headers
    )
    assert got.status_code == 403


def test_쌍둥이가_받으면_허브_관리가_되고_다시_받으면_그대로(
    client: TestClient, admin: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    exported = client.get(
        "/api/bundles/export", params={"group": hub["group"]}, headers=admin.headers
    ).json()
    twin = _names(tag, "twin")

    first = _receive(client, admin, _as_twin(exported, tag))
    assert first["applied"] is True, first
    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert types[twin["model"]]["managed_by"] == "hub"
    assert types[hub["model"]]["managed_by"] == ""
    relation_types = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    assert (
        next(one for one in relation_types if one["slug"] == twin["derived"])["managed_by"]
        == "hub"
    )

    models = client.get(f"/api/objects/{twin['model']}", headers=admin.headers).json()["items"]
    assert {one["key"] for one in models} == {"M-1", "M-2"}

    again = _receive(client, admin, _as_twin(exported, tag))
    assert again["counts"]["objects_create"] == 0 and again["counts"]["objects_update"] == 0, (
        again
    )
    assert again["counts"]["ontology_changes"] == 0

    # 허브에서 바꾸면 — 이름을 고치고 값을 비우면 — 받는 쪽도 그렇게 된다.
    hub_task = next(
        one
        for one in client.get(f"/api/objects/{hub['task']}", headers=admin.headers).json()[
            "items"
        ]
        if one["key"] == "T-1"
    )
    patched = client.patch(
        f"/api/objects/{hub['task']}/{hub_task['id']}",
        json={"label": "과제 1 (고침)", "properties": {"status_text": None}},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    exported = client.get(
        "/api/bundles/export", params={"group": hub["group"]}, headers=admin.headers
    ).json()
    third = _receive(client, admin, _as_twin(exported, tag))
    assert third["counts"]["objects_update"] == 1, third
    twin_task = next(
        one
        for one in client.get(f"/api/objects/{twin['task']}", headers=admin.headers).json()[
            "items"
        ]
        if one["key"] == "T-1"
    )
    assert twin_task["label"] == "과제 1 (고침)"
    assert twin_task["properties"].get("status_text") is None


def test_받은_것은_받는_쪽에서_못_고친다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    exported = client.get(
        "/api/bundles/export", params={"group": hub["group"]}, headers=admin.headers
    ).json()
    _receive(client, admin, _as_twin(exported, tag))
    twin = _names(tag, "twin")
    h = admin.headers
    models = {
        one["key"]: one
        for one in client.get(f"/api/objects/{twin['model']}", headers=h).json()["items"]
    }
    m1 = models["M-1"]

    def refused(response: Any) -> None:
        assert response.status_code == 409, response.text
        assert "관리" in response.json()["error"]["message"]

    refused(
        client.post(
            f"/api/objects/{twin['model']}", json={"key": "M-9", "label": "몰래"}, headers=h
        )
    )
    refused(
        client.patch(
            f"/api/objects/{twin['model']}/{m1['id']}", json={"label": "몰래"}, headers=h
        )
    )
    refused(client.delete(f"/api/objects/{twin['model']}/{m1['id']}", headers=h))
    refused(
        client.post(
            f"/api/objects/{twin['model']}/bulk-edit",
            json={"ids": [m1["id"]], "field": "label", "value": "몰래"},
            headers=h,
        )
    )
    refused(
        client.post(
            f"/api/objects/{twin['model']}/{models['M-2']['id']}/relations",
            json={"relation": twin["derived"], "dst_object_id": m1["id"]},
            headers=h,
        )
    )
    planned = client.post(
        f"/api/objects/{twin['model']}/import-rows",
        json={"rows": [{"key": "M-1", "label": "몰래"}], "apply": True},
        headers=h,
    )
    assert planned.status_code == 200 and planned.json()["applied"] is False
    assert any("관리" in one for one in planned.json()["errors"])

    # 정의도 — 화면 · 정의 가져오기 모두.
    refused(
        client.patch(f"/api/ontology/types/{twin['model']}", json={"label": "몰래"}, headers=h)
    )
    refused(
        client.post(
            f"/api/ontology/types/{twin['model']}/properties",
            json={"key": "sneaky", "label": "몰래", "data_type": "text"},
            headers=h,
        )
    )
    changed = _ontology(twin)
    changed["types"][0]["label"] = "몰래 바꾼 모델"
    plan = client.post("/api/ontology/import", json=changed, headers=h).json()
    assert any("관리하는 정의" in one for one in plan["errors"]), plan
    # 아무것도 안 바꾸면 막지 않는다(되돌리기 스냅샷이 이 길로 온다).
    same = client.post(
        "/api/ontology/import", json=exported_ontology(exported, tag), headers=h
    ).json()
    assert same["errors"] == [], same

    # 이 설치의 관계가 받은 객체를 **가리키는 것**은 된다.
    local = f"l{tag}"
    made = client.post(
        "/api/ontology/import",
        params={"dry_run": False},
        json={
            "relation_types": [
                {
                    "slug": local,
                    "label": "비교",
                    "src_type_slugs": [twin["model"]],
                    "dst_type_slugs": [twin["model"]],
                }
            ]
        },
        headers=h,
    )
    assert made.status_code == 200 and made.json()["errors"] == [], made.text
    linked = client.post(
        f"/api/objects/{twin['model']}/{models['M-2']['id']}/relations",
        json={"relation": local, "dst_object_id": m1["id"], "evidence_note": "그룹이 비교"},
        headers=h,
    )
    assert linked.status_code == 201, linked.text


def exported_ontology(exported: dict[str, Any], tag: str) -> dict[str, Any]:
    return dict(_as_twin(exported, tag)["ontology"])


def test_받은_묶음은_시스템_관리자만_넣는다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    exported = client.get(
        "/api/bundles/export", params={"group": hub["group"]}, headers=admin.headers
    ).json()
    bundle = _as_twin(exported, tag)
    got = client.post(
        "/api/bundles/import",
        json={"objects": bundle["objects"], "source": "hub"},
        headers=manager.headers,
    )
    assert got.status_code == 200
    assert any("시스템 관리자" in one for one in got.json()["errors"])
