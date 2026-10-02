"""큰 참조 대상 — **통째로 안 읽고 넣을 값만 묻는다**, 판정은 같다(ADR 0009 · 0010).

기록이 기록(200만 건)을 가리키면 그 식별자 · 이름을 다 읽어 메모리에 들게 된다. 대상이
`BIG_TARGET` 을 넘으면 넣을 값만 덩어리째 묻는다 — 그래도 식별자 → 별칭 → 이름 → id 차례,
이름이 여럿이면 거절, 구현 타입 여럿의 같은 식별자는 거절이 그대로여야 한다. 여기서는 문턱을
2건으로 낮춰 작은 타입으로 그 길을 탄다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.objects import bulk
from tests.api.conftest import Signed
from tests.api.test_interfaces import _make_interface
from tests.api.test_ontology import _make_object, _make_property, _make_type
from tests.api.test_retype import _retype, _value


@pytest.fixture(autouse=True)
def _small_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bulk, "BIG_TARGET", 2)


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:6]
    test = _make_type(client, admin, label="시험 건", key_policy="optional", usage="log")
    made = {
        "key": _make_object(client, admin, test, key=f"T{tag}-1", label=f"시험 {tag} 1"),
        "label": _make_object(client, admin, test, label=f"이름만 {tag}"),
        "alias": _make_object(client, admin, test, label=f"별칭 대상 {tag}"),
        "twin1": _make_object(client, admin, test, label=f"쌍둥이 {tag}"),
        "twin2": _make_object(client, admin, test, label=f"쌍둥이 {tag}"),
    }
    added = client.put(
        f"/api/objects/{test}/{made['alias']['id']}/aliases",
        json={"aliases": [f"다른이름{tag}"]},
        headers=admin.headers,
    )
    assert added.status_code == 200, added.text
    result = _make_type(client, admin, label="시험 결과", usage="log")
    _make_property(
        client,
        admin,
        result,
        key="test",
        label="시험",
        data_type="object_ref",
        ref_type_slug=test,
    )
    return {
        "tag": tag,
        "test": test,
        "result": result,
        **{k: v["id"] for k, v in made.items()},
    }


def _plan(client: TestClient, admin: Signed, slug: str, rows: list[dict[str, Any]]) -> Any:
    got = client.post(
        f"/api/objects/{slug}/import-rows",
        json={"rows": rows, "workspace_slug": admin.workspace, "apply": False},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return got.json()


def test_일괄_입력은_넣을_값만_묻고도_같은_규칙으로_푼다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    tag = w["tag"]
    rows = [
        {"label": "결과 1", "test": f"T{tag}-1"},
        {"label": "결과 2", "test": f"이름만 {tag}"},
        {"label": "결과 3", "test": f"다른이름{tag}"},
        {"label": "결과 4", "test": w["twin1"]},
        {"label": "결과 5", "test": f"쌍둥이 {tag}"},
        {"label": "결과 6", "test": f"없는 {tag}"},
    ]
    plan = _plan(client, admin, w["result"], rows)
    actions = [one["action"] for one in plan["rows"]]
    assert actions == ["create", "create", "create", "create", "error", "error"], plan
    assert "2개에 맞습니다" in plan["rows"][4]["message"]
    assert "찾을 수 없습니다" in plan["rows"][5]["message"]

    done = client.post(
        f"/api/objects/{w['result']}/import-rows",
        json={"rows": rows[:4], "workspace_slug": admin.workspace, "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    items = client.get(f"/api/objects/{w['result']}", headers=admin.headers).json()["items"]
    pointed = {one["label"]: one["properties"]["test"] for one in items}
    assert pointed == {
        "결과 1": w["key"],
        "결과 2": w["label"],
        "결과 3": w["alias"],
        "결과 4": w["twin1"],
    }


def test_인터페이스_대상의_같은_식별자는_큰_대상이어도_거절한다(
    client: TestClient, admin: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    iface = _make_interface(client, admin, label="설비")
    one = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    two = _make_type(
        client, admin, label="계측기", key_policy="optional", interface_slugs=[iface]
    )
    for kind in (one, two):
        for n in range(3):
            _make_object(client, admin, kind, key=f"E{tag}-{n}", label=f"{kind} {n}")
    _make_object(client, admin, one, key=f"E{tag}-only", label=f"하나뿐 {tag}")
    order = _make_type(client, admin, label="작업지시")
    _make_property(
        client,
        admin,
        order,
        key="eq",
        label="설비",
        data_type="object_ref",
        ref_type_slug=iface,
    )
    plan = _plan(
        client,
        admin,
        order,
        [{"label": "가", "eq": f"E{tag}-0"}, {"label": "나", "eq": f"E{tag}-only"}],
    )
    assert [row["action"] for row in plan["rows"]] == ["error", "create"], plan
    assert "타입 여럿에" in plan["rows"][0]["message"]


def test_종류_변경과_참조_후보도_큰_대상을_덩어리로_묻는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    tag = w["tag"]
    case = _make_type(client, admin, label="결과(글)", usage="log")
    _make_property(client, admin, case, key="test", label="시험", data_type="text")
    rows = {
        name: _make_object(client, admin, case, label=name, properties={"test": value})["id"]
        for name, value in (
            ("가", f"T{tag}-1"),
            ("나", f"이름만 {tag}"),
            ("다", f"다른이름{tag}"),
        )
    }
    done = _retype(
        client,
        admin,
        case,
        "test",
        data_type="object_ref",
        ref_type_slug=w["test"],
        apply=True,
    ).json()
    assert done["applied"] is True, done
    values = {name: _value(client, admin, case, one, "test") for name, one in rows.items()}
    assert values == {"가": w["key"], "나": w["label"], "다": w["alias"]}
