"""요청 안에서 세는 수 — **상한까지만 센다.**

정의 가져오기 · 정의 제안 · 스냅샷 복원의 계획은 요청 안에서 돈다. 그 경고(「지금 값이 있는
객체는 N개」 · 「이미 맺힌 N개」)가 타입 · 관계를 통째로 세면 2,000만 건 타입에서 계획 하나가
분 단위가 되고, 검토함에서 제안을 고를 때마다 같은 일을 다시 한다. 경고에 필요한 것은 「얼마나
많은가」 의 크기라서 상한(`CAP`)을 넘으면 「N건 이상」 으로 말한다.

`COUNTS` 는 계획의 지문이 경고에서 지우는 **지금 수**다(`planview.fingerprint` · 옮기기 · 키
변경의 지문) — 쓰기가 잦은 타입에서 수만 바뀐 계획이 「바뀌었다」 로 막히지 않게.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

#: 요청 안에서 세는 상한 — 종류 변경이 요청 안에서 하는 문턱(`retype.RETYPE_INLINE`)과 같다.
CAP = 20_000
#: 경고 속의 **지금 수**(「값이 있는 객체는 1,204개」) — 지문에서는 뺀다.
COUNTS = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: 상한을 넘은 수의 표(「20,000건 이상」) — 지문에서는 「이상」 도 뺀다. 안 빼면 미리 본 뒤
#: 수가 상한을 넘기만 해도(「19,990건」 → 「20,000건 이상」) 계획이 바뀐 것으로 보여 적용이
#: 막혔다.
_OVER = re.compile(r"(\d[\d,]*(?:\.\d+)?\s*(?:건|개)?)\s*이상")


def count(db: Session, rows: Any, *, cap: int | None = None) -> int:
    """`rows`(한 줄에 한 행인 select)의 수 — 상한(`cap`, 안 주면 `CAP`)을 넘으면 상한 + 1 에서
    멈춘다."""
    limit = CAP if cap is None else cap
    return int(
        db.scalar(select(func.count()).select_from(rows.limit(limit + 1).subquery())) or 0
    )


def shown(value: int, *, unit: str = "건", cap: int | None = None) -> str:
    """「1,204건」 — 상한을 넘었으면 「20,000건 이상」."""
    limit = CAP if cap is None else cap
    if value > limit:
        return f"{limit:,}{unit} 이상"
    return f"{value:,}{unit}"


def without_counts(text: str) -> str:
    """지문에 넣을 글 — 수를 `#` 로, 상한을 넘었다는 「이상」 은 지운다."""
    return COUNTS.sub("#", _OVER.sub(r"\1", text))
