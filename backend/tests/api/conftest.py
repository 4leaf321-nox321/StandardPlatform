"""API 시험이 쓰는 준비물.

**진짜 앱을 부른다.** 서비스 함수를 직접 부르면 라우터·의존성·권한 판정이 통째로
빠지는데, 실제로 깨지는 자리는 대개 거기다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.auth import security
from app.modules.workspaces.models import Workspace, WorkspaceMember

PASSWORD = "test-account-password"


@dataclass(frozen=True)
class Signed:
    """로그인한 사람. 헤더를 매번 손으로 만들지 않게 한다."""

    email: str
    token: str
    workspace: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _login(client: TestClient, email: str) -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _make_user(
    db: Session,
    workspace: Workspace,
    *,
    label: str,
    is_system_admin: bool,
    role: str,
) -> User:
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.local"
    user = User(
        email=email,
        password_hash=security.hash_password(PASSWORD),
        display_name=label,
        status="active",
        is_system_admin=is_system_admin,
        home_workspace_id=workspace.id,
    )
    db.add(user)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))
    db.commit()
    return user


@pytest.fixture
def workspace(db: Session) -> Workspace:
    row = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="시험팀")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def admin(client: TestClient, db: Session, workspace: Workspace) -> Signed:
    """시스템 관리자 한 명. 첫 관리자는 설치 시드처럼 직접 만든다."""
    user = _make_user(db, workspace, label="admin", is_system_admin=True, role="manager")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)


@pytest.fixture
def manager(client: TestClient, db: Session, workspace: Workspace) -> Signed:
    """시스템 관리자가 **아닌** 부서 관리자.

    부서 소유 자산(첨부·객체)을 만들려면 그 부서의 관리자여야 한다
    (`resolve_owner_workspace`). 시스템 관리자로만 시험하면 **그 문턱이 실제로
    있는지**를 아무도 안 보게 된다.
    """
    user = _make_user(db, workspace, label="manager", is_system_admin=False, role="manager")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)


@pytest.fixture
def member(client: TestClient, db: Session, workspace: Workspace) -> Signed:
    """같은 부서의 평범한 멤버. **권한 시험에는 관리자 아닌 사람이 필요하다** —
    관리자만으로 도는 시험은 무엇도 막지 못한다."""
    user = _make_user(db, workspace, label="member", is_system_admin=False, role="member")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)
