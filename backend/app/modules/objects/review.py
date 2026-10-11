"""검토함의 객체 쪽 제공자 넷 — **원래 자리에서 읽고, 원래 기능으로 처리한다**(ADR 0027).

    alias_candidate   못 찾은 말(별칭 후보)     search_misses    misses.plan_attach · attach
    alias_review      기계가 붙인 별칭           object_aliases   aliases.review · unverify
    duplicate         이름이 같은 객체(병합)     objects          lifecycle.merge_into
    broken_ref        지워진 것을 가리키는 칸    object_refs      일괄 수정의 묶음 기록 + 일괄
                                                                 입력의 쓰기(`bulk._Writes`)

처리를 여기서 새로 쓰지 않는다 — 같은 일이 두 길로 갈라지면 「검토함에서는 되는데 객체
화면에서는 안 되는」 상태가 생기고, 감사 기록도 두 모양이 된다. 그래서 별칭은 객체 화면 ·
데이터 품질의 그 함수로, 참조 바꾸기는 「여러 건 한 칸 바꾸기」 의 묶음 기록(`bulkedit.record`
— 같은 묶음 번호 · 사람이 고친 칸 표시)으로 간다. 하나 다른 것: 줄마다의 저장 검사는 **바뀌는
그 칸 몫만** 하고 쓰기는 일괄 입력의 덩어리 쓰기다(`_ref_changes` · `_write_ref_changes`) —
줄당 5ms 가 들던 일괄 수정의 길로는 10만 행이 8분 남짓이었고 이제 1분 10초 안팎이다
(2026-10-10).

## 추천과 확신도 — 무엇을 근거로

    alias_candidate   「이것 아닐까」 의 첫째(이름 · 식별자 · 별칭의 비슷함, pg_trgm).
                      둘째와 0.1 안이면 0.8 배 — 둘 중 어느 것인지 가리기 어렵다
    alias_review      다른 객체의 이름과 같으면 「삭제」 0.9, 이름과 표기만 다르면
                      「확인」 0.95, 그 밖은 「확인」 0.5 + 0.45 * 이름과의 비슷함(음역
                      「앤시스 ↔ Ansys」 은 낮게 나온다 — 사람이 볼 것으로 남는다)
    duplicate         남길 것 = 가리키는 것이 많은 것 → 식별자가 있는 것 → 먼저 만든 것.
                      0.5 에서 같은 부서 +0.2 · 식별자가 안 엇갈림 +0.2 · 엇갈리는 칸
                      없음 +0.1, 식별자가 서로 다르면 -0.3, 엇갈리는 칸마다 -0.1(셋까지)
    broken_ref        지워진 것이 병합된 곳 0.95 · 이름(식별자)이 같은 살아 있는 것 하나
                      0.9(여럿이면 0.6) · 이름이 비슷한 것 비슷함 * 0.8. 「비움」 은 후보일
                      뿐 추천하지 않는다

**참조 칸에 글자로 남은 값은 없다** — 참조 칸은 id 만 받고(`check_value`), 이름이 안 풀리는
줄은 일괄 입력 · 종류 변경이 거절하거나 대체 값을 받는다. 그래서 「못 맞춘 참조」 로 남는 것은
지워진 (또는 없는) 객체를 가리키는 칸이고, 같은 지워진 객체를 가리키는 줄을 한 결정으로
묶는다(300행이면 한 번).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, false, func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects import (
    aliases,
    bulk,
    bulkedit,
    lifecycle,
    misses,
    quality,
    rewrite,
    system,
)
from app.modules.objects.models import ObjectAlias, ObjectInstance, ObjectRef, SearchMiss
from app.modules.objects.scope import find as find_scope
from app.modules.objects.services import properties_of, require_unique_properties
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import validate_properties
from app.modules.review import registry
from app.modules.review.models import ReviewDecision
from app.modules.review.registry import (
    IGNORE,
    Applied,
    Check,
    Choice,
    Collected,
    Filters,
    Item,
)
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.batches import any_of, chunks
from app.shared.errors import AppError, Conflict, code
from app.shared.permissions import is_any_manager, require_owner_edit, visible_owner_clause
from app.shared.text import compare_key

#: 별칭 후보는 줄마다 「이것 아닐까」 를 찾는 질의(비슷함 — 인덱스를 못 탄다)가 붙는다. 많이
#: 찾은 것부터 이만큼만 후보를 센다 — 나머지는 수만 말한다.
CANDIDATE_CAP = 200
#: 기계가 붙인 별칭 · 지워진 것을 가리키는 묶음은 질의 하나로 읽는다 — 그래도 상한은 둔다.
ALIAS_CAP = 2000
REF_CAP = 1000
DUPLICATE_CAP = 1000
#: 이름이 비슷한 대체 후보를 찾을 문턱과, 찾지 않을 크기(비슷함은 행을 다 본다).
REF_SIMILAR_MIN = 0.4
REF_SIMILAR_MAX_ROWS = 50_000
#: 미리 보기에서 줄마다 (바뀌는 칸의) 저장 검사를 다 돌려 보는 크기 — 넘으면 적용할 때 본다고
#: 말한다. 그 칸의 값만 읽어 보므로(`_ref_values`) 1만 행 미리 보기가 0.4초 · 10만 행이 3.3초다
#: (200만 건 DB, 2026-10-10 실측) — 2만 행이면 한 줄 미리 보기가 1초 안이다. 예전(줄마다 저장
#: 검사 전체 — 줄당 3ms)에는 1,000행이었다.
REF_CHECK_ROWS = 20_000

MERGED_SCORE = 0.95
SAME_NAME_SCORE = 0.9
SAME_NAME_MANY_SCORE = 0.6
SIMILAR_FACTOR = 0.8
#: 첫째와 둘째 후보의 점수 차가 이보다 작으면 「가리기 어렵다」 — 확신도를 깎는다.
CLOSE_GAP = 0.1
CLOSE_FACTOR = 0.8

_NOT_WORD = re.compile(r"[\W_]+")


def _manager(db: Session, user: User) -> bool:
    return is_any_manager(db, user)


def _uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _uuids(raw: Iterable[str]) -> list[uuid.UUID]:
    return [one for one in (_uuid(text) for text in raw) if one is not None]


def _hash(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class _Editable:
    """「이 사람이 이 부서 것을 고칠 수 있나」 — 부서마다 한 번만 묻는다."""

    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user
        self.seen: dict[uuid.UUID | None, str | None] = {}

    def why_not(self, workspace_id: uuid.UUID | None) -> str | None:
        if workspace_id not in self.seen:
            try:
                require_owner_edit(
                    self.db,
                    self.user,
                    workspace_id,
                    what="객체",
                    code_value=code("OBJECTS", 12),
                )
            except AppError as denied:
                self.seen[workspace_id] = denied.message
            else:
                self.seen[workspace_id] = None
        return self.seen[workspace_id]


def _ignored(db: Session, kind: str) -> set[tuple[str, str]]:
    """결정 기록에만 기억하는 무시 — (항목, 지문). 복원한 것은 뺀다."""
    return {
        (item_id, signature)
        for item_id, signature in db.execute(
            select(ReviewDecision.item_id, ReviewDecision.signature).where(
                ReviewDecision.kind == kind,
                ReviewDecision.action == "ignore",
                ReviewDecision.status == "done",
                ReviewDecision.undone_at.is_(None),
            )
        )
    }


def _workspace_names(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.name for row in db.scalars(select(Workspace))}


def _close(scores: list[float]) -> bool:
    return len(scores) > 1 and scores[0] - scores[1] < CLOSE_GAP


def _ignore_check(effect: str) -> Check:
    return Check(status="apply", effect=effect, choice_label="무시", rows=0, undoable=True)


def _blocked(effect: str, label: str, message: str) -> Check:
    return Check(status="blocked", effect=effect, choice_label=label, message=message)


# --- 별칭 후보(못 찾은 말) ------------------------------------------------------------

_MATCHED = {"label": "이름이", "key": "식별자가", "alias": "별칭이"}


def _candidate_confidence(found: misses.Candidate) -> tuple[float | None, str]:
    if not found.suggestions:
        return None, found.suggest_note
    top = found.suggestions[0]
    reason = f"{_MATCHED.get(top.matched, '이름이')} 비슷함 {top.score:.2f}"
    if top.matched == "alias" and top.matched_text:
        reason = f"별칭 「{top.matched_text}」 이 비슷함 {top.score:.2f}"
    confidence = float(top.score)
    if _close([one.score for one in found.suggestions]):
        confidence = round(confidence * CLOSE_FACTOR, 2)
        reason += f" · 다음 후보 「{found.suggestions[1].label}」 와 차이가 작음"
    return confidence, reason


def collect_candidates(db: Session, user: User, flt: Filters) -> Collected:
    """대기 중인 못 찾은 말 — **세 번 이상 찾은 것**(홈 「남은 일」 과 같은 문턱). 한 번 친
    오타까지 실으면 그 수는 늘 크고, 늘 큰 수는 아무도 안 읽는다. 결정할 항목을 다시 읽을
    때(`item_ids`)는 문턱을 안 본다 — MCP 가 원래 목록에서 고른 후보도 처리된다."""
    base = select(SearchMiss).where(SearchMiss.status == "pending")
    notes: list[str] = []
    if flt.item_ids is not None:
        ids = _uuids(flt.item_ids)
        if not ids:
            return Collected(items=[], total=0)
        base = base.where(SearchMiss.id.in_(ids))
    else:
        base = base.where(SearchMiss.hits >= misses.REPEAT_MIN)
    if flt.type_slug is not None:
        base = base.where(SearchMiss.scope == flt.type_slug)
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = list(
        db.scalars(
            base.order_by(
                SearchMiss.hits.desc(),
                func.jsonb_array_length(SearchMiss.askers).desc(),
                SearchMiss.last_at.desc(),
                SearchMiss.id,
            ).limit(CANDIDATE_CAP)
        )
    )
    if total > len(rows):
        notes.append(
            f"별칭 후보는 많이 찾은 것부터 {CANDIDATE_CAP}건만 후보를 셉니다 — 나머지 "
            f"{total - len(rows):,}건은 이것을 처리한 뒤에 표시됩니다."
        )
    labels = misses._labels(db)
    found = [misses.describe(db, user, one, suggest=True, labels=labels) for one in rows]
    tops = [one.suggestions[0].id for one in found if one.suggestions]
    owners: dict[uuid.UUID, uuid.UUID | None] = {}
    for batch in chunks(tops):
        owners.update(
            {
                object_id: workspace_id
                for object_id, workspace_id in db.execute(
                    select(ObjectInstance.id, ObjectInstance.owner_workspace_id).where(
                        ObjectInstance.id.in_(batch)
                    )
                )
            }
        )
    items: list[Item] = []
    for one in found:
        row = one.row
        confidence, reason = _candidate_confidence(one)
        vias = " · ".join(misses.VIAS.get(via, via) for via in row.vias or [])
        recommended = str(one.suggestions[0].id) if one.suggestions else None
        items.append(
            Item(
                kind="alias_candidate",
                id=str(row.id),
                question=f"「{row.text}」 을(를) 무엇의 별칭으로 추가할까요?",
                evidence=[
                    f"{one.scope_label}에서 {row.hits}번 · "
                    f"{len(row.askers or [])}명이 찾았으나 못 찾음",
                    f"자리: {vias}" if vias else "",
                    f"마지막 {row.last_at:%Y-%m-%d}",
                ],
                choices=[
                    Choice(
                        value=str(hit.id),
                        label=hit.label,
                        detail=hit.type_label + (f" · {hit.key}" if hit.key else ""),
                        score=hit.score,
                    )
                    for hit in one.suggestions
                ],
                recommended=recommended,
                confidence=confidence,
                reason=reason,
                type_slug=row.scope,
                type_label=one.scope_label,
                workspace_id=owners.get(one.suggestions[0].id) if one.suggestions else None,
                rows=1,
                group=row.norm,
                group_label=f"「{row.text}」",
                link="/quality#alias_candidates",
                priority=float(row.hits),
            )
        )
    return Collected(items=items, total=total, notes=notes)


def check_candidate(db: Session, user: User, item: Item, value: str) -> Check:
    miss = db.get(SearchMiss, uuid.UUID(item.id))
    if miss is None or miss.status != "pending":
        return _blocked("", "", "이미 처리한 후보입니다.")
    if value == IGNORE:
        return _ignore_check(f"「{miss.text}」 무시 — 또 찾아도 표시하지 않음")
    object_id = _uuid(value)
    if object_id is None:
        return _blocked("", value, "객체 id 가 아닙니다 — 후보 중에서 선택하세요.")
    try:
        plan = misses.plan_attach(db, user, miss, object_id, None)
    except AppError as refused:
        return _blocked(f"「{miss.text}」 → ?", value, refused.message)
    label = f"{plan.target.label}({plan.object_type.label})"
    effect = f"「{plan.value}」 → {plan.target.label}의 별칭"
    if plan.blocking:
        return _blocked(effect, label, plan.blocking[0])
    return Check(
        status="apply",
        effect=effect,
        choice_label=label,
        message=" ".join(plan.warnings),
        rows=1,
        undoable=True,
    )


def apply_candidate(db: Session, user: User, item: Item, value: str, _in_job: bool) -> Applied:
    miss = misses.get(db, uuid.UUID(item.id))
    if value == IGNORE:
        if miss.status != "pending":
            raise Conflict(code("REVIEW", 6), "이미 처리한 후보입니다.")
        done, refused = misses.decide(db, user, [miss.id], "ignore")
        if refused or not done:
            raise Conflict(
                code("REVIEW", 6), refused[0] if refused else "무시하지 못했습니다."
            )
        return Applied(rows=1, undo={"op": "ignore", "miss_ids": [str(miss.id)]})
    if miss.status != "pending":
        # 같은 말을 다른 자리(통합 검색 · 인터페이스)에서 못 찾은 줄을 함께 골랐다 — 앞의
        # 결정이 붙이면서 이미 닫았다. 같은 객체면 처리된 것이다.
        if miss.status == "attached" and str(miss.object_id) == value:
            return Applied(rows=0, message="같은 말을 붙이면서 함께 닫혔습니다.")
        raise Conflict(code("REVIEW", 6), "이미 처리한 후보입니다.")
    plan = misses.plan_attach(db, user, miss, uuid.UUID(value), None)
    norm = compare_key(plan.value)
    waiting = list(
        db.scalars(
            select(SearchMiss).where(SearchMiss.norm == norm, SearchMiss.status == "pending")
        )
    )
    misses.attach(db, user, plan)
    db.flush()
    closed = [str(one.id) for one in waiting if one.status == "attached"]
    return Applied(
        rows=1,
        message=" ".join(plan.warnings),
        undo={
            "op": "attach",
            "object_id": str(plan.target.id),
            "value": plan.value,
            "miss_ids": closed or [str(miss.id)],
        },
    )


def undo_candidate(
    db: Session, user: User, data: dict[str, Any], apply: bool
) -> tuple[bool, str]:
    miss_ids = _uuids(data.get("miss_ids") or [])
    if data.get("op") == "ignore":
        rows = list(db.scalars(select(SearchMiss).where(SearchMiss.id.in_(miss_ids))))
        if not any(one.status == "ignored" for one in rows):
            return False, "그 후보는 이미 무시 상태가 아닙니다(또는 정리됐습니다)."
        if apply:
            misses.decide(db, user, [one.id for one in rows], "restore")
        return True, "무시를 거두고 대기로 복원"
    target = db.get(ObjectInstance, _uuid(str(data.get("object_id"))))
    if target is None or target.deleted_at is not None:
        return False, "별칭을 붙인 객체가 지워졌습니다."
    object_type = db.get(ObjectType, target.type_id)
    if object_type is None:  # pragma: no cover - FK 가 막는다
        return False, "타입이 없습니다."
    try:
        require_owner_edit(
            db, user, target.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
        managed.require_objects_editable(object_type, what="별칭을 고치지")
    except AppError as denied:
        return False, denied.message
    value = str(data.get("value") or "")
    norm = compare_key(value)
    current = aliases.human_of(db, [target.id]).get(target.id, [])
    has = any(compare_key(one) == norm for one in current)
    message = (
        f"「{value}」 을(를) {target.label}의 별칭에서 제외하고 후보를 대기로 복원"
        if has
        else f"「{value}」 은(는) 이미 {target.label}의 별칭이 아닙니다 — 후보만 대기로 복원"
    )
    if not apply:
        return True, message
    if has:
        before, after = aliases.set_human(
            db,
            target,
            object_type,
            [one for one in current if compare_key(one) != norm],
            mode="replace",
        )
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=target.id,
            target_label=f"{object_type.slug}:{target.label}",
            workspace_id=target.owner_workspace_id,
            changes={"aliases": {"before": before, "after": after}},
            reason=f"검토함 복원 — 별칭 후보로 추가한 「{value}」 을(를) 뺌",
        )
    misses.decide(db, user, miss_ids, "restore")
    return True, message


ALIAS_CANDIDATE = registry.Provider(
    kind="alias_candidate",
    label="별칭 후보",
    description="검색 · 이름 풀이에서 여러 번 못 찾은 말 — 어떤 객체의 다른 이름이면 별칭으로 "
    "추가합니다(다음부터 찾힙니다).",
    link="/quality#alias_candidates",
    can_view=_manager,
    collect=collect_candidates,
    check=check_candidate,
    apply=apply_candidate,
    undo=undo_candidate,
    maintenance_keys=("alias_candidates",),
    order=10,
)


# --- 기계가 붙인 별칭(검수 대기) -------------------------------------------------------


def _core(text: str | None) -> str:
    return _NOT_WORD.sub("", compare_key(text or ""))


def _alias_query(user: User) -> Select[Any]:
    obj = ObjectInstance
    other = aliased(ObjectInstance, name="clash_other")
    similarity = func.greatest(
        func.similarity(func.lower(ObjectAlias.value), func.lower(obj.label)),
        func.coalesce(
            func.similarity(func.lower(ObjectAlias.value), func.lower(obj.key)), 0.0
        ),
    )
    # 같은 타입의 **다른 객체**의 이름 · 식별자와 같은 별칭 — 그 표기로 찾으면 둘이 나온다
    # (품질의 「별칭이 다른 객체의 이름과 같음」 과 같은 물음, 대소문자만 모아 견준다).
    clash = (
        select(other.label)
        .where(
            other.type_id == ObjectAlias.type_id,
            other.id != obj.id,
            other.deleted_at.is_(None),
            or_(
                func.lower(other.label) == func.lower(ObjectAlias.value),
                func.lower(other.key) == func.lower(ObjectAlias.value),
            ),
        )
        .limit(1)
        .scalar_subquery()
    )
    return (
        select(
            ObjectAlias,
            obj.label,
            obj.key,
            obj.owner_workspace_id,
            ObjectType,
            similarity.label("similarity"),
            clash.label("clash"),
        )
        .join(obj, obj.id == ObjectAlias.object_id)
        .join(ObjectType, ObjectType.id == ObjectAlias.type_id)
        .where(
            ObjectAlias.kind == aliases.HUMAN,
            ObjectAlias.verified_at.is_(None),
            obj.deleted_at.is_(None),
            ObjectType.is_active.is_(True),
            ObjectType.kind_class != "system",
            visible_owner_clause(user, obj.owner_workspace_id),
        )
    )


def _alias_recommendation(
    value: str, label: str, key: str | None, similarity: float, clash: str | None
) -> tuple[str, float, str]:
    if clash:
        return (
            "remove",
            0.9,
            f"같은 타입의 「{clash}」 의 이름(식별자)과 같음 — 그 표기로 찾으면 둘이 나옴",
        )
    core = _core(value)
    if core and core in {_core(label), _core(key)}:
        return "approve", 0.95, "이름(식별자)과 표기만 다름"
    score = round(min(0.95, 0.5 + 0.45 * float(similarity or 0.0)), 2)
    return "approve", score, f"이름과 비슷함 {float(similarity or 0.0):.2f}"


def collect_aliases(db: Session, user: User, flt: Filters) -> Collected:
    """사람이 아직 안 본 별칭 — 기계(적재 · 동기화)가 붙인 것. 오래된 것부터."""
    stmt = _alias_query(user)
    if flt.item_ids is not None:
        ids = _uuids(flt.item_ids)
        if not ids:
            return Collected(items=[], total=0)
        stmt = stmt.where(ObjectAlias.id.in_(ids))
    if flt.type_slug is not None:
        stmt = stmt.where(ObjectType.slug == flt.type_slug)
    if flt.workspace_global:
        stmt = stmt.where(ObjectInstance.owner_workspace_id.is_(None))
    elif flt.workspace_id is not None:
        stmt = stmt.where(ObjectInstance.owner_workspace_id == flt.workspace_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    found = db.execute(
        stmt.order_by(ObjectAlias.created_at, ObjectAlias.id).limit(ALIAS_CAP)
    ).all()
    notes: list[str] = []
    if total > len(found):
        notes.append(
            f"검수 대기 별칭은 오래된 것부터 {ALIAS_CAP:,}건만 싣습니다 — 나머지 "
            f"{total - len(found):,}건은 이것을 처리한 뒤에 표시됩니다."
        )
    editable = _Editable(db, user)
    items: list[Item] = []
    for alias_row, label, key, workspace_id, object_type, similarity, clash in found:
        choice, confidence, reason = _alias_recommendation(
            alias_row.value, label, key, similarity, clash
        )
        blocked = managed.objects_refusal(object_type, what="별칭을 검수하지")
        blocked = blocked or editable.why_not(workspace_id)
        source = alias_row.source or ""
        items.append(
            Item(
                kind="alias_review",
                id=str(alias_row.id),
                question=f"「{alias_row.value}」 이(가) {label}의 별칭이 맞나요?",
                evidence=[
                    f"{object_type.label} · {label}" + (f" ({key})" if key else ""),
                    f"출처: {source}" if source else "출처 없음",
                    f"메모: {alias_row.note}" if alias_row.note else "",
                    f"붙은 때 {alias_row.created_at:%Y-%m-%d}",
                ],
                choices=[
                    Choice(value="approve", label="확인", detail="정본으로 사용합니다"),
                    Choice(value="remove", label="삭제", detail="이 별칭을 뺍니다"),
                ],
                recommended=choice,
                confidence=confidence,
                reason=reason,
                type_slug=object_type.slug,
                type_label=object_type.label,
                workspace_id=workspace_id,
                rows=1,
                group=f"source:{source}" if source else "",
                group_label=f"출처 「{source}」" if source else "",
                blocked=blocked,
                link=f"/o/{object_type.slug}/{alias_row.object_id}",
                ignorable=False,
                payload={"object_id": str(alias_row.object_id)},
            )
        )
    return Collected(items=items, total=total, notes=notes)


def check_alias(db: Session, user: User, item: Item, value: str) -> Check:
    alias_row = db.get(ObjectAlias, uuid.UUID(item.id))
    if alias_row is None:
        return _blocked("", value, "그 별칭은 이미 없습니다.")
    if alias_row.verified_at is not None:
        return _blocked("", value, "이미 확인한 별칭입니다.")
    if value not in ("approve", "remove"):
        return _blocked("", value, "확인(approve) · 삭제(remove) 중에서 선택하세요.")
    if item.blocked:
        return _blocked("", value, item.blocked)
    owner = db.get(ObjectInstance, alias_row.object_id)
    name = owner.label if owner is not None else "객체"
    if value == "approve":
        return Check(
            status="apply",
            effect=f"「{alias_row.value}」 확인 — {name}의 별칭으로 사용",
            choice_label="확인",
            rows=1,
            undoable=True,
        )
    return Check(
        status="apply",
        effect=f"「{alias_row.value}」 삭제 — {name}의 별칭에서 뺌",
        choice_label="삭제",
        rows=1,
        undoable=True,
    )


def apply_alias(db: Session, user: User, item: Item, value: str, _in_job: bool) -> Applied:
    alias_row = db.get(ObjectAlias, uuid.UUID(item.id))
    if alias_row is None or alias_row.verified_at is not None:
        raise Conflict(code("REVIEW", 6), "이미 처리한 별칭입니다.")
    object_type = db.get(ObjectType, alias_row.type_id)
    if object_type is None:  # pragma: no cover - FK 가 막는다
        raise Conflict(code("REVIEW", 6), "타입이 없습니다.")
    managed.require_objects_editable(object_type, what="별칭을 고치지")
    undo = {
        "op": value,
        "alias_id": str(alias_row.id),
        "object_id": str(alias_row.object_id),
        "value": alias_row.value,
        "source": alias_row.source or "",
        "note": alias_row.note or "",
    }
    done, refused = aliases.review(
        db, user, object_type, [alias_row.id], approve=value == "approve"
    )
    if refused or not done:
        raise Conflict(code("REVIEW", 6), refused[0] if refused else "처리하지 못했습니다.")
    return Applied(rows=1, undo=undo)


def undo_alias(db: Session, user: User, data: dict[str, Any], apply: bool) -> tuple[bool, str]:
    owner = db.get(ObjectInstance, _uuid(str(data.get("object_id"))))
    if owner is None or owner.deleted_at is not None:
        return False, "별칭의 객체가 지워졌습니다."
    object_type = db.get(ObjectType, owner.type_id)
    if object_type is None:  # pragma: no cover - FK 가 막는다
        return False, "타입이 없습니다."
    try:
        require_owner_edit(
            db, user, owner.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
        managed.require_objects_editable(object_type, what="별칭을 고치지")
    except AppError as denied:
        return False, denied.message
    value = str(data.get("value") or "")
    if data.get("op") == "approve":
        alias_row = db.get(ObjectAlias, _uuid(str(data.get("alias_id"))))
        if alias_row is None:
            return False, f"「{value}」 은(는) 이미 지워졌습니다."
        if alias_row.verified_at is None:
            return False, f"「{value}」 은(는) 이미 검수 대기입니다."
        if apply:
            aliases.unverify(db, user, object_type, alias_row, owner)
        return True, f"「{value}」 확인을 거두고 검수 대기로 복원"
    norm = compare_key(value)
    if any(
        compare_key(one) == norm for one in aliases.human_of(db, [owner.id]).get(owner.id, [])
    ):
        return False, f"「{value}」 은(는) 이미 {owner.label}의 별칭입니다."
    holder = aliases.taken_by(db, object_type, aliases.HUMAN, [value]).get(norm)
    if holder is not None and holder != owner.id:
        return False, f"「{value}」 은(는) 그 사이 다른 객체의 별칭이 됐습니다."
    if apply:
        before, after = aliases.set_human(
            db,
            owner,
            object_type,
            [
                aliases.Incoming(
                    value=value,
                    source=str(data.get("source") or ""),
                    note=str(data.get("note") or ""),
                )
            ],
            mode="add",
        )
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=owner.id,
            target_label=f"{object_type.slug}:{owner.label}",
            workspace_id=owner.owner_workspace_id,
            changes={"aliases": {"before": before, "after": after}},
            reason=f"검토함 복원 — 삭제한 별칭 「{value}」 을(를) 검수 대기로 다시",
        )
    return True, f"「{value}」 을(를) {owner.label}의 별칭으로 되살림(검수 대기)"


ALIAS_REVIEW = registry.Provider(
    kind="alias_review",
    label="검수 대기 별칭",
    description="적재 · 동기화가 붙였고 사람이 아직 안 본 별칭 — 확인한 것만 정본으로 씁니다.",
    link="/quality#alias_pending",
    can_view=_manager,
    collect=collect_aliases,
    check=check_alias,
    apply=apply_alias,
    undo=undo_alias,
    maintenance_keys=("quality_alias_pending",),
    order=20,
)


# --- 이름이 같은 객체(병합 후보) --------------------------------------------------------


@dataclass
class _Member:
    id: uuid.UUID
    label: str
    key: str | None
    workspace_id: uuid.UUID | None
    created_at: datetime
    refs: int = 0
    properties: dict[str, Any] | None = None


def _member_label(one: _Member) -> str:
    return one.label + (f" ({one.key})" if one.key else "")


def _owner_name(one: _Member, names: dict[uuid.UUID, str]) -> str:
    if one.workspace_id is None:
        return "전역"
    return names.get(one.workspace_id, "(지워진 부서)")


def _duplicate_types(db: Session, flt: Filters) -> list[ObjectType]:
    stmt = select(ObjectType).where(
        ObjectType.is_active.is_(True), ObjectType.kind_class != "system"
    )
    if flt.type_slug is not None:
        stmt = stmt.where(ObjectType.slug == flt.type_slug)
    if flt.item_ids is not None:
        slugs = {one.split(":", 1)[0] for one in flt.item_ids}
        stmt = stmt.where(ObjectType.slug.in_(slugs) if slugs else false())
    return list(db.scalars(stmt.order_by(ObjectType.sort_order, ObjectType.slug)))


def _winner(members: list[_Member]) -> _Member:
    """남길 것 — **가리키는 것이 많은 것**(옮길 것이 적다) → 식별자가 있는 것 → 먼저 만든
    것."""
    return sorted(
        members,
        key=lambda one: (-one.refs, 0 if one.key else 1, one.created_at, str(one.id)),
    )[0]


def _duplicate_confidence(members: list[_Member], labels: dict[str, str]) -> tuple[float, str]:
    score = 0.5
    why: list[str] = []
    if len({one.workspace_id for one in members}) == 1:
        score += 0.2
        why.append("같은 부서")
    else:
        why.append("부서가 다름")
    keys = {compare_key(one.key) for one in members if one.key}
    if len(keys) >= 2:
        score -= 0.3
        why.append("식별자가 서로 다름 — 다른 것일 수 있음")
    else:
        score += 0.2
        why.append("식별자가 엇갈리지 않음" if keys else "식별자 없음")
    conflicts: list[str] = []
    fields = sorted({key for one in members for key in (one.properties or {})})
    for key in fields:
        values = {
            json.dumps(raw, sort_keys=True, ensure_ascii=False)
            for one in members
            for raw in [(one.properties or {}).get(key)]
            if raw not in (None, "", [])
        }
        if len(values) > 1:
            conflicts.append(labels.get(key, key))
    if conflicts:
        score -= 0.1 * min(len(conflicts), 3)
        why.append(f"값이 엇갈리는 칸 {len(conflicts)}개({', '.join(conflicts[:3])})")
    else:
        score += 0.1
        why.append("값이 엇갈리는 칸 없음")
    return round(min(0.95, max(0.05, score)), 2), " · ".join(why)


def collect_duplicates(db: Session, user: User, flt: Filters) -> Collected:
    """이름을 정규화하면 같은 객체들(`quality.duplicate_key`) — 타입마다, **볼 수 있는 것만.**
    품질과 같은 상한: 객체가 `quality.SCAN_MAX_ROWS` 를 넘는 타입은 훑지 않고 그렇다고
    말한다."""
    ignored = _ignored(db, "duplicate")
    editable = _Editable(db, user)
    names = _workspace_names(db)
    wanted = flt.item_ids
    items: list[Item] = []
    notes: list[str] = []
    total = 0
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    for object_type in _duplicate_types(db, flt):
        alive = (
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            seen,
        )
        size = int(db.scalar(select(func.count()).where(*alive)) or 0)
        if size > quality.SCAN_MAX_ROWS:
            notes.append(
                f"{object_type.label}({size:,}건)은 너무 커서 이름이 같은 것을 훑지 "
                "않았습니다."
            )
            continue
        groups: dict[str, list[_Member]] = defaultdict(list)
        for object_id, label, key, workspace_id, created_at in db.execute(
            select(
                ObjectInstance.id,
                ObjectInstance.label,
                ObjectInstance.key,
                ObjectInstance.owner_workspace_id,
                ObjectInstance.created_at,
            ).where(*alive)
        ):
            groups[quality.duplicate_key(label)].append(
                _Member(object_id, label, key, workspace_id, created_at)
            )
        dupes = {
            norm: members
            for norm, members in groups.items()
            if len(members) > 1
            and (wanted is None or f"{object_type.slug}:{_hash(norm)}" in wanted)
        }
        if not dupes:
            continue
        member_ids = [one.id for members in dupes.values() for one in members]
        refs: dict[uuid.UUID, int] = {}
        props: dict[uuid.UUID, dict[str, Any]] = {}
        for batch in chunks(member_ids):
            refs.update(
                {
                    dst: int(count)
                    for dst, count in db.execute(
                        select(ObjectRef.dst_id, func.count())
                        .where(ObjectRef.dst_id.in_(batch))
                        .group_by(ObjectRef.dst_id)
                    )
                }
            )
            props.update(
                {
                    object_id: dict(values or {})
                    for object_id, values in db.execute(
                        select(ObjectInstance.id, ObjectInstance.properties).where(
                            ObjectInstance.id.in_(batch)
                        )
                    )
                }
            )
        labels = {one.key: one.label for one in properties_of(db, object_type.id)}
        refusal = managed.objects_refusal(object_type, what="병합하지")
        for norm, members in dupes.items():
            for one in members:
                one.refs = refs.get(one.id, 0)
                one.properties = props.get(one.id)
            item_id = f"{object_type.slug}:{_hash(norm)}"
            signature = _hash(*sorted(str(one.id) for one in members))
            if (item_id, signature) in ignored:
                continue
            total += 1
            winner = _winner(members)
            confidence, reason = _duplicate_confidence(members, labels)
            denied = next(
                (
                    why
                    for why in (editable.why_not(one.workspace_id) for one in members)
                    if why
                ),
                None,
            )
            moved = sum(one.refs for one in members if one.id != winner.id)
            items.append(
                Item(
                    kind="duplicate",
                    id=item_id,
                    question=f"「{winner.label}」 {len(members)}개를 하나로 병합할까요?",
                    evidence=[
                        f"{object_type.label} — 이름을 정규화하면 같음",
                        *(
                            f"{_member_label(one)} · {_owner_name(one, names)} · "
                            f"가리키는 것 {one.refs:,}건"
                            for one in members
                        ),
                    ],
                    choices=[
                        Choice(
                            value=str(one.id),
                            label=_member_label(one),
                            detail=(
                                f"{_owner_name(one, names)} · 가리키는 것 {one.refs:,}건 · "
                                f"{one.created_at:%Y-%m-%d} 생성"
                            ),
                        )
                        for one in members
                    ],
                    recommended=str(winner.id),
                    confidence=confidence,
                    reason=f"남길 것: 가리키는 것이 가장 많은 것 · {reason}",
                    type_slug=object_type.slug,
                    type_label=object_type.label,
                    workspace_id=winner.workspace_id,
                    rows=len(members) - 1,
                    blocked=refusal
                    or (f"병합할 수 없는 것이 섞여 있습니다 — {denied}" if denied else None),
                    link=f"/o/{object_type.slug}/{winner.id}",
                    signature=signature,
                    priority=float(len(members) * 10 + moved),
                    heavy=any(
                        one.refs > lifecycle.REWRITE_INLINE
                        for one in members
                        if one.id != winner.id
                    ),
                    payload={
                        "type_id": str(object_type.id),
                        "members": [str(one.id) for one in members],
                        "moved": moved,
                    },
                )
            )
            if len(items) >= DUPLICATE_CAP:
                notes.append(f"병합 후보는 {DUPLICATE_CAP:,}묶음까지 싣습니다.")
                return Collected(items=items, total=total, notes=notes)
    return Collected(items=items, total=total, notes=notes)


def _alive_members(db: Session, item: Item) -> dict[str, ObjectInstance]:
    ids = _uuids(item.payload.get("members") or [])
    return {
        str(row.id): row
        for row in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(ids), ObjectInstance.deleted_at.is_(None)
            )
        )
    }


def check_duplicate(db: Session, user: User, item: Item, value: str) -> Check:
    if item.blocked:
        return _blocked("", value, item.blocked)
    if value == IGNORE:
        return _ignore_check(
            f"{item.question.split(' 하나로')[0]} — 다른 것으로 두고 다시 표시하지 않음"
            "(같은 이름이 더 생기면 다시 표시)"
        )
    members = _alive_members(db, item)
    winner = members.get(value)
    if winner is None:
        return _blocked("", value, "병합할 묶음의 객체 중에서 남길 것을 선택하세요.")
    if len(members) < 2:
        return _blocked("", winner.label, "이미 병합했거나 지워졌습니다.")
    moved = int(item.payload.get("moved") or 0)
    return Check(
        status="apply",
        effect=(
            f"{len(members) - 1}개를 「{winner.label}」 에 병합 — 가리키는 것 {moved:,}건 · "
            "관계 · 별칭 · 첨부가 옮겨 감"
        ),
        choice_label=winner.label,
        message="병합은 복원할 수 없습니다.",
        rows=len(members) - 1,
        undoable=False,
        heavy=item.heavy,
    )


def apply_duplicate(db: Session, user: User, item: Item, value: str, in_job: bool) -> Applied:
    if value == IGNORE:
        return Applied(rows=0, undo={"op": "ignore"})
    members = _alive_members(db, item)
    winner = members.get(value)
    if winner is None or len(members) < 2:
        raise Conflict(code("REVIEW", 6), "이미 병합했거나 지워졌습니다.")
    object_type = db.get(ObjectType, winner.type_id)
    if object_type is None:  # pragma: no cover - FK 가 막는다
        raise Conflict(code("REVIEW", 6), "타입이 없습니다.")
    managed.require_objects_editable(object_type, what="병합하지")
    merged = 0
    for loser in [one for one in members.values() if one.id != winner.id]:
        try:
            # 병합 화면과 같은 문턱 — 지는 쪽도 고칠 수 있어야 한다(`merge_into` 는 이긴 쪽을
            # 본다).
            require_owner_edit(
                db, user, loser.owner_workspace_id, what="객체", code_value=code("OBJECTS", 17)
            )
            # **병합은 스스로 커밋한다** — 앞의 것이 들어간 뒤 뒤의 것이 막히면 들어간 수를
            # 말한다.
            lifecycle.merge_into(db, user, loser, object_type, winner, inline=not in_job)
        except AppError as refused:
            if not merged:
                raise
            db.rollback()
            return Applied(
                rows=merged, message=f"{merged}개만 병합 — 나머지는 막힘: {refused.message}"
            )
        merged += 1
    return Applied(rows=merged)


def undo_ignore_only(
    _db: Session, _user: User, data: dict[str, Any], _apply: bool
) -> tuple[bool, str]:
    """무시만 되돌린다 — 결정 기록이 기억이라 거둘 것이 그것뿐이다(복원하면 다시 표시)."""
    if data.get("op") == "ignore":
        return True, "무시를 거두고 다시 표시"
    return False, "복원할 수 없는 결정입니다."


DUPLICATE = registry.Provider(
    kind="duplicate",
    label="병합 후보",
    description="같은 타입에서 이름을 정규화하면 같은 객체들 — 같은 것이면 하나로 병합합니다.",
    link="/quality#duplicate",
    can_view=_manager,
    collect=collect_duplicates,
    check=check_duplicate,
    apply=apply_duplicate,
    undo=undo_ignore_only,
    undo_note="병합은 복원할 수 없습니다 — 지는 쪽은 「병합됨」 으로 남고 참조 · 관계 · "
    "별칭이 옮겨 갑니다. 무시만 복원합니다.",
    maintenance_keys=("quality_duplicate",),
    order=30,
)


# --- 지워진 것을 가리키는 칸 ------------------------------------------------------------


@dataclass(frozen=True)
class _RefField:
    owner: ObjectType
    definition: PropertyDef
    targets: tuple[ObjectType, ...]


def _ref_fields(db: Session, flt: Filters) -> list[_RefField]:
    types = {
        one.id: one
        for one in db.scalars(
            select(ObjectType).where(
                ObjectType.is_active.is_(True), ObjectType.kind_class != "system"
            )
        )
    }
    out: list[_RefField] = []
    wanted = {tuple(one.split(":", 2)[:2]) for one in flt.item_ids or []}
    for definition in db.scalars(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type", PropertyDef.data_type == "object_ref"
        )
    ):
        owner = types.get(definition.owner_id)
        if owner is None or not definition.ref_type_slug:
            continue
        if flt.type_slug is not None and owner.slug != flt.type_slug:
            continue
        if flt.item_ids is not None and (owner.slug, definition.key) not in wanted:
            continue
        found = find_scope(db, definition.ref_type_slug)
        # 원 표(부서 · 계정)를 가리키는 칸은 거기 물어야 한다 — 객체로 바꿔 넣을 수 없다.
        targets = tuple(
            one for one in (found.types if found else []) if not system.is_system(one)
        )
        if targets:
            out.append(_RefField(owner, definition, targets))
    return out


def _ref_id(owner: str, key: str, dst: uuid.UUID, workspace_id: uuid.UUID | None) -> str:
    return f"{owner}:{key}:{dst}:{workspace_id or '-'}"


def _parse_ref_id(raw: str) -> tuple[str, str, uuid.UUID, uuid.UUID | None] | None:
    parts = raw.split(":")
    if len(parts) != 4:
        return None
    dst = _uuid(parts[2])
    if dst is None:
        return None
    workspace_id = None if parts[3] == "-" else _uuid(parts[3])
    return parts[0], parts[1], dst, workspace_id


@dataclass
class _Dead:
    label: str
    key: str | None
    deleted_at: datetime | None
    merged_into: uuid.UUID | None
    known: bool


def _dead_info(db: Session, user: User, ids: list[uuid.UUID]) -> dict[uuid.UUID, _Dead]:
    out: dict[uuid.UUID, _Dead] = {}
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    for batch in chunks(ids):
        for row in db.execute(
            select(
                ObjectInstance.id,
                ObjectInstance.label,
                ObjectInstance.key,
                ObjectInstance.deleted_at,
                ObjectInstance.merged_into_id,
                seen.label("visible"),
            ).where(ObjectInstance.id.in_(batch))
        ):
            out[row.id] = _Dead(
                label=row.label if row.visible else system.HIDDEN_LABEL,
                key=row.key if row.visible else None,
                deleted_at=row.deleted_at,
                merged_into=row.merged_into_id,
                known=bool(row.visible),
            )
    return out


def _ref_candidates(
    db: Session, user: User, dead: _Dead | None, targets: tuple[ObjectType, ...]
) -> tuple[list[Choice], str]:
    """지워진 것 대신 넣을 것 — 병합된 곳 · 이름(식별자)이 같은 것 · 이름이 비슷한 것."""
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    target_ids = [one.id for one in targets]
    alive = (
        ObjectInstance.type_id.in_(target_ids),
        ObjectInstance.deleted_at.is_(None),
        seen,
    )
    found: dict[str, Choice] = {}
    note = ""

    def put(row: ObjectInstance, score: float, detail: str) -> None:
        old = found.get(str(row.id))
        if old is None or (old.score or 0) < score:
            found[str(row.id)] = Choice(
                value=str(row.id),
                label=row.label + (f" ({row.key})" if row.key else ""),
                detail=detail,
                score=round(score, 2),
            )

    if dead is not None and dead.known:
        # 병합된 곳 — 사슬을 따라 살아 있는 끝까지(다섯 걸음).
        hop = dead.merged_into
        for _ in range(5):
            if hop is None:
                break
            row = db.get(ObjectInstance, hop)
            if row is None:
                break
            if row.deleted_at is None:
                if row.type_id in target_ids and db.scalar(
                    select(ObjectInstance.id).where(ObjectInstance.id == row.id, seen)
                ):
                    put(row, MERGED_SCORE, f"「{dead.label}」 이(가) 병합된 곳")
                break
            hop = row.merged_into_id
        same = list(
            db.scalars(
                select(ObjectInstance)
                .where(
                    *alive,
                    or_(
                        func.lower(ObjectInstance.label) == dead.label.lower(),
                        and_(
                            ObjectInstance.key.isnot(None),
                            func.lower(ObjectInstance.key) == (dead.key or "").lower(),
                        )
                        if dead.key
                        else false(),
                    ),
                )
                .limit(5)
            )
        )
        for row in same:
            put(
                row,
                SAME_NAME_SCORE if len(same) == 1 else SAME_NAME_MANY_SCORE,
                "이름(식별자)이 같음" + ("" if len(same) == 1 else f" — {len(same)}개"),
            )
        size = int(db.scalar(select(func.count()).where(*alive)) or 0)
        if size > REF_SIMILAR_MAX_ROWS:
            note = f"대상이 {size:,}건이라 이름이 비슷한 것은 찾지 않았습니다."
        else:
            score = func.similarity(func.lower(ObjectInstance.label), dead.label.lower())
            for row, similarity in db.execute(
                select(ObjectInstance, score)
                .where(*alive, score >= REF_SIMILAR_MIN)
                .order_by(score.desc(), ObjectInstance.label)
                .limit(3)
            ):
                put(row, float(similarity) * SIMILAR_FACTOR, f"이름이 비슷함 {similarity:.2f}")
    ranked = sorted(found.values(), key=lambda one: (-(one.score or 0), one.label))
    return ranked, note


def collect_broken_refs(db: Session, user: User, flt: Filters) -> Collected:
    """지워진(또는 없는) 객체를 가리키는 칸 — **(타입 · 칸 · 지워진 것 · 소유 부서)마다 한
    줄.** 같은 것을 가리키는 300행은 한 결정이다. 부서로도 가르는 까닭: 한 줄을 고치는 사람은
    그 부서의 관리자라, 섞어 두면 아무도 그 줄 전체를 처리할 수 없다."""
    fields = _ref_fields(db, flt)
    if not fields:
        return Collected(items=[], total=0)
    by_pair = {(one.owner.id, one.definition.key): one for one in fields}
    src = aliased(ObjectInstance, name="ref_src")
    dead_ids = (
        select(ObjectInstance.id)
        .where(
            ObjectInstance.deleted_at.isnot(None),
            select(ObjectRef.src_id).where(ObjectRef.dst_id == ObjectInstance.id).exists(),
        )
        .scalar_subquery()
    )
    # **지운 객체에서 시작한다** — 지운 것은 적고(삭제 표식), 그것을 가리키는 줄은 색인
    # (`dst_id`)으로 찾는다. 참조 색인 전체를 객체 표와 맞대던 첫 모양은 200만 건 DB 에서
    # 3.2 ~ 5.7초였고, 이 모양은 1.0초다(지운 것 21개 · 가리키는 줄 23만, 2026-10-10 실측).
    # 영구 삭제로 **아예 없는** id 를 가리키는 칸은 참조 색인 전체를 훑어야 해서(9.8초) 여기서
    # 세지 않는다 — 데이터 품질이 센다(제공자 설명).
    stmt = (
        select(
            ObjectRef.src_type_id,
            ObjectRef.key,
            ObjectRef.dst_id,
            src.owner_workspace_id,
            func.count(),
        )
        .join(src, src.id == ObjectRef.src_id)
        .where(
            ObjectRef.dst_id.in_(dead_ids),
            tuple_(ObjectRef.src_type_id, ObjectRef.key).in_(list(by_pair)),
            src.deleted_at.is_(None),
            visible_owner_clause(user, src.owner_workspace_id),
        )
        .group_by(
            ObjectRef.src_type_id, ObjectRef.key, ObjectRef.dst_id, src.owner_workspace_id
        )
    )
    if flt.workspace_global:
        stmt = stmt.where(src.owner_workspace_id.is_(None))
    elif flt.workspace_id is not None:
        stmt = stmt.where(src.owner_workspace_id == flt.workspace_id)
    if flt.item_ids is not None:
        parsed = [one for one in (_parse_ref_id(raw) for raw in flt.item_ids) if one]
        stmt = stmt.where(ObjectRef.dst_id.in_([one[2] for one in parsed] or [uuid.uuid4()]))
    groups = db.execute(stmt.order_by(func.count().desc()).limit(REF_CAP + 1)).all()
    notes: list[str] = []
    if len(groups) > REF_CAP:
        notes.append(
            f"지워진 것을 가리키는 칸은 줄이 많은 것부터 {REF_CAP:,}묶음까지 싣습니다."
        )
        groups = groups[:REF_CAP]
    ignored = _ignored(db, "broken_ref")
    dead = _dead_info(db, user, list({row[2] for row in groups}))
    editable = _Editable(db, user)
    candidates: dict[tuple[uuid.UUID, tuple[uuid.UUID, ...]], tuple[list[Choice], str]] = {}
    items: list[Item] = []
    for type_id, key, dst_id, workspace_id, count in groups:
        field = by_pair[(type_id, key)]
        item_id = _ref_id(field.owner.slug, key, dst_id, workspace_id)
        if flt.item_ids is not None and item_id not in flt.item_ids:
            continue
        signature = str(int(count))
        if (item_id, signature) in ignored:
            continue
        info = dead.get(dst_id)
        target_key = (dst_id, tuple(one.id for one in field.targets))
        if target_key not in candidates:
            candidates[target_key] = _ref_candidates(db, user, info, field.targets)
        choices, note = candidates[target_key]
        if note and note not in notes:
            notes.append(note)
        scores = [one.score or 0.0 for one in choices]
        recommended = choices[0].value if choices else None
        confidence: float | None = choices[0].score if choices else None
        reason = (
            choices[0].detail
            if choices
            else "대신할 후보가 없습니다 — 비우거나 직접 선택하세요."
        )
        if confidence is not None and _close(scores):
            confidence = round(confidence * CLOSE_FACTOR, 2)
            reason += f" · 다음 후보 「{choices[1].label}」 와 차이가 작음"
        dead_label = info.label if info else f"없는 객체({str(dst_id)[:8]})"
        field_label = field.definition.label
        evidence = [f"{field.owner.label} · 「{field_label}」 칸 {int(count):,}건"]
        if info and info.deleted_at:
            evidence.append(f"「{dead_label}」 지운 때 {info.deleted_at:%Y-%m-%d}")
        if info is None:
            evidence.append("가리키는 id 의 객체가 이 설치에 없습니다")
        refusal = managed.objects_refusal(field.owner)
        items.append(
            Item(
                kind="broken_ref",
                id=item_id,
                question=(
                    f"「{field_label}」 칸 {int(count):,}건이 지워진 「{dead_label}」 을(를) "
                    "가리킵니다 — 무엇으로 바꿀까요?"
                ),
                evidence=evidence,
                choices=[
                    *choices,
                    Choice(value="clear", label="비움", detail="이 칸에서 참조를 삭제합니다"),
                ],
                recommended=recommended,
                confidence=confidence,
                reason=reason,
                type_slug=field.owner.slug,
                type_label=field.owner.label,
                workspace_id=workspace_id,
                rows=int(count),
                group=str(dst_id),
                group_label=f"지워진 「{dead_label}」",
                blocked=refusal or editable.why_not(workspace_id),
                link=f"/o/{field.owner.slug}?f.{key}.eq={dst_id}",
                signature=signature,
                priority=float(count),
                payload={
                    "type_id": str(type_id),
                    "key": key,
                    "dst_id": str(dst_id),
                    "workspace_id": str(workspace_id) if workspace_id else None,
                    "targets": [str(one.id) for one in field.targets],
                    "dead_label": dead_label,
                    "field_label": field_label,
                },
            )
        )
    return Collected(items=items, total=len(items), notes=notes)


def _ref_where(user: User, item: Item) -> tuple[ColumnElement[bool], ...]:
    """이 묶음의 줄 — 그 칸이 그 지워진 것을 가리키는 살아 있는 객체 중 그 부서 · 볼 수 있는
    것."""
    payload = item.payload
    workspace = (
        _uuid(str(payload.get("workspace_id"))) if payload.get("workspace_id") else None
    )
    return (
        ObjectRef.src_type_id == uuid.UUID(str(payload["type_id"])),
        ObjectRef.key == payload["key"],
        ObjectRef.dst_id == uuid.UUID(str(payload["dst_id"])),
        ObjectInstance.deleted_at.is_(None),
        ObjectInstance.owner_workspace_id == workspace
        if workspace is not None
        else ObjectInstance.owner_workspace_id.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )


def _ref_rows(db: Session, user: User, item: Item) -> list[ObjectInstance]:
    """이 묶음의 줄 — 지금 다시 읽는다(그 사이 고친 줄은 빠진다). **줄 차례(id)로** — 감사
    기록의 차례가 실행마다 같다."""
    return list(
        db.scalars(
            select(ObjectInstance)
            .join(ObjectRef, ObjectRef.src_id == ObjectInstance.id)
            .where(*_ref_where(user, item))
            .order_by(ObjectInstance.id)
        )
    )


def _ref_values(
    db: Session, user: User, item: Item
) -> list[tuple[uuid.UUID, uuid.UUID | None, Any]]:
    """미리 보기의 줄 — 객체를 통째로 읽지 않고 (id, 부서, 그 칸의 값)만. 차례는 `_ref_rows`
    와 같다."""
    key = str(item.payload["key"])
    return [
        (object_id, workspace_id, value)
        for object_id, workspace_id, value in db.execute(
            select(
                ObjectInstance.id,
                ObjectInstance.owner_workspace_id,
                ObjectInstance.properties[key],
            )
            .join(ObjectRef, ObjectRef.src_id == ObjectInstance.id)
            .where(*_ref_where(user, item))
            .order_by(ObjectInstance.id)
        )
    ]


def _ref_target(db: Session, user: User, item: Item, value: str) -> ObjectInstance | None:
    """고른 대신할 것 — **그 칸의 대상 타입(인터페이스면 구현한 타입)이고 살아 있고 이 사람이
    볼 수 있어야** 한다. 묶음의 모든 줄이 같은 것을 받으니 여기서 한 번 본다(줄마다의 검사도
    같은 것을 다시 본다 — `_refuse_dead_refs`)."""
    object_id = _uuid(value)
    if object_id is None:
        return None
    targets = _uuids(item.payload.get("targets") or [])
    return db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.type_id.in_(targets),
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )


@dataclass
class _RefChange:
    """한 줄의 계획 — 그 칸의 지금 값과 새 값, 바뀌나(`change` · `unchanged` · `error`)."""

    id: uuid.UUID
    workspace_id: uuid.UUID | None
    before: Any
    after: Any
    action: str = "change"
    message: str = ""

    def refuse(self, message: str) -> None:
        self.action, self.message = "error", message


def _ref_set(raw: Any) -> set[uuid.UUID]:
    """칸 값(글자 하나 · 목록)이 가리키는 id 들."""
    items = raw if isinstance(raw, list) else [raw]
    return set(_uuids(item for item in items if isinstance(item, str) and item))


def _ref_changes(
    db: Session,
    user: User,
    item: Item,
    new: str | None,
    current: list[tuple[uuid.UUID, uuid.UUID | None, Any]],
) -> tuple[ObjectType, PropertyDef, list[_RefChange]]:
    """줄마다 새 값(여러 값 칸이면 그 자리만 바꾼다 — 병합과 같은 규칙)을 만들고 **바뀌는 그
    칸만** 줄마다 검사한다. 아무것도 안 바꾼다. `current` 는 (id, 부서, 그 칸의 지금 값).

    검사는 저장 검사(`bulkedit._one`)의 그 칸 몫이다 — 고칠 권한 · 칸의 모양(값 하나 / 목록,
    비우면 필수) · 가리키는 것이 살아 있나(같은 칸에 남는 다른 값까지) · 새로 적은 것을 볼 수
    있나 · 칸의 대상 타입(인터페이스 포함)인가 · 유일. 말도 저장 검사의 그 말이다.

    ⚠️ **다른 칸은 다시 검증하지 않는다** — 이 결정이 안 건드리는 칸이다. 일괄 수정의 계획은
       줄마다 속성 전체(칸 서른 개)를 검증하고 참조마다 질의 셋을 보냈다 — 1,000행에 3.0초,
       예전 길의 줄당 4.8ms 중 3ms 가 그것이었다(200만 건 DB, 2026-10-10 실측). 그 덕에 한 줄에
       지워진 것을 가리키는 칸이 둘이어도 이제 한 칸씩 바꾼다(예전에는 다른 칸이 저장 검사를 못
       넘어 그 줄이 늘 빠졌다).
    """
    object_type = db.get(ObjectType, uuid.UUID(str(item.payload["type_id"])))
    if object_type is None:  # pragma: no cover - 수집이 살아 있는 타입만 낸다
        raise Conflict(code("REVIEW", 6), "타입이 없습니다.")
    key = str(item.payload["key"])
    definition = next(
        (one for one in properties_of(db, object_type.id) if one.key == key), None
    )
    if definition is None or definition.data_type != "object_ref":
        raise Conflict(
            code("REVIEW", 6),
            f"「{item.payload.get('field_label') or key}」 칸이 지금은 참조 칸이 아닙니다 — "
            "다시 미리 보세요.",
        )
    old = str(item.payload["dst_id"])
    groups: dict[str, list[_RefChange]] = {}
    for object_id, workspace_id, value in current:
        after = lifecycle.replace_ref({key: value}, key, old, new).get(key)
        groups.setdefault(json.dumps(after, sort_keys=True), []).append(
            _RefChange(object_id, workspace_id, value, after)
        )
    editable = _Editable(db, user)
    for members in groups.values():
        # 새 값의 모양은 값마다 한 번 — 값 하나 칸이면 묶음 전체가 같은 값이다.
        try:
            cleaned = validate_properties([definition], {key: members[0].after}).get(key)
            problem = ""
        except AppError as caught:
            cleaned, problem = None, caught.message
        for one in members:
            if editable.why_not(one.workspace_id):
                one.refuse("이 부서의 것은 고칠 수 없습니다.")
            elif problem:
                one.refuse(problem)
            else:
                one.after = cleaned
    # 같은 새 값끼리 — 예전 길(일괄 수정은 값 하나씩 계획했다)과 같은 차례, 그 안은 줄 차례.
    changes = [one for members in groups.values() for one in members]
    _refuse_dead_refs(db, user, definition, changes)
    if definition.unique:
        _refuse_taken(db, object_type, definition, changes)
    for one in changes:
        if one.action == "change" and one.after == one.before:
            one.action = "unchanged"
    if definition.unique:
        _refuse_same(object_type, definition, changes)
    return object_type, definition, changes


def _refuse_dead_refs(
    db: Session, user: User, definition: PropertyDef, changes: list[_RefChange]
) -> None:
    """가리키는 것이 살아 있나(**같은 칸에 남는 다른 값까지** — 여러 값 칸에 지워진 것이 하나
    더 있으면 그 줄은 저장할 수 없다) · 새로 적은 것을 볼 수 있나 · 칸의 대상 타입인가.
    저장 검사(`services.require_refs_exist`)와 같은 판정 · 같은 말이고, 줄마다 묻지 않고
    **모아서 한 번** 묻는다(대상 타입은 새로 적은 것이 같은 줄끼리 한 번)."""
    going = [one for one in changes if one.action == "change"]
    if not going:
        return
    pointed = {one.id: _ref_set(one.after) for one in going}
    fresh = {one.id: pointed[one.id] - _ref_set(one.before) for one in going}
    alive: set[uuid.UUID] = set()
    for part in chunks(sorted(set().union(*pointed.values()))):
        alive.update(
            db.scalars(
                select(ObjectInstance.id).where(
                    any_of(ObjectInstance.id, part), ObjectInstance.deleted_at.is_(None)
                )
            )
        )
    hidden = system.hidden_objects(db, user, set().union(*fresh.values()))
    wrong: dict[frozenset[uuid.UUID], list[str]] = {}
    for one in going:
        missing = [str(found) for found in sorted(pointed[one.id]) if found not in alive]
        missing += [
            str(found) for found in sorted(hidden & fresh[one.id]) if str(found) not in missing
        ]
        if missing:
            one.refuse(f"가리키는 객체를 찾을 수 없습니다: {', '.join(missing)}")
            continue
        written = frozenset(fresh[one.id])
        if not written:
            continue
        if written not in wrong:
            wrong[written] = system.wrong_type_refs(
                db, [definition], {definition.key: sorted(str(found) for found in written)}
            )
        if wrong[written]:
            one.refuse(f"참조 칸의 대상이 아닌 것을 가리킵니다: {'; '.join(wrong[written])}")


def _refuse_taken(
    db: Session, object_type: ObjectType, definition: PropertyDef, changes: list[_RefChange]
) -> None:
    """유일한 칸 — 그 값을 이미 쓰는 객체가 있나(저장 검사의 그 함수, 줄마다)."""
    for one in changes:
        if one.action != "change" or one.after in (None, "", []):
            continue
        try:
            require_unique_properties(
                db,
                object_type,
                [definition],
                {definition.key: one.after},
                owner_workspace_id=one.workspace_id,
                exclude_id=one.id,
            )
        except AppError as caught:
            one.refuse(caught.message)


def _refuse_same(
    object_type: ObjectType, definition: PropertyDef, changes: list[_RefChange]
) -> None:
    """유일한 칸 — **이 묶음 안에서** 둘이 같은 값이 되면 겹친 줄은 모두 오류(일괄 수정의
    `_refuse_same_in_batch` 와 같은 규칙 · 같은 말). 범위가 부서면 부서마다."""
    groups: dict[tuple[str, str], list[_RefChange]] = {}
    for one in changes:
        if one.action != "change" or one.after in (None, "", []):
            continue
        scope = str(one.workspace_id) if object_type.key_scope == "workspace" else ""
        groups.setdefault((json.dumps(one.after, sort_keys=True), scope), []).append(one)
    for hits in groups.values():
        if len(hits) < 2:
            continue
        for one in hits:
            one.refuse(
                f"{definition.label}: 이 묶음의 {len(hits)}건이 같은 값이 됩니다 — 유일해야 "
                "하는 칸이라 같은 것이 둘이 되면 둘 다 못 믿게 됩니다."
            )


def _write_ref_changes(
    db: Session,
    user: User,
    object_type: ObjectType,
    definition: PropertyDef,
    changes: list[_RefChange],
    rows: dict[uuid.UUID, ObjectInstance],
    *,
    batch_id: uuid.UUID,
    reason: str,
) -> int:
    """바뀌는 줄을 쓴다 — 바뀐 줄 수.

    값은 ORM 객체에서 **그 칸만** 고치고, 기록은 일괄 수정의 묶음 기록(`bulkedit.record` —
    줄마다 같은 묶음 번호 · 사람이 고친 칸 표시 · 감사 한 줄과 바깥 알림)이다. 쓰기만 일괄
    입력의 길(`bulk._Writes`)로 묶는다: 고친 객체는 덩어리 UPDATE 한 문장(`copyin.update_rows`
    — 참조 색인 트리거도 덩어리마다 한 번), 감사 줄은 COPY(`audit.Batch`). 되돌릴 기록(묶음
    판)은 검토함에서 열리지 않는다 — 복원은 묶음 표식에서 그때 값을 읽는다(`undo_broken_ref`).

    ⚠️ 예전에는 일괄 수정의 적용(`apply_to` — 속성 전체를 다시 검증) · flush(줄마다 UPDATE 와
       트리거) · `audit.record`(ORM — 1,000줄짜리 INSERT … RETURNING)였다. 1,000행에 쓰기만
       1.5초(UPDATE 1.2 · 그중 트리거 0.5 · 감사 0.3, 200만 건 DB, 2026-10-10 실측).
    """
    key = definition.key
    field_name = f"properties.{key}"
    going = [one for one in changes if one.action == "change" and one.id in rows]
    changed = {one.id for one in going}
    writes = bulk._Writes(db, ObjectInstance.__table__)
    for part in chunks(going, bulk.WRITE_EVERY):
        batch = [rows[one.id] for one in part]
        before_state = {row.id: bulkedit.state_of(row) for row in batch}
        before_raw = {row.id: bulkedit.raw_value(row, field_name) for row in batch}
        for one, row in zip(part, batch, strict=True):
            values = dict(row.properties or {})
            if one.after in (None, "", []):
                values.pop(key, None)
            else:
                values[key] = one.after
            row.properties = values
        bulkedit.record(
            db,
            user,
            object_type,
            rows=batch,
            before_state=before_state,
            before_raw=before_raw,
            changed=changed,
            field_name=field_name,
            field_label=definition.label,
            batch_id=batch_id,
            reason=reason,
            sink=writes.audit,
        )
        writes.changed.extend(batch)
        writes.write()
    return len(going)


def check_broken_ref(db: Session, user: User, item: Item, value: str) -> Check:
    if item.blocked:
        return _blocked("", value, item.blocked)
    dead = str(item.payload.get("dead_label") or "")
    field = str(item.payload.get("field_label") or "")
    if value == IGNORE:
        return _ignore_check(
            f"「{field}」 칸 {item.rows:,}건을 그대로 둠 — 다시 표시하지 않음(가리키는 줄이 "
            "늘면 다시 표시)"
        )
    if value == "clear":
        label, new = "비움", None
        effect = f"「{field}」 칸 {item.rows:,}건에서 「{dead}」 을(를) 지움"
    else:
        target = _ref_target(db, user, item, value)
        if target is None:
            return _blocked(
                "",
                value,
                "그 칸에 지정할 수 없는 객체입니다 — 대상 타입이 아니거나 볼 수 없습니다.",
            )
        label, new = target.label, str(target.id)
        effect = f"「{field}」 칸 {item.rows:,}건: 「{dead}」 → 「{target.label}」"
    message = ""
    rows = item.rows
    if item.rows <= REF_CHECK_ROWS:
        # 줄마다 그 칸의 저장 검사를 미리 돌려 본다 — 막힐 줄을 먼저 센다.
        _, _, changes = _ref_changes(db, user, item, new, _ref_values(db, user, item))
        counts = Counter(one.action for one in changes)
        first_error = next((one.message for one in changes if one.action == "error"), "")
        rows = counts["change"]
        if not rows:
            return _blocked(
                effect, label, first_error or "바꿀 줄이 없습니다 — 이미 처리했습니다."
            )
        if counts["error"]:
            message = f"{counts['error']:,}건은 저장할 수 없어 제외합니다 — {first_error}"
    else:
        message = "줄이 많아 저장 검사는 적용할 때 줄마다 합니다(막힌 줄은 제외합니다)."
    return Check(
        status="apply",
        effect=effect,
        choice_label=label,
        message=message,
        rows=rows,
        undoable=True,
    )


def apply_broken_ref(
    db: Session, user: User, item: Item, value: str, _in_job: bool
) -> Applied:
    if value == IGNORE:
        return Applied(rows=0, undo={"op": "ignore"})
    if value == "clear":
        new: str | None = None
        label = "비움"
    else:
        target = _ref_target(db, user, item, value)
        if target is None:
            raise Conflict(code("REVIEW", 7), "그 칸에 지정할 수 없는 객체입니다.")
        new, label = str(target.id), target.label
    key = str(item.payload["key"])
    rows = _ref_rows(db, user, item)
    object_type, definition, changes = _ref_changes(
        db,
        user,
        item,
        new,
        [(row.id, row.owner_workspace_id, (row.properties or {}).get(key)) for row in rows],
    )
    managed.require_objects_editable(object_type)
    batch_id = uuid.uuid4()
    changed_total = _write_ref_changes(
        db,
        user,
        object_type,
        definition,
        changes,
        {row.id: row for row in rows},
        batch_id=batch_id,
        reason=f"검토함 — 지워진 「{item.payload.get('dead_label')}」 대신 「{label}」",
    )
    errors = [one.message for one in changes if one.action == "error"]
    if not changed_total:
        raise Conflict(
            code("REVIEW", 6),
            errors[0] if errors else "바꿀 줄이 없습니다 — 이미 처리했습니다.",
        )
    return Applied(
        rows=changed_total,
        message=f"{len(errors):,}건은 저장하지 못했습니다 — {errors[0]}" if errors else "",
        undo={"op": "bulk_edit", "batch_id": str(batch_id), "type_slug": object_type.slug},
    )


def undo_broken_ref(
    db: Session, user: User, data: dict[str, Any], apply: bool
) -> tuple[bool, str]:
    """그 묶음이 바꾼 줄을 **그때 값(지워진 것을 가리키던 값)으로** 돌린다.

    일괄 수정의 묶음 되돌리기(`bulkedit.undo_plan`)를 그대로 쓰지 못한다 — 그것은 저장 검사를
    다시 거치고, 「가리키는 것이 있어야 한다」 는 검사가 **지워진 것을 다시 가리키는 값**을
    거절한다(실측 2026-10-10: 복원이 늘 「막힘」). 그래서 줄마다의 전 · 후는 그 묶음의 기록
    (`_batch` — 일괄 수정과 같은 기록)에서 읽고, 쓰기는 병합 · 참조 비우기의 길(`rewrite`)로
    그 칸만 돌린다. 규칙은 묶음 되돌리기와 같다 — **그 뒤에 누가 또 고친 줄은 덮어쓰지
    않는다.**
    """
    if data.get("op") == "ignore":
        return True, "무시를 거두고 다시 표시"
    batch_id = _uuid(str(data.get("batch_id")))
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == data.get("type_slug")))
    if batch_id is None or object_type is None:
        return False, "복원할 묶음을 찾을 수 없습니다."
    refusal = managed.objects_refusal(object_type)
    if refusal:
        return False, refusal
    entries = bulkedit.batch_entries(db, batch_id)
    rows = {
        row.id: row
        for row in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_([one.target_id for one in entries if one.target_id]),
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    }
    editable = _Editable(db, user)
    writes: list[tuple[uuid.UUID, bool, Any]] = []
    history: list[tuple[uuid.UUID, str, uuid.UUID | None, dict[str, Any]]] = []
    skipped: Counter[str] = Counter()
    key = ""
    for entry in entries:
        meta = (entry.changes or {}).get(bulkedit.BATCH_KEY) or {}
        field_name = str(meta.get("field") or "")
        key = field_name.split(".", 1)[-1]
        row = rows.get(entry.target_id) if entry.target_id else None
        if row is None:
            skipped["찾을 수 없음(지워졌거나 볼 수 없음)"] += 1
            continue
        now = bulkedit.raw_value(row, field_name)
        was, put = meta.get("before"), meta.get("after")
        if now == was:
            skipped["이미 그때 값"] += 1
            continue
        if now != put:
            skipped["그 뒤에 다시 바뀜"] += 1
            continue
        if editable.why_not(row.owner_workspace_id):
            skipped["고칠 권한 없음"] += 1
            continue
        gone = was in (None, "", [])
        writes.append((row.id, gone, was))
        history.append(
            (
                row.id,
                f"{object_type.slug}:{row.label}",
                row.owner_workspace_id,
                rewrite.changed_property(key, now, {} if gone else {key: was}),
            )
        )
    message = f"{len(writes):,}건을 그때 값으로"
    if skipped:
        message += " · 빼는 줄: " + ", ".join(f"{why} {n}건" for why, n in skipped.items())
    if not writes:
        return False, message
    if apply:
        db.flush()
        rewrite.write_key(db, key, writes)
        audit.record_rows(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            rows=history,
            reason="검토함 복원 — 지워진 것을 가리키던 그때 값으로",
        )
        for row_id, _gone, _was in writes:
            db.expire(rows[row_id])
    return True, message


BROKEN_REF = registry.Provider(
    kind="broken_ref",
    label="지워진 것을 가리키는 칸",
    description="참조 칸이 지워진 객체를 가리킵니다 — 같은 것을 가리키는 줄을 한 번에 다른 "
    "객체로 바꾸거나 비웁니다. 영구 삭제로 아예 없어진 것을 가리키는 칸은 데이터 품질에서 "
    "셉니다.",
    link="/quality#broken_ref",
    can_view=_manager,
    collect=collect_broken_refs,
    check=check_broken_ref,
    apply=apply_broken_ref,
    undo=undo_broken_ref,
    maintenance_keys=("quality_broken_ref",),
    order=40,
)

PROVIDERS = (ALIAS_CANDIDATE, ALIAS_REVIEW, DUPLICATE, BROKEN_REF)
