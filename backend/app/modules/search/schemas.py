"""전역 찾기의 응답 형태."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class SearchTypeOut(BaseModel):
    """타입마다 몇 건. **좁히는 단추가 된다** — 상한에 걸렸을 때 나머지가 어디 있는지
    이것만이 말해 준다."""

    type_slug: str
    type_label: str
    icon: str
    count: int
    usage: str = "axis"
    """`log` 이면 기록 — 섞어 볼 때 줄에 안 섞고 이 수로만 보인다."""


class SearchHitOut(BaseModel):
    id: uuid.UUID
    type_slug: str
    type_label: str
    icon: str
    label: str
    key: str | None
    matched: str
    """label · key · alias — **왜 이게 나왔나.** 안 적으면 엉뚱해 보이는 줄이 오류로 읽힌다."""
    matched_text: str


class SearchOut(BaseModel):
    q: str
    total: int
    """줄로 볼 수 있는 수. 섞어 볼 때는 기록을 뺀 수다(`records`)."""
    types: list[SearchTypeOut]
    records: int = 0
    """섞어 볼 때 줄에 안 섞은 기록의 수(ADR 0011) — 그 타입으로 좁히면 나온다."""
    items: list[SearchHitOut]
    limit: int
    offset: int
    min_query: int
    """이 글자 수부터 찾는다. 화면이 「두 글자 이상」 이라고 말할 근거."""
