"""확장 모듈을 켜고 끄는 일 — **화면이 정본, `.env` 가 기본값.**

번들 하나로 여러 플랫폼을 띄우므로 「이 설치에 무슨 기능이 있나」 는 설치가 정한다.
그것을 서버 파일로만 정하면 운영자가 SSH · 배포 없이는 아무것도 못 조립하고, 켜고 끈
사실이 시스템 안에 안 남는다. 코어 공개(`ObjectType.core`)를 이미 같은 무늬로 두었다 —
DB 플래그 · 시스템 관리자 화면 · 감사 기록.

**고를 수 있는 것은 코드에 있는 확장뿐이다.** 목록은 `main.py` 가 기동에서 넣어 주고
(`register_available`), 화면은 그 목록에서만 고른다 — `.env` 오타처럼 「없는 이름」 이
켜지는 일이 없다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.server.models import ExtensionState
from app.shared import audit
from app.shared.errors import NotFound, code

#: 이 번들에 들어 있는 확장 이름 — `main.py` 가 기동에서 한 번 넣는다.
#: **모듈은 `app.extensions` 를 import 하지 않는다**(구조 시험). 붙이는 자리가 하나여야
#: "이 확장이 왜 안 뜨지" 를 물을 자리가 생긴다.
_AVAILABLE: tuple[str, ...] = ()


def register_available(names: tuple[str, ...]) -> None:
    global _AVAILABLE
    _AVAILABLE = names


def available() -> tuple[str, ...]:
    return _AVAILABLE


def _rows(db: Session) -> dict[str, ExtensionState]:
    return {one.name: one for one in db.scalars(select(ExtensionState))}


def _default_on(name: str) -> bool:
    """행이 없을 때의 답 — `.env` 의 `EXTENSIONS`."""
    return name in get_settings().extension_names


def enabled_names(db: Session) -> tuple[str, ...]:
    """지금 켜져 있는 확장. 순서는 **이름 순**(번들에 들어 있는 순서)이다.

    `.env` 의 적은 순서를 쓰지 않는다 — 화면에서 켜고 끄기 시작하면 그 순서는
    아무 데도 없고, 사이드바가 오늘과 내일 다르게 보인다.
    """
    rows = _rows(db)
    return tuple(
        name
        for name in available()
        if (rows[name].enabled if name in rows else _default_on(name))
    )


def unknown_defaults() -> tuple[str, ...]:
    """`.env` 에 적혔는데 **이 번들에 없는** 이름.

    예전에는 이것이 기동을 막았다. 켜짐이 화면으로 옮겨 온 뒤로는 막지 않는다 —
    운영 재시작 중이라면 오타 하나가 서비스를 안 뜨게 하기 때문이다. 대신 **서버
    화면이 말한다**. 아무 데도 안 적으면 관리자는 「켰는데 메뉴가 없다」 로 만난다.
    """
    return tuple(one for one in get_settings().extension_names if one not in available())


def states(db: Session) -> list[tuple[str, bool, bool, datetime | None]]:
    """화면이 그리는 표 — `(이름, 켜짐, 화면에서_지정했나, 바뀐_시각)`."""
    rows = _rows(db)
    out: list[tuple[str, bool, bool, datetime | None]] = []
    for name in available():
        row = rows.get(name)
        if row is None:
            out.append((name, _default_on(name), False, None))
        else:
            out.append((name, row.enabled, True, row.updated_at))
    return out


def set_enabled(db: Session, user: User, name: str, on: bool) -> tuple[str, bool]:
    """켜고 끈다. **없는 이름은 거절한다** — 목록 밖의 값은 오타뿐이다."""
    if name not in available():
        raise NotFound(
            code("SERVER", 1),
            f"이 설치에 없는 확장입니다: {name}",
            details={"available": list(available())},
        )
    row = db.get(ExtensionState, name)
    was = row.enabled if row is not None else _default_on(name)
    if row is None:
        row = ExtensionState(name=name, enabled=on)
        db.add(row)
    else:
        row.enabled = on
    if was != on:
        # **켜고 끈 일은 남긴다.** 바깥으로 기능이 나타나고 사라지는 스위치에서
        # 「언제 누가」 를 물을 자리가 없으면, 켜 둔 것을 잊는다 — 코어 공개에서
        # 이미 같은 구멍을 메웠다(`ontology.type.core`).
        audit.record(
            db,
            action="extension.toggle",
            actor=user,
            target_table="extension_states",
            target_id=None,
            target_label=name,
            changes={"enabled": on, "was": was},
        )
    db.commit()
    return name, on


def require_extension(name: str) -> Callable[..., None]:
    """확장 라우터에 씌우는 문. **꺼져 있으면 없는 엔드포인트다.**

    라우터는 기동에서 전부 붙는다(런타임에 뗄 수 없다) — 그래서 끈 확장은 코드가
    프로세스에 남아 있고, 문이 404 로 답한다. 「끔」 은 감추는 것이고 자료는 남는다.
    """

    def guard(request: Request, db: Session = Depends(get_db)) -> None:
        if name not in enabled_names(db):
            raise NotFound(
                code("COMMON", 404),
                "존재하지 않는 엔드포인트입니다.",
                details={"path": request.url.path},
            )

    return guard
