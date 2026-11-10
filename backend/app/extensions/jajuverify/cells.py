"""자주검증 — 표의 열과 칸 안의 모양(ADR 0044).

## 열 — 한 곳에 종류와 함께

표의 열은 `COLUMNS` 한 곳이 정한다. 종류는 둘이다:

    text       글 + 사진(공통 첨부 — `owner_field` 가 열의 키). 사진은 글 사이 자리와 폭을
    갖는다 schedule   일정 — 단계 목록 `[{date, label}]`. 글이 아니라 값이다(날짜 차례로 선다)

담당 · 상태 같은 메타 열을 더하는 날 여기에 한 줄(과 그 종류의 모양)을 더한다. 화면 · 서버 ·
검색 · 감사가 이 표를 읽는다 — 열을 두 곳에 적으면 한쪽만 고쳐진다.

## 칸 글 안의 사진 — 한 줄 표시

글 칸의 사진은 **그 칸의 첨부 목록이 정본**이고, 칸 글 안의 한 줄 표시는 **자리와 폭**만
말한다:

    ![이름](photo:<첨부 id>?w=<폭>)

- 제 줄 하나를 통째로 차지한다(앞뒤에 다른 글이 있으면 표시가 아니라 글이다).
- 폭은 칸 너비의 % — 10 ~ 100(정수). 비우면 `DEFAULT_WIDTH`. 화면의 손잡이는 10 단위로 붙고,
  빠른 폭은 25 · 50 · 75 · 100 이다.
- 이름(`[...]`)은 사람이 읽으라고 둔 것이다 — 비워도 된다.

글 칸에 표시가 없는 사진(1단계에 붙은 것 · AI 가 올리기만 한 것)은 칸 끝에 기본 폭으로 선다. 그
칸의 첨부가 아닌 id 를 가리키는 표시(지운 사진 · 남의 칸 사진)는 그리지 않는다. 사진을 떼면 그
표시 줄도 뺀다(`without_photos`). 마크다운 그림 꼴이라 AI 가 읽고 고치기 쉽고, 칸 글을 견주는
덮어쓰기 보호 · 검색이 그대로 따라온다 — 새 표 · 새 편집기 없이.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.modules.files.photomarks import DEFAULT_WIDTH as DEFAULT_WIDTH
from app.modules.files.photomarks import MARKER as MARKER
from app.modules.files.photomarks import MAX_WIDTH as MAX_WIDTH
from app.modules.files.photomarks import MIN_WIDTH as MIN_WIDTH
from app.modules.files.photomarks import clamp as clamp
from app.modules.files.photomarks import marker as marker
from app.modules.files.photomarks import marker_ids as marker_ids
from app.modules.files.photomarks import plain as plain
from app.modules.files.photomarks import without_photos as without_photos

ColumnKind = Literal["text", "schedule"]


@dataclass(frozen=True)
class Column:
    key: str
    """저장 칸 — 글 열이면 사진의 `owner_field` 이기도 하다."""
    label: str
    kind: ColumnKind


#: 표의 열 — 차례가 화면 · 인쇄의 열 차례다.
COLUMNS: tuple[Column, ...] = (
    Column("topic", "항목", "text"),
    Column("problem", "문제점", "text"),
    Column("action", "개선대책", "text"),
    Column("schedule", "일정", "schedule"),
    Column("remark", "비고", "text"),
)

#: 글 열(사진이 붙는 칸) — 키 → 이름.
TEXT_CELLS: dict[str, str] = {one.key: one.label for one in COLUMNS if one.kind == "text"}

# --- 사진 표시 --------------------------------------------------------------------

# 표기는 공통 첨부 모듈(`files/photomarks.py`)이 정한다 — 문서 속성의 그림(ADR 0056)이 같은
# 표기를 쓰고, 코어는 확장을 import 하지 못한다. 이 확장이 쓰던 이름 그대로 위에서 다시
# 내보낸다.


# --- 일정 -------------------------------------------------------------------------

#: 한 칸의 일정 단계 상한 — 한 장 보고서의 한 칸이다.
SCHEDULE_MAX = 20
SCHEDULE_LABEL_MAX = 200


def schedule_of(items: list[Any]) -> list[dict[str, str]]:
    """저장하는 모양 — `[{date: "YYYY-MM-DD", label}]` 을 **날짜 차례로**(같은 날은 넣은 차례).
    받는 쪽은 `{date, label}` 모델이거나 이미 저장된 dict 다."""
    rows: list[tuple[date, str]] = []
    for one in items:
        raw = one.get("date") if isinstance(one, dict) else getattr(one, "date", None)
        label = one.get("label") if isinstance(one, dict) else getattr(one, "label", "")
        when = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
        rows.append((when, str(label or "").strip()))
    rows.sort(key=lambda pair: pair[0])
    return [{"date": when.isoformat(), "label": label} for when, label in rows]


def last_date(schedule: list[dict[str, str]]) -> date | None:
    """일정의 가장 늦은 날 — 줄의 `due_on`(기한 지남 · 정렬)이 이것이다."""
    dates = [date.fromisoformat(one["date"]) for one in schedule if one.get("date")]
    return max(dates) if dates else None
