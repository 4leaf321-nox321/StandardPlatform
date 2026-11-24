"""정의 제안(ADR 0096) — 정의를 **적용 대신 쌓고**, 시스템 관리자가 검토함에서 적용 ·
거절한다.

바깥 AI 가 문서 30개에서 뽑은 타입 12개를 `ontology_import(apply=True)` 로 넣으면 바로
반영되고, 확인은 그 대화창 속 한 사람뿐이다. 시스템 관리자가 아닌 사람의 AI 는 정의를
아예 못 냈다.

- **제안** = 보낸 정의(`schema`, 정의 가져오기의 본문) + 그때 센 계획(바뀌는 것 · 경고 ·
  비슷한 정의, ADR 0091) + 계획의 지문 + 근거 · 누가 · 어느 도구. 계획에 오류가 있거나
  바뀌는 것이 없으면 받지 않는다 — 보낸 쪽이 고쳐 다시 낸다. 같은 사람이 같은 계획을 또
  내면 있는 것을 돌려준다.
- **적용**은 지금 정의로 다시 센 계획의 지문이 **제안 때와 같을 때만** 한다. 그새 다른
  가져오기 · 화면 수정이 정의를 바꿔 계획이 달라졌으면 막고 「다시 셈」 을 고르게 한다 —
  사람이 읽은 계획과 다른 것이 적용되면 안 된다. 적용은 정의 가져오기와 같은 길이다
  (스냅샷 · `importer.apply` · 감사 `ontology.import`).
- **권한**: 제안은 로그인한 누구나(토큰은 `objects:write` — `ontology:write` 가 없어도),
  적용 · 거절은 시스템 관리자(검토함 `definition_proposal`). 제안한 사람은 자기 제안을
  철회 · 다시 셈한다.

검토함 제공자는 `proposal_review.py`, 경로는 `proposal_routes.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.ontology import importer, planview, similar
from app.modules.ontology.models import OntologyProposal, OntologySnapshot
from app.shared.audit import record as record_audit
from app.shared.errors import Conflict, Forbidden, NotFound, code

STATUS_LABELS = {
    "proposed": "제안",
    "accepted": "적용",
    "rejected": "거절",
    "withdrawn": "철회",
}
#: 한 사람이 열어 둘 수 있는 제안 — 넘으면 처리된 뒤에 더 낸다(검토함이 한 사람 것으로
#: 덮이지 않게).
OPEN_PER_USER = 50
#: 보낸 정의의 크기 상한(바이트, JSON) — 정의 가져오기 한 번의 몫.
SCHEMA_MAX_BYTES = 2_000_000
#: 「다시 셈」 이 필요하다는 오류.
STALE = code("ONTOLOGY", 108)


def _invalid(message: str, **details: Any) -> Conflict:
    return Conflict(code("ONTOLOGY", 107), message, details=details or None)


def compute(db: Session, schema: dict[str, Any], *, source: str) -> tuple[importer.Plan, str]:
    """지금 정의로 센 계획과 그 지문. 아무것도 안 바꾼다. 모르는 항목이면 ONTOLOGY-70 —
    정의 가져오기와 같은 말."""
    try:
        prepared = importer.plan(db, schema, source=source)
    except ValueError as caught:
        raise Conflict(code("ONTOLOGY", 70), str(caught)) from None
    return prepared, planview.fingerprint(prepared)


def _store_plan(
    db: Session,
    row: OntologyProposal,
    prepared: importer.Plan,
    fingerprint: str,
) -> None:
    found = similar.check(db, row.schema, prepared.changes)
    out = planview.plan_out(prepared, applied=False, snapshot_id=None, found=found)
    # 원천 잠금 없이 새 정의를 세우면 — 가져오기가 토큰 호출에 다는 것과 같은 경고. 제안은
    # 대개 기계가 내고, 「다시 셈」 은 관리자가 화면에서 해도 같은 말이 남아야 한다.
    warning = None if row.source else planview.unlocked_warning(prepared)
    if warning:
        out.warnings.append(warning)
    row.plan = out.model_dump(mode="json")
    row.fingerprint = fingerprint
    row.summary = planview.summary(prepared)[:300]
    row.planned_at = datetime.now(UTC)


def _changed(prepared: importer.Plan) -> int:
    return sum(1 for one in prepared.changes if one.action != "unchanged")


def propose(
    db: Session,
    user: User,
    schema: dict[str, Any],
    *,
    source: str = "",
    note: str = "",
    origin_ref: str = "",
    tool: str = "",
    token_name: str = "",
) -> tuple[OntologyProposal, bool]:
    """제안을 남긴다 — (제안, 새로 만들었나). **부르는 쪽이 커밋한다.**"""
    size = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    if size > SCHEMA_MAX_BYTES:
        raise _invalid(
            f"정의가 너무 큽니다({size:,}바이트 — {SCHEMA_MAX_BYTES:,}까지). 나눠서 "
            "제안하세요."
        )
    prepared, fingerprint = compute(db, schema, source=source)
    if prepared.errors:
        raise _invalid(
            "계획에 오류가 있어 제안할 수 없습니다 — 고쳐서 다시 제안하세요: "
            + " / ".join(prepared.errors[:5]),
            errors=prepared.errors[:50],
        )
    if not _changed(prepared):
        raise _invalid("바뀌는 것이 없습니다 — 이미 그 정의입니다.")
    same = db.scalar(
        select(OntologyProposal).where(
            OntologyProposal.status == "proposed",
            OntologyProposal.proposed_by_id == user.id,
            OntologyProposal.fingerprint == fingerprint,
            OntologyProposal.source == source,
        )
    )
    if same is not None:
        return same, False
    opened = int(
        db.scalar(
            select(func.count())
            .select_from(OntologyProposal)
            .where(
                OntologyProposal.status == "proposed",
                OntologyProposal.proposed_by_id == user.id,
            )
        )
        or 0
    )
    if opened >= OPEN_PER_USER:
        raise _invalid(
            f"처리를 기다리는 제안이 {opened}건입니다 — {OPEN_PER_USER}건까지. 처리된 뒤에 더 "
            "제안하세요."
        )
    row = OntologyProposal(
        schema=schema,
        source=source,
        note=note.strip(),
        origin_ref=origin_ref.strip()[:300],
        tool=tool[:64],
        token_name=token_name[:120],
        proposed_by_id=user.id,
    )
    _store_plan(db, row, prepared, fingerprint)
    db.add(row)
    db.flush()
    record_audit(
        db,
        action="ontology.proposal.create",
        actor=user,
        target_table="ontology_proposals",
        target_id=row.id,
        target_label=row.summary,
        changes={"source": source, "origin_ref": row.origin_ref, "tool": row.tool},
    )
    return row, True


def get(db: Session, proposal_id: uuid.UUID) -> OntologyProposal:
    row = db.get(OntologyProposal, proposal_id)
    if row is None:
        raise NotFound(code("ONTOLOGY", 110), "그 정의 제안이 없습니다.")
    return row


def require_open(row: OntologyProposal) -> None:
    if row.status != "proposed":
        raise Conflict(
            code("ONTOLOGY", 109),
            f"이미 처리된 제안입니다({STATUS_LABELS.get(row.status, row.status)}).",
        )


def require_owner_or_admin(user: User, row: OntologyProposal) -> None:
    if not user.is_system_admin and row.proposed_by_id != user.id:
        raise Forbidden(code("ONTOLOGY", 111), "제안한 사람과 시스템 관리자만 할 수 있습니다.")


def replan(db: Session, user: User, row: OntologyProposal) -> bool:
    """지금 정의로 계획을 다시 센다 — 달라졌나. **부르는 쪽이 커밋한다.** 오류가 생겨도 남긴다
    (그 계획을 보고 거절하게)."""
    require_open(row)
    prepared, fingerprint = compute(db, row.schema, source=row.source)
    changed = fingerprint != row.fingerprint
    _store_plan(db, row, prepared, fingerprint)
    record_audit(
        db,
        action="ontology.proposal.replan",
        actor=user,
        target_table="ontology_proposals",
        target_id=row.id,
        target_label=row.summary,
        changes={"changed": changed},
    )
    return changed


def stale_message(row: OntologyProposal) -> str:
    return (
        "제안 뒤 정의가 바뀌어 계획이 달라졌습니다 — 「다시 셈」 으로 새 계획을 보고 다시 "
        "고르세요(읽은 계획과 다른 것을 적용하지 않습니다)."
    )


def blocker(db: Session, row: OntologyProposal) -> tuple[str | None, importer.Plan | None]:
    """적용을 막는 것 — (까닭, 지금 계획). 막는 것이 없으면 (None, 계획)."""
    if row.status != "proposed":
        return f"이미 처리된 제안입니다({STATUS_LABELS.get(row.status, row.status)}).", None
    try:
        prepared, fingerprint = compute(db, row.schema, source=row.source)
    except Conflict as caught:
        return caught.message, None
    if fingerprint != row.fingerprint:
        return stale_message(row), prepared
    if prepared.errors:
        return "계획에 오류가 있습니다 — " + " / ".join(prepared.errors[:3]), prepared
    return None, prepared


def accept(
    db: Session, user: User, row: OntologyProposal, *, note: str = ""
) -> OntologySnapshot:
    """적용 — 지금 계획의 지문이 제안 때와 같을 때만. **부르는 쪽이 커밋한다.**"""
    denied, _prepared = blocker(db, row)
    if denied is not None:
        raise Conflict(
            STALE if denied == stale_message(row) else code("ONTOLOGY", 109), denied
        )
    snapshot = importer.take_snapshot(db, user, reason=f"정의 제안 적용 — {row.summary}"[:200])
    try:
        applied = importer.apply(
            db, row.schema, source=row.source, actor=user, reason="정의 제안 적용"
        )
    except ValueError as caught:
        raise Conflict(code("ONTOLOGY", 70), str(caught)) from None
    if applied.errors:
        raise Conflict(code("ONTOLOGY", 109), "적용하지 못했습니다 — " + applied.errors[0])
    changed = _changed(applied)
    # 정의 가져오기와 같은 감사 한 줄 — 「누가 언제 정의를 바꿨나」 는 한 자리에서 찾는다.
    record_audit(
        db,
        action="ontology.import",
        actor=user,
        target_table="ontology_snapshots",
        target_id=snapshot.id,
        target_label=f"{changed}건",
        reason=f"정의 제안 {row.id}",
    )
    row.status = "accepted"
    row.decided_by_id = user.id
    row.decided_at = datetime.now(UTC)
    row.decision_note = note[:500]
    row.snapshot_id = snapshot.id
    record_audit(
        db,
        action="ontology.proposal.accept",
        actor=user,
        target_table="ontology_proposals",
        target_id=row.id,
        target_label=row.summary,
        changes={"snapshot_id": str(snapshot.id), "changes": changed},
    )
    return snapshot


def reject(db: Session, user: User, row: OntologyProposal, *, note: str = "") -> None:
    require_open(row)
    row.status = "rejected"
    row.decided_by_id = user.id
    row.decided_at = datetime.now(UTC)
    row.decision_note = note[:500]
    record_audit(
        db,
        action="ontology.proposal.reject",
        actor=user,
        target_table="ontology_proposals",
        target_id=row.id,
        target_label=row.summary,
        reason=note or None,
    )


def withdraw(db: Session, user: User, row: OntologyProposal) -> None:
    require_owner_or_admin(user, row)
    require_open(row)
    row.status = "withdrawn"
    row.decided_by_id = user.id
    row.decided_at = datetime.now(UTC)
    record_audit(
        db,
        action="ontology.proposal.withdraw",
        actor=user,
        target_table="ontology_proposals",
        target_id=row.id,
        target_label=row.summary,
    )


def reopen(db: Session, user: User, row: OntologyProposal, *, apply: bool) -> tuple[bool, str]:
    """거절을 되돌린다 — 다시 열린 제안으로(검토함의 복원)."""
    if row.status != "rejected":
        return False, "그 제안의 상태가 그 사이 바뀌었습니다 — 복원할 것이 없음."
    if apply:
        row.status = "proposed"
        row.decided_by_id = None
        row.decided_at = None
        row.decision_note = ""
        record_audit(
            db,
            action="ontology.proposal.reopen",
            actor=user,
            target_table="ontology_proposals",
            target_id=row.id,
            target_label=row.summary,
        )
    return True, "거절을 거두고 다시 제안으로"


def open_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(OntologyProposal)
            .where(OntologyProposal.status == "proposed")
        )
        or 0
    )
