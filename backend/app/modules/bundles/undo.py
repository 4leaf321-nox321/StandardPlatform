"""묶음 한 판을 **통째로 되돌린다.**

## 왜 있나

백필은 한 번에 수만 줄을 넣는다. 그 판이 틀렸을 때(원천의 열을 잘못 맞췄다, 정제 규칙이
틀렸다) 되돌릴 길이 없었다 — 화면에서 객체를 하나씩 고치는 것뿐이고, 수만 줄에는 그 길이
없다. 그래서 넣으면서 적어 둔 기록(`journal`)을 거꾸로 읽어 그 판만 되돌린다.

## 무엇을 지키나

- **거꾸로 읽는다.** 뒤 단계가 앞 단계의 것을 가리킨다(관계가 객체를, 무덤이 합친 것을).
  선을 먼저 끊어야 그 객체를 지울 수 있다.
- **그 사이 남이 고친 것은 안 되돌린다.** 지금 값이 우리가 넣은 값과 다르면 그 줄은 건너뛰고
  이유를 적는다 — 남의 변경을 조용히 덮는 것이 되돌리지 않는 것보다 나쁘다.
- **밖에서 가리키는 것이 생겼으면 안 지운다.** 이 판이 만든 객체를 그 뒤에 누가 참조했으면,
  지우면 그 참조가 빈 칸이 된다. 그 줄은 건너뛰고 무엇이 걸렸는지 적는다.
- **계획이 먼저다.** 다른 모든 일괄 작업과 같은 모양(`bulk.Plan`)으로 「무엇이 되돌아가나」
  를 보여 주고, 사람이 누르면 적용한다.
- **합치기는 되돌리지 않는다.** 합치기는 참조를 옮긴 것이라 자동으로 풀면 어느 참조가
  원래 어느 쪽 것이었는지 알 수 없다 — 그 줄은 이유를 적고 사람에게 넘긴다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, and_, func, or_, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles.models import BundleRun, BundleUndoEntry
from app.modules.objects import aliases, bulk, lifecycle
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.objects.services import audit_state
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit
from app.shared.errors import AppError, Forbidden, NotFound, code
from app.shared.permissions import require_owner_edit
from app.shared.text import compare_key

#: 한 질의에 담을 id 수 — 수만 개를 한 `IN` 에 넣으면 계획 수립이 먼저 느려진다.
CHUNK = 500


@dataclass
class Outcome:
    run: BundleRun
    plan: bulk.Plan
    applied: bool = False

    @property
    def ok(self) -> bool:
        return self.plan.ok


def recent(db: Session, user: User, *, limit: int = 30) -> list[BundleRun]:
    """최근 판들 — 시스템 관리자는 전부, 나머지는 자기가 넣은 것."""
    stmt = select(BundleRun).order_by(BundleRun.at.desc(), BundleRun.id.desc()).limit(limit)
    if not user.is_system_admin:
        stmt = stmt.where(BundleRun.actor_id == user.id)
    return list(db.scalars(stmt))


def undo_counts(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """판마다 **되돌릴 줄이 몇 개 남았나** — 목록이 「되돌릴 수 있다」 를 말하는 근거.

    보관 기간이 지나면 줄을 지운다(판은 남는다) — 그때 「되돌리기」 를 내밀면 눌러 본 뒤에야
    안 된다는 것을 안다.
    """
    if not run_ids:
        return {}
    rows = db.execute(
        select(BundleUndoEntry.run_id, func.count())
        .where(BundleUndoEntry.run_id.in_(run_ids))
        .group_by(BundleUndoEntry.run_id)
    )
    return {run_id: int(count) for run_id, count in rows}


def find(db: Session, user: User, run_id: uuid.UUID) -> BundleRun:
    run = db.get(BundleRun, run_id)
    if run is None:
        raise NotFound(code("BUNDLES", 30), "그 판을 찾을 수 없습니다.")
    if not user.is_system_admin and run.actor_id != user.id:
        # 남의 판을 되돌리는 것은 남의 일을 지우는 것이다 — 그 판을 넣은 사람이나
        # 시스템 관리자가 한다.
        raise Forbidden(
            code("BUNDLES", 31), "그 판을 넣은 사람이나 시스템 관리자가 되돌립니다."
        )
    return run


def entries_of(db: Session, run: BundleRun) -> list[BundleUndoEntry]:
    """**거꾸로** — 넣은 차례의 반대로 되돌린다."""
    return list(
        db.scalars(
            select(BundleUndoEntry)
            .where(BundleUndoEntry.run_id == run.id)
            .order_by(BundleUndoEntry.seq.desc())
        )
    )


def run_undo(
    db: Session,
    user: User,
    run: BundleRun,
    *,
    apply: bool = False,
    on_progress: bulk.Progress = None,
) -> Outcome:
    """무엇이 되돌아가나 — `apply` 면 그대로 되돌린다. **커밋은 부르는 쪽이 한다.**"""
    out = Outcome(run=run, plan=bulk.Plan())
    if run.undone_at is not None:
        out.plan.errors.append(
            f"이미 되돌린 판입니다({run.undone_at:%Y-%m-%d %H:%M}). {run.undo_note}".strip()
        )
        return out
    entries = entries_of(db, run)
    if not entries:
        out.plan.errors.append(
            "되돌릴 기록이 없습니다 — 보관 기간이 지나 지웠거나, 아무것도 안 바뀐 판입니다."
        )
        return out

    types = {one.id: one for one in db.scalars(select(ObjectType))}
    # 먼저 선 · 별칭을 되돌리고(거꾸로 읽으므로 자연히 먼저 온다), 객체는 그 뒤다.
    doomed = {
        one.target_id
        for one in entries
        if one.table_name == "objects" and one.action == "create"
    }
    # **이 되돌리기가 먼저 끊을 선은 막는 것으로 세지 않는다.** 거꾸로 읽으므로 선이 먼저
    # 끊기는데, 계획은 아무것도 안 지우므로 그 선이 그대로 보인다 — 그러면 계획은 「관계가
    # 걸려 안 지웁니다」 라고 말하고, 적용은 지운다. 계획이 거짓말을 하는 자리다.
    cut = {
        one.target_id
        for one in entries
        if one.table_name in ("object_relations", "object_links") and one.action == "create"
    }
    total = len(entries)
    for done, entry in enumerate(entries, start=1):
        if on_progress is not None and (done % 200 == 0 or done == total):
            on_progress("되돌리기", done, total)
        if entry.table_name == "object_relations":
            out.plan.rows.append(_relation(db, user, entry, done, apply=apply))
        elif entry.table_name == "object_links":
            out.plan.rows.append(_link(db, entry, done, apply=apply))
        elif entry.table_name == "object_aliases":
            out.plan.rows.append(_alias(db, user, entry, done, types, apply=apply))
        elif entry.table_name == "objects":
            out.plan.rows.append(
                _object(db, user, entry, done, types, doomed=doomed, cut=cut, apply=apply)
            )
        else:  # pragma: no cover - 표가 늘면 여기 온다
            out.plan.rows.append(
                bulk.RowPlan(
                    row=done,
                    action="error",
                    label=entry.label,
                    message=f"되돌릴 줄을 읽을 수 없습니다: {entry.table_name}",
                )
            )
    if apply and out.plan.ok:
        run.undone_at = datetime.now(UTC)
        run.undone_by_id = user.id
        counts = out.plan.counts
        moved = counts.get("delete", 0) + counts.get("update", 0) + counts.get("create", 0)
        run.undo_note = f"되돌림 {moved}건 · 건너뜀 {counts.get('unchanged', 0)}건"[:500]
        audit.record(
            db,
            summary=True,
            action="bundle.undo",
            actor=user,
            target_table="bundle_runs",
            target_id=run.id,
            target_label=run.label or "묶음",
            changes={"counts": dict(counts), "ran_at": run.at.isoformat()},
            reason="묶음 한 판 되돌리기",
        )
        out.applied = True
    return out


# --- 줄마다 -----------------------------------------------------------------------


def _stale(current: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """우리가 넣은 값과 **지금 값이 다른 칸** — 있으면 그 사이 남이 고친 것이다."""
    return [key for key, value in after.items() if current.get(key) != value]


def _object(
    db: Session,
    user: User,
    entry: BundleUndoEntry,
    index: int,
    types: dict[uuid.UUID, ObjectType],
    *,
    doomed: set[uuid.UUID],
    cut: set[uuid.UUID],
    apply: bool,
) -> bulk.RowPlan:
    row = db.get(ObjectInstance, entry.target_id)
    if row is None or row.deleted_at is not None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=entry.label, message="이미 없습니다"
        )
    object_type = types.get(row.type_id)
    if object_type is None:  # pragma: no cover - 타입이 사라졌다
        return bulk.RowPlan(
            row=index, action="error", label=entry.label, message="타입이 없어졌습니다"
        )
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
    except AppError as denied:
        return bulk.RowPlan(
            row=index, action="error", label=entry.label, message=denied.message
        )
    if entry.action == "create":
        blocked = _blockers(db, row, doomed, cut)
        if blocked:
            return bulk.RowPlan(
                row=index,
                action="unchanged",
                label=entry.label,
                object_id=row.id,
                message=f"그 뒤에 {blocked} 이 이것을 가리켜 안 지웁니다",
            )
        if apply:
            lifecycle._soft_delete(db, user, row, object_type, reason="묶음 한 판 되돌리기")
            db.flush()
        return bulk.RowPlan(
            row=index,
            action="delete",
            label=entry.label,
            object_id=row.id,
            message="이 판이 만든 객체입니다 — 지웁니다",
        )
    current = audit_state(row)
    stale = _stale(current, entry.after)
    if stale:
        return bulk.RowPlan(
            row=index,
            action="unchanged",
            label=entry.label,
            object_id=row.id,
            message=f"그 뒤에 {', '.join(stale)} 이(가) 바뀌어 안 되돌립니다",
        )
    if apply:
        before = audit_state(row)
        for key, value in entry.before.items():
            _restore(row, key, value)
        db.flush()
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes=audit.diff(before, audit_state(row)),
            reason="묶음 한 판 되돌리기",
        )
    return bulk.RowPlan(
        row=index,
        action="update",
        label=entry.label,
        object_id=row.id,
        changes=sorted(entry.before),
        message="넣기 전 값으로 되돌립니다",
    )


def _restore(row: ObjectInstance, key: str, value: Any) -> None:
    """`audit_state` 의 칸 하나를 되돌린다 — 그 표의 칸 이름과 같다."""
    if key == "owner_workspace_id":
        row.owner_workspace_id = uuid.UUID(str(value)) if value else None
        return
    if key == "properties":
        row.properties = dict(value or {})
        return
    if key in ("valid_from_year", "valid_to_year"):
        setattr(row, key, int(value) if value not in (None, "") else None)
        return
    if key in ("key", "label", "description", "status"):
        setattr(row, key, value if value is not None else "")


def _blockers(
    db: Session, row: ObjectInstance, doomed: set[uuid.UUID], cut: set[uuid.UUID]
) -> str:
    """이 객체를 **이 판 밖에서** 가리키는 것 — 있으면 사람이 읽는 한 줄.

    ⚠️ 줄마다 참조를 다 훑으면(`lifecycle.references_of`) 수만 줄에서 질의가 수만 번이다.
       여기서는 그 객체에 **아직 남아 있는 선**과 그 객체를 담은 칸만 본다 — 이 판이 만든
       선은 앞에서 이미 끊었으므로(거꾸로 읽는다), 남아 있으면 밖에서 온 것이다.
    """
    edges = [
        one
        for one in db.scalars(
            select(ObjectRelation.id).where(
                or_(
                    ObjectRelation.src_object_id == row.id,
                    ObjectRelation.dst_object_id == row.id,
                )
            )
        )
        if one not in cut
    ]
    if edges:
        return f"관계 {len(edges)}건"
    links = [
        one
        for one in db.scalars(select(ObjectLink.id).where(ObjectLink.src_id == row.id))
        if one not in cut
    ]
    if links:
        return f"관계 {len(links)}건"
    pointing = _pointing(db, row, doomed)
    if pointing:
        return f"다른 객체의 칸 {pointing}건"
    return ""


def _pointing(db: Session, row: ObjectInstance, doomed: set[uuid.UUID]) -> int:
    """이 객체를 **속성으로** 가리키는 객체 수 — 함께 지울 것은 뺀다."""
    object_type = db.get(ObjectType, row.type_id)
    if object_type is None:  # pragma: no cover
        return 0
    defs = list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.data_type == "object_ref",
                # 이 타입이 구현한 인터페이스를 대상으로 둔 칸도 이것을 가리킬 수 있다.
                PropertyDef.ref_type_slug.in_(
                    interfaces.load_ends(db).reach_of(object_type.slug)
                ),
            )
        )
    )
    wanted = str(row.id)
    total = 0
    for definition in defs:
        column = ObjectInstance.properties[definition.key]
        found = db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.type_id == definition.owner_id,
                ObjectInstance.deleted_at.is_(None),
                or_(
                    column.astext == wanted,
                    and_(
                        func.jsonb_typeof(column) == "array",
                        column.has_any(array([wanted], type_=Text)),
                    ),
                ),
            )
        )
        total += len([one for one in found if one not in doomed])
    return total


def _relation(
    db: Session, user: User, entry: BundleUndoEntry, index: int, *, apply: bool
) -> bulk.RowPlan:
    edge = db.get(ObjectRelation, entry.target_id)
    if entry.action == "create":
        if edge is None:
            return bulk.RowPlan(
                row=index, action="unchanged", label=entry.label, message="이미 끊겼습니다"
            )
        if apply:
            audit.record(
                db,
                action="object.relation.remove",
                actor=user,
                target_table="object_relations",
                target_id=edge.id,
                target_label=entry.label,
                changes={"reason": "묶음 한 판 되돌리기"},
                reason="묶음 한 판 되돌리기",
            )
            db.delete(edge)
            db.flush()
        return bulk.RowPlan(
            row=index,
            action="delete",
            label=entry.label,
            message="이 판이 이은 선입니다 — 끊습니다",
        )
    if entry.action == "delete":
        if edge is not None:
            return bulk.RowPlan(
                row=index,
                action="unchanged",
                label=entry.label,
                message="이미 이어져 있습니다",
            )
        gone = _missing_end(db, entry.before)
        if gone:
            return bulk.RowPlan(
                row=index,
                action="unchanged",
                label=entry.label,
                message=f"{gone} 이 지금 없어 다시 이을 수 없습니다",
            )
        if apply:
            made = ObjectRelation(
                id=uuid.uuid4(),
                src_object_id=uuid.UUID(str(entry.before["src"])),
                dst_object_id=uuid.UUID(str(entry.before["dst"])),
                relation=str(entry.before["relation"]),
                properties=dict(entry.before.get("properties") or {}),
                evidence_note=str(entry.before.get("evidence_note") or ""),
                created_by_id=user.id,
            )
            db.add(made)
            db.flush()
            audit.record(
                db,
                action="object.relation.add",
                actor=user,
                target_table="object_relations",
                target_id=made.id,
                target_label=entry.label,
                changes={"reason": "묶음 한 판 되돌리기"},
                reason="묶음 한 판 되돌리기",
            )
        return bulk.RowPlan(
            row=index,
            action="create",
            label=entry.label,
            message="이 판이 끊은 선입니다 — 다시 잇습니다",
        )
    if edge is None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=entry.label, message="선이 이미 없습니다"
        )
    current = {
        "properties": dict(edge.properties or {}),
        "evidence_note": edge.evidence_note or "",
    }
    stale = _stale(current, entry.after)
    if stale:
        return bulk.RowPlan(
            row=index,
            action="unchanged",
            label=entry.label,
            message=f"그 뒤에 {', '.join(stale)} 이(가) 바뀌어 안 되돌립니다",
        )
    if apply:
        edge.properties = dict(entry.before.get("properties") or {})
        edge.evidence_note = str(entry.before.get("evidence_note") or "")
        db.flush()
        audit.record(
            db,
            action="object.relation.update",
            actor=user,
            target_table="object_relations",
            target_id=edge.id,
            target_label=entry.label,
            changes={
                "properties": {"before": current["properties"], "after": edge.properties}
            },
            reason="묶음 한 판 되돌리기",
        )
    return bulk.RowPlan(
        row=index, action="update", label=entry.label, message="넣기 전 값으로 되돌립니다"
    )


def _missing_end(db: Session, before: dict[str, Any]) -> str:
    """양끝이 지금도 있나 — 없으면 사람이 읽는 이름."""
    for name in ("src", "dst"):
        try:
            wanted = uuid.UUID(str(before.get(name)))
        except (TypeError, ValueError):
            return "끝점"
        found = db.get(ObjectInstance, wanted)
        if found is None or found.deleted_at is not None:
            return "출발점" if name == "src" else "도착점"
    return ""


def _link(db: Session, entry: BundleUndoEntry, index: int, *, apply: bool) -> bulk.RowPlan:
    link = db.get(ObjectLink, entry.target_id)
    if link is None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=entry.label, message="이미 끊겼습니다"
        )
    if apply:
        db.delete(link)
        db.flush()
    return bulk.RowPlan(
        row=index,
        action="delete",
        label=entry.label,
        message="이 판이 이은 선입니다 — 끊습니다",
    )


def _alias(
    db: Session,
    user: User,
    entry: BundleUndoEntry,
    index: int,
    types: dict[uuid.UUID, ObjectType],
    *,
    apply: bool,
) -> bulk.RowPlan:
    row = db.get(ObjectInstance, entry.target_id)
    if row is None or row.deleted_at is not None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=entry.label, message="객체가 이미 없습니다"
        )
    object_type = types.get(row.type_id)
    if object_type is None:  # pragma: no cover
        return bulk.RowPlan(
            row=index, action="error", label=entry.label, message="타입이 없어졌습니다"
        )
    now = aliases.human_of(db, [row.id]).get(row.id, [])
    # **묶음으로 견준다** — 표의 차례(붙인 시각)와 파일의 차례가 다를 수 있다. 순서로 보면
    # 바뀐 것이 없는데도 「그 뒤에 바뀌었다」 가 되어 되돌리기가 늘 건너뛴다.
    if _names(now) != _names(entry.after.get("aliases") or []):
        return bulk.RowPlan(
            row=index,
            action="unchanged",
            label=entry.label,
            message="그 뒤에 별칭이 바뀌어 안 되돌립니다",
        )
    if apply:
        aliases.set_human(
            db,
            row,
            object_type,
            aliases.load(list(entry.before.get("aliases") or [])),
            mode="replace",
        )
        db.flush()
    return bulk.RowPlan(
        row=index, action="update", label=entry.label, message="넣기 전 별칭으로 되돌립니다"
    )


def _names(values: list[Any]) -> set[str]:
    """별칭 값의 비교키 묶음 — 글자만 온 것도, `dump` 모양도 받는다."""
    out: set[str] = set()
    for one in values:
        text = one if isinstance(one, str) else str((one or {}).get("value") or "")
        if text.strip():
            out.add(compare_key(text))
    return out
