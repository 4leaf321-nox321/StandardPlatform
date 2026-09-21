"""작업의 생애 — 넣기 · 집기 · 진행 · 끝내기 · 되살리기.

두 종류의 세션이 있다. **요청의 세션**(넣기 · 보기 · 취소 요청)과 **워커의 세션**(집기 ·
돌리기). 그리고 진행률과 심장박동은 **따로 짧은 세션**을 연다 — 본 트랜잭션이 열려 있는
동안 그 세션으로 쓰면 커밋할 때까지 아무도 못 보고, 커밋하면 「전부 아니면 무」 가 깨진다.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.jobs import files, kinds
from app.modules.jobs.models import Job, JobFile, WorkerHeartbeat
from app.modules.notifications import services as notifications
from app.modules.workspaces.models import WorkspaceMember
from app.shared import extensions
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code

log = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled")


class Cancelled(Exception):
    """워커가 단계 사이에서 취소 요청을 봤다 — 롤백하고 `cancelled` 로."""


# --- 파일 — 저장 계층은 files.py --------------------------------------------------


def store_file(db: Session, *, name: str, content_type: str, data: bytes) -> JobFile:
    return files.store(db, name=name, content_type=content_type, data=data)


def purge_expired_files(db: Session) -> int:
    return files.purge_expired(db)


def purge_old_jobs(db: Session) -> int:
    return files.purge_old_jobs(db)


# --- 넣기 · 보기 ----------------------------------------------------------------


def enqueue(
    db: Session,
    *,
    kind: str,
    params: dict[str, Any],
    user: User | None,
    workspace_id: uuid.UUID | None,
    input_file: JobFile | None = None,
    parent: Job | None = None,
) -> Job:
    """작업 한 줄. **부르는 쪽이 커밋한다.** `user` 가 None 인 것은 타이머가 넣는 종류
    (`allow_system`)뿐이다."""
    spec = kinds.get(kind)
    if spec is None:
        raise AppError(
            code("JOBS", 3),
            f"모르는 작업 종류입니다: {kind}. 되는 것: {', '.join(kinds.names())}",
            status=422,
        )
    if spec.needs_file and input_file is None:
        raise AppError(code("JOBS", 4), f"{spec.label}에는 파일이 필요합니다.", status=422)
    if user is None and not spec.allow_system:
        raise AppError(
            code("JOBS", 14), f"{spec.label}은(는) 시킨 사람이 있어야 합니다.", status=422
        )
    row = Job(
        kind=kind,
        status="queued",
        params=params,
        input_file_id=input_file.id if input_file is not None else None,
        parent_id=parent.id if parent is not None else None,
        requested_by_id=user.id if user is not None else None,
        workspace_id=workspace_id,
        progress={},
    )
    db.add(row)
    db.flush()
    return row


def _visible_clause(user: User) -> Any:
    """내 것 + 내 부서 것. 시스템 관리자는 전부.

    작업의 결과에는 계획 표(객체 이름 · 값)가 든다 — 남의 부서 것이 보이면 그 부서의
    데이터가 새는 것이다.
    """
    if user.is_system_admin:
        return Job.id.isnot(None)
    mine = select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    return or_(Job.requested_by_id == user.id, Job.workspace_id.in_(mine))


def list_visible(
    db: Session,
    user: User,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    kind: str | None = None,
    mine: bool = False,
) -> tuple[list[Job], int]:
    """내 것 + 내 부서 것. `kind` · `mine` 은 **타이머가 넣은 것에 파묻히지 않으려고** 있다 —
    소스마다 5분에 한 줄이면 하루 288행이고, 사람이 올린 작업은 그 사이에 한 줄이다."""
    stmt = select(Job).where(_visible_clause(user))
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    if mine:
        stmt = stmt.where(Job.requested_by_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit).offset(offset)))
    return rows, int(total)


def get_visible(db: Session, user: User, job_id: uuid.UUID) -> Job:
    row = db.scalar(select(Job).where(Job.id == job_id, _visible_clause(user)))
    if row is None:
        raise NotFound(code("JOBS", 5), "작업을 찾을 수 없습니다.")
    return row


def request_cancel(db: Session, user: User, job: Job) -> Job:
    """`queued` 면 바로 끝, `running` 이면 표시만 — 워커가 단계 사이에서 본다."""
    if not user.is_system_admin and job.requested_by_id != user.id:
        raise Forbidden(code("JOBS", 6), "시킨 사람만 취소할 수 있습니다.")
    if job.status in TERMINAL:
        raise Conflict(code("JOBS", 7), "이미 끝난 작업입니다.")
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
    else:
        job.cancel_requested = True
    db.flush()
    return job


def make_apply(db: Session, user: User, plan_job: Job) -> Job:
    """계획 작업 → 적용 작업. **같은 파일 · 같은 지문.**"""
    spec = kinds.get(plan_job.kind)
    if spec is None or not spec.two_step:
        raise Conflict(code("JOBS", 8), "이 종류의 작업은 적용 단계가 없습니다.")
    if plan_job.status != "done" or not plan_job.result:
        raise Conflict(code("JOBS", 9), "계획이 끝난 작업만 적용할 수 있습니다.")
    if plan_job.params.get("apply"):
        raise Conflict(code("JOBS", 10), "이미 적용 작업입니다.")
    result = plan_job.result
    if result.get("errors") or (result.get("counts") or {}).get("error"):
        raise Conflict(
            code("JOBS", 11), "오류가 있는 계획은 적용하지 않습니다. 고쳐서 다시 올리세요."
        )
    if not user.is_system_admin and plan_job.requested_by_id != user.id:
        raise Forbidden(code("JOBS", 12), "계획을 본 사람이 적용합니다.")
    if plan_job.input_file_id is None:
        raise Conflict(code("JOBS", 13), "파일이 이미 지워졌습니다. 다시 올리세요.")
    input_file = db.get(JobFile, plan_job.input_file_id)
    params = {**plan_job.params, "apply": True, "fingerprint": result.get("fingerprint")}
    return enqueue(
        db,
        kind=plan_job.kind,
        params=params,
        user=user,
        workspace_id=plan_job.workspace_id,
        input_file=input_file,
        parent=plan_job,
    )


# --- 워커 쪽 --------------------------------------------------------------------


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def heartbeat(worker_id: str, job_id: uuid.UUID | None) -> None:
    """짧은 세션으로 — 본 트랜잭션과 섞이면 안 된다."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        row = db.get(WorkerHeartbeat, worker_id)
        if row is None:
            db.add(
                WorkerHeartbeat(
                    worker_id=worker_id,
                    host=socket.gethostname(),
                    pid=os.getpid(),
                    last_seen=now,
                    current_job_id=job_id,
                )
            )
        else:
            row.last_seen = now
            row.current_job_id = job_id
        if job_id is not None:
            db.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=now))
        db.commit()


def report_progress(job_id: uuid.UUID, stage: str, done: int, total: int) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                progress={"stage": stage, "done": done, "total": total},
                heartbeat_at=datetime.now(UTC),
            )
        )
        db.commit()


def cancel_requested(job_id: uuid.UUID) -> bool:
    with SessionLocal() as db:
        return bool(db.scalar(select(Job.cancel_requested).where(Job.id == job_id)))


def claim(db: Session, worker_id: str) -> Job | None:
    """`queued` 하나를 **집는다** — `FOR UPDATE SKIP LOCKED` 라 두 워커가 같은 행을 안 집는다.
    커밋까지 여기서 한다."""
    row = db.scalar(
        select(Job)
        .where(Job.status == "queued")
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        db.rollback()
        return None
    now = datetime.now(UTC)
    row.status = "running"
    row.worker_id = worker_id
    row.attempts = (row.attempts or 0) + 1
    row.started_at = now
    row.heartbeat_at = now
    row.progress = {"stage": "시작", "done": 0, "total": 0}
    db.commit()
    return row


def recover_stale(db: Session) -> int:
    """심장박동이 멎은 `running` 을 되돌린다. 시도가 다했으면 `failed`.

    워커가 죽으면 그 작업은 영영 `running` 으로 남고, 화면은 「진행 중」 을 보여 준다 —
    아무도 다시 안 돌린다. 그래서 **다른 워커가** 그것을 본다.
    """
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.worker_stale_seconds)
    stale = list(
        db.scalars(
            select(Job)
            .where(Job.status == "running", Job.heartbeat_at < cutoff)
            .with_for_update(skip_locked=True)
        )
    )
    for job in stale:
        if job.attempts >= settings.worker_max_attempts:
            job.status = "failed"
            job.error = (
                f"워커가 {job.attempts}번 시도했지만 끝내지 못했습니다"
                f"(마지막 워커 {job.worker_id}). 파일이 너무 크거나 워커가 계속 죽는 것입니다."
            )
            job.finished_at = datetime.now(UTC)
        else:
            job.status = "queued"
            job.worker_id = None
            job.progress = {"stage": "되돌림", "done": 0, "total": 0}
        log.warning("멎은 작업 되살림: %s (%s → %s)", job.id, job.kind, job.status)
    db.commit()
    return len(stale)


@dataclass
class Outcome:
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    output_file_id: uuid.UUID | None = None


def run(job: Job, *, worker_id: str) -> Outcome:
    """작업 하나를 **끝까지.** 본 트랜잭션은 여기서 한 번 커밋하거나 롤백한다.

    결과 · 상태는 커밋 뒤에 **다른 세션**으로 쓴다 — 본 트랜잭션에 섞어 쓰면 실패한 작업의
    「failed」 표시가 롤백과 함께 사라진다.
    """
    spec = kinds.get(job.kind)
    if spec is None:
        return Outcome("failed", error=f"모르는 작업 종류입니다: {job.kind}")

    def progress(stage: str, done: int, total: int) -> None:
        report_progress(job.id, stage, done, total)
        if cancel_requested(job.id):
            raise Cancelled()

    with SessionLocal() as db:
        try:
            user = db.get(User, job.requested_by_id) if job.requested_by_id else None
            if user is None and not spec.allow_system:
                raise AppError(
                    code("JOBS", 14), "시킨 사람의 계정이 없어졌습니다.", status=409
                )
            input_file = db.get(JobFile, job.input_file_id) if job.input_file_id else None
            if spec.needs_file and input_file is None:
                raise AppError(
                    code("JOBS", 13), "파일이 이미 지워졌습니다. 다시 올리세요.", status=409
                )
            work = kinds.Work(
                db=db,
                job=job,
                user=user,
                params=dict(job.params or {}),
                input_file=input_file,
                progress=progress,
            )
            result = spec.run(work)
            # 끝난 뒤의 취소 요청은 늦은 것이다 — `apply_*` 는 이미 커밋했다. 「취소됨」 이라
            # 적으면 들어간 것을 안 들어갔다고 말하는 셈이다.
            db.commit()
            return Outcome("done", result=result, output_file_id=work.output_file_id)
        except Cancelled:
            db.rollback()
            return Outcome("cancelled")
        except AppError as caught:
            db.rollback()
            return Outcome("failed", error=f"[{caught.code}] {caught.message}")
        except Exception as caught:  # 워커는 죽지 않는다. 스택은 로그에.
            db.rollback()
            log.exception("작업 실패: %s (%s)", job.id, job.kind)
            return Outcome("failed", error=f"{type(caught).__name__}: {caught}"[:2000])


def settle(job_id: uuid.UUID, outcome: Outcome) -> None:
    """끝난 상태를 적고, **오래 걸린 것이면 시킨 사람에게 알린다** — `run` 의 세션과 다른
    세션으로(본 트랜잭션에 섞으면 실패한 작업의 「failed」 표시가 롤백과 함께 사라진다)."""
    with SessionLocal() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=outcome.status,
                result=outcome.result,
                error=outcome.error,
                output_file_id=outcome.output_file_id,
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        row = db.get(Job, job_id)
        if row is not None:
            announce(db, row)
            db.commit()


def announce(db: Session, job: Job) -> None:
    """끝났다고 알린다 — **오래 걸린 것만, 시킨 사람에게만.**

    긴 작업은 창을 닫고 다른 일을 한다 — 끝났는지 알 길이 화면을 다시 여는 것뿐이면 사람은
    그 화면을 몇 번씩 연다. 반대로 1초에 끝난 것까지 알리면 그때 사람은 아직 그 화면을 보고
    있었고, 본 것을 한 번 더 말하는 종은 잡음이 된다. 타이머가 넣은 작업은 시킨 사람이 없어
    저절로 빠진다.
    """
    if job.requested_by_id is None or job.status == "cancelled":
        return
    started = job.started_at or job.created_at
    if job.finished_at is None or started is None:
        return
    if (job.finished_at - started).total_seconds() < get_settings().job_notify_after_seconds:
        return
    spec = kinds.get(job.kind)
    label = spec.label if spec else job.kind
    if job.status == "failed":
        notifications.notify(
            db,
            user_id=job.requested_by_id,
            kind=notifications.JOB_FAILED,
            title=f"{label}이(가) 실패했습니다",
            body=(job.error or "")[:500] or None,
            link="/jobs",
        )
        return
    result = job.result or {}
    counts = result.get("counts") or {}
    if result.get("applied"):
        body = (
            f"새로 {counts.get('create', 0)} · 고침 {counts.get('update', 0)}건을 넣었습니다."
            if counts
            else None
        )
        title = f"{label} — 적용이 끝났습니다"
    elif counts:
        body = (
            f"새로 {counts.get('create', 0)} · 고침 {counts.get('update', 0)} · "
            f"오류 {counts.get('error', 0)}건. **아직 아무것도 안 들어갔습니다** — "
            "작업 화면에서 계획을 읽고 적용하세요."
        )
        title = f"{label} — 계획이 끝났습니다"
    else:
        body = None
        title = f"{label}이(가) 끝났습니다"
    notifications.notify(
        db,
        user_id=job.requested_by_id,
        kind=notifications.JOB_DONE,
        title=title,
        body=body,
        link="/jobs",
    )


def process_one(worker_id: str) -> bool:
    """집어서 돌리고 적는다. 집은 것이 없으면 False."""
    with SessionLocal() as db:
        job = claim(db, worker_id)
        if job is None:
            return False
        job_id, kind = job.id, job.kind
        db.expunge(job)
    heartbeat(worker_id, job_id)
    log.info("작업 시작: %s (%s)", job_id, kind)
    outcome = run(job, worker_id=worker_id)
    settle(job_id, outcome)
    heartbeat(worker_id, None)
    log.info("작업 끝: %s (%s) → %s", job_id, kind, outcome.status)
    return True


def pending_for(db: Session, kind: str, **match: Any) -> Job | None:
    """같은 종류 · 같은 인자로 아직 안 끝난 작업이 있나 — 타이머가 두 번 넣지 않게."""
    for row in db.scalars(
        select(Job).where(Job.kind == kind, Job.status.in_(("queued", "running")))
    ):
        if all((row.params or {}).get(key) == value for key, value in match.items()):
            return row
    return None


# --- 홈의 「남은 일」 · 서버 화면의 「쌓인 것」 -------------------------------------


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**끝났는데 아무도 안 누른 계획**과 **멎은 워커**를 홈이 말한다.

    큰 파일을 올린 사람은 창을 닫고 다른 일을 한다. 알림이 가지만 그것을 놓치면 그 계획은
    「작업」 화면을 열어 봐야만 보이고, 그 화면은 평소에 아무도 안 연다 — 그러면 며칠 뒤
    「그때 그거 안 들어갔네」 가 된다.

    워커가 한 대도 안 살아 있으면 **작업도 웹훅도 멎는다.** 그 사실이 「작업」 화면 안에만
    있으면 운영자는 사람이 물어볼 때까지 모른다.
    """
    items: list[extensions.MaintenanceItem] = []
    # **적용 작업이 이미 걸린 계획은 할 일이 아니다.** 계획 작업의 `applied` 는 영영 거짓이고
    # (적용은 새 작업이다), 그것만 보면 적용하고 나서도 홈이 계속 조른다.
    handled = {
        parent
        for parent in db.scalars(
            select(Job.parent_id).where(Job.parent_id.isnot(None), Job.status != "cancelled")
        )
    }
    waiting = 0
    for row in db.scalars(
        select(Job).where(Job.status == "done", Job.requested_by_id == viewer.id)
    ):
        result = row.result or {}
        if row.id in handled or result.get("applied"):
            continue
        if not (result.get("counts") or result.get("ok")):
            continue
        counts = result.get("counts") or {}
        if counts.get("error") or result.get("errors"):
            continue
        if counts and not (counts.get("create", 0) + counts.get("update", 0)):
            continue
        waiting += 1
    if waiting:
        items.append(
            extensions.MaintenanceItem(
                key="job_awaiting_apply",
                label="읽고 적용하기를 기다리는 계획",
                count=waiting,
                link="/jobs",
            )
        )
    failed = (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "failed", Job.requested_by_id == viewer.id)
        )
        or 0
    )
    if failed:
        items.append(
            extensions.MaintenanceItem(
                key="job_failed",
                label="실패한 내 작업",
                count=int(failed),
                link="/jobs",
                severity="warning",
            )
        )
    if viewer.is_system_admin and not alive_workers(db) and pending_jobs(db):
        items.append(
            extensions.MaintenanceItem(
                key="worker_down",
                label="작업 워커가 살아 있지 않습니다 — 가져오기와 웹훅이 멎어 있습니다",
                count=1,
                link="/jobs",
                severity="warning",
            )
        )
    return items


def alive_workers(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().worker_stale_seconds)
    return int(
        db.scalar(
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(WorkerHeartbeat.last_seen >= cutoff)
        )
        or 0
    )


def pending_jobs(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running")))
        )
        or 0
    )


def stats(db: Session) -> list[extensions.StatItem]:
    """서버 화면의 「쌓인 것」 — 표가 얼마나 찼나. 작업 파일은 DB 에 들어 있어서,
    안 보여 주면 덤프가 왜 커졌는지 물을 자리가 없다."""
    jobs = db.scalar(select(func.count()).select_from(Job)) or 0
    files_count = db.scalar(select(func.count()).select_from(JobFile)) or 0
    stored = db.scalar(select(func.coalesce(func.sum(JobFile.size_bytes), 0))) or 0
    return [
        extensions.StatItem(label="작업", count=int(jobs)),
        extensions.StatItem(label="작업 워커(살아 있는)", count=alive_workers(db)),
        extensions.StatItem(
            label=f"작업 파일 ({int(stored) // (1024 * 1024)}MB)", count=int(files_count)
        ),
    ]
