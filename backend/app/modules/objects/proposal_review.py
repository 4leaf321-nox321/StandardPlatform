"""검토함의 **제안된 관계**(ADR 0042 · ADR 0027) — 열린 제안마다 한 항목.

    proposed_relation   제안된 관계   relation_proposals   proposals.accept · reject · reopen

원래 자리(`relation_proposals`)에서 읽고, 처리는 원래 기능(`objects/proposals.py` — 객체 화면의
「확정」 · 「거절」 과 같은 함수, 같은 감사)을 부른다. 확정은 「관계 추가」 와 같은 길이라 개수
제약 · 순환 · 기간 겹침에 걸리면 그 항목만 거절된다.

- 항목 = 제안 하나. 근거 = 제안의 근거 · 확신도 · 누가/무엇이 · 기간 · 속성.
- 후보 둘 — 「확정」(accept) · 「거절」(reject).
- **추천은 확신도가 `RECOMMEND_FROM`(0.5) 이상일 때만 「확정」**, 항목의 확신도는 그 제안의
  확신도다 — 「추천대로 모두」 는 다른 제공자와 같은 문턱(기본 0.9)으로 고른다. 확신도가 없거나
  그보다 낮으면 추천도 확신도도 없다(사람이 고른다) — 낮은 확신도로 「확정」 을 추천하면
  추천 단추가 낮은 값에 힘을 실어 준다. 거절을 추천하지 않는 까닭: 제안한 쪽이 낮게 매긴 것은
  「아닐 것」 이 아니라 「모른다」 다.
- 같은 출처(출처 + 출처 글 — 같은 대화 · 규칙 · 파일)는 「같은 것끼리 묶기」 로 접힌다.
- 보는 사람: 검토함과 같은 문턱(부서 관리자 이상). 출발 객체가 보이는 제안만 항목이 되고,
  출발 객체를 고칠 수 없으면 막힘으로 선다. 도착 객체를 못 보면 이름을 「(볼 수 없는 객체)」
  로 가리고 막힘이다(확정은 도착점을 볼 수 있어야 한다 — 「관계 추가」 와 같다).
- 「무시」 는 결정 기록에만 기억한다 — 지문은 제안의 근거 · 확신도라, 더 높은 확신도로 다시
  오면(근거 · 확신도가 바뀌면) 다시 뜬다.
- 되돌리기: 확정 → 그 선을 해제하고 다시 엶(그 뒤 선이 고쳐졌으면 건너뜀), 거절 → 다시 엶.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects import access, edges, proposals, reviewkit, system
from app.modules.objects.models import ObjectInstance, RelationProposal
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType, RelationType
from app.modules.review import registry
from app.modules.review.registry import (
    IGNORE,
    Applied,
    Check,
    Choice,
    Collected,
    Filters,
    Item,
)
from app.shared.errors import AppError, Conflict, code
from app.shared.permissions import is_any_manager, visible_owner_clause

KIND = "proposed_relation"
#: 한 번에 읽는 열린 제안 — 확신도 높은 것부터. 처리하면 다음 것이 뜬다.
CAP = 2000
#: 이 확신도부터 「확정」 을 추천한다 — 그 아래는 추천 없음(사람이 고른다).
RECOMMEND_FROM = 0.5

CHOICES = (
    Choice(
        value="accept",
        label="확정",
        detail="이 제안으로 관계를 추가 — 「연결」 과 같은 검사(개수 제약 · 순환 · 기간 겹침)",
    ),
    Choice(
        value="reject",
        label="거절",
        detail="제안을 거절 — 같은 제안이 다시 와도 만들지 않고 「거절된 적 있음」 으로 답함",
    ),
)


def _manager(db: Session, user: User) -> bool:
    return is_any_manager(db, user)


def _signature(row: RelationProposal) -> str:
    return reviewkit.digest(
        str(row.id),
        row.evidence_note,
        "" if row.confidence is None else f"{row.confidence:.4f}",
    )


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


def _evidence(row: RelationProposal, proposer: str) -> list[str]:
    who = proposals.ORIGIN_LABELS.get(row.origin, row.origin)
    ref = f" 「{row.origin_ref}」" if row.origin_ref else ""
    lines = [
        f"근거: {row.evidence_note}" if row.evidence_note else "근거: (적지 않음)",
        f"확신도: {row.confidence:.2f}" if row.confidence is not None else "확신도: (없음)",
        f"제안: {who}{ref}" + (f" · {proposer}" if proposer else ""),
    ]
    if row.valid_from is not None or row.valid_to is not None:
        lines.append(f"기간: {edges.describe(row.valid_from, row.valid_to)}")
    if row.properties:
        shown = ", ".join(f"{key}={value}" for key, value in sorted(row.properties.items()))
        lines.append(f"속성: {shown}")
    return lines


def collect(db: Session, user: User, flt: Filters) -> Collected:
    """열린 제안 — 양 끝이 살아 있고 출발 객체가 보이는 것만, 확신도 높은 것부터."""
    src = aliased(ObjectInstance)
    dst = aliased(ObjectInstance)
    stmt = (
        select(RelationProposal, src, dst)
        .join(src, src.id == RelationProposal.src_object_id)
        .join(dst, dst.id == RelationProposal.dst_object_id)
        .where(
            RelationProposal.status == "proposed",
            src.deleted_at.is_(None),
            dst.deleted_at.is_(None),
            visible_owner_clause(user, src.owner_workspace_id),
        )
    )
    if flt.item_ids is not None:
        wanted = [uuid.UUID(one) for one in flt.item_ids if _is_uuid(one)]
        if not wanted:
            return Collected(items=[], total=0)
        stmt = stmt.where(RelationProposal.id.in_(wanted))
    if flt.type_slug is not None:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == flt.type_slug))
        if type_id is None:
            return Collected(items=[], total=0)
        stmt = stmt.where(or_(src.type_id == type_id, dst.type_id == type_id))
    if flt.workspace_global:
        stmt = stmt.where(src.owner_workspace_id.is_(None))
    elif flt.workspace_id is not None:
        stmt = stmt.where(src.owner_workspace_id == flt.workspace_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    if not total:
        return Collected(items=[], total=0)
    rows = list(
        db.execute(
            stmt.order_by(
                RelationProposal.confidence.desc().nulls_last(),
                RelationProposal.created_at.desc(),
            ).limit(CAP)
        )
    )
    types = {one.id: one for one in db.scalars(select(ObjectType))}
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    hidden = system.hidden_objects(db, user, {d.id for _p, _s, d in rows})
    people_ids = {p.proposed_by_id for p, _s, _d in rows if p.proposed_by_id}
    people = {
        one.id: (one.display_name or one.email)
        for one in db.scalars(select(User).where(User.id.in_(people_ids)))
    }
    ignored = reviewkit.ignored(db, KIND)
    editor = access.Editor(db, user)
    items: list[Item] = []
    for row, s, d in rows:
        signature = _signature(row)
        if (str(row.id), signature) in ignored:
            continue
        s_type = types.get(s.type_id)
        d_type = types.get(d.type_id)
        if s_type is None:
            continue
        kind = kinds.get(row.relation)
        kind_label = kind.label if kind else row.relation
        dst_label = system.HIDDEN_LABEL if d.id in hidden else d.label
        blocked: str | None = None
        if kind is None or not kind.is_active:
            blocked = (
                f"관계 종류 「{kind_label}」 이(가) 없거나 쓰지 않는 종류입니다 — 거절하세요."
            )
        elif managed.owner_of(kind):
            blocked = (
                f"허브({managed.owner_of(kind)})가 관리하는 관계 종류라 확정하지 않습니다."
            )
        elif not editor(s_type, s):
            blocked = "출발 객체를 수정할 수 있는 사람(그 부서의 관리자)만 확정 · 거절합니다."
        elif d.id in hidden:
            blocked = "도착 객체를 볼 수 없어 확정할 수 없습니다."
        recommended = (
            "accept"
            if row.confidence is not None and row.confidence >= RECOMMEND_FROM
            else None
        )
        confidence = row.confidence if recommended else None
        reason = (
            f"제안한 쪽의 확신도 {row.confidence:.2f} — 「확정」 을 앞에 둡니다."
            if recommended
            else (
                f"확신도가 {RECOMMEND_FROM} 아래라 추천하지 않습니다 — 근거를 보고 고르세요."
                if row.confidence is not None
                else "확신도가 없는 제안이라 추천하지 않습니다 — 근거를 보고 고르세요."
            )
        )
        group = f"{row.origin}:{row.origin_ref}" if row.origin_ref else ""
        items.append(
            Item(
                kind=KIND,
                id=str(row.id),
                question=f"「{s.label}」 —{kind_label}→ 「{dst_label}」 을(를) 이을까요?",
                evidence=[
                    f"{s_type.label} → {d_type.label if d_type else '알 수 없음'}",
                    *_evidence(
                        row, people.get(row.proposed_by_id, "") if row.proposed_by_id else ""
                    ),
                ],
                choices=list(CHOICES),
                recommended=recommended,
                confidence=confidence,
                reason=reason,
                type_slug=s_type.slug,
                type_label=s_type.label,
                workspace_id=s.owner_workspace_id,
                group=group,
                group_label=(
                    f"{proposals.ORIGIN_LABELS.get(row.origin, row.origin)} "
                    f"「{row.origin_ref}」 의 제안"
                    if group
                    else ""
                ),
                blocked=blocked,
                link=f"/o/{s_type.slug}/{s.id}",
                signature=signature,
                priority=float(row.confidence or 0.0),
                payload={"proposal_id": str(row.id)},
            )
        )
    notes: list[str] = []
    if total > CAP:
        notes.append(
            f"열린 제안이 {total:,}건이라 확신도 높은 {CAP:,}건만 읽었습니다 — 처리하면 다음 "
            "것이 표시됩니다."
        )
    return Collected(items=items, total=total, notes=notes)


def _proposal(db: Session, item: Item) -> RelationProposal:
    found = db.get(RelationProposal, uuid.UUID(item.id))
    if found is None:
        raise Conflict(code("REVIEW", 6), "그 제안이 그 사이 철회됐거나 삭제됐습니다.")
    return found


def check(db: Session, user: User, item: Item, value: str) -> Check:
    if value == IGNORE:
        return Check(
            status="apply",
            effect=f"「{item.question}」 무시 — 근거 · 확신도가 바뀌기 전까지 표시하지 않음",
            choice_label="무시",
            rows=0,
            undoable=True,
        )
    choice = next((one for one in CHOICES if one.value == value), None)
    if choice is None:
        return Check(
            status="blocked",
            effect="",
            choice_label=value,
            message="확정(accept) · 거절(reject) 중에서 선택하세요.",
        )
    effect = item.question.replace(" 을(를) 이을까요?", "")
    try:
        row = _proposal(db, item)
    except AppError as gone:
        return Check(
            status="blocked", effect="", choice_label=choice.label, message=gone.message
        )
    if value == "accept":
        denied = proposals.blocker(db, user, row)
        effect = f"{effect} 관계 추가(제안 확정)"
    else:
        denied = proposals.reject_blocker(db, user, row)
        effect = f"{effect} 제안 거절"
    if denied is not None:
        return Check(
            status="blocked", effect=effect, choice_label=choice.label, message=denied.message
        )
    return Check(
        status="apply", effect=effect, choice_label=choice.label, rows=1, undoable=True
    )


def apply(db: Session, user: User, item: Item, value: str, _in_job: bool) -> Applied:
    if value == IGNORE:
        return Applied(rows=0, undo={"op": "ignore"})
    row = _proposal(db, item)
    if value == "accept":
        edge = proposals.accept(db, user, row, note="검토함 — 확정")
        return Applied(
            rows=1,
            undo={"op": "accept", "proposal_id": str(row.id), "relation_id": str(edge.id)},
        )
    if value == "reject":
        proposals.reject(db, user, row, note="검토함 — 거절")
        return Applied(rows=1, undo={"op": "reject", "proposal_id": str(row.id)})
    raise Conflict(code("REVIEW", 6), "확정(accept) · 거절(reject) 중에서 선택하세요.")


def undo(db: Session, user: User, data: dict[str, Any], apply: bool) -> tuple[bool, str]:
    if data.get("op") == "ignore":
        return True, "무시를 거두고 다시 표시"
    if data.get("op") not in ("accept", "reject"):
        return False, "복원할 수 없는 결정입니다."
    row = db.get(RelationProposal, uuid.UUID(str(data.get("proposal_id"))))
    if row is None:
        return False, "그 제안이 삭제됐습니다."
    if (
        data.get("op") == "accept"
        and row.status == "accepted"
        and str(row.relation_id or "") not in ("", str(data.get("relation_id")))
    ):
        return False, "그 제안은 그 사이 다른 관계로 확정됐습니다."
    expected = "accepted" if data.get("op") == "accept" else "rejected"
    if row.status != expected:
        return False, "그 제안의 상태가 그 사이 바뀌었습니다 — 복원할 것이 없음."
    return proposals.reopen(db, user, row, apply=apply)


PROPOSED_RELATION = registry.Provider(
    kind=KIND,
    label="제안된 관계",
    description="AI · 규칙 · 데이터 소스 · 권한 없는 사람이 남긴 「이어야 할 것 같은」 관계 — "
    "확정하면 관계가 추가되고(「연결」 과 같은 검사), 거절하면 같은 제안이 다시 와도 "
    "만들지 않습니다.",
    link="/review?kind=proposed_relation",
    can_view=_manager,
    collect=collect,
    check=check,
    apply=apply,
    undo=undo,
    maintenance_keys=("relation_proposals",),
    order=55,
)

PROVIDERS = (PROPOSED_RELATION,)
