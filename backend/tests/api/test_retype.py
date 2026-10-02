"""종류 변경 — 화면의 길: **계획을 먼저 보고, 변환할 수 없는 값은 값마다 정해야
적용된다**(ADR 0007).

`POST …/properties/{key}/retype` 은 계획(apply=false)과 적용(apply=true)이 같은 길이다.
계획은 타입의 건수 · 변환할 수 없는 값(값마다 건수 · 견본 · 까닭) · 경고를 내고, 적용은
스냅샷을 남기고 값 · 정의를 함께 바꾼다. 인터페이스의 공통 속성은 구현 타입 전부를 한 번에.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_interfaces import _add_common, _code, _make_interface, _props
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _retype(client: TestClient, who: Signed, slug: str, key: str, **body: Any) -> Any:
    return client.post(
        f"/api/ontology/types/{slug}/properties/{key}/retype",
        json={"apply": False, **body},
        headers=who.headers,
    )


def _value(client: TestClient, who: Signed, slug: str, object_id: str, key: str) -> Any:
    got = client.get(f"/api/objects/{slug}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return got.json()["object"]["properties"].get(key)


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="country", label="국가", data_type="text")
    rows = {
        label: _make_object(client, admin, part, label=label, properties={"country": value})
        for label, value in (("가", "한국"), ("나", "Korea"), ("다", "미국"), ("라", "??"))
    }
    return {"part": part, "rows": rows}


def test_계획은_값마다_건수와_견본을_내고_대체_값을_정해야_적용된다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    asked = {"data_type": "enum", "enum_options": ["한국", "미국"]}
    plan = _retype(client, admin, w["part"], "country", **asked).json()
    assert plan["applied"] is False and plan["errors"], plan
    failing = {one["value"]: one for one in plan["failures"]}
    assert set(failing) == {"Korea", "??"}
    assert failing["Korea"]["count"] == 1
    assert failing["Korea"]["samples"][0]["label"] == "나"
    assert plan["types"][0]["with_value"] == 4

    # 대체 값을 정하기 전에는 적용하라고 해도 적용하지 않는다.
    refused = _retype(client, admin, w["part"], "country", apply=True, **asked).json()
    assert refused["applied"] is False
    assert _value(client, admin, w["part"], w["rows"]["가"]["id"], "country") == "한국"

    mapping = {"Korea": "한국", "??": None}
    plan = _retype(client, admin, w["part"], "country", mapping=mapping, **asked).json()
    assert plan["errors"] == [], plan
    assert {one["value"]: one["to"] for one in plan["mapped"]} == mapping
    done = _retype(
        client, admin, w["part"], "country", mapping=mapping, apply=True, **asked
    ).json()
    assert done["applied"] is True and done["snapshot_id"], done
    values = {
        label: _value(client, admin, w["part"], row["id"], "country")
        for label, row in w["rows"].items()
    }
    assert values == {"가": "한국", "나": "한국", "다": "미국", "라": None}
    assert _props(client, admin, w["part"])["country"]["data_type"] == "enum"

    # 객체 이력에 남는다 — 안 바뀐 행(「가」)에는 안 남는다.
    def retyped(label: str) -> int:
        history = client.get(
            f"/api/objects/{w['part']}/{w['rows'][label]['id']}/history",
            headers=admin.headers,
        ).json()
        return len([one for one in history if "속성 종류 변경" in (one["reason"] or "")])

    assert retyped("나") == 1 and retyped("라") == 1 and retyped("가") == 0


def test_막는_자리마다_코드가_있다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin)
    _make_property(client, admin, part, key="ref", label="참조", data_type="object_ref")
    _make_property(client, admin, part, key="t", label="글", data_type="text")

    # 참조는 글 · 긴 글 · 선택과만 오간다(ADR 0009) — 숫자로는 안 된다.
    pair = _retype(client, admin, part, "ref", data_type="number")
    assert pair.status_code == 409 and _code(pair).endswith("ONTOLOGY-0064")
    aimless = _retype(client, admin, part, "t", data_type="object_ref")
    assert aimless.status_code == 422 and _code(aimless).endswith("ONTOLOGY-0066")
    same = _retype(client, admin, part, "t", data_type="text")
    assert same.status_code == 409 and _code(same).endswith("ONTOLOGY-0064")
    many = _retype(
        client,
        admin,
        part,
        "t",
        data_type="number",
        mapping={str(n): "1" for n in range(1001)},
    )
    assert many.status_code == 422 and _code(many).endswith("ONTOLOGY-0065")
    empty = _retype(client, admin, part, "t", data_type="enum", enum_options=[])
    assert empty.status_code == 409 and _code(empty).endswith("ONTOLOGY-0037")

    # 수정(PATCH)은 여전히 종류를 안 바꾸고 이 길을 가리킨다.
    patched = client.patch(
        f"/api/ontology/types/{part}/properties/t",
        json={"key": "t", "label": "글", "data_type": "number"},
        headers=admin.headers,
    )
    assert patched.status_code == 409 and "종류 변경" in patched.json()["error"]["message"]

    # 묶인 속성은 인터페이스에서.
    iface = _make_interface(client, admin)
    _add_common(
        client, admin, iface, key="maker", label="제조사", data_type="text"
    ).raise_for_status()
    bound = _make_type(client, admin, interface_slugs=[iface])
    refused = _retype(client, admin, bound, "maker", data_type="number")
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0008")


def test_공개_타입은_수신_시스템_확인을_받아야_적용한다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, key_policy="optional")
    _make_property(client, admin, part, key="qty", label="수량", data_type="text")
    _make_object(client, admin, part, label="하나", properties={"qty": "3"})
    client.patch(
        f"/api/ontology/types/{part}", json={"core": True}, headers=admin.headers
    ).raise_for_status()

    plan = _retype(client, admin, part, "qty", data_type="number").json()
    assert any("수신 시스템" in one for one in plan["warnings"]), plan
    blocked = _retype(client, admin, part, "qty", data_type="number", apply=True)
    assert blocked.status_code == 409 and _code(blocked).endswith("ONTOLOGY-0046")
    done = _retype(
        client, admin, part, "qty", data_type="number", apply=True, accept_core=True
    ).json()
    assert done["applied"] is True, done


def test_인터페이스의_공통_속성은_구현_타입_전부를_한_번에(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin)
    _add_common(
        client, admin, iface, key="power", label="출력", data_type="text"
    ).raise_for_status()
    first = _make_type(client, admin, key_policy="optional", interface_slugs=[iface])
    second = _make_type(client, admin, key_policy="optional", interface_slugs=[iface])
    ok = _make_object(client, admin, first, label="하나", properties={"power": "10"})
    bad = _make_object(client, admin, second, label="둘", properties={"power": "센 것"})

    url = f"/api/ontology/interfaces/{iface}/properties/power/retype"
    plan = client.post(url, json={"data_type": "number"}, headers=admin.headers).json()
    assert {one["type_slug"] for one in plan["types"]} == {first, second}
    assert [one["value"] for one in plan["failures"]] == ["센 것"]
    # 한 타입이라도 안 되면 아무것도 안 바뀐다.
    refused = client.post(
        url, json={"data_type": "number", "apply": True}, headers=admin.headers
    ).json()
    assert refused["applied"] is False
    assert _value(client, admin, first, ok["id"], "power") == "10"

    done = client.post(
        url,
        json={"data_type": "number", "apply": True, "mapping": {"센 것": "99"}},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    assert _value(client, admin, first, ok["id"], "power") == 10
    assert _value(client, admin, second, bad["id"], "power") == 99
    assert _props(client, admin, second)["power"]["data_type"] == "number"
    common = client.get(
        f"/api/ontology/interfaces/{iface}/properties", headers=admin.headers
    ).json()
    assert common[0]["data_type"] == "number"


def test_새로_깨지는_저장된_뷰와_데이터_소스를_경고한다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, key_policy="optional")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_object(client, admin, part, label="하나", properties={"weight": 3})
    made = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "무거운 것",
            "query": {
                "q": "",
                "conditions": [{"field": "weight", "op": "gte", "value": "10"}],
            },
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    source = client.post(
        "/api/datasources",
        json={
            "slug": f"plm_{uuid.uuid4().hex[:6]}",
            "name": "PLM 부품",
            "base_url": "http://plm.local/odata",
            "entity_set": "Parts",
            "type_slug": part,
            "workspace_slug": admin.workspace,
            "mapping": {
                "external_key": "No",
                "columns": [
                    {"source": "No", "target": "key"},
                    {"source": "Name", "target": "label"},
                    {"source": "Weight", "target": "properties.weight"},
                ],
            },
        },
        headers=admin.headers,
    )
    assert source.status_code == 201, source.text

    plan = _retype(client, admin, part, "weight", data_type="text").json()
    said = " ".join(plan["warnings"])
    assert "무거운 것" in said and "PLM 부품" in said, plan["warnings"]
    # 흉내만 냈다 — 정의는 그대로다.
    assert _props(client, admin, part)["weight"]["data_type"] == "number"


def test_종류가_바뀐_뒤에도_그_전_이력으로_되돌린다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, key_policy="optional")
    _make_property(client, admin, part, key="qty", label="수량", data_type="text")
    row = _make_object(client, admin, part, label="볼트", properties={"qty": "5"})
    client.patch(
        f"/api/objects/{part}/{row['id']}",
        json={"properties": {"qty": "7"}},
        headers=admin.headers,
    ).raise_for_status()
    done = _retype(client, admin, part, "qty", data_type="number", apply=True).json()
    assert done["applied"] is True, done

    history = client.get(
        f"/api/objects/{part}/{row['id']}/history", headers=admin.headers
    ).json()
    first = history[-1]
    assert first["snapshot"]["properties"] == {"qty": "5"}
    restored = client.post(
        f"/api/objects/{part}/{row['id']}/restore",
        json={"entry_id": first["id"]},
        headers=admin.headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["properties"] == {"qty": 5}

    # 변환할 수 없는 그때 값은 그렇다고 말한다 — 지금 값(1)은 예/아니오가 되지만 그때
    # 값(5)은 아니다.
    flags = _make_type(client, admin, key_policy="optional")
    _make_property(client, admin, flags, key="qty", label="수량", data_type="number")
    other = _make_object(client, admin, flags, label="너트", properties={"qty": 5})
    client.patch(
        f"/api/objects/{flags}/{other['id']}",
        json={"properties": {"qty": 1}},
        headers=admin.headers,
    ).raise_for_status()
    flag = _retype(client, admin, flags, "qty", data_type="bool", apply=True).json()
    assert flag["applied"] is True, flag
    earliest = client.get(
        f"/api/objects/{flags}/{other['id']}/history", headers=admin.headers
    ).json()[-1]
    refused = client.post(
        f"/api/objects/{flags}/{other['id']}/restore",
        json={"entry_id": earliest["id"]},
        headers=admin.headers,
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"].endswith("OBJECTS-0095")


def test_값이_없어도_RDF_가_새_종류를_본다(client: TestClient, admin: Signed) -> None:
    """RDF 는 판(정의 · 객체의 시각)이 바뀔 때만 다시 세운다 — 값이 하나도 없는 속성의 종류만
    바뀌면 객체 시각은 그대로라, 타입의 시각을 올리지 않으면 120초 동안 옛 종류가 나온다."""
    part = _make_type(client, admin)
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")

    def range_of() -> list[str]:
        asked = client.post(
            "/api/rdf/query",
            json={
                "query": (
                    "PREFIX sp: <http://testserver/ns#>\n"
                    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
                    f"SELECT ?r WHERE {{ <http://testserver/ns#{part}.qty> rdfs:range ?r }}"
                ),
                "types": [part],
            },
            headers=admin.headers,
        )
        assert asked.status_code == 200, asked.text
        return [str(one["r"]) for one in asked.json()["rows"]]

    assert any(one.endswith("decimal") for one in range_of())
    done = _retype(client, admin, part, "qty", data_type="text", apply=True).json()
    assert done["applied"] is True, done
    assert any(one.endswith("string") for one in range_of())
