"""확장 `svinput` — 자주검증 입력(ADR 0051).

여기서 지키는 것:
- **켜야 있다** — 꺼진 설치에서는 경로가 없다.
- 표도 타입도 새로 만들지 않는다 — 설정 한 줄이 허브의 타입 · 칸을 **역할**로 가리킨다. 설정은
  시스템 관리자만, 모르는 역할 · 없는 타입 · 그 타입에 없는 칸은 거절한다(조용히 버리지
  않는다).
- 문서 한 장은 문제점 줄과 하위 기록 수(판정 통과 · 실패)를 한 번에 준다 — 볼 수 있는 것만.
- 설정이 안 된 설치는 홈의 「남은 일」 이 시스템 관리자에게 말한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.extensions.svinput.models import SvInputSetting
from app.modules.server.models import ExtensionState
from tests.api.conftest import Signed, maintenance_counts
from tests.api.test_ontology import _make_object, _make_property, _make_type

SV = "/api/ext/svinput"


@pytest.fixture(autouse=True)
def _on(client: TestClient, admin: Signed, db: Session) -> Iterator[None]:
    """확장을 켜고, 설정을 비우고 시작한다 — 둘 다 전역 상태라 다음 시험이 물려받으면 엉뚱한
    곳에서 실패한다."""
    db.execute(delete(SvInputSetting))
    db.commit()
    client.patch(
        "/api/server/extensions/svinput", json={"enabled": True}, headers=admin.headers
    )
    yield
    db.execute(delete(ExtensionState))
    db.execute(delete(SvInputSetting))
    db.commit()


def _world(client: TestClient, admin: Signed) -> dict[str, str]:
    """허브의 `sv_pm_*` 를 흉내 낸 타입 셋 — 문서 · 문제점(문서 참조 · 일정 상태) · 단계별 판정
    (문제점 참조 · 단계 · 판정). slug 는 시험마다 다르게(시험 DB 를 함께 쓴다)."""
    tag = uuid.uuid4().hex[:6]
    report = _make_type(client, admin, slug=f"sv_pm_report_{tag}", label="포스트모텀 문서")
    issue = _make_type(client, admin, slug=f"sv_pm_issue_{tag}", label="문제점", usage="log")
    stage = _make_type(
        client, admin, slug=f"sv_pm_stage_{tag}", label="단계별 판정", usage="log"
    )
    _make_property(
        client,
        admin,
        issue,
        key="report",
        label="문서",
        data_type="object_ref",
        ref_type_slug=report,
    )
    _make_property(
        client,
        admin,
        issue,
        key="plan_status",
        label="일정 상태",
        data_type="enum",
        enum_options=["진행", "완료"],
    )
    _make_property(
        client, admin, issue, key="problem_text", label="현상", data_type="text_long"
    )
    _make_property(
        client,
        admin,
        stage,
        key="issue",
        label="문제점",
        data_type="object_ref",
        ref_type_slug=issue,
    )
    _make_property(client, admin, stage, key="stage", label="단계", data_type="text")
    _make_property(
        client,
        admin,
        stage,
        key="result",
        label="판정",
        data_type="enum",
        enum_options=["PASS", "FAIL"],
    )
    return {"report": report, "issue": issue, "stage": stage}


def _configure(client: TestClient, admin: Signed, w: dict[str, str]) -> dict[str, Any]:
    body = {
        "roles": {
            "report": {"type": w["report"], "fields": {}},
            "issue": {
                "type": w["issue"],
                "fields": {"report_ref": "report", "status": "plan_status"},
            },
            "stage_result": {
                "type": w["stage"],
                "fields": {"issue_ref": "issue", "stage": "stage", "result": "result"},
            },
        }
    }
    got = client.put(f"{SV}/settings", json=body, headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_꺼진_설치에는_경로가_없다(client: TestClient, admin: Signed) -> None:
    assert client.get(f"{SV}/status", headers=admin.headers).status_code == 200
    client.patch(
        "/api/server/extensions/svinput", json={"enabled": False}, headers=admin.headers
    )
    assert client.get(f"{SV}/status", headers=admin.headers).status_code == 404


def test_설정_전에는_준비_안_됨이고_남은_일이_말한다(
    client: TestClient, admin: Signed
) -> None:
    state = client.get(f"{SV}/status", headers=admin.headers).json()
    assert state["ready"] is False
    assert [one["key"] for one in state["defs"]][:3] == ["spine", "report", "issue"]
    assert maintenance_counts(client, admin).get("svinput_setup") == 1


def test_설정은_시스템_관리자만_그리고_틀린_것은_거절(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    w = _world(client, admin)
    body = {"roles": {"report": {"type": w["report"], "fields": {}}}}
    assert client.put(f"{SV}/settings", json=body, headers=member.headers).status_code == 403
    assert client.get(f"{SV}/candidates", headers=member.headers).status_code == 403

    unknown = client.put(
        f"{SV}/settings",
        json={"roles": {"nope": {"type": w["report"]}}},
        headers=admin.headers,
    )
    assert unknown.status_code == 422 and unknown.json()["error"]["code"].endswith(
        "SVINPUT-0001"
    )
    missing_type = client.put(
        f"{SV}/settings",
        json={"roles": {"report": {"type": "sv_pm_nothing"}}},
        headers=admin.headers,
    )
    assert missing_type.status_code == 422
    bad_field = client.put(
        f"{SV}/settings",
        json={"roles": {"issue": {"type": w["issue"], "fields": {"report_ref": "nope"}}}},
        headers=admin.headers,
    )
    assert bad_field.status_code == 422 and "nope" in bad_field.json()["error"]["message"]

    # 후보는 타입 전부에 허브 접두 여부 · 속성 키 — 설정 화면이 없는 칸을 못 고르게 하고,
    # 스파인(코어 과제 · 모델)은 접두가 아닌 것에서 고른다.
    spine = _make_type(client, admin, slug=f"plm_project_{uuid.uuid4().hex[:6]}", label="과제")
    found = client.get(f"{SV}/candidates", headers=admin.headers).json()
    assert found["prefix"] == "sv_pm_"
    issue = next(one for one in found["types"] if one["slug"] == w["issue"])
    assert issue["hub"] is True
    assert next(one for one in found["types"] if one["slug"] == spine)["hub"] is False
    # 스파인 역할은 접두가 아닌 타입도 받는다.
    okay = client.put(
        f"{SV}/settings", json={"roles": {"spine": {"type": spine}}}, headers=admin.headers
    )
    assert okay.status_code == 200, okay.text
    assert set(issue["properties"]) >= {"report", "plan_status", "problem_text"}


def test_설정하면_준비되고_부분_수정은_안_보낸_역할을_지킨다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    state = _configure(client, admin, w)
    assert state["ready"] is True
    by_key = {one["key"]: one for one in state["roles"]}
    assert by_key["issue"]["fields"] == {"report_ref": "report", "status": "plan_status"}
    assert by_key["stage_result"]["missing"] == []

    # 문서 역할만 비운다 — 문제점 설정은 그대로.
    cleared = client.put(
        f"{SV}/settings", json={"roles": {"report": {"type": None}}}, headers=admin.headers
    ).json()
    by_key = {one["key"]: one for one in cleared["roles"]}
    assert by_key["report"]["type_slug"] is None and by_key["issue"]["type_slug"] == w["issue"]
    assert cleared["ready"] is False


def test_문서_한_장은_문제점과_하위_기록_수를_한_번에_준다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    w = _world(client, admin)
    _configure(client, admin, w)
    report = _make_object(client, admin, w["report"], label="모델 X 포스트모텀")
    other = _make_object(client, admin, w["report"], label="다른 문서")
    hinge = _make_object(
        client,
        admin,
        w["issue"],
        label="힌지 이탈",
        properties={
            "report": report["id"],
            "plan_status": "진행",
            "problem_text": "낙하 시 이탈",
        },
    )
    deco = _make_object(
        client, admin, w["issue"], label="Deco 찍힘", properties={"report": report["id"]}
    )
    _make_object(
        client, admin, w["issue"], label="남의 것", properties={"report": other["id"]}
    )
    for stage, result in (("DV1", "FAIL"), ("DV2", "PASS"), ("PV2", "PASS")):
        _make_object(
            client,
            admin,
            w["stage"],
            label=f"힌지 {stage}",
            properties={"issue": hinge["id"], "stage": stage, "result": result},
        )

    sheet = client.get(f"{SV}/reports/{report['id']}", headers=member.headers)
    assert sheet.status_code == 200, sheet.text
    body = sheet.json()
    assert body["report"]["label"] == "모델 X 포스트모텀"
    assert body["report_type_slug"] == w["report"] and body["issue_type_slug"] == w["issue"]
    assert body["total_issues"] == 2
    lines = {one["label"]: one for one in body["issues"]}
    assert lines["힌지 이탈"]["plan_status"] == "진행"
    assert lines["힌지 이탈"]["counts"] == {
        "stage_result": 3,
        "cause": 0,
        "prevention": 0,
        "figure": 0,
        "passed": 2,
        "failed": 1,
    }
    assert lines["Deco 찍힘"]["plan_status"] is None
    assert lines["Deco 찍힘"]["counts"]["stage_result"] == 0
    assert lines["힌지 이탈"]["ref_labels"][report["id"]] == "모델 X 포스트모텀"
    assert body["kpis"] == []
    assert body["can_edit"] is False  # 구성원은 남이 만든 문서를 못 고친다(화면 단추의 근거)

    # 문서 타입이 아닌 객체 · 없는 id 는 404.
    assert client.get(f"{SV}/reports/{deco['id']}", headers=admin.headers).status_code == 404
    assert client.get(f"{SV}/reports/{uuid.uuid4()}", headers=admin.headers).status_code == 404


def test_설정_없이_한_장을_물으면_설정부터_하라고_한다(
    client: TestClient, admin: Signed
) -> None:
    got = client.get(f"{SV}/reports/{uuid.uuid4()}", headers=admin.headers)
    assert got.status_code == 404 and "설정" in got.json()["error"]["message"]


def test_자기소개와_쌓인_것은_켠_설치에서만(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _configure(client, admin, w)
    _make_object(client, admin, w["report"], label="문서 하나")
    status = client.get("/api/server/status", headers=admin.headers).json()
    labels = {one["label"]: one["count"] for one in status["counts"]}
    assert labels.get("자주검증 포스트모텀 문서", 0) >= 1
    assert "svinput" in status["extensions"]
    # 끄면 「쌓인 것」 에서도 빠진다 — 레지스트리는 켜짐을 모르고 확장이 스스로 본다.
    client.patch(
        "/api/server/extensions/svinput", json={"enabled": False}, headers=admin.headers
    )
    off = client.get("/api/server/status", headers=admin.headers).json()
    assert "자주검증 포스트모텀 문서" not in {one["label"] for one in off["counts"]}


def test_문서와_문제점의_연결은_관계_종류로도_된다(client: TestClient, admin: Signed) -> None:
    """한 문제점이 심사회 · 포스트모텀에 함께 실리려면 여럿 대 여럿이어야 한다(ADR 0051 7절) —
    참조 칸 대신 관계 종류(`rel:<slug>`)를 설정하면 코어의 선(`object_relations`)으로 읽는다.
    방향은 가리지 않는다."""
    w = _world(client, admin)
    rel = f"sv_cites_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": rel,
                    "label": "실은 문제점",
                    "src_type_slugs": [w["report"]],
                    "dst_type_slugs": [w["issue"]],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text

    # 없는 관계 종류는 거절 · 있는 것은 통과.
    bad = client.put(
        f"{SV}/settings",
        json={"roles": {"issue": {"type": w["issue"], "fields": {"report_ref": "rel:nope"}}}},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    state = client.put(
        f"{SV}/settings",
        json={
            "roles": {
                "report": {"type": w["report"], "fields": {}},
                "issue": {"type": w["issue"], "fields": {"report_ref": f"rel:{rel}"}},
            }
        },
        headers=admin.headers,
    ).json()
    assert state["ready"] is True
    found = client.get(f"{SV}/candidates", headers=admin.headers).json()
    assert any(one["slug"] == rel for one in found["relations"])

    review = _make_object(client, admin, w["report"], label="심사회")
    postmortem = _make_object(client, admin, w["report"], label="포스트모텀")
    shared = _make_object(client, admin, w["issue"], label="같은 문제점")
    only = _make_object(client, admin, w["issue"], label="포스트모텀만")
    for src, dst in ((review, shared), (postmortem, shared), (postmortem, only)):
        linked = client.post(
            f"/api/objects/{w['report']}/{src['id']}/relations",
            json={"relation": rel, "dst_object_id": dst["id"]},
            headers=admin.headers,
        )
        assert linked.status_code == 201, linked.text

    first = client.get(f"{SV}/reports/{review['id']}", headers=admin.headers).json()
    assert [one["label"] for one in first["issues"]] == ["같은 문제점"]
    second = client.get(f"{SV}/reports/{postmortem['id']}", headers=admin.headers).json()
    assert sorted(one["label"] for one in second["issues"]) == ["같은 문제점", "포스트모텀만"]
