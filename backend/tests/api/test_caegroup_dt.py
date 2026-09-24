"""확장 `caegroup` — 디지털 트윈 역량의 1단계(기준 정보와 연계).

여기서 지키는 것: **확장을 켜야 보인다** · 정의를 화면이 받아 간다 · 기준 정보는 시스템
관리자가 만든다 · 같은 조합을 두 번 등록하지 못한다 · 다른 타입의 객체는 등록되지 않는다.
"""

from __future__ import annotations

import uuid
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
    # **묶음을 안 주면 타입이 메뉴에 안 뜬다** — 그러면 시험 항목을 넣을 자리를 주소로만
    # 찾을 수 있고, 그 사실은 화면 어디에도 안 적힌다(실측 2026-09-24).
    nav = client.get("/api/ontology/nav", headers=admin.headers).json()
    master = next(one for one in nav if one["label"] == "디지털 트윈 기준정보")
    assert {"시험 항목", "시뮬레이션 해석"} <= {one["label"] for one in master["items"]}
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


def test_연계에서_고치는_것은_부서뿐이다(client: TestClient, admin: Signed) -> None:
    """시험 항목이나 해석을 바꾸는 것은 **다른 연계**다.

    바꾸는 길을 두면 이미 매긴 평가가 엉뚱한 대상의 평가로 남고, 그 손실은 숫자에서만
    드러난다. 바꾸려면 해제하고 다시 등록한다 — 평가가 함께 사라진다는 것을 확인 문구가 말한다.
    """
    _setup(client, admin)
    subject = _make_object(
        client, admin, "sim_test_item", label=f"음향 시험 {uuid.uuid4().hex[:4]}"
    )
    agent = _make_object(
        client, admin, "sim_analysis", label=f"음향 해석 {uuid.uuid4().hex[:4]}"
    )
    made = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": subject["id"],
            "agent_id": agent["id"],
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    pair = made.json()["id"]

    other = client.post(
        "/api/workspaces",
        json={"slug": f"move{uuid.uuid4().hex[:6]}", "name": "옮긴 부서"},
        headers=admin.headers,
    )
    assert other.status_code == 201, other.text

    moved = client.patch(
        f"{DT}/pairs/{pair}",
        json={"workspace_slug": other.json()["slug"]},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["workspace_name"] == "옮긴 부서"

    # 대상 · 수단을 바꾸는 길은 없다 — 보내도 받지 않는다.
    refused = client.patch(
        f"{DT}/pairs/{pair}",
        json={"workspace_slug": other.json()["slug"], "agent_id": subject["id"]},
        headers=admin.headers,
    )
    assert refused.status_code == 200
    assert refused.json()["agent_id"] == agent["id"]


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


def test_목록이_사용_도구와_담당_부서를_함께_낸다(client: TestClient, admin: Signed) -> None:
    """「무엇으로 보나」 · 「누가 들고 있나」 는 목록에서 바로 읽혀야 한다.

    이름은 **플랫폼의 해석기**가 찾는다 — 부서처럼 다른 표를 비추는 타입은 객체 표에 행이
    없어서, 직접 찾으면 이름이 영영 비어 있고 그 사실은 화면에서 「—」 로만 보인다
    (실측 2026-09-24).
    """
    _setup(client, admin)
    subject = _make_object(
        client, admin, "sim_test_item", label=f"열 시험 {uuid.uuid4().hex[:4]}"
    )
    agent = _make_object(
        client, admin, "sim_analysis", label=f"열 해석 {uuid.uuid4().hex[:4]}"
    )
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
    row = made.json()
    # 이 시험 DB 에는 도구 카탈로그도 부서 타입도 없다 — **그래도 칸은 있고 비어 있다.**
    assert row["agent_tools"] == [] and row["agent_dept"] is None
    assert row["workspace_name"]


def test_조회는_부서로_가리지_않는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**전사 역량은 조직을 가로지르는 물음이다.**

    자기 부서 것만 보이면 「지금 어디까지 왔나」 에 아무도 답할 수 없다. 화면이 홈 부서로
    걸어 두었을 때 다른 부서에 등록한 연계가 사라졌고, 사람은 그것을 「저장이 안 됐다」 로
    읽었다(실측 2026-09-24). **고치는 것은 부서 멤버만**이라 권한은 그대로다.
    """
    _setup(client, admin)
    subject = _make_object(client, admin, "sim_test_item", label="진동 시험")
    agent = _make_object(client, admin, "sim_analysis", label="진동 해석")
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

    # 같은 부서 멤버가 아니어도 목록에는 뜬다.
    seen = client.get(f"{DT}/pairs", headers=member.headers)
    assert seen.status_code == 200
    assert made.json()["id"] in [one["id"] for one in seen.json()]


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


# --- 2단계: 평가 ------------------------------------------------------------


def _pair(client: TestClient, admin: Signed, subject: str, agent: str) -> str:
    _setup(client, admin)
    one = _make_object(client, admin, "sim_test_item", label=subject)
    two = _make_object(client, admin, "sim_analysis", label=agent)
    made = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": one["id"],
            "agent_id": two["id"],
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def test_가상검증률의_수준은_값이_정한다(client: TestClient, admin: Signed) -> None:
    """**사람이 수준을 고르지 않는다.**

    값과 수준을 따로 받으면 값을 고쳐도 수준이 안 따라오고, 그때 가상검증률이 둘이 된다.
    화면이 보낸 수준은 무시한다.
    """
    pair = _pair(client, admin, "낙하 시험", "낙하 구조 해석")
    got = client.put(
        f"{DT}/pairs/{pair}/assessments/accuracy",
        json={
            "value": 91,
            "rung": "trend",  # 엉뚱한 수준을 보내도
            "note": "25년 낙하 32건 비교, 파손 위치 일치율 91%",
            "evidence": {"compared_tests": 32, "error_pct": 6},
            "evidence_tier": "verified",
            "evidence_ref": "CAE-2026-0312 낙하 상관성 보고서",
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["value"] == 91
    assert got.json()["rung"] == "correlated"  # 문턱(90)이 정한다

    # 문턱 아래로 고치면 수준이 따라 내려온다.
    again = client.put(
        f"{DT}/pairs/{pair}/assessments/accuracy",
        json={
            "value": 72,
            "note": "재측정 — 일치율 72%",
            "evidence_tier": "checked",
            "evidence_ref": "결과 파일 확인",
        },
        headers=admin.headers,
    )
    assert again.json()["rung"] == "quantitative"


def test_근거가_없으면_저장되지_않는다(client: TestClient, admin: Signed) -> None:
    """수준만 남은 평가는 다음 사람이 확인할 수 없다 — 「누가 언젠가 그렇게 봤다」 가 된다."""
    pair = _pair(client, admin, "굽힘 시험", "굽힘 강성 해석")
    blank = client.put(
        f"{DT}/pairs/{pair}/assessments/scope",
        json={"rung": "basic", "note": "   ", "evidence_tier": "stated"},
        headers=admin.headers,
    )
    assert blank.status_code == 409
    assert "근거를 적어야" in blank.json()["error"]["message"]


def test_확인_검증은_근거_자료가_필요하다(client: TestClient, admin: Signed) -> None:
    """등급만 받고 자료를 안 받으면 「검증」 이 말뿐이 된다."""
    pair = _pair(client, admin, "방수 시험", "실링 압력 해석")
    got = client.put(
        f"{DT}/pairs/{pair}/assessments/scope",
        json={"rung": "basic", "note": "대표 모델에 적용 중", "evidence_tier": "verified"},
        headers=admin.headers,
    )
    assert got.status_code == 409
    assert "근거 자료가 필요합니다" in got.json()["error"]["message"]

    # 진술이면 자료 없이 저장된다 — 「무엇을 보고 매겼나」 가 이미 적혀 있다.
    ok = client.put(
        f"{DT}/pairs/{pair}/assessments/scope",
        json={"rung": "basic", "note": "대표 모델에 적용 중", "evidence_tier": "stated"},
        headers=admin.headers,
    )
    assert ok.status_code == 200 and ok.json()["rung"] == "basic"


def test_선택형과_매트릭스를_저장한다(client: TestClient, admin: Signed) -> None:
    """선택형은 **정의 순서**로 담는다.

    고른 순서대로 두면 같은 평가가 화면마다 다르게 보인다.
    """
    pair = _pair(client, admin, "발열 시험", "열 해석")
    got = client.put(
        f"{DT}/pairs/{pair}/assessments/automation",
        json={
            "rungs": ["report", "pre", "run"],
            "note": "전처리·실행·보고 자동화",
            "evidence": {"hours_per_run": {"pre": 2, "run": 8}},
            "evidence_tier": "checked",
            "evidence_ref": "스크립트 저장소",
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["rungs"] == ["pre", "run", "report"]


def test_모델링_수준은_셈이_접는다(client: TestClient, admin: Signed) -> None:
    """**바탕 토글 + 불량 유형별 재현**이고 수준은 사람이 고르지 않는다.

    불량 유형은 **시험 항목이 든 목록**이 기준이다 — 지운 유형의 기록은 안 센다.
    """
    _setup(client, admin)
    subject = _make_object(
        client,
        admin,
        "sim_test_item",
        label=f"낙하 시험 {uuid.uuid4().hex[:4]}",
        properties={"defect_types": ["글라스 크랙", "프레임 찍힘"]},
    )
    agent = _make_object(
        client, admin, "sim_analysis", label=f"낙하 해석 {uuid.uuid4().hex[:4]}"
    )
    made = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": subject["id"],
            "agent_id": agent["id"],
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    pair = made.json()["id"]

    first = client.put(
        f"{DT}/pairs/{pair}/assessments/modeling",
        json={
            "rungs": ["geometry", "performance", "없는것"],
            "note": "형상 · 거동 일치 확인",
            "evidence_tier": "stated",
        },
        headers=admin.headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["rungs"] == ["geometry", "performance"]
    assert first.json()["rung"] == "performance"

    second = client.put(
        f"{DT}/pairs/{pair}/assessments/modeling",
        json={
            "rungs": ["geometry", "performance"],
            "defects": {
                "글라스 크랙": {"test": "2026-07", "없는열": "x"},
                "프레임 찍힘": {"test": "2026-08"},
            },
            "note": "두 유형 모두 시험 불량 재현",
            "evidence_tier": "stated",
        },
        headers=admin.headers,
    )
    assert second.json()["rung"] == "test_all"
    assert second.json()["defects"] == {
        "글라스 크랙": {"test": "2026-07"},
        "프레임 찍힘": {"test": "2026-08"},
    }

    third = client.put(
        f"{DT}/pairs/{pair}/assessments/modeling",
        json={
            "rungs": [],
            "defects": {"글라스 크랙": {"market": "2026-09"}},
            "note": "시장 불량까지 재현",
            "evidence_tier": "stated",
        },
        headers=admin.headers,
    )
    assert third.json()["rung"] == "market"


def test_평가가_바뀌면_이력이_남는다(client: TestClient, admin: Signed) -> None:
    """**담당자가 본다** — 감사 기록은 시스템 관리자만 읽는다. 「지난번엔 왜 이렇게
    적었나」 가 다음 평가의 근거다."""
    pair = _pair(client, admin, "진동 시험", "랜덤 진동 해석")
    for value, note in ((60, "1차 비교"), (85, "메시 개선 후 재비교")):
        client.put(
            f"{DT}/pairs/{pair}/assessments/accuracy",
            json={"value": value, "note": note, "evidence_tier": "stated"},
            headers=admin.headers,
        )
    rows = client.get(f"{DT}/pairs/{pair}/history", headers=admin.headers).json()
    assert [one["snapshot"]["value"] for one in rows] == [85, 60]
    assert rows[0]["axis_label"] == "가상검증률"


def test_연계를_해제하면_평가도_간다(client: TestClient, admin: Signed) -> None:
    """확인 문구가 그 사실을 말한다 — 모르고 해제하면 채운 자료가 사라진다."""
    pair = _pair(client, admin, "키 내구", "돔 스위치 피로 해석")
    client.put(
        f"{DT}/pairs/{pair}/assessments/scope",
        json={"rung": "issue", "note": "이슈 대응 단계", "evidence_tier": "stated"},
        headers=admin.headers,
    )
    assert len(client.get(f"{DT}/pairs/{pair}/assessments", headers=admin.headers).json()) == 1
    assert client.delete(f"{DT}/pairs/{pair}", headers=admin.headers).status_code == 204
    assert (
        client.get(f"{DT}/pairs/{pair}/assessments", headers=admin.headers).status_code == 404
    )


def test_평가_완료율(client: TestClient, admin: Signed) -> None:
    """축마다 따로 센다 — 「가상검증률은 1/2, 자동화는 0/2」 가 할 일을 가른다."""
    first = _pair(client, admin, "안테나 성능", "안테나 전자기 해석")
    _pair(client, admin, "스피커 음향", "음향 해석")
    client.put(
        f"{DT}/pairs/{first}/assessments/accuracy",
        json={"value": 80, "note": "비교 12건", "evidence_tier": "stated"},
        headers=admin.headers,
    )
    body = client.get(f"{DT}/coverage", headers=admin.headers).json()
    by = {one["axis"]: one for one in body["axes"]}
    assert body["pairs"] == 2
    assert by["accuracy"]["assessed"] == 1 and by["accuracy"]["ratio"] == 0.5
    assert by["automation"]["assessed"] == 0


def test_부서_멤버만_평가한다(client: TestClient, admin: Signed, member: Signed) -> None:
    """보는 것과 고치는 것은 다른 물음이다 — **목록은 전사, 편집은 그 부서 멤버만.**

    남의 부서 자료를 고치면 그 부서는 자기 숫자를 설명할 수 없게 된다.
    """
    _setup(client, admin)
    other = client.post(
        "/api/workspaces",
        json={"slug": f"other{uuid.uuid4().hex[:6]}", "name": "다른 부서"},
        headers=admin.headers,
    )
    assert other.status_code == 201, other.text
    one = _make_object(
        client, admin, "sim_test_item", label=f"충전 발열 {uuid.uuid4().hex[:4]}"
    )
    two = _make_object(
        client, admin, "sim_analysis", label=f"충전 열 해석 {uuid.uuid4().hex[:4]}"
    )
    made = client.post(
        f"{DT}/pairs",
        json={
            "subject_id": one["id"],
            "agent_id": two["id"],
            "workspace_slug": other.json()["slug"],
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    # 목록에는 보인다(전사 물음이다).
    assert made.json()["id"] in [
        row["id"] for row in client.get(f"{DT}/pairs", headers=member.headers).json()
    ]
    # 고치는 것은 막힌다.
    denied = client.put(
        f"{DT}/pairs/{made.json()['id']}/assessments/scope",
        json={"rung": "issue", "note": "확인", "evidence_tier": "stated"},
        headers=member.headers,
    )
    assert denied.status_code == 403
