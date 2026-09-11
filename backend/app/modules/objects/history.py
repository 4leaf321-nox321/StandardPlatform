"""객체의 이력 — **「작년엔 뭐였지」 에 그 객체 화면에서 답한다.**

감사 로그에는 「누가 어느 칸을 무엇에서 무엇으로」 가 이미 다 남아 있다. 다만 전사
목록 하나뿐이라 그 객체를 보면서는 못 읽는다. 여기서는 그 객체에 걸린 기록만 모아
시간순으로 주고, 각 기록에 **그 시점의 값 전체**를 붙인다.

## 그 시점의 값을 어떻게 아나

기록은 바뀐 것만 남긴다(`audit.diff`). 그래서 지금 값에서 **거꾸로** 간다: 마지막
기록의 「뒤」 는 지금 값이고, 그 기록의 `before` 를 대면 그 「앞」 이 나오고, 그것이
바로 전 기록의 「뒤」 다. 처음까지 가면 만들 때의 값이다. 스냅샷을 따로 저장하지 않는
이유는 그것 — 있는 기록으로 다 나온다.

## 되돌리기는 저장이다

「그 시점 값으로 되돌리기」 는 고치기와 **같은 검증**을 거친다. 그때 가리키던 객체가
지금은 지워졌거나, 그 사이 「무게는 1 이상」 규칙이 생겼으면 막고 이유를 말한다.
검증을 건너뛰면 되돌린 값이 화면에서 빈 칸으로 뜨고, 그것이 「없는 값」 인지 「지워진
것을 가리키는 값」 인지 아무도 모른다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import (
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import validate_properties
from app.shared import audit
from app.shared.errors import Conflict, NotFound, code

#: 값 기록에서 스냅샷을 구성하는 칸. 기록의 `changes` 키와 같다.
STATE_FIELDS = ("key", "label", "status", "properties")


@dataclass
class Snapshot:
    key: str | None
    label: str
    status: str
    properties: dict[str, Any]


@dataclass
class Entry:
    id: uuid.UUID
    at: datetime
    actor_label: str
    action: str
    reason: str | None
    """`object` 면 이 객체의 값이 바뀐 기록, `relation` 이면 관계가 걸리거나 끊긴 기록."""
    kind: str
    changes: dict[str, Any] = field(default_factory=dict)
    """속성은 **칸별로** 풀어 준다 — 통째 diff 는 사람이 못 읽는다."""
    relation: dict[str, Any] | None = None
    snapshot: Snapshot | None = None


def _split_properties(changes: dict[str, Any]) -> dict[str, Any]:
    """통째 속성 diff 를 칸별로 — `properties` 하나를 `properties.<키>` 여럿으로."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        # 「전→후」 꼴만 — 합치기의 merged_from 같은 메모는 사람이 읽을 칸이 아니다.
        if not isinstance(value, dict) or "after" not in value:
            continue
        if key != "properties":
            out[key] = value
            continue
        before = value.get("before") or {}
        after = value.get("after") or {}
        for prop in sorted(set(before) | set(after)):
            if before.get(prop) != after.get(prop):
                out[f"properties.{prop}"] = {
                    "before": before.get(prop),
                    "after": after.get(prop),
                }
    return out


def _entries(db: Session, row: ObjectInstance) -> list[AuditEntry]:
    """이 객체에 걸린 기록 전부, 오래된 것부터."""
    mine = str(row.id)
    stmt = (
        select(AuditEntry)
        .where(
            or_(
                (AuditEntry.target_table == "objects") & (AuditEntry.target_id == row.id),
                (AuditEntry.target_table == "object_relations")
                & or_(
                    AuditEntry.changes["src"].astext == mine,
                    AuditEntry.changes["dst"].astext == mine,
                ),
            )
        )
        # created_at 은 트랜잭션 시작 시각이라 한 트랜잭션 안에서는 같다 — 넣은 차례(seq)로.
        .order_by(AuditEntry.seq.asc())
    )
    return list(db.scalars(stmt))


def history_of(db: Session, row: ObjectInstance) -> list[Entry]:
    """이력 — 최근 것이 앞. 값 기록마다 그 시점의 값을 붙인다."""
    rows = _entries(db, row)
    # 거꾸로 — 지금 값에서 출발해 각 기록의 before 를 대며 앞으로 간다.
    running: dict[str, Any] = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }
    out: list[Entry] = []
    for entry in reversed(rows):
        is_object = entry.target_table == "objects"
        changes = entry.changes or {}
        snapshot: Snapshot | None = None
        if is_object:
            snapshot = Snapshot(
                key=running["key"],
                label=running["label"],
                status=running["status"],
                properties=dict(running["properties"]),
            )
            for name in STATE_FIELDS:
                change = changes.get(name)
                if isinstance(change, dict) and "before" in change:
                    running[name] = (
                        dict(change["before"] or {})
                        if name == "properties"
                        else change["before"]
                    )
        relation = None
        if not is_object:
            relation = {
                "relation": changes.get("relation"),
                "outgoing": changes.get("src") == str(row.id),
                "other_id": changes.get("dst")
                if changes.get("src") == str(row.id)
                else changes.get("src"),
                "other_label": (
                    changes.get("dst_label")
                    if changes.get("src") == str(row.id)
                    else changes.get("src_label")
                )
                or "",
            }
        out.append(
            Entry(
                id=entry.id,
                at=entry.created_at,
                actor_label=entry.actor_label,
                action=entry.action,
                reason=entry.reason,
                kind="object" if is_object else "relation",
                changes=_split_properties(changes) if is_object else {},
                relation=relation,
                snapshot=snapshot,
            )
        )
    return out


def snapshot_at(
    db: Session, row: ObjectInstance, entry_id: uuid.UUID
) -> tuple[Entry, Snapshot]:
    for entry in history_of(db, row):
        if entry.id == entry_id:
            if entry.snapshot is None:
                raise Conflict(code("OBJECTS", 60), "관계 기록에는 되돌릴 값이 없습니다.")
            return entry, entry.snapshot
    raise NotFound(code("OBJECTS", 61), "그 기록을 찾을 수 없습니다.")


def restore(
    db: Session,
    user: User,
    row: ObjectInstance,
    object_type: ObjectType,
    entry_id: uuid.UUID,
) -> None:
    """그 시점의 값으로 **고친다** — 저장과 같은 검증을 거쳐서."""
    entry, wanted = snapshot_at(db, row, entry_id)
    if wanted.status == "deleted":  # pragma: no cover - 상태값에 없다
        raise Conflict(code("OBJECTS", 62), "지워진 상태로는 되돌리지 않습니다.")

    before = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }
    defs = properties_of(db, object_type.id)
    key = normalize_key(object_type, wanted.key)
    if key != row.key:
        require_key_free(
            db, object_type, key, owner_workspace_id=row.owner_workspace_id, exclude_id=row.id
        )
    # 그때는 있었는데 지금은 없는 속성 정의는 **빼고** 되돌린다 — 정의를 지운 것은
    # 그 값을 더는 안 쓴다는 뜻이고, 되살리면 어느 화면에도 안 나오는 값이 된다.
    known = {d.key for d in defs if d.data_type != "file"}
    dropped = sorted(set(wanted.properties) - known)
    properties = validate_properties(
        defs, {k: v for k, v in wanted.properties.items() if k in known}
    )
    require_refs_exist(db, defs, properties)
    require_unique_properties(
        db,
        object_type,
        defs,
        properties,
        owner_workspace_id=row.owner_workspace_id,
        exclude_id=row.id,
    )

    row.key = key
    row.label = wanted.label
    row.status = wanted.status
    row.properties = properties
    after = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }
    stamp = entry.at.strftime("%Y-%m-%d %H:%M")
    note = f"{stamp} 시점 값으로 되돌림"
    if dropped:
        note += f" (지금은 없는 속성은 뺌: {', '.join(dropped)})"
    audit.record(
        db,
        action="object.update",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        changes={**audit.diff(before, after), "restored_from": str(entry.id)},
        reason=note,
    )
    db.commit()
