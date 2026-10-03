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
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.metrics import alerts, compute, query, recipes, schemas
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef, MetricRun, MetricValue
from app.modules.objects.summary import METRIC_LABELS
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import require_slug
from app.shared import audit, extensions
from app.shared.errors import AppError, Conflict, NotFound, code

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


def pending_recompute(db: Session, slug: str) -> Job | None:
    return job_services.pending_for(db, JOB_KIND, slugs=[slug])


def enqueue_recompute(
    db: Session, metric: MetricDef, *, user: User | None, reason: str
) -> Job:
    """작업 하나 — 같은 지표의 작업이 줄에 있으면 그것을. 부르는 쪽이 커밋한다."""
    existing = pending_recompute(db, metric.slug)
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
    """이 타입을 원천으로 쓰는 켜진 지표마다 작업 하나 — 적재 작업의 본문이 부른다."""
    out: list[Job] = []
    for metric in db.scalars(
        select(MetricDef)
        .where(MetricDef.source_type_id == type_id, MetricDef.is_active.is_(True))
        .order_by(MetricDef.slug)
    ):
        if pending_recompute(db, metric.slug) is None:
            out.append(enqueue_recompute(db, metric, user=None, reason=reason))
    return out


def due(db: Session, now: datetime | None = None) -> list[MetricDef]:
    """타이머가 돌릴 차례 — 켜져 있고, 주기가 0 이 아니고, 마지막 계산에서 그만큼 지난 것."""
    now = now or datetime.now(UTC)
    out: list[MetricDef] = []
    for metric in db.scalars(
        select(MetricDef)
        .where(MetricDef.is_active.is_(True), MetricDef.interval_hours > 0)
        .order_by(MetricDef.slug)
    ):
        if (
            metric.last_run_at is None
            or metric.last_run_at + timedelta(hours=metric.interval_hours) <= now
        ):
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
) -> dict[str, Any]:
    """지표들을 차례로 — **지표마다 커밋.** 하나가 실패해도 나머지는 돌고, 끝에 실패를 모아
    작업을 실패로 만든다(성공한 것의 새 값은 이미 들어가 있다)."""
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
            run = compute.run_one(db, metric, job_id=job_id, progress=progress)
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
    if metric.current_run_id is None:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(MetricValue)
            .where(
                MetricValue.metric_id == metric.id, MetricValue.run_id == metric.current_run_id
            )
        )
        or 0
    )
