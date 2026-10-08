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
import threading
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
#: 고아 첨부 정리는 기동 한 시간 뒤가 첫 차례 — 업데이트로 워커가 자주 다시 떠도 밀리지 않게,
#: 그렇다고 기동하자마자 저장소 전체를 훑지는 않게.
GC_FIRST_AFTER = 3600.0


def _clean_orphans() -> None:
    """고아 첨부 정리 한 차례 — 워커의 바퀴와 **다른 스레드**에서(제 세션으로) 돈다."""
    from app.modules.files import gc

    try:
        with SessionLocal() as db:
            done = gc.clean(db, gc.scan(db))
    except Exception:
        log.exception("고아 첨부 정리 실패 — 다음 차례에 다시")
        return
    if done.files or done.temp:
        log.info(
            "고아 첨부 정리: 파일 %d개(%.1fMB) · 임시 파일 %d개",
            done.files,
            done.bytes / 1024 / 1024,
            done.temp,
        )


class Worker:
    def __init__(self) -> None:
        self.worker_id = services.worker_identity()
        self.stop = False
        self._last_beat = 0.0
        self._last_recover = 0.0
        self._last_purge = 0.0
        self._last_webhook = 0.0
        hours = get_settings().filestore_gc_hours
        self._gc_every = hours * 3600.0 if hours > 0 else 0.0
        self._last_gc = time.monotonic() - max(self._gc_every - GC_FIRST_AFTER, 0.0)
        self._gc_thread: threading.Thread | None = None

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
                # 끊긴 선의 무덤도 — 안 지우면 내보내기가 몇 해치 이력을 통째로 보낸다.
                old_graves = services.purge_old_tombstones(db)
                # 되돌릴 기록은 바뀐 줄마다 한 줄이다 — 안 지우면 데이터보다 커진다.
                old_undo = services.purge_old_undo_journals(db)
            if gone or old_jobs or old_graves or old_undo:
                log.info(
                    "정리: 작업 파일 %d개 · 끝난 작업 기록 %d개 · 끊긴 선의 무덤 %d개 · "
                    "되돌릴 기록 %d줄",
                    gone,
                    old_jobs,
                    old_graves,
                    old_undo,
                )
            self._last_purge = now
        if self._gc_every and now - self._last_gc >= self._gc_every:
            # **아무 첨부도 안 가리키는 파일** — 거절된 업로드 · 뗀 첨부의 파일이 쌓이기만 했다
            # (`files/gc.py`). 서버마다 저장소가 따로면 각자 제 것을, 같이 쓰면 둘이 같은 것을
            # 훑는다 — 지우기는 없는 파일을 넘어가므로 겹쳐도 된다.
            # **실패해도 차례는 넘긴다** — 시각을 안 옮기면 5초마다 저장소 전체를 다시 훑는다.
            self._last_gc = now
            if self._gc_thread is not None and self._gc_thread.is_alive():
                log.warning("앞 차례의 고아 첨부 정리가 아직 돕니다 — 이번 차례는 건너뜁니다")
            else:
                # ⚠️ **따로 돈다.** 이 바퀴 안에서 돌면 큰 저장소를 훑는 동안 박동이 멎어, 5분을
                #    넘기면 살아 있는 워커가 화면에서 「꺼짐」 이 되고 그동안 작업 · 웹훅도 못
                #    집었다(2026-10-08). 정리는 파일과 제 연결만 만지므로 바퀴와 나란히 돌아도
                #    된다.
                self._gc_thread = threading.Thread(
                    target=_clean_orphans, name="filestore-gc", daemon=True
                )
                self._gc_thread.start()

    def run_forever(self) -> None:
        settings = get_settings()
        idle = settings.worker_poll_seconds
        log.info("워커 시작: %s", self.worker_id)
        services.heartbeat(self.worker_id, None)
        while not self.stop:
            try:
                self._tick_housekeeping()
                worked = services.process_one(self.worker_id)
                if worked:
                    # 작업을 돌렸으면 `process_one` 이 끝에서 박동을 남겼다 — 30초를 거기서
                    # 다시 센다. ⚠️ **빈 바퀴에서는 되감지 않는다.** 예전에는 바퀴마다 되감아,
                    # 쉬는 워커(바퀴가 몇 초)는 30초에 영영 못 닿고 기동 뒤로 박동이 멎었다 —
                    # 5분 뒤 살아 있는 워커가 화면에서 「꺼짐」 이 됐다.
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
        if self._gc_thread is not None and self._gc_thread.is_alive():
            # 데몬 스레드라 프로세스와 함께 끊긴다 — 지우기는 파일 하나씩이라 반쯤 남아도
            # 다음 차례가 처음부터 다시 훑는다.
            log.info("고아 첨부 정리가 도는 중에 멈춥니다 — 다음 차례에 처음부터 다시")
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
