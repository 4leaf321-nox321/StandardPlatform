"""읽기 질의의 시간 상한 — 화면 · AI 가 묻는 목록 · 통계가 서버를 붙잡지 않게(ADR 0031).

이어진 것 너머를 걷는 기준 · 조건은 **지나가는 줄 수**만큼 든다 — 끝이 좁으면 네 걸음도
2초인데, 기록 2,000만 건을 지나가면 세 걸음도 수십 초, 네 걸음은 3분이다(실측, ADR 0031).
상한이 없으면 그동안 DB 연결 하나와 API 작업자 하나가 묶이고, 사람은 화면을 닫아도 질의는
끝까지 돈다. 이중화의 nginx 는 600초를 넘기면 **같은 요청을 다른 서버에 다시 보낸다**
(`proxy_next_upstream timeout`).

상한은 `SET LOCAL` 이라 그 요청의 트랜잭션에만 걸린다 — 작업(내보내기 · 지표 계산)은 따로
돈다. 넘으면 PostgreSQL 이 질의를 취소하고(`57014`), 오류 처리기가 「좁히거나 지표로」 를
504 로 낸다(`errors.register_error_handlers`). 503 이 아닌 까닭: nginx 가 503 이면 다른 서버로
다시 보내 같은 무거운 질의가 한 번 더 돈다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

#: PostgreSQL 의 「질의 취소」 — statement_timeout 을 넘겼거나 누가 취소했다.
QUERY_CANCELED = "57014"

#: 계획기가 조인 순서를 **다 따져 보는** 조인 수. 기본(8 · GEQO 12)이면 네 걸음 기준과 세
#: 걸음 세부 기준에 조건까지 건 통계(조인 17개)가 순서를 어림으로 골라 100초 걸려 끊겼고,
#: 20 이면 같은 질의가 13~20초였다(기록 2,000만, ADR 0031). 조인이 8개 아래인 보통 질의에는
#: 아무 차이가 없고, 계획에 드는 시간도 상한 안에 든다.
PLANNER_JOINS = 20


def cap(db: Session) -> None:
    """이 트랜잭션의 질의마다 `read_query_seconds` 상한을 걸고(0 이면 안 건다), 이어진 것
    너머를 많이 걷는 질의를 위해 계획기를 넓힌다(`PLANNER_JOINS`)."""
    for name in ("join_collapse_limit", "from_collapse_limit", "geqo_threshold"):
        db.execute(text(f"SET LOCAL {name} = {PLANNER_JOINS}"))
    seconds = get_settings().read_query_seconds
    if seconds <= 0:
        return
    db.execute(text(f"SET LOCAL statement_timeout = {max(1, round(seconds * 1000))}"))


def cancelled(caught: BaseException) -> bool:
    """시간 상한(또는 취소)으로 끊긴 질의인가 — SQLAlchemy 가 감싼 psycopg 오류의 sqlstate."""
    orig: Any = getattr(caught, "orig", None)
    return getattr(orig, "sqlstate", None) == QUERY_CANCELED
