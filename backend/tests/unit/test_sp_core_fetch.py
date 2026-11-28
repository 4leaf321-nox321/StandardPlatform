"""형제 SP 의 코어 받기(`datasources/fetchers.fetch_sp_core`) — 한 차례에 같은 `key` 의 산 행이
둘 오면 마지막 것만 넘긴다.

쪽을 넘기는 사이 바뀐 객체는 앞 쪽(옛 값)과 뒤 쪽(새 값)에 두 번 오고, 허브가 타입을 옮기며
같은 식별자의 객체로 병합한 것도 같은 `key` 로 여러 번 올 수 있다. 그대로 넘기면 일괄 입력이
「같은 식별자가 N행에도 있습니다」 로 조각을 거절하고 시계가 안 움직였다(2026-11-28 검토).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.modules.datasources import fetchers, odata


def _item(key: str, label: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "status": "active", "updated_at": "1", **extra}


def test_같은_key_의_산_행은_마지막_것만_무덤은_그대로() -> None:
    pages = [
        {"items": [_item("V-1", "옛 이름"), _item("V-3", "대성")], "next": "c1"},
        {
            "items": [
                _item("V-1", "새 이름"),
                _item("V-2", "한화", deleted=True),
                _item("V-3", "대성", deleted=True, merged_into="V-3"),
            ],
            "next": None,
            "as_of": "2026-11-28T03:00:00.000000Z",
        },
    ]

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages.pop(0))

    got = fetchers.fetch_sp_core(
        base_url="http://hub.local/api",
        type_slug="vendor",
        since="2026-11-28T00:00:00.000000Z",
        auth=odata.Auth(kind="bearer", user="", secret="t"),
        page_size=2,
        max_rows=1000,
        transport=httpx.MockTransport(answer),
    )
    assert [(one["key"], one["label"], bool(one.get("deleted"))) for one in got.rows] == [
        ("V-3", "대성", False),
        ("V-1", "새 이름", False),
        ("V-2", "한화", True),
        ("V-3", "대성", True),
    ]
    assert got.as_of == "2026-11-28T03:00:00.000000Z"
