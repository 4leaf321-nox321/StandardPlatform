"""묶음 내보내기 — 허브가 쌍둥이에 **가져오기와 같은 모양으로** 준다.

허브 플랫폼은 PLM 기준정보(사이드바 묶음 하나 — `plm`)의 정의 · 객체 · 관계를 이것으로
내보내고, 쌍둥이는 받은 것을 그대로 `POST /bundles/import` 에 `source` 를 붙여 보낸다
(미리 보기 → 적용).
새 형식을 만들지 않는다 — 따로 만들면 변환기가 하나 더 생기고, 둘은 언젠가 갈린다.

## 무엇이 가고 무엇이 안 가나

- 정의: 그 묶음, 그 묶음의 타입(원 표를 비추는 타입 제외), **양끝이 모두 그 안인** 관계 종류.
- 객체: 지워지지 않은 것 전부. 참조 칸은 상대의 **식별자**로 — 받는 쪽에서 다시 푼다. 사용
  중지도 상태로 간다.
- 관계: 내보내는 관계 종류의 줄.
- **안 가는 것:** 허브에서 지운 객체(받는 쪽에 남는다 — 허브는 지우지 말고 사용 중지로 둔다),
  비운 설명, 관계에 붙은 속성, 파일 칸. 경고로 말한다.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects import aliases
from app.modules.objects.bulk import MAX_ROWS, MULTI_SEP
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.objects.services import properties_of
from app.modules.ontology import importer
from app.modules.ontology.models import NavGroup, ObjectType, PropertyDef
from app.shared.errors import Conflict, NotFound, code

FORMAT = "sp-bundle/1"


def export_group(db: Session, group_slug: str) -> dict[str, Any]:
    group = db.scalar(select(NavGroup).where(NavGroup.slug == group_slug))
    if group is None:
        raise NotFound(code("BUNDLES", 20), f"사이드바 묶음을 찾을 수 없습니다: {group_slug}")
    types = list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.nav_group_id == group.id, ObjectType.kind_class != "system")
            .order_by(ObjectType.sort_order, ObjectType.slug)
        )
    )
    if not types:
        raise Conflict(code("BUNDLES", 21), f"{group.label} 묶음에 내보낼 타입이 없습니다.")
    slugs = {one.slug for one in types}
    defs_of = {
        one.slug: [d for d in properties_of(db, one.id) if d.data_type != "file"]
        for one in types
    }
    outside = sorted(
        f"{one.slug}.{d.key} → {d.ref_type_slug}"
        for one in types
        for d in defs_of[one.slug]
        if d.data_type == "object_ref" and d.ref_type_slug not in slugs
    )
    if outside:
        # 받는 쪽에 그 타입이 없으면 참조가 안 풀려 묶음 전체가 막힌다 — 보내기 전에 말한다.
        raise Conflict(
            code("BUNDLES", 22),
            "묶음 밖을 가리키는 참조 칸이 있어 받는 쪽에서 풀리지 않습니다: "
            + ", ".join(outside),
        )

    schema = importer.capture(db)
    relation_types = [
        one
        for one in schema["relation_types"]
        if one.get("src_type_slugs")
        and one.get("dst_type_slugs")
        and set(one["src_type_slugs"]) <= slugs
        and set(one["dst_type_slugs"]) <= slugs
    ]
    ontology = {
        "groups": [one for one in schema["groups"] if one["slug"] == group_slug],
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
    human = aliases.human_of(db, [row.id for row in rows])
    warnings: list[str] = []
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
                objects.append({"type_slug": one.slug, "workspace_slug": None, "rows": chunk})

    type_by_object = {row.id: type_of[row.type_id].slug for row in rows}
    relations, with_properties = _relations(
        db, [one["slug"] for one in relation_types], type_by_object, names
    )
    if with_properties:
        warnings.append(
            f"관계에 붙은 속성 {with_properties}줄은 가지 않는다 — "
            "관계 행 가져오기가 속성을 받지 않는다"
        )

    return {
        "format": FORMAT,
        "group": group_slug,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ontology": ontology,
        "objects": objects,
        "relations": relations,
        "counts": {
            "types": len(types),
            "relation_types": len(relation_types),
            "objects": len(rows),
            "relations": sum(len(one["rows"]) for one in relations),
        },
        "warnings": warnings,
    }


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
    human: list[str],
) -> dict[str, Any]:
    out: dict[str, Any] = {"key": row.key or None, "label": row.label, "status": row.status}
    if row.description:
        out["description"] = row.description
    if row.valid_from_year is not None:
        out["valid_from_year"] = row.valid_from_year
    if row.valid_to_year is not None:
        out["valid_to_year"] = row.valid_to_year
    if human:
        out["aliases"] = MULTI_SEP.join(human)
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
) -> tuple[list[dict[str, Any]], int]:
    """내보내는 관계 종류의 줄 — 출발 타입마다. 관계에 붙은 속성은 몇 줄인지만 센다."""
    if not kinds or not names:
        return [], 0
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
    with_properties = 0
    for edge in edges:
        if edge.properties:
            with_properties += 1
        rows_by_type.setdefault(type_by_object[edge.src_object_id], []).append(
            {
                "src": names[edge.src_object_id],
                "relation": edge.relation,
                "dst": names[edge.dst_object_id],
                "evidence_note": edge.evidence_note or "",
            }
        )
    out: list[dict[str, Any]] = []
    for slug, rows in rows_by_type.items():
        for at in range(0, len(rows), MAX_ROWS):
            out.append({"type_slug": slug, "rows": rows[at : at + MAX_ROWS]})
    return out, with_properties
