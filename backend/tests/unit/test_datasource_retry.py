"""짧은 재시도(`datasources/retry.py`) — 무엇을 몇 번 다시 묻고, 실패를 어떤 종류로 적나."""

from __future__ import annotations

import httpx
import pytest

from app.modules.datasources import retry
from app.shared.errors import AppError, code


def _client(
    answers: list[int | type[httpx.TransportError]], headers: dict[str, str] | None = None
) -> tuple[httpx.Client, list[httpx.Request]]:
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        one = answers.pop(0)
        if isinstance(one, int):
            return httpx.Response(one, headers=headers or {}, json={"ok": one < 400})
        raise one("끊김", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler)), asked


def test_연결_오류와_503_은_물러나며_세_번까지_묻는다(
    _retry_without_waiting: list[float],
) -> None:
    client, asked = _client([httpx.ConnectError, 503, 200])
    assert retry.get(client, "http://x.local/a", params={"p": "1"}).status_code == 200
    assert len(asked) == 3 and _retry_without_waiting == [2.0, 8.0]
    assert all(one.url.params["p"] == "1" for one in asked)  # 같은 쪽을 다시


def test_세_번_다_안_되면_마지막_답이나_오류를_그대로_낸다(
    _retry_without_waiting: list[float],
) -> None:
    client, _ = _client([502, 504, 503])
    assert retry.get(client, "http://x.local/a").status_code == 503
    client, _ = _client([httpx.ReadTimeout] * 3)
    with pytest.raises(httpx.ReadTimeout):
        retry.get(client, "http://x.local/a")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_다시_물어도_같은_답은_다시_묻지_않는다(
    status: int, _retry_without_waiting: list[float]
) -> None:
    client, asked = _client([status, 200])
    assert retry.get(client, "http://x.local/a").status_code == status
    assert len(asked) == 1 and _retry_without_waiting == []


def test_주소_꼴_오류는_다시_묻지_않는다(_retry_without_waiting: list[float]) -> None:
    client, asked = _client([httpx.UnsupportedProtocol, 200])
    with pytest.raises(httpx.UnsupportedProtocol):
        retry.get(client, "http://x.local/a")
    assert len(asked) == 1


@pytest.mark.parametrize(
    ("after", "waited"),
    [("5", 5.0), ("600", retry.RETRY_AFTER_MAX), ("Wed, 21 Oct 2026 07:28:00 GMT", 2.0)],
)
def test_Retry_After_를_따르되_상한까지(
    after: str, waited: float, _retry_without_waiting: list[float]
) -> None:
    client, _ = _client([429, 200], headers={"Retry-After": after})
    assert retry.get(client, "http://x.local/a").status_code == 200
    assert _retry_without_waiting == [waited]


@pytest.mark.parametrize(
    ("number", "upstream", "kind"),
    [
        (13, None, "connect"),  # OData · REST · 파일 URL 에 연결 못 함
        (38, None, "connect"),  # 형제 코어에 못 닿음
        (56, None, "connect"),  # RA 에 못 닿음
        (12, 503, "connect"),  # 다시 물어도 503 — 장애가 길어진 것
        (53, 429, "connect"),
        (12, 500, "source"),
        (12, 404, "source"),
        (10, None, "source"),  # JSON 이 아님
        (11, None, "auth"),
        (51, None, "auth"),  # RA 토큰 거절
        (21, None, None),  # 타입이 없음 — 받기 오류가 아니다
    ],
)
def test_실패_종류(number: int, upstream: int | None, kind: str | None) -> None:
    details = {"upstream_status": upstream} if upstream else None
    caught = AppError(code("DATASOURCES", number), "x", details=details)
    assert retry.failure_kind(caught) == kind
    assert retry.failure_kind(AppError(code("METRICS", 13), "x")) is None
