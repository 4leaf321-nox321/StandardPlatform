"""긴 읽기를 POST 로 — `X-HTTP-Method-Override: GET` 이면 몸통(쿼리 문자열 모양)을 주소의
쿼리에 이어 붙여 **같은 GET 경로**로 돌린다.

「이 모델들 아래 전부」 에 id 수천 개를 넣으면 주소가 4만 ~ 7만 자가 된다(합성 견주기,
2026-11-11) — nginx 는 머리 줄이 8KB 를 넘으면 414 로 끊고, 프록시마다 상한이 다르다. 읽기
경로를 POST 로 한 벌 더 만들면 목록 · 통계 · 진단 · 파일이 두 벌이 되고 언젠가 한쪽만
고쳐진다. 그래서 **모양만 바꿔 같은 경로로** 보낸다 — 거르기 · 권한 · 시간 상한이 하나다.

- 머리 `X-HTTP-Method-Override: GET` 이 있는 POST 만 — 그 머리는 남의 사이트가 CORS 허락 없이
  못 붙이므로, 이 길로 남의 페이지가 읽기를 일으킬 수 없다(쓰기 경로는 원래 POST 다).
- 몸통은 `application/x-www-form-urlencoded`(쿼리 문자열과 같은 모양) 하나 — `MAX_BODY` 까지.
- `/api` 아래만.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.shared.errors import code
from app.shared.request_context import get_request_id

HEADER = b"x-http-method-override"
FORM = b"application/x-www-form-urlencoded"
#: 몸통 상한 — id 10만 개(37자씩)가 넉넉히 든다. 넘으면 413.
MAX_BODY = 8 * 1024 * 1024


class ReadOverrideMiddleware:
    def __init__(self, app: ASGIApp, *, api_prefix: str) -> None:
        self.app = app
        self.api_prefix = api_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(HEADER, b"").strip().upper() != b"GET":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        root = scope.get("root_path", "") or ""
        inner = path[len(root) :] if root and path.startswith(root) else path
        if not inner.startswith(self.api_prefix + "/"):
            await _refuse(send, 400, "읽기 바꾸기(POST → GET)는 /api 아래에서만 됩니다.")
            return
        kind = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if kind != FORM:
            await _refuse(
                send,
                415,
                "읽기 바꾸기의 몸통은 application/x-www-form-urlencoded"
                "(쿼리 문자열 모양)입니다.",
            )
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > MAX_BODY:
                limit_mb = MAX_BODY // (1024 * 1024)
                await _refuse(send, 413, f"읽기 바꾸기의 몸통이 {limit_mb}MB 를 넘습니다.")
                return
            if not message.get("more_body", False):
                break
        query = bytes(scope.get("query_string") or b"")
        extra = bytes(body).strip()
        joined = b"&".join(one for one in (query, extra) if one)
        moved = dict(scope)
        moved["method"] = "GET"
        moved["query_string"] = joined
        moved["headers"] = [
            (key, value)
            for key, value in scope.get("headers") or []
            if key not in (b"content-type", b"content-length", HEADER)
        ]

        async def empty() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(moved, empty, send)


async def _refuse(send: Send, status: int, message: str) -> None:
    """앱의 오류 모양(`{"error": {"code", "message", …}}`)과 같게 — 미들웨어라 처리기를 못
    탄다."""
    error = {
        "code": code("COMMON", status),
        "message": message,
        "request_id": get_request_id(),
        "details": {},
    }
    body = json.dumps({"error": error}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
