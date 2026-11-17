"""목록 응답 — **상한을 서버가 강제한다.**

클라이언트가 보낸 limit 을 그대로 믿으면 `?limit=1000000` 한 번에 서버가 죽는다.
악의가 없어도 그렇게 된다 — 화면이 "전부 보기" 를 구현하면서 큰 수를 넣는다.

total 을 함께 주는 이유: 없으면 화면이 "다음 쪽이 있는지" 를 알려고 한 건 더
요청하는 편법을 쓰게 되고, 그 편법은 화면마다 달라진다.

## 화면과 기계는 상한이 다르다(ADR 0079)

**화면(로그인 세션)은 200** 그대로다. 화면은 한 번에 여러 목록 · 통계를 함께 부르고(홈의 위젯,
검토함의 종류), 그 하나하나가 커지면 화면이 멈춘다 — 실제로 검토함이 종류 여덟을 한 번에 받다가
열리지 않았다(ADR 0073 · 0075). **API 토큰(MCP 의 AI · 다른 시스템)은 `MACHINE_MAX_LIMIT`**
까지 — 전부 읽어야 답이 되는 물음을 200줄씩 끊어 받으면 2,000만 건 표에서 10만 번이다.
토큰인지는 인증이 정한다(`request.state.token_scopes` — 사람 세션에는 없다). 머리 · 인자로는
못 바꾼다.
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: API 토큰(기계) 호출의 쪽 상한 — 코어 API(`coreapi`)와 같은 2,000. 줄 하나가 JSON 으로 1KB
#: 남짓이라 한 쪽이 2MB 안팎이다.
MACHINE_MAX_LIMIT = 2_000


def is_machine(request: Request | None) -> bool:
    """API 토큰으로 들어온 요청인가(MCP · 연계 · 스크립트). 화면의 로그인 세션은 아니다."""
    return request is not None and getattr(request.state, "token_scopes", None) is not None


def max_limit(request: Request | None) -> int:
    return MACHINE_MAX_LIMIT if is_machine(request) else MAX_LIMIT


def clamp_limit(limit: int | None, request: Request | None = None) -> int:
    """쪽 크기 — 안 주면 `DEFAULT_LIMIT`, 넘으면 상한(화면 200 · 토큰 `MACHINE_MAX_LIMIT`)."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, max_limit(request)))


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
