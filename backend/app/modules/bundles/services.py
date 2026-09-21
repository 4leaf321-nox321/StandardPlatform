"""묶음 가져오기 — **정의 · 객체 · 관계를 한 트랜잭션으로.**

로컬 정제 도구(`pipeline/`)가 AI 로 만든 결과물을 사람이 검토하고 넣는 자리다.

## 왜 한 번에 미리 보나

정의 가져오기와 객체 가져오기가 따로면, 새 타입에 넣을 객체의 계획은 **정의를 적용한 뒤에야**
볼 수 있다. 그러면 검토가 둘로 쪼개지고, 두 번째 검토에서 틀린 것을 찾았을 때 정의는 이미
들어가 있다.

## 어떻게 — 바깥 트랜잭션 하나

기존 서비스(정의 가져오기 · 객체 · 관계)를 **그대로** 부른다. 규칙을 새로 적지 않는다 — 두 벌이
되면 「묶음으로는 되는데 파일로는 안 되는」 상태가 생긴다. 그 서비스들은 저마다 커밋하므로,
바깥 트랜잭션 안에서 세션을 `create_savepoint` 로 열어 **그 커밋을 세이브포인트로 만든다.**
미리 보기는 끝에 전부 롤백하고, 적용은 한 곳이라도 오류면 롤백한다.

**바깥에 알리는 것(웹훅 · 지켜보기 알림)은 모았다가 진짜 커밋 뒤에만** 내보낸다
(`events.hold`). 안 그러면 미리 보기가 일어나지 않은 변경을 알린다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.modules.accounts.models import User
from app.modules.bundles.schemas import BatchOut, BundleIn, BundleOut
from app.modules.objects import bulk
from app.modules.objects.schemas import ImportPlanOut as RowsPlanOut
from app.modules.objects.schemas import ImportRowOut
from app.modules.ontology import importer
from app.modules.ontology.models import ObjectType
from app.modules.ontology.schemas import ChangeOut
from app.modules.ontology.schemas import ImportPlanOut as SchemaPlanOut
from app.shared import audit, events
from app.shared.errors import AppError, code
from app.shared.permissions import resolve_owner_workspace


@dataclass
class Batch:
    type_slug: str
    plan: bulk.Plan | None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.plan is not None and self.plan.ok


@dataclass
class Outcome:
    ontology: importer.Plan | None = None
    objects: list[Batch] = field(default_factory=list)
    relations: list[Batch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    snapshot_id: uuid.UUID | None = None
    applied: bool = False

    @property
    def ok(self) -> bool:
        if self.errors or (self.ontology is not None and self.ontology.errors):
            return False
        return all(one.ok for one in [*self.objects, *self.relations])

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {
            "ontology_changes": len(
                [
                    c
                    for c in (self.ontology.changes if self.ontology else [])
                    if c.action != "unchanged"
                ]
            )
        }
        for name, batches in (("objects", self.objects), ("relations", self.relations)):
            for one in batches:
                for action, count in (one.plan.counts if one.plan else {}).items():
                    out[f"{name}_{action}"] = out.get(f"{name}_{action}", 0) + count
                if one.error:
                    out[f"{name}_error"] = out.get(f"{name}_error", 0) + 1
        return out


def run(
    user: User,
    bundle: BundleIn,
    *,
    max_rows: int = bulk.MAX_ROWS,
    on_progress: bulk.Progress = None,
    before_commit: Callable[[Outcome], None] | None = None,
) -> Outcome:
    """미리 보기(`apply` 거짓)는 늘 롤백, 적용은 **전부 괜찮을 때만** 커밋.

    `before_commit` 은 적용 직전에 결과를 보는 자리다 — 워커가 미리 본 지문과 견준다.
    거기서 예외가 나면 롤백된다."""
    connection = engine.connect()
    outer = connection.begin()
    db = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    events.hold(db)
    outcome = Outcome()
    try:
        _stages(db, user, bundle, outcome, max_rows=max_rows, on_progress=on_progress)
        if bundle.apply and outcome.ok and before_commit is not None:
            before_commit(outcome)
        if bundle.apply and outcome.ok:
            audit.record(
                db,
                action="bundle.import",
                actor=user,
                target_table="bundles",
                target_id=None,
                target_label=", ".join(sorted({b.type_slug for b in outcome.objects}))
                or "정의",
                changes={
                    **outcome.counts,
                    **({"source": bundle.source} if bundle.source else {}),
                },
            )
            db.commit()
            outer.commit()
            outcome.applied = True
            events.release(db)
        else:
            events.drop_held(db)
            outer.rollback()
    except Exception:
        events.drop_held(db)
        outer.rollback()
        raise
    finally:
        db.close()
        connection.close()
    if not outcome.applied:
        # 롤백된 스냅샷을 가리키면 없는 되돌릴 자리를 약속하는 것이다.
        outcome.snapshot_id = None
    return outcome


def _stages(
    db: Session,
    user: User,
    bundle: BundleIn,
    out: Outcome,
    *,
    max_rows: int = bulk.MAX_ROWS,
    on_progress: bulk.Progress = None,
) -> None:
    if bundle.source and not user.is_system_admin:
        out.errors.append(
            f"받은 묶음(source={bundle.source})은 시스템 관리자만 넣습니다 — "
            "허브의 것을 고칠 수 있는 길이라서다."
        )
        return
    if bundle.ontology is not None:
        if not user.is_system_admin:
            out.errors.append(
                "정의(ontology)가 든 묶음은 시스템 관리자만 넣습니다 — 정의를 빼고 보내거나 "
                "시스템 관리자에게 맡기세요."
            )
            return
        if bundle.apply:
            out.snapshot_id = importer.take_snapshot(db, user, reason="묶음 가져오기").id
        try:
            planned = importer.apply(db, bundle.ontology, source=bundle.source)
        except ValueError as caught:
            out.errors.append(f"정의: {caught}")
            return
        out.ontology = planned
        if planned.errors:
            # **정의가 안 서면 객체를 맞춰 볼 수 없다** — 거기서 멈추고 정의의 오류를 보인다.
            return
        if bundle.apply and out.snapshot_id is not None:
            audit.record(
                db,
                action="ontology.import",
                actor=user,
                target_table="ontology_snapshots",
                target_id=out.snapshot_id,
                target_label="묶음 가져오기",
            )
        # 뒤 단계가 새 정의를 보게 한다 — 이 세션은 스스로 flush 하지 않는다.
        db.flush()

    types = {row.slug: row for row in db.scalars(select(ObjectType))}
    for batch in bundle.objects:
        object_type = types.get(batch.type_slug)
        if object_type is None:
            out.objects.append(
                Batch(batch.type_slug, None, f"타입을 찾을 수 없습니다: {batch.type_slug}")
            )
            continue
        try:
            owner = resolve_owner_workspace(
                db, user, batch.workspace_slug, what="객체", code_value=code("BUNDLES", 10)
            )
            # 미리 보기에서도 **적용한다** — 그래야 뒤 묶음(참조 · 관계)이 이 객체를 찾는다.
            # 바깥이 롤백되므로 남지 않는다.
            planned_rows = bulk.apply_objects(
                db,
                user,
                object_type,
                batch.rows,
                owner_workspace_id=owner,
                source=bundle.source,
                max_rows=max_rows,
                on_progress=_staged(on_progress, f"객체 {batch.type_slug}"),
            )
        except AppError as caught:
            out.objects.append(Batch(batch.type_slug, None, caught.message))
            continue
        out.objects.append(Batch(batch.type_slug, planned_rows))

    for links in bundle.relations:
        source_type = types.get(links.type_slug)
        if source_type is None:
            out.relations.append(
                Batch(links.type_slug, None, f"타입을 찾을 수 없습니다: {links.type_slug}")
            )
            continue
        try:
            planned_links = bulk.apply_relations(
                db,
                user,
                source_type,
                links.rows,
                source=bundle.source,
                max_rows=max_rows,
                on_progress=_staged(on_progress, f"관계 {links.type_slug}"),
            )
        except AppError as caught:
            out.relations.append(Batch(links.type_slug, None, caught.message))
            continue
        out.relations.append(Batch(links.type_slug, planned_links))


def _staged(on_progress: bulk.Progress, prefix: str) -> bulk.Progress:
    """묶음은 타입마다 한 단계다 — 「객체 part · 계획 1,200 / 5,000」 처럼 어느 타입인지
    붙인다."""
    if on_progress is None:
        return None

    def tick(stage: str, done: int, total: int) -> None:
        assert on_progress is not None
        on_progress(f"{prefix} · {stage}", done, total)

    return tick


# --- 응답 모양 -------------------------------------------------------------------


def outcome_out(outcome: Outcome) -> BundleOut:
    """결과를 API 모양으로 — 라우터와 워커가 **같은 것**을 돌려준다."""
    return BundleOut(
        applied=outcome.applied,
        ok=outcome.ok,
        ontology=_schema_out(outcome) if outcome.ontology is not None else None,
        objects=[_batch_out(one, outcome.applied) for one in outcome.objects],
        relations=[_batch_out(one, outcome.applied) for one in outcome.relations],
        errors=outcome.errors,
        snapshot_id=outcome.snapshot_id,
        counts=outcome.counts,
    )


def fingerprint(outcome: Outcome) -> str:
    """미리 본 결과와 적용 직전 결과가 **같은 계획**인지. id · 스냅샷은 뺀다."""
    body = {
        "ontology": [
            (c.kind, c.slug, c.action, sorted(c.fields))
            for c in (outcome.ontology.changes if outcome.ontology else [])
        ],
        "objects": [
            (
                one.type_slug,
                one.error,
                [
                    (r.row, r.action, r.label, r.key, sorted(r.changes), r.message)
                    for r in (one.plan.rows if one.plan else [])
                ],
            )
            for one in outcome.objects
        ],
        "relations": [
            (
                one.type_slug,
                one.error,
                [
                    (r.row, r.action, r.label, r.key, sorted(r.changes), r.message)
                    for r in (one.plan.rows if one.plan else [])
                ],
            )
            for one in outcome.relations
        ],
        "errors": outcome.errors,
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _schema_out(outcome: Outcome) -> SchemaPlanOut:
    planned = outcome.ontology
    assert planned is not None
    return SchemaPlanOut(
        applied=outcome.applied,
        changes=[
            ChangeOut(kind=c.kind, slug=c.slug, action=c.action, fields=c.fields)
            for c in planned.changes
        ],
        warnings=planned.warnings,
        errors=planned.errors,
        snapshot_id=outcome.snapshot_id,
    )


def _batch_out(batch: Batch, applied: bool) -> BatchOut:
    return BatchOut(
        type_slug=batch.type_slug,
        plan=_rows_out(batch.plan, applied) if batch.plan is not None else None,
        error=batch.error,
    )


def _rows_out(plan: bulk.Plan, applied: bool) -> RowsPlanOut:
    return RowsPlanOut(
        applied=applied,
        rows=[
            ImportRowOut(
                row=one.row,
                action=one.action,
                label=one.label,
                key=one.key,
                object_id=one.object_id,
                changes=one.changes,
                message=one.message,
            )
            for one in plan.rows
        ],
        errors=plan.errors,
        counts=plan.counts,
    )
