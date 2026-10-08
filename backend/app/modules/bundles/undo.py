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
- **적용은 전부 아니면 무다.** 오류 줄이 하나라도 있으면 앞 줄에서 바꾼 것까지 세이브포인트로
  되돌린다 — 예전에는 앞 줄의 지우기 · 되돌리기가 커밋되고 결과는 「적용 안 됨」 이라 말했다
  (`apply=true` 로 곧장 부르면 계획을 안 거친다, 2026-10-08).
- **끊었던 선을 다시 이을 때도 잇는 규칙을 지킨다**(관계 종류 · 허용 타입 · 개수 제약 ·
  순환). 그 사이 같은 선이 새로 이어졌으면 「그대로」 다 — 안 그러면 유일 제약에 걸려 그 판은
  영영 못 되돌린다.
- **합치기는 되돌리지 않는다.** 합치기는 참조를 옮긴 것이라 자동으로 풀면 어느 참조가
  원래 어느 쪽 것이었는지 알 수 없다 — 그 줄은 이유를 적고 사람에게 넘긴다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles.models import BundleRun, BundleUndoEntry
from app.modules.objects import aliases, bulk, lifecycle
from app.modules.objects import relations as rel
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRef, ObjectRelation
from app.modules.objects.services import audit_state, properties_of
from app.modules.ontology import interfaces, managed
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.services import InvalidValue, check_value
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


@dataclass
class _Context:
    """한 판을 되돌리는 동안 줄마다 다시 읽지 않을 것."""

    run: BundleRun
    now: datetime
    """이 트랜잭션의 `now()` — 이 되돌리기가 고친 행의 `updated_at` 이 이것이다."""
    types: dict[uuid.UUID, ObjectType]
    kinds: dict[str, RelationType]
    """관계 종류 — slug 로. 선을 다시 이을 때 규칙(허용 타입 · 개수 제약 · 순환)을 본다."""
    ends: interfaces.Ends | None = None
    """허용 타입 판정에 쓰는 정의 — 처음 쓸 때 한 번 읽는다(줄마다 읽으면 수만 번이다)."""

    def ends_of(self, db: Session) -> interfaces.Ends:
        if self.ends is None:
            self.ends = interfaces.load_ends(db)
        return self.ends


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
    # **적용은 세이브포인트 안에서** — 줄마다 flush 하므로, 뒤 줄이 오류면 앞 줄이 이미 DB 에
    # 있다. 워커는 결과가 무엇이든 커밋하므로 여기서 거둬야 전부 아니면 무가 된다.
    savepoint = db.begin_nested() if apply else None
    ctx = _Context(
        run=run,
        now=db.execute(select(func.now())).scalar_one(),
        types=types,
        kinds={one.slug: one for one in db.scalars(select(RelationType))},
    )
    for done, entry in enumerate(entries, start=1):
        if on_progress is not None and (done % 200 == 0 or done == total):
            on_progress("되돌리기", done, total)
        if entry.table_name == "object_relations":
            out.plan.rows.append(_relation(db, user, entry, done, ctx, cut=cut, apply=apply))
        elif entry.table_name == "object_links":
            out.plan.rows.append(_link(db, user, entry, done, ctx, apply=apply))
        elif entry.table_name == "object_aliases":
            out.plan.rows.append(_alias(db, user, entry, done, types, ctx, apply=apply))
        elif entry.table_name == "objects":
            out.plan.rows.append(
                _object(db, user, entry, done, types, ctx, doomed=doomed, cut=cut, apply=apply)
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
    if savepoint is not None:
        if not out.plan.ok:
            # 오류 줄이 있으면 **아무것도 안 바꾼다** — 앞 줄에서 지운 객체 · 끊은 선까지
            # 되돌린다. 결과의 줄은 그대로 두어 무엇이 막았는지 읽게 한다.
            savepoint.rollback()
            return out
        savepoint.commit()
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
    ctx: _Context,
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
    refused = _refusal(db, user, row, object_type, ctx)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=entry.label, message=refused)
    if entry.action == "create":
        if (
            row.updated_at is not None
            and row.updated_at > ctx.run.at
            and row.updated_at != ctx.now
        ):
            # **만든 객체도 남이 고쳤으면 안 지운다** — 지우면 그 사람의 변경이 함께 사라진다.
            # 만든 줄에는 「넣은 값」 이 안 적혀 있어 칸마다 견줄 수 없고, 그 판보다 뒤에
            # 고쳐졌는지(`updated_at`)로 본다(2026-10-08). 이 되돌리기가 방금 앞 줄에서 고친
            # 것(`now()` 와 같다)은 남의 변경이 아니다 — 안 빼면 계획과 적용이 갈린다.
            return bulk.RowPlan(
                row=index,
                action="unchanged",
                label=entry.label,
                object_id=row.id,
                message="그 뒤에 고쳐져 안 지웁니다",
            )
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
    wrong = _wrong_kind(db, row, entry.before.get("properties"))
    if wrong:
        return bulk.RowPlan(
            row=index,
            action="error",
            label=entry.label,
            object_id=row.id,
            message=(
                "그 뒤 속성 정의가 바뀌어(속성 종류 변경 등) 그때 값이 지금 정의에 맞지 "
                f"않습니다 — {wrong}"
            ),
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


def _refusal(
    db: Session, user: User, row: ObjectInstance, object_type: ObjectType, ctx: _Context
) -> str:
    """이 객체를 **지금** 고칠 수 있나 — 못 하면 그 까닭, 되면 빈 글자.

    판을 넣은 날의 권한이 아니라 지금의 권한이다(그 사이 부서를 옮겼을 수 있다). 허브가
    관리하게 된 타입은 그 허브의 판(`source`)만 되돌린다 — 아니면 다음 받기가 덮어쓴다.
    """
    refused = managed.objects_refusal(object_type, source=ctx.run.source, what="되돌리지")
    if refused:
        return refused
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
    except AppError as denied:
        return denied.message
    return ""


def _wrong_kind(db: Session, row: ObjectInstance, properties: Any) -> str:
    """되돌릴 값이 **지금 속성 종류에 맞나** — 안 맞으면 그 까닭.

    그 뒤 속성 종류가 변경됐으면(ADR 0007) 옛 종류의 값이다. 되돌리기는 정의를 안 되돌리므로,
    검사 없이 넣으면 숫자 칸에 글이 들어간다. 값의 모양만 본다 — 그 사이 지워진 속성 · 필수가
    된 속성은 예전처럼 되돌리기를 막지 않는다.
    """
    if not isinstance(properties, dict):
        return ""
    for definition in properties_of(db, row.type_id):
        if definition.key not in properties:
            continue
        raw = properties[definition.key]
        items = raw if definition.multi and isinstance(raw, list) else [raw]
        try:
            for item in items:
                if item is not None and item != "":
                    check_value(definition, item)
        except InvalidValue as caught:
            return caught.message
    return ""


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
    # 원 표와 잇는 선은 **양쪽을 다 본다** — 계정 → 과제처럼 원 표가 출발점이면 이 객체는
    # 도착점에 있다. 출발점만 보면 그 선이 이름 없이 남은 채로 지웠다(2026-10-08).
    links = [
        one
        for one in db.scalars(
            select(ObjectLink.id).where(
                or_(ObjectLink.src_id == row.id, ObjectLink.dst_id == row.id)
            )
        )
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
    if not defs:
        return 0
    # 참조 색인으로(ADR 0010) — 예전에는 칸마다 가리키는 타입을 통째로 읽었다.
    found = db.scalars(
        select(ObjectRef.src_id).where(
            ObjectRef.dst_id == row.id,
            or_(
                *(
                    and_(ObjectRef.key == d.key, ObjectRef.src_type_id == d.owner_id)
                    for d in defs
                )
            ),
        )
    )
    return len({one for one in found if one not in doomed})


def _relation(
    db: Session,
    user: User,
    entry: BundleUndoEntry,
    index: int,
    ctx: _Context,
    *,
    cut: set[uuid.UUID],
    apply: bool,
) -> bulk.RowPlan:
    edge = db.get(ObjectRelation, entry.target_id)
    if entry.action == "create":
        if edge is None:
            return bulk.RowPlan(
                row=index, action="unchanged", label=entry.label, message="이미 끊겼습니다"
            )
        refused = _edge_refusal(db, user, edge.src_object_id, edge.relation, ctx)
        if refused:
            return bulk.RowPlan(row=index, action="error", label=entry.label, message=refused)
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
        src_id = uuid.UUID(str(entry.before["src"]))
        dst_id = uuid.UUID(str(entry.before["dst"]))
        slug = str(entry.before.get("relation") or "")
        twin = db.scalar(
            select(ObjectRelation.id).where(
                ObjectRelation.src_object_id == src_id,
                ObjectRelation.dst_object_id == dst_id,
                ObjectRelation.relation == slug,
            )
        )
        if twin is not None:
            # 그 사이 **같은 선이 새 id 로** 이어졌다 — 다시 넣으면 유일 제약에 걸려 그 판
            # 전체가 영영 안 되돌려진다(2026-10-08). 바라는 상태가 이미 그렇다.
            return bulk.RowPlan(
                row=index,
                action="unchanged",
                label=entry.label,
                message="같은 선이 이미 이어져 있습니다",
            )
        refused = _edge_refusal(db, user, src_id, slug, ctx) or _relink_refusal(
            db, ctx, slug, src_id, dst_id, cut
        )
        if refused:
            return bulk.RowPlan(row=index, action="error", label=entry.label, message=refused)
        if apply:
            made = ObjectRelation(
                id=uuid.uuid4(),
                src_object_id=src_id,
                dst_object_id=dst_id,
                relation=slug,
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
    refused = _edge_refusal(db, user, edge.src_object_id, edge.relation, ctx)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=entry.label, message=refused)
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


def _edge_refusal(db: Session, user: User, src_id: uuid.UUID, slug: str, ctx: _Context) -> str:
    """이 선을 **지금** 잇거나 끊을 수 있나 — 출발점을 고칠 수 있어야 하고(일괄 입력과 같은
    문턱), 허브가 관리하는 관계 종류는 그 허브의 판만 되돌린다."""
    kind = ctx.kinds.get(slug)
    if kind is not None:
        try:
            managed.require_relation_editable(kind, source=ctx.run.source)
        except AppError as refused:
            return refused.message
    src = db.get(ObjectInstance, src_id)
    if src is None:
        return ""
    try:
        require_owner_edit(
            db, user, src.owner_workspace_id, what="객체", code_value=code("OBJECTS", 27)
        )
    except AppError as denied:
        return denied.message
    return ""


def _relink_refusal(
    db: Session,
    ctx: _Context,
    slug: str,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    cut: set[uuid.UUID],
) -> str:
    """끊었던 선을 **다시 이으면 잇는 규칙을 깨나** — 깨면 사람이 읽는 까닭.

    화면 · 일괄 입력이 지키는 셋(`objects/relations.py`)을 그대로 본다: 관계 종류가 있고 쓰는
    중인가, 양끝이 허용 타입인가, 개수 제약 · 순환을 안 깨나. 안 보면 「한 부품의 상위는
    하나」 인 관계에 둘째 상위가 조용히 들어간다(2026-10-08).

    **이 되돌리기가 끊을 선(`cut`)은 없는 것으로 친다** — 「파일대로 맞춤」 으로 상위를 옮긴
    판은 새 선을 이은 뒤 옛 선을 끊었고, 거꾸로 읽으면 옛 선을 먼저 되살린다. 그때 새 선은
    아직 있지만 곧 끊긴다.
    """
    kind = ctx.kinds.get(slug)
    if kind is None:
        return f"관계 종류 {slug} 이(가) 지금 없어 다시 이을 수 없습니다"
    if not kind.is_active:
        return f"{kind.label}은 지금 쓰지 않는 관계 종류라 다시 잇지 않습니다"
    src = db.get(ObjectInstance, src_id)
    dst = db.get(ObjectInstance, dst_id)
    if src is None or dst is None:  # pragma: no cover - `_missing_end` 가 먼저 본다
        return "끝점이 없어 다시 이을 수 없습니다"
    slugs = {one.id: one.slug for one in ctx.types.values()}
    try:
        rel.require_end_types_allowed(
            db, kind, slugs.get(src.type_id, ""), slugs.get(dst.type_id, ""), ctx.ends_of(db)
        )
    except AppError as refused:
        return refused.message
    if kind.cardinality in ("one_to_one", "many_to_one") and _edges_from(
        db, kind.slug, src_id, cut, side="src"
    ):
        return f"{kind.label}은 하나만 맺을 수 있는데 그 사이 다른 선이 이어졌습니다"
    if kind.cardinality in ("one_to_one", "one_to_many") and _edges_from(
        db, kind.slug, dst_id, cut, side="dst"
    ):
        return f"{kind.label}의 도착 쪽은 하나만 받을 수 있는데 그 사이 다른 선이 이어졌습니다"
    if kind.acyclic and _closes_cycle(db, kind.slug, src_id, dst_id, cut):
        return f"다시 이으면 {kind.label}에 순환이 생깁니다 — 그 사이 돌아오는 길이 생겼습니다"
    return ""


def _edges_from(
    db: Session, slug: str, end: uuid.UUID, cut: set[uuid.UUID], *, side: str
) -> bool:
    """그 끝에 이 관계의 선이 **이 되돌리기가 끊을 것 말고** 있나 — 두 표(`object_relations`
    · 원 표와 잇는 `object_links`)를 다 본다(`relations.require_cardinality` 와 같다)."""
    edge_end = ObjectRelation.src_object_id if side == "src" else ObjectRelation.dst_object_id
    link_end = ObjectLink.src_id if side == "src" else ObjectLink.dst_id
    found = [
        *db.scalars(
            select(ObjectRelation.id).where(ObjectRelation.relation == slug, edge_end == end)
        ),
        *db.scalars(select(ObjectLink.id).where(ObjectLink.relation == slug, link_end == end)),
    ]
    return any(one not in cut for one in found)


def _closes_cycle(
    db: Session, slug: str, src_id: uuid.UUID, dst_id: uuid.UUID, cut: set[uuid.UUID]
) -> bool:
    """`src → dst` 를 이으면 순환인가 — `relations.require_no_cycle` 과 같은 훑기에서 이
    되돌리기가 끊을 선만 뺀다."""
    if src_id == dst_id:
        return True
    reachable = db.execute(
        text("""
            WITH RECURSIVE walk(id) AS (
                SELECT dst_object_id FROM object_relations
                 WHERE src_object_id = :dst AND relation = :rel
                   AND NOT (id = ANY(CAST(:cut AS uuid[])))
                UNION
                SELECT r.dst_object_id FROM object_relations r
                  JOIN walk w ON r.src_object_id = w.id
                 WHERE r.relation = :rel AND NOT (r.id = ANY(CAST(:cut AS uuid[])))
            )
            SELECT 1 FROM walk WHERE id = :src LIMIT 1
        """),
        {
            "dst": str(dst_id),
            "src": str(src_id),
            "rel": slug,
            "cut": [str(one) for one in cut],
        },
    ).scalar()
    return bool(reachable)


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


def _link(
    db: Session, user: User, entry: BundleUndoEntry, index: int, ctx: _Context, *, apply: bool
) -> bulk.RowPlan:
    link = db.get(ObjectLink, entry.target_id)
    if link is None:
        return bulk.RowPlan(
            row=index, action="unchanged", label=entry.label, message="이미 끊겼습니다"
        )
    # 원 표 쪽 끝은 행이 없다 — `objects` 에 있는 끝의 부서로 본다(일괄 입력은 출발점이다).
    end = link.src_id if db.get(ObjectInstance, link.src_id) is not None else link.dst_id
    refused = _edge_refusal(db, user, end, link.relation, ctx)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=entry.label, message=refused)
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
    ctx: _Context,
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
    refused = _refusal(db, user, row, object_type, ctx)
    if refused:
        return bulk.RowPlan(row=index, action="error", label=entry.label, message=refused)
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
