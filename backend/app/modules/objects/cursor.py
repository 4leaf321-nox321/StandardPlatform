"""커서 쪽넘김(ADR 0079) — **뒤쪽으로 가도 느려지지 않는 쪽 넘기기.**

`offset` 은 앞의 줄을 모두 지나가서 버린다. 2,000만 건 표에서 100만 번째 줄부터의 한 쪽은 100만
줄을 읽고 버린 뒤에야 나오고, 쪽마다 전체 수(`total`)까지 다시 센다 — 끝까지 받는 AI 는 쪽이
갈수록 느려지다 시간 상한에 걸린다.

커서는 **마지막으로 준 줄의 id** 를 들고 다음 쪽을 `id > 그것` 으로 고른다 — 기본 키 색인으로
그 자리에 바로 간다. 쪽마다 비용이 같다.

- **차례는 id 차례**다(목록 화면의 정렬을 따르지 않는다). 커서는 「전부 읽기」 의 길이다 —
  이름순으로 끊으면 이름이 같은 줄 · 값이 빈 줄 · 고쳐서 이름이 바뀐 줄이 쪽 사이에서 빠지거나
  겹친다. id 는 바뀌지 않는다.
- **전체 수는 첫 쪽에서 한 번만 센다** — 커서에 실어 다음 쪽에 그대로 돌려준다(`total`). 받는
  사이에 생기거나 지운 것은 그 수에 없다(받은 줄 수와 조금 다를 수 있다).
- **조건의 지문을 싣는다** — 조건을 바꾸고 옛 커서를 쓰면 다른 목록의 중간부터가 된다. 그때는
  422 로 「처음부터」 를 말한다.

커서는 서버만 읽는 글(base64)이다 — 모양에 기대지 않는다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from app.shared.errors import AppError, code

#: 처음 쪽을 뜻하는 값 — 빈 글자도(`?cursor=`).
START = ("", "start")
#: 지문에서 빼는 인자 — 쪽 · 받는 모양이지 조건이 아니다.
NOT_FILTERS = frozenset({"cursor", "limit", "offset", "columns", "diagnose"})


@dataclass(frozen=True)
class Cursor:
    after: uuid.UUID | None
    """이 id 다음부터. None 이면 처음 쪽."""
    total: int | None
    """첫 쪽에서 센 전체 수. 처음 쪽이면 None(이번에 센다)."""
    seen: int
    """앞 쪽들에서 준 줄 수 — 응답의 `offset` 으로 돌려준다(진행률)."""


def fingerprint(params: Iterable[tuple[str, str]]) -> str:
    """조건의 지문 — 같은 조건이면 인자의 차례가 달라도 같다."""
    kept = sorted((key, value) for key, value in params if key not in NOT_FILTERS)
    raw = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_start(raw: str) -> bool:
    return raw.strip().lower() in START


def decode(raw: str, filters: str) -> Cursor:
    if is_start(raw):
        return Cursor(None, None, 0)
    try:
        padded = raw.strip() + "=" * (-len(raw.strip()) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        found = Cursor(uuid.UUID(str(data["a"])), int(data["t"]), int(data["n"]))
        made_for = str(data["f"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as caught:
        raise AppError(
            code("OBJECTS", 154),
            "커서를 읽을 수 없습니다 — 받은 `next_cursor` 를 그대로 넘기거나, 처음부터 "
            "받으려면 cursor=start 로 부르세요.",
            status=422,
        ) from caught
    if made_for != filters:
        raise AppError(
            code("OBJECTS", 155),
            "이 커서는 다른 조건의 목록에서 받은 것입니다 — 조건을 바꿨으면 "
            "처음부터(cursor=start) 다시 받으세요.",
            status=422,
        )
    return found


def encode(after: uuid.UUID, total: int, seen: int, filters: str) -> str:
    raw = json.dumps(
        {"a": str(after), "t": total, "n": seen, "f": filters}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
