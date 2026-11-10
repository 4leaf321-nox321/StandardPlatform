"""밤 그래프 계산의 응답 모양(ADR 0067)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CalcRunOut(BaseModel):
    id: int
    computed_at: datetime | None = Field(
        None, description="셈을 마친 때 — 그 뒤의 변화는 안 들었다"
    )
    engine: str = Field(..., description="실제로 센 엔진 — cpu · gpu")
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description="노드 · 짝 수, 관계마다(맨 위 · 칸 · 비슷한 것), 무리 수 · 모듈성, "
        "단계별 초, 건너뛴 까닭",
    )


class CalcPendingOut(BaseModel):
    id: uuid.UUID
    status: str


class CalcStatusOut(BaseModel):
    run: CalcRunOut | None
    pending: CalcPendingOut | None
    paused: str | None = Field(None, description="자동 계산이 멈춘 까닭(없으면 돈다)")
    engine_setting: str
    night_hour: int
    gpu_visible: bool = Field(..., description="이 웹 프로세스에서 GPU 가 보이나(셈은 워커)")
    gpu_name: str | None
    gpu_reason: str | None
    networkit: bool


class CalcObjectBrief(BaseModel):
    id: uuid.UUID
    label: str
    key: str | None
    type_slug: str


class CalcTopItem(CalcObjectBrief):
    value: float = Field(
        ..., description="기대는 수(맨 위의 수) · 다리 역할(가장 큰 다리 대비)"
    )
    rank: int


class CalcTopOut(BaseModel):
    run: CalcRunOut | None
    total: int
    items: list[CalcTopItem]


class CalcScoreOut(BaseModel):
    metric: str
    relation: str
    value: float
    rank: int
    of: int = Field(..., description="순위가 매겨진 객체 수(값이 0 인 것은 순위가 없다)")


class CalcClusterOut(BaseModel):
    cluster: int
    size: int
    label: str = Field(..., description="대표(무리 안에서 이음이 가장 많은 것)의 이름 — 셀 때")
    representative_id: uuid.UUID
    types: dict[str, int] = Field(default_factory=dict, description="타입 slug → 수")


class CalcObjectClusterOut(BaseModel):
    cluster: int
    rank: int = Field(..., description="무리 안의 차례(이음이 많은 것부터)")
    size: int | None
    label: str | None
    representative_id: uuid.UUID | None
    types: dict[str, int] = Field(default_factory=dict)


class CalcSimilarOut(CalcObjectBrief):
    relation: str
    rank: int
    score: float = Field(..., description="끝까지 펼친 구성의 자카드(0 ~ 1)")


class CalcObjectOut(BaseModel):
    run: CalcRunOut | None
    scores: list[CalcScoreOut]
    cluster: CalcObjectClusterOut | None
    similar: list[CalcSimilarOut]


class CalcClustersOut(BaseModel):
    run: CalcRunOut | None
    total: int
    items: list[CalcClusterOut]


class CalcMemberOut(CalcObjectBrief):
    rank: int


class CalcClusterMembersOut(BaseModel):
    run: CalcRunOut | None
    cluster: CalcClusterOut | None
    total: int
    items: list[CalcMemberOut]
