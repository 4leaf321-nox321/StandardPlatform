"""조건 거르기와 저장된 뷰 — **칸 안 OR·칸끼리 AND 가 맞게 걸리나, 뷰가 범위를 지키나.**"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _world(client: TestClient, admin: Signed) -> tuple[str, str, dict[str, Any]]:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(
        client,
        admin,
        part,
        key="region",
        label="지역",
        data_type="enum",
        enum_options=["수도권", "영남", "호남"],
    )
    _make_property(
        client, admin, part, key="tags", label="꼬리표", data_type="text", multi=True
    )
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    _make_property(client, admin, part, key="made", label="제조일", data_type="date")
    acme = _make_object(client, admin, vendor, label="ACME")
    rows = {
        "a": _make_object(
            client,
            admin,
            part,
            key="P-A",
            label="볼트",
            properties={
                "weight": 5,
                "region": "영남",
                "tags": ["x", "y"],
                "vendor": acme["id"],
                "made": "2026-01-10",
            },
        ),
        "b": _make_object(
            client,
            admin,
            part,
            key="P-B",
            label="너트",
            properties={
                "weight": 12,
                "region": "호남",
                "tags": ["y"],
                "made": "2026-03-01",
            },
        ),
        "c": _make_object(
            client,
            admin,
            part,
            key="P-C",
            label="와셔",
            properties={
                "weight": 20,
                "region": "수도권",
                "made": "2025-12-31",
            },
        ),
    }
    return part, vendor, rows


def _labels(client: TestClient, who: Signed, part: str, **params: str) -> list[str]:
    response = client.get(f"/api/objects/{part}", params=params, headers=who.headers)
    assert response.status_code == 200, response.text
    return sorted(one["label"] for one in response.json()["items"])


def test_숫자_범위와_선택_OR_와_비어_있음(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    assert _labels(client, admin, part, **{"f.weight.gte": "10"}) == ["너트", "와셔"]
    assert _labels(client, admin, part, **{"f.weight.gt": "5", "f.weight.lt": "20"}) == [
        "너트"
    ]
    assert _labels(client, admin, part, **{"f.region.in": "영남|호남"}) == ["너트", "볼트"]
    assert _labels(client, admin, part, **{"f.vendor.empty": ""}) == ["너트", "와셔"]
    assert _labels(client, admin, part, **{"f.vendor.notempty": ""}) == ["볼트"]
    # 칸끼리는 AND.
    assert _labels(
        client, admin, part, **{"f.region.in": "영남|호남", "f.weight.gte": "10"}
    ) == ["너트"]


def test_여러_값_칸과_글자와_날짜(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    assert _labels(client, admin, part, **{"f.tags.eq": "y"}) == ["너트", "볼트"]
    assert _labels(client, admin, part, **{"f.tags.in": "x|z"}) == ["볼트"]
    assert _labels(client, admin, part, **{"f.label.contains": "트"}) == ["너트", "볼트"]
    assert _labels(client, admin, part, **{"f.key.starts": "P-C"}) == ["와셔"]
    assert _labels(client, admin, part, **{"f.made.gte": "2026-01-01"}) == ["너트", "볼트"]
    assert _labels(client, admin, part, **{"f.region.ne": "영남"}) == ["너트", "와셔"]


def test_없는_칸과_안_맞는_연산은_무엇이_틀렸는지_말한다(
    client: TestClient, admin: Signed
) -> None:
    part, _, _ = _world(client, admin)
    missing = client.get(
        f"/api/objects/{part}", params={"f.color.eq": "x"}, headers=admin.headers
    )
    assert missing.status_code == 422 and "color" in missing.json()["error"]["message"]
    wrong = client.get(
        f"/api/objects/{part}", params={"f.region.gt": "x"}, headers=admin.headers
    )
    assert wrong.status_code == 422 and "gt" in wrong.json()["error"]["message"]
    nan = client.get(
        f"/api/objects/{part}", params={"f.weight.gte": "무거움"}, headers=admin.headers
    )
    assert nan.status_code == 422


def test_내보내기도_같은_조건을_쓴다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    exported = client.get(
        f"/api/objects/{part}/export", params={"f.weight.gte": "10"}, headers=admin.headers
    )
    lines = exported.text.lstrip("﻿").splitlines()
    assert len(lines) == 3 and "볼트" not in exported.text


def _view_body(**kw: Any) -> dict[str, Any]:
    return {
        "name": kw.pop("name", "무거운 것"),
        "query": {"q": "", "conditions": [{"field": "weight", "op": "gte", "value": "10"}]},
        **kw,
    }


def test_뷰는_내_것과_부서_것을_가르고_범위를_지킨다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    part, _, _ = _world(client, admin)
    # member 는 자기 뷰만 만들 수 있고, 부서 뷰는 못 만든다.
    mine = client.post(
        f"/api/objects/{part}/views", json=_view_body(name="내 것"), headers=member.headers
    )
    assert mine.status_code == 201, mine.text
    assert mine.json()["workspace_slug"] is None and mine.json()["can_edit"] is True
    denied = client.post(
        f"/api/objects/{part}/views",
        json=_view_body(name="부서 것", workspace_slug=member.workspace),
        headers=member.headers,
    )
    assert denied.status_code == 403
    # 부서 관리자는 부서 뷰를 만든다.
    shared = client.post(
        f"/api/objects/{part}/views",
        json=_view_body(name="부서 것", workspace_slug=manager.workspace),
        headers=manager.headers,
    )
    assert shared.status_code == 201, shared.text

    # member 목록: 부서 것이 먼저, 내 것. 부서 것은 못 고친다.
    listed = client.get(f"/api/objects/{part}/views", headers=member.headers).json()
    assert [one["name"] for one in listed] == ["부서 것", "내 것"]
    assert listed[0]["can_edit"] is False
    # manager 목록: 부서 것만(member 의 개인 뷰는 안 보인다).
    assert [
        one["name"]
        for one in client.get(f"/api/objects/{part}/views", headers=manager.headers).json()
    ] == ["부서 것"]

    # member 가 부서 뷰를 지우려 하면 막힌다. 자기 것은 지운다.
    assert (
        client.delete(
            f"/api/objects/{part}/views/{shared.json()['id']}", headers=member.headers
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/objects/{part}/views/{mine.json()['id']}", headers=member.headers
        ).status_code
        == 204
    )


def test_뷰는_저장할_때_조건을_검사한다(client: TestClient, admin: Signed) -> None:
    """저장은 됐는데 열면 422 인 뷰는 아무도 못 고친다."""
    part, _, _ = _world(client, admin)
    bad = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "틀린 것",
            "query": {"q": "", "conditions": [{"field": "nope", "op": "eq", "value": "1"}]},
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422


def test_뷰를_고치면_이름과_조건이_바뀐다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    made = client.post(
        f"/api/objects/{part}/views", json=_view_body(), headers=admin.headers
    ).json()
    patched = client.patch(
        f"/api/objects/{part}/views/{made['id']}",
        json={
            "name": "아주 무거운 것",
            "query": {
                "q": "",
                "conditions": [{"field": "weight", "op": "gte", "value": "20"}],
            },
        },
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "아주 무거운 것"
    assert patched.json()["query"]["conditions"][0]["value"] == "20"
