"""타입의 쓰임새 — **축인가 기록인가**(ADR 0011).

저장 · 권한 · 값은 같고 기본 동작만 다르다. 기록은 통합 검색에서 건수로, 축의 상세 ·
그래프에서는 「가리키는 기록」 의 수로 선다 — 인기 모델은 기록 10만 건이 가리킨다. 참조
후보로 제안되지 않고, 표에서 만든 타입은 기본이 기록이다.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_interfaces import _code
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:6]
    group = f"g_{tag}"
    made = client.post(
        "/api/ontology/groups", json={"slug": group, "label": "품질"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    model = _make_type(
        client, admin, label="개발모델", key_policy="required", nav_group_slug=group
    )
    case = _make_type(client, admin, label="시장 서비스", usage="log", nav_group_slug=group)
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
        inverse_label="시장 서비스",
    )
    hot = _make_object(client, admin, model, key=f"{tag}-HOT", label=f"인기 {tag}")
    for n in range(3):
        _make_object(
            client, admin, case, label=f"건 {tag} {n}", properties={"model": hot["id"]}
        )
    return {"tag": tag, "group": group, "model": model, "case": case, "hot": hot["id"]}


def test_쓰임새는_고를_수_있고_정의_파일로도_오간다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert (types[w["model"]]["usage"], types[w["case"]]["usage"]) == ("axis", "log")

    wrong = client.patch(
        f"/api/ontology/types/{w['model']}", json={"usage": "ledger"}, headers=admin.headers
    )
    assert wrong.status_code in (409, 422) and "축 · 기록" in wrong.json()["error"]["message"]

    exported = client.get(
        "/api/ontology/export", params={"format": "json"}, headers=admin.headers
    )
    assert exported.status_code == 200, exported.text
    mine = next(one for one in exported.json()["types"] if one["slug"] == w["case"])
    assert mine["usage"] == "log"

    mine["usage"] = "axis"
    plan = client.post(
        "/api/ontology/import?dry_run=false", json={"types": [mine]}, headers=admin.headers
    ).json()
    assert plan["applied"] is True, plan
    again = {
        one["slug"]: one
        for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert again[w["case"]]["usage"] == "axis"


def test_통합_검색은_기록을_섞지_않고_세며_좁히면_보인다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    mixed = client.get("/api/search", params={"q": w["tag"]}, headers=admin.headers).json()
    assert {one["type_slug"] for one in mixed["items"]} == {w["model"]}
    counted = {one["type_slug"]: one for one in mixed["types"]}
    assert counted[w["case"]]["count"] == 3 and counted[w["case"]]["usage"] == "log"
    assert (mixed["total"], mixed["records"]) == (1, 3)

    narrowed = client.get(
        "/api/search", params={"q": w["tag"], "type": w["case"]}, headers=admin.headers
    ).json()
    assert len(narrowed["items"]) == 3 and narrowed["total"] == 3


def test_축의_상세와_그래프는_가리키는_기록을_수로_보인다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    profile = client.get(f"/api/objects/{w['model']}/{w['hot']}", headers=admin.headers).json()
    assert not [one for one in profile["related"] if one["object_type_slug"] == w["case"]]
    assert profile["log_counts"] == [
        {
            "type_slug": w["case"],
            "type_label": "시장 서비스",
            "key": "model",
            "label": "모델",
            "inverse_label": "시장 서비스",
            "count": 3,
        }
    ]

    graph = client.get(
        "/api/graph/neighborhood", params={"focus": w["hot"]}, headers=admin.headers
    ).json()
    assert [one["id"] for one in graph["nodes"]] == [w["hot"]]
    assert graph["log_counts"][0]["count"] == 3
    focus = graph["nodes"][0]
    assert not focus.get("degree"), "이웃에서 뺀 기록을 「+N 더」 로 세지 않는다"

    with_records = client.get(
        "/api/graph/neighborhood",
        params={"focus": w["hot"], "records": "true"},
        headers=admin.headers,
    ).json()
    assert len(with_records["nodes"]) == 4 and with_records["log_counts"] == []

    # 기록 쪽에서 보면 가리키는 축은 그대로 관련 객체다.
    case_row = client.get(
        f"/api/objects/{w['case']}", params={"q": f"건 {w['tag']} 0"}, headers=admin.headers
    ).json()["items"][0]
    from_case = client.get(
        f"/api/objects/{w['case']}/{case_row['id']}", headers=admin.headers
    ).json()
    assert [one["object_id"] for one in from_case["related"]] == [w["hot"]]


def test_사이드바는_축_다음에_기록을_세운다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    nav = client.get("/api/ontology/nav", headers=admin.headers).json()
    mine = next(one for one in nav if one["slug"] == w["group"])
    assert [(one["slug"], one["usage"]) for one in mine["items"]] == [
        (w["model"], "axis"),
        (w["case"], "log"),
    ]


def test_표에서_만든_타입은_기록이고_기록은_참조로_제안하지_않는다(
    client: TestClient, admin: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    case = _make_type(client, admin, label="시험 건", usage="log", key_policy="required")
    for n in range(5):
        _make_object(client, admin, case, key=f"T{tag}-{n}", label=f"시험 {tag} {n}")
    text = "\n".join(["이름,시험", *(f"결과 {n},T{tag}-{n % 5}" for n in range(10))])
    got = client.post(
        "/api/ontology/infer",
        files={"file": ("rows.csv", io.BytesIO(text.encode()), "text/csv")},
        headers=admin.headers,
    ).json()
    column = next(one for one in got["columns"] if one["header"] == "시험")
    assert column["data_type"] == "text", "기록은 대상으로 제안하지 않는다"
    assert column["ref_candidates"][0]["target_slug"] == case
    assert "기록이라 제안하지 않습니다" in column["ref_note"]

    built = client.post(
        "/api/ontology/infer/build",
        json={
            "slug": f"res_{tag}",
            "label": "시험 결과",
            "columns": got["columns"],
            "raw_rows": got["raw_rows"],
        },
        headers=admin.headers,
    ).json()
    assert built["schema"]["types"][0]["usage"] == "log"
    refused = client.post(
        "/api/ontology/infer/build",
        json={
            "slug": f"res_{tag}",
            "label": "시험 결과",
            "usage": "ledger",
            "columns": got["columns"],
            "raw_rows": got["raw_rows"],
        },
        headers=admin.headers,
    )
    assert refused.status_code in (409, 422) and _code(refused)
