"""롤업 — **「아래 전부」 의 숫자를 볼 때마다 모은다.** 저장하지 않는다.

저장하면 부품 하나를 고친 날 어셈블리는 옛 값을 보여 주고, 그 차이는 아무 데도 안 뜬다.
그리고 값이 빈 것이 몇 개인지 함께 말해야 합계가 「전부의 합」 으로 안 읽힌다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _assembly(client: TestClient, admin: Signed) -> tuple[str, str]:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="weight", label="무게", data_type="number", unit="kg"
    )
    _make_property(client, admin, part, key="name", label="명칭", data_type="text")
    kind = _make_relation(
        client,
        admin,
        "part_of",
        label="속함",
        inverse_label="포함",
        transitive=True,
        acyclic=True,
    )
    return part, kind


def test_롤업은_트리와_숫자_칸이_있어야_한다(client: TestClient, admin: Signed) -> None:
    part, kind = _assembly(client, admin)
    no_tree = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"rollups": [{"property": "weight", "fn": "sum"}]}},
        headers=admin.headers,
    )
    assert no_tree.status_code == 422 and "트리" in no_tree.json()["error"]["message"]

    text_column = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [{"property": "name", "fn": "sum"}],
            }
        },
        headers=admin.headers,
    )
    assert text_column.status_code == 422 and "숫자" in text_column.json()["error"]["message"]

    bad_fn = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [{"property": "weight", "fn": "median"}],
            }
        },
        headers=admin.headers,
    )
    assert bad_fn.status_code == 422


def test_아래_전부를_모으고_빈_것을_센다(client: TestClient, admin: Signed) -> None:
    part, kind = _assembly(client, admin)
    ok = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [
                    {"property": "weight", "fn": "sum", "label": "총 무게"},
                    {"property": "weight", "fn": "max"},
                    {"property": "weight", "fn": "count"},
                ],
            }
        },
        headers=admin.headers,
    )
    assert ok.status_code == 200, ok.text

    top = _make_object(client, admin, part, label="어셈블리")
    mid = _make_object(client, admin, part, label="서브", properties={"weight": 1.5})
    leaf = _make_object(client, admin, part, label="볼트", properties={"weight": 0.25})
    blank = _make_object(client, admin, part, label="와셔")  # 무게 없음
    _link(client, admin, part, mid["id"], kind, top["id"])
    _link(client, admin, part, leaf["id"], kind, mid["id"])
    _link(client, admin, part, blank["id"], kind, mid["id"])

    got = client.get(f"/api/objects/{part}/{top['id']}/rollup", headers=admin.headers)
    assert got.status_code == 200, got.text
    rows = {(r["fn"]): r for r in got.json()}
    assert rows["sum"]["label"] == "총 무게" and rows["sum"]["value"] == 1.75
    assert rows["max"]["value"] == 1.5 and rows["max"]["label"] == "무게 최대"
    assert rows["count"]["value"] == 2
    # 아래 셋 중 하나가 비었다 — 그것을 말한다.
    assert rows["sum"]["descendants"] == 3 and rows["sum"]["count"] == 2
    assert rows["sum"]["missing"] == 1

    # 잎에서는 아래가 없다 — 합계는 0 이 아니라 「없음」 이다.
    leaf_rollup = client.get(f"/api/objects/{part}/{leaf['id']}/rollup", headers=admin.headers)
    assert leaf_rollup.json()[0]["value"] is None and leaf_rollup.json()[0]["descendants"] == 0

    # 저장하지 않는다 — 부품을 고치면 어셈블리의 수가 바로 바뀐다.
    client.patch(
        f"/api/objects/{part}/{leaf['id']}",
        json={"properties": {"weight": 0.5}},
        headers=admin.headers,
    )
    again = client.get(f"/api/objects/{part}/{top['id']}/rollup", headers=admin.headers).json()
    assert next(r for r in again if r["fn"] == "sum")["value"] == 2.0


def test_롤업이_없는_타입은_빈_목록이다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    bolt = _make_object(client, admin, part, label="볼트")
    assert (
        client.get(f"/api/objects/{part}/{bolt['id']}/rollup", headers=admin.headers).json()
        == []
    )
