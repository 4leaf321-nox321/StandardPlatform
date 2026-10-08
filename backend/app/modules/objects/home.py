"""부서 홈의 **한 줄** — 저장된 뷰와 지표가 같은 자리 값(`home_order`)으로 선다.

뷰는 `saved_views.home_order`, 지표는 `home_metrics.home_order` 에 자리를 둔다. 따로 매기면
둘이 같은 자리 값을 가져 「위로」 가 안 움직인 것처럼 보이고, 새로 올린 것이 엉뚱한 곳에 선다 —
그래서 자리를 매기는 일은 여기 한 곳에서 **둘을 섞어 통째로** 한다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.metrics.models import HomeMetric, MetricDef
from app.modules.objects.models import SavedView


def items(db: Session, workspace_id: uuid.UUID) -> list[Any]:
    """그 부서 홈의 것들 — 자리 순(같으면 뷰 먼저, 이름 순)."""
    views = list(
        db.scalars(
            select(SavedView).where(
                SavedView.workspace_id == workspace_id, SavedView.home_order.is_not(None)
            )
        )
    )
    pins = list(db.scalars(select(HomeMetric).where(HomeMetric.workspace_id == workspace_id)))
    labels = {
        one.id: one.label
        for one in db.scalars(
            select(MetricDef).where(MetricDef.id.in_([pin.metric_id for pin in pins]))
        )
    }

    def key(one: Any) -> tuple[int, int, str]:
        if isinstance(one, SavedView):
            return (one.home_order or 0, 0, one.name)
        return (one.home_order or 0, 1, labels.get(one.metric_id, ""))

    return sorted([*views, *pins], key=key)


def next_order(db: Session, workspace_id: uuid.UUID) -> int:
    """맨 끝 자리."""
    found = items(db, workspace_id)
    return (max(one.home_order or 0 for one in found) + 1) if found else 0


def place(db: Session, workspace_id: uuid.UUID, moving: Any, position: int) -> None:
    """`moving` 을 `position` 째 자리로 — **그 부서 홈을 통째로 다시 매긴다.** 두 값만 맞바꾸면
    옛 자료에 같은 자리 값이 여럿일 때 순서가 안 바뀐 것처럼 보인다."""
    others = [one for one in items(db, workspace_id) if one is not moving]
    index = max(0, min(position, len(others)))
    others.insert(index, moving)
    for order, one in enumerate(others):
        one.home_order = order
