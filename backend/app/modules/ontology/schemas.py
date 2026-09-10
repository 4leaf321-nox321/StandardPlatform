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
    entry_policy: str
    key_policy: str
    key_scope: str
    temporal_kind: str
    list_view: dict[str, Any]
    is_active: bool
    object_count: int
    """이 타입의 인스턴스가 몇 개인가. **지우기 전에 무엇이 걸렸는지 알아야 한다** —
    그 물음에 답할 자리가 목록 자체여야 한다."""


class ObjectTypeWriteRequest(BaseModel):
    slug: str
    label: str = Field(min_length=1, max_length=64)
    icon: str = Field(default="", max_length=40)
    description: str = ""
    sort_order: int = 0
    nav_group_slug: str | None = None
    """NULL 이면 사이드바에 안 선다. 어휘 축은 대개 그렇다."""
    kind_class: str = "record"
    entry_policy: str = "open"
    key_policy: str = "none"
    key_scope: str = "global"
    temporal_kind: str = "evergreen"
    list_view: dict[str, Any] = Field(default_factory=dict)
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
    kind_class: str | None = None
    entry_policy: str | None = None
    key_policy: str | None = None
    key_scope: str | None = None
    temporal_kind: str | None = None
    list_view: dict[str, Any] | None = None
    is_active: bool | None = None


class NavGroupPatchRequest(BaseModel):
    """묶음 부분 수정. 같은 이유로 전체 교체가 아니다."""

    label: str | None = Field(default=None, min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=40)
    audience: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class ObjectTypeSchema(ObjectTypeOut):
    """타입 하나 + 그 속성 정의 전부. 스키마 응답의 원소."""

    properties: list[PropertyDefOut]


class OntologySchemaOut(BaseModel):
    """**자기 설명적 스키마** — MCP 의 입력.

    이 하나를 읽으면 도구를 동적으로 만들 수 있다. 도메인마다 MCP 서버를 새로
    짤 필요가 없는 이유가 이것이다.
    """

    groups: list[NavGroupOut]
    types: list[ObjectTypeSchema]
    data_types: list[str]
    generated_at: datetime


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
