"""눈여겨볼 것의 창구(ADR 0032) — `/api/insights`.

읽기는 누구나(보이는 지표의 것만 — `insights.visible_metric_ids`), 「그만 보기」 · 지표마다
끄기 · 지금 훑기는 시스템 관리자만. 개인 토큰은 `ontology:write` 로 처리한다(`app/main.py` —
처음에는 범위 표에 없어 읽기만 됐다, 2026-10-25 ADR 0032 에서 바꿈). 범위가 있어도 사람의
권한이 그대로 막는다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.metrics import alerts, insights
from app.modules.metrics.models import MetricDef, MetricInsight
from app.modules.metrics.schemas import (
    InsightDismissIn,
    InsightOut,
    InsightScanIn,
    InsightScanOut,
    InsightSettingIn,
    InsightSettingOut,
    InsightsOut,
)
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import NotFound, code

router = APIRouter(prefix="/insights", tags=["insights"])

KINDS = tuple(insights.KIND_LABELS)


def _out(row: MetricInsight, metric: MetricDef) -> InsightOut:
    past = metric.insights_scanned_at is not None and row.last_seen_at < (
        metric.insights_scanned_at
    )
    return InsightOut(
        id=row.id,
        metric_slug=metric.slug,
        metric_label=metric.label,
        kind=row.kind,
        kind_label=insights.KIND_LABELS.get(row.kind, row.kind),
        recipe=row.recipe,
        recipe_label=alerts.recipe_label(row.recipe),
        title=row.title,
        detail=dict(row.detail or {}),
        score=row.score,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        past=past,
        dismissed=row.dismissed_at is not None,
        link=insights.link_of(metric.slug, row),
    )


@router.get("", response_model=InsightsOut)
def list_insights(
    metric: str | None = Query(default=None, description="이 지표만(slug)"),
    kind: str | None = Query(default=None, description="change · worse · high · signal"),
    days: int | None = Query(
        default=None, ge=1, le=3650, description="최근 N일에 처음 본 것만"
    ),
    include_past: bool = Query(default=False, description="마지막 훑기에 안 나온 것도"),
    include_dismissed: bool = Query(default=False, description="「그만 보기」 한 것도"),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InsightsOut:
    """**점수 높은 것부터** — 보이는 지표의 것만(그 지표의 셀이 든 부서를 모두 보는 사람)."""
    metrics_q = select(MetricDef).where(MetricDef.is_active.is_(True))
    if metric:
        metrics_q = metrics_q.where(MetricDef.slug == metric)
    metrics = list(db.scalars(metrics_q))
    visible = insights.visible_metric_ids(db, user, metrics)
    by_id = {one.id: one for one in metrics if one.id in visible}
    if not by_id:
        return InsightsOut(items=[], total=0, scanned_at=None)
    stmt = select(MetricInsight).where(MetricInsight.metric_id.in_(list(by_id)))
    if kind:
        stmt = stmt.where(MetricInsight.kind == kind)
    if days:
        stmt = stmt.where(
            MetricInsight.first_seen_at >= datetime.now(UTC) - timedelta(days=days)
        )
    if not include_dismissed:
        stmt = stmt.where(MetricInsight.dismissed_at.is_(None))
    if not include_past:
        # 마지막 훑기에 다시 나온 것만 — 지표마다 훑은 때가 달라 지표와 이어 견준다.
        stmt = stmt.join(MetricDef, MetricDef.id == MetricInsight.metric_id).where(
            MetricInsight.last_seen_at
            >= func.coalesce(MetricDef.insights_scanned_at, func.now())
        )
    # 같은 줄이 여러 지표에 서면(같은 기록을 같은 분모로 센 지표끼리) 점수 높은 쪽 하나만 —
    # 나머지 지표는 `also` 에. 결론은 지표마다 수백이라 다 읽어 가른다.
    rows = db.scalars(
        stmt.order_by(
            MetricInsight.score.desc(),
            MetricInsight.first_seen_at.desc(),
            MetricInsight.id,
        )
    )
    shown: dict[tuple[str, str], InsightOut] = {}
    for row in rows:
        metric_def = by_id[row.metric_id]
        same = shown.get((row.kind, row.title))
        if same is not None:
            if metric_def.label not in same.also and metric_def.slug != same.metric_slug:
                same.also.append(metric_def.label)
            continue
        shown[(row.kind, row.title)] = _out(row, metric_def)
    scanned = [one.insights_scanned_at for one in by_id.values() if one.insights_scanned_at]
    return InsightsOut(
        items=list(shown.values())[:limit],
        total=len(shown),
        scanned_at=max(scanned) if scanned else None,
    )


@router.put("/{insight_id}/dismissed", response_model=InsightOut)
def dismiss(
    insight_id: uuid.UUID,
    payload: InsightDismissIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> InsightOut:
    """「그만 보기」 — 모두에게서 내려간다. 같은 열쇠는 다시 안 뜬다(새 열쇠는 뜬다). 되돌릴 수
    있다(`dismissed=false`)."""
    row = db.get(MetricInsight, insight_id)
    if row is None:
        raise NotFound(code("METRICS", 90), "그 결론이 없습니다.")
    metric = db.get(MetricDef, row.metric_id)
    assert metric is not None
    row.dismissed_at = datetime.now(UTC) if payload.dismissed else None
    row.dismissed_by_id = user.id if payload.dismissed else None
    db.commit()
    return _out(row, metric)


@router.post("/scan", response_model=InsightScanOut, status_code=202)
def scan_now(
    payload: InsightScanIn,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> InsightScanOut:
    """지금 훑기 — 작업으로 넣는다(같은 지표의 대기 작업이 있으면 그것). 평소에는 지표를 다시
    센 뒤 저절로 돈다."""
    stmt = select(MetricDef).where(
        MetricDef.is_active.is_(True), MetricDef.insights_off.is_(False)
    )
    if payload.metric:
        stmt = stmt.where(MetricDef.slug == payload.metric)
    metrics = list(db.scalars(stmt))
    if payload.metric and not metrics:
        raise NotFound(
            code("METRICS", 91), f"훑을 지표가 없습니다(꺼졌거나 없음): {payload.metric}"
        )
    queued = sum(1 for one in metrics if insights.request(db, one) is not None)
    db.commit()
    return InsightScanOut(queued=queued)


@router.put("/metrics/{slug}", response_model=InsightSettingOut)
def set_metric(
    slug: str,
    payload: InsightSettingIn,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> InsightSettingOut:
    """지표마다 훑기를 끄고 켠다 — 결론이 잡음뿐인 지표. 끈 지표의 결론은 「지남」 으로
    남는다."""
    metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
    if metric is None:
        raise NotFound(code("METRICS", 92), f"없는 지표입니다: {slug}")
    metric.insights_off = payload.off
    db.commit()
    return InsightSettingOut(slug=metric.slug, off=metric.insights_off)
