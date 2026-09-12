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


# --- 묶음으로 되돌리기 -------------------------------------------------------------


def _undo(client: TestClient, who: Signed, slug: str, batch: str, apply: bool = False) -> Any:
    return client.post(
        f"/api/objects/{slug}/bulk-edit/{batch}/undo",
        json={"apply": apply},
        headers=who.headers,
    )


def test_같이_바뀐_것을_한_번에_되돌린다(client: TestClient, admin: Signed) -> None:
    """한 번에 수백 건을 바꾸는 길이 있는데 되돌리는 길이 한 건씩뿐이면, 실수 한 번은
    사실상 되돌릴 수 없다."""
    part, ids = _three(client, admin)
    done = _edit(
        client, admin, part, ids=ids, field="properties.grade", value="B", apply=True
    ).json()
    batch = done["batch_id"]
    assert batch

    planned = _undo(client, admin, part, batch)
    assert planned.status_code == 200, planned.text
    body = planned.json()
    # **계획은 아무것도 안 바꾼다.**
    assert body["applied"] is False and body["counts"]["change"] == 3
    assert body["rows"][0]["before"] == "B" and body["rows"][0]["after"] == "A"
    got = client.get(f"/api/objects/{part}/{ids[0]}", headers=admin.headers).json()
    assert got["object"]["properties"]["grade"] == "B"

    applied = _undo(client, admin, part, batch, apply=True).json()
    assert applied["applied"] is True and applied["batch_id"]
    for object_id in ids:
        got = client.get(f"/api/objects/{part}/{object_id}", headers=admin.headers).json()
        assert got["object"]["properties"]["grade"] == "A"

    # 두 번 눌러도 같은 결과다 — 이미 그때 값이면 「그대로」.
    again = _undo(client, admin, part, batch).json()
    assert again["counts"]["unchanged"] == 3 and again["counts"]["change"] == 0

    # 되돌리기도 묶음으로 남는다 — 그것을 다시 되돌리면 B 로 간다.
    redo = _undo(client, admin, part, applied["batch_id"], apply=True).json()
    assert redo["counts"]["change"] == 3
    got = client.get(f"/api/objects/{part}/{ids[1]}", headers=admin.headers).json()
    assert got["object"]["properties"]["grade"] == "B"


def test_그_뒤에_누가_고친_행은_덮어쓰지_않는다(client: TestClient, admin: Signed) -> None:
    """**설명은 기록의 diff 에 안 잡힌다** — 묶음 표식이 그때 값을 따로 들고 있어야 한다."""
    part, ids = _three(client, admin)
    done = _edit(
        client, admin, part, ids=ids, field="description", value="일괄 메모", apply=True
    ).json()
    client.patch(
        f"/api/objects/{part}/{ids[0]}",
        json={"description": "누가 새로 적음"},
        headers=admin.headers,
    )

    applied = _undo(client, admin, part, done["batch_id"], apply=True).json()
    assert applied["counts"]["change"] == 2 and applied["counts"]["error"] == 1
    skipped = next(one for one in applied["rows"] if one["id"] == ids[0])
    assert "다시 바뀌었습니다" in skipped["message"]

    kept = client.get(f"/api/objects/{part}/{ids[0]}", headers=admin.headers).json()
    assert kept["object"]["description"] == "누가 새로 적음"
    back = client.get(f"/api/objects/{part}/{ids[1]}", headers=admin.headers).json()
    assert back["object"]["description"] in ("", None)


def test_이력에_묶음이_실리고_표식은_칸으로_안_보인다(
    client: TestClient, admin: Signed
) -> None:
    part, ids = _three(client, admin)
    done = _edit(
        client, admin, part, ids=ids, field="status", value="deprecated", apply=True
    ).json()
    history = client.get(f"/api/objects/{part}/{ids[0]}/history", headers=admin.headers).json()
    entry = next(one for one in history if one["batch"])
    assert entry["batch"] == {"id": done["batch_id"], "field_label": "상태", "size": 3}
    assert all(not key.startswith("_") for key in entry["changes"])


def test_남의_부서_것은_되돌리지_않고_없는_묶음은_404(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part, ids = _three(client, admin)
    done = _edit(
        client, admin, part, ids=ids, field="status", value="deprecated", apply=True
    ).json()
    denied = _undo(client, member, part, done["batch_id"]).json()
    assert denied["counts"]["change"] == 0 and denied["counts"]["error"] == 3

    missing = _undo(client, admin, part, "00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
