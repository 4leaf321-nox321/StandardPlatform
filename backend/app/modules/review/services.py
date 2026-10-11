"""검토함 — **모으고, 고르고, 미리 보고, 적용하고, 기록한다**(ADR 0027).

항목은 제공자가 원래 자리에서 읽는다(`registry.py`). 여기는 종류를 모르고 그 위의 일만 한다:

    list      제공자마다 모아 거르고(종류 · 타입 · 부서 · 확신도 · 처리할 수 있는 것)
              세우고 묶는다
    plan      결정들이 무엇이 되나 — 「추천대로 모두」 면 문턱 이상을 고르고, 막히는 것은
              따로 센다. **아무것도 안 바꾼다.** 지문을 준다
    apply     같은 결정을 다시 계획해 지문이 같을 때만 — 항목마다 원래 기능으로 처리하고
              결정 기록 한 줄과 함께 **항목마다 커밋한다**(하나가 막혀도 나머지는 간다).
              크면 작업으로 간다
    undo      묶음 하나를 — 종류마다 원래 기능의 되돌리기로
    accuracy  사람이 추천을 따랐나 — 종류 · 확신도 구간마다

## 왜 항목마다 커밋하나

결정 여럿 중 하나가 그 사이 막히면(남이 먼저 처리 · 권한이 바뀜) 나머지까지 물리면 사람은 그
목록을 다시 안 본다. 그리고 병합은 스스로 커밋한다(`lifecycle.merge_into`) — 묶음 전체를 한
트랜잭션으로 둘 수가 없다. 그래서 항목 하나 = 처리 + 결정 기록 한 줄 = 커밋 하나. 작업 안에서는
그 커밋마다 워커의 검사(「아직 내가 쥔 작업인가」)가 돈다(`work.db`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.review import registry
from app.modules.review.models import ReviewDecision
from app.modules.review.registry import IGNORE, Filters, Item, Provider
from app.modules.workspaces.models import Workspace, WorkspaceMember
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from app.shared.permissions import is_any_manager, workspace_by_slug

log = logging.getLogger(__name__)

#: 「추천대로 모두」 의 기본 문턱 — 사람이 바꾼다.
DEFAULT_THRESHOLD = 0.9
#: 이보다 많은 결정 · 줄이면 요청이 아니라 작업으로 간다(화면이 멈추지 않게). 줄의 문턱은
#: 요청 하나를 3초 안에 두는 수다 — 지워진 것을 가리키는 칸은 바뀌는 칸만 검사하고 덩어리로
#: 쓰는데(`objects/review.py`), 200만 건 DB 에서 1,000행 0.42초 · 1만 행 4.8초(줄당 약 0.5ms)라
#: 5,000행이면 미리 보기 검사까지 2.7초 남짓이다(2026-10-10 실측). 예전 길(일괄 수정 — 줄당
#: 약 5ms)일 때는 같은 3초가 500행이었다.
INLINE_DECISIONS = 100
INLINE_ROWS = 5000
#: 한 번에 계획하는 결정의 상한 — 넘으면 거르기를 좁히라고 말한다.
MAX_DECISIONS = 5000
#: 미리 보기 응답에 싣는 결과 줄 — 수는 전부 센다.
SHOWN_OUTCOMES = 500
#: 부서 거르기에서 「전역」 을 뜻하는 값 — slug 에는 `@` 가 없다.
GLOBAL = "@global"

MODES = ("decisions", "recommended", "all")


def require_viewer(db: Session, user: User) -> None:
    """검토함은 **부서 관리자 이상**이다 — 판단을 처리하는 것은 자료를 고치는 일이라, 못 고치는
    사람에게 목록을 주면 못 지우는 숫자가 된다(데이터 품질 · 별칭 후보와 같은 문턱)."""
    if not is_any_manager(db, user):
        raise Forbidden(
            code("REVIEW", 1),
            "검토함은 부서 관리자 이상이 봅니다 — 판단을 처리하는 것은 자료를 수정하는 "
            "일입니다.",
        )


# --- 모으기 ---------------------------------------------------------------------


@dataclass
class KindSummary:
    provider: Provider
    total: int
    """원래 자리에 있는 수."""
    matched: int
    """읽은 것 중 거르기에 맞는 수."""
    scanned: int
    notes: list[str] = field(default_factory=list)


@dataclass
class Listed:
    kinds: list[KindSummary]
    items: list[Item]
    notes: list[str]


@dataclass(frozen=True)
class Query:
    kind: str | None = None
    type_slug: str | None = None
    workspace: str | None = None
    """부서 slug · `GLOBAL`(전역만) · None(전부)."""
    min_confidence: float | None = None
    max_confidence: float | None = None
    actionable: bool = False
    """처리할 수 있는 것만 — 처리 길이 있고 막히지 않은 것."""


def filters_of(db: Session, query: Query) -> Filters:
    if query.workspace == GLOBAL:
        return Filters(type_slug=query.type_slug, workspace_global=True)
    if query.workspace:
        return Filters(
            type_slug=query.type_slug, workspace_id=workspace_by_slug(db, query.workspace).id
        )
    return Filters(type_slug=query.type_slug)


def _chosen(kind: str | None) -> list[Provider]:
    if kind is None:
        return registry.providers()
    found = registry.get(kind)
    if found is None:
        known = ", ".join(one.kind for one in registry.providers())
        raise AppError(
            code("REVIEW", 2), f"모르는 종류입니다: {kind} — 있는 것: {known}", status=422
        )
    return [found]


def _passes(item: Item, provider: Provider, flt: Filters, query: Query) -> bool:
    if flt.workspace_global and item.workspace_id is not None:
        return False
    if flt.workspace_id is not None and item.workspace_id != flt.workspace_id:
        return False
    if query.min_confidence is not None and (
        item.confidence is None or item.confidence < query.min_confidence
    ):
        return False
    if query.max_confidence is not None and (
        item.confidence is None or item.confidence >= query.max_confidence
    ):
        return False
    return not (query.actionable and (provider.check is None or item.blocked))


def collect(db: Session, user: User, query: Query) -> Listed:
    """종류마다 원래 자리에서 읽어 거른다 — **볼 수 있는 종류만.** 못 보는 종류는 수도 안
    말한다 (데이터 소스는 시스템 관리자의 것이다)."""
    flt = filters_of(db, query)
    kinds: list[KindSummary] = []
    items: list[Item] = []
    notes: list[str] = []
    for provider in _chosen(query.kind):
        if not provider.can_view(db, user):
            if query.kind is not None:
                notes.append(f"「{provider.label}」 은(는) 볼 수 없는 종류입니다.")
            continue
        got = provider.collect(db, user, flt)
        mine = [one for one in got.items if _passes(one, provider, flt, query)]
        mine.sort(key=lambda one: (-one.priority, one.question))
        kinds.append(
            KindSummary(
                provider=provider,
                total=got.total,
                matched=len(mine),
                scanned=len(got.items),
                notes=got.notes,
            )
        )
        items.extend(mine)
    return Listed(kinds=kinds, items=items, notes=notes)


def grouped(items: list[Item]) -> list[Item]:
    """「같은 것끼리 묶기」 — 같은 종류 · 같은 묶음 열쇠(같은 말 · 같은 지워진 객체 · 같은
    출처)의 줄을 한 줄로 접는다. 접은 줄의 후보는 **모두에게 있는 것만**, 추천은 모두 같을
    때만, 확신도는 가장 낮은 것. 결정하면 펼친 줄마다 같은 결정이 간다(`members`)."""
    out: list[Item] = []
    buckets: dict[tuple[str, str], list[Item]] = {}
    for one in items:
        if not one.group:
            out.append(one)
            continue
        slot = (one.kind, one.group)
        if slot not in buckets:
            buckets[slot] = []
            out.append(one)
        buckets[slot].append(one)
    folded: list[Item] = []
    for one in out:
        members = buckets.get((one.kind, one.group)) if one.group else None
        if not members or len(members) == 1:
            folded.append(one)
            continue
        common = [
            choice
            for choice in members[0].choices
            if all(any(c.value == choice.value for c in m.choices) for m in members[1:])
        ]
        recs = {m.recommended for m in members}
        confidences = [m.confidence for m in members]
        blocked = [m.blocked for m in members if m.blocked]
        folded.append(
            Item(
                kind=one.kind,
                id=f"group:{one.group}",
                question=f"{one.group_label} — {len(members)}줄을 한 번에",
                evidence=[m.question for m in members[:5]]
                + ([f"… 그 밖에 {len(members) - 5}줄"] if len(members) > 5 else []),
                choices=common,
                recommended=next(iter(recs)) if len(recs) == 1 else None,
                confidence=min(c for c in confidences if c is not None)
                if all(c is not None for c in confidences)
                else None,
                reason=one.reason
                if len(recs) == 1
                else "줄마다 추천이 다릅니다 — 줄마다 추천대로",
                type_slug=one.type_slug
                if all(m.type_slug == one.type_slug for m in members)
                else "",
                type_label=one.type_label
                if all(m.type_slug == one.type_slug for m in members)
                else "여러 타입",
                workspace_id=one.workspace_id
                if all(m.workspace_id == one.workspace_id for m in members)
                else None,
                rows=sum(m.rows for m in members),
                group=one.group,
                group_label=one.group_label,
                blocked=blocked[0] if len(blocked) == len(members) else None,
                link=one.link,
                ignorable=all(m.ignorable for m in members),
                priority=sum(m.priority for m in members),
                payload={"members": [m.id for m in members]},
            )
        )
    return folded


# --- 계획 -----------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    kind: str
    item_id: str
    action: str
    value: str | None = None


@dataclass
class Outcome:
    decision: Decision
    status: str
    """계획에서는 `apply` · `blocked`, 적용한 뒤에는 `done` · `refused`."""
    question: str = ""
    choice: str = ""
    choice_label: str = ""
    effect: str = ""
    message: str = ""
    rows: int = 0
    undoable: bool = False
    heavy: bool = False
    recommended: str | None = None
    confidence: float | None = None
    item: Item | None = None


@dataclass
class Planned:
    decisions: list[Decision]
    outcomes: list[Outcome]
    fingerprint: str
    skipped: int = 0
    """「추천대로 모두」 에서 문턱 아래 · 추천 없음이라 고르지 않은 수."""
    notes: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        found = Counter(one.status for one in self.outcomes)
        return {"apply": found.get("apply", 0), "blocked": found.get("blocked", 0)}

    @property
    def rows(self) -> int:
        return sum(one.rows for one in self.outcomes if one.status == "apply")

    @property
    def as_job(self) -> bool:
        return needs_job(self.outcomes)


def needs_job(outcomes: list[Outcome]) -> bool:
    """요청 안에서 하기에 큰가 — 결정 수 · 손대는 줄 수 · 무거운 항목(가리키는 기록이 많은
    병합). 요청 안에서 1만 줄을 고치면 분 단위가 되고 클라이언트가 먼저 끊는다."""
    going = [one for one in outcomes if one.status == "apply"]
    return (
        len(going) > INLINE_DECISIONS
        or sum(one.rows for one in going) > INLINE_ROWS
        or any(one.heavy for one in going)
    )


def fingerprint_of(outcomes: list[Outcome]) -> str:
    """미리 본 것과 적용하는 것이 **같은 계획**인가 — 무엇을 무엇으로, 막히나, 몇 줄인가."""
    body = [
        [
            one.decision.kind,
            one.decision.item_id,
            one.decision.action,
            one.choice,
            one.status,
            one.rows,
        ]
        for one in outcomes
    ]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_same(outcomes: list[Outcome], fingerprint: str) -> None:
    """미리 본 것과 같은 계획인가 — 아니면 아무것도 안 하고 거절한다."""
    if fingerprint_of(outcomes) != fingerprint:
        raise Conflict(
            code("REVIEW", 3),
            "미리 본 것과 달라졌습니다 — 그 사이 누군가 처리했거나 후보가 바뀌었습니다. "
            "아무것도 처리하지 않았으니 다시 미리 보세요.",
        )


def resolve(
    db: Session,
    user: User,
    *,
    mode: str,
    decisions: list[Decision],
    query: Query,
    threshold: float,
    action: str,
) -> tuple[list[Decision], int]:
    """화면이 보낸 것을 결정 목록으로 — 고른 것은 그대로, 「추천대로 모두」 · 「거른 전부」 는
    **지금 거르기에 맞는 항목을 원래 자리에서 다시 읽어** 고른다. (결정들, 문턱 아래로 뺀
    수)."""
    if mode == "decisions":
        out = decisions
        skipped = 0
    else:
        listed = collect(db, user, query)
        handled = {one.provider.kind for one in listed.kinds if one.provider.check is not None}
        out = []
        skipped = 0
        for item in listed.items:
            if item.kind not in handled:
                continue
            if mode == "recommended":
                if (
                    item.recommended is None
                    or item.confidence is None
                    or item.confidence < threshold
                ):
                    skipped += 1
                    continue
                out.append(Decision(item.kind, item.id, "accept"))
            else:
                out.append(Decision(item.kind, item.id, action))
    if len(out) > MAX_DECISIONS:
        raise AppError(
            code("REVIEW", 4),
            f"한 번에 {MAX_DECISIONS:,}건까지 정합니다({len(out):,}건) — 종류 · 타입 · 부서로 "
            "좁혀 나눠 하세요.",
            status=422,
        )
    return out, skipped


def _blocked(decision: Decision, message: str, item: Item | None = None) -> Outcome:
    return Outcome(
        decision=decision,
        status="blocked",
        question=item.question if item else "",
        message=message,
        recommended=item.recommended if item else None,
        confidence=item.confidence if item else None,
        item=item,
    )


def plan(db: Session, user: User, decisions: list[Decision]) -> list[Outcome]:
    """결정마다 무엇이 되나 — **항목을 원래 자리에서 다시 읽어** 본다. 아무것도 안 바꾼다."""
    wanted: dict[str, set[str]] = {}
    for one in decisions:
        wanted.setdefault(one.kind, set()).add(one.item_id)
    items: dict[tuple[str, str], Item] = {}
    for kind, ids in wanted.items():
        provider = registry.get(kind)
        if provider is None or provider.check is None or not provider.can_view(db, user):
            continue
        for got in provider.collect(db, user, Filters(item_ids=frozenset(ids))).items:
            items[(kind, got.id)] = got
    outcomes: list[Outcome] = []
    seen: set[tuple[str, str]] = set()
    for one in decisions:
        provider = registry.get(one.kind)
        if provider is None:
            outcomes.append(_blocked(one, f"모르는 종류입니다: {one.kind}"))
            continue
        if provider.check is None:
            outcomes.append(
                _blocked(one, f"「{provider.label}」 은(는) 원래 화면에서 처리합니다.")
            )
            continue
        if not provider.can_view(db, user):
            outcomes.append(
                _blocked(one, f"「{provider.label}」 은(는) 볼 수 없는 종류입니다.")
            )
            continue
        slot = (one.kind, one.item_id)
        if slot in seen:
            outcomes.append(
                _blocked(one, "같은 항목을 두 번 선택했습니다 — 앞의 결정만 봅니다.")
            )
            continue
        seen.add(slot)
        item = items.get(slot)
        if item is None:
            outcomes.append(
                _blocked(one, "이미 처리했거나(그 사이 누군가) 볼 수 없는 항목입니다.")
            )
            continue
        if one.action == "accept":
            value = item.recommended
            if value is None:
                outcomes.append(
                    _blocked(one, "추천이 없습니다 — 「다른 것으로」 선택하세요.", item)
                )
                continue
        elif one.action == "choose":
            value = one.value
            if not value:
                outcomes.append(_blocked(one, "고른 값이 없습니다.", item))
                continue
        elif one.action == "ignore":
            if not item.ignorable:
                outcomes.append(
                    _blocked(
                        one,
                        f"「{provider.label}」 에는 무시가 없습니다 — 후보 중에서 선택하세요.",
                        item,
                    )
                )
                continue
            value = IGNORE
        else:
            outcomes.append(_blocked(one, f"모르는 동작입니다: {one.action}", item))
            continue
        found = provider.check(db, user, item, value)
        outcomes.append(
            Outcome(
                decision=one,
                status=found.status,
                question=item.question,
                choice=value,
                choice_label=found.choice_label,
                effect=found.effect,
                message=found.message,
                rows=found.rows if found.status == "apply" else 0,
                undoable=found.undoable,
                heavy=found.heavy,
                recommended=item.recommended,
                confidence=item.confidence,
                item=item,
            )
        )
    return outcomes


def preview(
    db: Session,
    user: User,
    *,
    mode: str,
    decisions: list[Decision],
    query: Query,
    threshold: float,
    action: str,
) -> Planned:
    resolved, skipped = resolve(
        db,
        user,
        mode=mode,
        decisions=decisions,
        query=query,
        threshold=threshold,
        action=action,
    )
    outcomes = plan(db, user, resolved)
    notes: list[str] = []
    if mode == "recommended" and skipped:
        notes.append(
            f"추천이 없거나 확신도가 {threshold:.2f} 아래인 {skipped:,}건은 선택하지 "
            "않았습니다 — 사람이 봐야 합니다."
        )
    return Planned(
        decisions=resolved,
        outcomes=outcomes,
        fingerprint=fingerprint_of(outcomes),
        skipped=skipped,
        notes=notes,
    )


# --- 적용 -----------------------------------------------------------------------


@dataclass
class Result:
    batch_id: uuid.UUID | None
    outcomes: list[Outcome]
    job: Job | None = None

    @property
    def counts(self) -> dict[str, int]:
        found = Counter(one.status for one in self.outcomes)
        return {"done": found.get("done", 0), "refused": found.get("refused", 0)}


Progress = Callable[[str, int, int], None]


def _record(
    db: Session,
    user: User,
    batch_id: uuid.UUID,
    one: Outcome,
    *,
    mode: str,
    threshold: float | None,
    undo: dict[str, Any],
) -> None:
    item = one.item
    recommended = item.recommended if item else None
    db.add(
        ReviewDecision(
            batch_id=batch_id,
            kind=one.decision.kind,
            item_id=one.decision.item_id[:300],
            question=one.question[:500],
            action=one.decision.action,
            choice=one.choice[:200],
            choice_label=one.choice_label[:300],
            recommended=recommended,
            confidence=item.confidence if item else None,
            # 추천을 따랐나 — 추천이 없었으면 셈하지 않는다(NULL). 무시는 따르지 않은 것이다.
            followed=None if recommended is None else one.choice == recommended,
            mode=mode if mode in ("one", "selected", "all", "recommended") else "selected",
            threshold=threshold if mode == "recommended" else None,
            status=one.status,
            message=one.message[:500],
            rows=one.rows,
            signature=(item.signature if item else "")[:64],
            undo=undo,
            undoable=one.status == "done" and one.undoable,
            type_slug=(item.type_slug if item else "")[:64],
            workspace_id=item.workspace_id if item else None,
            decided_by_id=user.id,
        )
    )


def apply(
    db: Session,
    user: User,
    decisions: list[Decision],
    fingerprint: str,
    *,
    mode: str,
    threshold: float | None,
    in_job: bool = False,
    progress: Progress | None = None,
    planned: list[Outcome] | None = None,
) -> Result:
    """미리 본 그 계획대로 — **지문이 다르면 아무것도 안 한다.** 항목마다 처리 + 기록 +
    커밋. `planned` 를 주면(방금 같은 결정으로 계획한 것) 다시 계획하지 않는다."""
    outcomes = planned if planned is not None else plan(db, user, decisions)
    require_same(outcomes, fingerprint)
    batch_id = uuid.uuid4()
    user_id = user.id
    total = len(outcomes)
    for index, one in enumerate(outcomes):
        if progress is not None:
            progress("적용", index, total)
        if one.status != "apply" or one.item is None:
            continue
        provider = registry.get(one.decision.kind)
        assert provider is not None and provider.apply is not None
        try:
            applied = provider.apply(db, user, one.item, one.choice, in_job)
            one.status = "done"
            one.rows = applied.rows
            if applied.message:
                one.message = applied.message
            db.flush()
            _record(db, user, batch_id, one, mode=mode, threshold=threshold, undo=applied.undo)
            db.commit()
        except AppError as refused:
            db.rollback()
            one.status = "refused"
            one.rows = 0
            one.message = refused.message
            _record(db, user, batch_id, one, mode=mode, threshold=threshold, undo={})
            db.commit()
        except Exception as caught:
            # 하나가 터져도 나머지는 간다 — 터진 것은 그 줄에 까닭을 남기고 로그에 스택을.
            db.rollback()
            log.exception("검토함 적용 실패: %s %s", one.decision.kind, one.decision.item_id)
            one.status = "refused"
            one.rows = 0
            one.message = f"처리 중 오류가 났습니다({type(caught).__name__})."
            _record(db, user, batch_id, one, mode=mode, threshold=threshold, undo={})
            db.commit()
        user = db.get(User, user_id) or user
    if progress is not None:
        progress("적용", total, total)
    return Result(batch_id=batch_id, outcomes=outcomes)


def enqueue(
    db: Session,
    user: User,
    decisions: list[Decision],
    fingerprint: str,
    *,
    mode: str,
    threshold: float | None,
) -> Job:
    """큰 묶음 — 작업으로. 워커가 같은 함수(`apply`)를 같은 지문으로 부른다."""
    job = job_services.enqueue(
        db,
        kind="review_apply",
        params={
            "decisions": [asdict(one) for one in decisions],
            "fingerprint": fingerprint,
            "mode": mode,
            "threshold": threshold,
        },
        user=user,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return job


def outcome_dict(one: Outcome) -> dict[str, Any]:
    provider = registry.get(one.decision.kind)
    return {
        "kind": one.decision.kind,
        "kind_label": provider.label if provider else one.decision.kind,
        "item_id": one.decision.item_id,
        "question": one.question,
        "action": one.decision.action,
        "choice": one.choice,
        "choice_label": one.choice_label,
        "status": one.status,
        "effect": one.effect,
        "message": one.message,
        "rows": one.rows,
        "undoable": one.undoable,
        "recommended": one.recommended,
        "confidence": one.confidence,
        "followed": None if one.recommended is None else one.choice == one.recommended,
    }


def run_job(
    db: Session, user: User, params: dict[str, Any], progress: Progress
) -> dict[str, Any]:
    """작업 `review_apply` — 요청 경로와 **같은 함수**. 줄 목록(`rows`)은 작업 목록이 요약만
    싣는다(`sp_job_brief` · `rows_omitted`)."""
    decisions = [
        Decision(
            kind=str(one["kind"]),
            item_id=str(one["item_id"]),
            action=str(one["action"]),
            value=None if one.get("value") is None else str(one["value"]),
        )
        for one in params.get("decisions") or []
    ]
    unknown = sorted({one.kind for one in decisions if registry.get(one.kind) is None})
    if unknown:
        # 제공자는 `app/main.py` 의 조립이 등록한다 — 워커가 그것을 안 거치고 떴으면 비어 있다.
        # 그대로 두면 모든 결정이 「모르는 종류」 로 막혀 「미리 본 것과 달라졌습니다」 라는
        # 엉뚱한 말로 실패한다(지문이 달라진다).
        raise AppError(
            code("REVIEW", 8),
            f"이 프로세스에 검토함의 종류가 등록되지 않았습니다({', '.join(unknown)}) — "
            "워커가 앱과 같은 조립으로 떴는지 보세요.",
            status=409,
        )
    threshold = params.get("threshold")
    result = apply(
        db,
        user,
        decisions,
        str(params.get("fingerprint") or ""),
        mode=str(params.get("mode") or "selected"),
        threshold=float(threshold) if threshold is not None else None,
        in_job=True,
        progress=progress,
    )
    return {
        "applied": True,
        "batch_id": str(result.batch_id),
        "counts": result.counts,
        "rows": [outcome_dict(one) for one in result.outcomes],
    }


# --- 결정 기록 · 되돌리기 · 정확도 --------------------------------------------------


def _visible_decisions(db: Session, user: User) -> Any:
    """결정 기록은 **내 것 + 내가 관리하는 부서의 것**, 시스템 관리자는 전부."""
    if user.is_system_admin:
        return ReviewDecision.id.isnot(None)
    managed = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user.id, WorkspaceMember.role == "manager"
    )
    return or_(
        ReviewDecision.decided_by_id == user.id, ReviewDecision.workspace_id.in_(managed)
    )


def decisions_page(
    db: Session,
    user: User,
    *,
    kind: str | None,
    batch_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[ReviewDecision], int]:
    stmt = select(ReviewDecision).where(_visible_decisions(db, user))
    if kind:
        stmt = stmt.where(ReviewDecision.kind == kind)
    if batch_id is not None:
        stmt = stmt.where(ReviewDecision.batch_id == batch_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


@dataclass
class Batch:
    batch_id: uuid.UUID
    decided_at: datetime
    decided_by_id: uuid.UUID | None
    mode: str
    count: int
    done: int
    refused: int
    undoable: int
    undone: int
    kinds: list[str]


def batches(db: Session, user: User, *, limit: int) -> list[Batch]:
    """최근 적용한 묶음 — 복원할 수 있는 것이 몇이나 남았나."""
    rows = db.execute(
        select(
            ReviewDecision.batch_id,
            func.min(ReviewDecision.decided_at),
            # uuid 에는 min 이 없다 — 글자로 모아 꺼낸다(한 묶음은 한 사람이 적용한다).
            func.min(cast(ReviewDecision.decided_by_id, String)),
            func.min(ReviewDecision.mode),
            func.count(),
            func.count().filter(ReviewDecision.status == "done"),
            func.count().filter(ReviewDecision.status == "refused"),
            func.count().filter(
                ReviewDecision.undoable.is_(True), ReviewDecision.undone_at.is_(None)
            ),
            func.count().filter(ReviewDecision.undone_at.isnot(None)),
            func.array_agg(func.distinct(ReviewDecision.kind)),
        )
        .where(_visible_decisions(db, user))
        .group_by(ReviewDecision.batch_id)
        .order_by(func.min(ReviewDecision.decided_at).desc())
        .limit(limit)
    ).all()
    return [
        Batch(
            batch_id=row[0],
            decided_at=row[1],
            decided_by_id=uuid.UUID(row[2]) if row[2] else None,
            mode=row[3],
            count=int(row[4]),
            done=int(row[5]),
            refused=int(row[6]),
            undoable=int(row[7]),
            undone=int(row[8]),
            kinds=sorted(row[9] or []),
        )
        for row in rows
    ]


@dataclass
class UndoLine:
    decision: ReviewDecision
    status: str
    """계획: `undo` · `blocked`, 적용 뒤: `done` · `refused`."""
    message: str


def undo_batch(db: Session, user: User, batch_id: uuid.UUID, *, apply: bool) -> list[UndoLine]:
    """묶음 하나를 복원한다 — `apply=False` 면 무엇이 복원되나만. 종류마다 원래 기능의
    되돌리기(일괄 수정의 묶음 되돌리기 · 별칭 빼기 · 후보 대기로)로 간다. 병합은 복원이
    없다."""
    rows = list(
        db.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.batch_id == batch_id, _visible_decisions(db, user))
            .order_by(ReviewDecision.decided_at, ReviewDecision.id)
        )
    )
    if not rows:
        raise NotFound(
            code("REVIEW", 5), "그 묶음을 찾을 수 없습니다 — 검토함에서 적용한 것이 아닙니다."
        )
    lines: list[UndoLine] = []
    for row in rows:
        provider = registry.get(row.kind)
        if row.status != "done":
            lines.append(UndoLine(row, "blocked", "적용하지 못한 결정입니다."))
            continue
        if row.undone_at is not None:
            lines.append(UndoLine(row, "blocked", "이미 복원했습니다."))
            continue
        if not row.undoable or provider is None or provider.undo is None:
            note = provider.undo_note if provider and provider.undo_note else ""
            lines.append(UndoLine(row, "blocked", note or "복원할 수 없는 결정입니다."))
            continue
        ok, message = provider.undo(db, user, dict(row.undo or {}), False)
        lines.append(UndoLine(row, "undo" if ok else "blocked", message))
    if not apply:
        return lines
    user_id = user.id
    for line in lines:
        if line.status != "undo":
            continue
        provider = registry.get(line.decision.kind)
        assert provider is not None and provider.undo is not None
        decision_id = line.decision.id
        try:
            ok, message = provider.undo(db, user, dict(line.decision.undo or {}), True)
            if not ok:
                raise Conflict(code("REVIEW", 6), message)
            target = db.get(ReviewDecision, decision_id)
            if target is not None:
                target.undone_at = datetime.now(UTC)
                target.undone_by_id = user_id
            db.commit()
            line.status, line.message = "done", message
        except AppError as refused:
            db.rollback()
            line.status, line.message = "refused", refused.message
        user = db.get(User, user_id) or user
    return lines


@dataclass
class Accuracy:
    kind: str
    label: str
    decided: int
    recommended: int
    followed: int


#: 확신도 구간 — 「0.9 이상은 믿어도 되나」 를 이것으로 잰다.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("0.9 이상", 0.9, 1.01),
    ("0.7 ~ 0.9", 0.7, 0.9),
    ("0.5 ~ 0.7", 0.5, 0.7),
    ("0.5 미만", 0.0, 0.5),
)


def accuracy(db: Session, user: User) -> tuple[list[Accuracy], list[Accuracy]]:
    """추천을 따랐나 — 처리한(복원하지 않은) 결정 중 추천이 있었던 것만 센다. 복원한 결정은
    「그 추천은 틀렸다」 일 수 있어 따른 것으로 세지 않는다."""
    alive = (
        _visible_decisions(db, user),
        ReviewDecision.status == "done",
    )
    followed = func.count().filter(
        ReviewDecision.followed.is_(True), ReviewDecision.undone_at.is_(None)
    )
    by_kind = []
    for kind, decided, recommended, kept in db.execute(
        select(
            ReviewDecision.kind,
            func.count(),
            func.count().filter(ReviewDecision.recommended.isnot(None)),
            followed,
        )
        .where(*alive)
        .group_by(ReviewDecision.kind)
        .order_by(ReviewDecision.kind)
    ):
        provider = registry.get(kind)
        by_kind.append(
            Accuracy(
                kind=kind,
                label=provider.label if provider else kind,
                decided=int(decided),
                recommended=int(recommended),
                followed=int(kept),
            )
        )
    band = case(
        *[(ReviewDecision.confidence >= low, name) for name, low, _high in BANDS if low > 0],
        else_=BANDS[-1][0],
    )
    found = {
        name: (int(decided), int(kept))
        for name, decided, kept in db.execute(
            select(band, func.count(), followed)
            .where(*alive, ReviewDecision.recommended.isnot(None))
            .group_by(band)
        )
    }
    by_band = [
        Accuracy(
            kind="",
            label=name,
            decided=found.get(name, (0, 0))[0],
            recommended=found.get(name, (0, 0))[0],
            followed=found.get(name, (0, 0))[1],
        )
        for name, _low, _high in BANDS
    ]
    return by_kind, by_band


def maintenance_links(db: Session, user: User) -> dict[str, str]:
    """홈 「남은 일」 의 줄 → 검토함의 그 종류. **볼 수 있는 종류만** — 못 보는 사람의 홈은
    원래 화면으로 간다."""
    if not is_any_manager(db, user):
        return {}
    out: dict[str, str] = {}
    for provider in registry.providers():
        if not provider.maintenance_keys or not provider.can_view(db, user):
            continue
        for key in provider.maintenance_keys:
            out[key] = f"/review?kind={provider.kind}"
    return out


def workspace_labels(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str]]:
    if not ids:
        return {}
    return {
        row.id: (row.slug, row.name)
        for row in db.scalars(select(Workspace).where(Workspace.id.in_(ids)))
    }


def user_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {
        row.id: row.display_name for row in db.scalars(select(User).where(User.id.in_(ids)))
    }
