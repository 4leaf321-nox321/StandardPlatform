"""묶음 내보내기 — 허브가 쌍둥이에 **가져오기와 같은 모양으로** 준다.

허브 플랫폼은 사이드바 묶음(`plm` · `core` …)의 정의 · 객체 · 관계를 이것으로 내보내고,
쌍둥이는 받은 것을 그대로 `POST /bundles/import` 에 `source` 를 붙여 보낸다
(미리 보기 → 적용). **묶음은 여럿을 한 번에** 낼 수 있다 — 코어를 축별로 나눠 둔 설치에서
하나씩 내보내면 그 사이 참조가 서로를 가리켜 어느 쪽도 못 보낸다.
새 형식을 만들지 않는다 — 따로 만들면 변환기가 하나 더 생기고, 둘은 언젠가 갈린다.

## 무엇이 가고 무엇이 안 가나

- 정의: 고른 묶음들, 그 안의 타입(원 표를 비추는 타입 제외), **양끝이 모두 그 안인** 관계 종류.
- 객체: 지워지지 않은 것 전부. 참조 칸은 상대의 **식별자**로 — 받는 쪽에서 다시 푼다. 사용
  중지도 상태로 간다.
- 관계: 내보내는 관계 종류의 줄 — **붙은 속성도 `properties` 로 함께**(근거 건수처럼).
- 별칭: **배열로.** 예전에는 `;` 로 이어 보냈고, 그래서 이름 안에 `;` 가 든 별칭이 받는 쪽에서
  둘로 갈렸다(고장 모드 80건 · 메커니즘 482건이 그런 이름이다).
- **무덤(`tombstones`):** 허브에서 지운 객체와 끊긴 선. 예전에는 아무것도 안 갔고, 그래서
  받는 쪽에는 지운 것이 살아 남았다(코어 API 는 이미 `merged_into` 를 주니 **두 길이 다르게
  움직였다**). 받는 쪽은 지우지 않는다 — **사용 중지**로 두고, 합쳐진 것은 이긴 쪽에
  **합친다.**
- **안 가는 것:** 비운 설명, 파일 칸. 경고로 말한다.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects import aliases
from app.modules.objects.bulk import MAX_ROWS, relation_defs
from app.modules.objects.models import (
    ObjectAlias,
    ObjectInstance,
    ObjectRelation,
    ObjectRelationTombstone,
)
from app.modules.objects.services import properties_of
from app.modules.ontology import importer, interfaces
from app.modules.ontology.models import NavGroup, ObjectType, PropertyDef, RelationType
from app.shared.errors import Conflict, NotFound, code

FORMAT = "sp-bundle/1"


def export_group(
    db: Session,
    group_slug: str | list[str],
    *,
    allow_outside_refs: bool = False,
    since: datetime | None = None,
) -> dict[str, Any]:
    """고른 묶음(하나 또는 여럿)을 한 봉투로.

    `allow_outside_refs` 는 **밖을 가리키는 참조를 막지 않는다** — 받는 쪽이 그 타입을 이미
    다른 묶음으로 받아 뒀을 때 쓴다. 기본은 막는다: 없는 타입을 가리키면 받는 쪽에서 묶음
    **전체**가 거절되고(전부 아니면 무), 그때 사람은 왜 아무것도 안 들어갔는지 모른다.

    `since` 는 **사라진 것**에만 걸린다 — 그 시각 뒤에 지워진 것만 싣는다. 무덤은 쌓이기만
    하므로, 안 자르면 몇 해가 지난 뒤 봉투의 대부분이 「없어진 것」 이 된다. 살아 있는 것은
    늘 전량이다(받는 쪽이 그것으로 제 상태를 맞춘다).
    """
    wanted = [group_slug] if isinstance(group_slug, str) else list(group_slug)
    if not wanted:
        raise Conflict(code("BUNDLES", 21), "내보낼 사이드바 묶음을 하나는 골라야 합니다.")
    groups: list[NavGroup] = []
    for slug in wanted:
        found = db.scalar(select(NavGroup).where(NavGroup.slug == slug))
        if found is None:
            raise NotFound(code("BUNDLES", 20), f"사이드바 묶음을 찾을 수 없습니다: {slug}")
        groups.append(found)
    types = list(
        db.scalars(
            select(ObjectType)
            .where(
                ObjectType.nav_group_id.in_([one.id for one in groups]),
                ObjectType.kind_class != "system",
            )
            .order_by(ObjectType.sort_order, ObjectType.slug)
        )
    )
    if not types:
        raise Conflict(
            code("BUNDLES", 21),
            f"{', '.join(one.label for one in groups)} 묶음에 내보낼 타입이 없습니다.",
        )
    slugs = {one.slug for one in types}
    defs_of = {
        one.slug: [d for d in properties_of(db, one.id) if d.data_type != "file"]
        for one in types
    }
    # **타입이 구현한 인터페이스를 함께 싣는다**(상위 인터페이스까지) — 안 실으면 받는 쪽에서
    # 「없는 인터페이스를 구현한다」 로 묶음 전체가 거절된다.
    carried = set(
        interfaces.closure(
            {name for one in types for name in one.interface_slugs or []},
            interfaces.load(db).extends_of(),
        )
    )
    #: 묶음 안 — 타입과 함께 실리는 인터페이스. 참조 대상 · 관계 끝이 이 안이면 받는 쪽에서
    #: 풀린다.
    inside = slugs | carried
    outside = sorted(
        f"{one.slug}.{d.key} → {d.ref_type_slug}"
        for one in types
        for d in defs_of[one.slug]
        if d.data_type == "object_ref" and d.ref_type_slug not in inside
    )
    if outside and not allow_outside_refs:
        # 받는 쪽에 그 타입이 없으면 참조가 안 풀려 묶음 전체가 막힌다 — 보내기 전에 말한다.
        raise Conflict(
            code("BUNDLES", 22),
            "묶음 밖을 가리키는 참조 칸이 있어 받는 쪽에서 풀리지 않습니다: "
            + ", ".join(outside)
            + ". 묶음을 함께 고르거나, 받는 쪽이 그 타입을 이미 가졌으면 "
            "「밖을 가리키는 참조 허용」 으로 보내세요.",
        )

    schema = importer.capture(db)
    # 관계 종류는 **양끝이 다 실릴 때만** — 끝에 적힌 인터페이스도 함께 실리면 된다.
    relation_types = [
        one
        for one in schema["relation_types"]
        if one.get("src_type_slugs")
        and one.get("dst_type_slugs")
        and set(one["src_type_slugs"]) <= inside
        and set(one["dst_type_slugs"]) <= inside
    ]
    ontology = {
        "groups": [one for one in schema["groups"] if one["slug"] in set(wanted)],
        "interfaces": [one for one in schema["interfaces"] if one["slug"] in carried],
        "types": [one for one in schema["types"] if one["slug"] in slugs],
        "relation_types": relation_types,
    }

    ordered = _ordered(types, defs_of)
    type_of = {one.id: one for one in types}
    rows = list(
        db.scalars(
            select(ObjectInstance)
            .where(
                ObjectInstance.type_id.in_(list(type_of)),
                ObjectInstance.deleted_at.is_(None),
            )
            .order_by(ObjectInstance.created_at, ObjectInstance.id)
        )
    )
    names = {row.id: row.key or row.label for row in rows}
    human = {
        object_id: [one for one in found if one.kind == aliases.HUMAN]
        for object_id, found in aliases.of(db, [row.id for row in rows]).items()
    }
    warnings: list[str] = []
    if outside:
        warnings.append(
            "묶음 밖을 가리키는 참조 칸을 그대로 보낸다 — 받는 쪽에 그 타입이 없으면 "
            "**묶음 전체가** 거절된다: " + ", ".join(outside)
        )
    keyless: Counter[str] = Counter()

    by_type: dict[str, list[dict[str, Any]]] = {one.slug: [] for one in types}
    for row in rows:
        object_type = type_of[row.type_id]
        if not row.key:
            keyless[object_type.slug] += 1
        by_type[object_type.slug].append(
            _row(row, defs_of[object_type.slug], names, human.get(row.id, []))
        )
    for slug, count in sorted(keyless.items()):
        warnings.append(
            f"{slug}: 식별자가 없는 객체 {count}개 — "
            "받는 쪽은 이름으로 찾으므로 이름이 겹치면 갈린다"
        )

    objects: list[dict[str, Any]] = []
    for one in ordered:
        batch = by_type[one.slug]
        for at in range(0, max(len(batch), 1), MAX_ROWS):
            chunk = batch[at : at + MAX_ROWS]
            if chunk:
                objects.append(
                    {
                        "type_slug": one.slug,
                        "workspace_slug": None,
                        "rows": chunk,
                        # **허브가 정본이다** — 허브에서 뺀 별칭은 받는 쪽에서도 빠져야 한다.
                        "aliases_mode": "replace",
                    }
                )

    kind_slugs = [one["slug"] for one in relation_types]
    type_by_object = {row.id: type_of[row.type_id].slug for row in rows}
    relations = _relations(
        db, [one["slug"] for one in relation_types], type_by_object, names, warnings
    )
    # **살아 있는 것과 겹치는 무덤은 빼고 보낸다** — 아래에서 왜 그런지 적는다.
    tombstones = _tombstones(
        db,
        type_of,
        kind_slugs,
        alive_keys={(type_of[row.type_id].slug, row.key or row.label) for row in rows},
        alive_edges={
            (batch["type_slug"], str(one["src"]), str(one["relation"]), str(one["dst"]))
            for batch in relations
            for one in batch["rows"]
        },
        since=since,
    )

    return {
        "format": FORMAT,
        "group": ",".join(wanted),
        "groups": wanted,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ontology": ontology,
        "objects": objects,
        "relations": relations,
        "tombstones": tombstones,
        "counts": {
            "types": len(types),
            "relation_types": len(relation_types),
            "objects": len(rows),
            "relations": sum(len(one["rows"]) for one in relations),
            "tombstones": len(tombstones["objects"]) + len(tombstones["relations"]),
        },
        "warnings": warnings,
    }


def _tombstones(
    db: Session,
    type_of: dict[uuid.UUID, ObjectType],
    kind_slugs: list[str],
    *,
    alive_keys: set[tuple[str, str]],
    alive_edges: set[tuple[str, str, str, str]],
    since: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """**사라진 것.** 지운 객체(합쳐진 것은 이긴 쪽의 식별자와 함께)와 끊긴 선.

    끝점은 식별자로 — 받는 쪽의 uuid 는 우리 것과 다르다. 끝점까지 정말 사라져 식별자를
    못 만드는 선은 뺀다(그 객체의 무덤이 이미 그것을 말한다).

    ⚠️ **살아 있는 것과 겹치는 무덤은 빼고 보낸다.** 무덤은 식별자 · 세 끝으로만 말하므로,
       같은 식별자를 다시 만들거나 같은 선을 다시 이으면 받는 쪽에서는 옛 무덤과 새것이
       구별되지 않는다 — 방금 받은 것을 그 자리에서 다시 사용 중지 · 끊기 한다(실측).
       이 봉투에 **살아서 실려 가는 것**이 정본이다.
    """
    dead = select(ObjectInstance).where(
        ObjectInstance.type_id.in_(list(type_of)),
        ObjectInstance.deleted_at.is_not(None),
    )
    if since is not None:
        dead = dead.where(ObjectInstance.deleted_at > since)
    gone = list(db.scalars(dead.order_by(ObjectInstance.deleted_at)))
    won_ids = {one.merged_into_id for one in gone if one.merged_into_id}
    winners = {
        one.id: one.key or one.label
        for one in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(won_ids)))
    }
    objects = [
        {
            "type_slug": type_of[one.type_id].slug,
            "key": one.key or one.label,
            "merged_into": winners.get(one.merged_into_id) if one.merged_into_id else None,
            "at": one.deleted_at.isoformat() if one.deleted_at else None,
        }
        for one in gone
        if (type_of[one.type_id].slug, one.key or one.label) not in alive_keys
    ]

    edges: list[dict[str, Any]] = []
    if kind_slugs:
        cut = select(ObjectRelationTombstone).where(
            ObjectRelationTombstone.relation.in_(kind_slugs)
        )
        if since is not None:
            cut = cut.where(ObjectRelationTombstone.removed_at > since)
        graves = list(db.scalars(cut.order_by(ObjectRelationTombstone.removed_at)))
        end_ids = {one.src_object_id for one in graves} | {one.dst_object_id for one in graves}
        ends = {
            one.id: one
            for one in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(end_ids)))
        }
        for grave in graves:
            start, end = ends.get(grave.src_object_id), ends.get(grave.dst_object_id)
            if start is None or end is None or start.type_id not in type_of:
                continue
            edge = (
                type_of[start.type_id].slug,
                start.key or start.label,
                grave.relation,
                end.key or end.label,
            )
            if edge in alive_edges:
                # 다시 이은 선이다 — 무덤을 보내면 받는 쪽이 방금 받은 선을 끊는다.
                continue
            edges.append(
                {
                    "type_slug": edge[0],
                    "src": edge[1],
                    "relation": edge[2],
                    "dst": edge[3],
                    "at": grave.removed_at.isoformat(),
                }
            )
    return {"objects": objects, "relations": edges}


def _ordered(
    types: list[ObjectType], defs_of: dict[str, list[PropertyDef]]
) -> list[ObjectType]:
    """참조되는 타입이 먼저 — 받는 쪽이 적는 차례대로 넣기 때문이다. 서로 가리키면 적힌
    차례로."""
    needs = {
        one.slug: {
            d.ref_type_slug
            for d in defs_of[one.slug]
            if d.data_type == "object_ref" and d.ref_type_slug and d.ref_type_slug != one.slug
        }
        for one in types
    }
    placed: list[ObjectType] = []
    done: set[str] = set()
    left = list(types)
    while left:
        ready = [one for one in left if needs[one.slug] <= done]
        if not ready:
            ready = left[:1]
        for one in ready:
            placed.append(one)
            done.add(one.slug)
            left.remove(one)
    return placed


def _row(
    row: ObjectInstance,
    defs: list[PropertyDef],
    names: dict[uuid.UUID, str],
    human: list[ObjectAlias],
) -> dict[str, Any]:
    out: dict[str, Any] = {"key": row.key or None, "label": row.label, "status": row.status}
    history = [one for one in (row.previous_keys or []) if one and one != row.key]
    if history:
        # 받는 쪽은 이 값으로 제가 가진 행을 찾아 식별자를 옮긴다(`renamed_from` 열).
        # **목록으로도 보낸다** — 두 번 바뀌었으면 받는 쪽이 가진 것은 마지막 것이 아니다.
        out["renamed_from"] = history[-1]
        if len(history) > 1:
            out["previous_keys"] = history
    if row.description:
        out["description"] = row.description
    if row.valid_from_year is not None:
        out["valid_from_year"] = row.valid_from_year
    if row.valid_to_year is not None:
        out["valid_to_year"] = row.valid_to_year
    if human:
        # **배열로, 그리고 붙은 것까지.** 이름만 보내면 받는 쪽에서는 그것이 어디서 왔는지
        # · 사람이 봤는지 알 수 없어, 허브에서 확인한 것이 쌍둥이에는 **영영 검수 대기**로
        # 남는다(실측). 이름 안에 `;` 가 들면 이어 보낸 것은 받는 쪽에서 둘로 갈린다.
        out["aliases"] = [
            {
                "value": one.value,
                **({"source": one.source} if one.source else {}),
                **({"note": one.note} if one.note else {}),
                **({"verified": True} if one.verified_at is not None else {}),
            }
            for one in human
        ]
    values = row.properties or {}
    for definition in defs:
        raw = values.get(definition.key)
        if raw is None:
            # **비움도 보낸다** — 허브에서 지운 값이 받는 쪽에 남으면 둘이 갈린다.
            out[definition.key] = None
            continue
        if definition.data_type == "object_ref":
            out[definition.key] = (
                [_name(item, names) for item in raw]
                if isinstance(raw, list)
                else _name(raw, names)
            )
        else:
            out[definition.key] = raw
    return out


def _name(raw: Any, names: dict[uuid.UUID, str]) -> str:
    try:
        return names.get(uuid.UUID(str(raw)), str(raw))
    except ValueError:
        return str(raw)


def _relations(
    db: Session,
    kinds: list[str],
    type_by_object: dict[uuid.UUID, str],
    names: dict[uuid.UUID, str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """내보내는 관계 종류의 줄 — 출발 타입마다. **붙은 속성도 함께 간다.**"""
    if not kinds or not names:
        return []
    defs_of = {
        one.slug: relation_defs(db, one)
        for one in db.scalars(select(RelationType).where(RelationType.slug.in_(kinds)))
    }
    edges = db.scalars(
        select(ObjectRelation)
        .where(
            ObjectRelation.relation.in_(kinds),
            ObjectRelation.src_object_id.in_(list(names)),
            ObjectRelation.dst_object_id.in_(list(names)),
        )
        .order_by(ObjectRelation.created_at, ObjectRelation.id)
    )
    rows_by_type: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        row: dict[str, Any] = {
            "src": names[edge.src_object_id],
            "relation": edge.relation,
            "dst": names[edge.dst_object_id],
            "evidence_note": edge.evidence_note or "",
        }
        properties = _properties(edge.properties or {}, defs_of.get(edge.relation, []), names)
        if properties:
            row["properties"] = properties
        rows_by_type.setdefault(type_by_object[edge.src_object_id], []).append(row)
    out: list[dict[str, Any]] = []
    for slug, rows in rows_by_type.items():
        for chunk, mode in _chunks(rows):
            if mode == "add":
                warnings.append(
                    f"{slug}: 한 객체의 한 관계 종류가 묶음 하나({MAX_ROWS}줄)에 안 들어가 "
                    "「맞춤」 을 못 쓴다 — 허브에서 끊은 선이 받는 쪽에 남는다"
                )
            out.append({"type_slug": slug, "rows": chunk, "mode": mode})
    return out


def _chunks(rows: list[dict[str, Any]]) -> list[tuple[list[dict[str, Any]], str]]:
    """묶음으로 나누되 **(출발 객체 · 관계 종류)를 쪼개지 않는다.**

    받는 쪽의 「맞춤」(`mode="replace"`)은 그 쌍을 범위로 본다. 한 쌍이 두 묶음에 걸치면
    **뒤 묶음이 앞 묶음이 넣은 선을 끊는다** — 내보낸 것과 받은 것이 달라진다. 한 쌍이
    묶음 하나에 안 들어갈 만큼 크면 맞춤을 포기하고 더하기로 보낸다(경고로 말한다).
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((str(row["src"]), str(row["relation"])), []).append(row)
    if any(len(one) > MAX_ROWS for one in groups.values()):
        return [(rows[at : at + MAX_ROWS], "add") for at in range(0, len(rows), MAX_ROWS)]
    out: list[tuple[list[dict[str, Any]], str]] = []
    current: list[dict[str, Any]] = []
    for group in groups.values():
        if current and len(current) + len(group) > MAX_ROWS:
            out.append((current, "replace"))
            current = []
        current.extend(group)
    if current:
        out.append((current, "replace"))
    return out


def _properties(
    values: dict[str, Any], defs: list[PropertyDef], names: dict[uuid.UUID, str]
) -> dict[str, Any]:
    """관계에 붙은 값 — 참조 칸은 **식별자로** 바꾼다(받는 쪽의 uuid 는 다르다)."""
    out: dict[str, Any] = {}
    for definition in defs:
        raw = values.get(definition.key)
        if raw is None:
            continue
        if definition.data_type == "object_ref":
            out[definition.key] = (
                [_name(item, names) for item in raw]
                if isinstance(raw, list)
                else _name(raw, names)
            )
        else:
            out[definition.key] = raw
    return out
