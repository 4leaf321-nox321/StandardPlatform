"""작업 API — 넣고, 보고, 적용하고, 취소하고, 받는다.

넣는 요청은 파일을 표에 저장하고 행 하나를 만들고 **202 로 곧장** 돌려준다. 그 뒤는 화면이
`GET /api/jobs/{id}` 를 2초마다 본다. 워커가 없으면 영영 `queued` 다 — 그래서
`GET /api/jobs/workers` 가 워커가 살아 있는지 말해 준다.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.jobs import kinds, services
from app.modules.jobs.models import Job, JobFile, WorkerHeartbeat
from app.modules.jobs.schemas import (
    JobListOut,
    JobOut,
    JobProgress,
    KindOut,
    WorkerOut,
)
from app.modules.workspaces.models import Workspace
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, NotFound, code
from app.shared.pagination import clamp_limit
from app.shared.permissions import resolve_owner_workspace

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _out(db: Session, row: Job) -> JobOut:
    spec = kinds.get(row.kind)
    who = db.get(User, row.requested_by_id) if row.requested_by_id else None
    workspace = db.get(Workspace, row.workspace_id) if row.workspace_id else None
    input_name = (
        db.scalar(select(JobFile.name).where(JobFile.id == row.input_file_id))
        if row.input_file_id
        else None
    )
    return JobOut(
        id=row.id,
        kind=row.kind,
        kind_label=spec.label if spec else row.kind,
        status=row.status,
        params={k: v for k, v in (row.params or {}).items() if k != "fingerprint"},
        progress=JobProgress(**(row.progress or {})),
        result=row.result,
        error=row.error,
        parent_id=row.parent_id,
        input_file_name=input_name,
        has_output=row.output_file_id is not None,
        requested_by_name=who.display_name if who else None,
        workspace_slug=workspace.slug if workspace else None,
        cancel_requested=row.cancel_requested,
        attempts=row.attempts,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def submit(
    db: Session,
    user: User,
    *,
    kind: str,
    params: dict[str, Any],
    upload: UploadFile | None,
    workspace_slug: str | None,
) -> Job:
    """작업 하나를 넣는다 — 객체 라우터의 가져오기 경로도 이것을 부른다.

    부서는 여기서 확정한다(`resolve_owner_workspace`) — 워커가 돌 때 거절되면 사람은 몇 분
    뒤에야 「권한이 없습니다」 를 본다. 넣는 순간에 거절해야 한다.
    """
    spec = kinds.get(kind)
    if spec is None:
        raise AppError(
            code("JOBS", 3),
            f"모르는 작업 종류입니다: {kind}. 되는 것: {', '.join(kinds.names())}",
            status=422,
        )
    workspace_id = (
        resolve_owner_workspace(
            db, user, workspace_slug, what="작업", code_value=code("JOBS", 15)
        )
        if spec.owns_workspace
        else None
    )
    stored: JobFile | None = None
    if upload is not None:
        stored = services.store_file(
            db,
            name=upload.filename or "upload.bin",
            content_type=upload.content_type or "application/octet-stream",
            data=upload.file.read(),
        )
    merged = (
        {**params, "workspace_slug": workspace_slug} if spec.owns_workspace else dict(params)
    )
    job = services.enqueue(
        db,
        kind=kind,
        params=merged,
        user=user,
        workspace_id=workspace_id,
        input_file=stored,
    )
    db.commit()
    db.refresh(job)
    return job


@router.get("/kinds", response_model=list[KindOut])
def list_kinds() -> list[KindOut]:
    return [
        KindOut(
            name=one.name, label=one.label, needs_file=one.needs_file, two_step=one.two_step
        )
        for one in kinds.all_kinds()
    ]


@router.get("/workers", response_model=list[WorkerOut])
def list_workers(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[WorkerOut]:
    """워커가 살아 있나. **없으면 작업은 영영 대기다** — 화면이 그것을 말해 줘야 한다."""
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().worker_stale_seconds)
    rows = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen.desc()))
    return [
        WorkerOut(
            worker_id=one.worker_id,
            host=one.host,
            pid=one.pid,
            started_at=one.started_at,
            last_seen=one.last_seen,
            current_job_id=one.current_job_id,
            alive=one.last_seen >= cutoff,
        )
        for one in rows
    ]


@router.post("", response_model=JobOut, status_code=202)
def create_job(
    kind: str = Form(),
    params: str = Form(default="{}", description="JSON"),
    workspace_slug: str | None = Form(default=None),
    upload: UploadFile | None = File(default=None, alias="file"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    try:
        parsed = json.loads(params or "{}")
    except ValueError:
        raise AppError(code("JOBS", 16), "params 는 JSON 이어야 합니다.", status=422) from None
    if not isinstance(parsed, dict):
        raise AppError(code("JOBS", 16), "params 는 JSON 객체여야 합니다.", status=422)
    job = submit(
        db, user, kind=kind, params=parsed, upload=upload, workspace_slug=workspace_slug
    )
    return _out(db, job)


@router.get("", response_model=JobListOut)
def list_jobs(
    status: str | None = Query(default=None),
    kind: str | None = Query(
        default=None, description="작업 종류 — `/api/jobs/kinds` 의 이름"
    ),
    mine: bool = Query(default=False, description="내가 시킨 것만"),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobListOut:
    capped = clamp_limit(limit)
    rows, total = services.list_visible(
        db, user, limit=capped, offset=offset, status=status, kind=kind, mine=mine
    )
    return JobListOut(items=[_out(db, one) for one in rows], total=total)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> JobOut:
    return _out(db, services.get_visible(db, user, job_id))


@router.post("/{job_id}/apply", response_model=JobOut, status_code=202)
def apply_job(
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """계획을 본 뒤 **사람이 누르는 자리.** 같은 파일 · 같은 지문으로 적용 작업을 만든다."""
    plan_job = services.get_visible(db, user, job_id)
    needed = (plan_job.params or {}).get("needs_scope")
    granted: list[str] | None = getattr(request.state, "token_scopes", None)
    if needed and granted is not None and needed not in granted:
        raise AppError(
            code("JOBS", 18),
            f"이 토큰에는 {needed} 범위가 없습니다 — 이 작업을 적용하려면 필요합니다.",
            status=403,
            details={"needed": needed, "granted": granted},
        )
    job = services.make_apply(db, user, plan_job)
    db.commit()
    db.refresh(job)
    return _out(db, job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> JobOut:
    job = services.request_cancel(db, user, services.get_visible(db, user, job_id))
    db.commit()
    db.refresh(job)
    return _out(db, job)


@router.get("/{job_id}/download")
def download_output(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    job = services.get_visible(db, user, job_id)
    if job.output_file_id is None:
        raise NotFound(code("JOBS", 17), "이 작업에는 결과 파일이 없습니다.")
    stored = db.get(JobFile, job.output_file_id)
    if stored is None:
        raise NotFound(code("JOBS", 13), "결과 파일이 이미 지워졌습니다.")
    return Response(
        content=stored.data,
        media_type=stored.content_type,
        headers={"Content-Disposition": f'attachment; filename="{stored.name}"'},
    )


@router.post("/maintenance/purge", status_code=200)
def purge_files(
    user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, int]:
    """기한 지난 작업 파일과 **끝난 작업 기록** 지우기 — 워커도 한 시간마다 한다.
    손으로 당길 때."""
    return {
        "files": services.purge_expired_files(db),
        "jobs": services.purge_old_jobs(db),
    }
