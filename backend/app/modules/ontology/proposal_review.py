"""검토함의 **정의 제안**(ADR 0096 · ADR 0027) — 열린 제안마다 한 항목.

    definition_proposal   정의 제안   ontology_proposals   proposals.accept · replan · reject

원래 자리(`ontology_proposals`)에서 읽고, 처리는 `ontology/proposals.py` 를 부른다(경로와 같은
함수 · 같은 감사).

- 항목 = 제안 하나. 근거 = 근거 · 근거 문서 · 누가 · 어느 도구 · 원천 잠금, 그리고 **계획**
  (바뀌는 것 · 경고 · 오류 · 비슷한 정의 — 제안 때 또는 마지막 「다시 셈」 때 센 것).
- 후보 셋 — 「적용」(accept) · 「다시 셈」(replan) · 「거절」(reject). **추천은 없다** — 정의는
  사람이 읽고 고른다(「추천대로 모두」 가 정의를 적용하지 않는다).
- 「적용」 은 미리 보기에서 지금 정의로 계획을 다시 센다. 지문이 제안 때와 다르면 막고
  「다시 셈」 을 고르게 한다 — 사람이 읽은 계획과 다른 것을 적용하지 않는다. 다시 센 계획은
  지문이 바뀌어 새 항목처럼 다시 뜬다.
- 보는 사람: 시스템 관리자. 정의는 전역이라 부서로 거르면 없다(전역은 전부).
- 「무시」 는 없다 — 제안은 적용하거나 거절한다(무시하면 낸 쪽은 영영 기다린다).
- 되돌리기: 거절 → 다시 엶. 적용은 스냅샷 복원으로(관리 › 온톨로지 › 가져오기의 스냅샷).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.ontology import planview, proposals
from app.modules.ontology.models import OntologyProposal
from app.modules.review import registry
from app.modules.review.registry import Applied, Check, Choice, Collected, Filters, Item
from app.shared.errors import AppError, Conflict, code
from app.shared.extensions import MaintenanceItem

KIND = "definition_proposal"
LINK = "/admin/ontology/proposals"
#: 한 번에 읽는 열린 제안 — 오래된 것부터. 처리하면 다음 것이 뜬다.
CAP = 500
#: 근거에 싣는 바뀌는 것 · 경고의 줄 수 — 넘는 것은 원래 화면(정의 제안)에서.
CHANGE_LINES = 20
WARNING_LINES = 8

CHOICES = (
    Choice(
        value="accept",
        label="적용",
        detail="이 정의를 적용 — 정의 가져오기와 같은 길(스냅샷을 남기고 한 트랜잭션으로). "
        "지금 "
        "정의로 다시 센 계획이 제안 때와 같을 때만",
    ),
    Choice(
        value="replan",
        label="다시 셈",
        detail="지금 정의로 계획을 다시 센다 — 그새 정의가 바뀌었을 때. 달라졌으면 새 "
        "계획으로 "
        "다시 표시",
    ),
    Choice(
        value="reject",
        label="거절",
        detail="제안을 거절 — 낸 사람 · AI 가 목록에서 거절과 까닭을 본다",
    ),
)
ACTION_WORDS = {"create": "새로", "update": "수정"}


def _admin(_db: Session, user: User) -> bool:
    return bool(user.is_system_admin)


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


def _evidence(row: OntologyProposal, proposer: str) -> list[str]:
    plan = row.plan or {}
    who = proposer or "(알 수 없음)"
    if row.tool:
        who += f" · AI 도구 {row.tool}"
    elif row.token_name:
        who += f" · 토큰 「{row.token_name}」"
    lines = [
        f"근거: {row.note}" if row.note else "근거: (적지 않음)",
        *([f"근거 문서: {row.origin_ref}"] if row.origin_ref else []),
        f"제안: {who} · {row.created_at:%Y-%m-%d %H:%M}",
        *([f"원천 잠금: {row.source}"] if row.source else []),
        f"계획: {row.summary} (센 때 {row.planned_at:%Y-%m-%d %H:%M})",
    ]
    changed = [one for one in plan.get("changes") or [] if one.get("action") != "unchanged"]
    for one in changed[:CHANGE_LINES]:
        kind = planview.KIND_WORDS.get(one.get("kind", ""), one.get("kind", ""))
        action = ACTION_WORDS.get(one.get("action", ""), one.get("action", ""))
        fields = one.get("fields") or []
        shown = (
            f" — {', '.join(fields[:6])}" + (" …" if len(fields) > 6 else "") if fields else ""
        )
        lines.append(f"· {kind} {action}: {one.get('slug', '')}{shown}")
    if len(changed) > CHANGE_LINES:
        lines.append(f"· … 외 {len(changed) - CHANGE_LINES}건(정의 제안 화면에서 전부)")
    warnings = list(plan.get("warnings") or [])
    lines += [f"경고: {one}" for one in warnings[:WARNING_LINES]]
    if len(warnings) > WARNING_LINES:
        lines.append(f"경고 … 외 {len(warnings) - WARNING_LINES}건")
    lines += [f"오류: {one}" for one in (plan.get("errors") or [])[:WARNING_LINES]]
    return lines


def _first_type(row: OntologyProposal) -> str:
    for one in (row.plan or {}).get("changes") or []:
        if one.get("kind") == "type" and one.get("action") != "unchanged":
            return str(one.get("slug") or "")
    return ""


def _touches(row: OntologyProposal, slug: str) -> bool:
    return any(
        one.get("slug") == slug or str(one.get("slug") or "").startswith(f"{slug}.")
        for one in (row.plan or {}).get("changes") or []
    )


def collect(db: Session, user: User, flt: Filters) -> Collected:
    """열린 제안 — 오래된 것부터."""
    if flt.workspace_id is not None:
        # 정의는 전역이다 — 부서로 거르면 없다.
        return Collected(items=[], total=0)
    stmt = select(OntologyProposal).where(OntologyProposal.status == "proposed")
    if flt.item_ids is not None:
        wanted = [uuid.UUID(one) for one in flt.item_ids if _is_uuid(one)]
        if not wanted:
            return Collected(items=[], total=0)
        stmt = stmt.where(OntologyProposal.id.in_(wanted))
    rows = list(db.scalars(stmt.order_by(OntologyProposal.created_at).limit(CAP + 1)))
    if flt.type_slug is not None:
        rows = [one for one in rows if _touches(one, flt.type_slug)]
    cut = rows[:CAP]
    people_ids = {one.proposed_by_id for one in cut if one.proposed_by_id}
    people = {
        one.id: (one.display_name or one.email)
        for one in db.scalars(select(User).where(User.id.in_(people_ids)))
    }
    items: list[Item] = []
    for row in cut:
        errors = (row.plan or {}).get("errors") or []
        first = _first_type(row)
        group = f"ref:{row.origin_ref}" if row.origin_ref else ""
        items.append(
            Item(
                kind=KIND,
                id=str(row.id),
                question=f"정의 제안 「{row.summary}」 을(를) 적용할까요?",
                evidence=_evidence(
                    row, people.get(row.proposed_by_id, "") if row.proposed_by_id else ""
                ),
                choices=list(CHOICES),
                recommended=None,
                confidence=None,
                reason=(
                    "마지막 계획에 오류가 있습니다 — 「다시 셈」 하거나 거절하세요."
                    if errors
                    else "정의는 사람이 계획을 읽고 고릅니다 — 추천하지 않습니다."
                ),
                type_slug=first,
                type_label="정의",
                workspace_id=None,
                rows=sum(
                    1
                    for one in (row.plan or {}).get("changes") or []
                    if one.get("action") != "unchanged"
                ),
                group=group,
                group_label=f"「{row.origin_ref}」 에서 낸 제안" if group else "",
                link=f"{LINK}#{row.id}",
                signature=row.fingerprint,
                ignorable=False,
                payload={"proposal_id": str(row.id)},
            )
        )
    notes: list[str] = []
    total = len(items)
    if len(rows) > CAP:
        # 상한을 넘으면 원래 자리에 있는 수를 센다 — 읽은 수(CAP + 1)를 전부라고 하지 않는다.
        total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        notes.append(
            f"열린 제안이 {total:,}건이라 오래된 {CAP}건만 읽었습니다 — 처리하면 다음 것이 "
            "표시됩니다."
        )
    return Collected(items=items, total=total, notes=notes)


def _row(db: Session, item: Item) -> OntologyProposal:
    found = db.get(OntologyProposal, uuid.UUID(item.id))
    if found is None:
        raise Conflict(code("REVIEW", 6), "그 제안이 그 사이 삭제됐습니다.")
    return found


def check(db: Session, _user: User, item: Item, value: str) -> Check:
    choice = next((one for one in CHOICES if one.value == value), None)
    if choice is None:
        return Check(
            status="blocked",
            effect="",
            choice_label=value,
            message="적용(accept) · 다시 셈(replan) · 거절(reject) 중에서 선택하세요.",
        )
    try:
        row = _row(db, item)
    except AppError as gone:
        return Check(
            status="blocked", effect="", choice_label=choice.label, message=gone.message
        )
    if row.status != "proposed":
        return Check(
            status="blocked",
            effect="",
            choice_label=choice.label,
            message=f"이미 처리된 제안입니다({proposals.STATUS_LABELS.get(row.status)}).",
        )
    if value == "replan":
        return Check(
            status="apply",
            effect=f"「{row.summary}」 — 지금 정의로 계획을 다시 셈"
            "(달라졌으면 새 계획으로 다시 표시)",
            choice_label=choice.label,
            rows=0,
        )
    if value == "reject":
        return Check(
            status="apply",
            effect=f"정의 제안 「{row.summary}」 거절",
            choice_label=choice.label,
            rows=1,
            undoable=True,
        )
    denied, prepared = proposals.blocker(db, row)
    if denied is not None:
        return Check(
            status="blocked",
            effect=f"정의 제안 「{row.summary}」 적용",
            choice_label=choice.label,
            message=denied,
        )
    assert prepared is not None
    return Check(
        status="apply",
        effect=f"정의 제안 「{row.summary}」 적용(스냅샷을 남김, 지문 {row.fingerprint[:8]})",
        choice_label=choice.label,
        rows=item.rows,
        undoable=False,
        # 종류가 바뀌는 속성이 있으면 저장된 값을 바꾼다 — 요청 안에서 하기에 클 수 있다.
        heavy=bool(prepared.retypes),
    )


def apply(db: Session, user: User, item: Item, value: str, _in_job: bool) -> Applied:
    row = _row(db, item)
    if value == "accept":
        snapshot = proposals.accept(db, user, row, note="검토함 — 적용")
        return Applied(
            rows=item.rows,
            message="적용했습니다 — 되돌리려면 스냅샷 "
            f"{snapshot.taken_at:%Y-%m-%d %H:%M} 복원",
            undo={"op": "accept", "proposal_id": str(row.id), "snapshot_id": str(snapshot.id)},
        )
    if value == "replan":
        changed = proposals.replan(db, user, row)
        return Applied(
            rows=0,
            message=(
                f"계획이 달라졌습니다 — 「{row.summary}」 로 다시 표시합니다."
                if changed
                else "계획이 그대로입니다 — 그대로 다시 표시합니다."
            ),
            undo={"op": "replan"},
        )
    if value == "reject":
        proposals.reject(db, user, row, note="검토함 — 거절")
        return Applied(rows=1, undo={"op": "reject", "proposal_id": str(row.id)})
    raise Conflict(
        code("REVIEW", 6), "적용(accept) · 다시 셈(replan) · 거절(reject) 중에서 선택하세요."
    )


def undo(db: Session, user: User, data: dict[str, object], apply: bool) -> tuple[bool, str]:
    op = data.get("op")
    if op == "accept":
        return False, "적용한 정의는 스냅샷 복원으로 되돌립니다(관리 › 온톨로지 › 가져오기)."
    if op == "replan":
        return False, "다시 셈은 되돌릴 것이 없습니다."
    if op != "reject":
        return False, "복원할 수 없는 결정입니다."
    row = db.get(OntologyProposal, uuid.UUID(str(data.get("proposal_id"))))
    if row is None:
        return False, "그 제안이 삭제됐습니다."
    return proposals.reopen(db, user, row, apply=apply)


def maintenance(db: Session, viewer: User) -> list[MaintenanceItem]:
    """홈 「남은 일」 — 열린 정의 제안의 수(시스템 관리자). 누르면 검토함의 그 종류로 간다."""
    if not viewer.is_system_admin:
        return []
    count = proposals.open_count(db)
    if not count:
        return []
    return [
        MaintenanceItem(
            key="ontology_proposals",
            label="정의 제안",
            count=count,
            link=f"/review?kind={KIND}",
        )
    ]


DEFINITION_PROPOSAL = registry.Provider(
    kind=KIND,
    label="정의 제안",
    description="AI · 사람이 적용 대신 낸 정의(새 타입 · 속성 · 관계 종류 …) — 계획을 읽고 "
    "적용하면 "
    "정의 가져오기와 같은 길로 들어가고(스냅샷을 남김), 거절하면 낸 쪽이 까닭을 봅니다. 그새 "
    "정의가 바뀌었으면 「다시 셈」.",
    link=LINK,
    can_view=_admin,
    collect=collect,
    check=check,
    apply=apply,
    undo=undo,
    undo_note="적용한 정의는 스냅샷 복원으로 되돌립니다.",
    maintenance_keys=("ontology_proposals",),
    order=50,
)

PROVIDERS = (DEFINITION_PROPOSAL,)
