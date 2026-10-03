"""경보 경로(ADR 0016) — **내 경보만** 보고 고친다(로그인한 누구나, 자기 것).

남의 경보는 없는 것처럼 404 다 — 누가 무엇을 지켜보는지도 그 사람의 일이다. 만들 때 한 번
돌려 지금 있는 결론을 「처음부터 있던 것」 으로 적는다 — 인자가 틀렸으면 그때 분석의 422 가
그대로 난다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.metrics import alerts, query, services
from app.modules.metrics.models import MetricAlert, MetricAlertEvent, MetricDef
from app.modules.metrics.schemas import (
    AlertCheckOut,
    AlertEventOut,
    AlertFindingOut,
    AlertIn,
    AlertOut,
    AlertPatch,
    AlertSavedOut,
)
from app.shared.auth import current_user
from app.shared.errors import NotFound, code

router = APIRouter()

EVENTS_MAX = 200


def _out(alert: MetricAlert, metric: MetricDef, events: int) -> AlertOut:
    return AlertOut(
        id=alert.id,
        metric=metric.slug,
        metric_label=metric.label,
        name=alert.name,
        recipe=alert.recipe,
        recipe_label=alerts.recipe_label(alert.recipe),
        params=dict(alert.params or {}),
        is_active=alert.is_active,
        last_checked_at=alert.last_checked_at,
        last_status=alert.last_status,
        last_error=alert.last_error,
        events=events,
        created_at=alert.created_at,
        link=alerts.link_of(metric.slug, alert.recipe, alert.params or {}),
    )


def _event_out(row: MetricAlertEvent, alert: MetricAlert, metric: MetricDef) -> AlertEventOut:
    return AlertEventOut(
        id=row.id,
        alert_id=alert.id,
        alert_name=alert.name,
        metric=metric.slug,
        metric_label=metric.label,
        recipe=alert.recipe,
        key=row.key,
        title=row.title,
        detail=dict(row.detail or {}),
        baseline=row.baseline,
        run_id=row.run_id,
        created_at=row.created_at,
        link=alerts.event_link(
            metric.slug, alert.recipe, alert.params or {}, row.detail or {}
        ),
    )


def _mine(db: Session, user: User, metric: MetricDef, alert_id: uuid.UUID) -> MetricAlert:
    alert = db.get(MetricAlert, alert_id)
    if alert is None or alert.owner_id != user.id or alert.metric_id != metric.id:
        raise NotFound(code("METRICS", 44), "경보를 찾을 수 없습니다.")
    return alert


@router.get("/alerts/events", response_model=list[AlertEventOut])
def my_events(
    limit: int = Query(default=50, ge=1, le=EVENTS_MAX),
    baseline: bool = Query(default=False, description="처음 확인에서 본 것(알리지 않은 것)도"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AlertEventOut]:
    """내 경보들의 최근 발생 — 새것부터. 「요즘 무엇이 울렸나」."""
    stmt = (
        select(MetricAlertEvent, MetricAlert, MetricDef)
        .join(MetricAlert, MetricAlert.id == MetricAlertEvent.alert_id)
        .join(MetricDef, MetricDef.id == MetricAlert.metric_id)
        .where(MetricAlert.owner_id == user.id)
        .order_by(MetricAlertEvent.created_at.desc(), MetricAlertEvent.key)
        .limit(limit)
    )
    if not baseline:
        stmt = stmt.where(MetricAlertEvent.baseline.is_(False))
    return [_event_out(row, alert, metric) for row, alert, metric in db.execute(stmt).tuples()]


@router.get("/{slug}/alerts", response_model=list[AlertOut])
def list_alerts(
    slug: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[AlertOut]:
    """이 지표에 건 내 경보."""
    metric = services.get(db, slug)
    rows = list(
        db.scalars(
            select(MetricAlert)
            .where(MetricAlert.metric_id == metric.id, MetricAlert.owner_id == user.id)
            .order_by(MetricAlert.created_at)
        )
    )
    counts = alerts.event_counts(db, [one.id for one in rows])
    return [_out(one, metric, counts.get(one.id, 0)) for one in rows]


@router.post("/{slug}/alerts", response_model=AlertSavedOut, status_code=201)
def create_alert(
    slug: str,
    payload: AlertIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AlertSavedOut:
    """경보 하나 — 한 번 돌려 지금 있는 결론을 「처음부터 있던 것」 으로 적고 돌려준다(알리지
    않는다). 다음 계산부터 처음 보는 결론이 알림이 된다."""
    query.snapshot(db)
    metric = services.get(db, slug)
    values = alerts.clean(payload.recipe, payload.params)
    outcome = alerts.evaluate(db, user, metric, payload.recipe, values)
    alert = MetricAlert(
        metric_id=metric.id,
        owner_id=user.id,
        name=payload.name.strip(),
        recipe=payload.recipe,
        params=values,
    )
    db.add(alert)
    db.flush()
    added = alerts.record(db, alert, metric, outcome, notify=False)
    db.commit()
    db.refresh(alert)
    return AlertSavedOut(
        **_out(alert, metric, len(added)).model_dump(),
        baseline=AlertCheckOut(
            run_id=outcome.run_id,
            findings=[
                AlertFindingOut(key=one.key, title=one.title, detail=one.detail, new=False)
                for one in outcome.findings
            ],
            notes=outcome.notes,
        ),
    )


@router.patch("/{slug}/alerts/{alert_id}", response_model=AlertOut)
def update_alert(
    slug: str,
    alert_id: uuid.UUID,
    payload: AlertPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AlertOut:
    """이름 · 켜고 끄기만. **인자는 못 바꾼다** — 바꾸면 지난 발생이 다른 물음의 것이 된다.
    인자를 바꾸려면 분석 탭에서 그 인자로 열어 새로 저장한다."""
    metric = services.get(db, slug)
    alert = _mine(db, user, metric, alert_id)
    if payload.name is not None:
        alert.name = payload.name.strip()
    if payload.is_active is not None:
        alert.is_active = payload.is_active
    db.commit()
    db.refresh(alert)
    return _out(alert, metric, alerts.event_counts(db, [alert.id]).get(alert.id, 0))


@router.delete("/{slug}/alerts/{alert_id}", status_code=204)
def delete_alert(
    slug: str,
    alert_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """경보와 그 발생 기록을 함께 지운다."""
    metric = services.get(db, slug)
    db.delete(_mine(db, user, metric, alert_id))
    db.commit()


@router.post("/{slug}/alerts/{alert_id}/check", response_model=AlertCheckOut)
def check_alert(
    slug: str,
    alert_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AlertCheckOut:
    """지금 확인 — **적지도 알리지도 않는다.** 지금 셀에서 보이는 결론과, 그중 이 경보가 아직
    안 본 것(`new` — 다음 계산 뒤 알림이 될 것)."""
    query.snapshot(db)
    metric = services.get(db, slug)
    alert = _mine(db, user, metric, alert_id)
    outcome = alerts.evaluate(db, user, metric, alert.recipe, alert.params or {})
    new = {
        one.key for one in alerts.fresh(outcome, alerts.seen_keys(db, alert.id), alert.recipe)
    }
    return AlertCheckOut(
        run_id=outcome.run_id,
        findings=[
            AlertFindingOut(
                key=one.key, title=one.title, detail=one.detail, new=one.key in new
            )
            for one in outcome.findings
        ],
        notes=outcome.notes,
    )


@router.get("/{slug}/alerts/{alert_id}/events", response_model=list[AlertEventOut])
def alert_events(
    slug: str,
    alert_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=EVENTS_MAX),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AlertEventOut]:
    """이 경보의 발생 — 새것부터, 처음부터 있던 것도 함께(표시가 붙는다)."""
    metric = services.get(db, slug)
    alert = _mine(db, user, metric, alert_id)
    rows = db.scalars(
        select(MetricAlertEvent)
        .where(MetricAlertEvent.alert_id == alert.id)
        .order_by(MetricAlertEvent.created_at.desc(), MetricAlertEvent.key)
        .limit(limit)
    )
    return [_event_out(row, alert, metric) for row in rows]
