"""분석 경로 — `/api/metrics/{slug}/analysis/<레시피>`. 읽기다(로그인한 누구나, 보이는 것만).

모든 경로가 먼저 `query.snapshot` 으로 한 스냅샷을 잡는다 — 분석은 여러 번 읽는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.metrics import compute, params, query, services
from app.modules.metrics.recipes import pareto
from app.modules.metrics.recipes.schemas import ParetoOut
from app.shared.auth import current_user

router = APIRouter(prefix="/{slug}/analysis")


@router.get("/pareto", response_model=ParetoOut)
def pareto_analysis(
    slug: str,
    request: Request,
    dim: str = Query(description="몫을 볼 기준 이름"),
    top: int = Query(
        default=30, ge=1, le=200, description="줄로 보일 값 수 — 나머지는 「그 밖」"
    ),
    include_empty: bool = Query(default=False, description="값이 빈 기록을 몫에 넣을지"),
    by_period: bool = Query(default=False, description="기간마다의 집중도 추이"),
    period_from: str | None = Query(default=None),
    period_to: str | None = Query(default=None, description="이 날 **앞까지**"),
    cohort_from: str | None = Query(default=None),
    cohort_to: str | None = Query(default=None),
    compact: bool = Query(default=False, description="줄을 10개로 줄이고 추이를 뺀다(MCP)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ParetoOut:
    """⑦ 파레토 · 집중도 — 기준 하나의 값별 몫 · 누적 · ABC, HHI · 유효 개수 · 지니 · CR.
    거르기는 `d.<기준>=<값>`."""
    query.snapshot(db)
    metric = services.get(db, slug)
    built = compute.built_of(db, metric)
    ask = params.ask_from_request(
        request,
        period_from=period_from,
        period_to=period_to,
        cohort_from=cohort_from,
        cohort_to=cohort_to,
    )
    return pareto.run(
        db,
        user,
        metric,
        built,
        ask,
        dim=dim,
        top=top,
        include_empty=include_empty,
        by_period=by_period,
        compact=compact,
    )
