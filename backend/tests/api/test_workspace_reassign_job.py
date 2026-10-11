"""부서 통폐합(자료 이동)은 **작업**이다 — 계획을 보고 적용한다(2026-10-10).

한 부서 객체 20만 개면 90초 남짓이라 요청 안에서 돌면 브라우저 · 이중화의 nginx 가 먼저 끊었다.
이제 `POST /api/workspaces/{slug}/reassign` 은 계획 작업을 넣고(202), 사람이 읽고 적용한다
(`POST /api/jobs/{id}/apply`).

여기서 보는 것: 계획의 수 · 막히는 것(아무것도 안 바뀐다), 적용이 예전 동기 경로와 같은지
(객체 · 객체마다의 기록 · 통폐합 한 줄 · 지운 객체도), 권한(전용 경로 · 일반 작업 API · 워커),
취소 · 빼앗김이면 아무것도 안 옮겨지는지, 지문, 도는 동안 원본을 못 지우는지. 코어 API 의
「안 보이게 됨」 은 `test_core_reassign.py`, 홈의 지표는 `test_metric_home.py` 가 같은 도우미로
본다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.audit.models import AuditEntry
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.objects import services as objects_services
from app.modules.objects.models import ObjectInstance
from app.modules.workspaces.models import Workspace, WorkspaceMember
from tests.api.conftest import Signed, _make_user, finish_job, work_until
from tests.api.test_ontology import _make_object, _make_type


def reassign_plan(
    client: TestClient, admin: Signed, source: str, target: str, kinds: list[str]
) -> dict[str, Any]:
    """통폐합의 **계획**까지 — 넣고(202) 워커가 계획을 세운 작업."""
    started = client.post(
        f"/api/workspaces/{source}/reassign",
        json={"target_slug": target, "kinds": kinds},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "workspace_reassign"
    planned = finish_job(client, admin, started.json())
    assert planned["status"] == "done", planned
    return planned


def reassign(
    client: TestClient, admin: Signed, source: str, target: str, kinds: list[str]
) -> dict[str, Any]:
    """통폐합을 **끝까지** — 계획을 세우고 적용한다. 돌려주는 것은 적용 작업의 결과 — 옮긴 수는
    `moved`(예전 동기 응답의 `{moved}` 와 같은 모양)."""
    planned = reassign_plan(client, admin, source, target, kinds)
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _space(client: TestClient, admin: Signed, db: Session, name: str) -> Workspace:
    made = client.post(
        "/api/workspaces",
        json={"slug": f"ws-{uuid.uuid4().hex[:8]}", "name": name},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return db.scalars(select(Workspace).where(Workspace.slug == made.json()["slug"])).one()


def _owner_of(db: Session, object_id: str) -> uuid.UUID | None:
    db.expire_all()
    row = db.get(ObjectInstance, uuid.UUID(object_id))
    assert row is not None
    return row.owner_workspace_id


def _object_records(db: Session, ids: list[str]) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(
                AuditEntry.target_table == "objects",
                AuditEntry.target_id.in_([uuid.UUID(one) for one in ids]),
                AuditEntry.changes.has_key("owner_workspace_id"),
            )
        )
        or 0
    )


def _reassigned_records(db: Session, source: Workspace) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(
                AuditEntry.action == "workspace.reassigned",
                AuditEntry.target_id == source.id,
            )
        )
        or 0
    )


@pytest.fixture
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """덩어리를 작게 — 객체 다섯이 세 덩어리가 되어 그 사이에 진행 · 취소가 선다."""
    monkeypatch.setattr(objects_services, "MOVE_CHUNK", 2)


def test_계획은_몇_건이_옮겨지나와_막히는_것을_보이고_아무것도_안_바꾼다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(
        client, admin, label="부품", key_policy="optional", key_scope="workspace"
    )
    source = _space(client, admin, db, "없어지는 팀")
    target = _space(client, admin, db, "합쳐지는 팀")
    moving = [
        _make_object(
            client, admin, part, label=f"부품 {n}", key=f"M-{n}", workspace_slug=source.slug
        )
        for n in range(3)
    ]
    _make_object(client, admin, part, label="이미 거기", key="M-0", workspace_slug=target.slug)

    planned = reassign_plan(client, admin, source.slug, target.slug, ["objects", "members"])
    result = planned["result"]
    assert result["reassign"] is True and result["applied"] is False
    counts = {one["kind"]: one["count"] for one in result["items"]}
    # 등록된 차례 그대로(멤버가 먼저) — 고른 차례가 아니다.
    assert [one["kind"] for one in result["items"]] == ["members", "objects"]
    assert counts == {"members": 1, "objects": 3}
    # **겹쳐서 막히는 것** — 부서 안에서 하나여야 할 식별자가 대상 부서에 이미 있다.
    assert result["ok"] is False and len(result["errors"]) == 1
    assert "M-0" in result["errors"][0] and result["errors"][0].startswith("객체:")
    assert "막힘 1" in result["summary"]
    # 계획은 아무것도 안 바꾼다.
    assert all(_owner_of(db, one["id"]) == source.id for one in moving)
    assert _object_records(db, [one["id"] for one in moving]) == 0
    # 막힌 계획은 적용 작업이 안 선다(작업의 공통 거절).
    refused = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert refused.status_code == 409 and "JOBS-0011" in refused.text

    # 막는 것을 비키면(겹치는 것을 빼고 고르면) 적용할 수 있는 계획이다.
    clean = reassign_plan(client, admin, source.slug, target.slug, ["members"])
    assert clean["result"]["ok"] is True and clean["result"]["errors"] == []
    assert clean["result"]["summary"].startswith("계획 — 이동할 것: 멤버 1")


def test_적용은_예전_동기_경로와_같다(
    client: TestClient, admin: Signed, db: Session, small_chunks: None
) -> None:
    """옮긴 수 · 객체마다의 소유 부서 기록(지운 객체도) · 통폐합 한 줄 · 비운 원본은 지울 수
    있다 — 요청 안에서 돌던 때와 같다. 덩어리로 나눠도(여기서는 둘씩) 빠지는 것이 없다."""
    part = _make_type(client, admin, label="부품", key_policy="optional")
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    made = [
        _make_object(
            client, admin, part, label=f"부품 {n}", key=f"R-{n}", workspace_slug=source.slug
        )
        for n in range(5)
    ]
    gone = client.delete(f"/api/objects/{part}/{made[0]['id']}", headers=admin.headers)
    assert gone.status_code in (200, 204), gone.text

    done = reassign(client, admin, source.slug, target.slug, ["members", "objects"])
    assert done["applied"] is True and done["ok"] is True
    assert done["moved"] == {"members": 1, "objects": 5}
    assert {one["kind"]: one["moved"] for one in done["items"]} == {"members": 1, "objects": 5}
    assert done["summary"].startswith("적용 — 이동: 멤버 1 · 객체 5")
    assert all(_owner_of(db, one["id"]) == target.id for one in made)
    # 객체마다 한 줄(지운 것도) · 통폐합은 한 줄 — 바깥에는 그 한 줄만 알린다.
    assert _object_records(db, [one["id"] for one in made]) == 5
    assert _reassigned_records(db, source) == 1
    entry = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "workspace.reassigned", AuditEntry.target_id == source.id
        )
    ).one()
    assert entry.changes == {"target": target.slug, "moved": {"members": 1, "objects": 5}}
    assert entry.actor_label == "admin"

    # 진행률은 마지막 단계까지 적혔다.
    applied = db.scalars(
        select(Job).where(
            Job.kind == "workspace_reassign",
            Job.params["apply"].astext == "true",
            Job.params["slug"].astext == source.slug,
        )
    ).one()
    assert applied is not None and applied.progress["stage"] == "기록"
    assert applied.progress["done"] == applied.progress["total"] == 6

    # 비었으니 지울 수 있다 — 통폐합의 끝.
    after = {
        one["kind"]: one["count"]
        for one in client.get(
            f"/api/workspaces/{source.slug}/contents", headers=admin.headers
        ).json()
    }
    assert after["objects"] == 0 and after["members"] == 0
    removed = client.delete(f"/api/workspaces/{source.slug}", headers=admin.headers)
    assert removed.status_code == 204, removed.text


def test_시스템_관리자만_넣고_일반_작업_API_로는_못_넣는다(
    client: TestClient,
    admin: Signed,
    manager: Signed,
    member: Signed,
    db: Session,
) -> None:
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    body = {"target_slug": target.slug, "kinds": ["members"]}
    for who in (manager, member):
        denied = client.post(
            f"/api/workspaces/{source.slug}/reassign", json=body, headers=who.headers
        )
        assert denied.status_code == 403, denied.text

    # 일반 작업 API 는 전용 경로의 검사를 건너뛴다 — 시스템 관리자라도 그 길로는 안 받는다.
    general = client.post(
        "/api/jobs",
        data={
            "kind": "workspace_reassign",
            "params": f'{{"slug": "{source.slug}", "target_slug": "{target.slug}", '
            '"kinds": ["members"]}',
        },
        headers=admin.headers,
    )
    assert general.status_code == 403 and "JOBS-0025" in general.text

    # 넣는 순간에 거절하는 것 — 몇 분 뒤 「실패」 가 아니라.
    same = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": source.slug, "kinds": ["members"]},
        headers=admin.headers,
    )
    assert same.status_code == 400
    unknown = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": target.slug, "kinds": ["보고서"]},
        headers=admin.headers,
    )
    assert unknown.status_code == 422 and "아는 것" in unknown.json()["error"]["message"]
    missing = client.post(
        "/api/workspaces/nope-nope/reassign", json=body, headers=admin.headers
    )
    assert missing.status_code == 404

    # **워커도 다시 본다**(`admin_only`) — 넣는 길을 건너뛰어 들어온 줄(시킨 사람이 관리자가
    # 아니다)은 돌리지 않는다.
    plain = _make_user(db, source, label="plain", is_system_admin=False, role="manager")
    sneaked = Job(
        kind="workspace_reassign",
        status="queued",
        params={"slug": source.slug, "target_slug": target.slug, "kinds": ["members"]},
        requested_by_id=plain.id,
        progress={},
    )
    db.add(sneaked)
    db.commit()
    work_until(db, sneaked.id)
    db.expire_all()
    failed = db.get(Job, sneaked.id)
    assert failed is not None and failed.status == "failed"
    assert "JOBS-0024" in (failed.error or "")
    assert db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == source.id)
    )


@pytest.fixture
def stage_hook(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[Any, Any]]:
    """진행 보고에 끼어드는 자리 — 단계 이름으로 그때 할 일(`before` · `after`, (작업 id, 옮긴
    수) 를 받는다)을 건다. 진짜 워커처럼 보고는 제 연결로 커밋한다."""
    hooks: dict[Any, Any] = {}
    original = job_services.report_progress

    def wrapped(job_id: uuid.UUID, worker_id: str, stage: str, done: int, total: int) -> bool:
        before = hooks.get(("before", stage))
        if before is not None:
            before(job_id, done)
        answer = original(job_id, worker_id, stage, done, total)
        after = hooks.get(("after", stage))
        if after is not None:
            after(job_id, done)
        return answer

    monkeypatch.setattr(job_services, "report_progress", wrapped)
    yield hooks


def _set(job_id: uuid.UUID, **values: Any) -> None:
    with SessionLocal() as other:
        other.execute(update(Job).where(Job.id == job_id).values(**values))
        other.commit()


def _apply_job(client: TestClient, admin: Signed, source: str, target: str) -> dict[str, Any]:
    planned = reassign_plan(client, admin, source, target, ["members", "objects"])
    started = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert started.status_code == 202, started.text
    return dict(started.json())


def test_취소하면_덩어리_사이에서_멈추고_아무것도_안_옮긴다(
    client: TestClient,
    admin: Signed,
    db: Session,
    small_chunks: None,
    stage_hook: dict[Any, Any],
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    made = [
        _make_object(
            client, admin, part, label=f"부품 {n}", key=f"C-{n}", workspace_slug=source.slug
        )
        for n in range(5)
    ]
    started = _apply_job(client, admin, source.slug, target.slug)

    # 멤버 하나 · 객체 둘(첫 덩어리)을 옮긴 뒤에 사람이 「취소」 를 누른다.
    def cancel_after_first_chunk(job_id: uuid.UUID, done: int) -> None:
        if done >= 3:
            _set(job_id, cancel_requested=True)

    stage_hook[("before", "객체 이동")] = cancel_after_first_chunk

    done = finish_job(client, admin, started)
    assert done["status"] == "cancelled", done
    # **한 트랜잭션** — 앞 덩어리 · 앞 종류(멤버)까지 무른다.
    assert all(_owner_of(db, one["id"]) == source.id for one in made)
    assert _object_records(db, [one["id"] for one in made]) == 0
    assert _reassigned_records(db, source) == 0
    assert db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == source.id)
    )


def test_빼앗긴_작업은_커밋하지_않는다(
    client: TestClient,
    admin: Signed,
    db: Session,
    small_chunks: None,
    stage_hook: dict[Any, Any],
) -> None:
    """다 옮기고 기록까지 남긴 **커밋 직전**에 작업을 빼앗겼다(박동이 멎어 되살려지고 남이
    집었다) — 워커의 끝 조건(`_owned`)이 0줄이라 롤백이다. 같은 통폐합이 두 번 들어가지 않는다.
    처리 함수는 스스로 커밋하지 않으므로 그 한 번의 검사가 전부다."""
    part = _make_type(client, admin, label="부품", key_policy="optional")
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    made = [
        _make_object(
            client, admin, part, label=f"부품 {n}", key=f"L-{n}", workspace_slug=source.slug
        )
        for n in range(3)
    ]
    started = _apply_job(client, admin, source.slug, target.slug)
    stage_hook[("after", "기록")] = lambda job_id, _done: _set(
        job_id, worker_id="someone-else"
    )
    job_id = uuid.UUID(started["id"])
    try:
        assert job_services.process_one("test-worker")
        db.expire_all()
        held = db.get(Job, job_id)
        assert (
            held is not None and held.status == "running" and held.worker_id == "someone-else"
        )
        assert all(_owner_of(db, one["id"]) == source.id for one in made)
        assert _object_records(db, [one["id"] for one in made]) == 0
        assert _reassigned_records(db, source) == 0
    finally:
        _set(job_id, status="failed", error="시험이 끝냄")


def test_미리_본_뒤_원본이_바뀌면_적용하지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    first = _make_object(
        client, admin, part, label="하나", key="F-1", workspace_slug=source.slug
    )
    planned = reassign_plan(client, admin, source.slug, target.slug, ["objects"])
    # 그 사이 원본 부서에 객체가 하나 더 들어왔다 — 계획의 수가 달라진다.
    _make_object(client, admin, part, label="둘", key="F-2", workspace_slug=source.slug)
    started = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert started.status_code == 202, started.text
    failed = finish_job(client, admin, started.json())
    assert failed["status"] == "failed" and "JOBS-0020" in failed["error"]
    assert _owner_of(db, first["id"]) == source.id


def test_자료_이동이_끝나기_전에는_원본도_대상도_지우지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """작업이 되면서 「이동」 뒤 곧장 「삭제」 를 누를 수 있게 됐다 — 대기 중인 작업이 멤버를
    옮기기 전에 원본을 지우면 멤버십이 부서와 함께 사라진다(막는 참조가 아니다)."""
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    started = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": target.slug, "kinds": ["members"]},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    for slug in (source.slug, target.slug):
        refused = client.delete(f"/api/workspaces/{slug}", headers=admin.headers)
        assert refused.status_code == 409 and "WORKSPACES-0017" in refused.text
        assert refused.json()["error"]["details"]["job_id"] == started.json()["id"]

    planned = finish_job(client, admin, started.json())
    assert planned["status"] == "done"
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    refused = client.delete(f"/api/workspaces/{source.slug}", headers=admin.headers)
    assert refused.status_code == 409  # 적용이 아직 줄에 있다
    finish_job(client, admin, applied.json())
    removed = client.delete(f"/api/workspaces/{source.slug}", headers=admin.headers)
    assert removed.status_code == 204, removed.text
    db.expire_all()
    assert (
        db.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == target.id)
        )
        == 1
    )


def test_작업_화면의_종류_목록에_뜬다(client: TestClient, admin: Signed) -> None:
    kinds = {
        one["name"]: one for one in client.get("/api/jobs/kinds", headers=admin.headers).json()
    }
    found = kinds["workspace_reassign"]
    assert found["two_step"] is True and found["needs_file"] is False
    assert found["label"] == "부서 자료 이동" and found["max_file_bytes"] is None
