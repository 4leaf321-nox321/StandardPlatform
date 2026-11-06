"""뜻으로 찾기의 관리 화면 형태(ADR 0052)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SemanticFieldOut(BaseModel):
    """고를 수 있는 칸 하나 — 글 · 여러 줄 글 · 선택 · 참조 · 문서."""

    key: str
    label: str
    data_type: str


class SemanticTypeOut(BaseModel):
    type_slug: str
    type_label: str
    icon: str
    usage: str
    """axis · log — 기록은 칸을 골라야 대상이 된다(ADR 0054)."""
    objects: int
    synced: int
    """맞춰 본 객체 수."""
    pieces: int
    """조각 수 — 기본 글 + 문서의 절. 대상에서 뺀 뒤에도 워커가 지울 때까지 남을 수 있다."""
    pending: int | None
    """대상 타입에서 아직 안 맞춘 수(새것 · 고친 것). 대상이 아니면 None."""
    enabled: bool
    fields: list[str] | None
    """고른 칸 — None 이면 자동(축만). `label` 은 이름."""
    options: list[SemanticFieldOut]
    why_not: str | None
    """대상으로 지정할 수 없는 까닭 — 지정할 수 있으면 None."""
    estimate_pieces: int
    """대상으로 하면 생길 조각 수의 어림(지금 고른 칸 — 아니면 자동, 기록은 칸 하나)."""
    estimate_bytes: int
    """그 조각이 드는 자리(조각 x 약 14KB, ADR 0050 실측)."""


class SemanticJobOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    result: dict[str, Any] | None
    progress: dict[str, Any]


class SemanticOut(BaseModel):
    state: str
    """ready · off · problem — 서버 화면의 「뜻으로 찾기」 와 같은 판단."""
    problem: str | None
    model: str
    kb_per_object: int
    min_similarity: float
    """이만큼 가까워야 「뜻이 가까운 것」 으로 낸다(코사인 유사도)."""
    per_second: float | None
    """지난 큰 계산이 초당 바꾼 조각 수 — 이 서버의 속도(GPU 유무). 아직 모르면 None."""
    types: list[SemanticTypeOut]
    job: SemanticJobOut | None
    """가장 최근의 계산 — 돌거나 기다리는 것 포함."""


class SemanticTargetIn(BaseModel):
    type_slug: str
    fields: list[str] | None = None
    """고른 칸 — None 이면 자동(축만). 기록은 반드시 고른다. `label` 은 이름."""


class SemanticTargetsIn(BaseModel):
    targets: list[SemanticTargetIn]
    """대상 전부 — 여기 없는 타입은 대상에서 뺀다."""
