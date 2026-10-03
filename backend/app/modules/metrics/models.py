"""지표 — **기록을 미리 세어 둔 값**(ADR 0013).

운영에서는 서비스 기록이 한 타입에 200만 건씩 쌓이고, 사람이 묻는 것은 「판매월별 누적
인입률」 「생산월 x 공장별 건수」 같은 **분모가 있고 시간이 있는** 물음이다. 그때그때 세면
화면 한 번에 수십 초이고, AI 는 원시 행을 토큰으로 끌어와 세게 된다. 그래서 정의 하나를
두고 밤마다(또는 적재 뒤에) 세어 둔다 — **계산은 플랫폼이 하고, AI 는 읽고 해석만 한다.**

## 세 표

- `metric_defs` — 정의. 원천 타입 · 집계 · 시간 칸 · 코호트 칸 · 기준들 · 거르기 · 분모가
  `spec`(JSONB) 한 덩어리다. 타입 · 속성처럼 화면 · API · MCP 로 만든다.
- `metric_runs` — 계산 한 번의 기록. 실패도 남긴다 — 옛 값이 그대로인 이유를 다음 사람이
  본다.
- `metric_values` — 셀. **기초 집계만**(`count` · `value_count` · `sum` · `min` · `max`).
  평균은 읽을 때 `sum / value_count` 로 낸다 — 숫자가 아닌 값이 빠진 평균이 틀리지 않게.

## 바꿔 끼운다

새 실행의 셀을 **다 쓴 뒤** `current_run_id` 를 바꾸고 옛 실행의 셀을 지운다(한 트랜잭션).
읽는 쪽은 `run_id = current_run_id` 로만 읽으므로 반쯤 쓰인 값을 보지 않고, 계산이 실패하면
옛 값이 남는다. `current_run_id` 에 외래키를 걸지 않는 이유: 정의 → 실행 → 정의로 도는
외래키는 `create_all` 과 삭제 순서를 꼬이게 하고, 그 값은 이 모듈만 쓴다.

## 보이는 것만 센다

셀은 **기록의 소유 부서별 부분합**이다. 읽을 때 보이는 부서의 셀만 더하므로 목록 · 통계와
같은 규칙이 선다 — 걸음 너머 객체(기본 모델)의 부서는 보지 않는다(통계와 같다).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.modules.ontology.models import SLUG_MAX

#: 계산 기록의 상태.
RUN_STATUSES = ("running", "ok", "failed")


class MetricDef(Base):
    __tablename__ = "metric_defs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(SLUG_MAX), unique=True)
    """API · MCP · 분모가 이 이름으로 부른다 — **바뀌지 않는다.**"""
    label: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="RESTRICT"), index=True
    )
    """원천 기록 타입. RESTRICT — 지표가 세는 타입은 지울 수 없다(지표를 먼저 지운다)."""
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """`spec.MetricSpec` — 집계 · 시간 · 코호트 · 기준 · 거르기 · 분모 · 닫힘 일수."""
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    interval_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    """0 이면 손으로만. 그 밖이면 타이머(`scripts/recompute_metrics.py --due`)가 이
    간격으로."""
    overlap: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """한 기록이 여러 셀에 드나(여러 값 기준 · 여럿과 이어진 걸음). 그러면 셀의 합이 기록
    수보다 크다 — 정의 · 응답 · 화면 세 곳이 이것을 말한다."""

    current_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    """읽는 쪽이 보는 실행. 바꿔 끼우기 전까지 새 실행의 셀은 아무도 안 본다."""
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cells: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MetricRun(Base):
    """계산 한 번 — 실패도 남긴다."""

    __tablename__ = "metric_runs"
    __table_args__ = (Index("ix_metric_runs_metric_started", "metric_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("metric_defs.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """돌린 작업. 손으로 · 타이머로 · 적재 뒤에 — 어느 길이었는지는 작업이 안다."""
    status: Mapped[str] = mapped_column(
        String(20), default="running", server_default="running"
    )
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """이 실행이 본 「여기까지」 — 코어 API 의 `as_of` 와 같은 규칙(아직 안 끝난 적재보다
    앞서지 않는다). 닫힌 기간 판정과 (나중의) 증분 재계산이 이것을 쓴다."""
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rows: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    """셀의 `count` 합 — 겹침이 없으면 센 기록 수와 같다."""
    cells: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """{unbucketed, unbucketed_cohort, negative_age} — 숨기지 않는 수들."""


class MetricValue(Base):
    """셀 하나 — (부서, 기간, 코호트, 기준 값들) 마다 기초 집계.

    유일성은 JSONB 유니크가 아니라 **해시 PK** 가 지킨다 — JSONB 를 유니크에 넣으면 2704B
    한도와 NULL 구분 문제가 있고, 해시면 정의가 바뀌어도 표는 그대로다. 나중의 증분 재계산
    (`ON CONFLICT … DO UPDATE`)도 이 키로 든다.
    """

    __tablename__ = "metric_values"
    __table_args__ = (
        Index("ix_metric_values_period", "metric_id", "run_id", "period"),
        Index("ix_metric_values_cohort", "metric_id", "run_id", "cohort"),
        # `dims @> '{"base_model": "…"}'` — 기준 값으로 거를 때.
        Index(
            "ix_metric_values_dims",
            "dims",
            postgresql_using="gin",
            postgresql_ops={"dims": "jsonb_path_ops"},
        ),
    )

    metric_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("metric_defs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("metric_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cell_hash: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    """`md5(부서 | 기간 | 코호트 | 기준 JSON)` — 같은 셀은 같은 해시."""
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """기록의 소유 부서. NULL 은 전역. 외래키를 걸지 않는다 — 부서를 지우면 다음 계산이
    새로 센다."""
    period: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    """시간 칸의 기간 시작일. NULL 은 날짜를 못 읽은 기록(「(비어 있음)」) — 합이 맞게
    남긴다."""
    cohort: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """기간 - 코호트(코호트의 단위, 달력 차이). 음수도 저장한다 — 응답이 그 수를 말한다."""
    dims: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """{기준 이름: 값(글자) | null}."""
    count: Mapped[int] = mapped_column(BigInteger)
    value_count: Mapped[int] = mapped_column(BigInteger)
    """측정값이 숫자로 읽힌 기록 수 — 평균의 분모."""
    sum: Mapped[float | None] = mapped_column(Float, nullable=True)
    min: Mapped[float | None] = mapped_column(Float, nullable=True)
    max: Mapped[float | None] = mapped_column(Float, nullable=True)
