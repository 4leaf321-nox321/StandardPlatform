"""동기화의 참조(ADR 0089) — 함께 온 행 · 참조 대기 · 칸이 늘면 다시 받기.

- **같은 동기화에서 함께 온 행도 참조 대상이다.** 같은 타입을 가리키는 칸(상위 절)이 이 차례의
  뒤 행을 가리켜도, 먼저 행을 다 만들고 그 칸을 채운다(두 단계, 한 트랜잭션).
- **끝내 못 찾은 참조는 그 칸만 비우고 넣는다** — 참조 대기로 남아 다음 동기화가 다시 찾고,
  `REF_WAIT_LIMIT` 번 연속 못 찾으면 오류로 올린다. 필수 칸이면 그 줄만 미뤘다가 다시 싣는다.
- 미리 보기는 같은 동기화에서 풀 참조 · 참조 대기를 따로 세고, 이 차례에 함께 오는 객체를 잇는
  선은 「기다림」 이 아니라 「함께 들어오는 선」 으로 센다.
- 칸 대응에 칸을 더하면 받은 시점을 비워 처음부터 다시 받는다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.modules.datasources import services
from tests.api.conftest import Signed
from tests.api.test_datasource_core import (
    FakeCore,
    _edge,
    _partner_kind,
    _source,
    _sync,
    _vendor_type,
    sibling,
)
from tests.api.test_ontology import _make_object, _make_property, _make_type

_ = sibling


def _item(key: str, label: str, at: str, **properties: Any) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": "active",
        "updated_at": at,
        "deleted": False,
        "properties": properties,
    }


def _with_parent(client: TestClient, admin: Signed) -> str:
    vendor = _vendor_type(client, admin)
    _make_property(
        client,
        admin,
        vendor,
        key="parent",
        label="상위",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    return vendor


def _parent_source(
    client: TestClient, admin: Signed, vendor: str, **kw: Any
) -> dict[str, Any]:
    return _source(
        client,
        admin,
        vendor,
        mapping={
            "external_key": "key",
            "columns": [
                {"source": "key", "target": "key"},
                {"source": "label", "target": "label"},
                {"source": "parent", "target": "properties.parent"},
            ],
        },
        **kw,
    )


def _by_key(client: TestClient, admin: Signed, vendor: str) -> dict[str, dict[str, Any]]:
    got = client.get(f"/api/objects/{vendor}?limit=200", headers=admin.headers).json()
    return {one["key"]: one for one in got["items"]}


def _waiting(client: TestClient, admin: Signed, slug: str) -> int:
    saved = client.get(f"/api/datasources/{slug}", headers=admin.headers).json()
    return int(saved["refs_waiting"])


def test_같은_동기화에서_함께_온_행을_가리키는_칸은_행을_다_넣은_뒤_채운다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """예전에는 「상위」 가 이 차례의 **뒤 행**(아직 없는 객체)을 가리키면 그 줄이 「찾을 수
    없습니다」 오류였고, 오류 한 줄이 그 차례 전체를 막았다 — 시계도 못 옮겨 매번 같은 자리에서
    멈췄다."""
    vendor = _with_parent(client, admin)
    at = "2026-09-01T00:00:00.000000Z"
    sibling.items = [
        _item("V-001", "자식", at, parent="V-003"),  # 뒤 행을 가리킨다
        _item("V-002", "손자", at, parent="V-001"),
        _item("V-003", "부모", at),
    ]
    source = _parent_source(client, admin, vendor)

    planned = _sync(client, admin, source["slug"], apply=False)
    assert planned["run"]["status"] == "planned", planned["run"]
    assert planned["counts"]["create"] == 3 and planned["counts"]["error"] == 0
    assert planned["counts"]["refs_same_sync"] == 2, planned["counts"]
    assert "refs_waiting" not in planned["counts"]

    done = _sync(client, admin, source["slug"])
    assert done["run"]["status"] == "ok", done["run"]
    assert done["counts"]["refs_same_sync"] == 2, done["counts"]
    rows = _by_key(client, admin, vendor)
    assert rows["V-001"]["properties"]["parent"] == rows["V-003"]["id"]
    assert rows["V-002"]["properties"]["parent"] == rows["V-001"]["id"]
    assert _waiting(client, admin, source["slug"]) == 0


def test_끝내_못_찾은_참조는_그_칸만_비우고_기다렸다가_다음_동기화에서_채운다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    vendor = _with_parent(client, admin)
    sibling.items = [
        _item("V-001", "자식", "2026-09-01T00:00:00.000000Z", parent="V-404"),
        _item("V-002", "이웃", "2026-09-01T00:00:00.000000Z"),
    ]
    source = _parent_source(client, admin, vendor)

    planned = _sync(client, admin, source["slug"], apply=False)
    assert planned["run"]["status"] == "planned", planned["run"]
    assert planned["counts"]["refs_waiting"] == 1, planned["counts"]
    first = _sync(client, admin, source["slug"])
    # 행은 들어갔다 — 그 칸만 비었다.
    assert first["run"]["status"] == "ok", first["run"]
    assert first["counts"]["create"] == 2 and first["counts"]["refs_waiting"] == 1
    assert any("기다립니다" in one and "V-404" in one for one in first["run"]["errors"])
    rows = _by_key(client, admin, vendor)
    assert "parent" not in rows["V-001"]["properties"]
    assert _waiting(client, admin, source["slug"]) == 1

    # 가리키던 것이 들어온다 — V-001 은 다시 안 온다(바뀐 것만 받는다).
    sibling.items.append(_item("V-404", "부모", "2026-09-03T00:00:00.000000Z"))
    sibling.as_of = "2026-09-03T12:00:00.000000Z"
    second = _sync(client, admin, source["slug"])
    assert second["run"]["status"] == "ok", second["run"]
    assert second["counts"]["refs_resolved"] == 1, second["counts"]
    assert "refs_waiting" not in second["counts"]
    rows = _by_key(client, admin, vendor)
    assert rows["V-001"]["properties"]["parent"] == rows["V-404"]["id"]
    assert _waiting(client, admin, source["slug"]) == 0


def test_참조를_연속으로_못_찾으면_오류로_올린다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    vendor = _with_parent(client, admin)
    sibling.items = [_item("V-001", "자식", "2026-09-01T00:00:00.000000Z", parent="V-404")]
    source = _parent_source(client, admin, vendor)
    for _round in range(services.REF_WAIT_LIMIT - 1):
        got = _sync(client, admin, source["slug"])
        assert got["run"]["status"] == "ok", got["run"]
        assert got["counts"]["refs_waiting"] == 1, got["counts"]
    # 세 번째 — 객체는 그대로 두고 실행을 실패로 적는다(사람이 원천 값을 봐야 한다).
    late = _sync(client, admin, source["slug"])
    assert late["run"]["status"] == "failed", late["run"]
    assert late["counts"]["refs_error"] == 1, late["counts"]
    assert any(
        f"{services.REF_WAIT_LIMIT}번 연속 찾지 못했습니다" in one
        for one in late["run"]["errors"]
    )
    # 미리 보기는 막지 않는다 — 나머지 행은 계속 들어와야 한다.
    planned = _sync(client, admin, source["slug"], apply=False)
    assert planned["run"]["status"] == "planned", planned["run"]


def test_필수_참조를_못_찾으면_그_줄만_미뤘다가_다시_싣는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """필수 칸은 비울 수 없다 — 그 줄만 미룬다. 시계는 넘어가므로 그 줄은 원천에서 다시 안
    온다 — 미룬 줄을 소스가 들고 있다가 다음 동기화에 다시 싣는다."""
    vendor = _vendor_type(client, admin)
    maker = _make_type(client, admin, label="제조사", key_policy="optional")
    _make_property(
        client,
        admin,
        vendor,
        key="maker",
        label="제조사",
        data_type="object_ref",
        ref_type_slug=maker,
        required=True,
    )
    sibling.items = [
        _item("V-001", "부품", "2026-09-01T00:00:00.000000Z", maker="M-1"),
        _item("V-002", "다른 부품", "2026-09-01T00:00:00.000000Z", maker="M-2"),
    ]
    _make_object(client, admin, maker, key="M-2", label="둘째 제조사")
    source = _source(
        client,
        admin,
        vendor,
        mapping={
            "external_key": "key",
            "columns": [
                {"source": "key", "target": "key"},
                {"source": "label", "target": "label"},
                {"source": "maker", "target": "properties.maker"},
            ],
        },
    )
    first = _sync(client, admin, source["slug"])
    assert first["run"]["status"] == "ok", first["run"]
    assert first["counts"]["create"] == 1 and first["counts"]["refs_waiting"] == 1
    assert set(_by_key(client, admin, vendor)) == {"V-002"}

    # 다른 소스(여기서는 사람)가 제조사를 넣는다 — 원천은 V-001 을 다시 안 보낸다.
    made = _make_object(client, admin, maker, key="M-1", label="첫째 제조사")
    second = _sync(client, admin, source["slug"])
    assert second["run"]["status"] == "ok", second["run"]
    assert second["counts"]["create"] == 1, second["counts"]
    rows = _by_key(client, admin, vendor)
    assert rows["V-001"]["properties"]["maker"] == made["id"]
    assert _waiting(client, admin, source["slug"]) == 0


def test_미리_보기는_함께_들어오는_객체를_잇는_선을_기다림으로_세지_않는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """행 오류가 없고 선만 기다릴 때 — 처음 받는 미리 보기는 객체를 아직 안 넣어, 이 차례에
    함께 오는 객체를 잇는 선까지 「끝점이 아직 없어 기다립니다」 로 셌다. 적용할 수 있는
    계획인데 막힌 것처럼 읽혔다."""
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [
        _edge("V-001", kind, "V-002", "2026-09-02T00:00:00.000000Z"),  # 둘 다 이 차례에 온다
        _edge("V-001", kind, "V-404", "2026-09-02T00:00:00.000000Z"),  # 정말 없다
    ]
    source = _source(client, admin, vendor, options={"relations": True})
    planned = _sync(client, admin, source["slug"], apply=False)
    assert planned["run"]["status"] == "planned", planned["run"]
    assert planned["counts"]["relations_same_sync"] == 1, planned["counts"]
    assert planned["counts"]["relations_waiting"] == 1, planned["counts"]
    assert any("함께 들어오는 객체를 이어" in one for one in planned["run"]["errors"])
    waits = [one for one in planned["run"]["errors"] if "기다립니다" in one]
    assert len(waits) == 1 and "V-404" in waits[0] and "-> V-002" not in waits[0]

    done = _sync(client, admin, source["slug"])
    assert done["run"]["status"] == "ok", done["run"]
    assert done["counts"]["relations_create"] == 1
    assert done["counts"]["relations_waiting"] == 1


def test_칸_대응에_칸을_더하면_받은_시점을_비워_처음부터_다시_받는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """이어 받기는 바뀐 행만 온다 — 칸을 더해도 이미 받은 행에는 그 칸의 값이 영영 안
    들어왔다."""
    vendor = _vendor_type(client, admin)
    columns = [
        {"source": "key", "target": "key"},
        {"source": "label", "target": "label"},
        {"source": "country", "target": "properties.country"},
    ]
    source = _source(
        client, admin, vendor, mapping={"external_key": "key", "columns": columns}
    )
    _sync(client, admin, source["slug"])
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["since_mark"] == sibling.as_of

    # 대응을 그대로 다시 저장하면 그대로 이어 받는다.
    same = client.patch(
        f"/api/datasources/{source['slug']}",
        json={"mapping": {"external_key": "key", "columns": columns}},
        headers=admin.headers,
    )
    assert same.status_code == 200, same.text
    assert same.json()["since_mark"] == sibling.as_of

    grown = client.patch(
        f"/api/datasources/{source['slug']}",
        json={
            "mapping": {
                "external_key": "key",
                "columns": [*columns, {"source": "rating", "target": "properties.rating"}],
            }
        },
        headers=admin.headers,
    )
    assert grown.status_code == 200, grown.text
    assert grown.json()["since_mark"] == ""
    again = _sync(client, admin, source["slug"])
    assert again["counts"]["update"] == 2, again["counts"]
    rows = _by_key(client, admin, vendor)
    assert rows["V-001"]["properties"]["rating"] == 92
