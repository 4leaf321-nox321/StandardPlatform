"""그래프 응답 — **화면이 그리는 데 필요한 것만.**

속성 전체를 실으면 노드 500개에 JSONB 500개가 딸려 온다. 그림에는 이름·타입·
「더 있음」 만 있으면 되고, 나머지는 노드를 눌렀을 때 상세가 준다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

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
    valid_from: date | None = None
    """유효 기간의 시작일(포함, ADR 0039) — 유효 기간을 받는 관계 종류의 선만."""
    valid_to: date | None = None
    """유효 기간의 끝날(제외)."""


class NeighborhoodOut(BaseModel):
    focus: uuid.UUID
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    depth: int
    fanout: int
    node_limit: int
    truncated: bool
    """상한 때문에 어딘가가 잘렸다. 잘렸는데 말 안 하면 그림은 「이게 전부」 로 읽힌다."""
    as_of: date | None = None
    """이 그림의 기준일(ADR 0039) — 그날 유효한 관계만 그렸다."""
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
    as_of: date | None = None
    """이 그림의 기준일(ADR 0039)."""


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
    as_of: date | None = None
    """이 길의 기준일(ADR 0039) — 그날 유효한 관계만 따라갔다."""
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


# --- 정적 그리기(통째 그림) ---------------------------------------------------
#
# ADR 0045. 노드 20만 개를 `NodeOut` 사전으로 주면 수십 MB 다 — 그래서 **열 배열**로 준다:
# i 번째 노드는 `nodes` 의 각 배열의 i 번째 값이고, 선은 노드 번호 둘과 관계 표 번호다.
# 타입 · 관계의 이름은 표에 한 번씩만 적힌다.


class WholeTypeOut(BaseModel):
    """그림에 실린 타입 하나 — 노드의 `type` 이 이 표의 번호다."""

    slug: str
    label: str
    count: int
    """이 그림에 실린(거절이면 센) 그 타입의 노드 수."""


class WholeRelationOut(BaseModel):
    """그림에 실린 선의 종류 하나(관계 · 참조 칸 · 원 표 링크) — 선의 `rel` 이 이 표의
    번호다."""

    slug: str
    label: str
    inverse_label: str
    directed: bool
    count: int


class WholeNodesOut(BaseModel):
    """노드 — **열 배열.** 배열마다 길이가 같고, 같은 자리가 같은 노드다."""

    ids: list[str]
    """객체 id(uuid 글자). 원 표의 행(부서 · 계정)이면 그 행의 id."""
    type: list[int]
    """`types` 표의 번호."""
    label: list[str]
    depth: list[int]
    """씨앗에서 몇 단계(씨앗 = 0) — 처음 닿은 단계(BFS)다."""
    parent: list[int]
    """그 노드를 처음 데려온 노드의 번호(바로 앞 단계). 씨앗은 -1."""
    x: list[float] | None = None
    """자리 — **지금은 늘 비어 있다.** 화면이 자리를 정한다(무리 · 단계). 서버가 전체의 자리를
    미리 계산해 두는 다음 단계(ADR 0045 「다음」)가 채울 자리로 열어 둔다."""
    y: list[float] | None = None


class WholeEdgesOut(BaseModel):
    """선 — 열 배열. `src[k]` → `dst[k]` 가 `relations[rel[k]]` 종류의 선이다."""

    src: list[int]
    dst: list[int]
    rel: list[int]


class WholeOverOut(BaseModel):
    """**상한을 넘어 그리지 않았다** — 반쯤 그린 그림은 「이게 전부」 로 읽히므로 잘라 주지
    않고, 무엇이 얼마나 많은지를 준다. 화면은 타입 · 관계로 좁히라고 안내한다."""

    what: str
    """`nodes` 면 노드가, `edges` 면 선이 상한을 넘었다."""
    count: int
    exact: bool
    """거짓이면 「count 개 이상」 — 상한을 넘은 자리에서 세기를 멈췄다(넘은 것을 끝까지 세면
    거절이 느려진다)."""
    limit: int
    types: list[WholeTypeOut]
    """센 노드의 타입별 수 — 큰 것부터."""
    layers: list[int]
    """단계별 노드 수(센 데까지) — 「3단계에서 늘어난다」 를 보고 단계를 줄인다."""
    relations: list[WholeRelationOut]
    """선이 넘었을 때 센 선의 종류별 수 — 큰 것부터."""


class WholeOut(BaseModel):
    """정적 그리기의 재료 — 씨앗에서 `depth` 단계 안의 노드와 **그 사이의 선 전부**.

    탐색 그림과 달리 노드마다 이웃 수로 자르지 않는다(`fanout` 이 없다). 상한(`max_nodes` ·
    `max_edges`)을 넘으면 그림 대신 `over` 가 온다 — 그때 `nodes` · `edges` 는 비어 있다."""

    seed: str
    """`focus`(객체 하나) 또는 `types`(타입 전부)."""
    focus: uuid.UUID | None
    seed_types: list[str]
    depth: int
    records: bool
    as_of: date | None = None
    """이 그림의 기준일(ADR 0039) — 그날 유효한 관계만 따라가고 그렸다."""
    max_nodes: int
    max_edges: int
    types: list[WholeTypeOut]
    relations: list[WholeRelationOut]
    nodes: WholeNodesOut
    edges: WholeEdgesOut
    over: WholeOverOut | None = None


# --- 전체 지도(ADR 0046) -------------------------------------------------------
#
# 서버가 미리 계산해 둔 지도 — 기준정보는 판에 적힌 자리, 기록은 허브 원반 안의 해시 자리.
# 멀리서는 원반(넓이 = 보이는 기록 수)과 밀도, 확대하면 상자 안의 점(5만까지). 열 배열이다.


class MapTypeOut(BaseModel):
    """지도에 나오는 타입 — 기준정보(점) 또는 기록(원반 안의 점)."""

    slug: str
    label: str
    record: bool
    hub_key: str | None = None
    """기록 타입이면 이 판의 허브 칸(비면 미지정 — 모든 기록이 미지정 자리에)."""
    hub_label: str | None = None
    count: int
    """이 지도에서 보이는 수 — 기준정보는 점 수, 기록은 원반 · 미지정 자리에 든 수(집계)."""


class MapLayoutOut(BaseModel):
    """판 — 배치 한 번. 번호는 늘기만 한다."""

    version: int
    kind: str
    """`full`(전체 배치) · `append`(매일 덧붙임 — 새 기준정보만, 기존 자리는 그대로)."""
    laid_at: datetime
    full_version: int
    full_at: datetime
    by_timer: bool
    """타이머가 놓았나(처음 판 · 매일 덧붙임) — 아니면 관리자의 「전체 다시 배치」."""
    masters: int
    moved_p50: float | None = None
    """전체 다시 배치에서 기존 기준정보가 움직인 거리(지도 폭 대비) — 가운데 · 95%."""
    moved_p95: float | None = None


class MapJobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str


class MapMastersOut(BaseModel):
    """기준정보 — 열 배열. 보는 사람에게 보이고 살아 있는 것만."""

    ids: list[str]
    type: list[int]
    """`types` 표의 번호."""
    label: list[str]
    x: list[float]
    y: list[float]


class MapDiscsOut(BaseModel):
    """원반 — 열 배열. 허브 원반(`hub` 가 기준정보 번호)이거나 기록 타입의 미지정 자리(`slot`
    이 타입 번호). 반지름은 **보는 사람이 볼 수 있는 부서의 얼린 수**로 정했다.

    수는 세 열로 — `count_disc[k]` 원반의 `count_type[k]` 타입 기록이 지금 `count_n[k]`
    건(시간마다 센 수, 볼 수 있는 부서만)."""

    x: list[float]
    y: list[float]
    r: list[float]
    hub: list[int]
    slot: list[int]
    count_disc: list[int]
    count_type: list[int]
    count_n: list[int]


class MapUnplacedOut(BaseModel):
    """아직 자리가 없는 기록 — 판 뒤에 생긴 기준정보를 가리킨다(다음 덧붙임에서 놓인다)."""

    type: int
    count: int


class MapOut(BaseModel):
    """전체 지도 — 멀리 보기의 재료. 판이 없으면 `ready` 가 거짓이고 나머지는 비어 있다."""

    ready: bool
    layout: MapLayoutOut | None = None
    counted_at: datetime | None = None
    """원반의 수 · 밀도가 언제 센 것인가(한 시간마다)."""
    scale: float = 0.0
    """반지름 = scale x √(얼린 수)."""
    bbox: list[float]
    """지도 틀 [x0, y0, x1, y1] — 화면은 이 틀에 고정한다."""
    max_points: int
    """확대한 상자에 실을 점의 상한 — 넘으면 밀도로."""
    types: list[MapTypeOut]
    masters: MapMastersOut
    discs: MapDiscsOut
    unplaced: list[MapUnplacedOut]
    relations: list[WholeRelationOut]
    """선의 종류 — 지도에 나올 수 있는 것 전부(관계 종류 · 지도 타입의 참조 칸), slug 차례.
    확대한 점의 선은 종류를 slug 로만 주고 이름은 이 표에서 찾는다. 수는 기준정보끼리의
    선(`lines`)의 것."""
    lines: WholeEdgesOut
    """보이는 기준정보끼리의 선(참조 칸 · 오늘 살아 있는 관계) — 끝은 `masters` 의 번호,
    `rel` 은 `relations` 의 번호. 판에 적지 않고 읽으므로 새 선은 다음 집계(한 시간 안)부터
    보인다."""
    job: MapJobOut | None = None
    """기다리거나 도는 지도 작업."""
    last_error: str | None = None
    """마지막 지도 작업이 실패했으면 그 까닭(판을 못 만든 까닭 — 상한 넘음 등)."""
    can_relayout: bool
    """「전체 다시 배치」 를 누를 수 있나(시스템 관리자)."""
    drift: list[str]
    """허브 칸 설정이 이 판과 다른 기록 타입(slug) — 다음 전체 배치에 반영된다."""


class MapPointLinesOut(BaseModel):
    """확대한 점의 선 — 열 배열. 끝 번호가 점 수(`count`)보다 작으면 그 점, 아니면
    `masters[번호 - count]` 기준정보(멀리 보기의 `masters` 에 있는 것). `rel[k]` 는 `kinds` 의
    번호이고, 이름 · 색은 멀리 보기의 `relations` 에서 slug 로 찾는다. 상자 밖 기록과 잇는 선은
    없다."""

    src: list[int]
    dst: list[int]
    rel: list[int]
    kinds: list[str]
    masters: list[str]


class MapPointsOut(BaseModel):
    """상자 안의 기록 점 — 기대 수(집계)가 상한을 넘으면 `over` 이고 점은 비어 있다."""

    box: list[float]
    expected: int
    """집계로 센 상자 안 기대 수(원반 넓이 x 상자에 든 몫)."""
    over: bool
    limit: int
    count: int
    types: list[str]
    """점의 타입 slug 표 — `type` 이 이 표의 번호다."""
    ids: list[str]
    x: list[float]
    y: list[float]
    type: list[int]
    labels: list[str] | None = None
    """점이 5천 개 이하일 때만 이름."""
    skipped: list[MapTypeOut]
    """점으로 못 그린 미지정 기록(그 타입이 커서 색인 없이 못 읽는다) — 상자 안 기대 수."""
    lines: MapPointLinesOut | None = None
    """점의 선(참조 칸 · 오늘 살아 있는 관계) — 점이 `lines_up_to` 이하일 때만."""
    lines_up_to: int
    lines_over: bool = False
    """점은 문턱 이하인데 선이 너무 많아(여러 값 칸) 싣지 않았다."""


class MapLinkOut(BaseModel):
    id: str
    label: str
    type_slug: str
    count: int
    """그 허브의 기록 중 이 기준정보도 함께 가리킨 수(판 시각, 볼 수 있는 부서만)."""
    x: float
    y: float


class MapLinksOut(BaseModel):
    """허브의 주 참조 밖의 연결 — 공동 참조로 이어진 기준정보(위에서부터)."""

    hub: str
    items: list[MapLinkOut]
    withheld: bool
    """부서별로 세지 않은 판이라 부서로 좁힌 사람에게는 보이지 않는다."""
    counted_at: datetime | None = None
    """판 시각 — 공동 참조 선은 판을 만들 때 센다."""


class MapLocateOut(BaseModel):
    """객체 하나가 지도의 어디인가 — 주소의 `at`(객체 id)을 자리로."""

    id: str
    kind: str
    """`master`(기준정보 점) · `record`(원반 안의 점) · `none`(지도에 없음 — `reason`)."""
    x: float | None = None
    y: float | None = None
    r: float | None = None
    """기록이면 그 원반의 반지름 — 화면이 그만큼 확대한다."""
    label: str = ""
    type_slug: str = ""
    reason: str | None = None


class MapHubCandidateOut(BaseModel):
    key: str
    label: str
    target_label: str
    target_types: list[str]
    """가리킬 수 있는 타입 slug — 정적 그리기의 「한 축만 보기」 가 이웃 타입을 이것으로
    좁힌다."""
    required: bool
    hubs: int


class MapHubKeyOut(BaseModel):
    """기록 타입의 허브 칸 — 자동 · 관리자가 고름 · 미지정."""

    type_slug: str
    type_label: str
    mode: str
    """`auto` · `admin` · `none`."""
    key: str | None
    """지금 설정으로 쓸 칸(다음 전체 배치부터)."""
    auto_key: str | None
    used_key: str | None
    """지금 판이 쓰는 칸."""
    in_layout: bool
    """지금 판에 이 타입이 있나."""
    stale: bool
    """관리자가 고른 칸이 이제 알맞지 않아 자동으로 돌아갔다."""
    candidates: list[MapHubCandidateOut]


class MapHubKeyIn(BaseModel):
    mode: str
    """`auto`(자동 규칙) · `key`(아래 칸) · `none`(미지정 — 허브 없음)."""
    key: str | None = None
