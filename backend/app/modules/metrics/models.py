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
    UniqueConstraint,
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
    """0 이면 손으로만. 24 미만이면 그 시간마다(낮에도), 24 의 배수면 **N일마다 밤에**
    (`services.due`). 타이머는 `scripts/recompute_metrics.py --due` 다."""
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """**타이머가 마지막으로 넣은 때** — 「N일마다 밤」 의 차례는 이것으로 센다.

    계산이 끝난 때(`last_run_at`)로 세면 안 된다 — 오늘 밤 타이머가 어제 끝난 시각보다 몇 분
    일찍 깨면 「아직 24시간이 안 지났다」 로 건너뛰어 이틀에 한 번꼴로 셀 수 있었고, 낮에
    적재로 다시 센 날은 그 밤 계산이 밀렸다."""
    overlap: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """한 기록이 여러 셀에 드나(여러 값 기준 · 여럿과 이어진 걸음). 그러면 셀의 합이 기록
    수보다 크다 — 정의 · 응답 · 화면 세 곳이 이것을 말한다."""

    current_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    """읽는 쪽이 보는 실행 — 계산 시각 · 워터마크 · 셈은 이것의 것이다. 바꿔 끼우기 전까지 새
    실행의 셀은 아무도 안 본다."""
    cells_run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """**셀이 들어 있는 실행** — 비면 `current_run_id`. 증분 계산(`incremental.py`)은 새 실행
    기록을 남기되 셀은 이 실행의 칸을 고친다(셀 전부를 새 실행으로 옮겨 쓰면 증분의 이득이
    사라진다). 전량 계산이 이것을 자기로 바꾼다."""
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
    mode: Mapped[str] = mapped_column(String(12), default="full", server_default="full")
    """`full`(전부 다시) · `incremental`(바뀐 기간만 — `periods`)."""
    spec_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    """이 실행이 센 정의의 지문 — 증분은 셀을 만든 전량 실행과 지문이 같을 때만 한다."""
    periods: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    """증분이 다시 센 기간(`YYYY-MM-DD`, 날짜를 못 읽은 칸은 null)."""
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    """전량으로 센 까닭 · 증분의 요약 — 사람이 「왜 오래 걸렸나」 를 여기서 읽는다."""
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


#: 경보가 부르는 분석(ADR 0016) — 「새로 나온 것」 의 뜻이 서는 셋.
ALERT_RECIPES = ("sprt", "control", "changes")


class MetricAlert(Base):
    """경보 — 지표 하나 · 분석 하나 · 그 인자 · 주인(ADR 0016).

    계산이 끝날 때마다 **주인의 눈으로** 다시 돌려, 처음 보는 결론만 주인에게 알린다.
    주인에게만 가는 이유: 셀은 보이는 부서 것만 더해지므로, 다른 사람에게 보내면 그 사람이 못
    보는 부서의 수가 새고 알림을 눌러 열면 다른 수가 보인다.
    """

    __tablename__ = "metric_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("metric_defs.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    recipe: Mapped[str] = mapped_column(String(20))
    """`ALERT_RECIPES` 중 하나."""
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """분석 경로의 쿼리 그대로 — {이름: 글자}, 거르기는 `d.<기준>`. 화면의 분석 탭에서 본 것을
    그대로 저장하고, 알림의 링크도 이것으로 같은 화면을 다시 연다."""
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """NULL 이면 아직 한 번도 안 봤다 — 처음 본 것은 「처음부터 있던 것」 으로 적고 알리지
    않는다."""
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """마지막으로 본 지표 실행. 외래키를 걸지 않는다 — `current_run_id` 와 같은 이유."""
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """`ok` · `failed`. 실패는 처음 한 번만 알린다(같은 실패를 밤마다 알리면 종이 잡음이
    된다)."""
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MetricAlertEvent(Base):
    """발생 — 경보가 처음 본 결론 하나. **(경보, 열쇠)가 유일**해서 같은 결론은 다시 적지도
    알리지도 않는다. 순차 검정의 「나쁨」 은 누적이라 한 번 서면 계속 서 있다 — 열쇠가 없으면
    날마다 같은 알림이 온다."""

    __tablename__ = "metric_alert_events"
    __table_args__ = (
        UniqueConstraint("alert_id", "key", name="uq_metric_alert_events_key"),
        Index("ix_metric_alert_events_alert_created", "alert_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("metric_alerts.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """이 결론을 처음 본 지표 실행."""
    key: Mapped[str] = mapped_column(String(300))
    """`worse:<모델>` · `<차트>:<부분군>:<규칙>` · `<up|down>:<변화점>` — `alerts.py` 가
    짓는다."""
    title: Mapped[str] = mapped_column(String(300))
    """한 줄 — 알림 본문과 화면의 발생 목록이 그대로 쓴다."""
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """수 몇 개(관측 · 기대 · 비) — 화면이 다시 계산하지 않고 그린다."""
    baseline: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """처음 확인에서 본 것 — 알리지 않았다."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HomeMetric(Base):
    """**부서 홈에 올린 지표** — 어느 부서 홈의 몇 번째에, 무엇으로 나눠 볼지.

    지표는 부서에 속하지 않는다(정의는 누구나 보고, 값은 보이는 것만 더한다). 그래서 「어느
    부서 홈에」 를 따로 둔다. 자리(`home_order`)는 **그 부서 홈의 저장된 뷰와 같은 줄**이다 —
    둘을 섞어 한 번에 매긴다(`objects/home.py`). 따로 매기면 뷰와 지표가 같은 자리 값을 가져
    「위로」 가 안 움직인 것처럼 보인다.
    """

    __tablename__ = "home_metrics"
    __table_args__ = (
        UniqueConstraint("workspace_id", "metric_id", name="uq_home_metrics_workspace_metric"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("metric_defs.id", ondelete="CASCADE"), index=True
    )
    """지표를 지우면 홈에서도 내려간다 — 홈에 「없는 지표」 가 남으면 아무도 못 치운다."""
    home_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    split: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """선을 나눌 기준 이름 — 없으면 합계 한 줄."""
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
