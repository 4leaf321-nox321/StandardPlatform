"""뜻으로 찾기의 관리 — `/api/semantic`(ADR 0052). **시스템 관리자만.**

어느 타입을 켤지 정하고, 계산 차례를 넣는다. 찾기 자체는 통합
검색(`/api/search?meaning=true`)이 한다 — 찾는 길이 둘이면 사람이 어느 쪽을 써야 하는지 물어야
한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.semantic import services
from app.modules.semantic.schemas import (
    SemanticFieldOut,
    SemanticJobOut,
    SemanticOut,
    SemanticTargetsIn,
    SemanticTypeOut,
)
from app.shared import embedding
from app.shared.auth import require_system_admin

router = APIRouter(prefix="/semantic", tags=["semantic"])


def _out(db: Session) -> SemanticOut:
    ready = embedding.readiness(db)
    job = services.last_job(db)
    return SemanticOut(
        state=ready.state,
        problem=ready.problem,
        model=get_settings().embedding_model,
        kb_per_object=services.KB_PER_OBJECT,
        min_similarity=services.MIN_SIMILARITY,
        per_second=services.rate(db),
        types=[
            SemanticTypeOut(
                type_slug=one.object_type.slug,
                type_label=one.object_type.label,
                icon=one.object_type.icon,
                usage=one.object_type.usage,
                objects=one.objects,
                synced=one.synced,
                pieces=one.pieces,
                pending=one.pending,
                enabled=one.enabled,
                fields=one.fields,
                options=[
                    SemanticFieldOut(
                        key=field.key, label=field.label, data_type=field.data_type
                    )
                    for field in one.options
                ],
                why_not=one.why_not,
                estimate_pieces=one.estimate_pieces,
                estimate_bytes=one.estimate_pieces * services.KB_PER_OBJECT * 1024,
            )
            for one in services.overview(db)
        ],
        job=SemanticJobOut(
            id=job.id,
            status=job.status,
            created_at=job.created_at,
            finished_at=job.finished_at,
            error=job.error,
            result=job.result,
            progress=job.progress or {},
        )
        if job is not None
        else None,
    )


@router.get("", response_model=SemanticOut)
def state(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> SemanticOut:
    """타입마다 객체 수 · 바꾼 수 · 남은 수 · 켰나, 그리고 가장 최근의 계산."""
    return _out(db)


@router.put("/targets", response_model=SemanticOut)
def put_targets(
    payload: SemanticTargetsIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> SemanticOut:
    """대상을 통째로 정한다 — 바뀌었으면 계산을 곧장 넣는다(대상은 채우고 뺀 것은 지운다).

    기록은 칸을 골라야 한다(`SEMANTIC-0004`) — 건수가 많아 전부는 서버가 감당하지 못한다. 고를
    수 없는 칸은 `SEMANTIC-0005`, 원 표 타입은 `SEMANTIC-0002`. 칸을 바꾼 타입은 전부 다시
    맞춘다.
    """
    if services.set_targets(db, user, {one.type_slug: one.fields for one in payload.targets}):
        services.enqueue(db, full=False, reason="targets", user=user)
    db.commit()
    return _out(db)


@router.post("/sync", response_model=SemanticOut)
def sync(
    user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> SemanticOut:
    """지금 전부를 다시 맞춘다(해시로 — 바뀐 것만 다시 바꾼다).

    이미 기다리거나 돌면 그대로 둔다."""
    services.enqueue(db, full=True, reason="manual", user=user)
    db.commit()
    return _out(db)
