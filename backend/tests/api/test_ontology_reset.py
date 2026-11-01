"""온톨로지 통째로 비우기 — **되돌릴 수 없는 일에는 세 겹의 문이 있다.**

계획 먼저, 적어서 확인, 그리고 정의는 스냅샷으로 남는다. 여기서 지키는 것은 그 셋과,
「비우지 말아야 할 것」 이 안 지워지는가다 — 부서·계정은 온톨로지가 아니다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.ontology.reset import CONFIRM_PHRASE
from tests.api.conftest import Signed
from tests.api.test_ontology import _link, _make_object, _make_relation, _make_type


def _reset(client: TestClient, who: Signed, **body: Any) -> Any:
    return client.post("/api/ontology/reset", json=body, headers=who.headers)


def _seed(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(client, admin, "supplied_by", label="공급받음")
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    assert _link(client, admin, part, bolt["id"], kind, acme["id"]).status_code == 201


def test_지표_정의가_있어도_비워진다(client: TestClient, admin: Signed) -> None:
    """지표 정의는 **타입을 가리킨다**(`source_type_id`, RESTRICT) — 비우기가 그 표를 안
    지우면 날 FK 문구로 막힌다.

    실측으로 그렇게 막혔다(CI). 지표를 하나라도 정의한 설치에서는 「비우기」 가 아예 안 됐고,
    오류 문구는 `fk_metric_defs_source_type_id_object_types` 였다 — 사람이 읽고 할 일을 알 수
    없는 말이다. 순서에 기대는 시험(지표 시험 뒤에 도는 것)으로는 이 자리가 비면 조용히
    지나가므로, **여기서 직접 정의하고 비운다.**
    """
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트", properties={})
    made = client.post(
        "/api/metrics",
        params={"recompute": "false"},
        json={
            "slug": "reset_m",
            "label": "부품 수",
            "source_type_slug": part,
            "spec": {"measure": "count"},
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    # 계획이 **먼저 센다** — 안 세면 화면에는 안 보이고 적용만 막힌다.
    planned = _reset(client, admin).json()
    counts = {one["table"]: one["count"] for one in planned["items"]}
    assert counts.get("metric_defs", 0) >= 1, planned["items"]

    applied = _reset(client, admin, confirm=CONFIRM_PHRASE, apply=True)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    assert client.get("/api/metrics", headers=admin.headers).json() == []


def test_장표_틀과_장표가_있어도_비워진다(client: TestClient, admin: Signed) -> None:
    """장표 틀은 **주제 타입을 가리키고**, 장표는 틀을 가리킨다(둘 다 RESTRICT).

    비우기가 두 표를 안 지워 장표 틀이 하나라도 있는 설치에서는 「비우기」 가 FK 로 막혔다.
    전체 시험에서는 장표 시험이 같은 작업자에서 먼저 돌 때만 걸려서, 여기서 직접 만들고 비운다.
    """
    model = _make_type(client, admin, label="모델")
    subject = _make_object(client, admin, model, label="A")
    template = client.post(
        "/api/slides/templates",
        json={
            "name": "초기화 시험 틀",
            "subject_type_slug": model,
            "rows": 1,
            "cols": 1,
            "areas": [{"key": "note", "title": "의견", "row": 0, "col": 0, "kind": "text"}],
        },
        headers=admin.headers,
    )
    assert template.status_code == 201, template.text
    slide = client.post(
        "/api/slides",
        json={"template_id": template.json()["id"], "subject_id": subject["id"]},
        headers=admin.headers,
    )
    assert slide.status_code == 201, slide.text

    planned = _reset(client, admin).json()
    counts = {one["table"]: one["count"] for one in planned["items"]}
    assert counts.get("slide_templates", 0) >= 1 and counts.get("slides", 0) >= 1, counts

    applied = _reset(client, admin, confirm=CONFIRM_PHRASE, apply=True)
    assert applied.status_code == 200, applied.text
    assert client.get("/api/slides/templates", headers=admin.headers).json() == []


def test_제안된_관계가_있어도_비워지고_계획이_센다(client: TestClient, admin: Signed) -> None:
    """제안된 관계(ADR 0042)는 **객체를 가리킨다**(CASCADE) — 비우기가 그 표를 안 세면 계획에는
    안 보이고 객체와 함께 소리 없이 사라진다. 확정된 제안은 선도 가리킨다(SET NULL). 장표 틀이
    빠졌던 자리와 같은 실수를 막으려고 여기서 직접 만들고(열린 것 · 확정한 것) 비운다."""
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    kind = _make_relation(client, admin, "supplied_by", label="공급받음")
    bolt = _make_object(client, admin, part, label="볼트")
    acme = _make_object(client, admin, vendor, label="ACME")
    beta = _make_object(client, admin, vendor, label="BETA")
    made = []
    for dst in (acme, beta):
        got = client.post(
            f"/api/objects/{part}/{bolt['id']}/relation-proposals",
            json={"relation": kind, "dst_object_id": dst["id"], "confidence": 0.8},
            headers=admin.headers,
        )
        assert got.status_code == 200 and got.json()["status"] == "created", got.text
        made.append(got.json()["proposal"]["id"])
    accepted = client.post(
        f"/api/objects/relation-proposals/{made[0]}/accept", headers=admin.headers
    )
    assert accepted.status_code == 200, accepted.text

    planned = _reset(client, admin).json()
    counts = {one["table"]: one["count"] for one in planned["items"]}
    assert counts.get("relation_proposals", 0) >= 2, counts

    applied = _reset(client, admin, confirm=CONFIRM_PHRASE, apply=True)
    assert applied.status_code == 200, applied.text
    after = {one["table"]: one["count"] for one in _reset(client, admin).json()["items"]}
    assert after["relation_proposals"] == 0 and after["object_relations"] == 0, after


def test_계획이_먼저고_아무것도_안_지운다(client: TestClient, admin: Signed) -> None:
    """「정말 삭제하시겠습니까」 만 묻는 창은 아무도 안 읽고 예를 누른다 — 읽을 것이
    없어서다."""
    _seed(client, admin)
    planned = _reset(client, admin)
    assert planned.status_code == 200, planned.text
    body = planned.json()
    assert body["applied"] is False
    counts = {one["table"]: one["count"] for one in body["items"]}
    assert counts["object_types"] >= 2
    assert counts["objects"] >= 2
    assert counts["object_relations"] >= 1
    assert body["total"] == sum(one["count"] for one in body["items"])
    assert body["confirm_phrase"] == CONFIRM_PHRASE

    # 계획만 봤으므로 정의는 그대로다.
    assert client.get("/api/ontology/types", headers=admin.headers).json()


def test_확인_문구가_틀리면_안_지운다(client: TestClient, admin: Signed) -> None:
    _seed(client, admin)
    denied = _reset(client, admin, apply=True, confirm="지워줘")
    assert denied.status_code == 409
    assert CONFIRM_PHRASE in denied.json()["error"]["message"]
    assert client.get("/api/ontology/types", headers=admin.headers).json()


def test_비우면_정의와_데이터가_함께_사라지고_스냅샷이_남는다(
    client: TestClient, admin: Signed
) -> None:
    _seed(client, admin)
    # **수를 세지 않는다.** 목록은 50개까지만 오므로, 스냅샷이 쌓인 DB 에서는 새로 하나가
    # 생겨도 수가 그대로다(`50 > 50` 으로 간헐 실패했다). 새 id 가 왔는지를 본다.
    before = {
        one["id"]
        for one in client.get("/api/ontology/snapshots", headers=admin.headers).json()
    }

    done = _reset(client, admin, apply=True, confirm=CONFIRM_PHRASE)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["applied"] is True
    assert body["snapshot_id"]

    assert client.get("/api/ontology/types", headers=admin.headers).json() == []
    assert client.get("/api/ontology/relation-types", headers=admin.headers).json() == []
    # 한 번 더 비우면 셀 것이 없다 — 지워진 것이 진짜 지워졌다.
    again = _reset(client, admin).json()
    assert again["total"] == 0

    # **정의는 되돌릴 수 있다.** 데이터는 안 돌아온다 — 그 비대칭이 이 기능의 값이다.
    snapshots = client.get("/api/ontology/snapshots", headers=admin.headers).json()
    assert {one["id"] for one in snapshots} - before
    restored = client.post(
        f"/api/ontology/snapshots/{body['snapshot_id']}/restore", headers=admin.headers
    )
    assert restored.status_code == 200, restored.text
    assert client.get("/api/ontology/types", headers=admin.headers).json()
    assert client.get("/api/objects/quality/report", headers=admin.headers).status_code == 200


def test_부서와_계정은_안_건드린다(client: TestClient, admin: Signed, db: Session) -> None:
    """온톨로지가 아닌 것까지 지우면 그것은 초기화가 아니라 사고다."""
    _seed(client, admin)
    workspaces = len(client.get("/api/workspaces?all=true", headers=admin.headers).json())
    assert workspaces > 0

    assert _reset(client, admin, apply=True, confirm=CONFIRM_PHRASE).status_code == 200
    after = client.get("/api/workspaces?all=true", headers=admin.headers).json()
    assert len(after) == workspaces
    assert client.get("/api/accounts", headers=admin.headers).status_code == 200


def test_시스템_관리자만(client: TestClient, manager: Signed) -> None:
    denied = _reset(client, manager)
    assert denied.status_code == 403
