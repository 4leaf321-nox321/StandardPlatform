"""부서 홈 위젯 — **저장된 뷰를 홈에 올린다.**

새 개념을 만들지 않는다. 뷰에 「통계」 설정을 함께 담고, 그 뷰를 부서 홈에
올리는 것뿐이다 — 조건과 기준은 같은 물음의 두 쪽이라 따로 두면 매번 다시 고르게 된다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _type_with_grade(client: TestClient, admin: Signed) -> str:
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
    _make_object(client, admin, part, label="볼트", properties={"grade": "A"})
    return part


def _view(client: TestClient, who: Signed, part: str, **body: Any) -> dict[str, Any]:
    made = client.post(
        f"/api/objects/{part}/views",
        json={"name": "등급별", "query": {"q": "", "conditions": []}, **body},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def _home(client: TestClient, who: Signed, workspace: str) -> list[dict[str, Any]]:
    got = client.get(f"/api/objects/home?workspace={workspace}", headers=who.headers)
    assert got.status_code == 200, got.text
    return list(got.json())


def test_뷰가_묶어_보기_설정을_담고_홈에_오른다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    part = _type_with_grade(client, admin)
    view = _view(
        client,
        manager,
        part,
        workspace_slug=manager.workspace,
        summary={"group_by": "properties.grade", "metric": "count", "chart": "pie"},
    )
    assert view["summary"]["group_by"] == "properties.grade"
    assert view["summary"]["chart"] == "pie"
    assert view["home_order"] is None

    pinned = client.patch(
        f"/api/objects/{part}/views/{view['id']}",
        json={"on_home": True},
        headers=manager.headers,
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["home_order"] == 0

    widgets = _home(client, manager, manager.workspace)
    assert [one["view"]["name"] for one in widgets] == ["등급별"]
    # 홈은 타입을 모른다 — 그려야 할 것을 여기서 다 실어 준다.
    assert widgets[0]["type_label"] == "부품"
    assert widgets[0]["view"]["type_slug"] == part

    # 내리면 홈에서 빠지되 뷰는 남는다 — 거르기까지 잃을 이유가 없다.
    client.patch(
        f"/api/objects/{part}/views/{view['id']}",
        json={"on_home": False},
        headers=manager.headers,
    )
    assert _home(client, manager, manager.workspace) == []
    assert client.get(f"/api/objects/{part}/views", headers=manager.headers).json()


def test_저장과_올리기가_한_번에_된다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """화면에서 「홈에 올리기」 는 한 동작이다. 요청 둘로 나누면 저장은 됐는데 안 올라간
    상태가 생기고, 그때 사람은 자기가 무엇을 빠뜨렸는지 모른다."""
    part = _type_with_grade(client, admin)
    view = _view(
        client,
        manager,
        part,
        workspace_slug=manager.workspace,
        summary={"group_by": "properties.grade", "metric": "count", "chart": "bar"},
        on_home=True,
    )
    assert view["home_order"] == 0
    assert [one["view"]["id"] for one in _home(client, manager, manager.workspace)] == [
        view["id"]
    ]


def test_개인_뷰는_부서_홈에_못_올린다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """같은 화면을 보는 사람마다 다른 것이 뜨면 「내 홈에는 왜 그게 없지」 를 아무도
    설명하지 못한다."""
    part = _type_with_grade(client, admin)
    mine = _view(client, manager, part)  # workspace_slug 없음 = 내 것
    denied = client.patch(
        f"/api/objects/{part}/views/{mine['id']}",
        json={"on_home": True},
        headers=manager.headers,
    )
    assert denied.status_code == 409
    assert "부서 뷰로 저장" in denied.json()["error"]["message"]


def test_멤버는_홈을_보되_못_올린다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    part = _type_with_grade(client, admin)
    view = _view(client, manager, part, workspace_slug=manager.workspace)
    client.patch(
        f"/api/objects/{part}/views/{view['id']}",
        json={"on_home": True},
        headers=manager.headers,
    )
    # 부서 사람은 본다 — 부서가 함께 보는 자리다.
    assert len(_home(client, member, manager.workspace)) == 1

    # 멤버는 부서 뷰 자체를 못 만든다 — 아무나 부서 뷰를 만들면 목록이 곧 개인 취향으로
    # 가득 찬다. 그러니 홈에 올릴 것도 없다.
    denied = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "내가 올리는 것",
            "query": {"q": "", "conditions": []},
            "workspace_slug": member.workspace,
        },
        headers=member.headers,
    )
    assert denied.status_code == 403


def test_못_쓰는_기준은_저장에서_막는다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """**저장할 때 검사한다.** 안 하면 열었을 때 그림만 안 뜨고, 무엇이 잘못됐는지
    말할 자리가 없다."""
    part = _make_type(client, admin, label="문서")
    _make_property(client, admin, part, key="body", label="본문", data_type="text_long")
    bad = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "안 되는 것",
            "query": {"q": "", "conditions": []},
            "summary": {"group_by": "properties.body", "metric": "count"},
        },
        headers=manager.headers,
    )
    assert bad.status_code == 422
    assert "그룹이 행 수만큼" in bad.json()["error"]["message"]

    bad_chart = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "안 되는 그림",
            "query": {"q": "", "conditions": []},
            "summary": {"group_by": "status", "chart": "도넛"},
        },
        headers=manager.headers,
    )
    assert bad_chart.status_code == 409


def test_남의_부서_홈은_빈_목록이다(client: TestClient, admin: Signed, member: Signed) -> None:
    other = client.post(
        "/api/workspaces",
        json={"slug": "home-other", "name": "남의팀"},
        headers=admin.headers,
    )
    slug = other.json()["slug"] if other.status_code == 201 else "home-other"
    assert _home(client, member, slug) == []


def test_홈에서_순서를_바꾸고_내린다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """내리는 자리는 **홈**이다 — 치우고 싶은 순간은 홈을 보다가 온다."""
    part = _type_with_grade(client, admin)
    first = _view(
        client, manager, part, name="첫째", workspace_slug=manager.workspace, on_home=True
    )
    second = _view(
        client, manager, part, name="둘째", workspace_slug=manager.workspace, on_home=True
    )
    assert [one["view"]["name"] for one in _home(client, manager, manager.workspace)] == [
        "첫째",
        "둘째",
    ]

    moved = client.patch(
        f"/api/objects/{part}/views/{second['id']}",
        json={"home_position": 0},
        headers=manager.headers,
    )
    assert moved.status_code == 200, moved.text
    assert [one["view"]["name"] for one in _home(client, manager, manager.workspace)] == [
        "둘째",
        "첫째",
    ]

    client.patch(
        f"/api/objects/{part}/views/{second['id']}",
        json={"on_home": False},
        headers=manager.headers,
    )
    left = _home(client, manager, manager.workspace)
    assert [one["view"]["name"] for one in left] == ["첫째"]
    # 내려도 뷰는 남는다 — 거르기까지 잃을 이유가 없다.
    names = [
        one["name"]
        for one in client.get(f"/api/objects/{part}/views", headers=manager.headers).json()
    ]
    assert "둘째" in names

    # 홈에 없는 것은 자리를 못 옮긴다 — 무엇을 옮기는지가 없다.
    denied = client.patch(
        f"/api/objects/{part}/views/{second['id']}",
        json={"home_position": 0},
        headers=manager.headers,
    )
    assert denied.status_code == 409
    assert first["home_order"] == 0


def test_부서를_안_주면_내_부서_전부(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """사람은 대개 여러 부서에 속한다 — 부서를 갈아 가며 도는 일이 없어야 한다."""
    part = _type_with_grade(client, admin)
    other = client.post(
        "/api/workspaces",
        json={"slug": "home-second", "name": "둘째팀"},
        headers=admin.headers,
    )
    slug = other.json()["slug"] if other.status_code == 201 else "home-second"

    _view(
        client,
        manager,
        part,
        name="내 부서 것",
        workspace_slug=manager.workspace,
        on_home=True,
    )
    _view(client, admin, part, name="둘째팀 것", workspace_slug=slug, on_home=True)

    got = client.get("/api/objects/home", headers=admin.headers)
    assert got.status_code == 200, got.text
    names = {one["view"]["name"] for one in got.json()}
    assert {"내 부서 것", "둘째팀 것"} <= names
    # **어디 것인지 적는다** — 안 적으면 같은 이름의 위젯 둘이 나란히 서고 구별이 안 된다.
    for one in got.json():
        assert one["workspace_slug"] and one["workspace_name"]


def test_지켜보는_것을_한자리에_모아_본다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    """알림은 읽고 나면 사라진다 — 모아 볼 곳이 없으면 지켜보기는 그때만 떠오른다."""
    part = _type_with_grade(client, admin)
    watched = _make_object(client, admin, part, label="지켜볼 것")
    _make_object(client, admin, part, label="안 지켜볼 것")
    client.put(
        f"/api/objects/{part}/{watched['id']}/watch",
        json={"on": True},
        headers=manager.headers,
    )

    got = client.get("/api/objects/watching", headers=manager.headers)
    assert got.status_code == 200, got.text
    rows = got.json()
    assert [one["label"] for one in rows] == ["지켜볼 것"]
    assert rows[0]["type_label"] == "부품"

    # 지켜보지도 만들지도 않은 사람에게는 안 보인다.
    other = client.get("/api/objects/watching", headers=member.headers).json()
    assert all(one["id"] != watched["id"] for one in other)

    # 만든 사람(admin)은 **자동으로 지켜보므로** 둘 다 보인다.
    made = client.get("/api/objects/watching", headers=admin.headers).json()
    assert {watched["id"]} <= {one["id"] for one in made}
