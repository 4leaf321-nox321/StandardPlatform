"""지켜보기 — **내가 보던 그것이 바뀌면 알려 준다.**

여기서 지키는 것 셋: 만든 사람은 자동으로 지켜보나, 바뀌면 알림이 오나, 그리고
**내가 한 일은 나에게 안 오나.** 마지막이 가장 중요하다 — 자기 행동을 돌려받으면
그 종은 곧 잡음이 되고, 잡음이 된 종은 진짜 하나가 울려도 안 읽힌다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, notifications_of
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


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


def test_알림이_사람의_말로_적힌다(client: TestClient, admin: Signed, manager: Signed) -> None:
    """**내부 키를 그대로 내보내면 알림이 하려던 일이 안 된다.**

    `properties.weight` 를 받은 사람은 그것이 화면의 어느 칸인지 모르고, 그러면 열어
    보는 수밖에 없다 — 열지 말지를 정하라고 보낸 알림인데.
    """
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    bolt = _make_object(client, admin, part, label="볼트", properties={"weight": 10})
    _watch(client, manager, part, bolt["id"], True)

    client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"label": "육각 볼트", "properties": {"weight": 12}},
        headers=admin.headers,
    )
    got = notifications_of(client, manager, "object.changed")[0]
    # 제목에 타입 slug 가 새면 안 된다 — 사람이 화면에서 본 적 없는 글자다.
    assert got["title"].startswith("육각 볼트")
    assert f"{part}:" not in got["title"]
    # 바뀐 칸은 정의에 적힌 이름으로.
    assert "이름" in got["body"] and "무게" in got["body"]
    assert "properties.weight" not in got["body"]
    # 어느 타입의 것인지도 한 줄에.
    assert "부품" in got["body"]


def test_종류_변경은_객체마다_알리지_않고_이력에만_남긴다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """정의 하나가 객체 수백만 개를 고치는 일이다 — 줄마다 알리면 아무도 못 읽고, 그 알림이
    커밋까지 메모리에 쌓였다(실측: 기록 200만 건 4.6GB). 이력에는 객체마다 남는다(ADR 0009)."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="text")
    bolt = _make_object(client, admin, part, label="볼트", properties={"qty": "3"})
    _watch(client, manager, part, bolt["id"], True)
    before = len(notifications_of(client, manager, "object.changed"))

    done = client.post(
        f"/api/ontology/types/{part}/properties/qty/retype",
        json={"data_type": "number", "apply": True},
        headers=admin.headers,
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    assert len(notifications_of(client, manager, "object.changed")) == before
    history = client.get(f"/api/objects/{part}/{bolt['id']}/history", headers=admin.headers)
    assert history.json()[0]["changes"]["properties.qty"] == {"before": "3", "after": 3}


# --- 관계 ---------------------------------------------------------------------------


def test_관계가_생기고_바뀌고_끊기면_두_끝을_지켜보는_사람에게_간다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """선의 기록은 객체가 아니라 선의 표(`object_relations`)에 남는다 — 객체의 일만 보던
    때는 「관계가 추가됐습니다」 가 목록에 있는데도 한 번도 안 갔다. 그 말은 어느 끝에서 읽어도
    맞으므로 두 끝 모두에 — 도착 끝에서는 역방향의 말로."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "part_of", label="속함", inverse_label="포함")
    motor = _make_object(client, admin, part, label="모터")
    drive = _make_object(client, admin, part, label="구동부")
    for one in (motor, drive):
        assert _watch(client, manager, part, one["id"], True).status_code == 200
    before = len(notifications_of(client, manager, "object.changed"))
    mine = len(notifications_of(client, admin, "object.changed"))

    made = _link(client, admin, part, motor["id"], kind, drive["id"])
    assert made.status_code == 201, made.text
    got = notifications_of(client, manager, "object.changed")
    assert len(got) == before + 2
    by_title = {one["title"]: one for one in got[:2]}
    assert set(by_title) == {"모터 — 관계가 추가됐습니다", "구동부 — 관계가 추가됐습니다"}
    assert "속함: 구동부" in by_title["모터 — 관계가 추가됐습니다"]["body"]
    assert "포함: 모터" in by_title["구동부 — 관계가 추가됐습니다"]["body"]
    assert by_title["모터 — 관계가 추가됐습니다"]["link"] == f"/o/{part}/{motor['id']}"

    edge = f"/api/objects/{part}/{motor['id']}/relations/{made.json()['relation_id']}"
    edited = client.patch(edge, json={"evidence_note": "BOM 기준"}, headers=admin.headers)
    assert edited.status_code == 200, edited.text
    got = notifications_of(client, manager, "object.changed")
    assert len(got) == before + 4
    assert {one["title"] for one in got[:2]} == {
        "모터 — 관계가 수정됐습니다",
        "구동부 — 관계가 수정됐습니다",
    }
    assert client.delete(edge, headers=admin.headers).status_code == 204
    got = notifications_of(client, manager, "object.changed")
    assert len(got) == before + 6
    assert {one["title"] for one in got[:2]} == {
        "모터 — 관계가 해제됐습니다",
        "구동부 — 관계가 해제됐습니다",
    }
    # 만든 사람(admin)도 두 끝을 지켜보지만 — 자기가 한 일이다.
    assert len(notifications_of(client, admin, "object.changed")) == mine


def test_객체를_지우며_끊은_선은_다른_끝에만_알린다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """지운 객체를 지켜보는 사람은 「삭제됐습니다」 한 통이면 된다 — 선마다 한 통씩 더 가면
    잡음이다. 다른 끝을 지켜보는 사람에게는 그 선이 끊어진 일이다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    gone = _make_object(client, admin, part, label="옛 부품")
    stays = _make_object(client, admin, part, label="남는 부품")
    assert _link(client, admin, part, gone["id"], kind, stays["id"]).status_code == 201
    for one in (gone, stays):
        _watch(client, manager, part, one["id"], True)
    before = len(notifications_of(client, manager, "object.changed"))

    deleted = client.delete(
        f"/api/objects/{part}/{gone['id']}", params={"mode": "detach"}, headers=admin.headers
    )
    assert deleted.status_code == 204, deleted.text
    got = notifications_of(client, manager, "object.changed")
    assert sorted(one["title"] for one in got[: len(got) - before]) == [
        "남는 부품 — 관계가 해제됐습니다",
        "옛 부품 — 삭제됐습니다",
    ]


def test_한_번에_이은_선_여럿은_한_통으로_묶는다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """일괄 입력 한 번이 허브 하나에 선 수천 개를 이으면 그 허브를 지켜보는 사람에게 수천 통이
    간다 — (사람 · 객체 · 일)마다 한 통에 수를 적는다."""
    import uuid

    from app.modules.objects import watches
    from app.shared import events

    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    hub = _make_object(client, admin, part, label="허브")
    leaves = [_make_object(client, admin, part, label=f"잎{n}") for n in range(3)]
    _watch(client, manager, part, hub["id"], True)
    before = len(notifications_of(client, manager, "object.changed"))

    watches.on_events(
        [
            events.ChangeEvent(
                action="object.relation.add",
                target_table="object_relations",
                target_id=uuid.uuid4(),
                target_label="",
                workspace_id=None,
                actor_id=None,
                actor_label="일괄 입력",
                actor_client=None,
                actor_token=None,
                changes={"relation": kind, "src": leaf["id"], "dst": hub["id"]},
            )
            for leaf in leaves
        ]
    )
    got = notifications_of(client, manager, "object.changed")
    assert len(got) == before + 1
    assert got[0]["title"] == "허브 — 관계가 추가됐습니다(3건)"
    assert "외 2건" in got[0]["body"]


def test_끝을_안_실은_선의_기록은_선에서_끝을_읽는다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """일괄 가져오기의 선 고침은 기록에 끝을 안 싣는다(`object_relations` 의 id 와 바뀐 칸뿐) —
    선은 아직 있으니 거기서 두 끝을 읽는다."""
    import uuid

    from app.modules.objects import watches
    from app.shared import events

    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    one = _make_object(client, admin, part, label="갑")
    two = _make_object(client, admin, part, label="을")
    made = _link(client, admin, part, one["id"], kind, two["id"])
    assert made.status_code == 201, made.text
    _watch(client, manager, part, two["id"], True)
    before = len(notifications_of(client, manager, "object.changed"))

    watches.on_events(
        [
            events.ChangeEvent(
                action="object.relation.update",
                target_table="object_relations",
                target_id=uuid.UUID(made.json()["relation_id"]),
                target_label="갑 씀 을",
                workspace_id=None,
                actor_id=None,
                actor_label="일괄 가져오기",
                actor_client=None,
                actor_token=None,
                changes={"properties": {"before": {}, "after": {"n": 1}}},
            )
        ]
    )
    got = notifications_of(client, manager, "object.changed")
    assert len(got) == before + 1
    assert got[0]["title"] == "을 — 관계가 수정됐습니다"
    assert "씀의 반대: 갑" in got[0]["body"], got[0]


def test_파일대로_맞춤이_끊은_선도_두_끝을_지켜보는_사람에게_간다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """묶음의 「파일대로 맞춤」 은 파일에 없는 선을 끊고 곧바로 지운다 — 그 기록에 끝이 없으면
    선을 되읽을 데가 없어 알림이 안 갔다(이력에서도 그 줄을 못 찾았다)."""
    import uuid

    from tests.api.conftest import bundle_import

    # 식별자는 시험마다 새로 — 끝 타입을 정하지 않은 관계라, 같은 DB 의 다른 시험이
    # 만든 「A」 와 겹치면 끝이 모호해진다(병렬 시험에서 실제로 그랬다).
    tag = uuid.uuid4().hex[:6]
    a, b, c = f"A{tag}", f"B{tag}", f"C{tag}"
    part = _make_type(client, admin, label="부품", key_policy="optional")
    kind = _make_relation(client, admin, "uses", label="씀")
    one = _make_object(client, admin, part, label="갑", key=a)
    two = _make_object(client, admin, part, label="을", key=b)
    _make_object(client, admin, part, label="병", key=c)
    made = _link(client, admin, part, one["id"], kind, two["id"])
    assert made.status_code == 201, made.text
    _watch(client, manager, part, two["id"], True)
    before = len(notifications_of(client, manager, "object.changed"))

    replaced = bundle_import(
        client,
        admin,
        {
            "relations": [
                {
                    "type_slug": part,
                    "mode": "replace",
                    "rows": [{"src": a, "relation": kind, "dst": c}],
                }
            ],
            "apply": True,
        },
    )
    assert replaced["applied"] is True, replaced
    got = notifications_of(client, manager, "object.changed")
    assert len(got) == before + 1, got
    assert got[0]["title"] == "을 — 관계가 해제됐습니다"
    assert "갑" in got[0]["body"], got[0]
    history = client.get(f"/api/objects/{part}/{two['id']}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    assert "파일대로 맞춤" in history.text, history.text
