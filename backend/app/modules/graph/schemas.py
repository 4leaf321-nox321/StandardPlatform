"""그래프 응답 — **화면이 그리는 데 필요한 것만.**

속성 전체를 실으면 노드 500개에 JSONB 500개가 딸려 온다. 그림에는 이름·타입·
「더 있음」 만 있으면 되고, 나머지는 노드를 눌렀을 때 상세가 준다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.objects.schemas import LogCountOut

# --- 정의 그래프 --------------------------------------------------------------


class TypeNodeOut(BaseModel):
    slug: str
    label: str
    icon: str
    group_slug: str | None
    count: int
    """보이는 객체 수. 남의 부서 것은 안 센다 — 그림에서 수가 새면 안 된다."""


class TypeEdgeOut(BaseModel):
    relation: str
    label: str
    directed: bool
    src_type: str
    dst_type: str
    count: int
    """실제로 걸린 관계 수. 0 이면 **정의만 있고 아직 아무것도 안 이어진** 것이다."""


class OverviewOut(BaseModel):
    nodes: list[TypeNodeOut]
    edges: list[TypeEdgeOut]
    object_count: int
    """전체 객체 수(보이는 것). 「이 그림이 몇 개를 요약한 것인가」."""
    edge_count: int
    counted_at: datetime | None = None
    """선 굵기(관계 · 참조 칸 · 링크 수)를 **찍어 둔 때**(ADR 0038) — 비면 지금 센 것(부서로
    조회를 좁힌 설치)이거나 아직 한 번도 안 센 것(`counting`)."""
    counting: bool = False
    """선 굵기를 아직 안 찍었다 — 세는 작업이 돈다. 그동안 선의 수는 0 이 아니라 「모름」
    이다."""


# --- 이웃 그래프 --------------------------------------------------------------


class NodeOut(BaseModel):
    id: uuid.UUID
    label: str
    key: str | None
    type_slug: str
    type_label: str
    status: str
    owner_workspace_slug: str | None
    """NULL 은 전역. 색 기준 「부서별」 이 쓴다."""
    degree: int
    """이 노드에 걸린 **보이는** 관계의 수 — 잘렸으면 화면의 수보다 크다."""
    truncated: bool
    """화면에 실린 것보다 관계가 더 있다. **「+N 더」 를 적는 근거다.**"""


class EdgeOut(BaseModel):
    id: uuid.UUID
    relation: str
    label: str
    inverse_label: str
    """역방향의 말. hover 에 「개발사 ↔ 개발함」 으로 둘 다 보인다."""
    directed: bool
    src: uuid.UUID
    dst: uuid.UUID


class NeighborhoodOut(BaseModel):
    focus: uuid.UUID
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    depth: int
    fanout: int
    node_limit: int
    truncated: bool
    """상한 때문에 어딘가가 잘렸다. 잘렸는데 말 안 하면 그림은 「이게 전부」 로 읽힌다."""
    log_counts: list[LogCountOut] = []
    """시작점을 가리키는 **기록**(ADR 0011) — 이웃으로 안 싣고 수로 보인다."""


class SubgraphOut(BaseModel):
    """한 타입(들)의 인스턴스 전부 — **상한 안에서.**"""

    nodes: list[NodeOut]
    edges: list[EdgeOut]
    total: int
    """조건에 맞는 객체 전체 수. 실린 수보다 크면 화면이 「N개 중 M개」 라고 말한다."""
    limit: int
    offset: int
    truncated: bool


class PathStepsOut(BaseModel):
    """길 하나 — 노드 차례와 그 사이의 선 차례.

    `edges[k]` 가 `nodes[k]` 와 `nodes[k+1]` 을 잇는다."""

    nodes: list[uuid.UUID]
    edges: list[uuid.UUID]


class PathOut(BaseModel):
    """두 객체 사이의 **가장 짧은 길들** — 그림에 세울 노드 · 선과, 길마다의 차례."""

    source: uuid.UUID
    target: uuid.UUID
    found: bool
    length: int | None
    """선 수. 못 찾았으면 null."""
    paths: list[PathStepsOut]
    """같은 길이의 길들(`limit` 개까지)."""
    nodes: list[NodeOut]
    """길들이 지나는 노드 전부. 못 찾았으면 출발 · 도착 둘."""
    edges: list[EdgeOut]
    max_depth: int
    visited: int
    """찾느라 들른 노드 수(양쪽 합)."""
    truncated: bool
    """상한(들른 노드 수 · 노드당 선 수)에 닿아 덜 찾았다 — **못 찾았어도 「길이 없다」 가
    아니다.** 관계 · 타입을 좁혀 다시 찾는다."""


class SearchHitOut(BaseModel):
    id: uuid.UUID
    label: str
    key: str | None
    type_slug: str
    type_label: str
