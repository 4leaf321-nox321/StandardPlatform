"""부서 홈에 **지표**를 올린다 — 저장된 뷰와 한 줄에 선다.

홈 위젯은 저장된 뷰뿐이었다. 지표는 부서에 속하지 않으니(정의는 누구나 보고 값은 보이는 것만
더한다) 「어느 부서 홈의 몇 번째에」 를 따로 둔다. 자리는 뷰와 같은 줄에서 함께 매긴다 — 따로
매기면 뷰와 지표가 같은 자리 값을 가져 「위로」 가 안 움직인 것처럼 보인다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_home_widgets import _home, _type_with_grade, _view
from tests.api.test_metrics import _define, _sales_spec, _world


def _label(one: dict[str, Any]) -> str:
    return str(one["view"]["name"] if one["kind"] == "view" else one["metric"]["metric_label"])


def _pin(client: TestClient, who: Signed, slug: str, **body: Any) -> Any:
    return client.put(f"/api/metrics/{slug}/home", json=body, headers=who.headers)


def test_지표를_부서_홈에_올리면_뷰와_한_줄에_선다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    w = _world(client, admin)
    metric = _define(
        client,
        admin,
        source=w["sales"],
        spec=_sales_spec(),
        recompute=False,
        label="판매 대수",
    )
    part = _type_with_grade(client, admin)
    view = _view(client, manager, part, workspace_slug=manager.workspace, on_home=True)

    pinned = _pin(
        client, manager, metric["slug"], workspace_slug=manager.workspace, split="base_model"
    )
    assert pinned.status_code == 200, pinned.text
    home = _home(client, manager, manager.workspace)
    assert [(one["kind"], _label(one)) for one in home] == [
        ("view", "등급별"),
        ("metric", "판매 대수"),
    ]
    widget = home[1]["metric"]
    assert widget["metric_slug"] == metric["slug"] and widget["split"] == "base_model"

    # 지표를 맨 앞으로 — 뷰와 같은 줄에서 매긴다.
    moved = _pin(
        client,
        manager,
        metric["slug"],
        workspace_slug=manager.workspace,
        split="base_model",
        position=0,
    )
    assert moved.status_code == 200, moved.text
    assert [_label(one) for one in _home(client, manager, manager.workspace)] == [
        "판매 대수",
        "등급별",
    ]
    # 뷰 쪽에서 옮겨도 같은 줄이다.
    back = client.patch(
        f"/api/objects/{part}/views/{view['id']}",
        json={"home_position": 0},
        headers=manager.headers,
    )
    assert back.status_code == 200, back.text
    assert [_label(one) for one in _home(client, manager, manager.workspace)] == [
        "등급별",
        "판매 대수",
    ]

    where = client.get(f"/api/metrics/{metric['slug']}/home", headers=manager.headers).json()
    assert [(one["workspace_slug"], one["split"]) for one in where] == [
        (manager.workspace, "base_model")
    ]

    removed = client.delete(
        f"/api/metrics/{metric['slug']}/home",
        params={"workspace": manager.workspace},
        headers=manager.headers,
    )
    assert removed.status_code == 204, removed.text
    assert [_label(one) for one in _home(client, manager, manager.workspace)] == ["등급별"]


def test_부서_관리자만_올리고_없는_기준으로는_못_나눈다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["sales"], spec=_sales_spec(), recompute=False)

    denied = _pin(client, member, metric["slug"], workspace_slug=member.workspace)
    assert denied.status_code == 403, denied.text

    wrong = _pin(
        client, manager, metric["slug"], workspace_slug=manager.workspace, split="nope"
    )
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["error"]["code"].endswith("METRICS-0046")


def test_지표를_지우면_홈에서도_내려간다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["sales"], spec=_sales_spec(), recompute=False)
    assert (
        _pin(client, manager, metric["slug"], workspace_slug=manager.workspace).status_code
        == 200
    )
    gone = client.delete(f"/api/metrics/{metric['slug']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    assert _home(client, manager, manager.workspace) == []


def test_부서를_합치면_홈의_지표도_옮겨_간다(client: TestClient, admin: Signed) -> None:
    """통폐합의 「자료 이동」 에 홈의 지표가 없으면 원본 부서를 지울 때 그 홈의 지표가 말없이
    사라진다(외래키 CASCADE). 받는 쪽에 같은 지표가 이미 있으면 하나만 남는다."""
    import uuid

    w = _world(client, admin)
    metric = _define(
        client, admin, source=w["sales"], spec=_sales_spec(), recompute=False, label="판매"
    )
    other = _define(
        client, admin, source=w["sales"], spec=_sales_spec(), recompute=False, label="판매2"
    )
    tag = uuid.uuid4().hex[:6]
    spaces = []
    for name in (f"s{tag}", f"g{tag}"):
        made = client.post(
            "/api/workspaces", json={"slug": name, "name": name}, headers=admin.headers
        )
        assert made.status_code == 201, made.text
        spaces.append(name)
    source, target = spaces
    for slug, where in (
        (metric["slug"], source),
        (other["slug"], source),
        (other["slug"], target),
    ):
        assert _pin(client, admin, slug, workspace_slug=where).status_code == 200

    kinds = {
        one["kind"]: one["count"]
        for one in client.get(
            f"/api/workspaces/{source}/contents", headers=admin.headers
        ).json()
    }
    assert kinds["home_metrics"] == 2
    done = client.post(
        f"/api/workspaces/{source}/reassign",
        json={"target_slug": target, "kinds": ["home_metrics"]},
        headers=admin.headers,
    )
    assert done.status_code == 200, done.text
    pinned = [
        one["metric"]["metric_slug"]
        for one in _home(client, admin, target)
        if one["kind"] == "metric"
    ]
    assert sorted(pinned) == sorted([metric["slug"], other["slug"]])
