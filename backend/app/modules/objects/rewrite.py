"""칸 하나를 **덩어리째** 고친다 — 수만 ~ 수백만 객체의 같은 칸(종류 변경 · 병합 · 참조
비우기).

객체를 ORM 으로 하나씩 고치면 행마다 갱신 문장 · 속성 전체를 두 번 담은 이력 · 바깥 알림이
나간다. 참조 색인 트리거가 행마다 돌고 알림 이벤트가 커밋까지 쌓여, 기록 200만 건의 종류 변경이
38.5분 · 4.6GB 였다(ADR 0009). 여기서는 덩어리 하나에 갱신 문장 하나로 그 칸만 고치고
(`jsonb_set` · `-`), 이력은 바뀐 칸만 덩어리째 남긴다(`audit.record_rows` — 바깥 알림은 부르는
쪽이 한 줄로).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: 한 번에 고치는 행 수 — 덩어리 하나가 갱신 문장 하나다.
CHUNK = 5_000

_SET_ONE = text(
    """
    UPDATE objects AS o
    SET properties = CASE WHEN v.gone THEN o.properties - CAST(:key AS text)
                          ELSE jsonb_set(o.properties, ARRAY[CAST(:key AS text)], v.val) END,
        updated_at = now()
    FROM unnest(CAST(:ids AS uuid[]), CAST(:vals AS jsonb[]), CAST(:gone AS boolean[]))
         AS v(id, val, gone)
    WHERE o.id = v.id
    """
)


def write_key(db: Session, key: str, rows: Iterable[tuple[uuid.UUID, bool, Any]]) -> None:
    """(객체 id, 지우나, 새 값) 줄마다 `key` 칸 하나를 고친다 — 문장 하나로. 다른 칸은
    그대로다.

    ORM 을 거치지 않으므로 세션에 실린 그 객체는 옛 값을 든다 — 부르는 쪽이 필요하면 다시
    읽는다(`Session.expire`)."""
    ids: list[uuid.UUID] = []
    values: list[str | None] = []
    gone: list[bool] = []
    for object_id, remove, value in rows:
        ids.append(object_id)
        gone.append(remove)
        values.append(None if remove else json.dumps(value, ensure_ascii=False))
    if ids:
        db.execute(_SET_ONE, {"key": key, "ids": ids, "vals": values, "gone": gone})


def changed_property(key: str, before: Any, after: dict[str, Any]) -> dict[str, Any]:
    """칸 **하나만** 바뀐 이력 — `keys` 가 그 칸이다. 이력 화면(`history_of`)은 `keys` 가
    있으면 그 칸만 되짚고 나머지는 그대로 둔다."""
    return {
        "properties": {
            "before": {key: before},
            "after": {key: after[key]} if key in after else {},
            "keys": [key],
        }
    }
