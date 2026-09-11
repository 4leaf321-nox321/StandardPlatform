"""코드표 — **이름을 바꾸면 저장된 값도 따라오나, 승격하면 문자열이 참조가 되나.**"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _part(client: TestClient, admin: Signed) -> tuple[str, list[dict[str, Any]]]:
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="material",
        label="재질",
        data_type="enum",
        enum_options=["스틸", "알루미늄", "고무"],
        default_value="스틸",
    )
    _make_property(
        client,
        admin,
        part,
        key="materials",
        label="재질들",
        data_type="enum",
        enum_options=["스틸", "알루미늄", "고무"],
        multi=True,
    )
    rows = [
        _make_object(
            client,
            admin,
            part,
            key="P-1",
            label="볼트",
            properties={"material": "스틸", "materials": ["스틸", "고무"]},
        ),
        _make_object(
            client, admin, part, key="P-2", label="너트", properties={"material": "스틸"}
        ),
        _make_object(
            client, admin, part, key="P-3", label="링", properties={"material": "고무"}
        ),
    ]
    return part, rows


def _profile(client: TestClient, admin: Signed, part: str, object_id: str) -> dict[str, Any]:
    return dict(client.get(f"/api/objects/{part}/{object_id}", headers=admin.headers).json())


def test_이름을_바꾸면_저장된_값도_함께_바뀐다(client: TestClient, admin: Signed) -> None:
    part, rows = _part(client, admin)
    plan = client.post(
        f"/api/ontology/types/{part}/properties/material/rename-option",
        json={"from": "스틸", "to": "강", "apply": False},
        headers=admin.headers,
    ).json()
    assert (
        plan["applied"] is False and plan["objects_with_value"] == 2 and plan["errors"] == []
    )

    done = client.post(
        f"/api/ontology/types/{part}/properties/material/rename-option",
        json={"from": "스틸", "to": "강", "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True
    assert (
        _profile(client, admin, part, rows[0]["id"])["object"]["properties"]["material"]
        == "강"
    )
    assert (
        _profile(client, admin, part, rows[2]["id"])["object"]["properties"]["material"]
        == "고무"
    )
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    definition = next(
        d
        for t in schema["types"]
        if t["slug"] == part
        for d in t["properties"]
        if d["key"] == "material"
    )
    assert definition["enum_options"] == ["강", "알루미늄", "고무"]
    assert definition["default_value"] == "강"
    # 옛 이름으로는 못 거른다 — 새 이름으로 걸린다.
    assert (
        client.get(
            f"/api/objects/{part}", params={"f.material.eq": "강"}, headers=admin.headers
        ).json()["total"]
        == 2
    )


def test_다중값_칸에서도_그_값만_바뀐다(client: TestClient, admin: Signed) -> None:
    part, rows = _part(client, admin)
    client.post(
        f"/api/ontology/types/{part}/properties/materials/rename-option",
        json={"from": "고무", "to": "러버", "apply": True},
        headers=admin.headers,
    )
    assert _profile(client, admin, part, rows[0]["id"])["object"]["properties"][
        "materials"
    ] == ["스틸", "러버"]


def test_이미_있는_이름으로는_못_바꾼다(client: TestClient, admin: Signed) -> None:
    part, _ = _part(client, admin)
    plan = client.post(
        f"/api/ontology/types/{part}/properties/material/rename-option",
        json={"from": "스틸", "to": "고무", "apply": True},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is False and any("이미 있는" in one for one in plan["errors"])


def test_승격하면_코드표가_생기고_문자열이_참조가_된다(
    client: TestClient, admin: Signed
) -> None:
    part, rows = _part(client, admin)
    slug = f"material_{part[-6:]}"
    plan = client.post(
        f"/api/ontology/types/{part}/properties/material/promote",
        json={"new_slug": slug, "new_label": "재질", "apply": False},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is False and plan["target_new"] is True
    assert [(o["value"], o["action"], o["objects_with_value"]) for o in plan["options"]] == [
        ("스틸", "create", 2),
        ("알루미늄", "create", 0),
        ("고무", "create", 1),
    ]

    done = client.post(
        f"/api/ontology/types/{part}/properties/material/promote",
        json={"new_slug": slug, "new_label": "재질", "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True and done["snapshot_id"]
    ids = {o["value"]: o["object_id"] for o in done["options"]}
    assert all(ids.values())

    # 정의가 참조로 바뀌었다.
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    definition = next(
        d
        for t in schema["types"]
        if t["slug"] == part
        for d in t["properties"]
        if d["key"] == "material"
    )
    assert definition["data_type"] == "object_ref" and definition["ref_type_slug"] == slug
    assert definition["enum_options"] is None
    assert definition["default_value"] == ids["스틸"]
    target = next(t for t in schema["types"] if t["slug"] == slug)
    assert target["kind_class"] == "reference"

    # 저장된 값이 객체를 가리키고, 이름으로 읽힌다.
    bolt = _profile(client, admin, part, rows[0]["id"])["object"]
    assert bolt["properties"]["material"] == ids["스틸"]
    assert bolt["ref_labels"][ids["스틸"]] == "스틸"
    # 승격한 뒤에도 참조 조건으로 걸린다.
    assert (
        client.get(
            f"/api/objects/{part}",
            params={"f.material.eq": ids["고무"]},
            headers=admin.headers,
        ).json()["total"]
        == 1
    )
    # 코드표 객체 셋.
    assert client.get(f"/api/objects/{slug}", headers=admin.headers).json()["total"] == 3


def test_있는_코드표에_붙이면_같은_이름은_재사용한다(
    client: TestClient, admin: Signed
) -> None:
    part, _ = _part(client, admin)
    book = _make_type(
        client, admin, label="재질", kind_class="reference", key_policy="optional"
    )
    steel = _make_object(client, admin, book, label="스틸")
    plan = client.post(
        f"/api/ontology/types/{part}/properties/material/promote",
        json={"target_type_slug": book, "apply": True},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is True and plan["target_new"] is False
    by_value = {o["value"]: o for o in plan["options"]}
    assert (
        by_value["스틸"]["action"] == "reuse" and by_value["스틸"]["object_id"] == steel["id"]
    )
    assert by_value["알루미늄"]["action"] == "create"
    assert client.get(f"/api/objects/{book}", headers=admin.headers).json()["total"] == 3


def test_코드표가_아닌_타입에는_못_붙인다(client: TestClient, admin: Signed) -> None:
    part, _ = _part(client, admin)
    other = _make_type(client, admin, label="기록", kind_class="record")
    response = client.post(
        f"/api/ontology/types/{part}/properties/material/promote",
        json={"target_type_slug": other, "apply": False},
        headers=admin.headers,
    )
    assert response.status_code == 409


def test_옵션에_없는_저장값이_있으면_막는다(client: TestClient, admin: Signed) -> None:
    """조용히 두면 uuid 도 문자열도 아닌 값이 참조 칸에 남는다."""
    part, rows = _part(client, admin)
    # 옵션에서 「고무」 를 지워 저장값을 고아로 만든다.
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    definition = next(
        d
        for t in schema["types"]
        if t["slug"] == part
        for d in t["properties"]
        if d["key"] == "material"
    )
    body = {
        k: definition[k]
        for k in (
            "key",
            "label",
            "data_type",
            "unit",
            "help",
            "required",
            "multi",
            "ref_type_slug",
            "min_value",
            "max_value",
            "decimals",
            "pattern",
            "default_value",
            "unique",
            "section",
            "sort_order",
        )
    }
    body["enum_options"] = ["스틸", "알루미늄"]
    patched = client.patch(
        f"/api/ontology/types/{part}/properties/material", json=body, headers=admin.headers
    )
    assert patched.status_code == 200, patched.text
    plan = client.post(
        f"/api/ontology/types/{part}/properties/material/promote",
        json={"new_slug": f"m_{part[-6:]}", "new_label": "재질", "apply": True},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is False and any("고무" in one for one in plan["errors"])
    # 아무것도 안 바뀌었다.
    assert (
        _profile(client, admin, part, rows[2]["id"])["object"]["properties"]["material"]
        == "고무"
    )


def test_승격은_시스템_관리자만(client: TestClient, admin: Signed, member: Signed) -> None:
    part, _ = _part(client, admin)
    response = client.post(
        f"/api/ontology/types/{part}/properties/material/promote",
        json={"new_slug": "x", "new_label": "x"},
        headers=member.headers,
    )
    assert response.status_code == 403
