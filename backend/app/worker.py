"""작업 워커 — `python -m app.worker`.

앱과 같은 코드 · 같은 `.env` 로 돈다. 표(`jobs`)에서 `queued` 를 하나씩 집어 돌리고, 30초마다
심장박동을 남기고, 심장박동이 멎은 남의 작업을 되돌린다. A · B 두 대에 하나씩 떠도 같은 행을
두 번 안 집는다(`FOR UPDATE SKIP LOCKED`). 설계는 `docs/작업-워커-설계.md`.

    cd backend && .venv/bin/python -m app.worker          # 개발
    systemd: <slug>-worker.service                          # 운영(deploy.sh 가 만든다)
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from app.config import get_settings
from app.database import SessionLocal
from app.logging_setup import setup_logging
from app.modules.jobs import services
from app.modules.webhooks import services as webhooks

log = logging.getLogger("app.worker")

HEARTBEAT_EVERY = 30.0
RECOVER_EVERY = 60.0
PURGE_EVERY = 3600.0
IDLE_MAX = 5.0


class Worker:
    def __init__(self) -> None:
        self.worker_id = services.worker_identity()
        self.stop = False
        self._last_beat = 0.0
        self._last_recover = 0.0
        self._last_purge = 0.0
        self._last_webhook = 0.0

    def _tick_housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_beat >= HEARTBEAT_EVERY:
            services.heartbeat(self.worker_id, None)
            self._last_beat = now
        if now - self._last_recover >= RECOVER_EVERY:
            with SessionLocal() as db:
                revived = services.recover_stale(db)
            if revived:
                log.warning("멎은 작업 %d개를 되살렸습니다", revived)
            self._last_recover = now
        if now - self._last_webhook >= webhooks.RETRY_AFTER_SECONDS:
            # **밀린 웹훅은 여기서 다시 살아난다.** 전에는 프로세스 안의 타이머였고, 앱을
            # 재시작하면 그 타이머가 사라져 다음 이벤트가 올 때까지 안 나갔다.
            with SessionLocal() as db:
                if webhooks.pending_count(db):
                    webhooks.enqueue_dispatch(db)
            self._last_webhook = now
        if now - self._last_purge >= PURGE_EVERY:
            with SessionLocal() as db:
                gone = services.purge_expired_files(db)
                # 기록도 함께 — 타이머가 하루 288행을 넣는다. 안 지우면 사람이 올린 작업이
                # 그 사이에 파묻힌다.
                old_jobs = services.purge_old_jobs(db)
            if gone or old_jobs:
                log.info("정리: 작업 파일 %d개 · 끝난 작업 기록 %d개", gone, old_jobs)
            self._last_purge = now

    def run_forever(self) -> None:
        settings = get_settings()
        idle = settings.worker_poll_seconds
        log.info("워커 시작: %s", self.worker_id)
        services.heartbeat(self.worker_id, None)
        while not self.stop:
            try:
                self._tick_housekeeping()
                worked = services.process_one(self.worker_id)
                self._last_beat = time.monotonic()
            except Exception:  # 워커는 죽지 않는다 — DB 가 잠깐 끊겨도 다음 바퀴에 다시.
                log.exception("워커 바퀴 실패 — %.0f초 뒤 다시", IDLE_MAX)
                worked = False
                time.sleep(IDLE_MAX)
                continue
            if worked:
                idle = settings.worker_poll_seconds
            else:
                # 비어 있으면 천천히 — 빈 표를 2초마다 두드리는 것은 DB 에 대한 예의가 아니다.
                time.sleep(idle)
                idle = min(idle * 1.5, IDLE_MAX)
        log.info("워커 종료: %s", self.worker_id)

    def handle_signal(self, signum: int, _frame: Any) -> None:
        log.info("신호 %s — 하던 것을 끝내고 멈춥니다", signum)
        self.stop = True


def main() -> None:
    setup_logging(get_settings())
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.handle_signal)
    signal.signal(signal.SIGINT, worker.handle_signal)
    worker.run_forever()


if __name__ == "__main__":
    main()
