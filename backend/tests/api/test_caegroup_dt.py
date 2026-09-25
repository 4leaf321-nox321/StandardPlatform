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
    assert "evidence_tiers" not in body  # 근거는 글 하나다(등급 · 자료 칸을 걷었다)


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
        },
        headers=admin.headers,
    )
    assert again.json()["rung"] == "quantitative"


def test_근거가_없으면_저장되지_않는다(client: TestClient, admin: Signed) -> None:
    """수준만 남은 평가는 다음 사람이 확인할 수 없다 — 「누가 언젠가 그렇게 봤다」 가 된다."""
    pair = _pair(client, admin, "굽힘 시험", "굽힘 강성 해석")
    blank = client.put(
        f"{DT}/pairs/{pair}/assessments/scope",
        json={"rung": "basic", "note": "   "},
        headers=admin.headers,
    )
    assert blank.status_code == 409
    assert "근거를 적어야" in blank.json()["error"]["message"]


def test_근거는_글_하나다(client: TestClient, admin: Signed) -> None:
    """등급 · 자료 칸을 두었다가 걷었다(2026-09-24).

    칸이 늘수록 채우는 사람이 줄고, 안 채운 칸은 「모름」 과 구별되지 않는다 — 무엇을 보고
    매겼는지는 근거 글에 적는다. **보내도 받지 않는다.**
    """
    pair = _pair(client, admin, "방수 시험", "실링 압력 해석")
    got = client.put(
        f"{DT}/pairs/{pair}/assessments/scope",
        json={
            "rung": "basic",
            "note": "대표 모델에 적용 중 — 25년 상관성 보고서 확인",
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert "evidence_tier" not in got.json()
    assert "evidence_ref" not in got.json()

    defs = client.get(f"{DT}/defs", headers=admin.headers).json()
    assert "evidence_tiers" not in defs


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
            json={"value": value, "note": note},
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
        json={"rung": "issue", "note": "이슈 대응 단계"},
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
        json={"value": 80, "note": "비교 12건"},
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
        json={"rung": "issue", "note": "확인"},
        headers=member.headers,
    )
    assert denied.status_code == 403


def test_목록에서_여럿을_한_번에_옮기고_해제한다(client: TestClient, admin: Signed) -> None:
    """목록에서 서른 건을 고른 사람에게 서른 번을 누르게 하지 않는다.

    다만 권한은 **건마다** 본다 — 「여럿이라서 한 번에 통과」 가 되면 그 예외가 곧 규칙이 된다.
    """
    _setup(client, admin)
    made: list[str] = []
    for index in range(3):
        subject = _make_object(
            client, admin, "sim_test_item", label=f"묶음 시험{index} {uuid.uuid4().hex[:4]}"
        )
        agent = _make_object(
            client, admin, "sim_analysis", label=f"묶음 해석{index} {uuid.uuid4().hex[:4]}"
        )
        got = client.post(
            f"{DT}/pairs",
            json={
                "subject_id": subject["id"],
                "agent_id": agent["id"],
                "workspace_slug": admin.workspace,
            },
            headers=admin.headers,
        )
        made.append(got.json()["id"])

    # 매긴 축 수가 목록에 함께 온다 — 「어디까지 채웠나」 가 바로 읽히게.
    client.put(
        f"{DT}/pairs/{made[0]}/assessments/scope",
        json={"rung": "issue", "note": "이슈 대응"},
        headers=admin.headers,
    )
    rows = {one["id"]: one for one in client.get(f"{DT}/pairs", headers=admin.headers).json()}
    assert rows[made[0]]["assessed"] == 1 and rows[made[1]]["assessed"] == 0

    other = client.post(
        "/api/workspaces",
        json={"slug": f"bulk{uuid.uuid4().hex[:6]}", "name": "일괄 부서"},
        headers=admin.headers,
    )
    moved = client.post(
        f"{DT}/pairs/bulk-move",
        json={"ids": made[:2], "workspace_slug": other.json()["slug"]},
        headers=admin.headers,
    )
    assert moved.status_code == 200 and moved.json()["changed"] == 2

    gone = client.post(f"{DT}/pairs/bulk-unlink", json={"ids": made}, headers=admin.headers)
    assert gone.status_code == 200 and gone.json()["changed"] == 3
    left = {one["id"] for one in client.get(f"{DT}/pairs", headers=admin.headers).json()}
    assert not (set(made) & left)


def test_대시보드는_타일과_최근_변경을_한_번에_낸다(client: TestClient, admin: Signed) -> None:
    """**분포와 벽은 같은 자료에서 나온다.**

    서버가 분포를 따로 세어 내려 주면 둘이 갈릴 수 있고, 그때 어느 쪽이 맞는지 아무도
    답할 수 없다 — 그래서 타일 목록 하나를 주고 화면이 그것으로 둘을 그린다.
    """
    pair = _pair(
        client, admin, f"벽 시험 {uuid.uuid4().hex[:4]}", f"벽 해석 {uuid.uuid4().hex[:4]}"
    )
    client.put(
        f"{DT}/pairs/{pair}/assessments/accuracy",
        json={"value": 95, "note": "비교 20건, 일치율 95%"},
        headers=admin.headers,
    )
    body = client.get(f"{DT}/board", headers=admin.headers).json()
    tile = next(one for one in body["tiles"] if one["id"] == pair)
    # 묶음은 담당 부서이고, 없으면 소속 부서다 — 「누가 들고 있나」 로 묶어야 벽이
    # 조직의 그림이 된다.
    assert tile["group"]
    assert tile["levels"]["accuracy"]["rung"] == "correlated"
    assert tile["levels"]["accuracy"]["value"] == 95
    assert "automation" not in tile["levels"]  # 안 매긴 축은 없다(미평가와 0 을 가른다)

    recent = next(one for one in body["recent"] if one["pair_id"] == pair)
    assert recent["axis_label"] == "가상검증률"
    assert "95%" in recent["note"]


# --- 4단계: 인력 · 인프라 --------------------------------------------------


def test_한_사람은_하나이고_몫은_담당에_갈린다(client: TestClient, admin: Signed) -> None:
    """**투입률을 받지 않는다.**

    퍼센트를 사람이 적으면 정의가 흔들리고 합이 사람 수를 넘는다 — 셈은 서버가 한다.
    담당이 셋이면 각 1/3, 조사 밖 업무가 있으면 n+1 로 나눈다(없는 일까지 넣지 않는다).
    """
    _setup(client, admin)
    agents = [
        _make_object(
            client, admin, "sim_analysis", label=f"해석{index} {uuid.uuid4().hex[:4]}"
        )["id"]
        for index in range(3)
    ]
    made = client.post(
        f"{DT}/staff",
        json={
            "workspace_slug": admin.workspace,
            "name": "홍길동",
            "agents": agents,
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["share"] == round(1 / 3, 4)
    assert made.json()["fte"] == 1.0

    # 조사 밖 업무가 있으면 이 조사에는 3/4 만 잡힌다.
    moved = client.put(
        f"{DT}/staff/{made.json()['id']}",
        json={
            "workspace_slug": admin.workspace,
            "name": "홍길동",
            "agents": agents,
            "outside": True,
        },
        headers=admin.headers,
    )
    assert moved.json()["share"] == 0.25
    assert moved.json()["fte"] == 0.75

    summary = client.get(f"{DT}/staff/summary", headers=admin.headers).json()
    assert summary["head_count"] == 1
    # **합이 사람 수를 넘지 않는다.**
    assert summary["fte"] <= summary["head_count"]
    assert len(summary["by_agent"]) == 3
    assert summary["by_agent"][0]["fte"] == 0.25


def test_실명은_권한_있는_사람에게만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """표에 서는 것은 가명(담당 A)이다.

    전사에 실명을 열면 이 표는 「누가 느린가」 로 읽히고, 그때 부서는 자료를 방어적으로
    적는다 — 사람을 세는 자리이지 사람을 평가하는 자리가 아니다.
    """
    _setup(client, admin)
    other = client.post(
        "/api/workspaces",
        json={"slug": f"staff{uuid.uuid4().hex[:6]}", "name": "다른 팀"},
        headers=admin.headers,
    )
    client.post(
        f"{DT}/staff",
        json={"workspace_slug": other.json()["slug"], "name": "김해석"},
        headers=admin.headers,
    )
    rows = client.get(f"{DT}/staff", headers=member.headers).json()
    theirs = [one for one in rows if one["workspace_name"] == "다른 팀"]
    assert theirs, rows
    assert theirs[0]["alias"] == "담당 A"
    assert theirs[0]["name"] is None  # 남의 부서 사람의 실명은 안 온다

    mine = client.get(f"{DT}/staff", headers=admin.headers).json()
    seen = next(one for one in mine if one["workspace_name"] == "다른 팀")
    assert seen["name"] == "김해석"


def test_공유_자원은_전사에서_한_번만_센다(client: TestClient, admin: Signed) -> None:
    """부서마다 적힌 공유 라이선스를 그대로 더하면 전사 합이 실제보다 커지고, 그 숫자로
    투자를 판단하면 이미 있는 것을 또 산다."""
    _setup(client, admin)
    other = client.post(
        "/api/workspaces",
        json={"slug": f"cap{uuid.uuid4().hex[:6]}", "name": "옆 팀"},
        headers=admin.headers,
    )
    for slug in (admin.workspace, other.json()["slug"]):
        got = client.put(
            f"{DT}/capacity?workspace={slug}",
            json={
                "sw": [
                    {"name": "전사 공유 솔버", "quantity": 10, "unit": "copy", "shared": True},
                    {"name": "부서 전용 툴", "quantity": 2, "unit": "copy", "shared": False},
                ],
                "hw": [
                    {
                        "name": "공용 클러스터",
                        "cpu_cores": 256,
                        "ram_gb": 1024,
                        # **GPU 는 글이다.** 숫자로 강제했더니 표본의 「A100 4장」 이
                        # 합계에서 500 을 냈다(2026-09-25).
                        "gpu": "A100 4장",
                        "shared": True,
                    }
                ],
                "material_types": 30,
                "has_process_std": True,
            },
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text

    body = client.get(f"{DT}/capacity/summary", headers=admin.headers).json()
    by_name = {one["name"]: one["quantity"] for one in body["sw"]}
    assert by_name["전사 공유 솔버"] == 10  # 두 부서가 적었어도 한 번
    assert by_name["부서 전용 툴"] == 4  # 2 + 2
    assert body["hw"]["cpu_cores"] == 256  # 공용 클러스터도 한 번
    # GPU 는 개수를 더하지 않고 **사양이 적힌 자원 수**를 센다.
    assert body["hw"]["gpu_units"] == 1
    assert body["material_types"] == 60 and body["process_std"] == 2


# --- 5단계: 일괄 입력 -------------------------------------------------------


def test_현재값을_표로_내려_주고_고쳐서_한_번에_받는다(
    client: TestClient, admin: Signed
) -> None:
    """**한 줄씩 창을 여는 길만 있으면 아무도 최신으로 유지하지 않는다.**

    현재값을 표로 내려 주고, 고친 것을 한 번에 되돌려 받는다. 줄마다 결과를 준다 —
    한 줄이 틀려도 나머지는 저장한다.
    """
    first = _pair(
        client, admin, f"낙하 {uuid.uuid4().hex[:4]}", f"낙하 해석 {uuid.uuid4().hex[:4]}"
    )
    second = _pair(
        client, admin, f"굽힘 {uuid.uuid4().hex[:4]}", f"굽힘 해석 {uuid.uuid4().hex[:4]}"
    )
    client.put(
        f"{DT}/pairs/{first}/assessments/accuracy",
        json={"value": 70, "note": "1차 비교"},
        headers=admin.headers,
    )

    sheet = client.get(f"{DT}/assessments/sheet?axis=accuracy", headers=admin.headers).json()
    assert sheet["axis_label"] == "가상검증률" and sheet["kind"] == "value"
    rows = {one["pair_id"]: one for one in sheet["rows"]}
    # 현재값이 채워져 온다 — 아직 안 매긴 줄은 비어 있다(0 이 아니다).
    assert rows[first]["value"] == 70 and rows[first]["note"] == "1차 비교"
    assert rows[second]["value"] is None and rows[second]["rung"] == ""

    saved = client.put(
        f"{DT}/assessments/bulk",
        json={
            "axis": "accuracy",
            "rows": [
                {"pair_id": first, "value": "93", "note": "재비교 — 일치율 93%"},
                # **이름으로도 찾는다** — 엑셀에서 붙여 넣은 표에는 id 가 없다.
                {
                    "subject_label": rows[second]["subject_label"],
                    "agent_label": rows[second]["agent_label"],
                    "value": "80",
                    "note": "첫 비교 12건",
                },
                {
                    "subject_label": "없는 시험",
                    "agent_label": "없는 해석",
                    "value": "50",
                    "note": "x",
                },
                {"pair_id": first, "value": "", "note": "값이 없으면 건너뛴다"},
                {"pair_id": first, "value": "구십", "note": "숫자가 아니다"},
            ],
        },
        headers=admin.headers,
    )
    assert saved.status_code == 200, saved.text
    got = saved.json()
    assert [one["status"] for one in got] == ["ok", "ok", "error", "skipped", "error"]
    assert "연계를 찾을 수 없습니다" in got[2]["message"]
    assert "숫자가 아닙니다" in got[4]["message"]

    after = client.get(f"{DT}/assessments/sheet?axis=accuracy", headers=admin.headers).json()
    done = {one["pair_id"]: one for one in after["rows"]}
    assert done[first]["value"] == 93 and done[first]["rung"] == "현상 재현"
    assert done[second]["value"] == 80 and done[second]["rung"] == "우열 판정"


def test_선택형도_이름으로_일괄_저장된다(client: TestClient, admin: Signed) -> None:
    """엑셀에는 **이름**이 적힌다 — key 를 적게 하면 아무도 못 채운다."""
    pair = _pair(
        client, admin, f"진동 {uuid.uuid4().hex[:4]}", f"진동 해석 {uuid.uuid4().hex[:4]}"
    )
    got = client.put(
        f"{DT}/assessments/bulk",
        json={
            "axis": "automation",
            "rows": [
                {
                    "pair_id": pair,
                    "rungs": "전처리 자동화 · 실행 자동화",
                    "note": "템플릿 적용",
                },
                {"pair_id": pair, "rungs": "없는 항목", "note": "x"},
            ],
        },
        headers=admin.headers,
    )
    assert [one["status"] for one in got.json()] == ["ok", "error"]
    assert "모르는 항목입니다" in got.json()[1]["message"]
    rows = client.get(f"{DT}/assessments/sheet?axis=automation", headers=admin.headers).json()
    mine = next(one for one in rows["rows"] if one["pair_id"] == pair)
    assert mine["rungs"] == ["전처리 자동화", "실행 자동화"]


def test_현재값_표를_파일로_내려받는다(client: TestClient, admin: Signed) -> None:
    """엑셀에서 고치는 것이 가장 빠른 사람이 많다. 열 순서가 붙여넣기와 같아야 한다."""
    _pair(client, admin, f"방수 {uuid.uuid4().hex[:4]}", f"실링 해석 {uuid.uuid4().hex[:4]}")
    for kind in ("csv", "xlsx"):
        got = client.get(
            f"{DT}/assessments/sheet/export?axis=accuracy&format={kind}", headers=admin.headers
        )
        assert got.status_code == 200, got.text
        assert len(got.content) > 100
    csv = client.get(
        f"{DT}/assessments/sheet/export?axis=accuracy&format=csv", headers=admin.headers
    ).content.decode("utf-8-sig")
    header = csv.splitlines()[0]
    assert header.split(",")[:2] == ["시험 항목", "시뮬레이션 해석"]
    assert "가상검증률" in header and "근거" in header


def test_인력도_표로_받아_한_번에_고친다(client: TestClient, admin: Signed) -> None:
    """**이름과 부서로 그 줄을 찾는다.** 새 이름이면 새로 만든다.

    사람마다 창을 여는 길만 있으면 조사가 끝나지 않는다 — 스무 명이면 스무 번이다.
    """
    _setup(client, admin)
    agent = _make_object(
        client, admin, "sim_analysis", label=f"열 해석 {uuid.uuid4().hex[:4]}"
    )
    name = f"박해석{uuid.uuid4().hex[:4]}"
    # 표에는 **부서 이름**이 적힌다(slug 가 아니다) — 사람이 읽고 적는 값이다.
    dept = next(
        one["name"]
        for one in client.get("/api/workspaces", headers=admin.headers).json()
        if one["slug"] == admin.workspace
    )
    got = client.put(
        f"{DT}/staff/bulk",
        json={
            "rows": [
                {
                    "name": name,
                    "workspace_name": dept,
                    "agents": agent["label"],
                    "outside": "예",
                    "note": "표에서 넣음",
                },
                {"name": "", "workspace_name": dept},
                {"name": "김없음", "workspace_name": "없는 부서"},
                {
                    "name": f"최해석{uuid.uuid4().hex[:4]}",
                    "workspace_name": dept,
                    "agents": "없는 해석",
                },
            ]
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert [one["status"] for one in got.json()] == ["ok", "skipped", "error", "error"]
    assert "부서를 찾을 수 없습니다" in got.json()[2]["message"]
    assert "모르는 해석입니다" in got.json()[3]["message"]

    sheet = client.get(f"{DT}/staff/sheet", headers=admin.headers).json()
    mine = next(one for one in sheet if one["name"] == name)
    assert mine["agents"] == agent["label"] and mine["outside"] == "예"

    # 같은 이름 · 부서로 다시 보내면 **고친다**(새로 만들지 않는다).
    again = client.put(
        f"{DT}/staff/bulk",
        json={
            "rows": [
                {
                    "name": name,
                    "workspace_name": dept,
                    "agents": agent["label"],
                    "note": "고침",
                }
            ]
        },
        headers=admin.headers,
    )
    assert again.json()[0]["status"] == "ok"
    rows = [
        one
        for one in client.get(f"{DT}/staff/sheet", headers=admin.headers).json()
        if one["name"] == name
    ]
    assert len(rows) == 1 and rows[0]["note"] == "고침" and rows[0]["outside"] == ""


def test_안_채운_것이_홈의_남은_일에_오른다(client: TestClient, admin: Signed) -> None:
    """**대시보드를 열어야만 보이는 자료는 안 채워진다.**

    0 건인 항목은 안 낸다(레지스트리가 거른다) — 다 0 인 목록을 매일 보면 사람은 그 자리를
    아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다.
    """
    pair = _pair(
        client, admin, f"음향 {uuid.uuid4().hex[:4]}", f"음향 해석 {uuid.uuid4().hex[:4]}"
    )
    client.put(
        f"{DT}/pairs/{pair}/assessments/accuracy",
        json={"value": 60, "note": "첫 비교"},
        headers=admin.headers,
    )
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    mine = {one["label"]: one for one in home if "디지털 트윈" in one["label"]}
    # 가상검증률은 매겼고 자동화는 안 매겼다 — 안 매긴 것만 뜬다.
    assert "디지털 트윈 — 자동화 미평가" in mine
    assert mine["디지털 트윈 — 자동화 미평가"]["link"] == "/ext/caegroup/dt/bulk"
