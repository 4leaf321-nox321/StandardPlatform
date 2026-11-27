"""검토함 라우터 — **사람이 판단할 일을 한 곳에서, 한꺼번에**(ADR 0027).

    GET  /api/review/kinds                 볼 수 있는 종류(모으지 않는다 — 화면이 종류마다)
    GET  /api/review/items                 모아 거른 한 쪽(종류 · 타입 · 부서 · 확신도 · 추천)
    POST /api/review/plan                  미리 보기(고른 것 · 추천대로 모두 · 거른 전부)
    POST /api/review/apply                 미리 본 그대로(지문) — 크면 작업(202)
    POST /api/review/bulk                  「전부 작업으로」 — 상한 밖까지 미리 보는 작업(202),
                                           적용은 `POST /api/jobs/{id}/apply`
    GET  /api/review/auto                  자동 확인 — 종류마다 켜졌나 · 근거
    PUT  /api/review/auto/{kind}           자동 확인 켜고 끄기(시스템 관리자)
    GET  /api/review/decisions             결정 기록(추천을 따랐나)
    GET  /api/review/decisions/accuracy    추천이 얼마나 맞나 — 종류 · 확신도 구간마다
    GET  /api/review/batches               최근 적용한 묶음
    POST /api/review/batches/{id}/undo     묶음 복원 — `apply=false` 면 미리 보기

부서 관리자 이상(데이터 품질 · 별칭 후보와 같은 문턱). 항목마다의 권한은 원래 기능의 것이다 —
보이는 것만 모이고(제공자가 `visible_owner_clause` 로 읽는다), 못 고치는 것은 미리 보기에서
「막힘」 으로 서고 적용에서 빠진다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.review import registry, services
from app.modules.review.registry import Item
from app.modules.review.schemas import (
    ReviewAccuracyLineOut,
    ReviewAccuracyOut,
    ReviewApplyOut,
    ReviewApplyRequest,
    ReviewAutoIn,
    ReviewAutoOut,
    ReviewBatchOut,
    ReviewBulkOut,
    ReviewBulkRequest,
    ReviewChoiceOut,
    ReviewDecisionIn,
    ReviewDecisionOut,
    ReviewDecisionPage,
    ReviewFacetOut,
    ReviewFilterIn,
    ReviewItemOut,
    ReviewKindInfoOut,
    ReviewKindOut,
    ReviewKindPlanOut,
    ReviewOutcomeOut,
    ReviewPageOut,
    ReviewPlanOut,
    ReviewPlanRequest,
    ReviewUndoLineOut,
    ReviewUndoOut,
    ReviewUndoRequest,
)
from app.shared.auth import current_user
from app.shared.errors import AppError, code
from app.shared.pagination import clamp_limit

router = APIRouter(prefix="/review", tags=["review"])


def _by_token(request: Request) -> bool:
    """토큰(기계 자격)이 보낸 요청인가 — 사람 세션에는 범위가 없다(`shared/auth.py`). 사람만
    정하는 종류(`Provider.human_only`)를 가르는 데 쓴다."""
    return getattr(request.state, "token_scopes", None) is not None


def _query(filters: ReviewFilterIn) -> services.Query:
    return services.Query(
        kind=filters.kind or None,
        type_slug=filters.type if filters.type is not None else None,
        workspace=filters.workspace or None,
        min_confidence=filters.min_confidence,
        max_confidence=filters.max_confidence,
        actionable=filters.actionable,
        recommendation=filters.recommendation,
    )


def _items_out(db: Session, items: list[Item]) -> list[ReviewItemOut]:
    places = services.workspace_labels(
        db, {one.workspace_id for one in items if one.workspace_id is not None}
    )
    out: list[ReviewItemOut] = []
    for one in items:
        provider = registry.get(one.kind)
        place = places.get(one.workspace_id) if one.workspace_id else None
        recommended = next((c for c in one.choices if c.value == one.recommended), None)
        out.append(
            ReviewItemOut(
                kind=one.kind,
                kind_label=provider.label if provider else one.kind,
                id=one.id,
                question=one.question,
                evidence=[line for line in one.evidence if line],
                choices=[ReviewChoiceOut(**vars(c)) for c in one.choices],
                recommended=one.recommended,
                recommended_label=recommended.label if recommended else None,
                confidence=one.confidence,
                reason=one.reason,
                type_slug=one.type_slug,
                type_label=one.type_label,
                workspace_slug=place[0] if place else None,
                workspace_name=place[1] if place else None,
                rows=one.rows,
                group=one.group,
                group_label=one.group_label,
                blocked=one.blocked,
                link=one.link,
                ignorable=one.ignorable,
                actionable=bool(provider and provider.check is not None),
                members=list(one.payload.get("members") or [])
                if one.id.startswith("group:")
                else [],
            )
        )
    return out


def _facets(
    db: Session, items: list[Item]
) -> tuple[list[ReviewFacetOut], list[ReviewFacetOut]]:
    types: dict[str, tuple[str, int]] = {}
    places: dict[uuid.UUID | None, int] = {}
    for one in items:
        if one.type_slug:
            label, count = types.get(one.type_slug, (one.type_label, 0))
            types[one.type_slug] = (label, count + 1)
        places[one.workspace_id] = places.get(one.workspace_id, 0) + 1
    names = services.workspace_labels(db, {one for one in places if one is not None})
    workspaces = [
        ReviewFacetOut(
            value=names[key][0] if key is not None else services.GLOBAL,
            label=names[key][1] if key is not None else "전역",
            count=count,
        )
        for key, count in places.items()
        if key is None or key in names
    ]
    return (
        sorted(
            (
                ReviewFacetOut(value=slug, label=label, count=count)
                for slug, (label, count) in types.items()
            ),
            key=lambda one: (-one.count, one.label),
        ),
        sorted(workspaces, key=lambda one: (-one.count, one.label)),
    )


@router.get("/kinds", response_model=list[ReviewKindInfoOut])
def review_kinds(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ReviewKindInfoOut]:
    """**볼 수 있는 종류** — 모으지 않고 이름 · 설명만(ADR 0075). 화면은 이것을 먼저 받아
    종류마다 따로 `GET /items?kind=` 를 불러 먼저 끝난 종류부터 그린다 — 한 종류가 느려도
    나머지가 먼저 선다."""
    services.require_viewer(db, user)
    return [
        ReviewKindInfoOut(
            kind=one.kind,
            label=one.label,
            description=one.description,
            link=one.link,
            actionable=one.check is not None,
            undo_note=one.undo_note,
        )
        for one in registry.providers()
        if one.can_view(db, user)
    ]


@router.get("/items", response_model=ReviewPageOut)
def review_items(
    kind: str | None = Query(default=None, description="종류 하나만 — `/items` 의 `kinds`"),
    type_slug: str | None = Query(
        default=None, alias="type", description="타입 slug(별칭 후보는 못 찾은 자리)"
    ),
    workspace: str | None = Query(
        default=None, description="부서 slug — `@global` 이면 전역(부서 없음)만"
    ),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    max_confidence: float | None = Query(default=None, ge=0, le=1.01),
    actionable: bool = Query(
        default=False, description="처리할 수 있는 것만(막힌 것 · 링크만 빼고)"
    ),
    recommendation: str | None = Query(
        default=None,
        pattern="^(with|without)$",
        description="`with` 추천이 있는 것만 · `without` 추천이 없는 것만(ADR 0075)",
    ),
    group: bool = Query(default=False, description="같은 것끼리 묶기"),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewPageOut:
    """**사람이 판단할 일** — 종류마다 원래 자리에서 읽어 한 줄(물음 · 근거 · 후보 · 추천 ·
    확신도)로. 종류마다 묶여 선다(`kinds` 의 차례). 수는 `kinds[].total`(원래 자리에 있는 수)과
    `matched`(거르기에 맞는 수). 0건이면 `notes` · `kinds[].notes` 가 까닭을 말한다.

    **종류마다 시간이 있다**(ADR 0073) — 한 종류가 느려도 나머지는 선다. 시간 안에 다 못 읽은
    종류는 `kinds[].late`(앞의 것만 읽었거나 건너뜀 — 「없다」 가 아니다), 까닭은 그 종류의
    `notes`. 종류 하나만(`kind`) 보면 그 종류가 시간을 다 쓴다."""
    services.require_viewer(db, user)
    query = services.Query(
        kind=kind,
        type_slug=type_slug,
        workspace=workspace,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        actionable=actionable,
        recommendation=recommendation,
    )
    listed = services.collect(db, user, query)
    items = services.grouped(listed.items) if group else listed.items
    capped = clamp_limit(limit)
    types, places = _facets(db, listed.items)
    notes = list(listed.notes)
    late = [one.provider.label for one in listed.kinds if one.late]
    if not items:
        if not listed.kinds:
            notes.append("볼 수 있는 종류가 없습니다.")
        elif late:
            notes.append(
                f"시간 안에 다 읽지 못한 종류가 있습니다({', '.join(late)}) — 없는 것이 "
                "아닙니다. 그 종류만 골라 다시 보세요."
            )
        elif any(one.total for one in listed.kinds):
            notes.append(
                "거르기에 맞는 항목이 없습니다 — 거르기(타입 · 부서 · 확신도)를 넓혀 보세요."
            )
        else:
            notes.append("판단을 기다리는 일이 없습니다.")
    return ReviewPageOut(
        kinds=[
            ReviewKindOut(
                kind=one.provider.kind,
                label=one.provider.label,
                description=one.provider.description,
                link=one.provider.link,
                total=one.total,
                matched=one.matched,
                scanned=one.scanned,
                actionable=one.provider.check is not None,
                undo_note=one.provider.undo_note,
                notes=one.notes,
                late=one.late,
                seconds=one.seconds,
            )
            for one in listed.kinds
        ],
        items=_items_out(db, items[offset : offset + capped]),
        total=len(items),
        limit=capped,
        offset=offset,
        types=types,
        workspaces=places,
        notes=notes,
    )


def _decisions(raw: list[ReviewDecisionIn]) -> list[services.Decision]:
    return [
        services.Decision(
            kind=one.kind, item_id=one.item_id, action=one.action, value=one.value
        )
        for one in raw
    ]


def _outcome_out(one: services.Outcome) -> ReviewOutcomeOut:
    return ReviewOutcomeOut(**services.outcome_dict(one))


@router.post("/plan", response_model=ReviewPlanOut)
def review_plan(
    payload: ReviewPlanRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewPlanOut:
    """**미리 보기** — 무엇이 무엇으로 되나. 아무것도 안 바꾼다.

    - `mode="decisions"` — 고른 것(`decisions`: 추천대로 `accept` · 다른 것으로 `choose` +
      `value` · 무시 `ignore`).
    - `mode="recommended"` — **「추천대로 모두」**: `filters` 에 맞고 추천이 있고 확신도가
      `threshold`(기본 0.9) 이상인 것. 문턱 아래 · 추천 없음은 `skipped` 로 센다.
    - `mode="all"` — 거른 전부에 `action`(추천대로 · 무시) 하나.

    막히는 것(다른 객체가 이미 쓰는 별칭 · 남의 부서 · 병합 묶음에 못 고치는 것)은 `blocked`
    로 따로 세고 적용에서 빠진다. 적용은 이 응답의 `decisions` 와 `fingerprint` 를 그대로
    `POST /api/review/apply` 에 — 그 사이 바뀌면 거절된다."""
    services.require_viewer(db, user)
    planned = services.preview(
        db,
        user,
        mode=payload.mode,
        decisions=_decisions(payload.decisions),
        query=_query(payload.filters),
        threshold=payload.threshold,
        action=payload.action,
        by_token=_by_token(request),
    )
    by_kind: dict[str, ReviewKindPlanOut] = {}
    for one in planned.outcomes:
        provider = registry.get(one.decision.kind)
        line = by_kind.setdefault(
            one.decision.kind,
            ReviewKindPlanOut(
                kind=one.decision.kind,
                label=provider.label if provider else one.decision.kind,
                apply=0,
                blocked=0,
                rows=0,
                undoable=True,
                undo_note=provider.undo_note if provider else "",
            ),
        )
        if one.status == "apply":
            line.apply += 1
            line.rows += one.rows
            line.undoable = line.undoable and one.undoable
        else:
            line.blocked += 1
    shown = sorted(planned.outcomes, key=lambda one: 0 if one.status == "blocked" else 1)
    notes = list(planned.notes)
    if planned.as_job:
        notes.append(
            "큰 묶음이라 적용하면 작업이 됩니다 — 화면을 닫아도 계속되고, 작업 화면에서 "
            "진행을 봅니다."
        )
    return ReviewPlanOut(
        fingerprint=planned.fingerprint,
        mode=payload.mode,
        threshold=payload.threshold if payload.mode == "recommended" else None,
        decisions=[ReviewDecisionIn.model_validate(asdict(one)) for one in planned.decisions],
        counts=planned.counts,
        by_kind=list(by_kind.values()),
        outcomes=[_outcome_out(one) for one in shown[: services.SHOWN_OUTCOMES]],
        outcomes_omitted=max(0, len(shown) - services.SHOWN_OUTCOMES),
        skipped=planned.skipped,
        rows=planned.rows,
        as_job=planned.as_job,
        notes=notes,
        unread=planned.unread,
    )


@router.post("/apply", response_model=ReviewApplyOut)
def review_apply(
    payload: ReviewApplyRequest,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewApplyOut:
    """**미리 본 그대로 적용** — 미리 보기의 `decisions` · `fingerprint` 를 그대로. 그 사이
    바뀐 것이 있으면 409(아무것도 안 했다). 결정마다 원래 기능으로 처리하고 결정 기록 한 줄을
    남긴다(하나가 막혀도 나머지는 간다 — `refused`).

    큰 묶음(결정 100건 · 줄 5,000 넘음, 가리키는 기록이 많은 병합)은 **작업으로** 간다 — 202 와
    `job_id`. 되돌리기는 `POST /api/review/batches/{batch_id}/undo`."""
    services.require_viewer(db, user)
    if not payload.decisions:
        raise AppError(code("REVIEW", 4), "적용할 결정이 없습니다.", status=422)
    decisions = _decisions(payload.decisions)
    by_token = _by_token(request)
    planned = services.plan(db, user, decisions, by_token=by_token)
    # 작업에 넣기 전에도 본다 — 다른 계획을 작업으로 보내 놓고 실패를 늦게 알리지 않게.
    services.require_same(planned, payload.fingerprint)
    if services.needs_job(planned):
        job = services.enqueue(
            db,
            user,
            decisions,
            payload.fingerprint,
            mode=payload.mode,
            threshold=payload.threshold,
            by_token=by_token,
        )
        response.status_code = 202
        return ReviewApplyOut(
            applied=False,
            batch_id=None,
            job_id=job.id,
            counts={"queued": len(decisions)},
            outcomes=[],
            undoable=0,
        )
    result = services.apply(
        db,
        user,
        decisions,
        payload.fingerprint,
        mode=payload.mode,
        threshold=payload.threshold,
        planned=planned,
        by_token=by_token,
    )
    return ReviewApplyOut(
        applied=True,
        batch_id=result.batch_id,
        job_id=None,
        counts=result.counts,
        outcomes=[_outcome_out(one) for one in result.outcomes],
        undoable=sum(1 for one in result.outcomes if one.status == "done" and one.undoable),
    )


@router.get("/decisions", response_model=ReviewDecisionPage)
def review_decisions(
    kind: str | None = Query(default=None),
    batch: uuid.UUID | None = Query(default=None, description="묶음 하나만"),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewDecisionPage:
    """**결정 기록** — 무엇을 누가 언제 어떻게 정했나, 추천을 따랐나. 내 것과 내가 관리하는
    부서의 것(시스템 관리자는 전부)."""
    services.require_viewer(db, user)
    capped = clamp_limit(limit)
    rows, total = services.decisions_page(
        db, user, kind=kind, batch_id=batch, limit=capped, offset=offset
    )
    names = services.user_names(db, {one.decided_by_id for one in rows if one.decided_by_id})
    return ReviewDecisionPage(
        items=[
            ReviewDecisionOut(
                id=one.id,
                batch_id=one.batch_id,
                kind=one.kind,
                kind_label=_kind_label(one.kind),
                item_id=one.item_id,
                question=one.question,
                action=one.action,
                choice=one.choice,
                choice_label=one.choice_label,
                recommended=one.recommended,
                confidence=one.confidence,
                followed=one.followed,
                mode=one.mode,
                threshold=one.threshold,
                status=one.status,
                message=one.message,
                rows=one.rows,
                undoable=one.undoable and one.undone_at is None,
                undone_at=one.undone_at,
                type_slug=one.type_slug,
                decided_by_name=names.get(one.decided_by_id) if one.decided_by_id else None,
                decided_at=one.decided_at,
            )
            for one in rows
        ],
        total=total,
        limit=capped,
        offset=offset,
    )


def _kind_label(kind: str) -> str:
    provider = registry.get(kind)
    return provider.label if provider else kind


def _rate(decided: int, followed: int) -> float | None:
    return round(followed / decided, 3) if decided else None


@router.get("/decisions/accuracy", response_model=ReviewAccuracyOut)
def review_accuracy(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> ReviewAccuracyOut:
    """**추천이 얼마나 맞나** — 처리한 결정 중 추천이 있던 것에서 사람이 추천대로 한 몫.
    종류마다, 그리고 확신도 구간마다(「0.9 이상은 믿어도 되나」). 복원한 결정은 따른 것으로
    세지 않는다."""
    services.require_viewer(db, user)
    kinds, bands = services.accuracy(db, user)
    return ReviewAccuracyOut(
        kinds=[
            ReviewAccuracyLineOut(
                kind=one.kind,
                label=one.label,
                decided=one.decided,
                recommended=one.recommended,
                followed=one.followed,
                rate=_rate(one.recommended, one.followed),
            )
            for one in kinds
        ],
        bands=[
            ReviewAccuracyLineOut(
                kind="",
                label=one.label,
                decided=one.decided,
                recommended=one.recommended,
                followed=one.followed,
                rate=_rate(one.recommended, one.followed),
            )
            for one in bands
        ],
    )


@router.get("/batches", response_model=list[ReviewBatchOut])
def review_batches(
    limit: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ReviewBatchOut]:
    """최근 적용한 묶음 — 복원할 수 있는 것이 몇이나 남았나."""
    services.require_viewer(db, user)
    found = services.batches(db, user, limit=min(clamp_limit(limit), 50))
    names = services.user_names(db, {one.decided_by_id for one in found if one.decided_by_id})
    return [
        ReviewBatchOut(
            batch_id=one.batch_id,
            decided_at=one.decided_at,
            decided_by_name=names.get(one.decided_by_id) if one.decided_by_id else None,
            mode=one.mode,
            count=one.count,
            done=one.done,
            refused=one.refused,
            undoable=one.undoable,
            undone=one.undone,
            kinds=[_kind_label(kind) for kind in one.kinds],
        )
        for one in found
    ]


@router.post("/batches/{batch_id}/undo", response_model=ReviewUndoOut)
def review_undo(
    batch_id: uuid.UUID,
    payload: ReviewUndoRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewUndoOut:
    """**묶음 복원** — `apply=false`(기본)면 무엇이 복원되나만. 종류마다 원래 기능의 되돌리기로
    간다(지워진 것을 가리키는 칸은 그 묶음의 기록에서 그때 값으로, 별칭은 빼거나 검수 대기로,
    별칭 후보는 대기로). **병합은 복원이 없다** — 그 줄은 「막힘」 으로 서고 까닭을 말한다.
    그 뒤에 누가 또 고친 줄은 덮어쓰지 않는다."""
    services.require_viewer(db, user)
    lines = services.undo_batch(
        db, user, batch_id, apply=payload.apply, by_token=_by_token(request)
    )
    counts: dict[str, Any] = {}
    for line in lines:
        counts[line.status] = counts.get(line.status, 0) + 1
    return ReviewUndoOut(
        applied=payload.apply,
        batch_id=batch_id,
        counts=counts,
        lines=[
            ReviewUndoLineOut(
                decision_id=line.decision.id,
                kind=line.decision.kind,
                question=line.decision.question,
                choice_label=line.decision.choice_label,
                status=line.status,
                message=line.message,
            )
            for line in lines
        ],
    )


@router.post("/bulk", response_model=ReviewBulkOut, status_code=202)
def review_bulk(
    payload: ReviewBulkRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewBulkOut:
    """**「전부 작업으로」**(ADR 0075) — 「추천대로 모두」 · 「거른 전부」 를 화면이 읽는
    상한(별칭 후보 200줄) 밖까지. 작업이 상한 없이 전부 읽어 미리 보고(**아무것도 안 바꾼다** —
    결과가 미리 보기, 결정 전부는 결과 파일 CSV), 사람이 보고 `POST /api/jobs/{job_id}/apply`
    로 적용한다 (같은 결정 · 같은 지문 — 그 사이 바뀌면 거절). 별칭 후보는 「이것 아닐까」 를
    미리 계산한 줄만 고른다."""
    services.require_viewer(db, user)
    job = services.enqueue_bulk(
        db,
        user,
        mode=payload.mode,
        filters=payload.filters.model_dump(),
        threshold=payload.threshold,
        action=payload.action,
        by_token=_by_token(request),
    )
    return ReviewBulkOut(job_id=job.id)


def _auto_out(db: Session, user: User, state: services.AutoState) -> ReviewAutoOut:
    from app.config import get_settings

    rule = state.rule
    names = (
        services.user_names(db, {rule.enabled_by_id})
        if rule is not None and rule.enabled_by_id
        else {}
    )
    return ReviewAutoOut(
        kind=state.provider.kind,
        label=state.provider.label,
        enabled=bool(rule and rule.enabled),
        threshold=rule.threshold if rule is not None else 0.95,
        enabled_by_name=names.get(rule.enabled_by_id) if rule and rule.enabled_by_id else None,
        enabled_at=rule.enabled_at if rule is not None else None,
        last_run_at=rule.last_run_at if rule is not None else None,
        last_job_id=rule.last_job_id if rule is not None else None,
        last_note=rule.last_note if rule is not None else "",
        decided=state.evidence.decided,
        followed=state.evidence.followed,
        rate=state.evidence.rate,
        ready=state.ready,
        why=state.why,
        min_decided=services.AUTO_MIN_DECIDED,
        min_rate=services.AUTO_MIN_RATE,
        max_per_night=services.AUTO_MAX,
        hour=get_settings().review_auto_hour,
        can_edit=user.is_system_admin,
    )


@router.get("/auto", response_model=list[ReviewAutoOut])
def review_auto_list(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ReviewAutoOut]:
    """**자동 확인**(ADR 0075) — 처리 길이 있는 종류마다 켜졌나 · 근거(그 확신도 이상에서
    사람이 하나씩 정한 결정 · 추천대로 한 몫) · 지금 돌 수 있나. 켜고 끄는 것은 시스템
    관리자."""
    services.require_viewer(db, user)
    return [_auto_out(db, user, one) for one in services.auto_states(db)]


@router.put("/auto/{kind}", response_model=ReviewAutoOut)
def review_auto_set(
    kind: str,
    payload: ReviewAutoIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewAutoOut:
    """자동 확인을 켜고 끈다 — **시스템 관리자만**. 켜면 그 사람의 이름으로 밤마다(하루 한 번)
    처리하고, 근거가 모자라거나 자동으로 정한 것을 누가 복원했으면 돌지 않는다. 감사 기록에
    남는다."""
    services.require_viewer(db, user)
    services.set_auto(db, user, kind, enabled=payload.enabled, threshold=payload.threshold)
    db.commit()
    state = next(one for one in services.auto_states(db) if one.provider.kind == kind)
    return _auto_out(db, user, state)
