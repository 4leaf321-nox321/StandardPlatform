"""별칭 — **같은 것을 다르게 불러도 같은 것으로 풀린다.**

못 찾은 사람은 새로 만들고, 그러면 같은 것이 둘이 된다. 찾기·참조 풀이·파일·합치기가 전부
별칭을 봐야 그 일이 안 생긴다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _aliases(
    client: TestClient, who: Signed, type_slug: str, object_id: str, values: list[str]
) -> Any:
    return client.put(
        f"/api/objects/{type_slug}/{object_id}/aliases",
        json={"aliases": values},
        headers=who.headers,
    )


def test_별칭을_붙이면_찾기에_걸리고_이력이_남는다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    got = _aliases(client, admin, vendor, ansys["id"], ["앤시스", " ANSYS Inc. ", "앤시스"])
    assert got.status_code == 200, got.text
    assert got.json()["aliases"] == ["앤시스", "ANSYS Inc."]

    found = client.get(f"/api/objects/{vendor}", params={"q": "앤시"}, headers=admin.headers)
    assert [row["label"] for row in found.json()["items"]] == ["Ansys"]
    graph = client.get("/api/graph/search", params={"q": "ANSYS Inc"}, headers=admin.headers)
    assert any(hit["id"] == ansys["id"] for hit in graph.json())

    history = client.get(
        f"/api/objects/{vendor}/{ansys['id']}/history", headers=admin.headers
    ).json()
    assert history[0]["changes"]["aliases"]["after"] == ["앤시스", "ANSYS Inc."]


def test_같은_타입의_다른_객체가_쓰는_별칭은_거절한다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    other = _make_object(client, admin, vendor, label="Altair")
    assert _aliases(client, admin, vendor, ansys["id"], ["Anssys"]).status_code == 200
    denied = _aliases(client, admin, vendor, other["id"], ["anssys"])
    assert denied.status_code == 422
    assert "Ansys의 별칭" in denied.json()["error"]["message"]


def test_파일의_참조가_별칭으로_풀린다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    ansys = _make_object(client, admin, vendor, label="Ansys")
    _aliases(client, admin, vendor, ansys["id"], ["ANSYS Inc."])

    done = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [
                {
                    "key": "P-1",
                    "label": "볼트",
                    "vendor": "ansys inc.",
                    "aliases": "bolt;볼트M6",
                }
            ],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"]
    bolt = listed[0]
    assert bolt["properties"]["vendor"] == ansys["id"]
    assert bolt["aliases"] == ["bolt", "볼트M6"]

    # 내보내면 별칭 열이 있고, 그대로 다시 넣으면 「그대로」 다.
    exported = client.get(
        f"/api/objects/{part}/export", params={"format": "json"}, headers=admin.headers
    ).json()["rows"]
    assert exported[0]["aliases"] == "bolt;볼트M6"
    again = client.post(
        f"/api/objects/{part}/import-rows",
        json={"rows": exported, "workspace_slug": admin.workspace, "apply": False},
        headers=admin.headers,
    ).json()
    assert [r["action"] for r in again["rows"]] == ["unchanged"]

    # 관계 파일의 끝점도 별칭으로.
    from tests.api.test_ontology import _make_relation

    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    linked = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={"rows": [{"src": "bolt", "relation": kind, "dst": "ANSYS Inc."}], "apply": True},
        headers=admin.headers,
    ).json()
    assert linked["applied"] is True, linked


def test_합치면_지는_쪽_이름이_별칭으로_남는다(client: TestClient, admin: Signed) -> None:
    """같은 표기로 다시 와도 같은 것으로 풀린다 — 중복이 다시 안 생긴다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    dup = _make_object(client, admin, vendor, label="ANSYS Inc.")
    _aliases(client, admin, vendor, dup["id"], ["앤시스"])
    merged = client.post(
        f"/api/objects/{vendor}/{dup['id']}/merge",
        json={"into": ansys["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    winner = client.get(f"/api/objects/{vendor}/{ansys['id']}", headers=admin.headers).json()
    assert set(winner["object"]["aliases"]) == {"앤시스", "ANSYS Inc."}

    # 이제 「ANSYS Inc.」 로 넣으면 새로 만들지 않고 같은 것을 가리킨다.
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    plan = client.post(
        f"/api/objects/{part}/import-rows",
        json={"rows": [{"label": "볼트", "vendor": "ANSYS Inc."}], "apply": True},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is True
    row = client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"][0]
    assert row["properties"]["vendor"] == ansys["id"]


def test_별칭이_다른_객체의_이름과_같으면_품질에_뜬다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    _make_object(client, admin, vendor, label="Altair")
    _aliases(client, admin, vendor, ansys["id"], ["altair"])
    report = client.get(
        "/api/objects/quality/report", params={"kind": "alias_clash"}, headers=admin.headers
    ).json()
    finding = next(f for f in report["findings"] if f["type_slug"] == vendor)
    assert finding["count"] == 1 and "Altair" in finding["hits"][0]["detail"]
