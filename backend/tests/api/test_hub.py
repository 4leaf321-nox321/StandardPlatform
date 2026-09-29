"""허브 → 쌍둥이 — **내보낸 것을 그대로 받고, 받은 것은 받는 쪽에서 못 고친다.**

한 시험 DB 에 허브와 쌍둥이를 함께 둘 수 없어서, 허브가 내보낸 묶음의 slug 를 바꿔(`_hub_` →
`_twin_`) 쌍둥이 몫으로 받는다. 받는 길 · 막는 길은 slug 와 상관없다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, bundle_export, bundle_import, finish_job


def _export_many(client: TestClient, admin: Signed, groups: list[str]) -> dict[str, Any]:
    """묶음 여럿을 한 봉투로 — 작업이 되고, 결과 파일을 받는다."""
    started = client.post(
        "/api/bundles/export", json={"groups": groups}, headers=admin.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done", done
    got = client.get(f"/api/jobs/{done['id']}/download", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


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
                # 관계에 붙는 값 — 근거 건수 · 근거 종류. 이것이 내보내기에 실려야 한다.
                "properties": [
                    {"key": "n", "label": "근거 건수", "data_type": "number"},
                    {"key": "basis", "label": "근거 종류", "data_type": "text"},
                ],
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
                    {
                        "key": "M-1",
                        "label": "모델 1",
                        "task": "T-1",
                        "region": "KOR",
                        # **`;` 가 든 별칭** — 이어 보내면 받는 쪽에서 둘로 갈린다.
                        "aliases": ["갈라짐; 크랙", "크랙"],
                    },
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
                        "properties": {"n": 3, "basis": "시험"},
                    }
                ],
            }
        ],
        "apply": True,
    }
    got = bundle_import(client, admin, body)
    assert got["applied"] is True, got
    return n


def _as_twin(exported: dict[str, Any], tag: str) -> dict[str, Any]:
    """허브의 묶음을 쌍둥이 몫으로 — slug 만 바꾼다."""
    raw = json.dumps(exported, ensure_ascii=False).replace(f"hub{tag}", f"twin{tag}")
    body = json.loads(raw)
    return {
        "ontology": body["ontology"],
        "objects": body["objects"],
        "relations": body["relations"],
        "tombstones": body.get("tombstones") or {"objects": [], "relations": []},
    }


def _receive(
    client: TestClient, admin: Signed, bundle: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    return bundle_import(client, admin, {**bundle, "source": "hub", "apply": True, **extra})


def test_허브는_참조되는_것부터_식별자로_내보낸다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    n = _hub(client, admin, tag)
    body = bundle_export(client, admin, n["group"])
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
    # 관계에 붙은 값도 간다 — 예전에는 「속성 1줄은 가지 않는다」 경고만 남았다.
    assert body["relations"] == [
        {
            "type_slug": n["model"],
            "rows": [
                {
                    "src": "M-2",
                    "relation": n["derived"],
                    "dst": "M-1",
                    "evidence_note": "PLM 원 모델",
                    "properties": {"n": 3, "basis": "시험"},
                }
            ],
            # 허브가 정본이다 — 받는 쪽은 이 범위에서 안 온 선을 끊는다.
            "mode": "replace",
        }
    ]
    # 별칭은 **배열로** — `;` 가 든 이름이 하나로 산다.
    models = {row["key"]: row for row in body["objects"][2]["rows"]}
    assert models["M-1"]["aliases"] == ["갈라짐; 크랙", "크랙"]
    assert [one["slug"] for one in body["ontology"]["groups"]] == [n["group"]]
    assert {one["slug"] for one in body["ontology"]["types"]} == {
        n["project"],
        n["task"],
        n["model"],
    }
    assert body["counts"] == {
        "types": 3,
        "relation_types": 1,
        "objects": 5,
        "relations": 1,
        "tombstones": 0,
    }
    assert body["warnings"] == []

    missing = client.post(
        "/api/bundles/export", json={"group": "없는묶음"}, headers=admin.headers
    )
    assert missing.status_code == 404


def test_내보내기는_시스템_관리자만(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    n = _hub(client, admin, uuid.uuid4().hex[:6])
    got = client.post(
        "/api/bundles/export", json={"group": n["group"]}, headers=manager.headers
    )
    assert got.status_code == 403


def test_쌍둥이가_받으면_허브_관리가_되고_다시_받으면_그대로(
    client: TestClient, admin: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    exported = bundle_export(client, admin, hub["group"])
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
    twin_model = next(one for one in models if one["key"] == "M-1")
    profile = client.get(
        f"/api/objects/{twin['model']}/{twin_model['id']}", headers=admin.headers
    ).json()
    # `;` 가 든 별칭이 갈리지 않았나.
    assert profile["object"]["aliases"] == ["갈라짐; 크랙", "크랙"]
    # 관계에 붙은 근거 건수 · 근거 종류도 받는 쪽에 그대로 있나.
    incoming = next(one for one in profile["related"] if not one["outgoing"])
    assert incoming["properties"] == {"n": 3, "basis": "시험"}
    assert incoming["evidence_note"] == "PLM 원 모델"

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
    exported = bundle_export(client, admin, hub["group"])
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
    exported = bundle_export(client, admin, hub["group"])
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
    exported = bundle_export(client, admin, hub["group"])
    bundle = _as_twin(exported, tag)
    got = bundle_import(client, manager, {"objects": bundle["objects"], "source": "hub"})
    assert any("시스템 관리자" in one for one in got["errors"])


def test_허브에서_사라진_것이_쌍둥이에_전해진다(client: TestClient, admin: Signed) -> None:
    """**지운 것이 받는 쪽에 살아 남아 있었다.**

    내보내기는 살아 있는 것만 보냈다. 코어 API 는 같은 사실을 이미 `merged_into` 로 말하고
    있었으니, 두 길이 다르게 움직인 것이 더 나쁜 쪽이다. 받는 쪽은 **지우지 않는다** —
    사용 중지로 두고, 합쳐진 것은 이긴 쪽에 합치고, 선은 끊는다.
    """
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    twin = _names(tag, "twin")
    first = _receive(client, admin, _as_twin(bundle_export(client, admin, hub["group"]), tag))
    assert first["applied"] is True, first

    def hub_id(type_slug: str, key: str) -> str:
        rows = client.get(f"/api/objects/{type_slug}", headers=admin.headers).json()["items"]
        return str(next(one for one in rows if one["key"] == key)["id"])

    # 허브에서 ① 모델 하나를 다른 모델에 합치고 ② 그러고 나서 비게 된 과제를 지운다
    # (합치기가 먼저다 — 가리키는 것이 남아 있으면 지우기가 막힌다).
    merged = client.post(
        f"/api/objects/{hub['model']}/{hub_id(hub['model'], 'M-2')}/merge",
        json={"into": hub_id(hub["model"], "M-1")},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    dropped = client.delete(
        f"/api/objects/{hub['task']}/{hub_id(hub['task'], 'T-2')}", headers=admin.headers
    )
    assert dropped.status_code in (200, 204), dropped.text

    exported = bundle_export(client, admin, hub["group"])
    graves = exported["tombstones"]
    assert {one["key"] for one in graves["objects"]} == {"T-2", "M-2"}
    gone = next(one for one in graves["objects"] if one["key"] == "M-2")
    assert gone["merged_into"] == "M-1"
    # 합치면서 M-2 의 선도 사라졌다 — 그 무덤도 함께 간다.
    assert any(one["src"] == "M-2" for one in graves["relations"])

    got = _receive(client, admin, _as_twin(exported, tag))
    assert got["applied"] is True, got
    actions = {one["label"]: one["action"] for one in got["tombstones"]["rows"]}
    assert actions["T-2"] == "deprecate"
    assert actions["M-2"] == "merge"

    # 쌍둥이에서 ① 지운 과제는 **사용 중지**로 남고(지우지 않는다)
    tasks = client.get(f"/api/objects/{twin['task']}", headers=admin.headers).json()["items"]
    stopped = next(one for one in tasks if one["key"] == "T-2")
    assert stopped["status"] == "deprecated"
    # ② 합쳐진 모델은 이 설치에서도 합쳐졌다(목록에서 빠진다).
    models = client.get(f"/api/objects/{twin['model']}", headers=admin.headers).json()["items"]
    assert {one["key"] for one in models} == {"M-1"}

    # 다시 받아도 같은 말을 두 번 하지 않는다.
    again = _receive(client, admin, _as_twin(exported, tag))
    repeat = {one["label"]: one["action"] for one in again["tombstones"]["rows"]}
    assert repeat["T-2"] == "unchanged" and repeat["M-2"] == "unchanged"


def test_묶음_여럿을_한_봉투로_내보낸다(client: TestClient, admin: Signed) -> None:
    """**코어를 축별로 나눠 둔 설치**에서 하나씩 내보내면, 축끼리 가리키는 참조 때문에 어느
    쪽도 못 보낸다(참조가 묶음 밖을 가리켜 거절된다).

    여럿을 한 봉투로 내면 그 참조가 안에 든다. 그래도 밖을 가리키는 것이 남으면 막되,
    받는 쪽이 그 타입을 이미 가졌을 때는 켜서 보낼 수 있다.
    """
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    # 과제를 다른 사이드바 묶음으로 옮긴다 — 모델(그 묶음)이 과제(다른 묶음)를 가리킨다.
    other = f"g2hub{tag}"
    made = client.post(
        "/api/ontology/groups",
        json={"slug": other, "label": "다른 묶음"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    moved = client.patch(
        f"/api/ontology/types/{hub['task']}",
        json={"nav_group_slug": other},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text

    # 하나만 내보내면 막힌다 — 받는 쪽에서 참조가 안 풀린다.
    started = client.post(
        "/api/bundles/export", json={"group": hub["group"]}, headers=admin.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "failed", done
    assert "묶음 밖을 가리키는" in (done.get("error") or "")

    # 둘을 함께 고르면 간다.
    body = _export_many(client, admin, [hub["group"], other])
    assert {one["slug"] for one in body["ontology"]["groups"]} == {hub["group"], other}
    assert body["groups"] == [hub["group"], other]
    assert {one["type_slug"] for one in body["objects"]} == {
        hub["project"],
        hub["task"],
        hub["model"],
    }
