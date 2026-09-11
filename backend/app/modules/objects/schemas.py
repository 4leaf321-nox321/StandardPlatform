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


class SavedViewOut(BaseModel):
    id: uuid.UUID
    type_slug: str
    name: str
    query: SavedViewQuery
    owner_user_id: uuid.UUID
    owner_label: str
    workspace_slug: str | None
    """있으면 그 부서가 함께 쓴다. 없으면 내 것."""
    can_edit: bool
    created_at: datetime
    updated_at: datetime


class SavedViewWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    query: SavedViewQuery
    workspace_slug: str | None = None
    """부서와 함께 쓸지. 그 부서의 관리자여야 한다."""


class SavedViewPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    query: SavedViewQuery | None = None


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
