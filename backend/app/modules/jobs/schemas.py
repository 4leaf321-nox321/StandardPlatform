"""작업 API 형태."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobProgress(BaseModel):
    stage: str = ""
    done: int = 0
    total: int = 0


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    kind_label: str
    status: str
    """`queued` · `running` · `done` · `failed` · `cancelled`."""
    params: dict[str, Any]
    progress: JobProgress
    result: dict[str, Any] | None
    """끝난 뒤의 결과 — 가져오기면 계획 표(`ImportPlanOut` 모양 + `fingerprint`)."""
    error: str | None
    parent_id: uuid.UUID | None
    input_file_name: str | None
    has_output: bool
    requested_by_name: str | None
    workspace_slug: str | None
    cancel_requested: bool
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int


class KindOut(BaseModel):
    name: str
    label: str
    needs_file: bool
    two_step: bool


class WorkerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    worker_id: str
    host: str
    pid: int
    started_at: datetime
    last_seen: datetime
    current_job_id: uuid.UUID | None
    alive: bool = Field(description="심장박동이 최근인가")
