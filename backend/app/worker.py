"""작업 워커 — `python -m app.worker`.

앱과 같은 코드 · 같은 `.env` 로 돈다. 표(`jobs`)에서 `queued` 를 하나씩 집어 돌리고, 30초마다
심장박동을 남기고, 심장박동이 멎은 남의 작업을 되돌린다. A · B 두 대에 하나씩 떠도 같은 행을
두 번 안 집는다(`FOR UPDATE SKIP LOCKED`). 설계는 `docs/작업-워커-설계.md`.

    cd backend && .venv/bin/python -m app.worker          # 개발
    systemd: <slug>-worker.service                          # 운영(deploy.sh 가 만든다)
"""

from __future__ import annotations

import contextlib
import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import SessionLocal
from app.logging_setup import setup_logging
from app.modules.audit import retention
from app.modules.jobs import services
from app.modules.server import performance
from app.modules.webhooks import services as webhooks

log = logging.getLogger("app.worker")

HEARTBEAT_EVERY = 30.0
RECOVER_EVERY = 60.0
PURGE_EVERY = 3600.0
IDLE_MAX = 5.0
#: 지식 그래프 지도(ADR 0046)의 차례를 보는 간격 — 처음 판 · 매일 밤 덧붙임 · 한 시간마다
#: 집계를 넣을지 DB 의 시각으로 판단한다(재시작해도 밀리지 않는다).
MAP_CHECK_EVERY = 300.0
#: 뜻으로 찾기(ADR 0052)의 차례를 보는 간격 — 새것 · 고친 것이 있으면 계산을 넣는다(하루 한
#: 번은 전부).
SEMANTIC_CHECK_EVERY = 300.0
#: 별칭 후보의 「이것 아닐까」 미리 셈(ADR 0074)의 차례를 보는 간격 — 셈할 줄이 있으면 넣는다.
#: 작업은 다른 작업이 기다리면 비켜 주므로(`misses.refresh`) 짧게 봐도 된다 — 새로 세 번을 넘긴
#: 줄이 몇 분 안에 셈해진다.
ALIAS_SUGGEST_CHECK_EVERY = 60.0
#: 고아 첨부 정리는 기동 한 시간 뒤가 첫 차례 — 업데이트로 워커가 자주 다시 떠도 밀리지 않게,
#: 그렇다고 기동하자마자 저장소 전체를 훑지는 않게.
GC_FIRST_AFTER = 3600.0
#: 실패한 살림살이를 다시 해 보기까지 늘리는 간격의 끝(초) — 실패할 때마다 두 배로 늘리되
#: 이보다는 안 늘린다(원래 간격이 이보다 길면 원래 간격 그대로).
STEP_BACKOFF_MAX = 900.0
#: 간격을 늘리는 지수의 끝 — 실패 수가 아무리 쌓여도 2**16 배에서 멈춘다(`Step.wait`).
STEP_BACKOFF_EXPONENT_MAX = 16
#: 살림살이가 **잇달아 이만큼 실패하면** 시스템 관리자에게 알린다 — 한 번의 끊김(DB 재시작
#: 등)으로는 안 알린다. 같은 일은 `notifications.NOTICE_EVERY` 안에 한 번.
STEP_NOTIFY_AFTER = 2


def _notify_step_failed(label: str, failures: int, error: BaseException) -> None:
    """살림살이 「`label`」 이 계속 실패한다고 시스템 관리자에게 — `NOTICE_EVERY` 안에 한 번.

    로그에만 남기면 아무도 안 읽는다: 첨부 저장소의 작업 폴더가 권한 오류를 내던 때는 정리가
    5초마다 다시 돌다 실패했고, 그 바퀴에 끼어 있던 작업은 하나도 안 돌았다(2026-11-28 점검).
    알리기가 실패해도(DB 가 끊겼다) 워커는 멈추지 않는다."""
    from app.modules.notifications import services as notifications

    try:
        with SessionLocal() as db:
            if notifications.notify_system_admins_once(
                db,
                kind=notifications.WORKER_STEP_FAILED,
                title=f"작업 워커의 「{label}」 이(가) 계속 실패합니다",
                body=(
                    f"{failures}번 잇달아 실패했습니다 — 마지막 오류: "
                    f"{type(error).__name__}: {error}"[:600]
                    + " 워커는 이 일만 간격을 늘려 다시 시도하고 작업은 계속 실행합니다. "
                    "원인(권한 · 디스크 · 공용 폴더 마운트 등)을 해결하면 다음 차례에 "
                    "자동으로 복구됩니다. 자세한 내용은 워커 로그"
                    "(journalctl -u <slug>-worker)."
                ),
                link="/admin/server",
            ):
                db.commit()
    except Exception:
        log.warning("살림살이 실패 알림을 남기지 못했습니다(%s)", label, exc_info=True)


def _clean_orphans() -> None:
    """고아 첨부 정리 한 차례 — 워커의 바퀴와 **다른 스레드**에서(제 세션으로) 돈다."""
    from app.modules.files import gc

    try:
        with SessionLocal() as db:
            found = gc.scan(db)
            foreign = gc.looks_foreign(found)
            if foreign:
                # 지우지 않고 알린다 — 다른 DB 에 붙은 워커가 남의 저장소를 비우는 것을 막는다.
                # 임시 파일 정리도 미룬다(저장소가 이 설치의 것이 아닐 수 있다).
                gc.halt(db, found, foreign)
                return
            done = gc.clean(db, found)
    except Exception as caught:
        log.exception("고아 첨부 정리 실패 — 다음 차례에 다시")
        # 차례가 하루쯤에 한 번이라 한 번의 실패도 알린다(같은 말은 `NOTICE_EVERY` 안에 한 번).
        _notify_step_failed("고아 파일 정리", 1, caught)
        return
    if done.files or done.temp:
        log.info(
            "고아 첨부 정리: 파일 %d개(%.1fMB) · 임시 파일 %d개",
            done.files,
            done.bytes / 1024 / 1024,
            done.temp,
        )


class Waker:
    """**작업이 들어오면 바로 깬다** — `LISTEN`(`services.WAKE_CHANNEL`) 한 연결로 기다린다.

    예전에는 빈 워커가 2초에서 5초까지 늦춰 가며 물어, 넣고 집기까지 1 ~ 5초가 들었다 —
    계획과 적용 두 번이면 그것이 두 번이다(2026-10-09). 연결이 안 되거나 끊기면 그냥 그
    시간만큼 잔다(예전과 같다) — 다음 기다림에 다시 붙는다. 일하는 동안 온 알림은 연결에 쌓여
    있다가 다음 기다림을 곧장 끝낸다(빈 바퀴 한 번이면 된다)."""

    def __init__(self) -> None:
        self._conn: psycopg.Connection[Any] | None = None

    def _connected(self) -> psycopg.Connection[Any] | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            url = make_url(get_settings().database_url).set(drivername="postgresql")
            conn = psycopg.connect(url.render_as_string(hide_password=False), autocommit=True)
            conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(services.WAKE_CHANNEL)))
        except Exception:
            log.warning(
                "작업 알림을 들을 연결을 못 열었습니다 — 시간마다 묻습니다", exc_info=True
            )
            self._conn = None
            return None
        self._conn = conn
        return conn

    def wait(self, seconds: float) -> None:
        conn = self._connected()
        if conn is None:
            time.sleep(seconds)
            return
        try:
            for _ in conn.notifies(timeout=seconds, stop_after=1):
                break
        except Exception:
            log.warning("작업 알림 연결이 끊겼습니다 — 다음에 다시 붙습니다", exc_info=True)
            self.close()
            time.sleep(seconds)

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):  # 닫는 것은 실패해도 할 일이 없다
                self._conn.close()
        self._conn = None


@dataclass
class Step:
    """살림살이 한 가지의 차례 — **실패해도 차례는 넘긴다.**

    예전에는 살림살이 전부와 작업 집기(`process_one`)가 한 `try` 안에 있었고, 차례 시각은
    성공한 뒤에만 옮겼다. 그래서 하나가 실패하면(작업 폴더의 권한 오류로 정리가 실패) 그 정리가
    5초마다 다시 돌다 실패했고, 그 뒤의 `process_one` 에는 **영영 닿지 않았다** — 박동은 맨
    앞이라 화면은 워커를 「살아 있음」 으로 보였다(2026-11-28 점검). 이제 하나하나 따로 잡고,
    실패하면 간격을 두 배씩 늘려(`STEP_BACKOFF_MAX` 까지) 다시 해 본다."""

    key: str
    label: str
    every: float
    last: float = 0.0
    failures: int = 0

    def wait(self) -> float:
        if not self.failures:
            return self.every
        # 지수를 묶는다 — `float(2**1024)` 는 OverflowError 다(15분 끝에서 열흘 남짓, 한 시간
        # 간격의 정리는 43일쯤 잇달아 실패하면 닿는다). 2**16 이면 어느 간격이든 이미 끝값이다.
        grown = self.every * float(2 ** min(self.failures, STEP_BACKOFF_EXPONENT_MAX))
        return min(grown, max(self.every, STEP_BACKOFF_MAX))

    def due(self, now: float) -> bool:
        return now - self.last >= self.wait()


class Worker:
    def __init__(self) -> None:
        self.worker_id = services.worker_identity()
        self.stop = False
        self.waker = Waker()
        self._last_beat = 0.0
        # 차례마다 부르는 것은 `self._<key>` — 시험이 메서드를 바꿔 끼울 수 있게 이름으로
        # 찾는다.
        self.steps = [
            Step("recover", "멎은 작업 복구", RECOVER_EVERY),
            Step("webhooks", "밀린 웹훅 재발송", webhooks.RETRY_AFTER_SECONDS),
            Step("map_schedule", "지식 그래프 지도 자동 계산", MAP_CHECK_EVERY),
            Step("calc_schedule", "밤 그래프 계산 자동 실행", MAP_CHECK_EVERY),
            Step("review_schedule", "검토함 자동 확인", MAP_CHECK_EVERY),
            Step("semantic", "뜻으로 찾기 자동 계산", SEMANTIC_CHECK_EVERY),
            Step("alias_suggest", "별칭 후보 「이것 아닐까」 계산", ALIAS_SUGGEST_CHECK_EVERY),
            Step("purge", "오래된 기록 정리", PURGE_EVERY),
        ]
        hours = get_settings().filestore_gc_hours
        self._gc_every = hours * 3600.0 if hours > 0 else 0.0
        self._last_gc = time.monotonic() - max(self._gc_every - GC_FIRST_AFTER, 0.0)
        self._gc_thread: threading.Thread | None = None

    def _tick_housekeeping(self) -> None:
        """살림살이를 차례대로 — **하나가 실패해도 나머지와 작업 집기는 그대로 간다.** 예외를
        밖으로 내지 않는다."""
        now = time.monotonic()
        if now - self._last_beat >= HEARTBEAT_EVERY:
            try:
                services.heartbeat(self.worker_id, None)
                self._last_beat = now
            except Exception:  # DB 가 잠깐 끊겼다 — 다음 바퀴에 다시(박동은 늦추지 않는다)
                log.warning("워커 박동을 남기지 못했습니다 — 다음 바퀴에 다시", exc_info=True)
        for step in self.steps:
            if self.stop:
                return
            # **차례 셈 · 기록 · 알림까지 이 안에서** — 일 자체가 아니라 그 둘레(간격 셈 ·
            # 로그 · 알림)가 실패해도 그 한 가지만 건너뛰고 다음 일 · 고아 정리 · 작업 집기로
            # 간다. 둘레가 밖으로 새던 때는 그 일이 다시는 안 돌고 뒤의 일이 모두
            # 건너뛰어졌다(간격 셈의 OverflowError — 2026-11-28 검토).
            try:
                self._run_step(step, now)
            except Exception:
                step.last = now
                log.exception(
                    "살림살이 「%s」 의 차례를 다루지 못했습니다 — 다음 차례에", step.label
                )
        try:
            self._maybe_clean_orphans(now)
        except Exception:
            self._last_gc = now
            log.exception("고아 파일 정리를 시작하지 못했습니다 — 다음 차례에")

    def _run_step(self, step: Step, now: float) -> None:
        if not step.due(now):
            return
        # **시작할 때 옮긴다** — 실패해도 다음 바퀴에 곧장 다시 돌지 않는다.
        step.last = now
        run: Callable[[], None] = getattr(self, f"_{step.key}")
        try:
            run()
        except Exception as caught:
            step.failures += 1
            log.exception(
                "살림살이 「%s」 실패(%d번째 잇달아) — %.0f초 뒤 다시",
                step.label,
                step.failures,
                step.wait(),
            )
            if step.failures >= STEP_NOTIFY_AFTER:
                _notify_step_failed(step.label, step.failures, caught)
        else:
            if step.failures:
                log.info("살림살이 「%s」 가 다시 됩니다", step.label)
            step.failures = 0

    def _maybe_clean_orphans(self, now: float) -> None:
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

    # --- 살림살이 하나하나 — 실패는 `_tick_housekeeping` 이 잡는다 -----------------------

    def _recover(self) -> None:
        with SessionLocal() as db:
            revived = services.recover_stale(db)
        if revived:
            log.warning("멎은 작업 %d개를 되살렸습니다", revived)

    def _webhooks(self) -> None:
        # **밀린 웹훅은 여기서 다시 살아난다.** 전에는 프로세스 안의 타이머였고, 앱을
        # 재시작하면 그 타이머가 사라져 다음 이벤트가 올 때까지 안 나갔다.
        with SessionLocal() as db:
            if webhooks.pending_count(db):
                webhooks.enqueue_dispatch(db)

    # 지도 · 밤 그래프 계산 · 검토함 자동 확인은 **넣기만** 한다 — 도는 것은 다음 바퀴의
    # `process_one` 이다. 셋을 따로 두는 것은 하나가 실패해도 나머지의 간격이 안 늘게.

    def _map_schedule(self) -> None:
        from app.modules.graph import mapbuild

        with SessionLocal() as db:
            queued = mapbuild.schedule(db)
        if queued:
            log.info("지식 그래프 지도 작업을 넣었습니다: %s", queued)

    def _calc_schedule(self) -> None:
        # 밤 그래프 계산(ADR 0067)
        from app.modules.graph import calc as graph_calc

        with SessionLocal() as db:
            queued = graph_calc.schedule(db)
        if queued:
            log.info("밤 그래프 계산을 넣었습니다: %s", queued)

    def _review_schedule(self) -> None:
        # 검토함의 자동 확인(ADR 0075) — 켠 종류가 없으면 안 넣는다.
        from app.modules.review import services as review_services

        with SessionLocal() as db:
            auto = review_services.schedule_auto(db)
        if auto:
            log.info("검토함 자동 확인을 넣었습니다: %s", auto)

    def _semantic(self) -> None:
        # 뜻으로 찾기의 계산도 **넣기만** 한다 — 켠 타입이 없거나 끔이면 아무것도 안 넣는다.
        from app.modules.semantic import services as semantic

        with SessionLocal() as db:
            queued = semantic.schedule(db)
        if queued:
            log.info("뜻으로 찾기 계산을 넣었습니다: %s", queued)

    def _alias_suggest(self) -> None:
        # 별칭 후보의 「이것 아닐까」 도 **넣기만** 한다 — 셈할 줄이 없으면 안 넣는다.
        from app.modules.objects import misses

        with SessionLocal() as db:
            if misses.schedule(db):
                log.info("별칭 후보 「이것 아닐까」 셈을 넣었습니다")

    def _purge(self) -> None:
        """오래된 것 정리 — **하나하나 제 세션으로.** 하나가 실패해도(작업 폴더의 권한 오류로
        작업 파일 정리가 실패) 나머지 기록은 지운다. 실패는 모아 한 번에 낸다."""
        counts: dict[str, int] = {}
        failed: list[str] = []
        for what, purge in (
            ("작업 파일", services.purge_expired_files),
            # 기록도 함께 — 타이머가 하루 288행을 넣는다. 안 지우면 사람이 올린 작업이
            # 그 사이에 파묻힌다.
            ("끝난 작업 기록", services.purge_old_jobs),
            # 끊긴 선의 무덤도 — 안 지우면 내보내기가 몇 해치 이력을 통째로 보낸다.
            ("끊긴 선의 무덤", services.purge_old_tombstones),
            # 되돌릴 기록은 바뀐 줄마다 한 줄이다 — 안 지우면 데이터보다 커진다.
            ("되돌릴 기록", services.purge_old_undo_journals),
            # 요청 시간 기록은 요청마다 한 줄이다 — 가장 빨리 자라는 표(ADR 0026).
            ("요청 시간 기록", performance.purge_old_timings),
            # 접근 로그는 보관 기간(기본 1년)이 지난 것만. 감사 기록은 안 지운다.
            ("접근 로그", retention.purge_old_access_logs),
        ):
            try:
                with SessionLocal() as db:
                    counts[what] = purge(db)
            except Exception:
                log.exception("정리 실패: %s", what)
                failed.append(what)
        if any(counts.values()):
            log.info("정리: %s", " · ".join(f"{what} {n}" for what, n in counts.items() if n))
        if failed:
            raise RuntimeError(f"정리하지 못함: {', '.join(failed)}")

    def run_forever(self) -> None:
        settings = get_settings()
        idle = settings.worker_poll_seconds
        log.info("워커 시작: %s", self.worker_id)
        services.heartbeat(self.worker_id, None)
        while not self.stop:
            try:
                self._tick_housekeeping()
            except (
                Exception
            ):  # 살림살이는 하나하나 잡는다 — 여기까지 오면 버그다. 작업은 집는다.
                log.exception("워커 살림살이 바퀴 실패")
            if self.stop:
                # 살림살이 도중에 멈추라는 신호가 왔다 — 새 작업을 집지 않는다.
                break
            try:
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
                # 기다리는 동안 작업이 들어오면 알림으로 바로 깬다(`Waker`).
                self.waker.wait(idle)
                idle = min(idle * 1.5, IDLE_MAX)
        if self._gc_thread is not None and self._gc_thread.is_alive():
            # 데몬 스레드라 프로세스와 함께 끊긴다 — 지우기는 파일 하나씩이라 반쯤 남아도
            # 다음 차례가 처음부터 다시 훑는다.
            log.info("고아 첨부 정리가 도는 중에 멈춥니다 — 다음 차례에 처음부터 다시")
        self.waker.close()
        log.info("워커 종료: %s", self.worker_id)

    def handle_signal(self, signum: int, _frame: Any) -> None:
        """멈추라는 신호 — **새 작업을 안 집고** 하던 것을 끝낸다. 하던 작업에는 멈추는
        중이라고 적는다(`services.mark_stopping`) — systemd 가 기다리다(`TimeoutStopSec`)
        죽이면, 그 작업을 거두는 워커가 「메모리를 다 써 죽었다」 가 아니라 「재시작 · 갱신에
        끊겼다」 로 적는다."""
        log.info("신호 %s — 하던 것을 끝내고 멈춥니다(새 작업은 안 집는다)", signum)
        self.stop = True
        services.mark_stopping(self.worker_id)


def _assemble() -> None:
    """**앱과 같은 조립**을 이 프로세스에도 건다 — 레지스트리(`app/main.py` 의
    `_register_extensions` · 확장의 `register`)는 조립 지점 한 곳이 채운다.

    워커는 그것을 안 거쳐 레지스트리가 **비어** 있었다. 처리 함수가 등록을 부르는 자리에서
    조용히 다른 길로 갔다 — 사진 일괄 업로드의 적용은 「첨부 자리」
    (`register_attachment_owner`)를 못 찾아 객체의 부서 대신 전역으로 판정했고(부서 관리자는
    거절, 시스템 관리자는 전역 첨부), 부서 자료 이동은 「멤버」 말고는 옮길 종류를 몰랐다
    (2026-10-10). 시험은 앱을 띄운 프로세스에서 워커를 돌려 이것을 못 본다
    (`tests/api/test_worker_assembly.py` 가 따로 띄워 본다).

    **변경 이벤트를 듣는 쪽도 앱과 같다**(2026-10-10) — 예전에는 비워 둬서 작업으로 한 일은
    웹훅 · 지켜보기 알림이 하나도 안 갔다. 웹훅은 작업마다 **요약 한 건**만 받고(줄마다 보내면
    일괄 입력 10만 줄이 웹훅 10만 건이다 — `events.job_scope`), 지켜보기는 지켜보는 객체가
    바뀔 때마다 받는다. 적재 뒤의 일(지표 다시 세기)은 여전히 작업 본문이 넣는다(ADR 0013)."""
    import app.main  # noqa: F401 — import 가 곧 조립이다(`create_app`)


def main() -> None:
    setup_logging(get_settings())
    _assemble()
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.handle_signal)
    signal.signal(signal.SIGINT, worker.handle_signal)
    worker.run_forever()


if __name__ == "__main__":
    main()
