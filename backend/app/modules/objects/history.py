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
from app.modules.objects import humanedits
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import (
    audit_state,
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology import conversion, retype
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import InvalidValue, validate_properties
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.errors import AppError, Conflict, NotFound, code

#: 값 기록에서 스냅샷을 구성하는 칸. 기록의 `changes` 키와 같다.
STATE_FIELDS = (
    "key",
    "label",
    "description",
    "status",
    "valid_from_year",
    "valid_to_year",
    "owner_workspace_id",
    "properties",
)


@dataclass
class Snapshot:
    key: str | None
    label: str
    status: str
    properties: dict[str, Any]
    description: str = ""
    valid_from_year: int | None = None
    valid_to_year: int | None = None
    """소유 부서는 **싣지 않는다** — 이력에는 서지만 되돌리기로 부서를 옮기지는 않는다.
    부서를 옮기는 일은 권한이 실린 일이라 그 화면(여럿 고치기·부서 통폐합)에서 한다."""


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
    batch: dict[str, Any] | None = None
    """여럿 골라 고치기로 같이 바뀐 기록이면 `{id, field_label, size}`."""


def _workspace_names(db: Session, rows: list[AuditEntry]) -> dict[str, str]:
    """기록에 남은 소유 부서 id → 이름. **기록은 id 로 남기고 보여 줄 때 이름으로** —
    부서 이름은 바뀌므로 이름으로 남기면 옛 기록이 지금 없는 이름을 가리킨다."""
    ids: set[uuid.UUID] = set()
    for entry in rows:
        change = (entry.changes or {}).get("owner_workspace_id")
        if not isinstance(change, dict):
            continue
        for value in (change.get("before"), change.get("after")):
            try:
                ids.add(uuid.UUID(str(value)))
            except ValueError:
                continue
    if not ids:
        return {}
    return {
        str(one.id): one.name
        for one in db.scalars(select(Workspace).where(Workspace.id.in_(ids)))
    }


def _split_properties(
    changes: dict[str, Any], workspaces: dict[str, str] | None = None
) -> dict[str, Any]:
    """통째 속성 diff 를 칸별로 — `properties` 하나를 `properties.<키>` 여럿으로. 소유
    부서는 id 대신 이름으로(전역이면 「(전역)」, 지워졌으면 「(지워진 부서)」)."""
    out: dict[str, Any] = {}
    names = workspaces or {}

    def workspace(value: Any) -> str:
        if value is None:
            return "(전역)"
        return names.get(str(value), "(지워진 부서)")

    for key, value in changes.items():
        # `_` 로 시작하는 키는 기록에 붙인 표식(묶음 번호 등)이다 — 사람이 읽을 칸이 아니다.
        if key.startswith("_"):
            continue
        # 「전→후」 꼴만 — 합치기의 merged_from 같은 메모는 사람이 읽을 칸이 아니다.
        if not isinstance(value, dict) or "after" not in value:
            continue
        if key == "owner_workspace_id":
            out[key] = {
                "before": workspace(value.get("before")),
                "after": workspace(value.get("after")),
            }
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
                # 원 표와 이은 선(`object_links`)도 같은 모양으로 남는다 — 이력에 함께.
                AuditEntry.target_table.in_(("object_relations", "object_links"))
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
    running: dict[str, Any] = audit_state(row)
    workspaces = _workspace_names(db, rows)
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
                description=running["description"] or "",
                valid_from_year=running["valid_from_year"],
                valid_to_year=running["valid_to_year"],
            )
            for name in STATE_FIELDS:
                change = changes.get(name)
                if isinstance(change, dict) and "before" in change:
                    running[name] = (
                        _properties_before(running["properties"], change)
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
                changes=_split_properties(changes, workspaces) if is_object else {},
                relation=relation,
                snapshot=snapshot,
                batch=_batch_of(changes) if is_object else None,
            )
        )
    return out


def _properties_before(now: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """속성 기록 하나 앞의 값. 보통은 기록이 속성 전체를 담는다. **바뀐 칸만** 담은 기록
    (`keys` — 종류 변경 · 병합, `rewrite.changed_property`)은 그 칸만 되짚고 나머지는 지금 값
    그대로다."""
    keys = change.get("keys")
    if not isinstance(keys, list):
        return dict(change["before"] or {})
    before = change["before"] or {}
    out = dict(now)
    for key in keys:
        if key in before:
            out[key] = before[key]
        else:
            out.pop(key, None)
    return out


def _batch_of(changes: dict[str, Any]) -> dict[str, Any] | None:
    meta = changes.get("_batch")
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    return {
        "id": meta["id"],
        "field_label": str(meta.get("field_label") or meta.get("field") or ""),
        "size": int(meta.get("size") or 0),
    }


def snapshot_at(
    db: Session, row: ObjectInstance, entry_id: uuid.UUID
) -> tuple[Entry, Snapshot]:
    for entry in history_of(db, row):
        if entry.id == entry_id:
            if entry.snapshot is None:
                raise Conflict(code("OBJECTS", 60), "관계 기록에는 되돌릴 값이 없습니다.")
            return entry, entry.snapshot
    raise NotFound(code("OBJECTS", 61), "그 기록을 찾을 수 없습니다.")


#: 저장값의 파이썬 모양 — 종류마다. 그때 값이 지금 종류의 모양이 아니면 종류가 바뀐 것이다.
_SHAPE_OF: dict[str, tuple[type, ...]] = {
    "number": (int, float),
    "bool": (bool,),
}


def _kind_changed(definition: PropertyDef, raw: Any) -> bool:
    """그때 값이 **지금 종류의 모양이 아닌가** — 그러면 그 사이 종류가 변경된 것이다
    (ADR 0007)."""
    items = raw if isinstance(raw, list) else [raw]
    if definition.data_type == "object_ref":
        # 참조 칸에 id 가 아닌 글자 — 그 사이 글 → 참조로 바뀌었다(ADR 0009).
        return any(item is not None and not _is_id(item) for item in items)
    wanted = _SHAPE_OF.get(definition.data_type, (str,))
    for item in items:
        if item is None:
            continue
        if isinstance(item, bool) and bool not in wanted:
            return True
        if not isinstance(item, wanted):
            return True
    return False


def _is_id(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _then_converted(
    definition: PropertyDef,
    raw: Any,
    values: dict[str, Any],
    linker: retype.RefLinker,
    *,
    from_ref: bool = False,
) -> None:
    """그때 값 하나를 지금 종류로 — `values` 를 고친다. 안 되면 OBJECTS-95."""
    result = conversion.convert_stored(definition, raw, linker=linker, from_ref=from_ref)
    if result.failures:
        raise AppError(
            code("OBJECTS", 95),
            f"{definition.label}: 그때 값 {raw!r} 은(는) 지금 종류"
            f"로 변환할 수 없습니다 — {result.failures[0][1]}",
            status=422,
        ) from None
    if result.remove:
        values.pop(definition.key, None)
    else:
        values[definition.key] = result.value


def _then_values(
    db: Session,
    defs: list[PropertyDef],
    values: dict[str, Any],
    *,
    from_ref: set[str] | None = None,
) -> dict[str, Any]:
    """그때 값을 **지금 정의로** 검사한다. 그 사이 종류가 변경됐으면(글 → 숫자) 종류 변경과
    같은 규칙으로 변환해 넣는다 — 안 그러면 종류를 바꾼 뒤로는 그 전 이력으로 되돌릴 수 없다.

    글 → 참조로 바뀐 칸은 그때 글자를 이름 풀이로, 참조 → 글로 바뀐 칸(`from_ref` — 그때 값은
    상대의 id 라 글로도 검사를 통과한다)은 상대의 식별자로 바꾼다(ADR 0009).

    변환할 수 없으면 그렇다고 말한다(OBJECTS-95). 종류가 그대로인데 안 맞는 것(고를 값에서 빠진
    값 등)은 예전처럼 저장할 때의 오류를 그대로 낸다.
    """
    linker = retype.RefLinker(db)
    values = dict(values)
    for definition in defs:
        if (
            definition.key in (from_ref or set())
            and definition.data_type in conversion.LINKABLE
            and definition.key in values
        ):
            _then_converted(definition, values[definition.key], values, linker, from_ref=True)
    try:
        return validate_properties(defs, values)
    except InvalidValue as original:
        converted = dict(values)
        for definition in defs:
            if definition.key not in values or not _kind_changed(
                definition, values[definition.key]
            ):
                continue
            _then_converted(definition, values[definition.key], converted, linker)
        if converted == values:
            raise original from None
        return validate_properties(defs, converted)


def _retyped_from_ref(db: Session, object_type: ObjectType, entry_id: uuid.UUID) -> set[str]:
    """그 기록 **뒤에** 참조 → 다른 종류로 바뀐 이 타입의 속성 키 — 그때 값은 상대의 id 다."""
    since = db.scalar(select(AuditEntry.seq).where(AuditEntry.id == entry_id))
    if since is None:
        return set()
    labels = db.scalars(
        select(AuditEntry.target_label).where(
            AuditEntry.action == "ontology.property.retype",
            AuditEntry.seq > since,
            AuditEntry.target_label.startswith(f"{object_type.slug}."),
            AuditEntry.changes["data_type"]["before"].astext == "object_ref",
        )
    )
    return {label.split(".", 1)[1] for label in labels if label}


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

    before = audit_state(row)
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
    properties = _then_values(
        db,
        defs,
        {k: v for k, v in wanted.properties.items() if k in known},
        from_ref=_retyped_from_ref(db, object_type, entry.id),
    )
    require_refs_exist(db, defs, properties, row.properties or {})
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
    row.description = wanted.description
    row.valid_from_year = wanted.valid_from_year
    row.valid_to_year = wanted.valid_to_year
    after = audit_state(row)
    # 되돌리기도 **사람이 고친 것**이다 — 그 값이 다음 적재에 또 덮이면 되돌린 뜻이 없다.
    humanedits.record(row, before, after)
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
