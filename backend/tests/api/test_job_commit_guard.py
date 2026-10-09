"""스스로 커밋하는 처리 함수의 커밋 앞 검사 — **빼앗긴 작업은 커밋하지 못한다.**

처리 함수는 커밋하지 않는 것이 규칙이지만 일괄 입력 적용 · 데이터 소스 동기화 · 지표 · 묶음은
스스로 커밋한다. 진행 보고와 끝에서만 「아직 내가 쥔 작업인가」 를 보던 때는 마지막 진행 보고와
그 커밋 사이에 작업을 빼앗기면(박동이 멎어 되살려져 남이 다시 집음) **두 번 들어갈 수
있었다**(2026-10-08). 이제 그 커밋 직전, 같은 트랜잭션에서 본다(`jobs/services.still_mine`).

여기서 「빼앗김」 은 처리 함수가 커밋하기 바로 앞에 다른 연결로 작업 행의 `worker_id` 를 바꿔
만든다 — `recover_stale` 이 되돌리고 다른 워커가 `claim` 한 뒤의 모양이다.
"""

from __future__ import annotations

import io
import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.jobs import files, kinds, services
from app.modules.jobs.models import Job, JobFile
from tests.api.conftest import Signed, finish_job
from tests.api.test_datasources import _source, _vendor_type
from tests.api.test_jobs import _detached, _take
from tests.api.test_ontology import _make_type


def _stored(name: str) -> int:
    with SessionLocal() as other:
        return int(
            other.scalar(select(func.count()).select_from(JobFile).where(JobFile.name == name))
            or 0
        )


def _row(job_id: uuid.UUID) -> Job:
    with SessionLocal() as other:
        row = other.get(Job, job_id)
        assert row is not None
        other.expunge(row)
        return row


@pytest.fixture
def temp_kind() -> Iterator[Callable[[Callable[[kinds.Work], dict[str, Any]]], str]]:
    """시험용 임시 종류 — 끝나면 지운다(다른 시험의 워커가 모르는 종류를 안 본다)."""
    made: list[str] = []

    def register(body: Callable[[kinds.Work], dict[str, Any]]) -> str:
        name = f"guard_{uuid.uuid4().hex[:8]}"
        kinds.register(
            kinds.Kind(name, "커밋 앞 검사 시험", False, False, body, allow_system=True)
        )
        made.append(name)
        return name

    yield register
    for name in made:
        kinds._registry.pop(name, None)


def _taken(db: Session, kind: str, worker: str) -> uuid.UUID:
    job = services.enqueue(db, kind=kind, params={}, user=None, workspace_id=None)
    db.commit()
    _take(db, job.id, worker)
    return job.id


def _cancel(db: Session, job_id: uuid.UUID) -> None:
    """줄에 남겨 다음 시험의 워커가 집지 않게."""
    db.execute(update(Job).where(Job.id == job_id).values(status="cancelled"))
    db.commit()


def test_스스로_커밋하기_직전에_빼앗기면_안_넣고_LOST(
    db: Session, temp_kind: Callable[..., str]
) -> None:
    marker = f"guard-{uuid.uuid4().hex}.txt"

    def body(work: kinds.Work) -> dict[str, Any]:
        work.progress("넣기", 0, 1)  # 마지막 진행 보고 — 여기까지는 내 것이다
        files.store(work.db, name=marker, content_type="text/plain", data=b"x")
        # 그 사이 박동이 멎어 되살려졌고, 다른 워커가 다시 집었다.
        _take(db, work.job.id, "other-worker", attempts=2)
        work.db.commit()  # 스스로 커밋 — 여기서 막혀야 한다
        return {"applied": True}

    job_id = _taken(db, temp_kind(body), "slow-worker")
    outcome = services.run(_detached(job_id), worker_id="slow-worker")
    assert outcome.status == services.LOST
    services.settle(job_id, outcome, worker_id="slow-worker")
    assert _stored(marker) == 0, "빼앗긴 작업의 커밋이 들어갔다"
    row = _row(job_id)
    # 지금 쥔 워커의 작업은 그대로다 — 원래 워커가 아무것도 안 적었다.
    assert (row.status, row.worker_id, row.result) == ("running", "other-worker", None)
    _cancel(db, job_id)


def test_빼앗기지_않았으면_스스로_커밋이_되고_박동이_새로_찍힌다(
    db: Session, temp_kind: Callable[..., str]
) -> None:
    """정상 경로는 그대로다. 그리고 검사는 박동을 함께 찍는다 — 우리가 먼저 잠그면 그 커밋이
    남긴 새 박동 때문에 `recover_stale` 이 되돌리지 않는다."""
    marker = f"guard-{uuid.uuid4().hex}.txt"
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    seen: list[datetime] = []

    def body(work: kinds.Work) -> dict[str, Any]:
        db.execute(update(Job).where(Job.id == work.job.id).values(heartbeat_at=long_ago))
        db.commit()  # 박동이 한참 멎은 것처럼
        files.store(work.db, name=marker, content_type="text/plain", data=b"x")
        work.db.commit()
        beat = _row(work.job.id).heartbeat_at
        assert beat is not None
        seen.append(beat)
        return {"applied": True}

    job_id = _taken(db, temp_kind(body), "steady-worker")
    outcome = services.run(_detached(job_id), worker_id="steady-worker")
    assert outcome.status == "done" and outcome.written, outcome
    assert _stored(marker) == 1
    assert seen and seen[0] > long_ago + timedelta(minutes=30), (
        "커밋 앞 검사가 박동을 안 찍었다"
    )
    assert _row(job_id).status == "done"


def test_세이브포인트를_놓을_때는_작업_행을_잠그지_않는다(
    db: Session, temp_kind: Callable[..., str]
) -> None:
    """세이브포인트를 놓는 것은 커밋이 아니다. 거기서 작업 행을 잠그면 그 잠금이 바깥 트랜잭션
    끝까지 남아, 다음 진행 보고(다른 연결)가 **제 트랜잭션을 기다리며 영영 멎는다.**"""
    marker = f"guard-{uuid.uuid4().hex}.txt"

    def body(work: kinds.Work) -> dict[str, Any]:
        with work.db.begin_nested():
            files.store(work.db, name=marker, content_type="text/plain", data=b"x")
        with SessionLocal() as other:
            # 잠겨 있으면 기다리지 않고 바로 터진다 — 시험이 멎지 않게.
            try:
                other.execute(
                    select(Job.id).where(Job.id == work.job.id).with_for_update(nowait=True)
                )
            except OperationalError as caught:
                raise AssertionError("세이브포인트를 놓으며 작업 행을 잠갔다") from caught
            other.rollback()
        work.progress("다음", 1, 1)
        return {"applied": True}

    job_id = _taken(db, temp_kind(body), "nested-worker")
    outcome = services.run(_detached(job_id), worker_id="nested-worker")
    assert outcome.status == "done", outcome
    assert _stored(marker) == 1


def test_반복_읽기_트랜잭션의_커밋은_박동과_부딪쳐_깨지지_않는다(
    db: Session, temp_kind: Callable[..., str]
) -> None:
    """경보 확인은 반복 읽기(REPEATABLE READ)로 돈다. 그 안에서 작업 행을 고치면 스냅샷 뒤에
    박동 스레드가 고친 행이라 직렬화 실패로 **커밋 자체가 깨진다** — 그래서 거기는 안 본다."""
    marker = f"guard-{uuid.uuid4().hex}.txt"

    def body(work: kinds.Work) -> dict[str, Any]:
        work.db.rollback()
        work.db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        work.db.scalar(select(func.count()).select_from(Job))  # 스냅샷이 여기서 선다
        # 그 사이 박동 스레드가 작업 행을 고쳤다.
        db.execute(
            update(Job).where(Job.id == work.job.id).values(heartbeat_at=datetime.now(UTC))
        )
        db.commit()
        files.store(work.db, name=marker, content_type="text/plain", data=b"x")
        work.db.commit()
        return {"applied": True}

    job_id = _taken(db, temp_kind(body), "rr-worker")
    outcome = services.run(_detached(job_id), worker_id="rr-worker")
    assert outcome.status == "done", outcome
    assert _stored(marker) == 1


def _submit(client: TestClient, who: Signed, type_slug: str, csv_text: str) -> dict[str, Any]:
    got = client.post(
        f"/api/objects/{type_slug}/import",
        files={"file": ("rows.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        data={"workspace_slug": who.workspace},
        headers=who.headers,
    )
    assert got.status_code == 202, got.text
    return dict(got.json())


def test_일괄_입력_적용이_커밋_직전에_빼앗기면_안_넣는다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_objects` 는 끝에서 스스로 커밋한다 — 그 앞 마지막 일(묶음 감사 한 줄)에서
    빼앗기게 한다. 진행 보고는 그보다 앞이라 지금까지의 검사로는 못 잡던 자리다."""
    from app.shared import audit

    part = _make_type(client, admin, label="부품", key_policy="required")
    plan = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    applying = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert applying.status_code == 202, applying.text
    apply_id = uuid.UUID(applying.json()["id"])
    _take(db, apply_id, "slow-worker")

    original = audit.record

    def stealing(session: Session, **kw: Any) -> Any:
        if kw.get("action") == "object.import":
            _take(db, apply_id, "other-worker", attempts=2)
        return original(session, **kw)

    monkeypatch.setattr(audit, "record", stealing)
    late = services.run(_detached(apply_id), worker_id="slow-worker")
    monkeypatch.setattr(audit, "record", original)
    assert late.status == services.LOST
    assert client.get(f"/api/objects/{part}", headers=admin.headers).json()["total"] == 0

    # 지금 쥔 워커가 돌리면 한 번 들어간다.
    done = services.run(_detached(apply_id), worker_id="other-worker")
    assert done.status == "done", done
    assert client.get(f"/api/objects/{part}", headers=admin.headers).json()["total"] == 1


def test_묶음_가져오기는_바깥_트랜잭션의_커밋_앞에서_본다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """묶음은 제 연결의 바깥 트랜잭션으로 커밋한다 — 워커가 본 세션에 건 검사가 거기는 안
    닿는다. 되돌릴 기록을 닫는 자리(커밋 바로 앞)에서 빼앗기게 한다."""
    from app.modules.bundles import journal

    slug = f"memo_{uuid.uuid4().hex[:6]}"
    body = {
        "apply": True,
        "ontology": {"types": [{"slug": slug, "label": "메모", "properties": []}]},
        "objects": [{"type_slug": slug, "rows": [{"label": "첫 메모"}]}],
    }
    started = client.post("/api/bundles/import", json=body, headers=admin.headers)
    assert started.status_code == 202, started.text
    job_id = uuid.UUID(started.json()["id"])
    _take(db, job_id, "slow-worker")

    original = journal.finish

    def stealing(session: Session, **kw: Any) -> Any:
        _take(db, job_id, "other-worker", attempts=2)
        return original(session, **kw)

    monkeypatch.setattr(journal, "finish", stealing)
    late = services.run(_detached(job_id), worker_id="slow-worker")
    monkeypatch.setattr(journal, "finish", original)
    assert late.status == services.LOST
    assert client.get(f"/api/objects/{slug}", headers=admin.headers).status_code == 404

    done = services.run(_detached(job_id), worker_id="other-worker")
    assert done.status == "done", done
    assert client.get(f"/api/objects/{slug}", headers=admin.headers).json()["total"] == 1


def test_동기화가_빼앗기면_실패로_삼키지_않고_실행_기록만_닫는다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """동기화는 무엇이 나도 기록을 닫는 그물(`_sync`)이 있다. 거기서 `Lost` 를 「실패」 로
    삼키면 실패를 적는 커밋이 같은 검사에 또 막히고, 소스의 상태가 「실패」 가 되어 이어받은
    워커가 끝낼 때 「다시 됩니다」 알림이 헛나간다. 앞 조각이 들어갔으면 기록만 닫는다."""
    from app.modules.datasources import services as datasource_services
    from app.modules.datasources.models import DataSource, DataSourceRun

    made = _source(client, admin, _vendor_type(client, admin))
    source = db.scalar(select(DataSource).where(DataSource.slug == made["slug"]))
    assert source is not None
    before = source.last_status

    def half_then_lost(
        session: Session, user: Any, src: Any, run: DataSourceRun, *, apply: bool
    ) -> Any:
        run.applied = True
        session.commit()  # 앞 조각이 들어갔다
        raise services.Lost()

    monkeypatch.setattr(datasource_services, "_sync_body", half_then_lost)
    with pytest.raises(services.Lost):
        datasource_services.sync(db, None, source, apply=True)

    db.expire_all()
    run = db.scalar(
        select(DataSourceRun)
        .where(DataSourceRun.source_id == source.id)
        .order_by(DataSourceRun.started_at.desc())
        .limit(1)
    )
    assert run is not None and run.status == "failed" and run.finished_at is not None
    assert run.errors == [datasource_services.TAKEN_OVER, datasource_services.PARTIAL]
    refreshed = db.get(DataSource, source.id)
    assert refreshed is not None and refreshed.last_status == before


def test_동기화_차례가_빼앗기면_다음_소스로_가지_않고_이미_돈_소스를_남긴다(
    client: TestClient,
    admin: Signed,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """차례의 소스마다 `except Exception` 이 있다 — 거기서 `Lost` 를 삼키면 빼앗긴 워커가 남은
    소스를 계속 돌고, 이어받은 워커도 같은 소스를 돈다. 이미 커밋한 앞 소스는 로그에 남는다."""
    from types import SimpleNamespace

    from app.modules.datasources import services as datasource_services

    vendor = _vendor_type(client, admin)
    first, second, third = (_source(client, admin, vendor)["slug"] for _ in range(3))
    ran: list[str] = []

    def fake_sync(session: Session, user: Any, source: Any, *, apply: bool) -> Any:
        ran.append(source.slug)
        if len(ran) == 2:
            raise services.Lost()
        run = SimpleNamespace(applied=False, counts={}, status="ok")
        return SimpleNamespace(run=run)

    monkeypatch.setattr(datasource_services, "sync", fake_sync)
    work = kinds.Work(
        db=db,
        job=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        user=None,
        params={"slugs": [first, second, third], "apply": True},
        input_file=None,
        progress=lambda *_: None,
        still_mine=lambda _: None,
    )
    with caplog.at_level(logging.WARNING), pytest.raises(services.Lost):
        kinds.datasource_sync_round(work)
    assert len(ran) == 2, "빼앗긴 뒤에도 다음 소스를 돌았다"
    assert ran[0] in caplog.text and "빼앗겼습니다" in caplog.text


def test_지표의_경보_확인은_빼앗김을_삼키지_않는다(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """경보 확인의 그물(`except Exception`)이 `Lost` 를 「경보 확인 실패」 로 삼키면 빼앗긴
    워커가 다음 지표를 계속 센다."""
    from app.modules.metrics import alerts
    from app.modules.metrics import services as metrics_services

    def lost(_db: Session, _metric_id: uuid.UUID) -> dict[str, int]:
        raise services.Lost()

    monkeypatch.setattr(alerts, "after_recompute", lost)
    with pytest.raises(services.Lost):
        metrics_services._check_alerts(db, uuid.uuid4(), "x")
