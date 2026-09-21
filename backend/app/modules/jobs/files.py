"""작업 파일의 저장 계층 — **여기 하나만 바꾸면 저장 위치가 바뀐다.**

지금은 DB(`job_files.data`)다. 서버 두 대가 같이 읽어야 하는데 공용 폴더가 아직 없어서다.
공용 폴더가 생기면 `data` 대신 경로를 두고 이 파일의 두 함수만 고친다
(`docs/작업-워커-설계.md` 3장).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.jobs.models import Job, JobFile
from app.shared.errors import AppError, code


def store(db: Session, *, name: str, content_type: str, data: bytes) -> JobFile:
    settings = get_settings()
    if len(data) > settings.job_file_max_bytes:
        limit = settings.job_file_max_bytes // (1024 * 1024)
        raise AppError(
            code("JOBS", 1),
            f"파일이 너무 큽니다({len(data) // (1024 * 1024)}MB). 한 번에 {limit}MB 까지 — "
            "나눠 올리세요.",
            status=413,
        )
    if not data:
        raise AppError(code("JOBS", 2), "빈 파일입니다.", status=422)
    row = JobFile(
        name=name[:255],
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        expires_at=datetime.now(UTC) + timedelta(days=settings.job_file_ttl_days),
    )
    db.add(row)
    db.flush()
    return row


def purge_expired(db: Session) -> int:
    """기한 지난 파일을 지운다. 작업 행의 참조는 SET NULL — 작업 기록은 남고 파일만 없다."""
    now = datetime.now(UTC)
    gone = db.execute(delete(JobFile).where(JobFile.expires_at < now).returning(JobFile.id))
    count = len(gone.all())
    db.commit()
    return count


def purge_old_jobs(db: Session) -> int:
    """기한 지난 **끝난** 작업 기록을 지운다.

    도는 것은 아무리 오래돼도 안 지운다 — 멎은 작업은 워커가 되살릴 몫이고, 여기서 지우면
    그 일이 있었다는 사실까지 사라진다. 적용 작업이 가리키던 계획 작업이 먼저 지워져도
    `parent_id` 는 SET NULL 이라 남는다.
    """
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().job_ttl_days)
    gone = db.execute(
        delete(Job)
        .where(Job.status.in_(("done", "failed", "cancelled")), Job.finished_at < cutoff)
        .returning(Job.id)
    )
    count = len(gone.all())
    db.commit()
    return count
