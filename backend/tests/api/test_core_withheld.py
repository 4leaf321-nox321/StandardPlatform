"""코어 현황 — **공개 타입에 닿지만 바깥에 안 나가는 관계**를 이유와 함께 말한다.

카탈로그는 나가는 것만 말한다. 끝에 적힌 타입 하나를 공개하지 않아 관계 종류가 통째로 빠져도
그 사실은 어디에도 안 적혀, 받는 쪽은 「그런 관계가 없다」 로 읽고 공개한 쪽은 보냈다고 여겼다
(2026-10-14 — 「관계까지 모두 보내지고 있나」). 여기서 지키는 것: 빠지는 이유가 맞나, 무엇을
공개하면 나가는지 말하나, 빠지는 선을 세나, 공개하면 목록에서 사라지나, 그리고 공개 타입에
안 닿는 종류는 싣지 않나. 참조 속성도 같다 — 공개 안 한 타입을 가리키면 값만 나가고 상대는
받아 올 길이 없다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_core_api import _open
from tests.api.test_core_interfaces import _import
from tests.api.test_interfaces import _make_interface
from tests.api.test_ontology import _make_object, _make_property, _make_relation, _make_type


def _status(client: TestClient, admin: Signed) -> dict[str, Any]:
    got = client.get("/api/ontology/core-status", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _withheld(client: TestClient, admin: Signed, kind: str) -> dict[str, Any] | None:
    rows = _status(client, admin)["withheld_relations"]
    found = [one for one in rows if one["slug"] == kind]
    return found[0] if found else None


def test_끝_타입을_공개하지_않은_관계_종류는_이유와_빠지는_선을_말한다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    kind = _make_relation(
        client, admin, "supplies", label="공급", src_type_slugs=[part], dst_type_slugs=[vendor]
    )
    _open(client, admin, part)
    _make_object(client, admin, part, label="볼트", key="P-1")
    _make_object(client, admin, vendor, label="ACME", key="V-1")
    _import(client, admin, part, [{"src": "P-1", "relation": kind, "dst": "V-1"}])

    withheld = _withheld(client, admin, kind)
    assert withheld is not None
    assert withheld["reason"] == "closed_types"
    assert withheld["missing"] == [vendor]
    assert withheld["missing_labels"] == ["공급사"]
    assert withheld["edges"] == 1

    # **공개하면 나간다** — 목록에서 사라지고 카탈로그에 선다.
    _open(client, admin, vendor)
    assert _withheld(client, admin, kind) is None


def test_인터페이스_끝의_구현_타입_일부만_공개했으면_빠지는_선을_말한다(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin, label="설비")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    tester = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    plant = _make_type(
        client, admin, label="생산설비", key_policy="optional", interface_slugs=[iface]
    )
    kind = _make_relation(
        client, admin, "uses", label="사용", src_type_slugs=[part], dst_type_slugs=[iface]
    )
    _open(client, admin, part)
    _make_object(client, admin, part, label="볼트", key="P-1")
    _make_object(client, admin, tester, label="인장 시험기", key="T-1")
    _make_object(client, admin, plant, label="프레스", key="X-1")
    _import(
        client,
        admin,
        part,
        [
            {"src": "P-1", "relation": kind, "dst": "T-1"},
            {"src": "P-1", "relation": kind, "dst": "X-1"},
        ],
    )

    # 구현 타입이 하나도 공개 안 됐으면 종류가 통째로 안 나간다.
    none_open = _withheld(client, admin, kind)
    assert none_open is not None
    assert none_open["reason"] == "no_open_implementer"
    assert set(none_open["missing"]) == {tester, plant}
    assert none_open["edges"] == 2

    # 하나만 공개하면 **나가지만 일부 선이 빠진다** — 공개 안 한 생산설비를 가리키는 선.
    _open(client, admin, tester)
    partial = _withheld(client, admin, kind)
    assert partial is not None
    assert partial["reason"] == "partial"
    assert partial["missing"] == [plant]
    assert partial["edges"] == 1

    _open(client, admin, plant)
    assert _withheld(client, admin, kind) is None


def test_끝을_안_적은_관계_종류는_공개로는_못_연다고_말한다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    kind = _make_relation(client, admin, "related", label="관련", src_type_slugs=[part])
    _open(client, admin, part)

    withheld = _withheld(client, admin, kind)
    assert withheld is not None
    assert withheld["reason"] == "unconstrained"
    # 공개할 타입이 아니라 **관계 종류에 끝 타입을 적어야** 나간다 — 그래서 비어 있다.
    assert withheld["missing"] == []


def test_공개_타입에_안_닿는_관계_종류는_싣지_않는다(
    client: TestClient, admin: Signed
) -> None:
    left = _make_type(client, admin, label="비공개 갑")
    right = _make_type(client, admin, label="비공개 을")
    kind = _make_relation(
        client, admin, "inner", src_type_slugs=[left], dst_type_slugs=[right]
    )
    assert _withheld(client, admin, kind) is None


def test_공개_안_한_타입을_가리키는_참조_속성을_말한다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    _open(client, admin, part)

    def mine() -> list[dict[str, Any]]:
        return [
            one
            for one in _status(client, admin)["withheld_references"]
            if one["type_slug"] == part
        ]

    found = mine()
    assert [(one["key"], one["ref_type_slug"], one["missing"]) for one in found] == [
        ("vendor", vendor, [vendor])
    ]
    assert found[0]["ref_type_label"] == "공급사"

    _open(client, admin, vendor)
    assert mine() == []
