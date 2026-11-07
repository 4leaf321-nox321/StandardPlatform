"""연결 오류 · 잠깐의 거절은 **짧게 다시 묻는다** — 하루 간격 소스가 몇 초의 장애로 하루를 잃지
않게.

받기(OData · REST · 파일 URL · 형제 코어 · RA)는 쪽마다 한 번 묻는다. 예전에는 그 한 번이
연결 오류 · 시간 초과 · 502 를 받으면 그 실행을 닫았고, 정해진 간격(보통 하루)이 지나야 다시
물었다 — 사내 API 의 야간 점검 몇 초가 하루치 기록을 늦췄다.

- **다시 묻는 것** — 연결이 안 됨 · 끊김 · 시간 초과(`TRANSIENT`), 그리고 HTTP 429 · 502 ·
  503 · 504(`RETRY_STATUS` — 과부하 · 앞단 장애). 쪽 하나를 다시 묻는다 — 이미 받은 쪽은
  그대로라 실패한 쪽부터 잇는 셈이다.
- **다시 묻지 않는 것** — 401 · 403(인증), 그 밖의 4xx(주소 · 쿼리가 틀림), 500(상대의 버그일
  때가 많다), 응답 모양 오류. 다시 물어도 같고, 사람이 고칠 일이다.
- **얼마나** — 처음 실패 뒤 2초, 다음 8초(`DELAYS`). 세 번째도 실패하면 그 오류를 그대로 낸다 —
  부르는 쪽의 오류 문구(「연결하지 못했습니다」 · 「HTTP 503」)가 그대로 선다. `Retry-After`
  (초)를 주면 따르되 `RETRY_AFTER_MAX` 까지만 — 쪽 하나에 몇 분을 묶지 않는다.

그래도 실패하면 실행 기록에 **실패 종류**를 적는다(`failure_kind` →
`DataSourceRun.error_kind`). 연결 실패(`connect`)면 타이머가 정기 간격보다 짧게 — 15 → 30 →
60분 뒤 — 다시 넣는다(`services.due`). 인증 거절(`auth`) · 주소 · 응답 오류(`source`)는 다시
해도 같아 사람 몫이다.

시험은 `sleep` 을 바꿔 기다리지 않는다(`tests/conftest.py`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

import httpx

from app.shared.errors import AppError

#: 다시 묻는 HTTP 상태 — 잠깐의 과부하 · 앞단(게이트웨이 · 프록시) 장애.
RETRY_STATUS = frozenset({429, 502, 503, 504})
#: 다시 묻는 연결 오류 — 연결 · 읽기 · 쓰기의 끊김과 시간 초과. 주소 꼴 오류
#: (`UnsupportedProtocol`) · 우리 쪽 요청 오류(`LocalProtocolError`)는 다시 물어도 같다.
TRANSIENT: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)
#: 물러나는 초 — 이 수만큼 다시 묻는다(모두 세 번 묻는다).
DELAYS: tuple[float, ...] = (2.0, 8.0)
#: `Retry-After` 를 따르는 상한(초).
RETRY_AFTER_MAX = 30.0

sleep: Callable[[float], None] = time.sleep

#: 실행 기록의 실패 종류(`DataSourceRun.error_kind`).
CONNECT, AUTH, SOURCE = "connect", "auth", "source"
#: 받기가 내는 오류 번호(`APP-DATASOURCES-00NN`)의 종류 — 연결 못 함 · 형제 코어에 못 닿음 ·
#: RA 에 못 닿음 / 인증 거절(OData · REST · 코어 · RA) / HTTP 오류(상대의 상태는
#: `details.upstream_status`).
_CONNECT_CODES = frozenset({13, 38, 56})
_AUTH_CODES = frozenset({11, 51})
_HTTP_CODES = frozenset({12, 53})
#: 주소 · 응답의 모양 오류 — JSON 이 아님 · items 없음 · 코어 타입 아님 · 파일 없음 · RA 창구
#: 없음.
_SOURCE_CODES = frozenset({10, 12, 33, 34, 37, 39, 52, 53})


def _wait(response: httpx.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return default
    try:
        asked = float(raw)
    except ValueError:  # 날짜 꼴은 읽지 않는다 — 기본 간격으로
        return default
    return max(0.0, min(asked, RETRY_AFTER_MAX))


def get(
    client: httpx.Client, url: str, *, params: Mapping[str, str] | None = None
) -> httpx.Response:
    """`client.get` 과 같되, 잠깐의 실패는 `DELAYS` 만큼 물러나며 다시 묻는다. 마지막 시도의
    응답(다시 묻는 상태여도)을 돌려주거나 그 오류를 낸다 — 판정은 부르는 쪽이 그대로 한다."""
    for delay in (*DELAYS, None):
        try:
            response = client.get(url, params=params)
        except TRANSIENT:
            if delay is None:
                raise
            sleep(delay)
            continue
        if delay is not None and response.status_code in RETRY_STATUS:
            sleep(_wait(response, delay))
            continue
        return response
    raise AssertionError("unreachable")  # pragma: no cover - 마지막 시도가 늘 돌려주거나 낸다


def failure_kind(caught: AppError) -> str | None:
    """받기 오류의 종류 — 데이터 소스의 오류가 아니면(대응 · 타입 · 그 밖) 비운다. HTTP 오류는
    다시 물은 끝에도 429 · 502 · 503 · 504 면 연결 실패로 본다(잠깐의 장애가 길어진 것)."""
    module, _, number = caught.code.rpartition("-")
    if not module.endswith("-DATASOURCES") or not number.isdigit():
        return None
    found = int(number)
    if found in _CONNECT_CODES:
        return CONNECT
    if found in _AUTH_CODES:
        return AUTH
    if found in _HTTP_CODES and caught.details.get("upstream_status") in RETRY_STATUS:
        return CONNECT
    if found in _SOURCE_CODES:
        return SOURCE
    return None
