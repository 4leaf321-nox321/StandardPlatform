"""확장 `caegroup` — 디지털 트윈 역량의 1단계(기준 정보와 연계).

여기서 지키는 것: **확장을 켜야 보인다** · 정의를 화면이 받아 간다 · 기준 정보는 시스템
관리자가 만든다 · 같은 조합을 두 번 등록하지 못한다 · 다른 타입의 객체는 등록되지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.extensions.caegroup.models import CaeDtPair, CaeDtSetting
from app.modules.server.models import ExtensionState
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object

DT = "/api/ext/caegroup/dt"


@pytest.fixture(autouse=True)
def _on(client: TestClient, admin: Signed, db: Session) -> Iterator[None]:
    """확장을 켜고 시작한다 — CI 에는 `.env` 가 없어 기본값이 「꺼짐」 이다.

    끝나면 켜짐과 이 확장의 행을 지운다. **전역 상태라** 다음 시험이 물려받으면
    엉뚱한 곳에서 실패한다(시험 DB 는 세션마다 한 번만 비워진다).
    """
    client.patch(
        "/api/server/extensions/caegroup", json={"enabled": True}, headers=admin.headers
    )
    yield
    db.execute(delete(CaeDtPair))
    db.execute(delete(CaeDtSetting))
    db.execute(delete(ExtensionState))
    db.commit()


def _setup(client: TestClient, admin: Signed) -> dict[str, Any]:
    got = client.post(f"{DT}/setup", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_끄면_없다(client: TestClient, admin: Signed) -> None:
    """확장이 꺼진 설치에는 **경로 자체가 없다.**"""
    client.patch(
        "/api/server/extensions/caegroup", json={"enabled": False}, headers=admin.headers
    )
    assert client.get(f"{DT}/defs", headers=admin.headers).status_code == 404


def test_정의를_화면이_받아_간다(client: TestClient, member: Signed) -> None:
    """**축 이름과 척도를 화면에 박지 않는다** — 문구를 고치는 일이 화면 수정이 되면
    안 고쳐진다."""
    body = client.get(f"{DT}/defs", headers=member.headers).json()
    assert body["sector"] == "simulation"
    axes = {one["key"]: one for one in body["axes"]}
    assert list(axes) == ["accuracy", "automation", "modeling", "scope", "substitution"]
    # 원본의 「정확도」 를 화면 이름만 바꿨다 — 저장 key 는 그대로다(이력이 key 로 묶인다).
    assert axes["accuracy"]["label"] == "가상검증률"
    assert axes["accuracy"]["kind"] == "value"
    assert [one["min"] for one in body["accuracy_thresholds"]] == [0.0, 70.0, 90.0]
    assert [one["key"] for one in body["evidence_tiers"]] == ["stated", "checked", "verified"]


def test_기준_정보는_시스템_관리자가_만든다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """온톨로지 정의를 바꾸는 일이라 부서 권한으로 할 일이 아니다."""
    assert client.post(f"{DT}/setup", headers=member.headers).status_code == 403

    before = client.get(f"{DT}/setup", headers=admin.headers).json()
    assert before["ready"] is False

    after = _setup(client, admin)
    assert after["ready"] is True
    assert after["subject_type_slug"] == "sim_test_item"
    assert after["agent_type_slug"] == "sim_analysis"

    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert "sim_test_item" in types
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    subject = next(one for one in schema["types"] if one["slug"] == "sim_test_item")
    keys = {one["key"] for one in subject["properties"]}
    assert {"defect_types", "dev_stages", "accuracy_rule"} <= keys

    # **두 번 불러도 안전하다** — 가져오기는 더하고 고치기만 한다.
    assert _setup(client, admin)["ready"] is True


def test_해석_타입은_도구_카탈로그와_다르다(client: TestClient, admin: Signed) -> None:
    """**해석과 소프트웨어 제품은 다른 것이다.**

    개발 설치의 `sim_tool` 은 해석 분야 · 라이선스를 필수로 받는 제품 카탈로그였다. 수단을
    거기 합치면 해석 한 줄을 적을 때마다 라이선스를 입력해야 하고, 같은 제품을 쓰는 해석
    열 개가 한 줄로 뭉쳐 「이 시험을 무엇으로 보나」 를 답할 수 없다 — 실측으로 표본을
    넣다가 막혔다(2026-09-24).
    """
    _setup(client, admin)
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    agent = next(one for one in schema["types"] if one["slug"] == "sim_analysis")
    assert agent["label"] == "시뮬레이션 해석"
    keys = {one["key"] for one in agent["properties"]}
    assert {"kind", "model_kind"} <= keys
    # 이 시험 DB 에는 도구 카탈로그가 없으므로 참조 속성은 안 붙는다 — 없는 타입을
    # 가리키는 속성을 만들면 기준 정보 생성이 통째로 막힌다.
    assert "tools" not in keys


def test_연계를_등록하고_해제한다(client: TestClient, admin: Signed) -> None:
    _setup(client, admin)
    subject = _make_object(client, admin, "sim_test_item", label="낙하 시험")
    agent = _make_object(client, admin, "sim_analysis", label="낙하 해석")

    made = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": subject["id"],
            "agent_id": agent["id"],
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["subject_label"] == "낙하 시험"
    assert made.json()["agent_label"] == "낙하 해석"

    rows = client.get(f"{DT}/pairs", headers=admin.headers).json()
    assert [one["id"] for one in rows] == [made.json()["id"]]

    # **같은 조합이 둘이면 평가가 갈린다** — 어느 쪽이 참인지 화면이 답할 수 없다.
    again = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": subject["id"],
            "agent_id": agent["id"],
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert again.status_code == 409, again.text

    gone = client.delete(f"{DT}/pairs/{made.json()['id']}", headers=admin.headers)
    assert gone.status_code == 204
    assert client.get(f"{DT}/pairs", headers=admin.headers).json() == []


def test_다른_타입의_객체는_등록되지_않는다(client: TestClient, admin: Signed) -> None:
    """대상 자리에 시뮬레이션을 등록하면 화면이 그 줄을 그릴 수 없다 — 이름도 속성도 다르다."""
    _setup(client, admin)
    agent = _make_object(client, admin, "sim_analysis", label="열 해석")
    got = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": agent["id"],
            "agent_id": agent["id"],
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert got.status_code == 409
    assert "타입이 설정과 다릅니다" in got.json()["error"]["message"]


def test_기준_정보_없이는_등록되지_않는다(client: TestClient, admin: Signed) -> None:
    """무엇이 시험 항목인지 먼저 정해야 한다 — 안 정하면 아무 객체나 등록된다."""
    got = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": "00000000-0000-0000-0000-000000000001",
            "agent_id": "00000000-0000-0000-0000-000000000002",
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert got.status_code == 409
    assert "기준 정보" in got.json()["error"]["message"]
