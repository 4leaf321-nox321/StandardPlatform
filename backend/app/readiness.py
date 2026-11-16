"""준비됨 — **이 웹 프로세스가 지금 요청을 받아 처리할 수 있나**(ADR 0077).

`/api/health` 는 살아 있음이다 — 프로세스가 떠 있으면 200 이다. DB 가 끊겼거나 마이그레이션이
덜 올라갔어도 200 이라, 배포 스크립트 · 점검은 「응답함」 이라 말하고 사람은 그것을 화면의 500
으로 먼저 만났다. 준비됨(`/api/health/ready`)은 둘을 더 본다:

    db      DB 에 닿아 한 번 왕복하나(`DB_SECONDS` 안에)
    schema  DB 의 마이그레이션 판이 코드보다 뒤처지지 않았나

**DB 확인은 따로 된 스레드에서 시간 상한을 걸고 한다.** DB 서버가 답 없이 패킷을 버리면 접속이
몇 분을 기다린다(psycopg 는 접속 시간 상한이 기본으로 없다) — 그동안 묻는 쪽(배포 스크립트 ·
감시)이 같이 멈춘다. 앞선 확인이 아직 기다리는 중이면 새로 묻지 않고 그것을 함께 기다린다 —
감시가 몇 초마다 물어도 매달린 접속이 쌓이지 않게.

**오류의 원문을 내지 않는다.** 로그인 없이 묻는 자리인데, 접속 오류의 원문에는 DB 호스트 ·
계정이 든다. 원문은 로그에 남긴다(상태가 바뀔 때만 — 감시가 몇 초마다 물어도 로그가 그 줄로
차지 않게)."""

from __future__ import annotations

import functools
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app import schema_version

log = logging.getLogger(__name__)

#: DB 한 번 왕복을 기다리는 시간(초). 넘으면 「답하지 않는다」.
DB_SECONDS = 3.0


@dataclass(frozen=True)
class Check:
    name: str
    """`db` · `schema`."""
    ok: bool
    detail: str
    """사람이 읽는 한 줄 — 원문 오류는 싣지 않는다(위)."""


_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readiness")
_lock = threading.Lock()
_pending: Future[tuple[float, str | None]] | None = None
_last: bool | None = None


@functools.cache
def _code() -> tuple[str | None, frozenset[str]]:
    """코드의 마이그레이션 머리와 아는 판 전부 — 프로세스마다 한 번 읽는다(코드는 안
    바뀐다)."""
    head = schema_version.code_head()
    if head is None:
        return None, frozenset()
    return head, schema_version.known_revisions() | {head}


def _probe(engine: Engine) -> tuple[float, str | None]:
    """DB 한 번 왕복(ms)과 DB 의 마이그레이션 판 — 표가 없으면(빈 DB) None."""
    started = time.perf_counter()
    with engine.connect() as connection:
        connection.execute(text(f"SET LOCAL statement_timeout = {round(DB_SECONDS * 1000)}"))
        connection.execute(text("SELECT 1"))
        revision = None
        if connection.scalar(text("SELECT to_regclass('alembic_version')")) is not None:
            found = connection.scalar(text("SELECT version_num FROM alembic_version"))
            revision = str(found) if found is not None else None
    return (time.perf_counter() - started) * 1000, revision


def _ask(engine: Engine) -> tuple[float, str | None]:
    global _pending
    with _lock:
        if _pending is None or _pending.done():
            _pending = _pool.submit(_probe, engine)
        waiting = _pending
    return waiting.result(timeout=DB_SECONDS)


def schema_check(revision: str | None) -> Check:
    """DB 의 판(`revision`)을 코드와 견준다. **코드보다 새 판은 준비된 것으로 본다** — 더 새
    판을 깔았다가 되돌린 서버는 대개 그대로 돈다(마이그레이션은 칸을 더하는 쪽이다). 뒤처진 판
    · 빈 DB 는 준비 안 됨이다 — 새 칸을 읽는 화면이 500 으로 실패한다."""
    head, known = _code()
    if head is None:
        return Check("schema", True, "코드의 마이그레이션 판을 읽지 못해 견주지 않았습니다")
    if revision is None:
        return Check(
            "schema",
            False,
            "DB 에 마이그레이션 기록(alembic_version)이 없습니다 — 빈 DB 입니다"
            "(sudo ./deploy.sh install 이 마이그레이션을 올립니다)",
        )
    if revision == head:
        return Check("schema", True, f"마이그레이션 {revision}")
    if revision in known:
        return Check(
            "schema",
            False,
            f"마이그레이션이 덜 올라갔습니다 — DB {revision} · 코드 {head}"
            "(sudo ./deploy.sh update 가 올립니다)",
        )
    return Check(
        "schema",
        True,
        f"DB 의 판({revision})이 이 코드({head})보다 새것입니다 — 더 새 판을 깔았다가 "
        "되돌렸을 수 있습니다(대개 그대로 돕니다)",
    )


def check(engine: Engine) -> tuple[bool, list[Check]]:
    """준비됐나와 그 까닭 — 하나라도 아니면 준비 안 됨."""
    global _last
    try:
        ms, revision = _ask(engine)
    except FutureTimeout:
        checks = [
            Check("db", False, f"DB 가 {DB_SECONDS:g}초 안에 답하지 않습니다"),
            Check("schema", False, "DB 에 닿지 않아 견주지 못했습니다"),
        ]
    except Exception:
        if _last is not False:
            log.warning("준비됨 확인: DB 에 닿지 않습니다", exc_info=True)
        checks = [
            Check("db", False, "DB 에 닿지 않습니다 — 서버 로그(app.log)에 까닭이 있습니다"),
            Check("schema", False, "DB 에 닿지 않아 견주지 못했습니다"),
        ]
    else:
        checks = [Check("db", True, f"응답 {ms:.0f}ms"), schema_check(revision)]
    ready = all(one.ok for one in checks)
    if ready != _last:
        if ready:
            log.info("준비됨: %s", " · ".join(one.detail for one in checks))
        else:
            log.warning(
                "준비 안 됨: %s", " · ".join(one.detail for one in checks if not one.ok)
            )
        _last = ready
    return ready, checks
