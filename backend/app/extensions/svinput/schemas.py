"""자주검증 입력 — 주고받는 모양."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoleFieldDef(BaseModel):
    key: str
    label: str
    required: bool
    default: str


class RoleDef(BaseModel):
    key: str
    label: str
    description: str
    default_type: str
    hub: bool = True
    fields: list[RoleFieldDef]


class RoleState(BaseModel):
    """역할 하나의 지금 — 설정값과 그것이 실제로 있는지."""

    key: str
    type_slug: str | None
    type_label: str | None
    """설정한 타입이 없으면(지워졌으면) None — 화면이 「빠짐」 으로 말한다."""
    fields: dict[str, str | None]
    """칸 역할 → 설정한 속성 키(없으면 None)."""
    missing: list[str]
    """설정은 돼 있는데 실제로 없는 것 — 타입 또는 칸 역할 key."""


class StatusOut(BaseModel):
    """화면이 처음 묻는 것 — 쓸 준비가 됐나."""

    roles: list[RoleState]
    ready: bool
    """`report` · `issue` 가 갖춰졌고 빠진 것이 없다."""
    defs: list[RoleDef]
    """역할의 정의 — 화면이 코드에 박지 않고 여기서 읽는다."""


class CandidateType(BaseModel):
    slug: str
    label: str
    usage: str
    hub: bool
    """허브 접두(`sv_pm_`)가 맞는 타입인가 — 스파인(코어 과제 · 모델)은 거짓인 것에서."""
    properties: list[str]
    """속성 키 — 설정 화면이 칸 역할을 고를 때 **없는 칸을 못 고르게** 한다."""


class CandidateRelation(BaseModel):
    slug: str
    label: str


class CandidatesOut(BaseModel):
    prefix: str
    types: list[CandidateType]
    relations: list[CandidateRelation] = Field(default_factory=list)
    """관계 종류 — 연결 칸에 `rel:<slug>` 로 고를 수 있는 것."""


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    fields: dict[str, str | None] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    """역할 → 타입 · 칸. **모르는 역할은 거절한다** — 조용히 버리면 보낸 쪽은 저장된 줄
    안다."""

    model_config = ConfigDict(extra="forbid")

    roles: dict[str, RoleIn]


class ChildCounts(BaseModel):
    stage_result: int = 0
    cause: int = 0
    prevention: int = 0
    figure: int = 0
    passed: int = 0
    """판정 칸이 PASS · OK 로 읽히는 단계 수."""
    failed: int = 0


class IssueLine(BaseModel):
    id: uuid.UUID
    label: str
    key: str | None
    status: str
    properties: dict[str, Any]
    ref_labels: dict[str, str]
    plan_status: str | None
    """설정의 상태 칸 값 — 없으면 None."""
    counts: ChildCounts


class ReportSheetOut(BaseModel):
    """문서 한 장 — 머리 + 문제점 줄 + 하위 기록 수. 화면이 이슈마다 세 번 묻지 않게 한
    번에."""

    report: dict[str, Any]
    report_type_slug: str
    issue_type_slug: str
    issues: list[IssueLine]
    total_issues: int
    kpis: list[dict[str, Any]]
    """결과요약 지표 객체들(역할 `kpi` 가 설정됐을 때)."""
    can_edit: bool
