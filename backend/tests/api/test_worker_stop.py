"""워커가 멈출 때 · 살림살이가 실패할 때 — 2026-11-28 점검의 고침.

- **재시작 · 갱신은 작업의 실패가 아니다.** systemd 는 멈추라고(SIGTERM) 한 뒤 `TimeoutStopSec`
안에
  못 끝내면 죽이고, 그 작업은 `running` 으로 남아 다른 워커가 거둔다. 예전에는 그것을
  「메모리를 다 써 강제 종료됐을 수 있습니다」 로 실패시켜, 다시 안 돌리는 종류(지도 · 밤
  그래프 계산)의 자동 계산이 사람이 다시 누를 때까지 멈췄다 — `deploy.sh update` 한 번으로.
- **실패로 멈춘 자동 계산은 시스템 관리자에게 알린다**(하루에 한 번쯤) — 멈춘 까닭이 지도
화면에만
  적혀 몇 주씩 아무도 몰랐다.
- **살림살이가 계속 실패하면 알린다** · **오래된 기록은 묶음으로 지운다** · **마이그레이션은
잠금을
  끝없이 기다리지 않는다.**
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.modules.graph import calc, mapbuild
from app.modules.graph.models import MapCounted
from app.modules.jobs import files
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.notifications import services as notifications
from app.modules.notifications.models import Notification
from tests.api.conftest import Signed, notifications_of

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def stopping() -> Iterator[None]:
    """멈춤 신호는 프로세스에 하나다 — 시험이 끝나면 꼭 거둔다(안 거두면 뒤 시험이 작업을 못
    집는다)."""
    job_services.forget_stopping()
    try:
        yield
    finally:
        job_services.forget_stopping()


def _stale(kind: str, worker: str, **progress: str) -> Job:
    return Job(
        kind=kind,
        status="running",
        params={},
        worker_id=worker,
        attempts=1,
        heartbeat_at=datetime.now(UTC) - timedelta(hours=1),
        progress={"stage": "계산", "done": 1, "total": 2, **progress},
    )


def test_멈추라는_신호를_받으면_쥔_작업에_적고_새_작업은_안_집는다(
    db: Session, stopping: None
) -> None:
    me = f"stop-test:{uuid.uuid4().hex[:6]}"
    mine = Job(
        kind="graph_map_full",
        status="running",
        params={},
        worker_id=me,
        attempts=1,
        heartbeat_at=datetime.now(UTC),
        progress={"stage": "짝짓기", "done": 3, "total": 9},
    )
    waiting = Job(kind="webhook_dispatch", status="queued", params={})
    db.add_all([mine, waiting])
    db.commit()
    try:
        job_services.mark_stopping(me).join(timeout=10)
        db.refresh(mine)
        assert mine.progress["stopping"] and mine.progress["stage"] == "짝짓기"
        # 그 뒤의 진행 보고도 표시를 지우지 않는다.
        job_services.report_progress(mine.id, me, "기록", 5, 9)
        db.refresh(mine)
        assert mine.progress["stage"] == "기록" and mine.progress["stopping"]
        # 새 작업은 안 집는다 — 기다리는 것이 있어도.
        assert job_services.process_one(me) is False
        db.refresh(waiting)
        assert waiting.status == "queued"
    finally:
        db.execute(delete(Job).where(Job.id.in_((mine.id, waiting.id))))
        db.commit()


def test_진행_보고는_먼저_적힌_멈춤_표시를_지우지_않는다(db: Session, stopping: None) -> None:
    """진행 보고가 「신호 없음」 을 읽은 뒤 · 적기 전에 신호가 와서 표시 스레드가 먼저 적은
    자리 — 예전에는 진행 보고가 그 표시를 덮어 지워, 그 작업이 옛 말(「메모리를 다 써…」)로
    거둬졌다(2026-11-28 검토). 이 프로세스는 아직 신호를 모른다(`_stopping_since` 없음)."""
    me = f"race-test:{uuid.uuid4().hex[:6]}"
    marked = "2026-11-28T03:00:00+00:00"
    job = Job(
        kind="graph_map_full",
        status="running",
        params={},
        worker_id=me,
        attempts=1,
        heartbeat_at=datetime.now(UTC),
        progress={"stage": "짝짓기", "done": 1, "total": 9, "stopping": marked},
    )
    db.add(job)
    db.commit()
    try:
        job_services.report_progress(job.id, me, "기록", 5, 9)
        db.refresh(job)
        assert job.progress == {"stage": "기록", "done": 5, "total": 9, "stopping": marked}
    finally:
        db.execute(delete(Job).where(Job.id == job.id))
        db.commit()


def test_멈추는_사이_끊긴_작업은_메모리_탓으로_적지_않는다(db: Session) -> None:
    gone = f"gone:{uuid.uuid4().hex[:6]}"
    cut = _stale("graph_map_full", gone, stopping="2026-11-28T03:00:00+00:00")
    killed = _stale("graph_calc", gone)
    db.add_all([cut, killed])
    db.commit()
    job_services.recover_stale(db)
    db.refresh(cut)
    db.refresh(killed)
    assert cut.status == "failed" and job_services.interrupted(cut.error), cut.error
    assert "재시작 · 갱신" in (cut.error or "") and "메모리" not in (cut.error or "")
    # 멈춤 표시가 없으면 예전 그대로 — 작업 자체가 워커를 죽였을 수 있다(ADR 0049).
    assert killed.status == "failed" and not job_services.interrupted(killed.error)
    assert "메모리를 다 써" in (killed.error or "")


def _settled(db: Session, kind: str, status: str, error: str | None, minutes_ago: int) -> None:
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    db.add(
        Job(
            kind=kind,
            status=status,
            params={},
            error=error,
            created_at=at,
            finished_at=at + timedelta(minutes=1),
        )
    )
    db.commit()


INTERRUPTED = f"[{job_services.INTERRUPTED}] 워커가 멈추는(재시작 · 갱신) 사이 …"


@pytest.mark.parametrize(
    ("kinds", "paused"),
    [(mapbuild.KINDS, mapbuild.paused), ((calc.KIND,), calc.paused)],
    ids=["지도", "밤 그래프 계산"],
)
def test_자동_계산은_멈추는_사이_끊긴_작업을_실패로_세지_않는다(
    db: Session, kinds: tuple[str, ...], paused: object
) -> None:
    check = paused  # mypy: 아래에서 부른다
    assert callable(check)
    db.execute(delete(Job).where(Job.kind.in_(kinds)))
    db.commit()
    kind = kinds[0]
    _settled(db, kind, "done", None, 60)
    _settled(db, kind, "failed", INTERRUPTED, 30)
    assert check(db) is None  # 재시작에 끊긴 것은 실패가 아니다
    _settled(db, kind, "failed", "MemoryError: 메모리가 모자랍니다", 20)
    assert "실패했습니다" in (check(db) or "")  # 진짜 실패는 멈춘다
    # 그 뒤 사람이 다시 눌렀는데(「전체 다시 배치」 · 「지금 계산」) 재시작이 그것을 끊었다 —
    # 그 누름이 마지막 결정이다. 끊긴 것을 건너뛰고 앞의 실패를 다시 보면, 사람이 다시 누른
    # 일이 재시작 한 번에 지워지고 지도는 또 사람을 기다린다(2026-11-28 검토).
    _settled(db, kind, "failed", INTERRUPTED, 10)
    assert check(db) is None
    db.execute(delete(Job).where(Job.kind.in_(kinds)))
    db.commit()


def test_재시작에_끊긴_집계는_여섯_시간을_기다리지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """집계의 실패는 6시간(`COUNT_RETRY`) 뒤에 다시 본다 — 재시작에 끊긴 집계까지 그렇게 세면
    갱신 한 번에 한 시간마다의 집계가 반나절 밀린다."""
    from tests.api.test_graph_map import _relayout, _world
    from tests.api.test_graph_map_safety import _timer

    db_kinds = mapbuild.KINDS
    with SessionLocal() as db:
        db.execute(delete(Job).where(Job.kind.in_(db_kinds)))
        db.execute(
            text(
                "TRUNCATE graph_map_counts, graph_map_counted, graph_map_links, "
                "graph_map_frozen, graph_map_masters, graph_map_points, graph_map_layouts"
            )
        )
        db.commit()
    _world(client, admin)
    _relayout(client, admin)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.execute(update(MapCounted).values(counted_at=now - timedelta(hours=2)))
        db.add(
            Job(
                kind="graph_map_count",
                status="failed",
                params={},
                error=INTERRUPTED,
                created_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=29),
            )
        )
        db.commit()
    try:
        assert _timer() == "graph_map_count"
    finally:
        with SessionLocal() as db:
            db.execute(delete(Job).where(Job.kind.in_(db_kinds)))
            db.commit()


@pytest.mark.parametrize(
    ("kinds", "schedule", "title"),
    [
        (mapbuild.KINDS, mapbuild.schedule, "지식 그래프 지도의 자동 계산을 멈췄습니다"),
        ((calc.KIND,), calc.schedule, "밤 그래프 계산의 자동 계산을 멈췄습니다"),
    ],
    ids=["지도", "밤 그래프 계산"],
)
def test_실패로_멈춘_자동_계산은_시스템_관리자에게_하루에_한_번_알린다(
    client: TestClient,
    admin: Signed,
    db: Session,
    kinds: tuple[str, ...],
    schedule: object,
    title: str,
) -> None:
    run = schedule
    assert callable(run)
    db.execute(delete(Job).where(Job.kind.in_(kinds)))
    db.execute(
        delete(Notification).where(Notification.kind == notifications.GRAPH_AUTO_PAUSED)
    )
    db.commit()
    _settled(db, kinds[0], "failed", "MemoryError: 메모리가 모자랍니다", 30)
    for _ in range(3):  # 타이머는 5분마다 본다 — 그때마다 알리지 않는다
        with SessionLocal() as session:
            assert run(session) is None
    shown = notifications_of(client, admin, "graph.auto_paused")
    assert [one["title"] for one in shown] == [title]
    assert "MemoryError" in (shown[0]["body"] or "") and shown[0]["link"] == "/graph"
    db.execute(delete(Job).where(Job.kind.in_(kinds)))
    db.commit()


def test_계속_실패하는_살림살이는_시스템_관리자에게_한_번_알린다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.worker import Step, Worker

    db.execute(
        delete(Notification).where(Notification.kind == notifications.WORKER_STEP_FAILED)
    )
    db.commit()
    worker = Worker()
    worker._last_beat = time.monotonic()  # 박동은 이 시험의 일이 아니다
    step = Step("purge", "오래된 기록 정리", 3600.0)
    worker.steps = [step]

    def denied() -> None:
        raise PermissionError(13, "Permission denied", "/data/filestore/_jobs")

    monkeypatch.setattr(worker, "_purge", denied)
    seen = []
    for _ in range(3):
        step.last = -1e12  # 차례가 됐다고 치고
        worker._tick_housekeeping()
        seen.append(len(notifications_of(client, admin, "worker.step_failed")))
    # 첫 실패로는 안 알리고(한 번의 끊김일 수 있다), 두 번째에 한 번, 세 번째는 같은 말이라 안
    # 알린다.
    assert seen == [0, 1, 1]
    shown = notifications_of(client, admin, "worker.step_failed")[0]
    assert "오래된 기록 정리" in shown["title"] and "Permission denied" in (
        shown["body"] or ""
    )
    assert step.failures == 3


def test_오래된_작업_기록은_묶음으로_지운다(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 문장으로 지우던 때는 몇 달 치가 한 트랜잭션이었다 — 묶음마다 커밋하고, 한 차례의 묶음
    수를 넘으면 나머지는 다음 차례(한 시간 뒤)가 지운다."""
    job_services.purge_old_jobs(db)  # 앞 시험이 남긴 오래된 것부터
    old = datetime.now(UTC) - timedelta(days=get_settings().job_ttl_days + 1)
    db.add_all(
        Job(kind="webhook_dispatch", status="done", params={}, finished_at=old)
        for _ in range(5)
    )
    db.commit()
    monkeypatch.setattr(files, "PURGE_BATCH", 2)
    monkeypatch.setattr(files, "PURGE_MAX_BATCHES", 2)
    assert job_services.purge_old_jobs(db) == 4
    assert job_services.purge_old_jobs(db) == 1
    assert job_services.purge_old_jobs(db) == 0


# --- 마이그레이션은 잠금을 끝없이 기다리지 않는다
# ---------------------------------------------


PROBE_MIGRATION = '''"""잠금 상한 시험 — 남이 쥔 표를 고친다."""
revision = "zz_lock_probe"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("ALTER TABLE {table} ADD COLUMN extra integer")


def downgrade() -> None:
    pass
'''


def test_마이그레이션은_남이_쥔_표에서_잠금_상한만큼만_기다린다(tmp_path: Path) -> None:
    """03:00 백업의 pg_dump 는 표마다 AccessShareLock 을 덤프가 끝날 때까지 쥔다 — 그 사이
    update 의 `ALTER TABLE` 이 상한 없이 기다리면 서비스가 내려간 채 멈춘다. 진짜
    `migrations/env.py` 를 그대로 쓰는 alembic 으로(마이그레이션만 시험용 하나) 남이 쥔 표를
    고쳐 본다."""
    table = f"zz_lock_probe_{uuid.uuid4().hex[:8]}"
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    (scripts / "env.py").symlink_to(BACKEND / "migrations" / "env.py")
    (scripts / "versions" / "zz_lock_probe.py").write_text(
        PROBE_MIGRATION.format(table=table), encoding="utf-8"
    )
    ini = (BACKEND / "alembic.ini").read_text(encoding="utf-8")
    ini = ini.replace("script_location = %(here)s/migrations", f"script_location = {scripts}")
    (tmp_path / "alembic.ini").write_text(ini, encoding="utf-8")

    with engine.connect() as holder:
        had_versions = holder.scalar(text("SELECT to_regclass('alembic_version')")) is not None
        holder.execute(text(f'CREATE TABLE "{table}" (id integer)'))
        holder.commit()
        try:
            holder.execute(text(f'LOCK TABLE "{table}" IN ACCESS SHARE MODE'))  # 덤프처럼 쥔다
            started = time.monotonic()
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    str(tmp_path / "alembic.ini"),
                    "upgrade",
                    "head",
                ],
                cwd=BACKEND,
                env={**os.environ, "MIGRATION_LOCK_TIMEOUT": "1s"},
                capture_output=True,
                text=True,
                timeout=60,
            )
            took = time.monotonic() - started
        finally:
            holder.rollback()
            holder.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
            if not had_versions:
                # 실패한 마이그레이션은 한 트랜잭션이라 남지 않지만 — 혹시 남았으면 치운다.
                holder.execute(text("DROP TABLE IF EXISTS alembic_version"))
            holder.commit()
    assert done.returncode != 0, done.stdout + done.stderr
    assert "lock timeout" in done.stderr, done.stderr[-2000:]
    assert took < 40, took
