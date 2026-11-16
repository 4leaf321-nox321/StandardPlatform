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
    late: bool = False
    """시간 안에 다 읽지 못했다 — 앞의 것만 읽었거나(`scanned`) 하나도 못 읽고 건너뜀(`total`
    0). **「없다」 가 아니다** — 까닭은 `notes`, 이 종류만(`kind`) 보면 시간을 더 쓴다."""
    seconds: float = 0.0
    """이 종류를 모으는 데 든 시간(초) — 어느 종류가 느린지."""


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
    recommendation: Literal["with", "without"] | None = Field(
        default=None, description="`with` 추천이 있는 것만 · `without` 추천이 없는 것만"
    )


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
    unread: int = 0
    """한 번에 읽는 상한 때문에 **보지 않은** 수 — 그만큼은 고르지 않았다. 그것까지 하려면
    `POST /api/review/bulk`(「전부 작업으로」, ADR 0075)."""


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


class ReviewKindInfoOut(BaseModel):
    """종류 하나 — **모으지 않고** 이름만(화면이 종류마다 따로 부르기 전에, ADR 0075)."""

    kind: str
    label: str
    description: str
    link: str
    actionable: bool
    undo_note: str


class ReviewBulkRequest(BaseModel):
    mode: Literal["recommended", "all"] = "recommended"
    """`recommended` — 거르기에 맞고 확신도가 `threshold` 이상인 것을 추천대로 · `all` — 거른
    전부에
    `action` 하나."""
    filters: ReviewFilterIn = Field(default_factory=ReviewFilterIn)
    threshold: float = Field(default=0.9, ge=0, le=1)
    action: Literal["accept", "ignore"] = "accept"


class ReviewBulkOut(BaseModel):
    job_id: uuid.UUID
    """미리 보기 작업 — 끝나면 결과(`result`)가 미리 보기이고, 결정 전부는 결과 파일(CSV)이다.
    적용은 `POST /api/jobs/{job_id}/apply`."""


class ReviewAutoIn(BaseModel):
    enabled: bool
    threshold: float = Field(default=0.95, ge=0.9, le=1)


class ReviewAutoOut(BaseModel):
    """자동 확인 한 종류(ADR 0075) — 켜져 있나 · 근거 · 지금 돌 수 있나."""

    kind: str
    label: str
    enabled: bool
    threshold: float
    enabled_by_name: str | None
    enabled_at: datetime | None
    last_run_at: datetime | None
    last_job_id: uuid.UUID | None
    last_note: str
    decided: int
    """그 확신도 이상에서 사람이 하나씩 정한 결정 수(추천이 있던 것)."""
    followed: int
    rate: float | None
    ready: bool
    """켜져 있고 근거가 충분하다 — 다음 차례에 돈다."""
    why: str
    """못 도는 까닭. 돌 수 있으면 빈 글자."""
    min_decided: int
    min_rate: float
    max_per_night: int
    hour: int
    """하루 한 번, 이 시각(서버 현지)이 지난 뒤."""
    can_edit: bool
