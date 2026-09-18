"""메타모델 API 형태.

**슬러그·키의 규칙은 여기 적지 않는다.** `services.require_slug` 가 정본이다 —
pydantic 패턴에도 적으면 두 벌이 되고, 그때 한쪽만 고쳐지면 「화면에서는 되는데
저장이 안 되는」 값이 생긴다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NavGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    label: str
    icon: str
    audience: str
    sort_order: int
    is_active: bool


class NavGroupWriteRequest(BaseModel):
    slug: str
    label: str = Field(min_length=1, max_length=64)
    icon: str = Field(default="", max_length=40)
    audience: str = "everyone"
    sort_order: int = 0
    is_active: bool = True


class PropertyDefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_kind: str
    owner_id: uuid.UUID
    key: str
    label: str
    data_type: str
    unit: str
    help: str
    required: bool
    multi: bool
    enum_options: list[str] | None
    ref_type_slug: str | None
    inverse_label: str = ""
    """`object_ref` 를 상대 쪽에서 읽는 말 — 「과제」 칸의 역은 「개발모델」."""
    min_value: float | None
    max_value: float | None
    decimals: int | None
    pattern: str | None
    default_value: Any | None
    unique: bool
    section: str
    sort_order: int


class PropertyDefWriteRequest(BaseModel):
    key: str
    label: str = Field(min_length=1, max_length=64)
    data_type: str
    unit: str = Field(default="", max_length=24)
    help: str = Field(default="", max_length=200)
    required: bool = False
    multi: bool = False
    enum_options: list[str] | None = None
    ref_type_slug: str | None = None
    inverse_label: str = Field(default="", max_length=64)
    min_value: float | None = None
    max_value: float | None = None
    decimals: int | None = Field(default=None, ge=0, le=10)
    pattern: str | None = Field(default=None, max_length=200)
    default_value: Any | None = None
    unique: bool = False
    section: str = Field(default="", max_length=48)
    sort_order: int = 0


class ObjectTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    label: str
    icon: str
    description: str
    sort_order: int
    nav_group_id: uuid.UUID | None
    nav_group_slug: str | None
    kind_class: str
    system_source: str
    """`kind_class='system'` 이면 어느 원 표를 비추는가(`workspace` · `user` …).
    아니면 빈 값."""
    entry_policy: str
    managed_by: str = ""
    """빈 값이면 이 설치의 정의, `hub` 면 허브가 내려준 것 — 화면은 고치는 단추를 감춘다."""
    parent_slug: str | None = None
    """상위 타입 — RDF/OWL 의 rdfs:subClassOf. 화면 동작은 바꾸지 않는다."""
    key_policy: str
    key_scope: str
    temporal_kind: str
    list_view: dict[str, Any]
    form_view: dict[str, Any]
    detail_view: dict[str, Any]
    title_template: str
    is_active: bool
    object_count: int
    """이 타입의 객체가 몇 개인가. **지우기 전에 무엇이 걸렸는지 알아야 한다** —
    그 물음에 답할 자리가 목록 자체여야 한다."""


class ObjectTypeWriteRequest(BaseModel):
    slug: str
    label: str = Field(min_length=1, max_length=64)
    icon: str = Field(default="", max_length=40)
    description: str = ""
    sort_order: int = 0
    nav_group_slug: str | None = None
    """NULL 이면 사이드바에 안 선다. 어휘 축은 대개 그렇다."""
    parent_slug: str | None = None
    kind_class: str = "record"
    system_source: str = ""
    entry_policy: str = "open"
    key_policy: str = "none"
    key_scope: str = "global"
    temporal_kind: str = "evergreen"
    list_view: dict[str, Any] = Field(default_factory=dict)
    form_view: dict[str, Any] = Field(default_factory=dict)
    detail_view: dict[str, Any] = Field(default_factory=dict)
    title_template: str = ""
    is_active: bool = True


class ObjectTypePatchRequest(BaseModel):
    """타입 부분 수정 — **「안 보낸 것」 과 「비운 것」 을 구별한다.**

    전체 교체로 두면 화면이 `list_view` 를 안 실어 보낸 날 그 설정이 통째로
    날아가고, **그 손실은 저장한 사람 눈에 안 보인다.** 무엇을 보냈는지는
    `model_fields_set` 이 안다 — `None` 을 기본값으로 둔 것과 명시적으로 `null`
    을 보낸 것이 그래야 갈린다(묶음에서 빼기가 그 경우다).
    """

    label: str | None = Field(default=None, min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=40)
    description: str | None = None
    sort_order: int | None = None
    nav_group_slug: str | None = None
    """`null` 을 명시하면 사이드바에서 뺀다. 안 보내면 그대로 둔다."""
    parent_slug: str | None = None
    """상위 타입. `null` 을 명시하면 뗀다."""
    kind_class: str | None = None
    system_source: str | None = None
    entry_policy: str | None = None
    key_policy: str | None = None
    key_scope: str | None = None
    temporal_kind: str | None = None
    list_view: dict[str, Any] | None = None
    form_view: dict[str, Any] | None = None
    detail_view: dict[str, Any] | None = None
    title_template: str | None = None
    is_active: bool | None = None


class NavGroupPatchRequest(BaseModel):
    """묶음 부분 수정. 같은 이유로 전체 교체가 아니다."""

    label: str | None = Field(default=None, min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=40)
    audience: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class RelationTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    label: str
    inverse_label: str
    description: str
    directed: bool
    transitive: bool
    acyclic: bool
    cardinality: str
    src_type_slugs: list[str] | None
    dst_type_slugs: list[str] | None
    managed_by: str = ""
    sort_order: int
    is_active: bool


class RelationTypeWriteRequest(BaseModel):
    slug: str
    label: str = Field(min_length=1, max_length=64)
    inverse_label: str = Field(default="", max_length=64)
    description: str = ""
    directed: bool = True
    transitive: bool = False
    acyclic: bool = False
    cardinality: str = "many_to_many"
    src_type_slugs: list[str] | None = None
    dst_type_slugs: list[str] | None = None
    sort_order: int = 0
    is_active: bool = True


class RelationTypePatchRequest(BaseModel):
    """**보낸 것만 바꾼다.** 허용 타입을 비우려면 `null` 을 명시한다."""

    label: str | None = Field(default=None, min_length=1, max_length=64)
    inverse_label: str | None = Field(default=None, max_length=64)
    description: str | None = None
    directed: bool | None = None
    transitive: bool | None = None
    acyclic: bool | None = None
    cardinality: str | None = None
    src_type_slugs: list[str] | None = None
    dst_type_slugs: list[str] | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class ObjectTypeSchema(ObjectTypeOut):
    """타입 하나 + 그 속성 정의 전부. 스키마 응답의 원소."""

    properties: list[PropertyDefOut]


class ReferenceEdgeOut(BaseModel):
    """참조 칸을 관계처럼 읽은 것 — **길 목록이 한 벌**이 되게. AI 와 그래프가 관계 종류와 함께
    본다. slug 는 `ref:<타입>.<칸>`, 조건 · 통계에서는 `ref.<칸>.…` 로 건넌다."""

    slug: str
    label: str
    inverse_label: str
    src_type_slug: str
    dst_type_slug: str
    field_key: str
    multi: bool


class OntologySchemaOut(BaseModel):
    """**자기 설명적 스키마** — MCP 의 입력.

    이 하나를 읽으면 도구를 동적으로 만들 수 있다. 도메인마다 MCP 서버를 새로
    짤 필요가 없는 이유가 이것이다.
    """

    groups: list[NavGroupOut]
    types: list[ObjectTypeSchema]
    relation_types: list[RelationTypeOut]
    reference_edges: list[ReferenceEdgeOut] = Field(default_factory=list)
    """참조 칸(`object_ref`)을 관계 모양으로 — 타입 사이의 길은 이것과 `relation_types` 를 합친
    것이다."""
    data_types: list[str]
    system_sources: list[SystemSourceOut] = Field(default_factory=list)
    """투영(system) 타입이 비출 수 있는 원 표들. 이 설치가 등록한 것만."""
    generated_at: datetime


class SystemSourceOut(BaseModel):
    key: str
    label: str


class NavGroupNode(BaseModel):
    """동적 사이드바 한 묶음."""

    slug: str
    label: str
    icon: str
    audience: str
    items: list[dict[str, str]]


class PropertyUsage(BaseModel):
    """**지우기 전에 무엇이 사라지는지.**

    "정말 삭제하시겠습니까" 만 묻는 창은 아무도 안 읽고 예를 누른다 — 읽을 것이
    없어서다. 속성 하나를 지우면 그 값이 전부 사라지므로, 몇 개인지 말해 준다.
    """

    key: str
    label: str
    objects_with_value: int


class ChangeOut(BaseModel):
    kind: str
    slug: str
    action: str
    fields: list[str]


class ImportPlanOut(BaseModel):
    """가져오기가 무엇을 할 것인가 — **적용 전에 보는 것.**

    이것이 없으면 에이전트의 실수가 **기계 속도로** 반영되고, 온톨로지는 데이터의
    모양이라 그 아래 쌓인 것이 전부 흔들린다.
    """

    applied: bool
    """실제로 적용했나. `dry_run` 이면 거짓."""
    changes: list[ChangeOut]
    warnings: list[str]
    """**적용은 되지만 조용히 무언가를 잃는 것.** 사람이 읽고 판단할 자리다."""
    errors: list[str]
    """적용하면 실패할 것. **하나라도 있으면 아무것도 안 바꾼다.**"""
    snapshot_id: uuid.UUID | None
    """적용 직전에 남긴 스냅샷. 되돌릴 때 쓴다."""


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    taken_at: datetime
    actor_label: str
    reason: str
    type_count: int
    relation_count: int


# --- 코드표 -------------------------------------------------------------------


class RenameOptionRequest(BaseModel):
    from_value: str = Field(alias="from", min_length=1)
    to_value: str = Field(alias="to", min_length=1)
    apply: bool = False

    model_config = ConfigDict(populate_by_name=True)


class RenameOptionOut(BaseModel):
    applied: bool
    from_value: str
    to_value: str
    objects_with_value: int
    """함께 바뀌는(바뀐) 저장값의 수."""
    errors: list[str]


class PromoteRequest(BaseModel):
    """있는 코드표에 붙이거나(`target_type_slug`), 새로 만든다(`new_slug`·`new_label`)."""

    target_type_slug: str | None = None
    new_slug: str | None = None
    new_label: str | None = None
    nav_group_slug: str | None = None
    apply: bool = False


class PromoteOptionOut(BaseModel):
    value: str
    action: str
    """`create`(코드표에 새로 만듦) · `reuse`(이미 있는 객체에 붙임)."""
    object_id: uuid.UUID | None
    objects_with_value: int


class PromoteOut(BaseModel):
    applied: bool
    target_slug: str
    target_label: str
    target_new: bool
    options: list[PromoteOptionOut]
    errors: list[str]
    warnings: list[str]
    snapshot_id: uuid.UUID | None


# --- 표에서 타입 추론 ---------------------------------------------------------


class InferColumnOut(BaseModel):
    header: str
    role: str
    """label · key · description · aliases · property · ignore."""
    key: str
    label: str
    data_type: str
    multi: bool
    enum_options: list[str]
    decimals: int | None
    filled: int
    distinct: int
    samples: list[str]
    note: str


class InferOut(BaseModel):
    rows: int
    columns: list[InferColumnOut]
    raw_rows: list[dict[str, Any]]
    """읽은 행 그대로 — 화면이 역할·종류를 고친 뒤 `build` 로 다시 보낸다."""


class InferBuildRequest(BaseModel):
    """사람이 고친 열 정의 + 행 → 정의 스키마와 가져올 행."""

    slug: str
    label: str = Field(min_length=1, max_length=64)
    nav_group_slug: str | None = None
    key_policy: str = "optional"
    columns: list[InferColumnOut]
    raw_rows: list[dict[str, Any]]


class InferBuildOut(BaseModel):
    schema_: dict[str, Any] = Field(alias="schema")
    """`POST /ontology/import` 에 그대로 보낼 것."""
    import_rows: list[dict[str, Any]]
    """`POST /objects/{slug}/import-rows` 에 그대로 보낼 것."""

    model_config = ConfigDict(populate_by_name=True)


# --- 통째로 비우기 ------------------------------------------------------------


class ResetItemOut(BaseModel):
    """사라질 것 한 줄. **0 건도 나간다** — 여기서는 「없다」 는 답도 정보다."""

    table: str
    label: str
    count: int


class ResetPlanOut(BaseModel):
    """비우기 계획. `applied=false` 면 아직 아무것도 안 지웠다."""

    applied: bool
    items: list[ResetItemOut]
    total: int
    confirm_phrase: str
    """적용하려면 이 문구를 그대로 보내야 한다 — 실수로 누르는 것과 작정하고 하는 것
    사이에 글자 몇 개를 둔다."""
    snapshot_id: uuid.UUID | None = None
    """비우기 직전에 남긴 정의. **정의는 여기서 되돌린다 — 데이터는 안 돌아온다.**"""


class ResetRequest(BaseModel):
    apply: bool = False
    confirm: str = ""
