"""분석의 응답 — 머리는 지표 읽기와 같고(계산 시각 · 닫힘 · 겹침 · 분모), 그 위에 방법 · 요청 ·
주의 · 뺀 것 · 보이는 몫이 붙는다(ADR 0014)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel

from app.modules.metrics.schemas import DrillOut, ReadHeader


class CaveatOut(BaseModel):
    """결과와 함께 **그대로 전해야 하는 말** — 코드는 기계가, 말은 사람이 읽는다."""

    code: str
    level: Literal["info", "warn"]
    message: str
    count: int | None = None


class AnalysisHeader(ReadHeader):
    recipe: str
    method: str
    """방법과 판 — 같은 물음의 답이 바뀌면 방법이 바뀐 것인지 자료가 바뀐 것인지 가른다."""
    params: dict[str, Any]
    """기본값을 채운 요청 그대로."""
    run_id: uuid.UUID | None
    caveats: list[CaveatOut]
    excluded: dict[str, int]
    """뺀 기록 수 — 열린 기간 · 분모 없음 · 분모보다 많음 · 음수 경과 …"""
    visible_share: float | None
    """지금 실행의 기록 중 이 사람에게 보이는 몫 — 1 보다 작으면 비율이 낮게 나올 수 있다."""


# --- ⑦ 파레토 · 집중도 ---------------------------------------------------------------


class ParetoItemOut(BaseModel):
    key: str | None
    label: str
    value: float
    count: int
    share: float
    cumulative: float
    cls: Literal["A", "B", "C"]
    ratio: float | None = None
    drill: DrillOut


class ConcentrationOut(BaseModel):
    categories: int
    """본 값의 수 — 한 번도 안 나온 값은 들지 않는다."""
    hhi: float
    hhi_norm: float
    effective: float
    """유효 개수 = 1 / HHI — 「몇 개가 고르게 나눠 가진 셈인가」."""
    gini: float
    cr1: float
    cr3: float
    cr5: float
    cr10: float
    vital_few: int
    """A 등급(누적 80% 에 처음 닿는 값까지)의 수."""
    vital_share: float


class ParetoTrendOut(BaseModel):
    period: str
    label: str
    total: float
    hhi: float
    effective: float
    gini: float
    top_share: float
    closed: bool


class ParetoOut(AnalysisHeader):
    dim: str
    dim_label: str
    basis: Literal["records", "occurrences"]
    """`occurrences` 면 한 기록이 여러 값에 든다(교체 부품) — 몫은 나온 횟수 기준이다."""
    total: float
    empty_count: int
    empty_value: float
    items: list[ParetoItemOut]
    other_categories: int
    other_value: float
    concentration: ConcentrationOut | None
    trend: list[ParetoTrendOut]
