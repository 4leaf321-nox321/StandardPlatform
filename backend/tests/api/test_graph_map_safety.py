"""지식 그래프 지도 자동 계산의 안전장치(ADR 0049).

운영의 고장 추적 DB(293GB)에서 전체 배치가 메모리 31GB 를 다 써 강제 종료됐다. 워커는 멎은
작업을 세 번까지 되살렸고, 실패로 끝나면 타이머가 「판이 없다」 를 보고 5분 안에 새로 넣었다 —
한 바퀴마다 임시 파일 50GB. 취소해도 다시 들어왔고 끄는 설정이 없었다. 여기서는 그 고리가
끊기는지(끄는 설정 · 실패 · 취소 뒤 멈춤 · 한 번만 · 취소가 도는 질의까지)와 계산의 상한을
본다.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

from app.config import get_settings
from app.database import SessionLocal
from app.modules.graph import mapbuild
from app.modules.graph.models import MapCounted
from app.modules.jobs import kinds
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from tests.api.conftest import Signed, finish_job
from tests.api.test_graph_map import _map, _relayout, _run, _world


@pytest.fixture(autouse=True)
def _no_map() -> None:
    """지도가 하나도 없던 설치에서 시작한다 — 지도 시험은 DB 를 비우지 않고 쌓으며 돈다. 남은
    대기 작업도 치운다(워커가 그것을 먼저 집으면 이 시험의 작업이 안 돈다)."""
    with SessionLocal() as db:
        db.execute(
            update(Job)
            .where(Job.status.in_(("queued", "running")))
            .values(status="cancelled", finished_at=datetime.now(UTC))
        )
        db.execute(delete(Job).where(Job.kind.in_(mapbuild.KINDS)))
        db.execute(
            text(
                "TRUNCATE graph_map_counts, graph_map_counted, graph_map_links, "
                "graph_map_frozen, graph_map_masters, graph_map_points, graph_map_layouts"
            )
        )
        db.commit()


def _timer() -> str | None:
    with SessionLocal() as db:
        return mapbuild.schedule(db)


def _forget_queued() -> None:
    """타이머가 넣은 것을 지운다 — 다음 판단을 보려고."""
    with SessionLocal() as db:
        db.execute(
            update(Job)
            .where(Job.kind.in_(mapbuild.KINDS), Job.status == "queued")
            .values(status="done", finished_at=datetime.now(UTC))
        )
        db.commit()


def test_끄면_타이머가_지도_작업을_넣지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _world(client, admin)
    monkeypatch.setattr(get_settings(), "graph_map_auto", False)
    assert _timer() is None
    shown = _map(client, admin)
    assert shown["ready"] is False and "GRAPH_MAP_AUTO" in (shown["paused"] or "")
    # 사람이 누르는 「전체 다시 배치」 는 그대로 돈다.
    _relayout(client, admin)
    assert _map(client, admin)["ready"] is True
    with SessionLocal() as db:
        db.execute(
            update(MapCounted).values(counted_at=datetime.now(UTC) - timedelta(hours=2))
        )
        db.commit()
    assert _timer() is None
    monkeypatch.undo()
    assert _timer() == "graph_map_count"


def test_전체_배치가_실패하면_사람이_다시_누를_때까지_넣지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _world(client, admin)
    assert _timer() == "graph_map_full"
    _forget_queued()
    monkeypatch.setattr(mapbuild, "MAX_MASTERS", 1)
    failed = _run("graph_map_full")
    assert failed.status == "failed"
    # 판이 없어도 다시 넣지 않는다 — 넣으면 같은 실패가 5분마다 돈다.
    assert _timer() is None
    shown = _map(client, admin)
    assert "자동 계산을 멈췄습니다" in (shown["paused"] or "")
    assert "실패했습니다" in shown["paused"]
    monkeypatch.undo()
    # 사람이 다시 눌러 끝나면 타이머도 다시 돈다.
    _relayout(client, admin)
    assert _map(client, admin)["paused"] is None
    with SessionLocal() as db:
        db.execute(
            update(MapCounted).values(counted_at=datetime.now(UTC) - timedelta(hours=2))
        )
        db.commit()
    assert _timer() == "graph_map_count"


def test_취소한_전체_배치는_다시_넣지_않는다(client: TestClient, admin: Signed) -> None:
    _world(client, admin)
    assert _timer() == "graph_map_full"
    with SessionLocal() as db:
        job_id = db.scalar(select(Job.id).where(Job.kind == "graph_map_full"))
    cancelled = client.post(f"/api/jobs/{job_id}/cancel", headers=admin.headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert _timer() is None
    assert "취소됐습니다" in (_map(client, admin)["paused"] or "")


def test_집계가_실패하면_여섯_시간_뒤에_다시_본다(client: TestClient, admin: Signed) -> None:
    _world(client, admin)
    _relayout(client, admin)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.execute(update(MapCounted).values(counted_at=now - timedelta(hours=2)))
        failed = Job(
            kind="graph_map_count",
            status="failed",
            params={},
            error="잠깐의 실패",
            finished_at=now - timedelta(hours=1),
        )
        db.add(failed)
        db.commit()
        failed_id = failed.id
    assert _timer() is None
    # 집계의 실패는 지도를 멈추지 않는다.
    assert _map(client, admin)["paused"] is None
    with SessionLocal() as db:
        db.execute(
            update(Job)
            .where(Job.id == failed_id)
            .values(finished_at=now - mapbuild.COUNT_RETRY - timedelta(minutes=1))
        )
        db.commit()
    assert _timer() == "graph_map_count"


def test_기록이_상한을_넘으면_계산하지_않고_바로_거절한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _world(client, admin, records=4)
    monkeypatch.setattr(get_settings(), "graph_map_max_records", 3)
    started = time.monotonic()
    made = client.post("/api/graph/map/relayout", headers=admin.headers)
    done = finish_job(client, admin, made.json())
    assert done["status"] == "failed"
    # 앞선 시험의 기록도 이 DB 에 있다 — 수는 4건 이상.
    assert "GRAPH-0018" in done["error"]
    assert "상한(3건, GRAPH_MAP_MAX_RECORDS)" in done["error"]
    assert time.monotonic() - started < 10
    assert "GRAPH_MAP_MAX_RECORDS" in (_map(client, admin)["last_error"] or "")


def test_짝이_많으면_기록을_고르게_덜어_짝짓는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기록이 서로 다른 것을 많이 가리키면 짝은 그 제곱이다 — 어림이 상한을 넘으면 기록을 id
    해시로 덜어 내고(수는 몫으로 나눠 어림), 판은 그대로 선다."""
    _world(client, admin, records=8)
    monkeypatch.setattr(mapbuild, "COREF_PROBE_PERCENT", 100.0)
    full = _relayout(client, admin)
    # 기록 하나가 모델 · 부품 둘을 가리킨다 — 짝 하나씩(앞선 시험의 기록도 이 DB 에 있다).
    estimated = int(full["coref_pairs_estimated"])
    assert full["coref_share"] == 1.0 and estimated >= 8
    monkeypatch.setattr(mapbuild, "COREF_PAIR_ROWS", estimated // 2)
    sampled = _relayout(client, admin)
    assert sampled["coref_share"] == pytest.approx((estimated // 2) / estimated, abs=1e-4)
    assert sampled["masters"] == full["masters"]
    assert _map(client, admin)["ready"] is True


def test_해시_문턱은_몫만큼을_고른다() -> None:
    from app.modules.graph import maphash

    ids = [uuid.uuid4() for _ in range(4000)]
    for share in (0.1, 0.5, 0.9):
        cut = mapbuild._hash_cut(share)
        picked = sum(1 for one in ids if maphash.hash_of(one) < cut)
        assert abs(picked / len(ids) - share) < 0.05
    assert mapbuild._hash_cut(1.0) == 2**63 - 1


def test_멎은_지도_작업은_다시_돌리지_않는다() -> None:
    """워커를 죽인 것이 그 작업 자체일 수 있다 — 되살리면 또 죽는다."""
    stale = datetime.now(UTC) - timedelta(hours=1)
    with SessionLocal() as db:
        mapped = Job(
            kind="graph_map_full",
            status="running",
            params={},
            worker_id="gone:1",
            attempts=1,
            heartbeat_at=stale,
        )
        other = Job(
            kind="filestore_gc",
            status="running",
            params={},
            worker_id="gone:1",
            attempts=1,
            heartbeat_at=stale,
        )
        db.add_all([mapped, other])
        db.commit()
        ids = (mapped.id, other.id)
        job_services.recover_stale(db)
        first, second = (db.get(Job, one) for one in ids)
        assert first is not None and second is not None
        db.refresh(first)
        db.refresh(second)
        assert first.status == "failed" and "다시 돌리지 않습니다" in (first.error or "")
        assert second.status == "queued"


def test_취소하면_도는_질의까지_끊는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """지도의 짝짓기는 질의 하나가 몇십 분이라 단계 사이가 오지 않는다 — 박동이 취소 요청을
    보면 도는 질의를 끊는다(`stop_midway`)."""

    def slow(work: kinds.Work) -> dict[str, Any]:
        work.db.execute(text("SELECT pg_sleep(30)"))
        raise AssertionError("취소가 이 질의를 끊었어야 한다")

    name = "test_slow_midway"
    monkeypatch.setitem(
        kinds._registry,
        name,
        kinds.Kind(name, "시험", False, False, slow, allow_system=True, stop_midway=True),
    )
    monkeypatch.setattr(job_services, "HEARTBEAT_EVERY", 0.2)
    with SessionLocal() as db:
        job = job_services.enqueue(db, kind=name, params={}, user=None, workspace_id=None)
        db.commit()
        job_id = job.id

    def ask_cancel() -> None:
        time.sleep(0.6)
        with SessionLocal() as db:
            db.execute(update(Job).where(Job.id == job_id).values(cancel_requested=True))
            db.commit()

    threading.Thread(target=ask_cancel).start()
    started = time.monotonic()
    assert job_services.process_one("test-worker")
    assert time.monotonic() - started < 10
    with SessionLocal() as db:
        row = db.get(Job, job_id)
        assert row is not None and row.status == "cancelled"
