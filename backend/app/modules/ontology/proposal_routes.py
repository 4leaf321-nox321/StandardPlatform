"""정의 제안의 경로(ADR 0096) — 내고 · 보고 · 다시 세고 · 철회한다.

적용 · 거절은 **검토함**(`definition_proposal`)에서만 한다 — 결정 기록 · 되돌리기 ·
「같은 것끼리 묶기」 가 거기 있다. 길을 둘 두면 한쪽에서 처리한 것이 다른 쪽 기록에 안
남는다.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.ontology import proposals
from app.modules.ontology.models import OntologyProposal
from app.modules.ontology.schemas import ImportPlanOut, ProposalOut, ProposalRequest
from app.shared.auth import current_user
from app.shared.request_context import get_actor_token

router = APIRouter(prefix="/ontology/proposals", tags=["ontology"])

#: MCP 가 싣는 도구 이름(ADR 0078) — 모양이 아니면 버린다(아무나 적는 머리다).
_TOOL = re.compile(r"[a-z][a-z0-9_]{0,63}")
LIST_LIMIT = 200


def _people(db: Session, rows: list[OntologyProposal]) -> dict[uuid.UUID, str]:
    ids = {one for row in rows for one in (row.proposed_by_id, row.decided_by_id) if one}
    if not ids:
        return {}
    return {
        one.id: (one.display_name or one.email)
        for one in db.scalars(select(User).where(User.id.in_(ids)))
    }


def to_out(
    row: OntologyProposal,
    people: dict[uuid.UUID, str],
    *,
    created: bool = False,
    replanned: bool | None = None,
) -> ProposalOut:
    return ProposalOut(
        id=row.id,
        status=row.status,
        status_label=proposals.STATUS_LABELS.get(row.status, row.status),
        summary=row.summary,
        source=row.source,
        note=row.note,
        origin_ref=row.origin_ref,
        tool=row.tool,
        token_name=row.token_name,
        proposed_by=people.get(row.proposed_by_id, "") if row.proposed_by_id else "",
        created_at=row.created_at,
        planned_at=row.planned_at,
        decided_by=people.get(row.decided_by_id, "") if row.decided_by_id else "",
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        snapshot_id=row.snapshot_id,
        plan=ImportPlanOut.model_validate(row.plan),
        fingerprint=row.fingerprint,
        created=created,
        replanned=replanned,
    )


@router.post("", response_model=ProposalOut, status_code=201)
def propose(
    payload: ProposalRequest,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalOut:
    """정의를 **적용 대신 제안**한다 — 시스템 관리자가 검토함에서 적용 · 거절한다.

    본문의 `schema` 는 정의 가져오기와 같다. 그 자리에서 계획(바뀌는 것 · 경고 · 비슷한 정의)을
    세어 함께 남긴다 — 계획에 오류가 있거나 바뀌는 것이 없으면 ONTOLOGY-107(고쳐서 다시).
    같은 사람이 같은 계획을 또 내면 있는 제안을 돌려준다(200, `created=false`).
    """
    raw_tool = (request.headers.get("x-sp-tool") or "").strip()
    row, created = proposals.propose(
        db,
        user,
        payload.schema_,
        source=payload.source,
        note=payload.note,
        origin_ref=payload.origin_ref,
        tool=raw_tool if _TOOL.fullmatch(raw_tool) else "",
        token_name=get_actor_token() or "",
    )
    db.commit()
    db.refresh(row)
    if not created:
        response.status_code = 200
    return to_out(row, _people(db, [row]), created=created)


@router.get("", response_model=list[ProposalOut])
def list_proposals(
    status: str = Query(
        default="proposed", pattern=r"^(proposed|accepted|rejected|withdrawn|all)$"
    ),
    mine: bool = Query(default=False, description="내가 낸 것만"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ProposalOut]:
    """정의 제안 — 시스템 관리자는 전부, 그 밖은 자기가 낸 것만. 새것부터 200건."""
    stmt = select(OntologyProposal).order_by(OntologyProposal.created_at.desc())
    if status != "all":
        stmt = stmt.where(OntologyProposal.status == status)
    if mine or not user.is_system_admin:
        stmt = stmt.where(OntologyProposal.proposed_by_id == user.id)
    rows = list(db.scalars(stmt.limit(LIST_LIMIT)))
    people = _people(db, rows)
    return [to_out(row, people) for row in rows]


def _visible(db: Session, user: User, proposal_id: uuid.UUID) -> OntologyProposal:
    row = proposals.get(db, proposal_id)
    proposals.require_owner_or_admin(user, row)
    return row


@router.get("/{proposal_id}", response_model=ProposalOut)
def get_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalOut:
    row = _visible(db, user, proposal_id)
    return to_out(row, _people(db, [row]))


@router.post("/{proposal_id}/replan", response_model=ProposalOut)
def replan_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalOut:
    """지금 정의로 계획을 다시 센다 — 그새 정의가 바뀌어 적용이 막혔을 때(ONTOLOGY-108).
    `replanned` 가 계획이 달라졌나다."""
    row = _visible(db, user, proposal_id)
    changed = proposals.replan(db, user, row)
    db.commit()
    db.refresh(row)
    return to_out(row, _people(db, [row]), replanned=changed)


@router.post("/{proposal_id}/withdraw", response_model=ProposalOut)
def withdraw_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProposalOut:
    """제안을 거둔다 — 제안한 사람 · 시스템 관리자."""
    row = proposals.get(db, proposal_id)
    proposals.withdraw(db, user, row)
    db.commit()
    db.refresh(row)
    return to_out(row, _people(db, [row]))
