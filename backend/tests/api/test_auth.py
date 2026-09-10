"""인증 — 로그인, 세션 회전, 비밀번호 변경, 개인 토큰.

이 틀에서 가장 먼저 깨지면 안 되는 것이다. 여기가 무너지면 나머지 화면은
아무도 못 연다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.branding import ERROR_PREFIX
from app.modules.accounts.models import User
from app.modules.auth import security
from app.modules.workspaces.models import Workspace
from tests.api.conftest import PASSWORD, Signed


def test_로그인하면_소속과_역할이_함께_온다(client: TestClient, admin: Signed) -> None:
    """**화면이 사이드바를 그리려면 이것이 필요하다.**

    소속을 따로 한 번 더 물어야 하면 로그인 직후 화면이 한 번 비었다 채워지고,
    사람은 그 깜빡임을 고장으로 읽는다.
    """
    response = client.get("/api/auth/me", headers=admin.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_system_admin"] is True
    assert body["home_workspace_slug"] == admin.workspace
    assert [one["slug"] for one in body["memberships"]] == [admin.workspace]
    # 경로가 있어야 같은 이름의 팀이 둘일 때 구별된다.
    assert body["memberships"][0]["path"]


def test_틀린_비밀번호는_사유를_안_알려_준다(client: TestClient, admin: Signed) -> None:
    """계정이 있는지 없는지가 응답에서 새면 안 된다 — 둘 다 같은 말을 한다."""
    wrong = client.post(
        "/api/auth/login", json={"email": admin.email, "password": "not-the-password"}
    )
    missing = client.post(
        "/api/auth/login", json={"email": "nobody@example.local", "password": "x"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]


def test_승인_대기_계정은_사유를_말한다(
    client: TestClient, db: Session, workspace: Workspace
) -> None:
    """**"왜 안 되는지" 를 구분해 준다.** 승인 대기와 정지는 사람이 할 일이 다르고,
    "비활성 계정" 이라고만 하면 관리자에게 무엇을 요청해야 할지 알 수 없다."""
    user = User(
        email="waiting@example.local",
        password_hash=security.hash_password(PASSWORD),
        display_name="대기",
        status="pending",
        requested_workspace_id=workspace.id,
    )
    db.add(user)
    db.commit()

    response = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 403
    assert "승인" in response.json()["error"]["message"]


def test_오류는_봉투와_요청id_를_싣는다(client: TestClient) -> None:
    """**사용자가 전달할 수 있는 문자열이 있어야 로그에서 그 요청을 찾는다.**

    화면마다 다르게 만들면 어떤 화면에서는 코드가 안 보여서 재현이 막힌다.
    """
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    error = response.json()["error"]
    # **접두사를 손으로 박지 않는다.** 포크해서 ERROR_PREFIX 를 바꾸면 올바른
    # 코드인데도 이 시험이 깨지고, 사람은 자기가 뭘 잘못했나 찾게 된다.
    assert error["code"].startswith(f"{ERROR_PREFIX}-")
    assert error["request_id"]
    # 헤더로도 나간다 — 응답 본문을 못 읽는 상황에서도 끈이 남아야 한다.
    assert response.headers["X-Request-ID"] == error["request_id"]


def test_세션은_한_번_쓰면_회전한다(client: TestClient, admin: Signed) -> None:
    """회전하지 않으면 탈취된 토큰이 만료까지 유효하다."""
    login = client.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD})
    assert login.status_code == 200, login.text

    first = client.post("/api/auth/refresh")
    assert first.status_code == 200, first.text
    # 쿠키가 바뀌었다 = 새 refresh 가 발급됐다.
    assert first.json()["access_token"]

    second = client.post("/api/auth/refresh")
    assert second.status_code == 200, second.text


def test_비밀번호를_바꾸면_모든_세션이_끊긴다(client: TestClient, member: Signed) -> None:
    """바꾼 이유가 유출일 수 있다. **다른 기기의 로그인이 남아 있으면** 바꾼 의미가
    없다."""
    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "brand-new-password"},
        headers=member.headers,
    )
    assert changed.status_code == 204, changed.text

    # 쿠키가 지워졌으므로 갱신이 안 된다.
    assert client.post("/api/auth/refresh").status_code == 401


def test_같은_비밀번호로는_못_바꾼다(client: TestClient, member: Signed) -> None:
    """길이 하한 대신 이 검사를 둔다 — 지키려던 것은 길이가 아니다."""
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=member.headers,
    )
    assert response.status_code == 400


def test_개인_토큰은_읽기만_기본이다(client: TestClient, admin: Signed) -> None:
    """**기본값이 전권이면 「일단 만들고 나중에 좁히자」 가 되고, 나중은 오지 않는다.**"""
    created = client.post(
        "/api/auth/tokens", json={"name": "연계 스크립트"}, headers=admin.headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["pat"]["scopes"] == ["read"]
    assert body["token"].startswith(security.PAT_PREFIX)

    # 발급된 토큰으로 읽기는 된다.
    with_pat = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/auth/me", headers=with_pat).status_code == 200


def test_개인_토큰으로는_아무_경로나_못_고친다(client: TestClient, admin: Signed) -> None:
    """**모르는 것은 막는다.** 등록되지 않은 쓰기 경로는 어떤 범위로도 안 열린다 —
    새 엔드포인트가 생길 때마다 자동으로 열리면 그것을 알아채는 사람이 없다."""
    created = client.post(
        "/api/auth/tokens", json={"name": "쓰기 시도"}, headers=admin.headers
    )
    token = created.json()["token"]

    response = client.post(
        "/api/workspaces",
        json={"slug": "sneaky", "name": "몰래"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "개인 토큰" in response.json()["error"]["message"]


def test_모르는_범위는_발급을_거절한다(client: TestClient, admin: Signed) -> None:
    response = client.post(
        "/api/auth/tokens",
        json={"name": "이상한 범위", "scopes": ["read", "everything:write"]},
        headers=admin.headers,
    )
    assert response.status_code == 400
    assert "everything:write" in response.json()["error"]["message"]


def test_아는_범위_목록은_서버가_준다(client: TestClient, admin: Signed) -> None:
    """화면이 손 목록을 들면 반드시 서버와 어긋난다."""
    response = client.get("/api/auth/token-scopes", headers=admin.headers)
    assert response.status_code == 200
    assert "read" in response.json()["scopes"]
