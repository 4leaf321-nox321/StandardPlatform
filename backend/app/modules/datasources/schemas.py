from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.objects.schemas import ImportRowOut


class DataSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    kind: str
    base_url: str
    entity_set: str
    options: dict[str, Any]
    filter: str
    select: str
    auth_kind: str
    auth_user: str
    has_secret: bool
    """비밀은 다시 보여 주지 않는다 — 있는지만."""
    page_size: int
    type_slug: str
    workspace_slug: str | None
    mapping: dict[str, Any]
    deprecate_missing: bool
    since_mark: str = ""
    """`sp_core` 가 지난번에 어디까지 받았나 — 비우면 다음 동기화가 처음부터 받는다."""
    interval_minutes: int
    is_active: bool
    last_run_at: datetime | None
    last_status: str | None
    created_at: datetime


class DataSourceWriteRequest(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=100)
    kind: str = "odata"
    base_url: str = Field(default="", max_length=500)
    entity_set: str = Field(min_length=1, max_length=500)
    options: dict[str, Any] = Field(default_factory=dict)
    filter: str = ""
    select: str = ""
    auth_kind: str = "none"
    auth_user: str = ""
    auth_secret: str = ""
    page_size: int = Field(default=500, ge=1, le=5000)
    type_slug: str
    workspace_slug: str | None = None
    mapping: dict[str, Any] = Field(default_factory=dict)
    deprecate_missing: bool = False
    interval_minutes: int = Field(default=0, ge=0, le=60 * 24 * 30)
    is_active: bool = True


class DataSourcePatchRequest(BaseModel):
    """**보낸 것만 바꾼다.** `auth_secret` 을 보내면 갈고, 안 보내면 그대로. slug 는 안 바꾼다
    — 별칭 `source:<slug>` 가 거기 물려 있다."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: str | None = None
    base_url: str | None = Field(default=None, max_length=500)
    entity_set: str | None = Field(default=None, min_length=1, max_length=500)
    options: dict[str, Any] | None = None
    filter: str | None = None
    select: str | None = None
    auth_kind: str | None = None
    auth_user: str | None = None
    auth_secret: str | None = None
    page_size: int | None = Field(default=None, ge=1, le=5000)
    type_slug: str | None = None
    workspace_slug: str | None = None
    mapping: dict[str, Any] | None = None
    deprecate_missing: bool | None = None
    since_mark: str | None = Field(default=None, max_length=64)
    """빈 문자열을 보내면 **처음부터 다시** 받는다 — 상대를 갈아엎었거나 대응을 크게
    고쳤을 때. 그 밖에는 손대지 않는다(동기화가 스스로 옮긴다)."""
    interval_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 30)
    is_active: bool | None = None


class CoreSuggestProperty(BaseModel):
    """상대의 칸 하나 — 우리 것과 어떻게 이어졌나."""

    key: str
    label: str
    data_type: str
    target: str | None = None
    """이어진 자리(`properties.<키>`). 못 이었으면 None 이고 `note` 가 이유를 적는다."""
    note: str = ""


class CoreSuggestOut(BaseModel):
    """상대의 카탈로그를 읽어 만든 **대응 초안.**

    사람이 옮겨 적지 않게 하는 자리다 — 옮겨 적으면 상대가 칸을 하나 더하는 날 그 문서가
    틀린 것이 된다. **저장은 사람이 한다**(이 응답은 제안일 뿐이다).
    """

    system: str
    revision: str
    type_slug: str
    type_label: str
    count: int
    mapping: dict[str, Any]
    properties: list[CoreSuggestProperty]
    notes: list[str]
    """이어지지 않은 것과 그 까닭 — 「우리 타입에 없는 칸」 이 여기 선다."""


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    applied: bool
    actor_label: str
    rows_seen: int
    counts: dict[str, int]
    errors: list[Any]
    started_at: datetime
    finished_at: datetime | None


class SyncOut(BaseModel):
    """계획(또는 적용) 결과 — 일괄 입력의 계획과 같은 모양에 기록이 붙는다."""

    run: RunOut
    applied: bool
    counts: dict[str, int]
    rows: list[ImportRowOut]
    errors: list[Any]
    truncated: bool


class PreviewOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    mapped: list[dict[str, Any]]
    mapping_error: str | None
