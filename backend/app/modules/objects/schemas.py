"""객체 API 형태."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.ontology.schemas import PropertyDefOut


class ObjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type_slug: str
    key: str | None
    label: str
    description: str
    properties: dict[str, Any]
    ref_labels: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    """사람이 붙인 다른 이름. 찾기·참조 풀이·파일이 이것으로도 찾는다."""
    external_ids: dict[str, str] = Field(default_factory=dict)
    """{데이터 소스 slug: 그쪽 식별자}. 동기화가 남긴다 — 화면에서는 보기만."""
    """`object_ref` 속성이 가리키는 객체의 **이름**(id -> 이름).

    **값에는 id 만 있다.** 그대로 그리면 목록에 UUID 가 뜨고, 그 열은 아무것도
    말해 주지 못한다 — 그러면 「참조를 열로 보이기」 기능 자체가 쓸모없어진다.
    화면이 객체마다 이름을 물으러 가면 목록 한 쪽에 요청이 수십 개 붙으므로,
    **서버가 한 번에 모아 실어 준다.**"""

    status: str
    owner_workspace_slug: str | None
    """NULL 은 전역이다 — 여러 부서가 함께 쓰므로 고치는 것은 시스템 관리자뿐이다."""
    valid_from_year: int | None
    valid_to_year: int | None
    created_at: datetime
    updated_at: datetime


class ObjectCreateRequest(BaseModel):
    key: str | None = None
    label: str = Field(min_length=1, max_length=200)
    description: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    workspace_slug: str | None = None
    """어느 부서 것으로 만들 것인가. **NULL 이면 전역이고 시스템 관리자만** 만든다."""
    valid_from_year: int | None = None
    valid_to_year: int | None = None


class ObjectPatchRequest(BaseModel):
    """**「안 보낸 것」 과 「비운 것」 을 구별한다.**

    안 보낸 필드는 그대로 둔다. `properties` 는 보낸 키만 병합하고, 값을
    지우려면 그 키에 `null` 을 명시한다 — 통째로 덮으면 이름 하나 바꿀 때마다
    다른 속성이 함께 날아가고 **그 손실은 저장한 사람 눈에 안 보인다.**
    """

    key: str | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    properties: dict[str, Any] | None = None
    status: str | None = None
    valid_from_year: int | None = None
    valid_to_year: int | None = None


class AttachmentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_field: str | None
    original_name: str
    size_bytes: int
    created_at: datetime


class RelatedObjectOut(BaseModel):
    """「관련 객체」 한 줄.

    **방향을 함께 준다.** 어느 쪽으로 읽느냐에 따라 말이 달라지는데(속함 <-> 포함),
    화면이 그것을 스스로 알 방법이 없다.
    """

    relation_id: uuid.UUID
    relation: str
    """관계 종류의 slug."""
    label: str
    """이 줄에 적을 말. 방향에 맞는 쪽(정방향이면 label, 역방향이면 inverse_label)."""
    outgoing: bool
    """내가 출발점인가. 거짓이면 저쪽이 나를 가리킨다."""

    object_id: uuid.UUID
    object_label: str
    object_key: str | None
    object_type_slug: str
    object_type_label: str

    properties: dict[str, Any]
    evidence_note: str
    created_at: datetime


class TreeNodeOut(BaseModel):
    """트리 한 줄."""

    id: uuid.UUID
    label: str
    key: str | None
    status: str
    child_count: int
    """**자식 수를 미리 준다.** 없는데 펼침 화살표가 보이면 눌러 보고서야 빈 것을
    안다 — 그 한 번이 매 노드마다 반복된다."""


class TreeOut(BaseModel):
    nodes: list[TreeNodeOut]
    orphan_count: int
    """부모도 자식도 없는 것의 수.

    **트리에 안 보이는 채로 남으면 눈에서 사라진다.** 뿌리와 가르는 이유는,
    트리를 아직 안 만든 타입에서는 거의 모두가 부모가 없어 안 가르면 뿌리
    목록이 곧 전체 목록이 되기 때문이다."""


class RelationCreateRequest(BaseModel):
    relation: str
    dst_object_id: uuid.UUID
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence_note: str = Field(default="", max_length=500)


class RelationPatchRequest(BaseModel):
    """**보낸 것만 바꾼다.** 양끝과 종류는 못 바꾼다 — 그건 다른 관계다."""

    properties: dict[str, Any] | None = None
    evidence_note: str | None = Field(default=None, max_length=500)


class ObjectProfileOut(BaseModel):
    """객체 하나에 착지하면 **연결된 것이 모인다.**

    타입 정의를 함께 주는 이유: 화면이 폼을 그리려면 속성 정의가 필요한데, 따로
    받게 하면 두 번 왕복하고 그 사이에 정의가 바뀔 수 있다.
    """

    object: ObjectOut
    type_label: str
    properties_schema: list[PropertyDefOut]
    attachments: list[AttachmentBrief]
    related: list[RelatedObjectOut]
    """이 객체에 걸린 관계들. **양방향 다 온다** — 「이것이 가리키는 것」 만 주면
    「이것을 가리키는 것」 을 물을 자리가 없어진다."""

    can_edit: bool
    """**서버가 판정한 것을 화면에 알려 준다.** 화면이 스스로 정하면 어떤 화면은
    단추를 보이고 어떤 화면은 안 보이는 상태가 되고, 그 차이는 설명할 수 없다."""


class AliasesRequest(BaseModel):
    """사람이 붙인 별칭을 통째로 — 빈 목록이면 전부 지운다."""

    aliases: list[str] = Field(max_length=50)


class RollupOut(BaseModel):
    """「아래 전부」 를 모은 수 하나 — 볼 때마다 센다(저장하지 않는다)."""

    property: str
    label: str
    fn: str
    value: float | None
    """값이 하나도 없으면 null. 0 으로 두면 「합이 0」 으로 읽힌다."""
    count: int
    missing: int
    """아래에 있지만 값이 빈 객체 수. **이것이 붙어야 합계가 「전부의 합」 으로 안 읽힌다.**"""
    descendants: int


# --- 일괄 -------------------------------------------------------------------


class ImportRowOut(BaseModel):
    row: int
    """파일의 몇 번째 행인가(헤더 다음이 1). 오류를 고치러 갈 자리."""
    action: str
    """`create` · `update` · `unchanged` · `error`."""
    label: str
    key: str | None
    object_id: uuid.UUID | None
    changes: list[str]
    """바뀌는 칸. 「고침」 이 무엇을 고치는지 보여 준다 — 안 보여 주면 사람은 안 누른다."""
    message: str


class ImportPlanOut(BaseModel):
    applied: bool
    rows: list[ImportRowOut]
    errors: list[str]
    """행과 무관한 오류(모르는 열, 상한). 하나라도 있으면 **아무것도 안 넣는다.**"""
    counts: dict[str, int]


class ImportRowsRequest(BaseModel):
    """파일 대신 JSON 으로 — MCP 가 쓴다. 규칙은 파일과 같다."""

    rows: list[dict[str, Any]]
    workspace_slug: str | None = None
    apply: bool = False


# --- 지우기 전에 -------------------------------------------------------------


class RefHitOut(BaseModel):
    """이 객체를 **속성으로** 가리키는 객체 하나."""

    object_id: uuid.UUID
    label: str
    key: str | None
    type_slug: str
    type_label: str
    property_key: str
    property_label: str


class RelationHitOut(BaseModel):
    relation_id: uuid.UUID
    relation: str
    outgoing: bool
    other_id: uuid.UUID
    other_label: str
    other_type_slug: str


class ReferencesOut(BaseModel):
    property_refs: list[RefHitOut]
    relations: list[RelationHitOut]
    hidden_property_refs: int
    """볼 수 없는 부서의 참조 수. **수만 말한다** — 안 말하면 「아무것도 안 걸렸다」 로
    읽는다."""
    hidden_relations: int
    total: int


class MergeRequest(BaseModel):
    into: uuid.UUID
    """이긴 쪽. 같은 타입이어야 한다."""


class MergeResultOut(BaseModel):
    into: uuid.UUID
    property_refs: int
    relations_moved: int
    relations_dropped: int
    """겹쳐서 버린 관계 — 두 겹으로 남기면 병합이 아니라 복제다."""


# --- 이력 -------------------------------------------------------------------


class SnapshotOut(BaseModel):
    """그 시점의 값 전체 — 지금 값에서 기록을 거꾸로 대어 재구성한 것."""

    key: str | None
    label: str
    status: str
    properties: dict[str, Any]


class HistoryEntryOut(BaseModel):
    id: uuid.UUID
    at: datetime
    actor_label: str
    action: str
    reason: str | None
    kind: str
    """`object` 는 값이 바뀐 기록, `relation` 은 관계가 걸리거나 끊긴 기록."""
    changes: dict[str, Any]
    """칸별 `{before, after}`. 속성은 `properties.<키>` 로 풀어서."""
    relation: dict[str, Any] | None
    snapshot: SnapshotOut | None
    """값 기록에만 있다. 되돌리기의 목표."""


class RestoreRequest(BaseModel):
    entry_id: uuid.UUID


# --- 저장된 뷰 --------------------------------------------------------------


class ConditionOut(BaseModel):
    field: str
    op: str
    value: str = ""


class SavedViewQuery(BaseModel):
    """뷰가 담는 것. 열·정렬은 안 담는다 — 그것은 타입 정의(`list_view`)의 몫이고,
    뷰마다 갈리면 「왜 이 뷰만 열이 다르지」 를 아무도 설명 못 한다."""

    q: str = ""
    conditions: list[ConditionOut] = Field(default_factory=list)
    status: str | None = None


class SavedViewSummary(BaseModel):
    """이 뷰를 **그림으로** 볼 때의 설정. 비어 있으면(group_by 가 없으면) 목록일 뿐이다.

    조건과 축은 같은 물음의 두 쪽이다(「영남 공급사를 등급별로」). 따로 두면 뷰를
    불러올 때마다 축을 다시 고르게 되고, 그 수고가 반복되면 사람은 CSV 로 내려받는다.
    """

    group_by: str = ""
    split_by: str = ""
    """두 번째 축. 있으면 계열이 여럿이 되고, 쌓은 막대·나란한 막대·히트맵이 뜻을 갖는다."""
    metric: str = "count"
    metric_field: str | None = None
    chart: str = "bar"
    """bar · line · area · pie · heatmap. 그림 모양까지 담는다 — 「원으로 보던 것」 이
    막대로 뜨면 같은 뷰로 안 읽힌다. `heatmap` 은 두 축일 때만 뜻이 있다."""

    stacked: bool = False
    """막대를 쌓을지. 여럿을 나란히 두면 「전체가 얼마인지」 를 못 읽는 물음이 있다."""
    order: str = "desc"
    """desc(많은 것부터) · asc(적은 것부터). 적은 것부터는 「가장 낮은 것」 을 찾을 때 쓴다."""


class SavedViewOut(BaseModel):
    id: uuid.UUID
    type_slug: str
    name: str
    query: SavedViewQuery
    owner_user_id: uuid.UUID
    owner_label: str
    workspace_slug: str | None
    """있으면 그 부서가 함께 쓴다. 없으면 내 것."""
    summary: SavedViewSummary
    home_order: int | None
    """부서 홈에 올린 자리. NULL 이면 홈에 없다."""
    can_edit: bool
    created_at: datetime
    updated_at: datetime


class SavedViewWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    query: SavedViewQuery
    workspace_slug: str | None = None
    """부서와 함께 쓸지. 그 부서의 관리자여야 한다."""
    summary: SavedViewSummary | None = None
    on_home: bool = False
    """저장하면서 **바로 부서 홈에 올릴지.**

    한 번에 받는 이유: 화면에서 「홈에 올리기」 는 한 동작인데 요청 둘로 나누면 저장은
    됐는데 안 올라간 상태가 생기고, 그때 사람은 자기가 무엇을 빠뜨렸는지 모른다.
    """


class SavedViewPatchRequest(BaseModel):
    """**안 보낸 것은 그대로다.** `home_order` 만 보내 홈에 올리거나 내린다."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    query: SavedViewQuery | None = None
    summary: SavedViewSummary | None = None
    on_home: bool | None = None
    """true 면 부서 홈 맨 끝에 올리고, false 면 내린다."""
    home_position: int | None = Field(default=None, ge=0)
    """부서 홈에서 몇 번째 자리로. 서버가 그 부서의 홈 뷰들을 **통째로 다시 매긴다** —
    화면이 자리를 제 손으로 매겨 여러 번 저장하면 중간 실패가 순서를 뒤섞는다."""


class HomeWidgetOut(BaseModel):
    """부서 홈에 올라간 뷰 하나. 홈 화면은 타입을 모르므로 **여기서 다 실어 준다.**"""

    view: SavedViewOut
    type_label: str
    icon: str


# --- 품질 -------------------------------------------------------------------


class QualityHitOut(BaseModel):
    id: uuid.UUID
    label: str
    key: str | None
    detail: str
    """왜 걸렸나."""


class QualityFindingOut(BaseModel):
    kind: str
    kind_label: str
    type_slug: str
    type_label: str
    count: int
    """전부 센 수. 아래 목록은 상한까지만."""
    hits: list[QualityHitOut]


class QualityReportOut(BaseModel):
    findings: list[QualityFindingOut]
    sample_limit: int


# --- 묶어 보기 ----------------------------------------------------------------


class GroupOptionOut(BaseModel):
    """묶을 수 있는(또는 셀 수 있는) 축 하나. 화면의 고르개가 이것만 보여 준다."""

    field: str
    label: str
    kind: str


class PartOut(BaseModel):
    """쪼갠 조각 하나 — 두 번째 축의 값별로. 합은 그 칸의 `count` 와 맞는다."""

    key: str | None
    label: str
    count: int
    value: float | None = None


class BucketOut(BaseModel):
    key: str | None
    """거르기에 그대로 넣을 수 있는 값. 빈 칸이면 null."""
    label: str
    count: int
    value: float | None = None
    parts: list[PartOut] = Field(default_factory=list)


class SummaryOut(BaseModel):
    """묶어 센 결과. **막대의 합이 total 과 다르면 그 차이가 「그 밖에」 다** —
    숨기면 사람은 그 차이를 오류로 읽는다."""

    group_field: str
    group_label: str
    order: str
    split_field: str
    split_label: str
    splits: list[str]
    """쪼갠 값들의 차례. 화면이 계열 순서를 여기서 가져간다 — 칸마다 나오는 대로
    만들면 첫 칸에 없던 값이 뒤에서 튀어나와 색이 밀린다."""
    other_splits: int
    metric: str
    metric_field: str | None
    metric_label: str
    total: int
    buckets: list[BucketOut]
    other_groups: int
    other_count: int
    group_options: list[GroupOptionOut]
    metric_options: list[GroupOptionOut]
