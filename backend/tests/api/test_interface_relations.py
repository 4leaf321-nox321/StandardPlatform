"""관계 끝에 인터페이스 — **그것을 구현한 타입이면 된다**(ADR 0006).

「설비는 교정 기관에서 교정받는다」 를 시험장비 · 계측기 · 생산설비마다 따로 적으면, 설비가
하나 늘 때마다 관계 종류 셋을 고쳐야 하고 하나를 빠뜨린다. 끝에 인터페이스를 적으면 구현
타입이 늘어도 관계 종류는 그대로다.

**빈 확장 함정**을 본다: 구현 타입이 없는 인터페이스를 끝으로 둔 관계는 「아무 타입이나」 가
아니라 **아무것도 안 된다.** 여러 곳이 빈 목록을 「제약 없음」 으로 읽어 왔다(화면 · 일괄
입력 · 그래프).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, bundle_export
from tests.api.test_interfaces import _add_common, _code, _implement, _make_interface
from tests.api.test_ontology import _link, _make_object, _make_relation, _make_type, _uniq


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """설비(인터페이스: 제조사) — 시험장비가 구현, 부품은 아니다. 「교정」 은 기관 → 설비."""
    iface = _make_interface(client, admin, label="설비")
    _add_common(
        client, admin, iface, key="maker", label="제조사", data_type="text"
    ).raise_for_status()
    tester = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    part = _make_type(client, admin, label="부품", key_policy="optional")
    lab = _make_type(client, admin, label="교정기관", key_policy="optional")
    kind = _make_relation(
        client,
        admin,
        "calibrates",
        label="교정",
        src_type_slugs=[lab],
        dst_type_slugs=[iface],
    )
    return {"iface": iface, "tester": tester, "part": part, "lab": lab, "kind": kind}


def _rows(client: TestClient, who: Signed, type_slug: str, rows: list[dict[str, str]]) -> Any:
    got = client.post(
        f"/api/objects/{type_slug}/relations/import-rows",
        json={"rows": rows},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return got.json()


def test_끝에_인터페이스를_적으면_구현_타입이_서고_아닌_타입은_이유와_함께_거절된다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    kosi = _make_object(client, admin, w["lab"], label="한국교정원")
    rig = _make_object(client, admin, w["tester"], label="시험기 1")
    bolt = _make_object(client, admin, w["part"], label="볼트")

    assert _link(client, admin, w["lab"], kosi["id"], w["kind"], rig["id"]).status_code == 201
    refused = _link(client, admin, w["lab"], kosi["id"], w["kind"], bolt["id"])
    assert refused.status_code == 409 and _code(refused).endswith("OBJECTS-0022")
    message = refused.json()["error"]["message"]
    # slug 가 아니라 이름으로, 인터페이스는 「구현한 타입」 으로 말한다.
    assert "설비을(를) 구현한 타입" in message and "부품" in message

    # **구현 타입이 늘면 관계 종류를 안 고쳐도 된다.**
    meter = _make_type(client, admin, label="계측기", key_policy="optional")
    gauge = _make_object(client, admin, meter, label="게이지")
    assert (
        _link(client, admin, w["lab"], kosi["id"], w["kind"], gauge["id"]).status_code == 409
    )
    _implement(client, admin, meter, w["iface"]).raise_for_status()
    assert (
        _link(client, admin, w["lab"], kosi["id"], w["kind"], gauge["id"]).status_code == 201
    )


def test_관계_끝에는_있는_타입이나_인터페이스만_적는다(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin)
    made = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("uses"), "label": "사용", "dst_type_slugs": [iface]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    wrong = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("uses"), "label": "사용", "dst_type_slugs": ["no_such_thing"]},
        headers=admin.headers,
    )
    assert wrong.status_code == 404 and _code(wrong).endswith("ONTOLOGY-0041")

    # 가져오기도 같은 말 — 인터페이스 끝은 받는다.
    planned = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {"slug": _uniq("uses"), "label": "사용", "src_type_slugs": [iface]}
            ]
        },
        headers=admin.headers,
    )
    assert planned.status_code == 200, planned.text
    assert planned.json()["errors"] == [], planned.json()

    # 끝에 적힌 인터페이스는 지우지 못한다 — 무엇이 가리키는지 말한다.
    usage = client.get(f"/api/ontology/interfaces/{iface}/usage", headers=admin.headers)
    assert usage.json()["relation_types"], usage.json()
    assert (
        client.delete(f"/api/ontology/interfaces/{iface}", headers=admin.headers).status_code
        == 409
    )


def test_구현_타입이_없는_인터페이스를_끝으로_둔_관계는_아무것도_잇지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """빈 확장을 「제약 없음」 으로 읽으면 아무 타입이나 잇는다 — 화면과 일괄 입력 둘 다."""
    lonely = _make_interface(client, admin, "lonely")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    kind = _make_relation(client, admin, "uses", label="사용", dst_type_slugs=[lonely])
    bolt = _make_object(client, admin, part, label="볼트")
    nut = _make_object(client, admin, part, label="너트")

    assert _link(client, admin, part, bolt["id"], kind, nut["id"]).status_code == 409
    plan = _rows(client, admin, part, [{"src": "볼트", "relation": kind, "dst": "너트"}])
    assert plan["rows"][0]["action"] == "error", plan["rows"]
    assert "해당하는 타입이 없습니다" in plan["rows"][0]["message"], plan["rows"]


def test_일괄_관계는_인터페이스의_구현_타입에서만_끝점을_찾는다(
    client: TestClient, admin: Signed
) -> None:
    """같은 이름이 구현 타입과 다른 타입에 함께 있으면 구현 타입의 것을 집는다 — 넓어지지
    않는다."""
    w = _world(client, admin)
    _make_object(client, admin, w["lab"], label="한국교정원")
    rig = _make_object(client, admin, w["tester"], label="공용 이름")
    _make_object(client, admin, w["part"], label="공용 이름")

    plan = _rows(
        client,
        admin,
        w["lab"],
        [{"src": "한국교정원", "relation": w["kind"], "dst": "공용 이름"}],
    )
    assert [one["action"] for one in plan["rows"]] == ["create"], plan["rows"]
    applied = client.post(
        f"/api/objects/{w['lab']}/relations/import-rows",
        json={
            "rows": [{"src": "한국교정원", "relation": w["kind"], "dst": "공용 이름"}],
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    assert applied["applied"] is True, applied
    related = client.get(f"/api/objects/{w['tester']}/{rig['id']}", headers=admin.headers)
    assert any(one["relation"] == w["kind"] for one in related.json()["related"]), (
        related.json()
    )


def test_끝이_인터페이스_하나면_그_공통_속성으로_거른다(
    client: TestClient, admin: Signed
) -> None:
    """「A사 설비를 교정하는 기관」 — 관계 너머의 칸이 공통 속성이면 구현 타입 전부에
    걸린다."""
    w = _world(client, admin)
    kosi = _make_object(client, admin, w["lab"], label="한국교정원")
    other = _make_object(client, admin, w["lab"], label="다른 기관")
    rig = _make_object(client, admin, w["tester"], label="시험기", properties={"maker": "A사"})
    spare = _make_object(client, admin, w["tester"], label="예비", properties={"maker": "B사"})
    _link(client, admin, w["lab"], kosi["id"], w["kind"], rig["id"]).raise_for_status()
    _link(client, admin, w["lab"], other["id"], w["kind"], spare["id"]).raise_for_status()

    fields = {
        one["field"]: one
        for one in client.get(f"/api/objects/{w['lab']}/fields", headers=admin.headers).json()
    }
    assert f"out.{w['kind']}.maker" in fields, sorted(fields)
    assert fields[f"out.{w['kind']}"]["ref_type_slug"] == w["iface"]

    got = client.get(
        f"/api/objects/{w['lab']}",
        params={f"f.out.{w['kind']}.maker.eq": "A사"},
        headers=admin.headers,
    )
    assert [one["label"] for one in got.json()["items"]] == ["한국교정원"]


def test_그래프는_구현_타입마다_선을_긋고_인터페이스로_거른다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    overview = client.get("/api/graph/overview", headers=admin.headers).json()
    declared = {
        (one["src_type"], one["dst_type"])
        for one in overview["edges"]
        if one["relation"] == w["kind"]
    }
    # 그림에는 타입만 선다 — 인터페이스 자리에 구현 타입이.
    assert declared == {(w["lab"], w["tester"])}

    kosi = _make_object(client, admin, w["lab"], label="한국교정원")
    rig = _make_object(client, admin, w["tester"], label="시험기")
    _link(client, admin, w["lab"], kosi["id"], w["kind"], rig["id"]).raise_for_status()
    near = client.get(
        "/api/graph/neighborhood",
        params={"focus": kosi["id"], "types": w["iface"]},
        headers=admin.headers,
    )
    assert near.status_code == 200, near.text
    assert rig["id"] in {one["id"] for one in near.json()["nodes"]}


def test_구현을_해제하면_관계_끝에서_빠진다고_저장_전에_말한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    kosi = _make_object(client, admin, w["lab"], label="한국교정원")
    rig = _make_object(client, admin, w["tester"], label="시험기")
    _link(client, admin, w["lab"], kosi["id"], w["kind"], rig["id"]).raise_for_status()

    plan = client.post(
        f"/api/ontology/types/{w['tester']}/interfaces/plan",
        json={"interface_slugs": []},
        headers=admin.headers,
    )
    assert plan.status_code == 200, plan.text
    said = [one for one in plan.json()["warnings"] if w["kind"] in one]
    assert said and "1건은 남지만" in said[0], plan.json()["warnings"]


def test_묶음은_끝의_인터페이스가_함께_실릴_때_관계_종류를_싣는다(
    client: TestClient, admin: Signed
) -> None:
    group = _uniq("grp")
    client.post(
        "/api/ontology/groups", json={"slug": group, "label": "설비"}, headers=admin.headers
    ).raise_for_status()
    iface = _make_interface(client, admin)
    tester = _make_type(client, admin, nav_group_slug=group, interface_slugs=[iface])
    lab = _make_type(client, admin, nav_group_slug=group)
    kind = _make_relation(
        client, admin, "calibrates", src_type_slugs=[lab], dst_type_slugs=[iface]
    )
    bundle = bundle_export(client, admin, group)
    assert kind in {one["slug"] for one in bundle["ontology"]["relation_types"]}
    assert tester in {one["slug"] for one in bundle["ontology"]["types"]}
