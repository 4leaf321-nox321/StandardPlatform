"""워커의 박동 — **쉬고 있어도 뛴다.**

화면(작업 · 서버)과 홈의 「남은 일」 은 `worker_heartbeats.last_seen` 으로 워커가 살아 있는지
본다. 박동이 `worker_stale_seconds`(5분)보다 오래 없으면 「꺼짐」 이다.

예전에는 바퀴마다 「방금 박동했다」 로 시계를 되감았다 — 작업이 없어 박동을 **안 남긴**
바퀴까지. 쉬는 워커는 바퀴가 몇 초라 30초에 영영 못 닿았고, 기동 때 한 번 뛴 뒤로 박동이
멎었다. 그래서 작업이 끝나고 5분이 지나면 **살아서 표를 두드리는 워커가 「꺼짐」 으로**
보였다. 시계를 가짜로 돌려 그 10분을 흉내 낸다 — DB 는 안 쓴다.
"""

from __future__ import annotations

import contextlib
from itertools import pairwise
from typing import Any

import pytest

from app import worker as worker_module
from app.config import get_settings
from app.modules.jobs import services as job_services
from app.modules.webhooks import services as webhook_services


class _Clock:
    """`time` 대신 — 잠들면 그만큼 시계가 간다. 끝 시각을 넘으면 워커를 세운다."""

    def __init__(self, worker: worker_module.Worker, until: float) -> None:
        self.now = 1_000.0
        self.worker = worker
        self.until = self.now + until

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.now >= self.until:
            self.worker.stop = True


def _run(monkeypatch: pytest.MonkeyPatch, *, minutes: int, jobs: set[int]) -> list[float]:
    """워커를 `minutes` 분 돌리고 박동이 남은 시각들을 돌려준다 — **끝 시각을 마지막에 붙여.**

    끝 시각까지 넣는 까닭: 고장은 「마지막 박동 뒤로 끝까지 조용한 것」 이었다. 박동 사이의
    간격만 재면 그 꼬리를 못 본다(처음 쓴 시험이 그래서 고장 난 워커에서도 통과했다).

    `jobs` 는 작업을 집는 바퀴 번호들이다(나머지 바퀴는 빈 표).
    """
    worker = worker_module.Worker()
    clock = _Clock(worker, until=minutes * 60)
    beats: list[float] = []
    turn = {"n": 0}

    def process_one(_worker_id: str) -> bool:
        turn["n"] += 1
        if turn["n"] in jobs:
            # 진짜 `process_one` 처럼 — 작업을 돌리면 끝에서 박동을 남긴다.
            clock.now += 3.0
            beats.append(clock.now)
            return True
        return False

    # 워커가 부르는 것은 이 모듈들의 속성이다 — 같은 모듈 객체를 고친다.
    services: Any = job_services
    monkeypatch.setattr(worker_module, "time", clock)
    monkeypatch.setattr(worker_module, "SessionLocal", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(services, "heartbeat", lambda *_a: beats.append(clock.now))
    monkeypatch.setattr(services, "process_one", process_one)
    monkeypatch.setattr(services, "recover_stale", lambda _db: 0)
    for name in (
        "purge_expired_files",
        "purge_old_jobs",
        "purge_old_tombstones",
        "purge_old_undo_journals",
    ):
        monkeypatch.setattr(services, name, lambda _db: 0)
    monkeypatch.setattr(webhook_services, "pending_count", lambda _db: 0)
    worker.run_forever()
    return [*beats, clock.now]


def _longest_gap(beats: list[float]) -> float:
    return max(after - before for before, after in pairwise(beats))


def test_쉬는_워커도_박동한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업이 하나도 없는 10분 — 박동이 30초(+한 바퀴)를 넘게 끊기면 안 된다."""
    beats = _run(monkeypatch, minutes=10, jobs=set())
    limit = worker_module.HEARTBEAT_EVERY + worker_module.IDLE_MAX
    assert len(beats) >= 10, beats
    assert _longest_gap(beats) <= limit, beats
    # 화면이 「꺼짐」 으로 판정하는 문턱보다 훨씬 안쪽이어야 한다.
    assert _longest_gap(beats) < get_settings().worker_stale_seconds


def test_작업을_돌린_뒤에도_박동이_이어진다(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업 몇 개 뒤 다시 쉴 때 — 고장은 정확히 여기서 보였다(끝나고 5분 뒤 「꺼짐」)."""
    beats = _run(monkeypatch, minutes=10, jobs={3, 4, 10})
    limit = worker_module.HEARTBEAT_EVERY + worker_module.IDLE_MAX
    assert _longest_gap(beats) <= limit, beats
