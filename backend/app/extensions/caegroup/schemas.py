"""디지털 트윈 역량 — 주고받는 모양."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SetupStatusOut(BaseModel):
    """기준 정보가 준비됐나 — 화면이 처음 묻는 것."""

    subject_type_slug: str | None
    subject_type_label: str | None
    agent_type_slug: str | None
    agent_type_label: str | None
    ready: bool
    """거짓이면 화면은 목록 대신 「기준 정보 만들기」 를 보여 준다."""


class DefsOut(BaseModel):
    """부문 · 축 · 척도 · 문턱 — **화면이 코드에 박지 않고 여기서 읽는다.**"""

    sector: str
    sector_label: str
    subject_label: str
    agent_label: str
    axes: list[dict[str, Any]]
    evidence_tiers: list[dict[str, Any]]
    accuracy_thresholds: list[dict[str, Any]]
    accuracy_rules: list[dict[str, str]]


class PairOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str
    subject_id: uuid.UUID
    subject_label: str
    agent_id: uuid.UUID
    agent_label: str
    agent_tools: list[str]
    """해석이 쓰는 도구 이름 — 목록에서 「무엇으로 보나」 가 바로 읽히게."""
    agent_dept: str | None
    created_at: datetime


class PairIn(BaseModel):
    subject_id: uuid.UUID
    agent_id: uuid.UUID
    workspace_slug: str = Field(min_length=1)
    """어느 부서의 연계인가. **권한과 집계의 단위**라 빠뜨릴 수 없다."""


class AssessmentOut(BaseModel):
    """평가 한 줄 — **축 종류마다 채워진 칸이 다르다**(§정의)."""

    axis: str
    value: float | None
    rung: str | None
    rungs: list[str]
    defects: dict[str, dict[str, str]]
    note: str
    evidence: dict[str, Any]
    evidence_tier: str
    evidence_ref: str
    assessed_at: datetime
    assessed_by_label: str


class AssessmentIn(BaseModel):
    """평가를 적을 때 보내는 것.

    **수준(`rung`)은 수치형 축에서 보내지 않는다** — 값이 정한다. 보내도 서버가 무시한다.
    """

    value: float | None = None
    rung: str | None = None
    rungs: list[str] = Field(default_factory=list)
    defects: dict[str, dict[str, str]] = Field(default_factory=dict)
    note: str
    """근거 — **비우면 저장되지 않는다.**"""
    evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_tier: str
    evidence_ref: str = ""


class PairPatchIn(BaseModel):
    """연계에서 고칠 수 있는 것 — **소속 부서뿐이다.**

    시험 항목이나 해석을 바꾸는 것은 다른 연계를 뜻한다(§services.move).
    """

    workspace_slug: str = Field(min_length=1)


class HistoryOut(BaseModel):
    axis: str
    axis_label: str
    snapshot: dict[str, Any]
    changed_at: datetime
    changed_by_label: str


class CoverageAxisOut(BaseModel):
    axis: str
    label: str
    assessed: int
    ratio: float


class CoverageOut(BaseModel):
    """**평가 완료율** — 평가된 연계 ÷ 전체 연계. 축마다 따로 센다."""

    pairs: int
    axes: list[CoverageAxisOut]
