"""작업 파일의 저장 계층 — **여기 하나만 바꾸면 저장 위치가 바뀐다.**

지금은 DB(`job_files.data`)다. 서버 두 대가 같이 읽어야 하는데 공용 폴더가 아직 없어서다.
공용 폴더가 생기면 `data` 대신 경로를 두고 이 파일의 두 함수만 고친다
(`docs/작업-워커-설계.md` 3장).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.bundles.models import BundleRun, BundleUndoEntry
from app.modules.jobs.models import Job, JobFile
from app.modules.objects.models import ObjectRelationTombstone
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


def purge_old_tombstones(db: Session) -> int:
    """기한 지난 **끊긴 선의 무덤**을 지운다.

    무덤은 영영 쌓이고 내보내기는 그 이력을 통째로 싣는다 — 몇 해가 지나면 봉투의 대부분이
    「없어진 것」 이 된다. 받는 쪽이 이 기간보다 오래 잠들어 있었다면 무덤으로 따라잡을 게
    아니라 **처음부터 다시 받아야** 한다.
    """
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().tombstone_ttl_days)
    gone = db.execute(
        delete(ObjectRelationTombstone)
        .where(ObjectRelationTombstone.removed_at < cutoff)
        .returning(ObjectRelationTombstone.id)
    )
    count = len(gone.all())
    db.commit()
    return count


def purge_old_undo_journals(db: Session) -> int:
    """기한 지난 **되돌릴 기록**을 지운다 — 판은 남기고 줄만.

    바뀐 줄마다 한 줄이라 백필 한 번이 수만 줄을 남긴다. 판을 함께 지우면 「그때 무엇이
    들어갔나」 까지 사라진다 — 그것은 남기고, 되돌릴 수 없다는 사실만 목록에서 드러나게
    한다(줄이 0개면 「되돌릴 수 없음」).
    """
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().undo_journal_ttl_days)
    old_runs = select(BundleRun.id).where(BundleRun.at < cutoff)
    gone = db.execute(
        delete(BundleUndoEntry)
        .where(BundleUndoEntry.run_id.in_(old_runs))
        .returning(BundleUndoEntry.id)
    )
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
