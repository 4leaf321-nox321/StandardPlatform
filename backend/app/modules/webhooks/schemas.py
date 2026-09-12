from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    has_secret: bool
    """비밀은 다시 보여 주지 않는다 — 있는지만."""
    events: list[str]
    type_slugs: list[str] | None
    is_active: bool
    last_status: str | None
    last_at: datetime | None
    created_at: datetime


class WebhookWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=500)
    secret: str = Field(default="", max_length=200)
    events: list[str] = Field(min_length=1)
    """`object.*` · `object.relation.add` · `*`. 비우면 아무것도 안 보내므로 하나는
    있어야 한다."""
    type_slugs: list[str] | None = None
    is_active: bool = True


class WebhookPatchRequest(BaseModel):
    """**보낸 것만 바꾼다.** `secret` 을 보내면 갈고, 안 보내면 그대로."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    secret: str | None = Field(default=None, max_length=200)
    events: list[str] | None = Field(default=None, min_length=1)
    type_slugs: list[str] | None = None
    is_active: bool | None = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event: str
    status: str
    attempts: int
    response_code: int | None
    last_error: str | None
    payload: dict[str, Any]
    created_at: datetime
    delivered_at: datetime | None
