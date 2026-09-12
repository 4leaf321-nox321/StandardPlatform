"""지켜보기 — **내가 보던 그것이 바뀌면 알려 준다.**

여기서 지키는 것 셋: 만든 사람은 자동으로 지켜보나, 바뀌면 알림이 오나, 그리고
**내가 한 일은 나에게 안 오나.** 마지막이 가장 중요하다 — 자기 행동을 돌려받으면
그 종은 곧 잡음이 되고, 잡음이 된 종은 진짜 하나가 울려도 안 읽힌다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, notifications_of
from tests.api.test_ontology import _make_object, _make_type


def _profile(client: TestClient, who: Signed, slug: str, object_id: str) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _watch(client: TestClient, who: Signed, slug: str, object_id: str, on: bool) -> Any:
    return client.put(
        f"/api/objects/{slug}/{object_id}/watch", json={"on": on}, headers=who.headers
    )


def test_만든_사람은_자동으로_지켜본다(client: TestClient, admin: Signed) -> None:
    """스스로 켜야만 하는 기능은 켜는 법을 아는 사람만 쓴다."""
    part = _make_type(client, admin, label="부품")
    bolt = _make_object(client, admin, part, label="볼트")
    found = _profile(client, admin, part, bolt["id"])
    assert found["watching"] is True
    assert found["watcher_count"] == 1


def test_바뀌면_지켜보는_사람에게_알림이_온다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    bolt = _make_object(client, admin, part, label="볼트")

    assert _watch(client, manager, part, bolt["id"], True).json()["watching"] is True
    before = len(notifications_of(client, manager, "object.changed"))

    edited = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"label": "육각 볼트"},
        headers=admin.headers,
    )
    assert edited.status_code == 200, edited.text

    after = notifications_of(client, manager, "object.changed")
    assert len(after) == before + 1
    assert "육각 볼트" in after[0]["title"] or "볼트" in after[0]["title"]
    assert after[0]["link"] == f"/o/{part}/{bolt['id']}"


def test_내가_한_일은_나에게_안_온다(client: TestClient, admin: Signed) -> None:
    """**이것이 이 기능의 값을 지킨다.** 자기 행동을 알림으로 돌려받으면 그 종은 곧
    잡음이 되고, 그때 진짜 하나가 울려도 안 읽힌다."""
    part = _make_type(client, admin, label="부품")
    bolt = _make_object(client, admin, part, label="너트")  # 만든 사람 = admin, 자동 지켜보기
    before = len(notifications_of(client, admin, "object.changed"))

    client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"description": "내가 고침"},
        headers=admin.headers,
    )
    assert len(notifications_of(client, admin, "object.changed")) == before


def test_그만두면_안_온다(client: TestClient, admin: Signed, manager: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    bolt = _make_object(client, admin, part, label="와셔")
    _watch(client, manager, part, bolt["id"], True)
    assert _watch(client, manager, part, bolt["id"], False).json()["watching"] is False
    before = len(notifications_of(client, manager, "object.changed"))

    client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"description": "그만둔 뒤"},
        headers=admin.headers,
    )
    assert len(notifications_of(client, manager, "object.changed")) == before

    # 두 번 눌러도 같은 결과 — 두 번 켠 사람이 두 통을 받지 않는다.
    _watch(client, manager, part, bolt["id"], True)
    _watch(client, manager, part, bolt["id"], True)
    assert _profile(client, admin, part, bolt["id"])["watcher_count"] == 2


def test_투영_타입은_여기서_안_지켜본다(client: TestClient, admin: Signed) -> None:
    projected = _make_type(
        client, admin, label="부서(투영)", kind_class="system", system_source="workspace"
    )
    listed = client.get(f"/api/objects/{projected}", headers=admin.headers).json()
    if not listed["items"]:
        return
    denied = _watch(client, admin, projected, listed["items"][0]["id"], True)
    assert denied.status_code == 409
