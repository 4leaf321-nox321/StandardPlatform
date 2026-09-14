"""참조 칸은 칸에 저장한 관계다 — **그래프 · 관련 객체 · 트리 · 스키마에서 관계와 함께
보이나.**

「개발모델의 과제」 를 참조 칸으로 두면 목록 · 조건 · 통계는 되는데 그래프에는 선이 없었다.
그러면 사용자는 「참조는 관계가 아닌가」 를 배우게 되는데, 그것은 저장 자리의 사정이지
온톨로지의 구분이 아니다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_graph import _neighborhood
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _hierarchy(client: TestClient, admin: Signed) -> dict[str, Any]:
    """프로젝트 ← 과제 ← 모델(참조 칸으로). 기술 분야는 자기 타입을 가리킨다(트리)."""
    project = _make_type(client, admin, "project", label="프로젝트", key_policy="required")
    task = _make_type(client, admin, "task", label="과제", key_policy="required")
    model = _make_type(client, admin, "model", label="개발모델", key_policy="required")
    _make_property(
        client,
        admin,
        task,
        key="project",
        label="프로젝트",
        data_type="object_ref",
        ref_type_slug=project,
        inverse_label="과제",
    )
    _make_property(
        client,
        admin,
        model,
        key="task",
        label="과제",
        data_type="object_ref",
        ref_type_slug=task,
    )
    p1 = _make_object(client, admin, project, key="P-1", label="프로젝트 1")
    t1 = _make_object(
        client, admin, task, key="T-1", label="과제 1", properties={"project": p1["id"]}
    )
    t2 = _make_object(
        client, admin, task, key="T-2", label="과제 2", properties={"project": p1["id"]}
    )
    m1 = _make_object(
        client, admin, model, key="M-1", label="모델 1", properties={"task": t1["id"]}
    )
    m2 = _make_object(
        client, admin, model, key="M-2", label="모델 2", properties={"task": t1["id"]}
    )
    return {
        "project": project,
        "task": task,
        "model": model,
        "p1": p1,
        "t1": t1,
        "t2": t2,
        "m1": m1,
        "m2": m2,
    }


def test_정의_그래프에_참조_칸이_선으로_굵기와_함께_선다(
    client: TestClient, admin: Signed
) -> None:
    h = _hierarchy(client, admin)
    body = client.get("/api/graph/overview", headers=admin.headers).json()
    edges = {(e["relation"], e["src_type"], e["dst_type"]): e for e in body["edges"]}
    task_to_project = edges[(f"ref:{h['task']}.project", h["task"], h["project"])]
    assert task_to_project["label"] == "프로젝트" and task_to_project["count"] == 2
    model_to_task = edges[(f"ref:{h['model']}.task", h["model"], h["task"])]
    assert model_to_task["count"] == 2 and model_to_task["directed"] is True


def test_이웃_펼치기가_참조_칸을_양방향으로_따라간다(
    client: TestClient, admin: Signed
) -> None:
    h = _hierarchy(client, admin)
    # 과제 1 에서 한 단계: 가리키는 프로젝트, 나를 가리키는 모델 둘.
    body = _neighborhood(client, admin, h["t1"]["id"], depth=1)
    ids = {node["id"] for node in body["nodes"]}
    assert ids == {h["t1"]["id"], h["p1"]["id"], h["m1"]["id"], h["m2"]["id"]}
    labels = {(e["src"], e["dst"]): e for e in body["edges"]}
    up = labels[(h["t1"]["id"], h["p1"]["id"])]
    assert up["label"] == "프로젝트" and up["inverse_label"] == "과제"
    down = labels[(h["m1"]["id"], h["t1"]["id"])]
    # 역방향 이름을 안 적은 칸은 가리키는 타입 이름으로 말한다.
    assert down["label"] == "과제" and down["inverse_label"] == "개발모델"
    focus = next(node for node in body["nodes"] if node["id"] == h["t1"]["id"])
    assert focus["degree"] == 3 and focus["truncated"] is False

    # 두 단계면 프로젝트에서 과제 2 까지.
    deeper = _neighborhood(client, admin, h["m1"]["id"], depth=3)
    assert {node["id"] for node in deeper["nodes"]} >= {h["t2"]["id"], h["p1"]["id"]}

    # 관계 slug 로 거르면 참조 칸도 그 이름으로 걸러진다.
    only = _neighborhood(
        client, admin, h["t1"]["id"], depth=1, relations=f"ref:{h['task']}.project"
    )
    assert {node["id"] for node in only["nodes"]} == {h["t1"]["id"], h["p1"]["id"]}


def test_상세의_관련_객체에_참조_칸이_양쪽으로_나온다(
    client: TestClient, admin: Signed
) -> None:
    h = _hierarchy(client, admin)
    profile = client.get(
        f"/api/objects/{h['task']}/{h['t1']['id']}", headers=admin.headers
    ).json()
    related = {(row["outgoing"], row["object_id"]): row for row in profile["related"]}
    up = related[(True, h["p1"]["id"])]
    assert (
        up["label"] == "프로젝트"
        and up["stored_as"] == "field"
        and up["field_key"] == "project"
    )
    down = related[(False, h["m1"]["id"])]
    assert down["label"] == "개발모델" and down["stored_as"] == "field"
    assert len(profile["related"]) == 3

    # 관계 줄을 끊는 길로 참조 선을 끊을 수는 없다 — 그것은 칸을 고치는 일이다.
    cut = client.delete(
        f"/api/objects/{h['task']}/{h['t1']['id']}/relations/{up['relation_id']}",
        headers=admin.headers,
    )
    assert cut.status_code == 404


def test_스키마가_참조_칸을_관계_모양으로도_준다(client: TestClient, admin: Signed) -> None:
    h = _hierarchy(client, admin)
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    edges = {one["slug"]: one for one in schema["reference_edges"]}
    edge = edges[f"ref:{h['task']}.project"]
    assert edge["src_type_slug"] == h["task"] and edge["dst_type_slug"] == h["project"]
    assert edge["inverse_label"] == "과제" and edge["field_key"] == "project"
    # 정의 가져오기로도 역방향 이름이 들어가고, 바꾸면 계획에 잡힌다.
    plan = client.post(
        "/api/ontology/import",
        json={
            "types": [
                {
                    "slug": h["task"],
                    "label": "과제",
                    "properties": [
                        {
                            "key": "project",
                            "label": "프로젝트",
                            "data_type": "object_ref",
                            "inverse_label": "딸린 과제",
                        }
                    ],
                }
            ]
        },
        headers=admin.headers,
    ).json()
    changed = next(c for c in plan["changes"] if c["slug"] == f"{h['task']}.project")
    assert changed["action"] == "update" and changed["fields"] == ["inverse_label"]


def test_같은_타입을_가리키는_참조_칸으로_트리를_그린다(
    client: TestClient, admin: Signed
) -> None:
    topic = _make_type(client, admin, "topic", label="기술 분야")
    _make_property(
        client,
        admin,
        topic,
        key="parent",
        label="상위 분야",
        data_type="object_ref",
        ref_type_slug=topic,
        inverse_label="하위 분야",
    )
    relation = f"ref:{topic}.parent"
    set_tree = client.patch(
        f"/api/ontology/types/{topic}",
        json={"list_view": {"tree": {"relation": relation, "parent": "dst"}}},
        headers=admin.headers,
    )
    assert set_tree.status_code == 200, set_tree.text
    root = _make_object(client, admin, topic, label="해석")
    child = _make_object(
        client, admin, topic, label="구조 해석", properties={"parent": root["id"]}
    )
    grandchild = _make_object(
        client, admin, topic, label="피로", properties={"parent": child["id"]}
    )
    lonely = _make_object(client, admin, topic, label="외톨이")

    roots = client.get(f"/api/objects/{topic}/tree", headers=admin.headers).json()
    assert [node["id"] for node in roots["nodes"]] == [root["id"]]
    assert roots["nodes"][0]["child_count"] == 1 and roots["orphan_count"] == 1
    below = client.get(
        f"/api/objects/{topic}/tree", params={"parent": root["id"]}, headers=admin.headers
    ).json()
    assert [node["id"] for node in below["nodes"]] == [child["id"]]
    # 「아래 것까지 포함」 — 손자까지.
    listed = client.get(
        f"/api/objects/{topic}", params={"under": root["id"]}, headers=admin.headers
    ).json()
    assert {row["id"] for row in listed["items"]} == {
        root["id"],
        child["id"],
        grandchild["id"],
    }
    assert lonely["id"] not in {row["id"] for row in listed["items"]}
