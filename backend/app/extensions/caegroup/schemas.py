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
    subject_id: uuid.UUID
    subject_label: str
    agent_id: uuid.UUID
    agent_label: str
    created_at: datetime


class PairIn(BaseModel):
    subject_id: uuid.UUID
    agent_id: uuid.UUID
    workspace_slug: str = Field(min_length=1)
    """어느 부서의 연계인가. **권한과 집계의 단위**라 빠뜨릴 수 없다."""
