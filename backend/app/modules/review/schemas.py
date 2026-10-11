"""검토함 API 의 모양."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReviewChoiceOut(BaseModel):
    value: str
    label: str
    detail: str = ""
    score: float | None = None


class ReviewItemOut(BaseModel):
    kind: str
    kind_label: str
    id: str
    question: str
    evidence: list[str]
    choices: list[ReviewChoiceOut]
    recommended: str | None
    recommended_label: str | None
    confidence: float | None
    reason: str
    type_slug: str
    type_label: str
    workspace_slug: str | None
    workspace_name: str | None
    rows: int
    group: str
    group_label: str
    blocked: str | None
    link: str
    ignorable: bool
    actionable: bool
    """처리 길이 있는 종류인가 — 아니면 「원래 화면으로」 만."""
    members: list[str] = Field(default_factory=list)
    """「같은 것끼리 묶기」 로 접은 줄이면 펼친 줄의 id — 결정은 이것마다 간다."""


class ReviewKindOut(BaseModel):
    kind: str
    label: str
    description: str
    link: str
    total: int
    """원래 자리에 있는 수."""
    matched: int
    """읽은 것 중 거르기에 맞는 수."""
    scanned: int
    actionable: bool
    undo_note: str
    notes: list[str]


class ReviewFacetOut(BaseModel):
    value: str
    label: str
    count: int


class ReviewPageOut(BaseModel):
    kinds: list[ReviewKindOut]
    items: list[ReviewItemOut]
    total: int
    limit: int
    offset: int
    types: list[ReviewFacetOut]
    workspaces: list[ReviewFacetOut]
    notes: list[str]


class ReviewFilterIn(BaseModel):
    kind: str | None = None
    type: str | None = None
    workspace: str | None = Field(
        default=None, description="부서 slug — `@global` 이면 전역(부서 없음)만"
    )
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    max_confidence: float | None = Field(default=None, ge=0, le=1.01)
    actionable: bool = False


class ReviewDecisionIn(BaseModel):
    kind: str
    item_id: str
    action: Literal["accept", "choose", "ignore"]
    """`accept` 추천대로 · `choose` 다른 것으로(`value` 에 후보의 값) · `ignore` 무시."""
    value: str | None = None


class ReviewPlanRequest(BaseModel):
    mode: Literal["decisions", "recommended", "all"] = "decisions"
    """`decisions` — 고른 것(`decisions`) · `recommended` — 「추천대로 모두」(거르기에 맞고
    확신도가 `threshold` 이상인 것) · `all` — 거른 전부에 `action` 하나."""
    decisions: list[ReviewDecisionIn] = Field(default_factory=list)
    filters: ReviewFilterIn = Field(default_factory=ReviewFilterIn)
    threshold: float = Field(default=0.9, ge=0, le=1)
    action: Literal["accept", "ignore"] = "accept"


class ReviewOutcomeOut(BaseModel):
    kind: str
    kind_label: str
    item_id: str
    question: str
    action: str
    choice: str
    choice_label: str
    status: str
    """미리 보기: `apply` · `blocked`. 적용 뒤: `done` · `refused`."""
    effect: str
    message: str
    rows: int
    undoable: bool
    recommended: str | None
    confidence: float | None
    followed: bool | None


class ReviewKindPlanOut(BaseModel):
    kind: str
    label: str
    apply: int
    blocked: int
    rows: int
    undoable: bool
    undo_note: str


class ReviewPlanOut(BaseModel):
    fingerprint: str
    mode: str
    threshold: float | None
    decisions: list[ReviewDecisionIn]
    """적용할 때 그대로 돌려보낸다 — 「추천대로 모두」 도 이 목록으로 적용한다."""
    counts: dict[str, int]
    by_kind: list[ReviewKindPlanOut]
    outcomes: list[ReviewOutcomeOut]
    """막힌 것 먼저, 앞의 500줄까지 — 수는 `counts` 가 전부 센다."""
    outcomes_omitted: int
    skipped: int
    """「추천대로 모두」 에서 추천이 없거나 문턱 아래라 고르지 않은 수."""
    rows: int
    as_job: bool
    """적용하면 작업이 된다(큰 묶음)."""
    notes: list[str]


class ReviewApplyRequest(BaseModel):
    decisions: list[ReviewDecisionIn]
    fingerprint: str
    mode: Literal["one", "selected", "all", "recommended"] = "selected"
    threshold: float | None = Field(default=None, ge=0, le=1)


class ReviewApplyOut(BaseModel):
    applied: bool
    batch_id: uuid.UUID | None
    job_id: uuid.UUID | None
    """큰 묶음이면 작업으로 갔다 — `GET /api/jobs/{job_id}` 로 본다."""
    counts: dict[str, int]
    outcomes: list[ReviewOutcomeOut]
    undoable: int


class ReviewDecisionOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    kind: str
    kind_label: str
    item_id: str
    question: str
    action: str
    choice: str
    choice_label: str
    recommended: str | None
    confidence: float | None
    followed: bool | None
    mode: str
    threshold: float | None
    status: str
    message: str
    rows: int
    undoable: bool
    undone_at: datetime | None
    type_slug: str
    decided_by_name: str | None
    decided_at: datetime


class ReviewDecisionPage(BaseModel):
    items: list[ReviewDecisionOut]
    total: int
    limit: int
    offset: int


class ReviewBatchOut(BaseModel):
    batch_id: uuid.UUID
    decided_at: datetime
    decided_by_name: str | None
    mode: str
    count: int
    done: int
    refused: int
    undoable: int
    undone: int
    kinds: list[str]


class ReviewUndoRequest(BaseModel):
    apply: bool = False


class ReviewUndoLineOut(BaseModel):
    decision_id: uuid.UUID
    kind: str
    question: str
    choice_label: str
    status: str
    """미리 보기: `undo` · `blocked`. 적용 뒤: `done` · `refused`."""
    message: str


class ReviewUndoOut(BaseModel):
    applied: bool
    batch_id: uuid.UUID
    counts: dict[str, int]
    lines: list[ReviewUndoLineOut]


class ReviewAccuracyLineOut(BaseModel):
    kind: str
    label: str
    decided: int
    recommended: int
    followed: int
    rate: float | None
    """추천이 있던 결정 중 따른 몫 — 추천이 없었으면 None."""


class ReviewAccuracyOut(BaseModel):
    kinds: list[ReviewAccuracyLineOut]
    bands: list[ReviewAccuracyLineOut]
