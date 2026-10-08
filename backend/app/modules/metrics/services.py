"""지표 서비스 — 정의 CRUD · 작업 넣기 · 돌릴 차례 · 홈의 「남은 일」.

## 돌리는 때 셋

- 밤마다: `scripts/recompute_metrics.py --due` 가 `due()` 로 차례인 지표마다 작업 하나를
  넣는다(데이터 소스 동기화와 같은 무늬 — 잠금 하나, 중복 없음, 시킨 사람 없음).
- 적재 뒤: `jobs/kinds.py` 의 적재 종류가 **작업 본문 안에서** `enqueue_for_type` 을
  부른다. 이벤트가 아닌 이유: 워커 프로세스는 `app.main` 을 import 하지 않아 리스너가 비어
  있다.
- 손으로: 화면 · MCP 가 `POST /recompute`.

같은 지표의 작업이 이미 줄에 있으면 **그 작업을 돌려준다** — 적재가 5분마다 와도 작업이
쌓이지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.metrics import alerts, compute, query, recipes, schemas
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import HomeMetric, MetricDef, MetricRun, MetricValue
from app.modules.objects import home
from app.modules.objects.summary import METRIC_LABELS
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import require_slug
from app.modules.workspaces.models import Workspace
from app.shared import audit, extensions
from app.shared.errors import AppError, Conflict, NotFound, code
from app.shared.permissions import require_owner_edit

log = logging.getLogger(__name__)

JOB_KIND = "metrics_recompute"
#: 홈의 「남은 일」 이 가리키는 화면.
PAGE = "/metrics"


def get(db: Session, slug: str) -> MetricDef:
    row = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
    if row is None:
        raise NotFound(code("METRICS", 1), f"지표를 찾을 수 없습니다: {slug}")
    return row


def _type(db: Session, slug: str) -> ObjectType:
    found = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if found is None:
        raise NotFound(code("METRICS", 2), f"타입을 찾을 수 없습니다: {slug}")
    return found


def _rejected(found: spec_module.Plan) -> AppError:
    return AppError(
        code("METRICS", 3),
        "정의를 저장할 수 없습니다 — " + " / ".join(found.errors),
        status=422,
        details={"errors": found.errors, "warnings": found.warnings},
    )


def plan_out(found: spec_module.Plan) -> schemas.PlanOut:
    return schemas.PlanOut(
        ok=found.ok,
        errors=found.errors,
        warnings=found.warnings,
        rows=found.rows,
        estimated_cells=found.estimated_cells,
        overlap=found.overlap,
        dims=[schemas.DimOut(**vars(one)) for one in found.dims],
        period_from=found.period_from,
        period_to=found.period_to,
    )


def plan(db: Session, payload: schemas.PlanIn) -> schemas.PlanOut:
    source = _type(db, payload.source_type_slug)
    return plan_out(spec_module.plan(db, source, payload.spec, self_slug=payload.slug))


def out(db: Session, metric: MetricDef) -> schemas.MetricOut:
    """정의 한 줄 — 지어 보고 기준의 이름 · 종류를 붙인다. 안 지어지면 그 이유(`broken`)를."""
    source = db.get(ObjectType, metric.source_type_id)
    spec = spec_module.MetricSpec.model_validate(metric.spec)
    dims: list[schemas.DimOut] = []
    broken: str | None = None
    built: spec_module.Built | None = None
    try:
        built = compute.built_of(db, metric)
        dims = [
            schemas.DimOut(
                name=one.name,
                address=one.address,
                label=one.axis.label,
                kind=one.axis.kind,
                multi=one.axis.multi,
                grain=one.grain,
                target=one.signature[1]
                if one.signature[0] == "ref" and one.signature[1]
                else None,
            )
            for one in built.dims
        ]
    except AppError as caught:
        broken = caught.message
    return schemas.MetricOut(
        id=metric.id,
        slug=metric.slug,
        label=metric.label,
        description=metric.description,
        source_type_slug=source.slug if source else "",
        source_type_label=source.label if source else "",
        spec=dict(metric.spec or {}),
        interval_hours=metric.interval_hours,
        is_active=metric.is_active,
        overlap=metric.overlap,
        measure_label=METRIC_LABELS.get(spec.measure, spec.measure),
        grain=spec.time.grain if spec.time else None,
        cohort_grain=spec.cohort.grain if spec.cohort else None,
        dims=dims,
        broken=broken,
        analyses=recipes.registry.availability(built, broken),
        current_run_id=metric.current_run_id,
        last_run_at=metric.last_run_at,
        last_status=metric.last_status,
        last_error=metric.last_error,
        cells=metric.cells,
        stale=query.is_stale(metric),
        created_at=metric.created_at,
        updated_at=metric.updated_at,
    )


def create(db: Session, user: User, payload: schemas.MetricIn) -> MetricDef:
    """정의 하나 — **계획이 통과할 때만.** 부르는 쪽이 커밋한다."""
    slug = require_slug(payload.slug, what="지표 slug")
    if db.scalar(select(MetricDef.id).where(MetricDef.slug == slug)) is not None:
        raise Conflict(code("METRICS", 4), f"같은 slug 의 지표가 이미 있습니다: {slug}")
    source = _type(db, payload.source_type_slug)
    found = spec_module.plan(db, source, payload.spec, self_slug=slug)
    if not found.ok:
        raise _rejected(found)
    row = MetricDef(
        slug=slug,
        label=payload.label,
        description=payload.description,
        source_type_id=source.id,
        spec=payload.spec.model_dump(mode="json"),
        interval_hours=payload.interval_hours,
        is_active=payload.is_active,
        overlap=found.overlap,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="metric.defined",
        actor=user,
        target_table="metric_defs",
        target_id=row.id,
        target_label=row.label,
        changes={
            "slug": slug,
            "source": source.slug,
            "estimated_cells": found.estimated_cells,
        },
    )
    return row


def update(
    db: Session, user: User, metric: MetricDef, payload: schemas.MetricPatch
) -> MetricDef:
    changes: dict[str, Any] = {}
    if payload.spec is not None:
        source = db.get(ObjectType, metric.source_type_id)
        if source is None:
            raise AppError(
                code("METRICS", 10), "원천 타입이 없어 정의를 고칠 수 없습니다.", status=422
            )
        found = spec_module.plan(db, source, payload.spec, self_slug=metric.slug)
        if not found.ok:
            raise _rejected(found)
        metric.spec = payload.spec.model_dump(mode="json")
        metric.overlap = found.overlap
        changes["spec"] = True
    for name in ("label", "description", "interval_hours", "is_active"):
        value = getattr(payload, name)
        if value is not None and value != getattr(metric, name):
            changes[name] = {"from": getattr(metric, name), "to": value}
            setattr(metric, name, value)
    if changes:
        audit.record(
            db,
            action="metric.updated",
            actor=user,
            target_table="metric_defs",
            target_id=metric.id,
            target_label=metric.label,
            changes=changes,
        )
    return metric


def delete(db: Session, user: User, metric: MetricDef) -> None:
    """지운다 — 다른 지표의 분모면 거절. 실행 · 셀은 외래키가 따라 지운다."""
    users = [
        one.slug
        for one in db.scalars(select(MetricDef).where(MetricDef.id != metric.id))
        if ((one.spec or {}).get("denominator") or {}).get("metric") == metric.slug
    ]
    if users:
        raise Conflict(
            code("METRICS", 5),
            f"「{metric.label}」 은 다른 지표의 분모라 지울 수 없습니다: {', '.join(users)}",
        )
    audit.record(
        db,
        action="metric.deleted",
        actor=user,
        target_table="metric_defs",
        target_id=metric.id,
        target_label=metric.label,
        changes={"slug": metric.slug, "cells": metric.cells},
    )
    db.delete(metric)


# --- 돌리기 ---------------------------------------------------------------------


#: 이 까닭으로 넣은 작업은 **전부** 다시 센다 — 사람이 「다시 세기」 를 누른 것은 지금 값을
#: 의심한다는 뜻이다. 타이머 · 적재 뒤 · 정의 저장은 되면 바뀐 기간만(정의가 바뀌었으면 정의
#: 지문이 달라 어차피 전량이다, `incremental.plan`).
FULL_REASONS = frozenset({"manual"})


def pending_recompute(db: Session, slug: str) -> Job | None:
    return job_services.pending_for(db, JOB_KIND, slugs=[slug])


def queued_recompute(db: Session, slug: str) -> Job | None:
    """줄에서 **아직 기다리는** 같은 지표의 작업 — 도는 것은 뺀다."""
    found = pending_recompute(db, slug)
    return found if found is not None and found.status == "queued" else None


def enqueue_recompute(
    db: Session, metric: MetricDef, *, user: User | None, reason: str
) -> Job:
    """작업 하나 — 같은 지표의 작업이 **줄에서 기다리고** 있으면 그것을. 부르는 쪽이 커밋한다.

    **도는 중인 작업에는 합치지 않는다** — 그 계산은 이미 원천을 읽기 시작해 그 뒤에 들어온
    적재를 못 본다. 합치면 적재 뒤 계산이 사라져 다음 타이머(하루 단위면 다음 밤)까지 값이
    낡았다(2026-10-08). 새 작업은 자문 잠금에서 앞 계산이 끝나기를 기다린다.

    기다리는 것이 바뀐 기간만 셀 작업인데 이번 까닭이 전량이면(사람의 「다시 세기」) 그 작업을
    전량으로 올린다 — **아직 안 집혔을 때만**(조건부로 고쳐, 그 사이 워커가 집었으면 새로
    넣는다)."""
    existing = queued_recompute(db, metric.slug)
    if existing is not None and reason in FULL_REASONS and not existing.params.get("full"):
        done = db.execute(
            sql_update(Job)
            .where(Job.id == existing.id, Job.status == "queued")
            .values(params={**(existing.params or {}), "full": True})
        )
        db.refresh(existing)
        if not extensions.rows_changed(done):
            existing = None  # 그 사이 워커가 집었다 — 그 계산은 바뀐 기간만 센다
    if existing is not None:
        return existing
    return job_services.enqueue(
        db,
        kind=JOB_KIND,
        params={"slugs": [metric.slug], "reason": reason},
        user=user,
        workspace_id=None,
    )


def enqueue_for_type(db: Session, type_id: uuid.UUID, *, reason: str) -> list[Job]:
    """이 타입을 원천으로 쓰는 켜진 지표마다 작업 하나 — 적재 작업의 본문이 부른다. 줄에서
    기다리는 것이 있으면 안 넣는다(도는 것은 이 적재를 못 보므로 넣는다)."""
    out: list[Job] = []
    for metric in db.scalars(
        select(MetricDef)
        .where(MetricDef.source_type_id == type_id, MetricDef.is_active.is_(True))
        .order_by(MetricDef.slug)
    ):
        if queued_recompute(db, metric.slug) is None:
            out.append(enqueue_recompute(db, metric, user=None, reason=reason))
    return out


#: 타이머는 몇 분 흩뜨려 깬다 — 그만큼은 봐준다(안 그러면 「6시간마다」 가 7시간마다가 된다).
DUE_GRACE = timedelta(minutes=15)
#: 「N일마다 밤」 — 밤 시간이면 지난 밤에서 이만큼만 지나도 차례다(밤 시간은 하루 한 번이다).
NIGHT_EARLY = timedelta(hours=20)
#: 밤을 놓쳤으면(서버가 꺼져 있었다) 이만큼 지난 뒤 아무 때나 따라잡는다.
NIGHT_MISSED = timedelta(hours=6)


def due(db: Session, now: datetime | None = None, *, nightly: bool = False) -> list[MetricDef]:
    """타이머가 돌릴 차례 — **타이머는 매시간 깬다.** `nightly` 는 지금이 밤 시간인가.

    - **하루 미만 주기**(6시간 …): 마지막 계산에서 그만큼 지났으면 — 낮에도.
    - **날 단위 주기**(매일 밤 · N일마다 밤): **밤 시간에만**, 타이머가 마지막으로 넣은 때
      (`scheduled_at`)에서 N밤이 지났으면. 계산이 끝난 때와 견주지 않는다 — 그러면 오늘 밤
      타이머가 어제 끝난 시각보다 몇 분 일찍 깨는 밤은 건너뛰어 이틀에 한 번꼴로 셀 수 있었고,
      낮에 적재로 다시 센 날은 그 밤 계산이 밀렸다. 한 번도 안 넣었으면 다음 밤에.
    - **밤을 놓쳤으면**(서버가 꺼져 있었다) 아무 때나 따라잡는다 — 그 뒤로는 다시 밤에.
    """
    now = now or datetime.now(UTC)
    out: list[MetricDef] = []
    for metric in db.scalars(
        select(MetricDef)
        .where(MetricDef.is_active.is_(True), MetricDef.interval_hours > 0)
        .order_by(MetricDef.slug)
    ):
        if metric.interval_hours < 24:
            every = timedelta(hours=metric.interval_hours)
            if metric.last_run_at is None or metric.last_run_at + every - DUE_GRACE <= now:
                out.append(metric)
            continue
        nights = timedelta(days=metric.interval_hours // 24)
        if metric.scheduled_at is None:
            if nightly:
                out.append(metric)
            continue
        since = now - metric.scheduled_at
        if (nightly and since >= nights - NIGHT_EARLY) or since >= nights + NIGHT_MISSED:
            out.append(metric)
    return out


def _mark_failed(
    db: Session, metric_id: uuid.UUID, job_id: uuid.UUID | None, message: str
) -> None:
    """실패를 **새 트랜잭션**으로 적는다 — 롤백된 계산과 섞이지 않게. 옛 값은 그대로다."""
    metric = db.get(MetricDef, metric_id)
    if metric is None:
        return
    now = datetime.now(UTC)
    db.add(
        MetricRun(
            metric_id=metric.id, job_id=job_id, status="failed", finished_at=now, error=message
        )
    )
    metric.last_status = "failed"
    metric.last_error = message
    db.commit()


def _check_alerts(db: Session, metric_id: uuid.UUID, slug: str) -> dict[str, int]:
    """계산이 커밋된 뒤 그 지표의 경보(ADR 0016). **경보는 계산을 실패로 만들지 않는다** —
    경보마다의 실패는 `alerts.check_one` 이 경보에 적고, 여기까지 온 것(DB 가 끊김 등)은
    로그만."""
    try:
        return alerts.after_recompute(db, metric_id)
    except Exception:
        db.rollback()
        log.exception("경보 확인 실패: %s", slug)
        return {"alerts": 0, "new": 0}


def run_recompute(
    db: Session,
    slugs: list[str],
    *,
    job_id: uuid.UUID | None,
    progress: Callable[[str, int, int], None],
    full: bool = True,
) -> dict[str, Any]:
    """지표들을 차례로 — **지표마다 커밋.** 하나가 실패해도 나머지는 돌고, 끝에 실패를 모아
    작업을 실패로 만든다(성공한 것의 새 값은 이미 들어가 있다). `full` 이 아니면 되는 지표는
    바뀐 기간만 센다."""
    runs: list[dict[str, Any]] = []
    failed: list[str] = []
    for index, slug in enumerate(slugs):
        progress("지표", index, len(slugs))
        metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
        if metric is None:
            failed.append(f"{slug}: 없는 지표")
            runs.append({"slug": slug, "status": "failed", "error": "없는 지표"})
            continue
        started = perf_counter()
        try:
            run = compute.run_one(db, metric, job_id=job_id, progress=progress, full=full)
        except job_services.Cancelled:
            db.rollback()
            raise
        except AppError as caught:
            db.rollback()
            message = f"[{caught.code}] {caught.message}"
            _mark_failed(db, metric.id, job_id, message)
            failed.append(f"{slug}: {message}")
            runs.append({"slug": slug, "status": "failed", "error": message})
            continue
        except Exception as caught:
            db.rollback()
            log.exception("지표 계산 실패: %s", slug)
            message = f"{type(caught).__name__}: {caught}"[:2000]
            _mark_failed(db, metric.id, job_id, message)
            failed.append(f"{slug}: {message}")
            runs.append({"slug": slug, "status": "failed", "error": message})
            continue
        seconds = round(perf_counter() - started, 3)
        progress("경보", index, len(slugs))
        runs.append(
            {
                "slug": slug,
                "status": "ok",
                "mode": run.mode,
                "note": run.note,
                "rows": run.rows,
                "cells": run.cells,
                "seconds": seconds,
                "error": None,
                "alerts": _check_alerts(db, metric.id, slug),
            }
        )
    if failed:
        raise AppError(
            code("METRICS", 6),
            f"지표 {len(failed)}개의 계산이 실패했습니다 — " + " / ".join(failed)[:1500],
        )
    return {"applied": True, "runs": runs}


# --- 홈의 「남은 일」 · 서버 화면의 「쌓인 것」 -------------------------------------------


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """조용히 멎은 지표를 홈이 말한다 — 시스템 관리자에게만(지표 정의는 그들의 것이다)."""
    if not viewer.is_system_admin:
        return []
    active = list(db.scalars(select(MetricDef).where(MetricDef.is_active.is_(True))))
    now = datetime.now(UTC)
    failed = sum(1 for one in active if one.last_status == "failed")
    never = sum(1 for one in active if one.last_run_at is None)
    stale = sum(
        1
        for one in active
        if one.last_run_at is not None and one.interval_hours > 0 and query.is_stale(one, now)
    )
    items: list[extensions.MaintenanceItem] = []
    if failed:
        items.append(
            extensions.MaintenanceItem(
                key="metric_failed",
                label="계산에 실패한 지표",
                count=failed,
                link=PAGE,
                severity="warning",
            )
        )
    if stale:
        items.append(
            extensions.MaintenanceItem(
                key="metric_stale",
                label="오래 안 센 지표(주기의 세 배)",
                count=stale,
                link=PAGE,
                severity="warning",
            )
        )
    if never:
        items.append(
            extensions.MaintenanceItem(
                key="metric_never_computed",
                label="아직 한 번도 안 센 지표",
                count=never,
                link=PAGE,
            )
        )
    return items


def stats(db: Session) -> list[extensions.StatItem]:
    defs = db.scalar(select(func.count()).select_from(MetricDef)) or 0
    cells = db.scalar(select(func.coalesce(func.sum(MetricDef.cells), 0))) or 0
    return [
        extensions.StatItem(label="지표", count=int(defs)),
        extensions.StatItem(label="지표 셀", count=int(cells)),
    ]


def values_count(db: Session, metric: MetricDef) -> int:
    """지금 실행의 셀 수(표에서 센다) — 시험과 점검이 `cells` 와 맞춰 본다."""
    cells_run = metric.cells_run_id or metric.current_run_id
    if cells_run is None:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(MetricValue)
            .where(MetricValue.metric_id == metric.id, MetricValue.run_id == cells_run)
        )
        or 0
    )


def profile_facts(db: Session) -> list[extensions.ProfileFact]:
    """자기소개의 「지표」(ADR 0019) — 미리 세어 둔 것이 무엇인가. 지표는 자주 생기고 지워져서
    낡음 표시로 쓰지 않는다(소개가 지표마다 낡으면 아무도 안 고친다)."""
    labels = list(
        db.scalars(
            select(MetricDef.label)
            .where(MetricDef.is_active.is_(True))
            .order_by(MetricDef.label)
        )
    )
    if not labels:
        return []
    shown = " · ".join(labels[:10]) + (
        f" · 그 밖에 {len(labels) - 10}개" if len(labels) > 10 else ""
    )
    return [extensions.ProfileFact(key="metrics", label="지표", lines=[shown], marks={})]


# --- 부서 홈 ------------------------------------------------------------------------


def home_pins(db: Session, metric: MetricDef) -> list[HomeMetric]:
    return list(db.scalars(select(HomeMetric).where(HomeMetric.metric_id == metric.id)))


def pin_home(
    db: Session,
    user: User,
    metric: MetricDef,
    *,
    workspace: Workspace,
    split: str | None,
    position: int | None,
) -> HomeMetric:
    """부서 홈에 올린다(이미 있으면 나눌 기준 · 자리만 고친다) — **그 부서의 관리자만**, 뷰를
    홈에 올리는 것과 같은 규칙이다. 자리는 그 부서 홈의 뷰와 같은 줄에서 센다."""
    require_owner_edit(db, user, workspace.id, what="부서 홈", code_value=code("METRICS", 45))
    if split and compute.built_of(db, metric).dim(split) is None:
        raise Conflict(
            code("METRICS", 46),
            f"「{split}」 은 이 지표의 기준이 아닙니다 — 정의의 기준 이름 중 하나로 나눕니다.",
        )
    found = db.scalar(
        select(HomeMetric).where(
            HomeMetric.workspace_id == workspace.id, HomeMetric.metric_id == metric.id
        )
    )
    if found is None:
        found = HomeMetric(
            workspace_id=workspace.id,
            metric_id=metric.id,
            home_order=home.next_order(db, workspace.id),
            created_by_id=user.id,
        )
        db.add(found)
    found.split = split or None
    db.flush()
    if position is not None:
        home.place(db, workspace.id, found, position)
    audit.record(
        db,
        action="metric.home",
        actor=user,
        target_table="metric_defs",
        target_id=metric.id,
        target_label=metric.slug,
        workspace_id=workspace.id,
        changes={"on_home": True, "split": found.split, "home_order": found.home_order},
    )
    return found


def _move_home_pins(db: Session, source: uuid.UUID, target: uuid.UUID) -> int:
    """부서 통폐합 — 홈에 올린 지표를 받는 부서 홈으로. 받는 쪽에 같은 지표가 이미 있으면
    옮기지 않고 지운다(부서마다 지표 하나는 한 번만 선다). 자리는 받는 쪽 끝에 붙인다."""
    have = set(
        db.scalars(select(HomeMetric.metric_id).where(HomeMetric.workspace_id == target))
    )
    moved = 0
    for pin in list(db.scalars(select(HomeMetric).where(HomeMetric.workspace_id == source))):
        if pin.metric_id in have:
            db.delete(pin)
        else:
            pin.workspace_id = target
            pin.home_order = home.next_order(db, target)
            have.add(pin.metric_id)
        moved += 1
        db.flush()
    return moved


def workspace_content(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceContent]:
    """부서 통폐합 때 옮길 것 — **홈에 올린 지표.** 빠뜨리면 원본 부서를 지울 때 그 홈의
    지표가 말없이 사라진다(외래키가 CASCADE 다)."""
    count = (
        db.scalar(
            select(func.count())
            .select_from(HomeMetric)
            .where(HomeMetric.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceContent(
            kind="home_metrics", label="홈의 지표", count=int(count), move=_move_home_pins
        )
    ]


def unpin_home(db: Session, user: User, metric: MetricDef, *, workspace: Workspace) -> None:
    require_owner_edit(db, user, workspace.id, what="부서 홈", code_value=code("METRICS", 45))
    found = db.scalar(
        select(HomeMetric).where(
            HomeMetric.workspace_id == workspace.id, HomeMetric.metric_id == metric.id
        )
    )
    if found is None:
        raise NotFound(code("METRICS", 47), "그 부서 홈에 올라가 있지 않은 지표입니다.")
    db.delete(found)
    audit.record(
        db,
        action="metric.home",
        actor=user,
        target_table="metric_defs",
        target_id=metric.id,
        target_label=metric.slug,
        workspace_id=workspace.id,
        changes={"on_home": False},
    )
