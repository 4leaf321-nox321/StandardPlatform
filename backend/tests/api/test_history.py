"""객체의 이력 — **그 시점 값이 맞게 재구성되나, 되돌리기가 검증을 거치나.**"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed, notifications_of
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _history(
    client: TestClient, who: Signed, type_slug: str, object_id: str
) -> list[dict[str, Any]]:
    response = client.get(f"/api/objects/{type_slug}/{object_id}/history", headers=who.headers)
    assert response.status_code == 200, response.text
    return list(response.json())


def _patch(
    client: TestClient, who: Signed, type_slug: str, object_id: str, **body: Any
) -> None:
    response = client.patch(
        f"/api/objects/{type_slug}/{object_id}", json=body, headers=who.headers
    )
    assert response.status_code == 200, response.text


def _part(client: TestClient, admin: Signed) -> str:
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(
        client,
        admin,
        part,
        key="material",
        label="재질",
        data_type="enum",
        enum_options=["스틸", "알루미늄"],
    )
    return part


def test_이력은_그_시점의_값_전체를_재구성한다(client: TestClient, admin: Signed) -> None:
    """기록은 바뀐 것만 남기지만, 지금 값에서 거꾸로 대면 각 시점의 전체가 나온다."""
    part = _part(client, admin)
    bolt = _make_object(
        client,
        admin,
        part,
        key="P-1",
        label="볼트",
        properties={"weight": 1, "material": "스틸"},
    )
    _patch(client, admin, part, bolt["id"], properties={"weight": 1.2})
    _patch(client, admin, part, bolt["id"], properties={"weight": 0.8, "material": "알루미늄"})

    entries = _history(client, admin, part, bolt["id"])
    assert [one["action"] for one in entries] == [
        "object.update",
        "object.update",
        "object.create",
    ]
    latest, middle, first = entries
    # 최근 기록의 뒤 = 지금 값.
    assert latest["snapshot"]["properties"] == {"weight": 0.8, "material": "알루미늄"}
    # 그 앞 기록의 뒤 = 두 번째 저장 직후.
    assert middle["snapshot"]["properties"] == {"weight": 1.2, "material": "스틸"}
    # 만들 때.
    assert first["snapshot"]["properties"] == {"weight": 1, "material": "스틸"}
    # 칸별로 풀린 변경.
    assert latest["changes"]["properties.weight"] == {"before": 1.2, "after": 0.8}
    assert latest["changes"]["properties.material"] == {"before": "스틸", "after": "알루미늄"}
    assert "properties.material" not in middle["changes"]


def test_관계가_걸리고_끊긴_것도_이력에_선다(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    kind = _make_relation(client, admin, "near", label="가까움", inverse_label="가까움")
    a = _make_object(client, admin, part, key="P-1", label="A")
    b = _make_object(client, admin, part, key="P-2", label="B")
    made = _link(client, admin, part, a["id"], kind, b["id"]).json()
    cut = client.delete(
        f"/api/objects/{part}/{a['id']}/relations/{made['relation_id']}", headers=admin.headers
    )
    assert cut.status_code == 204

    # 출발점(A)에서도, 도착점(B)에서도 보인다 — 방향이 맞게.
    for who, outgoing, other in ((a, True, "B"), (b, False, "A")):
        entries = [
            one
            for one in _history(client, admin, part, who["id"])
            if one["kind"] == "relation"
        ]
        assert [one["action"] for one in entries] == [
            "object.relation.remove",
            "object.relation.add",
        ]
        assert entries[1]["relation"]["outgoing"] is outgoing
        assert entries[1]["relation"]["other_label"] == other
        assert entries[1]["snapshot"] is None


def test_되돌리면_그_시점_값이_되고_기록이_남는다(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    bolt = _make_object(
        client,
        admin,
        part,
        key="P-1",
        label="볼트",
        properties={"weight": 1, "material": "스틸"},
    )
    _patch(
        client,
        admin,
        part,
        bolt["id"],
        label="볼트(수정)",
        properties={"weight": 0.8, "material": "알루미늄"},
    )
    first = _history(client, admin, part, bolt["id"])[-1]

    restored = client.post(
        f"/api/objects/{part}/{bolt['id']}/restore",
        json={"entry_id": first["id"]},
        headers=admin.headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["label"] == "볼트"
    assert restored.json()["properties"] == {"weight": 1, "material": "스틸"}

    top = _history(client, admin, part, bolt["id"])[0]
    assert top["action"] == "object.update"
    assert "되돌림" in top["reason"]
    assert top["changes"]["label"] == {"before": "볼트(수정)", "after": "볼트"}


def test_되돌리기는_저장과_같은_검증을_거친다(client: TestClient, admin: Signed) -> None:
    """그때 가리키던 것이 지금은 지워졌으면 막고 이유를 말한다."""
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
    acme = _make_object(client, admin, vendor, label="ACME")
    bolt = _make_object(
        client, admin, part, key="P-1", label="볼트", properties={"vendor": acme["id"]}
    )
    # 참조를 끊고 ACME 를 지운다.
    _patch(client, admin, part, bolt["id"], properties={"vendor": None})
    assert (
        client.delete(f"/api/objects/{vendor}/{acme['id']}", headers=admin.headers).status_code
        == 204
    )

    first = _history(client, admin, part, bolt["id"])[-1]
    assert first["snapshot"]["properties"] == {"vendor": acme["id"]}
    blocked = client.post(
        f"/api/objects/{part}/{bolt['id']}/restore",
        json={"entry_id": first["id"]},
        headers=admin.headers,
    )
    assert blocked.status_code == 422
    assert "찾을 수 없" in blocked.json()["error"]["message"]
    # 안 바뀌었다.
    now = client.get(f"/api/objects/{part}/{bolt['id']}", headers=admin.headers).json()
    assert now["object"]["properties"] == {}


def test_지금은_없는_속성은_빼고_되돌린다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(client, admin, part, key="color", label="색", data_type="text")
    bolt = _make_object(
        client, admin, part, key="P-1", label="볼트", properties={"weight": 1, "color": "빨강"}
    )
    _patch(client, admin, part, bolt["id"], properties={"weight": 2, "color": None})
    gone = client.delete(f"/api/ontology/types/{part}/properties/color", headers=admin.headers)
    assert gone.status_code == 204, gone.text

    first = _history(client, admin, part, bolt["id"])[-1]
    restored = client.post(
        f"/api/objects/{part}/{bolt['id']}/restore",
        json={"entry_id": first["id"]},
        headers=admin.headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["properties"] == {"weight": 1}
    assert "color" in _history(client, admin, part, bolt["id"])[0]["reason"]


def test_이력은_볼_수_있는_사람이면_누구나_되돌리기는_고칠_수_있는_사람만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part = _part(client, admin)
    bolt = _make_object(
        client, admin, part, key="P-1", label="볼트", workspace_slug=member.workspace
    )
    entries = _history(client, member, part, bolt["id"])
    assert len(entries) == 1
    denied = client.post(
        f"/api/objects/{part}/{bolt['id']}/restore",
        json={"entry_id": entries[0]["id"]},
        headers=member.headers,
    )
    assert denied.status_code == 403


def test_한_트랜잭션의_기록_여럿도_넣은_차례대로_되짚는다(
    client: TestClient, admin: Signed
) -> None:
    """created_at 은 트랜잭션 시작 시각이라 같은 값이 여럿 — 순서가 무작위면 시점 값이 틀린다.
    고를 값 이름 바꾸기가 객체 셋을 한 트랜잭션에 고친다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="material",
        label="재질",
        data_type="enum",
        enum_options=["스틸", "고무"],
    )
    rows = [
        _make_object(
            client,
            admin,
            part,
            key=f"P-{i}",
            label=f"부품{i}",
            properties={"material": "스틸"},
        )
        for i in range(3)
    ]
    for _ in range(3):
        client.post(
            f"/api/ontology/types/{part}/properties/material/rename-option",
            json={"from": "스틸", "to": "강", "apply": True},
            headers=admin.headers,
        )
        client.post(
            f"/api/ontology/types/{part}/properties/material/rename-option",
            json={"from": "강", "to": "스틸", "apply": True},
            headers=admin.headers,
        )
    for row in rows:
        entries = _history(client, admin, part, row["id"])
        values = [one["snapshot"]["properties"]["material"] for one in entries]
        # 최근 → 과거: 스틸, 강, 스틸, 강, 스틸, 강, 스틸(만듦)
        assert values == ["스틸", "강", "스틸", "강", "스틸", "강", "스틸"]


# --- 설명·연도·소유 부서 -----------------------------------------------------------
#
# 고치는 길마다 비교할 칸을 따로 적었더니 이 셋이 빠져, 바꾼 기록이 「고침」 만 남고 비어
# 있었다. 누가 설명을 바꿨는지 답할 수 없고, 알림에 칸 이름이 없고, 되돌릴 수도 없었다.


def _memo(client: TestClient, admin: Signed) -> tuple[str, str, str]:
    memo = _make_type(client, admin, label="메모")
    label = f"메모-{uuid.uuid4().hex[:6]}"
    made = _make_object(client, admin, memo, label=label, description="처음")
    return memo, made["id"], label


def test_설명과_연도를_고친_기록에_칸이_남고_되돌리면_돌아온다(
    client: TestClient, admin: Signed
) -> None:
    memo, object_id, _ = _memo(client, admin)
    _patch(client, admin, memo, object_id, description="고침", valid_from_year=2020)

    history = _history(client, admin, memo, object_id)
    latest = history[0]
    assert latest["changes"]["description"] == {"before": "처음", "after": "고침"}
    assert latest["changes"]["valid_from_year"] == {"before": None, "after": 2020}

    created = history[-1]
    assert created["snapshot"]["description"] == "처음"
    assert created["snapshot"]["valid_from_year"] is None

    restored = client.post(
        f"/api/objects/{memo}/{object_id}/restore",
        json={"entry_id": created["id"]},
        headers=admin.headers,
    )
    assert restored.status_code == 200, restored.text
    got = client.get(f"/api/objects/{memo}/{object_id}", headers=admin.headers).json()[
        "object"
    ]
    assert got["description"] == "처음" and got["valid_from_year"] is None
    # 되돌린 것도 칸이 남는 기록이다.
    assert _history(client, admin, memo, object_id)[0]["changes"]["description"] == {
        "before": "고침",
        "after": "처음",
    }


def test_여럿_고치기로_소유_부서를_바꾼_기록에_부서_이름이_남는다(
    client: TestClient, admin: Signed
) -> None:
    """기록은 id 로 남기고 보여 줄 때 이름으로 — 부서 이름은 바뀐다."""
    memo, object_id, _ = _memo(client, admin)
    done = client.post(
        f"/api/objects/{memo}/bulk-edit",
        json={"ids": [object_id], "field": "workspace", "value": "", "apply": True},
        headers=admin.headers,
    )
    assert done.status_code == 200 and done.json()["counts"]["change"] == 1, done.text

    change = _history(client, admin, memo, object_id)[0]["changes"]["owner_workspace_id"]
    assert change["after"] == "(전역)"
    assert change["before"] not in ("", None, "(지워진 부서)")
    with pytest.raises(ValueError):
        uuid.UUID(change["before"])


def test_설명을_고치면_지켜보는_사람의_알림에_칸_이름이_간다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    memo, object_id, label = _memo(client, admin)
    client.put(
        f"/api/objects/{memo}/{object_id}/watch", json={"on": True}, headers=manager.headers
    )
    _patch(client, admin, memo, object_id, description="고침")

    mine = [
        one
        for one in notifications_of(client, manager, "object.changed")
        if one["title"].startswith(label)
    ]
    assert mine and "설명" in mine[0]["body"]
