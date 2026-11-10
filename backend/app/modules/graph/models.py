"""지도(「전체 보기」, ADR 0046)의 표 — **판 · 기준정보 자리 · 얼린 수 · 시간마다 센 수 · 공동
참조 선 · 허브 칸 설정.**

기록(수천만)의 자리는 어디에도 적지 않는다 — 기록은 허브 원반 안에 id 해시로 놓인다(`maphash`).
적는 것은 기준정보(수만)의 자리와 원반의 크기를 정하는 수뿐이다. 판이 바뀌면 자리 표를 통째로
갈아 쓰고, 덧붙임은 새 기준정보의 줄만 더한다(기존 줄은 그대로 — 움직임 0).

표마다 **FK 가 없다**(설정 표만 타입에 묶는다) — 객체를 지워도 판의 줄은 남고, 읽을 때 살아
있고 보이는 것만 고른다. 지도는 계산해 둔 사본이지 정본이 아니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MapLayout(Base):
    """판 — 배치 한 번(전체 또는 덧붙임)이 한 줄. 번호(`id`)가 곧 판 번호이고 늘 는다.
    지금 판은 가장 큰 번호다. 자리 표는 지금 판의 것만 든다(옛 판은 이 메타만 남는다)."""

    __tablename__ = "graph_map_layouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    """`full`(전체 배치 — 처음 판 · 관리자의 「전체 다시 배치」) · `append`(매일 덧붙임)."""
    base_id: Mapped[int] = mapped_column(Integer)
    """이 판이 기대는 전체 배치의 번호 — 전체 배치면 자기 번호."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """전체 다시 배치를 누른 사람. 비면 타이머(처음 판 · 매일 덧붙임)."""
    scale: Mapped[float] = mapped_column(Float)
    """원반 반지름 = scale · √(기록 수) — 전체 배치에서 정하고 덧붙임은 그대로 쓴다."""
    bbox: Mapped[list[float]] = mapped_column(JSONB)
    """지도 범위 [x0, y0, x1, y1] — 화면이 이 틀에 고정한다(자리가 바뀌어도 다시 맞추지
    않는다)."""
    hub_keys: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """이 판의 허브 칸 — {기록 타입 id: 칸 키 또는 null(미지정)}. 시간마다의 집계 · 확대가
    **판의 칸**을 쓴다(설정을 바꿔도 다음 전체 배치까지는 이 칸이다)."""
    slots: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """기록 타입마다 「미지정」 자리 — {타입 id: [x, y, 잡아 둔 반지름]}."""
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """기준정보 수 · 허브 수 · 선 수 · 걸린 시간 · 움직인 거리(전체 다시 배치) — 화면과 운영
    문서."""


class MapMaster(Base):
    """지금 판의 기준정보 자리 — 기준정보 하나에 한 줄."""

    __tablename__ = "graph_map_masters"

    object_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    type_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    placed_in: Mapped[int] = mapped_column(Integer)
    """처음 놓인 판(전체 배치면 그 판, 덧붙임이면 그 덧붙임)."""


class MapFrozen(Base):
    """**얼린 수** — 판을 만들 때의 (허브, 기록 타입, 부서) 수. 원반 반지름이 이것으로 정해져
    판 안에서는 자리가 시간마다 흔들리지 않는다. 보는 사람의 반지름은 그 사람이 볼 수 있는
    부서의 수만 더한다 — 남의 부서 수가 크기로 새지 않는다."""

    __tablename__ = "graph_map_frozen"
    __table_args__ = (Index("ix_graph_map_frozen_hub", "hub_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hub_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """비면 「미지정」(주 참조가 빈 기록)."""
    type_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """기록의 소유 부서 — 비면 전역."""
    n: Mapped[int] = mapped_column(BigInteger)
    layout_id: Mapped[int] = mapped_column(Integer)
    """얼린 판(전체 배치 또는 그 허브를 처음 놓은 덧붙임)."""


class MapCount(Base):
    """**시간마다 센 수** — (허브, 기록 타입, 부서) → 지금의 수. 멀리 보기의 밀도와 확대의 기대
    수가 이것이다(기록을 안 읽는다). 트리거로 유지하지 않는다 — 쓰기마다 증감 줄이 붙으면
    넣기가 5 ~ 20% 느려졌다(측정 보고). 한 시간마다 통째로 갈아 쓴다."""

    __tablename__ = "graph_map_counts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hub_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    type_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    n: Mapped[int] = mapped_column(BigInteger)


class MapCounted(Base):
    """시간마다의 집계가 언제였나 — 한 줄(id 1). 지도에 「몇 시 기준」 으로 적힌다."""

    __tablename__ = "graph_map_counted"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    layout_id: Mapped[int] = mapped_column(Integer)
    """어느 판의 허브 칸으로 셌나 — 판이 바뀌면 다시 센다."""
    points_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """확대 점 표(`graph_map_points`)를 마지막으로 맞춘 때(DB 시각) — 다음 맞춤은 그 앞
    `POINTS_OVERLAP` 부터 바뀐 기록을 다시 본다."""
    points_keys: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """점 표를 채운 허브 칸 — 판의 칸과 다르면 통째로 다시 채운다."""


class MapPoint(Base):
    """**확대 점 표** — 기록 하나에 한 줄: (허브, 원반 안 자리의 해시, 기록 id). 확대가 상자에
    닿는 해시 칸을 `(허브, 해시)` 구간으로 읽는다(ADR 0046 「확대 색인의 형태」).

    참조 색인(`object_refs`)에 식 색인을 거는 길(①)은 쓰기마다 그 색인에도 써서 기록 2만 줄
    넣기가 두 배가 됐다(2,000만 건 DB 11.6 → 23.1초 — 해시 차례라 색인 쪽지가 무작위로
    흩어진다). 그래서 따로 둔 표를 **시간마다의 집계 작업이** 바뀐 기록만 맞춘다 — 쓰기 길에는
    아무것도 안 붙는다. 새 기록은 다음 집계(한 시간 안)부터 점으로 보인다. 지운 기록은 읽을 때
    객체 표로 거른다(바로 빠진다)."""

    __tablename__ = "graph_map_points"
    __table_args__ = (
        Index("ix_graph_map_points_hub", "hub_id", "h", postgresql_include=["id"]),
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    h: Mapped[int] = mapped_column(BigInteger)
    """`sp_map_hash(id)` — 원반 안 자리."""


class MapLink(Base):
    """공동 참조 선 — 같은 기록이 함께 가리킨 기준정보 짝(위 k 개만, 판 시각 기준). 허브를
    고르면 주 참조 밖의 연결(부품 · 센터 · 불량 …)을 이것으로 긋는다. 부서마다 따로 세어 보는
    사람이 볼 수 있는 부서의 수만 더한다."""

    __tablename__ = "graph_map_links"
    __table_args__ = (
        Index("ix_graph_map_links_a", "a_id"),
        Index("ix_graph_map_links_b", "b_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    a_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    b_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    n: Mapped[int] = mapped_column(BigInteger)


class MapHubKey(Base):
    """허브 칸 설정 — 기록 타입마다. **줄이 없으면 자동**(필수 · 값 하나 · 기준정보를 가리키는
    칸 중 가리킬 수 있는 기준정보가 가장 많은 칸), 줄이 있고 `key` 가 비면 「미지정」(허브
    없음)."""

    __tablename__ = "graph_map_hub_keys"

    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("object_types.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)


# ── 밤 그래프 계산(ADR 0067) ─────────────────────────────────────────────────────────────
# 지도처럼 **계산해 둔 사본**이다 — 객체에 FK 가 없고(지워도 판의 줄은 남는다), 읽을 때 살아
# 있고 보이는 것만 고른다. 판(`GraphCalcRun`)마다 줄을 새로 쓰고, 다 쓴 뒤 그 판을 「지금」
# 으로 삼고 앞 판을 지운다 — 쓰는 동안에도 읽는 쪽은 앞 판을 본다.


class GraphCalcRun(Base):
    """판 — 계산 한 번이 한 줄. **지금 판은 끝난(`done`) 것 중 가장 큰 번호.**"""

    __tablename__ = "graph_calc_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """「지금 계산」 을 누른 사람. 비면 타이머."""
    engine: Mapped[str] = mapped_column(String(8))
    """`cpu` · `gpu` — 실제로 센 엔진(설정이 `auto` 여도 무엇으로 돌았는지)."""
    status: Mapped[str] = mapped_column(String(12))
    """`running` · `done` · `failed`."""
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """노드 · 선 수, 단계마다 걸린 시간, 건너뛴 까닭, 실패 까닭 — 화면과 운영 문서."""


class GraphCalcScore(Base):
    """(판, 지표, 관계, 객체) → 값 · 순위. 지표: `depends_top`(관계마다) · `bridge` · `cluster`
    (값이 무리 번호, 순위는 무리 안의 차례 — 이음이 많은 것부터)."""

    __tablename__ = "graph_calc_scores"
    __table_args__ = (
        Index("ix_graph_calc_scores_rank", "run_id", "metric", "relation", "rank"),
        Index("ix_graph_calc_scores_value", "run_id", "metric", "relation", "value"),
        Index("ix_graph_calc_scores_object", "object_id"),
    )

    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("graph_calc_runs.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(24), primary_key=True)
    relation: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    """이어지는 관계 종류의 slug — 관계마다 세는 지표(`depends_top`)만. 나머지는 빈 글자."""
    object_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)


class GraphCalcCluster(Base):
    """(판, 무리) → 크기 · 대표(무리 안에서 이음이 가장 많은 것) · 이름 · 타입별 수."""

    __tablename__ = "graph_calc_clusters"

    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("graph_calc_runs.id", ondelete="CASCADE"), primary_key=True
    )
    cluster: Mapped[int] = mapped_column(Integer, primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    representative_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    label: Mapped[str] = mapped_column(String(300))
    """대표의 이름(셀 때) — 대표가 지워져도 무리를 부를 이름이 남게."""
    types: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """{타입 slug: 수}."""


class GraphCalcSimilar(Base):
    """(판, 관계, 객체, 차례) → 상대 · 점수(끝까지 펼친 구성의 자카드). 같은 타입끼리 위 10."""

    __tablename__ = "graph_calc_similar"
    __table_args__ = (Index("ix_graph_calc_similar_object", "object_id"),)

    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("graph_calc_runs.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    other_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    score: Mapped[float] = mapped_column(Float)
