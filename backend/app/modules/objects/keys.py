"""식별자 이력 — **키를 고친 사실을 밖이 따라올 수 있게.**

키 체계가 바뀌는 일은 실제로 일어난다(`FM-001` → `FM-BRK-001`). 그때 밖(쌍둥이 · 코어 API)
에서는 새 식별자가 **처음 보는 것**이라, 알려 주지 않으면 같은 객체가 둘이 된다.

**목록인 이유:** 받는 쪽이 하루에 한 번 받아 가고 그 사이에 키가 두 번 바뀌면(A→B→C),
마지막 값만 보내서는 B 밖에 말하지 못한다 — 받는 쪽이 가진 것은 A 다.
"""

from __future__ import annotations

from app.modules.objects.models import ObjectInstance

#: 이만큼만 들고 있는다. 이력이 길어지면 봉투가 커지고, 그만큼 옛날 것은 받는 쪽도 이미
#: 따라왔다(그 사이에 한 번이라도 받아 갔다면).
MAX_HISTORY = 10


def remember(row: ObjectInstance, old_key: str) -> None:
    """옛 식별자를 이력에 붙인다 — 오래된 것부터, 같은 것은 한 번만."""
    history = [one for one in (row.previous_keys or []) if one and one != old_key]
    history.append(old_key)
    # 새 값이 이력 안에 있었으면(되돌린 경우) 그것은 뺀다 — 지금 키가 이력에 있으면
    # 받는 쪽이 자기 것을 자기에게 옮기려 한다.
    row.previous_keys = [one for one in history if one != row.key][-MAX_HISTORY:]


def latest(row: ObjectInstance) -> str | None:
    """가장 마지막 옛 식별자 — 한 칸만 읽는 쪽(코어 API 의 `renamed_from`)에 준다."""
    history = row.previous_keys or []
    return str(history[-1]) if history else None
