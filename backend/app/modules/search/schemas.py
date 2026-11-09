"""전역 찾기의 응답 형태."""

from __future__ import annotations

import uuid
from datetime import datetime

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
    exact: bool = False
    """이름 · 식별자 · 별칭이 찾은 말과 **꼭 같다** — 이런 줄이 맨 위에 선다(꼭 맞음 → 그 말로
    시작 → 이름에 듦 → 식별자 · 별칭에만 듦, 같은 자리에서는 짧은 이름부터). 기록 타입으로
    좁힌 찾기는 이름 순이다."""


class SearchPostOut(BaseModel):
    """게시판 글 하나(ADR 0033) — 객체가 아니라 사람이 쓴 글이라 따로 싣는다."""

    id: uuid.UUID
    title: str
    author_name: str
    created_at: datetime
    comment_count: int
    snippet: str
    """본문에서 그 말이 든 자리 앞뒤 — 왜 걸렸나."""


class SearchMeaningOut(BaseModel):
    """뜻이 가까운 것 하나(ADR 0052) — 글자는 안 겹쳐도 뜻이 가까워 걸린 객체."""

    id: uuid.UUID
    type_slug: str
    type_label: str
    icon: str
    label: str
    key: str | None
    similarity: float
    """코사인 유사도(0 ~ 1) — 클수록 가깝다."""
    section: str = ""
    """문서의 절에서 걸렸으면 그 절의 경로(「3. 결과 › 3.1 모서리 찍힘」, ADR 0054)."""
    anchor: str = ""
    """그 절의 제목 id — 상세 주소 뒤에 `#` 로 붙이면 그 절로 간다."""
    same: int = 0
    """글이 똑같은 다른 객체의 수 — 하나만 세우고 수로 말한다."""


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
    posts: list[SearchPostOut] = []
    """제목 · 본문에 그 말이 든 게시판 글, 새 글부터 다섯 — **좁히지 않은 첫 쪽에만.**"""
    posts_total: int = 0
    """그 말이 든 글 전부의 수 — 다섯보다 많으면 「게시판에서 모두 보기」."""
    meaning: list[SearchMeaningOut] = []
    """뜻이 가까운 것(ADR 0052) — `meaning=true` 로 물은 첫 쪽에만, 글자로 찾은 줄은 뺀다."""
    meaning_state: str = ""
    """ready · off(켠 타입이 없거나 끔) · unavailable(Ollama 에 못 닿음).

    안 물었으면 빈 글자."""
    meaning_narrowed: bool = False
    """**덜 나왔을 수 있다** — 낼 수를 못 채웠는데 범위를 다 봤다고 할 수 없다(큰 범위는 HNSW
    색인이 가까운 조각을 먼저 받고 타입 · 부서로 나중에 거르고, 같은 글이 받는 수를 채울 수도
    있다). 이때 빈 `meaning` 은 「뜻으로도 없다」 가 아니다 — 더 구체적인 말로 다시 찾는다."""
