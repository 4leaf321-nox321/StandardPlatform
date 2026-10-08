"""작업의 생애 — 넣기 · 집기 · 진행 · 끝내기 · 되살리기.

두 종류의 세션이 있다. **요청의 세션**(넣기 · 보기 · 취소 요청)과 **워커의 세션**(집기 ·
돌리기). 그리고 진행률과 심장박동은 **따로 짧은 세션**을 연다 — 본 트랜잭션이 열려 있는
동안 그 세션으로 쓰면 커밋할 때까지 아무도 못 보고, 커밋하면 「전부 아니면 무」 가 깨진다.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

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


class Lost(Cancelled):
    """워커가 단계 사이에서 보니 이 작업이 **더는 제 것이 아니다**(되살려져 남이 다시 집었다)
    — 롤백하고 결과를 버린다.

    `Cancelled` 를 잇는 까닭: 「멈춰라」 를 받는 자리(지표 다시 계산 · 동기화 차례)는 이미
    `Cancelled` 를 다시 던지게 짜여 있다. 따로 두면 그 자리의 `except Exception` 이 삼켜
    지표마다 「실패」 로 적고 다음 것을 계속 돈다."""


# --- 파일 — 저장 계층은 files.py --------------------------------------------------


def store_file(db: Session, *, name: str, content_type: str, data: bytes) -> JobFile:
    return files.store(db, name=name, content_type=content_type, data=data)


def purge_expired_files(db: Session) -> int:
    return files.purge_expired(db)


def purge_old_jobs(db: Session) -> int:
    return files.purge_old_jobs(db)


def purge_old_tombstones(db: Session) -> int:
    return files.purge_old_tombstones(db)


def purge_old_undo_journals(db: Session) -> int:
    return files.purge_old_undo_journals(db)


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
    """`queued` 면 바로 끝, `running` 이면 표시만 — 워커가 단계 사이에서 본다.

    ⚠️ **읽은 상태를 믿고 쓰지 않는다** — 조건부 UPDATE 다. 예전에는 읽은 `queued` 를 보고
       `cancelled` 를 그대로 덮었다. 읽은 직후 워커가 집었으면 워커는 `cancel_requested` 만
       보므로 끝까지 돌았고, 끝에서 `done` 으로 다시 덮어 **취소가 소리 없이 사라졌다**
       (2026-10-08). 이제 아직 대기일 때만 끝내고, 그 사이 집혔으면 표시를 남긴다.
    """
    if not user.is_system_admin and job.requested_by_id != user.id:
        raise Forbidden(code("JOBS", 6), "시킨 사람만 취소할 수 있습니다.")
    if job.status in TERMINAL:
        raise Conflict(code("JOBS", 7), "이미 끝난 작업입니다.")
    stopped = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == "queued")
        .values(status="cancelled", finished_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if not extensions.rows_changed(stopped):
        marked = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "running")
            .values(cancel_requested=True)
            .execution_options(synchronize_session=False)
        )
        if not extensions.rows_changed(marked):
            raise Conflict(code("JOBS", 7), "이미 끝난 작업입니다.")
    db.refresh(job)
    return job


def counts_as_apply(child: Any) -> Any:
    """이 자식 작업이 **계획을 적용한(또는 적용 중인)** 것인가 — SQL 조건.

    실패하거나 취소된 적용, `applied` 가 거짓으로 끝난 적용은 셈하지 않는다 — 그때는 다시
    적용할 수 있어야 한다. 판정은 **여기 하나**다: 홈의 「적용 대기」 가 따로 셌을 때 실패한
    적용까지 「처리됨」 으로 쳐서, 다시 적용할 수 있는 계획이 홈에서 사라졌다(2026-10-08).
    `result` 가 없거나 `applied` 칸이 없으면 셈한다(`is not False` 와 같다).
    """
    return or_(
        child.status.in_(("queued", "running")),
        and_(
            child.status == "done",
            child.result["applied"].astext.is_distinct_from("false"),
        ),
    )


def apply_child(db: Session, plan_id: uuid.UUID) -> Job | None:
    """이 계획을 **적용한(또는 적용 중인) 작업** — 없으면 None.

    계획 작업의 `result.applied` 는 영영 거짓이다(적용은 새 작업이다). 그것만 보면 적용하고
    나서도 화면에 「적용 대기」 와 「적용」 단추가 남고, 같은 계획이 **두 번** 들어갈 수
    있었다. 무엇을 셈하나는 `counts_as_apply` 가 정한다.
    """
    return db.scalar(
        select(Job)
        .where(Job.parent_id == plan_id, counts_as_apply(Job))
        .order_by(Job.created_at.desc())
        .limit(1)
    )


def make_apply(db: Session, user: User, plan_job: Job) -> Job:
    """계획 작업 → 적용 작업. **같은 파일 · 같은 지문.**"""
    spec = kinds.get(plan_job.kind)
    if spec is None or not spec.two_step:
        raise Conflict(code("JOBS", 8), "이 종류의 작업은 적용 단계가 없습니다.")
    if plan_job.status != "done" or not plan_job.result:
        raise Conflict(code("JOBS", 9), "계획이 끝난 작업만 적용할 수 있습니다.")
    if plan_job.params.get("apply"):
        raise Conflict(code("JOBS", 10), "이미 적용 작업입니다.")
    # **같은 계획을 두 번 넣지 않는다** — 화면 · 정제 도구 · MCP 어디서 눌렀든.
    done = apply_child(db, plan_job.id)
    if done is not None:
        raise Conflict(
            code("JOBS", 22),
            "이 계획은 이미 적용했습니다(또는 적용 중입니다) — 「작업」 화면에서 그 적용 "
            "작업을 보세요. 다시 넣으려면 새로 미리 보세요.",
            details={"applied_by": str(done.id)},
        )
    result = plan_job.result
    if result.get("errors") or (result.get("counts") or {}).get("error"):
        raise Conflict(
            code("JOBS", 11), "오류가 있는 계획은 적용하지 않습니다. 고쳐서 다시 올리세요."
        )
    if not user.is_system_admin and plan_job.requested_by_id != user.id:
        raise Forbidden(code("JOBS", 12), "계획을 본 사람이 적용합니다.")
    if spec.needs_file and plan_job.input_file_id is None:
        raise Conflict(code("JOBS", 13), "파일이 이미 지워졌습니다. 다시 올리세요.")
    # 파일이 없는 두 단계 작업도 있다(묶음 되돌리기 — 인자가 판 번호 하나뿐이다).
    input_file = db.get(JobFile, plan_job.input_file_id) if plan_job.input_file_id else None
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
            # 내가 쥔 것만 — 되살려져 남이 집은 작업의 박동을 대신 뛰면, 그 워커가 죽어도
            # 살아 있는 것처럼 보인다.
            db.execute(update(Job).where(_owned(job_id, worker_id)).values(heartbeat_at=now))
        db.commit()


def report_progress(
    job_id: uuid.UUID, worker_id: str, stage: str, done: int, total: int
) -> bool:
    """진행을 적고 **취소 요청이 있나**를 돌려준다 — 아직 이 워커가 쥔 작업일 때만 적는다.

    쥐고 있지 않으면 `Lost` — 되살려져 남이 다시 집은 작업이다. 단계 사이에서 알아야 남의
    진행을 덮어쓰지 않고, **커밋 전에** 멈춰 같은 일이 두 번 안 든다(끝에서만 보면 스스로
    커밋하는 종류 — 일괄 입력 · 묶음 — 는 이미 넣은 뒤다).
    """
    with SessionLocal() as db:
        row = db.execute(
            update(Job)
            .where(_owned(job_id, worker_id))
            .values(
                progress={"stage": stage, "done": done, "total": total},
                heartbeat_at=datetime.now(UTC),
            )
            .returning(Job.cancel_requested)
        ).first()
        db.commit()
    if row is None:
        raise Lost()
    return bool(row[0])


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

    ⚠️ **살아 있는 워커의 것은 안 빼앗는다.** 예전에는 작업의 박동만 봤다 — 그 박동은 진행
       보고가 올려 주므로, 말없이 오래 걸리는 단계(수만 줄 커밋)에서 5분이 지나면 다른
       워커가 **같은 작업을 한 번 더 돌렸다**(실측: 백필이 두 벌 들어갔다). 워커 자신의
       박동(`worker_heartbeats`)이 아직 뛰고 그 작업을 쥐고 있으면, 늦은 것이지 죽은 것이
       아니다. 그 워커가 죽으면 박동이 멎고 다음 바퀴에 여기서 잡힌다.
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
    busy = {
        row.current_job_id
        for row in db.scalars(
            select(WorkerHeartbeat).where(WorkerHeartbeat.last_seen >= cutoff)
        )
        if row.current_job_id is not None
    }
    held = [job for job in stale if job.id in busy]
    for job in held:
        log.warning(
            "작업 %s 은 워커 %s 가 아직 쥐고 있습니다 — 안 빼앗습니다(박동만 늦음)",
            job.id,
            job.worker_id,
        )
    stale = [job for job in stale if job.id not in busy]
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


LOST = "lost"
"""이 워커가 **더는 쥐고 있지 않은** 작업 — 결과를 버린다(`Outcome.status` 로만 쓰고 표에는
안 적는다). 박동이 멎어 되살려진 뒤 다른 워커가 다시 집었거나, 그 사이 끝난 것이다."""


@dataclass
class Outcome:
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    output_file_id: uuid.UUID | None = None
    written: bool = False
    """본 트랜잭션이 상태(`done`)까지 함께 커밋했다 — `settle` 은 알리기만 한다."""


def _owned(job_id: uuid.UUID, worker_id: str) -> Any:
    """「아직 이 워커가 쥔, 도는 작업」 — 끝을 적는 모든 UPDATE 의 조건."""
    return and_(Job.id == job_id, Job.status == "running", Job.worker_id == worker_id)


def _why_cannot_run_as(user: User) -> str | None:
    """시킨 사람의 계정으로 지금 돌릴 수 없으면 그 까닭 — 돌려도 되면 None."""
    if user.can_sign_in:
        return None
    if user.deleted_at is not None:
        return "삭제되어"
    if user.status == "suspended":
        return "정지되어"
    return "로그인할 수 없는 상태라"


def run(job: Job, *, worker_id: str) -> Outcome:
    """작업 하나를 **끝까지.** 본 트랜잭션은 여기서 한 번 커밋하거나 롤백한다.

    **「아직 내가 쥔 작업」 인지 단계마다 본다** — 진행 보고(`report_progress`)가 조건부로
    적고, 남이 다시 집었으면 `Lost` 로 롤백한다(같은 일이 두 번 안 든다).

    **성공은 본 트랜잭션과 함께 적는다** — 같은 조건으로. 예전에는 커밋한 뒤 다른 세션으로
    적었다. 그 사이가 끊기면(적기 실패 · 워커가 죽음) 작업은 `running` 으로 남아 되살려지고,
    다시 돌면 지문이 달라 **이미 들어간 적용이 `failed`** 로 보였다(2026-10-08). 스스로
    커밋하는 종류(일괄 입력 · 묶음)는 그 커밋과 이 적기 사이가 아주 짧아질 뿐 하나가 되지는
    않는다.

    실패 · 취소는 **다른 세션**으로 쓴다(`settle`) — 본 트랜잭션에 섞어 쓰면 실패한 작업의
    「failed」 표시가 롤백과 함께 사라진다.
    """
    spec = kinds.get(job.kind)
    if spec is None:
        return Outcome("failed", error=f"모르는 작업 종류입니다: {job.kind}")

    def progress(stage: str, done: int, total: int) -> None:
        if report_progress(job.id, worker_id, stage, done, total):
            raise Cancelled()

    with SessionLocal() as db:
        try:
            user = db.get(User, job.requested_by_id) if job.requested_by_id else None
            if user is None and not spec.allow_system:
                raise AppError(
                    code("JOBS", 14), "시킨 사람의 계정이 없어졌습니다.", status=409
                )
            why = _why_cannot_run_as(user) if user is not None else None
            if user is not None and why is not None:
                # 작업은 **시킨 사람의 권한으로** 돈다 — 정지 · 삭제된 사람의 것을 돌리면 막은
                # 계정이 줄에 남은 작업으로 계속 쓴다(2026-10-08). 타이머가 넣은 것(시킨 사람
                # 없음)은 해당 없다.
                raise AppError(
                    code("JOBS", 27),
                    f"시킨 사람({user.display_name})의 계정이 {why} 돌리지 않습니다 — "
                    "필요하면 다른 사람이 다시 넣으세요.",
                    status=409,
                )
            if spec.admin_only and user is not None and not user.is_system_admin:
                # 넣는 길이 몇이든 여기서 한 번 더 — 전용 경로를 건너뛰어 들어온 것을 막는다.
                raise Forbidden(
                    code("JOBS", 24), f"{spec.label}은(는) 시스템 관리자만 돌립니다."
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
            finished = db.execute(
                update(Job)
                .where(_owned(job.id, worker_id))
                .values(
                    status="done",
                    result=result,
                    error=None,
                    output_file_id=work.output_file_id,
                    finished_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            if not extensions.rows_changed(finished):
                raise Lost()
            db.commit()
            return Outcome(
                "done", result=result, output_file_id=work.output_file_id, written=True
            )
        except Lost:
            db.rollback()
            log.warning(
                "작업 %s (%s) 은 이제 이 워커(%s)의 것이 아닙니다 — 결과를 버리고 "
                "롤백합니다(되살려져 다시 집혔거나 이미 끝났다)",
                job.id,
                job.kind,
                worker_id,
            )
            return Outcome(LOST)
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


def settle(job_id: uuid.UUID, outcome: Outcome, *, worker_id: str) -> None:
    """끝난 상태를 적고, **오래 걸린 것이면 시킨 사람에게 알린다** — `run` 의 세션과 다른
    세션으로(본 트랜잭션에 섞으면 실패한 작업의 「failed」 표시가 롤백과 함께 사라진다).

    **아직 이 워커가 쥔 작업일 때만 적는다.** 박동이 멎어 되살려진 뒤 남이 다시 집은 작업에
    원래 워커가 늦게 「실패」 를 적으면, 도는 쪽의 결과 위에 남의 결과가 덮인다. 그때는 버리고
    로그만 남긴다. 성공(`written`)은 `run` 이 본 트랜잭션과 함께 이미 적었다.
    """
    if outcome.status == LOST:
        return
    with SessionLocal() as db:
        if not outcome.written:
            wrote = db.execute(
                update(Job)
                .where(_owned(job_id, worker_id))
                .values(
                    status=outcome.status,
                    result=outcome.result,
                    error=outcome.error,
                    output_file_id=outcome.output_file_id,
                    finished_at=datetime.now(UTC),
                )
            )
            db.commit()
            if not extensions.rows_changed(wrote):
                log.warning(
                    "작업 %s 은 이제 이 워커(%s)의 것이 아닙니다 — 「%s」 를 안 적고 버립니다",
                    job_id,
                    worker_id,
                    outcome.status,
                )
                return
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
        # 계획을 「작업」 화면에서 적용하는 것은 **두 단계 종류**뿐이다 — 데이터 소스 동기화의
        # 계획은 그 화면의 「동기화」 를 다시 눌러 적용한다(작업 화면에는 적용이 없다).
        where = (
            "작업 화면에서 계획을 읽고 적용하세요."
            if spec is not None and spec.two_step
            else "그 화면에서 계획을 읽고 적용으로 다시 돌리세요."
        )
        body = (
            f"새로 {counts.get('create', 0)} · 고침 {counts.get('update', 0)} · "
            f"오류 {counts.get('error', 0)}건. **아직 아무것도 안 들어갔습니다** — {where}"
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


HEARTBEAT_EVERY = 30.0
"""작업이 도는 동안 몇 초마다 박동을 남기나. `worker_stale_seconds`(300) 보다 훨씬 짧아야
한다 — 그 사이에 한 번도 못 남기면 남의 워커가 이 작업을 「멎었다」 고 본다."""


class _Beating:
    """작업이 도는 동안 **심장박동을 따로 뛴다.**

    예전에는 진행 보고(`report_progress`)가 그 일을 겸했다 — 그래서 말없이 오래 걸리는
    단계에서 박동이 멎었고, 다른 워커가 같은 작업을 한 번 더 돌렸다. 박동은 「이 워커가 아직
    살아서 이 작업을 쥐고 있다」 는 사실이고, 그 사실은 진행 보고와 따로 뛰어야 한다.
    """

    def __init__(self, worker_id: str, job_id: uuid.UUID) -> None:
        self.worker_id = worker_id
        self.job_id = job_id
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._beat, name="job-heartbeat", daemon=True)

    def _beat(self) -> None:
        while not self.done.wait(HEARTBEAT_EVERY):
            try:
                heartbeat(self.worker_id, self.job_id)
            except Exception:  # 박동 하나 못 남긴 것으로 작업을 죽이지 않는다.
                log.warning("심장박동을 남기지 못했습니다 (작업 %s)", self.job_id)

    def __enter__(self) -> _Beating:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.done.set()
        self.thread.join(timeout=5.0)


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
    with _Beating(worker_id, job_id):
        outcome = run(job, worker_id=worker_id)
    settle(job_id, outcome, worker_id=worker_id)
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
    waiting = 0
    # **적용 단계가 있는 종류만 센다** — 데이터 소스 동기화의 계획은 작업 화면에서 적용할 수
    # 없다(그 화면에서 다시 돌린다). 세면 계획을 볼 때마다 홈의 수가 늘고 지울 길이 없었다
    # (2026-10-08).
    two_step = [one.name for one in kinds.all_kinds() if one.two_step]
    needs_file = [one.name for one in kinds.all_kinds() if one.two_step and one.needs_file]
    # **적용할 수 있는 계획만** — 올린 파일은 `job_file_ttl_days` 뒤 지워지고 그 뒤엔 적용이
    # 거절된다(`make_apply`). 그보다 오래된 계획은 할 일이 아니라 지난 일이다. 예전에는 홈을
    # 열 때마다 내가 끝낸 계획 전부를 **결과(계획 표 — 만 줄이면 만 줄) 째** 읽고, 모든
    # 사람의 자식 작업까지 훑었다(2026-10-08). 이제 판정에 쓰는 칸만 읽는다.
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().job_file_ttl_days)
    child = aliased(Job)
    result = Job.result
    rows = db.execute(
        select(
            result["applied"],
            result["ok"],
            result["counts"],
            # 오류 목록은 클 수 있다 — 있나 없나만(빈 목록 · 빈 값은 없는 것).
            func.coalesce(
                result["errors"].astext.notin_(("[]", "{}", "", "0", "false")), False
            ),
        ).where(
            Job.status == "done",
            Job.requested_by_id == viewer.id,
            Job.kind.in_(two_step),
            Job.finished_at >= cutoff,
            or_(Job.kind.notin_(needs_file), Job.input_file_id.isnot(None)),
            # **적용 작업이 이미 걸린 계획은 할 일이 아니다** — 판정은 `apply_child` 와 같은
            # 것(`counts_as_apply`). 실패한 적용은 다시 적용할 수 있으니 할 일로 남는다.
            ~exists().where(child.parent_id == Job.id, counts_as_apply(child)),
        )
    )
    for applied, ok, counts, has_errors in rows:
        if applied:
            continue
        if not (counts or ok):
            continue
        counts = counts or {}
        if counts.get("error") or has_errors:
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
