"""변경 이벤트 — **커밋된 뒤에** 바깥에 알린다.

감사 기록(`audit.record`)이 이 틀에서 「되돌릴 수 없거나 권한이 실린 변경」 의 깔때기다.
그래서 이벤트도 거기서 나온다 — 따로 심으면 두 벌이 되고, 두 벌은 반드시 갈린다(감사에는
남는데 웹훅은 안 가는 변경이 생기고, 그 차이는 아무도 모른다).

## 커밋 뒤에만

트랜잭션 안에서 바깥에 보내면 롤백된 변경이 이미 나가 있다. 그래서 세션에 **모아 두었다가**
`after_commit` 에 한 번에 내보내고, `after_rollback` 이면 버린다.

## 레지스트리

`shared` 는 도메인을 모른다. 듣는 쪽(웹훅 모듈)이 `register_listener` 로 등록하고
`main.py` 가 조립한다 — `extensions.py` 와 같은 무늬. 듣는 쪽이 던지는 예외는 여기서
삼키고 로그에 남긴다: **알림이 실패했다고 방금 성공한 저장이 실패로 보이면 안 된다.**
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_KEY = "change_events"

#: 이 세션의 커밋을 **바깥에 알리지 않고 모아 두는** 자리. `hold` 가 연다.
_HOLD = "change_events_held"


@dataclass(frozen=True)
class ChangeEvent:
    action: str
    target_table: str
    target_id: uuid.UUID | None
    target_label: str
    workspace_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    """누가 했나. **이름이 아니라 id 다** — 듣는 쪽이 「내가 한 일은 나에게 안 알린다」 를
    판단하려면 사람을 가려내야 하고, 이름은 같을 수 있다. 시스템이 한 일이면 None."""
    actor_label: str
    actor_client: str | None
    actor_token: str | None
    changes: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    request_id: str | None = None
    at: datetime | None = None


Listener = Callable[[list[ChangeEvent]], None]
_listeners: list[Listener] = []


def register_listener(listener: Listener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def stage(db: Session, one: ChangeEvent) -> None:
    """이 세션이 커밋되면 내보낼 것으로 적어 둔다."""
    db.info.setdefault(_KEY, []).append(one)


def hold(db: Session) -> None:
    """이 세션의 커밋을 **바깥에 알리지 않고 모아 둔다.**

    묶음 가져오기는 바깥 트랜잭션 하나 안에서 여러 서비스를 부르고, 그 서비스들이 저마다
    커밋한다(세이브포인트). 그 커밋마다 내보내면 끝내 롤백된 변경이 이미 나가 있다 —
    미리 보기는 전부 롤백이다. 모았다가 바깥이 커밋된 뒤 `release` 로 내보낸다.
    """
    db.info[_HOLD] = []


def release(db: Session) -> None:
    """모아 둔 것을 내보낸다. **바깥 트랜잭션이 커밋된 뒤에만** 부른다."""
    held: list[ChangeEvent] = [*db.info.pop(_HOLD, []), *db.info.pop(_KEY, [])]
    _dispatch(held)


def drop_held(db: Session) -> None:
    """모아 둔 것을 버린다 — 바깥이 롤백될 때."""
    db.info.pop(_HOLD, None)
    db.info.pop(_KEY, None)


@event.listens_for(Session, "after_commit")
def _flush(session: Session) -> None:
    staged: list[ChangeEvent] = session.info.pop(_KEY, [])
    if _HOLD in session.info:
        session.info[_HOLD].extend(staged)
        return
    _dispatch(staged)


def _dispatch(staged: list[ChangeEvent]) -> None:
    if not staged or not _listeners:
        return
    for listener in _listeners:
        try:
            listener(staged)
        except Exception:
            logger.exception("변경 이벤트 듣는 쪽이 실패했습니다 (%d건)", len(staged))


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop(_KEY, None)
