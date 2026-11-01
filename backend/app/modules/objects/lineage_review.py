"""검토함의 **낡은 파생물**(ADR 0041 · ADR 0027) — 원천이 고정 뒤에 바뀐 파생물마다 한 항목.

    stale_lineage   바로 낡은 파생물     object_relations 의 고정    lineage.repin · restore

원래 자리에서 읽고(`lineage.direct_links` — 판정은 `lineage.py` 한 곳), 처리는 원래 기능(계보
화면의 「다시 고정」 — `lineage.repin`)을 부른다. 같은 감사 기록(`lineage.repin`)이 남고,
되돌리기는 고정을 앞 값으로 돌린다(`lineage.restore`).

- **바로 낡은 것만** 항목이 된다 — 위에서 낡은 파생물은 원천을 다시 고정하면 함께 풀리므로 따로
  세우면 같은 일을 두 번 묻는다. 항목 하나 = 파생물 하나, 근거 = 바뀐 원천마다 한 줄(「도면
  브래킷 의 개정 B → C」).
- 후보 둘 — 「영향 없음 확인」(checked) · 「다시 만들었음」(rebuilt). 둘 다 그 항목의 낡은 선을
  다시 고정하고 감사의 까닭만 다르다. **추천은 「영향 없음 확인」 이되 확신도는 없다** — 원천의
  변경이 이 파생물에 영향이 있는지는 사람이 본다. 그래서 「추천대로 모두」(문턱)에는 안 들고,
  고른 항목에 「추천대로」 를 거는 것이 일괄 확인이다.
- 「무시」 는 결정 기록에만 기억한다 — 지문은 낡은 선과 그 원천의 지금 값이라, 원천이 또 바뀌면
  다시 뜬다(`reviewkit.ignored`).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import lineage, reviewkit
from app.modules.objects.models import ObjectInstance
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

KIND = "stale_lineage"
#: 한 번에 읽는 낡은 선 — 파생물로 묶기 전.
LINKS_CAP = 5000

CHOICES = (
    Choice(
        value="checked",
        label="영향 없음 확인",
        detail="원천이 바뀌었지만 이 파생물에는 영향이 없음 — 지금 원천으로 다시 고정",
    ),
    Choice(
        value="rebuilt",
        label="다시 만들었음",
        detail="새 원천으로 이 파생물을 다시 만듦 — 지금 원천으로 다시 고정",
    ),
)


def _manager(db: Session, user: User) -> bool:
    return is_any_manager(db, user)


def _signature(links: list[lineage.Link]) -> str:
    return reviewkit.digest(
        *sorted(
            f"{one.id}:{one.revision}:{one.source_updated_at.isoformat()}" for one in links
        )
    )


def collect(db: Session, user: User, flt: Filters) -> Collected:
    """바로 낡은 파생물 — 볼 수 있는 것만, 타입 · 부서 거르기를 싣고."""
    wanted = (
        [uuid.UUID(one) for one in flt.item_ids if _is_uuid(one)]
        if flt.item_ids is not None
        else None
    )
    found, total_links = lineage.direct_links(db, derived_ids=wanted, limit=LINKS_CAP)
    if not found:
        return Collected(items=[], total=0)
    by_derived: dict[uuid.UUID, list[lineage.Link]] = defaultdict(list)
    for one in found:
        by_derived[one.derived_id].append(one)
    stmt = (
        select(ObjectInstance, ObjectType)
        .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
        .where(
            ObjectInstance.id.in_(list(by_derived)),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if flt.type_slug is not None:
        stmt = stmt.where(ObjectType.slug == flt.type_slug)
    if flt.workspace_global:
        stmt = stmt.where(ObjectInstance.owner_workspace_id.is_(None))
    elif flt.workspace_id is not None:
        stmt = stmt.where(ObjectInstance.owner_workspace_id == flt.workspace_id)
    rows = list(db.execute(stmt))
    labels = lineage.nodes(db, user, [one.source_id for one in found])
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    ignored = reviewkit.ignored(db, KIND)
    editable = reviewkit.Editable(db, user)
    items: list[Item] = []
    for derived, object_type in rows:
        mine = by_derived[derived.id]
        signature = _signature(mine)
        if (str(derived.id), signature) in ignored:
            continue
        managed_by = sorted(
            {kinds[one.relation].managed_by for one in mine if one.relation in kinds} - {""}
        )
        denied = editable.why_not(derived.owner_workspace_id)
        items.append(
            Item(
                kind=KIND,
                id=str(derived.id),
                question=f"「{derived.label}」 의 원천이 바뀌었습니다 — 영향을 확인할까요?",
                evidence=[
                    f"{object_type.label} — 원천 {len(mine)}개가 고정 뒤에 바뀜",
                    *(lineage.reason(one, labels.get(one.source_id)) for one in mine),
                ],
                choices=list(CHOICES),
                recommended="checked",
                confidence=None,
                reason="원천의 변경이 이 파생물에 영향이 있는지는 사람이 봅니다 — 확신도 없이 "
                "「영향 없음 확인」 을 앞에 둡니다(「추천대로 모두」 에는 들지 않습니다).",
                type_slug=object_type.slug,
                type_label=object_type.label,
                workspace_id=derived.owner_workspace_id,
                rows=len(mine),
                blocked=(
                    f"허브({', '.join(managed_by)})가 관리하는 계보 관계입니다 — 허브에서 "
                    "다시 고정한 뒤 받으세요."
                    if managed_by
                    else denied
                ),
                link=f"/o/{object_type.slug}/{derived.id}",
                signature=signature,
                priority=float(len(mine)),
                payload={
                    "links": [str(one.id) for one in mine],
                    "pins": {
                        str(one.id): {
                            "revision": one.pinned_revision,
                            "at": one.pinned_at.isoformat() if one.pinned_at else None,
                        }
                        for one in mine
                    },
                },
            )
        )
    notes: list[str] = []
    if total_links > LINKS_CAP:
        notes.append(
            f"낡은 계보 관계가 {total_links:,}개라 앞의 {LINKS_CAP:,}개만 읽었습니다 — "
            "처리하면 다음 것이 표시됩니다."
        )
    return Collected(items=items, total=len(items), notes=notes)


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


def _derived(db: Session, item: Item) -> ObjectInstance:
    found = db.get(ObjectInstance, uuid.UUID(item.id))
    if found is None or found.deleted_at is not None:
        raise Conflict(code("REVIEW", 6), "그 파생물이 삭제됐습니다.")
    return found


def _ids(item: Item) -> list[uuid.UUID]:
    return [uuid.UUID(one) for one in item.payload.get("links") or []]


def check(db: Session, user: User, item: Item, value: str) -> Check:
    if value == IGNORE:
        return Check(
            status="apply",
            effect=f"「{item.question}」 무시 — 원천이 또 바뀌기 전까지 표시하지 않음",
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
            message="영향 없음 확인(checked) · 다시 만들었음(rebuilt) 중에서 선택하세요.",
        )
    try:
        derived = _derived(db, item)
        mine = [
            one for one in lineage.source_links(db, derived.id) if one.id in set(_ids(item))
        ]
        denied = lineage.blocker(db, user, derived, mine)
    except AppError as refused:
        return Check(
            status="blocked", effect="", choice_label=choice.label, message=refused.message
        )
    effect = (
        f"「{derived.label}」 의 원천 관계 {len(mine)}개를 지금 값으로 "
        f"다시 고정({choice.label})"
    )
    if denied is not None:
        return Check(
            status="blocked", effect=effect, choice_label=choice.label, message=denied.message
        )
    return Check(
        status="apply", effect=effect, choice_label=choice.label, rows=len(mine), undoable=True
    )


def apply(db: Session, user: User, item: Item, value: str, _in_job: bool) -> Applied:
    if value == IGNORE:
        return Applied(rows=0, undo={"op": "ignore"})
    derived = _derived(db, item)
    basis: lineage.Basis = "rebuilt" if value == "rebuilt" else "checked"
    mine = {one.id for one in lineage.source_links(db, derived.id)}
    wanted = [one for one in _ids(item) if one in mine]
    if not wanted:
        raise Conflict(code("REVIEW", 6), "그 계보 관계가 그 사이 해제됐습니다.")
    done = lineage.repin(
        db,
        user,
        derived,
        relation_ids=wanted,
        basis=basis,
        reason_text=f"검토함 — 다시 고정({lineage.BASIS_LABELS[basis]})",
    )
    return Applied(
        rows=len(done),
        message="" if done else "이미 지금 값으로 고정돼 있었습니다.",
        undo={
            "op": "repin",
            "derived_id": str(derived.id),
            "pins": {
                str(one.relation_id): {
                    "revision": one.before.revision,
                    "at": one.before.at.isoformat() if one.before.at else None,
                }
                for one in done
            },
        },
    )


def undo(db: Session, user: User, data: dict[str, Any], apply: bool) -> tuple[bool, str]:
    if data.get("op") == "ignore":
        return True, "무시를 거두고 다시 표시"
    if data.get("op") != "repin":
        return False, "복원할 수 없는 결정입니다."
    derived = db.get(ObjectInstance, uuid.UUID(str(data.get("derived_id"))))
    if derived is None or derived.deleted_at is not None:
        return False, "그 파생물이 삭제됐습니다."
    pins = {
        uuid.UUID(key): lineage.Pin(
            revision=value.get("revision"),
            at=datetime.fromisoformat(value["at"]) if value.get("at") else None,
        )
        for key, value in (data.get("pins") or {}).items()
    }
    if not pins:
        return True, "바뀐 고정이 없었습니다 — 복원할 것이 없음"
    try:
        count, skipped = lineage.restore(
            db,
            user,
            derived,
            pins,
            apply=apply,
            reason_text="검토함 복원 — 고정을 앞 값으로",
        )
    except AppError as denied:
        return False, denied.message
    if not count:
        return False, "복원할 계보 관계가 남아 있지 않습니다 — " + "; ".join(skipped)
    message = f"「{derived.label}」 의 원천 관계 {count}개의 고정을 앞 값으로 복원"
    if skipped:
        message += f"(건너뜀 {len(skipped)}개 — 그 사이 해제됨)"
    return True, message


STALE_LINEAGE = registry.Provider(
    kind=KIND,
    label="낡은 파생물",
    description="원천(도면 · 메시 …)이 고정 뒤에 바뀐 파생물 — 영향을 확인하고 지금 원천으로 "
    "다시 고정합니다. 위에서 낡은 것은 그 원천을 다시 고정하면 함께 풀립니다.",
    link="/review?kind=stale_lineage",
    can_view=_manager,
    collect=collect,
    check=check,
    apply=apply,
    undo=undo,
    order=50,
)

PROVIDERS = (STALE_LINEAGE,)
