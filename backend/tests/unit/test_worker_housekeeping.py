"""워커의 살림살이 — **하나가 실패해도 작업은 돈다**(2026-11-28 점검).

예전에는 살림살이 전부와 작업 집기(`process_one`)가 한 `try` 안에 있었고, 차례 시각은 성공한
뒤에만 옮겼다. 첨부 저장소의 작업 폴더(`_jobs`)가 권한 오류를 내면 정리가 5초마다 다시 돌다
실패했고, 그 뒤의 `process_one` 에는 영영 닿지 않았다 — 박동은 맨 앞이라 화면은 워커를 「살아
있음」 으로 보였다. 가짜 시계로 몇 분을 흉내 낸다 — DB 는 안 쓴다(가짜 세션).
"""

from __future__ import annotations

import contextlib
from itertools import pairwise
from typing import Any

import pytest

from app import worker as worker_module
from app.modules.audit import retention
from app.modules.jobs import services as job_services
from app.modules.server import performance
from app.modules.webhooks import services as webhook_services
from tests.unit.test_worker_heartbeat import _Clock

_PURGES = (
    "purge_expired_files",
    "purge_old_jobs",
    "purge_old_tombstones",
    "purge_old_undo_journals",
)


def _world(
    monkeypatch: pytest.MonkeyPatch, *, minutes: float
) -> tuple[worker_module.Worker, _Clock, dict[str, Any]]:
    """살림살이를 전부 조용한 가짜로 — 시험마다 하나를 실패로 바꿔 끼운다. 돌려주는 `seen` 에
    불린 수가 쌓인다(`process_one` · 알림 · 정리마다)."""
    worker = worker_module.Worker()
    clock = _Clock(worker, until=minutes * 60)
    # 단조 시계는 기동 뒤로 한참 간 값이다 — 1,000초에서 시작하면 한 시간 간격의 정리가 첫
    # 바퀴의 차례가 아니다.
    clock.now = 100_000.0
    clock.until = clock.now + minutes * 60
    seen: dict[str, Any] = {"process_one": 0, "notified": [], "calls": {}}

    def process_one(_worker_id: str) -> bool:
        seen["process_one"] += 1
        return False

    def counted(name: str) -> Any:
        def run(_db: Any) -> int:
            seen["calls"][name] = seen["calls"].get(name, 0) + 1
            return 0

        return run

    services: Any = job_services
    monkeypatch.setattr(worker_module, "time", clock)
    monkeypatch.setattr(worker, "waker", clock)
    monkeypatch.setattr(worker_module, "SessionLocal", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(services, "heartbeat", lambda *_a: None)
    monkeypatch.setattr(services, "process_one", process_one)
    monkeypatch.setattr(services, "recover_stale", lambda _db: 0)
    for name in _PURGES:
        monkeypatch.setattr(services, name, counted(name))
    monkeypatch.setattr(performance, "purge_old_timings", counted("purge_old_timings"))
    monkeypatch.setattr(retention, "purge_old_access_logs", counted("purge_old_access_logs"))
    monkeypatch.setattr(webhook_services, "pending_count", lambda _db: 0)
    # 넣기(지도 · 계산 · 검토함 · 뜻 찾기 · 별칭)는 가짜 세션에서 돌 수 없다 — 조용히.
    for key in (
        "map_schedule",
        "calc_schedule",
        "review_schedule",
        "semantic",
        "alias_suggest",
    ):
        monkeypatch.setattr(worker, f"_{key}", lambda: None)
    monkeypatch.setattr(
        worker_module,
        "_notify_step_failed",
        lambda label, failures, _error: seen["notified"].append((label, failures)),
    )
    return worker, clock, seen


def test_정리가_실패해도_작업은_계속_집고_정리는_한_시간_뒤에_다시(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _clock, seen = _world(monkeypatch, minutes=10)
    tried = {"n": 0}

    def denied(_db: Any) -> int:
        tried["n"] += 1
        raise PermissionError(13, "Permission denied", "/data/filestore/_jobs")

    monkeypatch.setattr(job_services, "purge_expired_files", denied)
    worker.run_forever()

    # 10분 동안 바퀴는 몇 초마다 — 작업 집기는 쉬지 않는다.
    assert seen["process_one"] >= 100, seen
    # 실패한 정리를 5초마다 되풀이하지 않는다(간격은 한 시간 그대로).
    assert tried["n"] == 1
    # 같은 차례의 다른 정리는 그대로 돈다 — 작업 파일 정리 하나 때문에 기록이 쌓이지 않게.
    assert seen["calls"].get("purge_old_jobs") == 1
    assert seen["calls"].get("purge_old_access_logs") == 1


def test_계속_실패하는_살림살이는_간격을_늘리고_두_번째부터_알린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _clock, seen = _world(monkeypatch, minutes=30)
    tried: list[float] = []

    def broken() -> None:
        tried.append(_clock.now)
        raise OSError(116, "Stale file handle")

    monkeypatch.setattr(worker, "_recover", broken)
    worker.run_forever()

    # 60초 간격이 실패마다 두 배(120 · 240 · 480 · 900초 끝) — 30분에 다섯 번쯤, 바퀴마다가
    # 아니다.
    gaps = [after - before for before, after in pairwise(tried)]
    assert 4 <= len(tried) <= 6, tried
    assert gaps == sorted(gaps) and gaps[0] >= 120 and max(gaps) <= 900 + 10, gaps
    assert seen["process_one"] >= 100
    # 한 번의 끊김으로는 안 알린다 — 잇달아 두 번째부터(같은 말을 줄이는 것은 알림 쪽이 한다).
    label = next(step.label for step in worker.steps if step.key == "recover")
    assert seen["notified"][0] == (label, worker_module.STEP_NOTIFY_AFTER)
    assert all(failures >= 2 for _label, failures in seen["notified"])


def test_다시_되면_간격이_돌아온다(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, _clock, _seen = _world(monkeypatch, minutes=12)
    tried: list[float] = []

    def flaky() -> None:
        tried.append(_clock.now)
        if len(tried) == 1:
            raise ConnectionError("DB 재시작")

    monkeypatch.setattr(worker, "_recover", flaky)
    worker.run_forever()

    step = next(one for one in worker.steps if one.key == "recover")
    assert step.failures == 0
    # 첫 실패 뒤 120초, 그다음부터는 다시 60초마다.
    assert tried[1] - tried[0] >= 120
    assert all(60 <= after - before < 70 for before, after in pairwise(tried[1:]))


def test_살림살이_도중에_멈추라는_신호가_오면_새_작업을_안_집는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """신호는 바퀴 어디서든 온다 — 살림살이 도중에 왔는데 같은 바퀴의 `process_one` 이 새
    작업을 집으면, systemd 가 기다리다 죽일 때 그 작업은 막 시작한 채 끊긴다."""
    worker, _clock, seen = _world(monkeypatch, minutes=5)
    stopping: list[str] = []
    monkeypatch.setattr(job_services, "mark_stopping", stopping.append)

    def signalled() -> None:
        worker.handle_signal(15, None)

    monkeypatch.setattr(worker, "_recover", signalled)
    worker.run_forever()

    assert seen["process_one"] == 0
    assert stopping == [worker.worker_id]  # 쥔 작업에 「멈추는 중」 을 적으러 갔다


@pytest.mark.parametrize("failures", [1024, 10**6])
def test_간격은_실패가_아무리_쌓여도_끝값에서_멈춘다(failures: int) -> None:
    """`float(2**1024)` 는 OverflowError 다 — 15분 끝에서 열흘 남짓 잇달아 실패하면 닿는다(한
    시간 간격의 정리는 43일쯤). 그 예외가 바퀴 밖으로 새면 그 일은 다시는 안 돌고 뒤의 일이
    모두 건너뛰어졌다(2026-11-28 검토)."""
    short = worker_module.Step("recover", "멎은 작업 복구", 60.0, failures=failures)
    hourly = worker_module.Step("purge", "오래된 기록 정리", 3600.0, failures=failures)
    assert short.wait() == worker_module.STEP_BACKOFF_MAX
    assert hourly.wait() == 3600.0
    assert short.due(10**9) is True


class _BrokenStep(worker_module.Step):
    """차례 셈 자체가 실패하는 일 — 일이 아니라 그 둘레의 고장을 흉내 낸다."""

    def due(self, now: float) -> bool:
        raise ArithmeticError("차례를 셀 수 없음")


def test_한_가지의_차례_셈이_실패해도_뒤의_일과_작업_집기는_돈다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _clock, seen = _world(monkeypatch, minutes=5)
    first = worker.steps[0]
    worker.steps[0] = _BrokenStep(first.key, first.label, first.every)

    worker.run_forever()

    # 맨 뒤의 정리(한 시간 간격 — 첫 바퀴가 차례)가 돌았다 — 앞의 고장이 바퀴를 끊지 않았다.
    assert seen["calls"].get("purge_old_jobs") == 1
    assert seen["process_one"] >= 50
