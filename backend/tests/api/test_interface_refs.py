"""참조 대상에 인터페이스 — **그것을 구현한 타입의 객체를 가리킨다**(ADR 0006).

「사용 설비」 칸이 시험장비 · 계측기 어느 것이든 가리키게. 그리고 **저장할 때 대상 타입을
확인한다** — 예전에는 있는지만 봐서 「공급사」 칸에 부품이 들어가도 막지 않았다. 다만 이 검사가
생기기 전에 저장된 값이 무관한 저장을 막지 않게 **새로 적힌 값만** 본다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_interfaces import _add_common, _code, _make_interface
from tests.api.test_ontology import _make_object, _make_property, _make_type, _uniq


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """설비(제조사) — 시험장비 · 계측기가 구현, 부품은 아니다. 작업지시의 「사용 설비」 칸."""
    iface = _make_interface(client, admin, label="설비")
    _add_common(
        client, admin, iface, key="maker", label="제조사", data_type="text"
    ).raise_for_status()
    tester = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    meter = _make_type(
        client, admin, label="계측기", key_policy="optional", interface_slugs=[iface]
    )
    part = _make_type(client, admin, label="부품", key_policy="optional")
    order = _make_type(client, admin, label="작업지시", key_policy="optional")
    _make_property(
        client,
        admin,
        order,
        key="equipment",
        label="사용 설비",
        data_type="object_ref",
        ref_type_slug=iface,
    )
    return {"iface": iface, "tester": tester, "meter": meter, "part": part, "order": order}


def test_대상이_인터페이스면_구현_타입의_것을_가리키고_아닌_것은_이유와_함께_거절한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    rig = _make_object(client, admin, w["tester"], label="시험기")
    gauge = _make_object(client, admin, w["meter"], label="게이지")
    bolt = _make_object(client, admin, w["part"], label="볼트")

    _make_object(client, admin, w["order"], label="WO-1", properties={"equipment": rig["id"]})
    _make_object(
        client, admin, w["order"], label="WO-2", properties={"equipment": gauge["id"]}
    )
    refused = client.post(
        f"/api/objects/{w['order']}",
        json={
            "label": "WO-3",
            "workspace_slug": admin.workspace,
            "properties": {"equipment": bolt["id"]},
        },
        headers=admin.headers,
    )
    assert refused.status_code == 422 and _code(refused).endswith("OBJECTS-0094")
    message = refused.json()["error"]["message"]
    assert "볼트" in message and "설비을(를) 구현한 타입" in message


def test_대상이_타입이어도_다른_타입의_것은_이제_막는다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사")
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    other = _make_object(client, admin, part, label="엉뚱한 것")
    refused = client.post(
        f"/api/objects/{part}",
        json={
            "label": "볼트",
            "workspace_slug": admin.workspace,
            "properties": {"vendor": other["id"]},
        },
        headers=admin.headers,
    )
    assert refused.status_code == 422 and _code(refused).endswith("OBJECTS-0094")


def test_대상_검사는_새로_적힌_값만_본다(client: TestClient, admin: Signed) -> None:
    """옛 값이 대상 밖이어도 **다른 칸을 고치는 저장은 막지 않는다** — 그 칸을 새 값으로 바꿀
    때만 걸린다."""
    w = _world(client, admin)
    _make_property(client, admin, w["order"], key="note", label="메모", data_type="text")
    _make_property(
        client,
        admin,
        w["order"],
        key="spare",
        label="예비 부품",
        data_type="object_ref",
        ref_type_slug=w["part"],
    )
    bolt = _make_object(client, admin, w["part"], label="볼트")
    nut = _make_object(client, admin, w["part"], label="너트")
    order = _make_object(
        client, admin, w["order"], label="WO", properties={"spare": bolt["id"]}
    )
    # 대상을 바꾼다 — 이미 저장된 값(볼트)은 이제 대상 밖이다.
    changed = client.patch(
        f"/api/ontology/types/{w['order']}/properties/spare",
        json={
            "key": "spare",
            "label": "예비 부품",
            "data_type": "object_ref",
            "ref_type_slug": w["iface"],
        },
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text

    url = f"/api/objects/{w['order']}/{order['id']}"
    kept = client.patch(url, json={"properties": {"note": "점검"}}, headers=admin.headers)
    assert kept.status_code == 200, kept.text
    moved = client.patch(url, json={"properties": {"spare": nut["id"]}}, headers=admin.headers)
    assert moved.status_code == 422 and _code(moved).endswith("OBJECTS-0094")


def test_참조_대상에는_있는_타입이나_인터페이스만_적는다(
    client: TestClient, admin: Signed
) -> None:
    kind = _make_type(client, admin)
    wrong = client.post(
        f"/api/ontology/types/{kind}/properties",
        json={"key": "x", "label": "x", "data_type": "object_ref", "ref_type_slug": "no_such"},
        headers=admin.headers,
    )
    assert wrong.status_code == 404 and _code(wrong).endswith("ONTOLOGY-0041")

    # 가져오기는 **경고만** — 옛 스냅샷의 사라진 대상 하나가 복원 전체를 막지 않게.
    iface = _make_interface(client, admin)
    planned = client.post(
        "/api/ontology/import",
        json={
            "types": [
                {
                    "slug": _uniq("order"),
                    "label": "작업지시",
                    "properties": [
                        {
                            "key": "eq",
                            "label": "설비",
                            "data_type": "object_ref",
                            "ref_type_slug": iface,
                        },
                        {
                            "key": "gone",
                            "label": "사라진 것",
                            "data_type": "object_ref",
                            "ref_type_slug": "no_such",
                        },
                    ],
                }
            ]
        },
        headers=admin.headers,
    )
    body = planned.json()
    assert body["errors"] == [], body
    assert any("no_such" in one for one in body["warnings"]), body["warnings"]


def test_일괄_입력은_구현_타입_전부에서_풀고_식별자가_겹치면_짐작하지_않는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    rig = _make_object(client, admin, w["tester"], label="시험기", key="EQ-1")
    _make_object(client, admin, w["meter"], label="게이지", key="EQ-2")
    _make_object(client, admin, w["meter"], label="겹침 계측기", key="EQ-1")
    _make_object(client, admin, w["part"], label="볼트", key="P-1")

    plan = client.post(
        f"/api/objects/{w['order']}/import-rows",
        json={
            "rows": [
                {"label": "이름으로", "equipment": "시험기"},
                {"label": "식별자로", "equipment": "EQ-2"},
                {"label": "겹친 식별자", "equipment": "EQ-1"},
                {"label": "구현 안 한 타입", "equipment": "볼트"},
            ]
        },
        headers=admin.headers,
    ).json()
    actions = [(one["action"], one["message"]) for one in plan["rows"]]
    assert actions[0][0] == "create" and actions[1][0] == "create", actions
    assert actions[2][0] == "error" and "여럿" in actions[2][1], actions
    assert actions[3][0] == "error" and "찾을 수 없습니다" in actions[3][1], actions
    assert rig["id"]


def test_지우기_전_확인은_인터페이스로_가리킨_칸도_센다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    rig = _make_object(client, admin, w["tester"], label="시험기")
    _make_object(client, admin, w["order"], label="WO-1", properties={"equipment": rig["id"]})
    refs = client.get(
        f"/api/objects/{w['tester']}/{rig['id']}/references", headers=admin.headers
    ).json()
    assert [one["property_key"] for one in refs["property_refs"]] == ["equipment"], refs


def test_인터페이스를_가리키는_칸으로_공통_속성을_건너_거르고_그래프에_선이_선다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    rig = _make_object(client, admin, w["tester"], label="시험기", properties={"maker": "A사"})
    gauge = _make_object(
        client, admin, w["meter"], label="게이지", properties={"maker": "B사"}
    )
    _make_object(client, admin, w["order"], label="WO-A", properties={"equipment": rig["id"]})
    _make_object(
        client, admin, w["order"], label="WO-B", properties={"equipment": gauge["id"]}
    )

    got = client.get(
        f"/api/objects/{w['order']}",
        params={"f.ref.equipment.maker.eq": "A사"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert [one["label"] for one in got.json()["items"]] == ["WO-A"]

    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    edge = next(
        one
        for one in schema["reference_edges"]
        if one["src_type_slug"] == w["order"] and one["field_key"] == "equipment"
    )
    assert edge["dst_type_slug"] == w["iface"]  # 적힌 대로

    overview = client.get("/api/graph/overview", headers=admin.headers).json()
    drawn = {
        (one["dst_type"], one["count"])
        for one in overview["edges"]
        if one["relation"] == edge["slug"]
    }
    # 그림에는 구현 타입마다 한 선.
    assert drawn == {(w["tester"], 1), (w["meter"], 1)}

    related = client.get(f"/api/objects/{w['tester']}/{rig['id']}", headers=admin.headers)
    assert any(
        one["stored_as"] == "field" and one["object_label"] == "WO-A"
        for one in related.json()["related"]
    ), related.json()["related"]
