"""API 시험이 쓰는 준비물.

**진짜 앱을 부른다.** 서비스 함수를 직접 부르면 라우터·의존성·권한 판정이 통째로
빠지는데, 실제로 깨지는 자리는 대개 거기다.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

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


def maintenance_counts(client: TestClient, who: Signed) -> dict[str, int]:
    """홈 「남은 일」 을 {열쇠: 수} 로. **시험 DB 를 스위트가 함께 쓰므로 차이로 본다** —
    남이 남긴 줄이 이미 있을 수 있다."""
    got = client.get("/api/server/maintenance", headers=who.headers)
    # **봉투가 오면 여기서 말한다.** 안 그러면 「문자열 인덱스」 라는 엉뚱한 오류가 나고,
    # 진짜 원인(어느 제공자가 터졌나)은 아무 데도 안 적힌다.
    assert got.status_code == 200, got.text
    return {one["key"]: one["count"] for one in got.json()}


def notifications_of(client: TestClient, who: Signed, kind: str) -> list[dict[str, Any]]:
    got = client.get("/api/notifications", headers=who.headers)
    assert got.status_code == 200, got.text
    body: Any = got.json()
    rows: list[dict[str, Any]] = body["items"] if isinstance(body, dict) else body
    return [one for one in rows if one["kind"] == kind]


def finish_job(client: TestClient, who: Signed, job: dict[str, Any]) -> dict[str, Any]:
    """워커를 **이 프로세스에서** 돌려 그 작업이 끝날 때까지. 시험은 워커 프로세스를 안
    띄운다 — 같은 코드를 같은 DB 로 부르면 그것이 곧 워커다. 남이 넣은 작업이 앞에 있어도
    집어 준다."""
    from app.modules.jobs import services as job_services

    for _ in range(50):
        got = client.get(f"/api/jobs/{job['id']}", headers=who.headers)
        assert got.status_code == 200, got.text
        current: dict[str, Any] = got.json()
        if current["status"] in ("done", "failed", "cancelled"):
            return current
        if not job_services.process_one("test-worker"):
            break
    got = client.get(f"/api/jobs/{job['id']}", headers=who.headers)
    return dict(got.json())


def import_file(
    client: TestClient,
    who: Signed,
    type_slug: str,
    text: str,
    *,
    apply: bool = False,
    path: str = "import",
    name: str = "rows.csv",
    workspace_slug: str | None = None,
) -> dict[str, Any]:
    """일괄 입력을 **끝까지** — 올리고, 워커가 계획을 세우고, `apply` 면 적용까지.
    돌아오는 것은 옛 동기 응답과 같은 계획 표(`ImportPlanOut` 모양)라 시험이 그대로 읽는다."""
    import io

    data = {"workspace_slug": workspace_slug or who.workspace}
    response = client.post(
        f"/api/objects/{type_slug}/{path}",
        files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/csv")},
        data=data,
        headers=who.headers,
    )
    assert response.status_code == 202, response.text
    planned = finish_job(client, who, response.json())
    assert planned["status"] == "done", planned
    plan = dict(planned["result"])
    if not apply:
        return plan
    if plan["errors"] or plan["counts"]["error"]:
        # 오류가 있는 계획은 적용 작업 자체가 안 만들어진다(409) — 옛 동기 응답의
        # 「applied: false」 와 같은 뜻이라 계획을 그대로 돌려준다.
        return plan
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=who.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, who, applied.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def export_file(
    client: TestClient, who: Signed, path: str, params: dict[str, Any] | None = None
) -> Any:
    """내보내기를 **끝까지** — 작업을 넣고, 워커가 파일을 만들고, 그 파일을 받는다.
    돌아오는 것은 받은 응답이라 옛 동기 응답처럼 `.text` · `.json()` 으로 읽는다."""
    started = client.post(f"/api/objects/{path}", params=params, headers=who.headers)
    assert started.status_code == 202, started.text
    done = finish_job(client, who, started.json())
    assert done["status"] == "done", done
    got = client.get(f"/api/jobs/{done['id']}/download", headers=who.headers)
    assert got.status_code == 200, got.text
    return got


def bundle_import(
    client: TestClient,
    who: Signed,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """묶음 가져오기를 **끝까지** — 넣고(202), 워커가 돌고, 결과(옛 `BundleOut` 모양)를
    돌려준다. 넣는 순간 거절되면(403 · 409) 여기서 assert 로 멈춘다 — 그런 시험은
    `client.post` 를 직접."""
    started = client.post("/api/bundles/import", json=body, headers=headers or who.headers)
    assert started.status_code == 202, started.text
    done = finish_job(client, who, started.json())
    if done["status"] != "done":
        raise AssertionError(f"묶음 작업 실패: {done.get('error')}")
    return dict(done["result"])


def bundle_export(client: TestClient, who: Signed, group: str) -> dict[str, Any]:
    started = client.post("/api/bundles/export", json={"group": group}, headers=who.headers)
    assert started.status_code == 202, started.text
    done = finish_job(client, who, started.json())
    assert done["status"] == "done", done
    got = client.get(f"/api/jobs/{done['id']}/download", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def pipeline_sender(client: TestClient) -> Any:
    """정제 도구(`pipeline/`)의 SEND 를 TestClient 로 — 그리고 **작업을 물을 때마다 워커를
    한 바퀴 돌린다.** 도구는 202 를 받고 `GET /api/jobs/{id}` 를 되풀이하는데, 시험에는 워커
    프로세스가 없다. 물을 때 한 바퀴 돌리면 그것이 곧 워커다."""
    from app.modules.jobs import services as job_services

    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        if method == "GET" and path.startswith("/api/jobs/"):
            job_services.process_one("test-worker")
        got = client.request(method, path, content=body, headers=headers)
        return got.status_code, got.json()

    return send


@contextlib.contextmanager
def patched_pipeline(module: Any, client: TestClient) -> Iterator[None]:
    """`module` 은 `sp_pipeline` 모듈 — SEND 를 바꾸고 기다림(WAIT)을 없앤다."""
    before_send, before_wait = module.SEND, module.WAIT
    module.SEND = pipeline_sender(client)
    module.WAIT = lambda _seconds: None
    try:
        yield
    finally:
        module.SEND, module.WAIT = before_send, before_wait
