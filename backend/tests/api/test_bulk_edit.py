"""여럿 골라 한 칸 바꾸기 — **계획 먼저, 못 고치는 것은 이유를 적고.**

여기서 지키는 것: 계획이 아무것도 안 바꾸나, 남의 부서 것이 조용히 건너뛰어지지 않나,
그리고 **한 건씩 기록이 남나**(안 남으면 그 객체의 이력에서 이 변경이 사라지고,
지켜보는 사람에게도 안 간다).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, notifications_of
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _edit(client: TestClient, who: Signed, slug: str, **body: Any) -> Any:
    return client.post(f"/api/objects/{slug}/bulk-edit", json=body, headers=who.headers)


def _three(client: TestClient, admin: Signed) -> tuple[str, list[str]]:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B"],
    )
    ids = [
        _make_object(client, admin, part, label=f"부품{i}", properties={"grade": "A"})["id"]
        for i in range(3)
    ]
    return part, ids


def test_계획이_먼저고_아무것도_안_바꾼다(client: TestClient, admin: Signed) -> None:
    part, ids = _three(client, admin)
    planned = _edit(client, admin, part, ids=ids, field="properties.grade", value="B")
    assert planned.status_code == 200, planned.text
    body = planned.json()
    assert body["applied"] is False
    assert body["field_label"] == "등급"
    assert body["counts"]["change"] == 3
    assert body["rows"][0]["before"] == "A" and body["rows"][0]["after"] == "B"

    # 아직 안 바뀌었다.
    got = client.get(f"/api/objects/{part}/{ids[0]}", headers=admin.headers).json()
    assert got["object"]["properties"]["grade"] == "A"


def test_적용하면_바뀌고_이미_그_값이면_그대로(client: TestClient, admin: Signed) -> None:
    part, ids = _three(client, admin)
    done = _edit(
        client, admin, part, ids=ids, field="properties.grade", value="B", apply=True
    ).json()
    assert done["applied"] is True and done["counts"]["change"] == 3

    again = _edit(client, admin, part, ids=ids, field="properties.grade", value="B").json()
    # **이미 그 값인 것은 「그대로」 다.** 다 바꿨다고 하면 사람은 무엇이 실제로
    # 바뀌는지 모른다.
    assert again["counts"]["unchanged"] == 3 and again["counts"]["change"] == 0


def test_한_건씩_기록이_남아_지켜보는_사람에게_간다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """한 줄로 뭉뚱그리면 그 객체의 이력에서 이 변경이 사라진다."""
    part, ids = _three(client, admin)
    client.put(
        f"/api/objects/{part}/{ids[0]}/watch", json={"on": True}, headers=manager.headers
    )
    before = len(notifications_of(client, manager, "object.changed"))

    _edit(client, admin, part, ids=ids, field="status", value="deprecated", apply=True)

    assert len(notifications_of(client, manager, "object.changed")) == before + 1
    history = client.get(f"/api/objects/{part}/{ids[0]}/history", headers=admin.headers).json()
    assert any("여럿 골라" in (one["reason"] or "") for one in history)


def test_못_고치는_것은_이유를_적고_나머지는_고친다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    """**조용히 건너뛰지 않는다** — 건너뛴 것은 「바꿨다」 고 믿은 사람에게 나중에
    다른 값으로 나타난다."""
    part, ids = _three(client, admin)
    planned = _edit(client, member, part, ids=ids, field="status", value="deprecated").json()
    assert planned["counts"]["error"] == 3
    assert "고칠 수 없습니다" in planned["rows"][0]["message"]

    # 없는 id 를 섞어도 그 줄만 오류다.
    mixed = _edit(
        client,
        admin,
        part,
        ids=[*ids, "00000000-0000-0000-0000-000000000000"],
        field="status",
        value="deprecated",
    ).json()
    assert mixed["counts"]["change"] == 3 and mixed["counts"]["error"] == 1
    assert "찾을 수 없습니다" in mixed["rows"][-1]["message"]


def test_모르는_칸과_값은_막는다(client: TestClient, admin: Signed) -> None:
    part, ids = _three(client, admin)
    denied = _edit(client, admin, part, ids=ids, field="properties.nope", value="x")
    assert denied.status_code == 422

    bad = _edit(client, admin, part, ids=ids, field="properties.grade", value="Z").json()
    # 값이 정의에 안 맞으면 **행마다** 이유가 붙는다 — 전체가 422 로 죽지 않는다.
    assert bad["counts"]["error"] == 3
