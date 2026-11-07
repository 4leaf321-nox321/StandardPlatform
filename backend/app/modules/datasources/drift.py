"""원천 칸 변화 — 대응에 쓴 칸이 받은 행에서 사라졌나, 원천에 새 칸이 생겼나(ADR 0061).

원천 시스템에서 칸 이름이 바뀌거나(`Rating` → `Score`) 빠지면, 대응에 쓴 칸은 행마다
「없음」 으로 읽힌다. 그것은 「빈 값」 과 구별되지 않아 **조용히** 처리됐다.

- 보통 소스는 빈 칸을 「안 건드림」 으로 읽어 옛 값이 그대로 남는다 — 그 칸이 낡아 가는 줄
  아무도 모른다.
- 거울(바깥을 정본으로, `options.mirror`)은 빈 칸을 「비움」 으로 읽어 **그 속성의 값을 모두
  지운다**.

그래서 받은 행 **어디에도 키가 없는** 대응 칸(`missing`)을 짚는다. 거울이면 적용을
멈추고(지우기 전에 사람이 대응을 고친다), 아니면 실행 기록에 경고를 남긴다. 원천에 새로 생긴
칸(`new` — 지난번 받은 칸 목록 `options.source_columns` 와 견준다)은 실행 기록에 한 번 적는다 —
받으려면 사람이 대응에 더한다.

**보지 않는 소스** — 형제 코어(`sp_core`)는 빈 칸을 키째 빼고 보낸다(그 칸에 값이 하나도 없는
쪽이 흔하다 — 「없음」 이 칸 변화가 아니다). RA 보고서는 칸 대응을 틀이 정한다. 선을 받는
소스는 아직 보지 않는다. OData 는 대응에 쓴 칸만 `$select` 로 청해 사라진 칸이 4xx 로
드러나고, 새 칸은 보이지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.modules.datasources.models import DataSource

#: 지난번 받은 칸(맨 위 단계)을 기억하는 `options` 의 열쇠.
SEEN = "source_columns"
#: 기억하는 칸 수의 상한 — 행마다 열쇠가 다른 원천이 `options` 를 키우지 않게.
SEEN_MAX = 300
#: 행의 칸이 아닌 것 — OData 의 주석(`@odata.*`) · v2 의 `__metadata`.
_NOT_COLUMNS = ("__metadata",)
#: 보지 않는 소스의 종류.
SKIP_KINDS = frozenset({"sp_core", "ra_reports"})


@dataclass
class Drift:
    missing: list[str] = field(default_factory=list)
    """대응에 썼는데 받은 행 어디에도 키가 없는 칸 — 대응에 적은 이름 그대로."""
    new: list[str] = field(default_factory=list)
    """지난번 받은 칸에 없던 새 칸(대응에 안 쓴 것만) — 지난 기록이 있을 때만."""
    seen: list[str] = field(default_factory=list)
    """이번에 받은 칸(맨 위 단계, 이름 차례) — 적용에 성공하면 기억한다(`remember`)."""


def has_path(row: dict[str, Any], path: str) -> bool:
    """`Address/Country` · `Address.Country` 처럼 안으로 들어간 칸도 — 값이 null 이어도 키가
    있으면 있다."""
    current: Any = row
    for part in path.replace("/", ".").split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _top(path: str) -> str:
    return path.replace("/", ".").split(".")[0]


def of(
    source: DataSource,
    *,
    external_key: str,
    sources: Iterable[str],
    rows: list[dict[str, Any]],
) -> Drift:
    """받은 행으로 칸 변화를 본다 — 행이 없거나 보지 않는 소스면 빈 것."""
    if source.kind in SKIP_KINDS or not rows:
        return Drift()
    used = [one for one in dict.fromkeys(sources) if one and one != external_key]
    # 바깥 식별자가 없는 행은 줄마다 오류로 이미 말한다 — 여기서 다시 말하지 않는다.
    missing = [one for one in used if not any(has_path(row, one) for row in rows)]
    names: set[str] = set()
    for row in rows:
        names.update(key for key in row if not key.startswith("@") and key not in _NOT_COLUMNS)
    seen = sorted(names)[:SEEN_MAX]
    before = (source.options or {}).get(SEEN)
    new: list[str] = []
    if isinstance(before, list):
        taken = {_top(one) for one in used} | {_top(external_key)}
        new = [one for one in seen if one not in set(before) and one not in taken]
    return Drift(missing=missing, new=new, seen=seen)


def remember(source: DataSource, found: Drift) -> None:
    """적용에 성공한 실행이 받은 칸을 기억한다 — 다음 실행의 「새 칸」 의 기준."""
    if found.seen and (source.options or {}).get(SEEN) != found.seen:
        source.options = {**(source.options or {}), SEEN: found.seen}


def notes(found: Drift, *, mirror: bool, rows: int) -> tuple[list[str], list[str]]:
    """(적용을 막는 말, 기록에만 남기는 말)."""
    blocking: list[str] = []
    noted: list[str] = []
    if found.missing:
        names = ", ".join(found.missing)
        said = (
            f"원천에 대응한 칸이 없습니다: {names} — 받은 {rows}행 어디에도 그 칸이 없습니다. "
            "원천에서 칸 이름이 바뀌었거나 빠졌을 수 있습니다."
        )
        if mirror:
            blocking.append(
                f"{said} 이 소스는 바깥을 정본으로 보므로(거울) 적용하면 그 칸의 값이 "
                "모두 비워져 적용하지 않았습니다 — 소스 수정에서 칸 대응을 고치거나 그 칸을 "
                "대응에서 빼세요."
            )
        else:
            noted.append(
                f"{said} 그 속성은 건드리지 않았습니다(이전 값이 그대로 남습니다) — 이름이 "
                "바뀌었으면 소스 수정에서 칸 대응을 고치세요."
            )
    if found.new:
        noted.append(
            f"원천에 새 칸이 생겼습니다(대응에 없음): {', '.join(found.new)} — 받으려면 소스 "
            "수정에서 칸 대응에 더하세요."
        )
    return blocking, noted
