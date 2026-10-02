"""**사람이 고친 값은 다음 적재가 되돌리지 않는다.**

파일로 넣기는 같은 식별자면 덮는다(upsert). 그래서 원천을 다시 정제해 넣으면 사람이 화면에서
고친 값이 사라지고, 고친 사람은 그 사실을 모른다 — 몇 주 뒤 「내가 고쳤는데 다시 틀려 있다」 로
만나고, 그때 사람은 고치기를 그만둔다. 그 뒤로는 아무도 데이터를 안 고치고, 플랫폼은 원천의
사본이 된다.

지키는 것: 화면에서 고친 칸은 표시되고 · 적재가 비켜 가며 계획에 적고 · 기계 자격은 표시를
남기지 않고 · 「덮어라」 로 보내면 덮고 표시를 지운다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, import_file
from tests.api.test_ontology import _make_property, _make_type


def _rows(client: TestClient, admin: Signed, type_slug: str) -> list[dict[str, Any]]:
    got = client.get(f"/api/objects/{type_slug}", headers=admin.headers)
    assert got.status_code == 200, got.text
    return list(got.json()["items"])


def _one(client: TestClient, admin: Signed, type_slug: str, key: str) -> dict[str, Any]:
    return next(one for one in _rows(client, admin, type_slug) if one["key"] == key)


def _world(client: TestClient, admin: Signed) -> str:
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(client, admin, part, key="vendor", label="공급사", data_type="text")
    import_file(client, admin, part, "key,label,vendor\nP-1,볼트,ACME\n", apply=True)
    return part


def test_사람이_고친_칸은_다시_적재해도_그대로다(client: TestClient, admin: Signed) -> None:
    part = _world(client, admin)
    made = _one(client, admin, part, "P-1")

    # 사람이 화면에서 고친다 — 이름과 공급사.
    fixed = client.patch(
        f"/api/objects/{part}/{made['id']}",
        json={"label": "육각 볼트", "properties": {"vendor": "한국ACME"}},
        headers=admin.headers,
    )
    assert fixed.status_code == 200, fixed.text

    # 같은 원천을 다시 적재한다 — 옛 값이 그대로 들어 있다.
    plan = import_file(client, admin, part, "key,label,vendor\nP-1,볼트,ACME\n")
    row = plan["rows"][0]
    # **바꿀 것이 없다** — 두 칸 다 사람이 고친 칸이다.
    assert row["action"] == "unchanged", row
    assert "사람이 고친 칸은 그대로 둡니다" in row["message"], row
    assert "이름" in row["message"] and "공급사" in row["message"], row["message"]

    import_file(client, admin, part, "key,label,vendor\nP-1,볼트,ACME\n", apply=True)
    after = _one(client, admin, part, "P-1")
    assert after["label"] == "육각 볼트", after
    detail = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert detail["object"]["properties"]["vendor"] == "한국ACME"


def test_사람이_안_건드린_칸은_적재가_고친다(client: TestClient, admin: Signed) -> None:
    """**지키는 것은 칸 단위다.** 객체 하나를 손댔다고 그 객체가 통째로 잠기면, 원천이
    고친 다른 칸까지 못 들어온다 — 그러면 사람은 적재를 못 믿는다."""
    part = _world(client, admin)
    made = _one(client, admin, part, "P-1")
    fixed = client.patch(
        f"/api/objects/{part}/{made['id']}",
        json={"properties": {"vendor": "한국ACME"}},
        headers=admin.headers,
    )
    assert fixed.status_code == 200, fixed.text

    # 원천이 이름을 고쳤다 — 사람이 안 건드린 칸이라 들어간다.
    plan = import_file(
        client, admin, part, "key,label,vendor\nP-1,육각볼트,ACME\n", apply=True
    )
    row = plan["rows"][0]
    assert row["action"] == "update" and row["changes"] == ["label"], row
    after = _one(client, admin, part, "P-1")
    assert after["label"] == "육각볼트"
    detail = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert detail["object"]["properties"]["vendor"] == "한국ACME", "공급사는 그대로"


def test_기계_자격의_수정은_표시를_남기지_않는다(client: TestClient, admin: Signed) -> None:
    """**표시는 사람 세션만 남긴다.** 스크립트가 고친 값은 다음 적재가 덮어도 되는 값이다 —
    기계가 고친 것까지 지켜 주면 적재는 곧 아무것도 못 고치게 된다."""
    part = _world(client, admin)
    made = _one(client, admin, part, "P-1")
    token = client.post(
        "/api/auth/tokens",
        json={"name": f"edit_{uuid.uuid4().hex[:6]}", "scopes": ["read", "objects:write"]},
        headers=admin.headers,
    )
    assert token.status_code == 201, token.text
    machine = {"Authorization": f"Bearer {token.json()['token']}"}

    changed = client.patch(
        f"/api/objects/{part}/{made['id']}", json={"label": "기계가 고침"}, headers=machine
    )
    assert changed.status_code == 200, changed.text

    plan = import_file(client, admin, part, "key,label,vendor\nP-1,볼트,ACME\n", apply=True)
    row = plan["rows"][0]
    assert row["action"] == "update" and "label" in row["changes"], row
    assert _one(client, admin, part, "P-1")["label"] == "볼트"


def test_덮어라고_정하면_덮고_표시를_지운다(client: TestClient, admin: Signed) -> None:
    part = _world(client, admin)
    made = _one(client, admin, part, "P-1")
    client.patch(
        f"/api/objects/{part}/{made['id']}",
        json={"label": "육각 볼트"},
        headers=admin.headers,
    )

    # `human_edits=overwrite` — 원천이 정본이라고 사람이 정한 경우.
    applied = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [{"key": "P-1", "label": "볼트", "vendor": "ACME"}],
            "apply": True,
            "human_edits": "overwrite",
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert applied.status_code == 200, applied.text
    assert _one(client, admin, part, "P-1")["label"] == "볼트"

    # **표시가 지워졌다** — 다음 적재가 또 비켜 가지 않는다(그 값은 이제 파일의 것이다).
    plan = import_file(client, admin, part, "key,label,vendor\nP-1,둥근볼트,ACME\n")
    row = plan["rows"][0]
    assert row["action"] == "update" and "label" in row["changes"], row
    assert "사람이 고친 칸" not in row["message"], row


def test_여럿의_한_칸_고치기도_표시된다(client: TestClient, admin: Signed) -> None:
    """화면의 「여럿 골라 한 칸 바꾸기」 도 사람이 한 일이다 — 그것만 빠지면 사람은
    어느 길로 고쳤는지에 따라 다른 결과를 본다."""
    part = _world(client, admin)
    made = _one(client, admin, part, "P-1")
    edited = client.post(
        f"/api/objects/{part}/bulk-edit",
        json={
            "ids": [made["id"]],
            "field": "properties.vendor",
            "value": "사람이 정한 공급사",
            "apply": True,
        },
        headers=admin.headers,
    )
    assert edited.status_code == 200, edited.text

    plan = import_file(client, admin, part, "key,label,vendor\nP-1,볼트,ACME\n")
    row = plan["rows"][0]
    assert row["action"] == "unchanged", row
    assert "공급사" in row["message"], row["message"]
