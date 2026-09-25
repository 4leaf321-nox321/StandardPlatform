"""디지털 트윈 역량 — 주고받는 모양."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

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
    accuracy_thresholds: list[dict[str, Any]]
    accuracy_rules: list[dict[str, str]]
    sw_units: list[dict[str, str]]
    """인프라 S/W 의 단위 · 용도 — **키를 저장하고 이름을 보여 준다.**"""
    sw_purposes: list[dict[str, str]]


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
    assessed: int
    """매긴 축 수 — 목록에서 「어디까지 채웠나」 가 바로 읽히게."""
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


class PairBulkIn(BaseModel):
    """목록에서 고른 것들에 같은 일을 한다 — 서른 건을 서른 번 누르게 하지 않는다."""

    ids: list[uuid.UUID] = Field(min_length=1)
    workspace_slug: str | None = None
    """옮기기일 때만 준다."""


class PairBulkOut(BaseModel):
    changed: int


class TileLevelOut(BaseModel):
    """타일 하나가 아는 축의 상태 — 수준 · 켠 항목 · 값 · 근거."""

    rung: str | None
    rungs: list[str]
    value: float | None
    note: str


class TileOut(PairOut):
    group: str
    """벽에서 묶는 단위 — 담당 부서(없으면 소속 부서)."""
    levels: dict[str, TileLevelOut]


class RecentOut(BaseModel):
    pair_id: uuid.UUID
    axis: str
    axis_label: str
    label: str
    note: str
    changed_at: datetime
    changed_by_label: str


class BoardOut(BaseModel):
    """대시보드 한 벌 — **타일과 분포가 같은 자료에서 나온다.**"""

    tiles: list[TileOut]
    recent: list[RecentOut]


class StaffAgentOut(BaseModel):
    id: str
    label: str


class StaffOut(BaseModel):
    """인력 한 줄 — **가명이 기본, 실명은 권한이 있을 때만.**"""

    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str
    alias: str
    """표에 서는 이름(담당 A · B). 부서 안에서 만든 순서로 붙는다."""
    name: str | None
    """실명. 그 부서를 고칠 수 있는 사람과 시스템 관리자에게만 온다 — **없으면 `null`.**"""
    agents: list[StaffAgentOut]
    skill_kinds: list[str]
    outside: bool
    note: str
    share: float
    """담당 하나에 주는 몫(1/n). 조사 밖 업무가 있으면 n+1 로 나눈 값이다."""
    fte: float
    """이 조사에 잡히는 이 사람의 몫 — 담당 수에 `share` 를 곱한 값."""


class StaffIn(BaseModel):
    workspace_slug: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    """실명을 받는다. **화면에는 가명으로 선다** — 투입률은 받지 않는다."""
    agents: list[str] = Field(default_factory=list)
    skill_kinds: list[str] = Field(default_factory=list)
    outside: bool = False
    note: str = ""


class StaffAgentFteOut(BaseModel):
    id: str
    label: str
    fte: float
    people: int


class StaffSummaryOut(BaseModel):
    head_count: int
    fte: float
    """합이 사람 수를 넘지 않는다 — 한 사람은 1.0 이고 담당에 갈려 들어간다."""
    by_agent: list[StaffAgentFteOut]
    by_kind: list[dict[str, Any]]


class CapacityOut(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    sw: list[dict[str, Any]]
    hw: list[dict[str, Any]]
    material_types: int | None
    has_process_std: bool
    note: str


class CapacityIn(BaseModel):
    sw: list[dict[str, Any]] = Field(default_factory=list)
    hw: list[dict[str, Any]] = Field(default_factory=list)
    material_types: int | None = None
    has_process_std: bool = False
    note: str = ""


class CapacitySummaryOut(BaseModel):
    """전사 합계 — **공유 자원은 한 번만 센다.**"""

    departments: int
    sw: list[dict[str, Any]]
    hw: dict[str, int]
    """`cpu_cores` · `ram_gb` 합과 `gpu_units`(GPU 사양이 적힌 자원 수).

    **GPU 는 개수를 더하지 않는다** — 사양을 글로 적기 때문이다(「A100 4장」).
    """
    material_types: int
    process_std: int


class CapacitySheetOut(BaseModel):
    """인프라 현재값 표 — **목록 둘과 부서 기준.**

    한 표에 섞지 않는다. 열이 서로 달라 붙여넣기에서 어긋나고, 그때 라이선스 수가 CPU
    코어 칸에 들어간다.
    """

    sw: list[dict[str, Any]]
    hw: list[dict[str, Any]]
    base: list[dict[str, Any]]


class CapacityBulkIn(BaseModel):
    """어느 표를 저장하나 — 한 번에 하나다(열이 다르다)."""

    what: Literal["sw", "hw", "base"]
    rows: list[dict[str, Any]] = Field(min_length=1)


class SheetRowOut(BaseModel):
    """일괄 입력 표의 한 줄 — **현재값이 채워져 온다.**"""

    pair_id: uuid.UUID
    subject_label: str
    agent_label: str
    agent_dept: str | None
    workspace_name: str
    value: float | None
    rung: str
    """수준 **이름**(key 가 아니다) — 엑셀에서 사람이 읽고 고치는 값이다."""
    rungs: list[str]
    note: str


class SheetOut(BaseModel):
    axis: str
    axis_label: str
    kind: str
    rows: list[SheetRowOut]


class BulkRowIn(BaseModel):
    """한 줄 — `pair_id` 가 없으면 **이름으로 찾는다**(엑셀에서 붙여 넣은 표)."""

    pair_id: uuid.UUID | None = None
    subject_label: str = ""
    agent_label: str = ""
    value: str = ""
    rung: str = ""
    rungs: str | list[str] = ""
    """고른 항목의 **이름들.** 표에서는 항목마다 열이 있어 목록으로 오고, 엑셀 한 칸에서
    온 것은 `·` 로 이어 적힌 글 하나다 — 둘 다 받는다."""
    note: str = ""


class BulkAssessIn(BaseModel):
    axis: str
    rows: list[BulkRowIn] = Field(min_length=1)


class BulkResultOut(BaseModel):
    line: int
    status: str
    """ok · skipped(값이 비어 있음) · error."""
    message: str


class StaffBulkRowIn(BaseModel):
    """인력 표의 한 줄 — **이름과 부서로 그 줄을 찾는다.**"""

    name: str = ""
    workspace_name: str = ""
    agents: str = ""
    """담당 해석 이름들을 `·` 로 이어 적는다."""
    outside: str = ""
    """「예」 면 조사 밖 업무가 있다."""
    skill_kinds: str = ""
    note: str = ""


class StaffBulkIn(BaseModel):
    rows: list[StaffBulkRowIn] = Field(min_length=1)
