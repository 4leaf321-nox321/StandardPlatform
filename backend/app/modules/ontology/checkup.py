"""정의 점검 — **지우거나 묶을 것**을 찾는다(ADR 0092).

AI 가 넣은 정의는 쌓이기만 하고 줄지 않는다. 「어디부터 채우나」(`objects/fill.py`)는 빈 칸을
**채울 곳**으로 줄 세운다 — 그것으로는 「이 칸은 아무도 안 채우고 아무 데도 안 쓴다, 지워도
되나」 · 「같은 칸이 세 타입에 따로 있다」 를 말하지 못한다. 여기서는 그것을 센다. **아무것도
안 바꾼다** — 줄마다 다음 걸음(삭제 미리 보기 · 인터페이스 구현 미리 보기 · 종류 변경 · 정의
가져오기)을 적고, 판단은 사람이 한다.

## 세는 것

    unused_field         객체가 있는데 아무도 안 채우고(0건) 아무 데도 안 쓰는 칸
    interface_candidate  같은 키 · 같은 모양의 칸이 두 타입 넘게 따로 있다
    field_mismatch       같은 키인데 타입마다 종류 · 단위가 다르다
    unused_option        객체가 넉넉한데 아무도 고르지 않은 선택 값
    empty_type           만든 지 오래됐는데 객체가 하나도 없는 타입
    unused_relation      만든 지 오래됐는데 맺힌 관계가 하나도 없는 관계 종류
    undescribed_core     외부 공개 타입인데 설명이 없다

「쓰는 곳」 은 채울 곳의 것(필수 · 지표 · 뷰 · 코어 공개)에 데이터 소스의 칸 대응 · 앞머리 ·
개정 칸 · 공통 속성을 더한다 — 그런 칸은 비어 있어도 지울 후보가 아니다.

## 큰 타입

칸의 채움은 채울 곳과 같은 셈을 쓴다 — 5만 건이 넘는 타입은 2만 건 표본에서 센 어림이다
(`estimated` — 표본에서 0건이어도 밖에 있을 수 있다). 선택 값은 표본으로 「없음」 을 말할 수
없으니 5만 건 이하 타입만 센다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.datasources.models import DataSource
from app.modules.objects import fill, system
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectRelation
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.retype import KIND_LABELS as DATA_KINDS

#: 종류와 화면 이름 — 화면 · MCP 가 같은 말을 쓴다(용어.md 「정의 점검」).
KIND_LABELS = {
    "unused_field": "사용하지 않는 속성",
    "interface_candidate": "인터페이스 후보",
    "field_mismatch": "종류가 다른 같은 속성",
    "unused_option": "사용하지 않는 선택 값",
    "empty_type": "빈 타입",
    "unused_relation": "사용하지 않는 관계 종류",
    "undescribed_core": "설명 없는 공개 타입",
}
KINDS = tuple(KIND_LABELS)
#: 이보다 새로 만든 타입 · 관계 종류는 비어 있어도 후보가 아니다 — 아직 채우는 중일 수 있다.
GRACE_DAYS = 30
#: 선택 값은 객체가 이만큼 넘는 타입만 — 몇 건뿐이면 안 고른 값이 많은 것이 당연하다.
OPTION_MIN_OBJECTS = 20
#: 한 종류에 몇 줄까지 — 넘으면 수만 말한다(`counts` 는 전체).
PER_KIND = 100


@dataclass
class Item:
    kind: str
    slug: str
    """타입 · 관계 종류의 slug — 인터페이스 후보 · 모양이 다른 칸은 속성 키."""
    label: str
    reason: str
    """왜 후보인가 — 사람에게 그대로 보인다."""
    next: str
    """다음 걸음 — 무엇으로 확인하고 무엇으로 바꾸나(MCP 도구 이름으로)."""
    key: str | None = None
    """속성이면 그 키."""
    types: list[str] = field(default_factory=list)
    """여러 타입에 걸친 것(인터페이스 후보 · 모양이 다른 칸)이면 그 타입 slug 들."""
    count: int | None = None
    """그 줄의 수 — 객체 수 · 타입 수 · 안 고른 값 수."""
    estimated: bool = False
    """표본에서 센 어림(큰 타입)."""
    managed_by: str = ""
    """원천이 잠근 정의 — 그 원천이 지우거나(`source`) 잠금 해제 뒤에 사람이."""
    action: str = ""
    """화면이 바로 여는 미리 보기 — `delete_type` · `delete_property` · `delete_relation`
    · 빈 값."""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _lock_note(owner: str) -> str:
    return (
        f" 원천({owner})이 잠근 정의 — 그 원천이 source=「{owner}」 로 삭제하거나, 시스템 "
        "관리자가 「잠금 해제」 한 뒤 삭제합니다."
        if owner
        else ""
    )


def _shape(row: PropertyDef) -> str:
    kind = DATA_KINDS.get(row.data_type, row.data_type)
    return f"{kind} · {row.unit}" if row.data_type == "number" and row.unit else kind


def _source_targets(db: Session) -> dict[uuid.UUID, set[str]]:
    """타입 → 데이터 소스가 넣는 칸 키."""
    out: dict[uuid.UUID, set[str]] = defaultdict(set)
    for source in db.scalars(select(DataSource)):
        for one in (source.mapping or {}).get("columns") or []:
            target = one.get("target") if isinstance(one, dict) else None
            if isinstance(target, str) and target.startswith("properties."):
                out[source.type_id].add(target.removeprefix("properties."))
    return out


def _unused_fields(
    db: Session,
    user: User,
    types: dict[str, ObjectType],
    catalog: interfaces.Catalog,
    out: list[Item],
) -> fill.Report:
    counted = fill.report(db, user, all_fields=True)
    fed = _source_targets(db)
    for one in counted.types:
        owner = types.get(one.type_slug)
        if owner is None or one.objects <= 0:
            continue
        bound = interfaces.bound_keys(catalog, owner.slug)
        for prop in one.fields:
            if prop.filled > 0 or prop.uses:
                continue
            if (
                prop.key in bound
                or prop.key in fed.get(owner.id, set())
                or owner.label_prefix == f"properties.{prop.key}"
                or owner.revision_key == prop.key
            ):
                continue
            where = (
                f"표본 {one.sample_rows:,}건에서 0건(약 {one.objects:,}건 — 어림)"
                if one.estimated
                else f"객체 {one.objects:,}건 중 0건"
            )
            out.append(
                Item(
                    kind="unused_field",
                    slug=owner.slug,
                    key=prop.key,
                    label=f"{owner.label} · {prop.label}",
                    reason=(
                        f"값이 있는 객체가 없고({where}) 필수 · 지표 · 뷰 · 코어 공개 · "
                        "데이터 소스 어디에도 사용하지 않는 칸입니다. 삭제해도 저장값이 없어 "
                        "잃는 것이 없습니다." + _lock_note(owner.managed_by)
                    ),
                    next=(
                        f'ontology_delete("property", "{owner.slug}", key="{prop.key}") 로 '
                        "미리 보기 → 사람 확인 → apply=true"
                    ),
                    count=one.objects,
                    estimated=one.estimated,
                    managed_by=owner.managed_by,
                    action="delete_property",
                )
            )
    return counted


def _shared_fields(
    db: Session,
    types: dict[str, ObjectType],
    catalog: interfaces.Catalog,
    out: list[Item],
) -> None:
    """같은 키의 칸이 여러 타입에 **따로** 있다 — 공통 속성으로 묶인 것은 빼고 센다."""
    by_id = {one.id: one for one in types.values()}
    bound = {slug: interfaces.bound_keys(catalog, slug) for slug in types}
    by_key: dict[str, list[tuple[ObjectType, PropertyDef]]] = defaultdict(list)
    for row in db.scalars(select(PropertyDef).where(PropertyDef.owner_kind == "type")):
        owner = by_id.get(row.owner_id)
        if owner is None or row.key in bound[owner.slug]:
            continue
        by_key[row.key].append((owner, row))
    offered = {key: iface.slug for iface in catalog.interfaces.values() for key in iface.props}
    ranked = sorted(by_key.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    for key, rows in ranked:
        if len(rows) < 2:
            continue
        rows.sort(key=lambda pair: pair[0].slug)
        slugs = [owner.slug for owner, _ in rows]
        shapes = {
            (row.data_type, row.unit if row.data_type == "number" else "") for _, row in rows
        }
        listed = " · ".join(f"{owner.label}({_shape(row)})" for owner, row in rows[:6])
        more = f" 외 {len(rows) - 6}개" if len(rows) > 6 else ""
        labels = sorted({row.label for _, row in rows})
        name = f"{' · '.join(labels[:3])}({key})"
        if len(shapes) > 1:
            out.append(
                Item(
                    kind="field_mismatch",
                    slug=key,
                    key=key,
                    label=name,
                    reason=(
                        f"같은 키의 칸이 타입마다 종류 · 단위가 다릅니다 — {listed}{more}. "
                        "함께 세거나 인터페이스로 묶을 수 없습니다."
                    ),
                    next=(
                        "같은 뜻이면 ontology_retype 으로 한쪽의 종류를 맞춘다(미리 보기 → "
                        "사람이 대체 값 결정). 다른 뜻이면 키를 달리 둔다"
                    ),
                    types=slugs,
                    count=len(rows),
                )
            )
            continue
        via = offered.get(key)
        out.append(
            Item(
                kind="interface_candidate",
                slug=key,
                key=key,
                label=name,
                reason=(
                    f"같은 키 · 같은 종류의 칸이 {len(rows)}개 타입에 따로 있습니다 — "
                    f"{listed}{more}."
                    + (
                        f" 인터페이스 「{catalog.interfaces[via].label}」 이 이 칸을 정합니다."
                        if via
                        else " 같은 개념이면 인터페이스로 묶어 한 번에 정의 · 검색할 수 "
                        "있습니다."
                    )
                ),
                next=(
                    f"인터페이스 「{via}」 를 구현 — ontology_import 의 interface_slugs 에 "
                    "더한다(미리 보기 먼저)"
                    if via
                    else "같은 개념인지 사람에게 묻는다 → ontology_import 로 interfaces 에 이 "
                    "칸을 정하고 그 타입들의 interface_slugs 에 더한다(미리 보기 먼저)"
                ),
                types=slugs,
                count=len(rows),
            )
        )


def _option_counts(db: Session, type_id: uuid.UUID, keys: list[str]) -> dict[str, set[str]]:
    """선택 칸마다 **쓰인 값** — 한 값 · 여러 값(배열) 모두."""
    rows = db.execute(
        text(
            "SELECT e.key, CASE WHEN jsonb_typeof(e.value) = 'array' THEN a.v "
            "ELSE e.value #>> '{}' END AS v "
            f"FROM {ObjectInstance.__tablename__} o "
            "CROSS JOIN LATERAL jsonb_each(o.properties) e "
            "LEFT JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(e.value) = "
            "'array' THEN e.value ELSE '[]'::jsonb END) a(v) ON true "
            "WHERE o.type_id = :type_id AND o.deleted_at IS NULL AND e.key = ANY(:keys) "
            "GROUP BY 1, 2"
        ),
        {"type_id": type_id, "keys": keys},
    )
    out: dict[str, set[str]] = defaultdict(set)
    for key, value in rows:
        if value is not None:
            out[str(key)].add(str(value))
    return out


def _unused_options(
    db: Session,
    types: dict[str, ObjectType],
    counted: fill.Report,
    catalog: interfaces.Catalog,
    out: list[Item],
    notes: list[str],
) -> None:
    sizes = {one.type_slug: one for one in counted.types}
    enums: dict[uuid.UUID, list[PropertyDef]] = defaultdict(list)
    for row in db.scalars(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type", PropertyDef.data_type == "enum"
        )
    ):
        if row.enum_options:
            enums[row.owner_id].append(row)
    skipped: list[str] = []
    for owner in types.values():
        props = enums.get(owner.id)
        size = sizes.get(owner.slug)
        if not props or size is None or size.objects < OPTION_MIN_OBJECTS:
            continue
        if size.estimated:
            skipped.append(owner.label)
            continue
        used = _option_counts(db, owner.id, [one.key for one in props])
        bound = interfaces.bound_keys(catalog, owner.slug)
        for prop in props:
            options = list(prop.enum_options or [])
            unused = [one for one in options if one not in used.get(prop.key, set())]
            if not unused:
                continue
            shown = " · ".join(f"「{one}」" for one in unused[:8])
            more = f" 외 {len(unused) - 8}개" if len(unused) > 8 else ""
            where = (
                f"공통 속성(인터페이스 「{bound[prop.key]}」)이라 인터페이스에서 수정"
                if prop.key in bound
                else "ontology_import 로 그 칸의 enum_options 에서 제외"
            )
            out.append(
                Item(
                    kind="unused_option",
                    slug=owner.slug,
                    key=prop.key,
                    label=f"{owner.label} · {prop.label}",
                    reason=(
                        f"선택 값 {len(options)}개 중 {len(unused)}개를 선택한 객체가 없습니다"
                        f"(객체 {size.objects:,}건) — {shown}{more}."
                        + _lock_note(owner.managed_by)
                    ),
                    next=(
                        f"쓸 일이 없는 값이면 {where}(쓰는 객체가 없어 저장값은 잃지 않는다, "
                        "미리 보기 먼저)"
                    ),
                    count=len(unused),
                    managed_by=owner.managed_by,
                )
            )
    if skipped:
        notes.append(
            "선택 값은 5만 건이 넘는 타입은 세지 않았습니다(표본으로는 「아무도 안 고름」 을 "
            "말할 수 없습니다) — " + ", ".join(skipped) + "."
        )


def _empty_types(db: Session, types: dict[str, ObjectType], out: list[Item]) -> None:
    cut = datetime.now(UTC) - timedelta(days=GRACE_DAYS)
    fed = set(db.scalars(select(DataSource.type_id)))
    for owner in types.values():
        if owner.created_at > cut or owner.core or owner.id in fed:
            continue
        live = db.scalar(
            select(
                exists().where(
                    ObjectInstance.type_id == owner.id, ObjectInstance.deleted_at.is_(None)
                )
            )
        )
        if live:
            continue
        out.append(
            Item(
                kind="empty_type",
                slug=owner.slug,
                label=owner.label,
                reason=(
                    f"만든 지 {GRACE_DAYS}일이 넘었는데 객체가 하나도 없습니다"
                    f"({owner.created_at:%Y-%m-%d} 생성)." + _lock_note(owner.managed_by)
                ),
                next=(
                    f'ontology_delete("type", "{owner.slug}") 로 미리 보기 — 지운 객체만 '
                    "남았으면 purge_deleted(영구 삭제)를 사람에게 확인받는다"
                ),
                managed_by=owner.managed_by,
                action="delete_type",
            )
        )


def _unused_relations(db: Session, out: list[Item]) -> None:
    cut = datetime.now(UTC) - timedelta(days=GRACE_DAYS)
    for kind in db.scalars(select(RelationType).order_by(RelationType.label)):
        if kind.created_at > cut:
            continue
        used = db.scalar(
            select(exists().where(ObjectRelation.relation == kind.slug))
        ) or db.scalar(select(exists().where(ObjectLink.relation == kind.slug)))
        if used:
            continue
        out.append(
            Item(
                kind="unused_relation",
                slug=kind.slug,
                label=kind.label,
                reason=(
                    f"만든 지 {GRACE_DAYS}일이 넘었는데 맺힌 관계가 하나도 없습니다"
                    f"({kind.created_at:%Y-%m-%d} 생성)." + _lock_note(kind.managed_by)
                ),
                next=f'ontology_delete("relation_type", "{kind.slug}") 로 미리 보기',
                managed_by=kind.managed_by,
                action="delete_relation",
            )
        )


def _undescribed(types: dict[str, ObjectType], out: list[Item]) -> None:
    for owner in types.values():
        if owner.core and not (owner.description or "").strip():
            out.append(
                Item(
                    kind="undescribed_core",
                    slug=owner.slug,
                    label=owner.label,
                    reason=(
                        "외부에 공개 중인 타입인데 설명이 없습니다 — 받는 시스템 · 그쪽 AI 는 "
                        "이름만으로 뜻을 짐작합니다." + _lock_note(owner.managed_by)
                    ),
                    next=(
                        "무엇을 담는 타입인지 사람에게 묻고 ontology_import 로 description 을 "
                        "추가(미리 보기 먼저)"
                    ),
                    managed_by=owner.managed_by,
                )
            )


def run(db: Session, user: User, *, kind: str | None = None) -> Report:
    """정의를 점검한다 — 아무것도 안 바꾼다. `kind` 를 주면 그 종류만(셈도 그만큼 준다)."""
    report = Report()
    every = list(
        db.scalars(select(ObjectType).order_by(ObjectType.sort_order, ObjectType.label))
    )
    # 원 표를 비추는 타입(부서 · 계정)은 행이 없다 — 칸 · 객체로 견줄 것이 없다.
    types = {one.slug: one for one in every if not system.is_system(one)}
    active = {slug: one for slug, one in types.items() if one.is_active}
    catalog = interfaces.load(db)
    found: list[Item] = []
    wants = (lambda name: True) if kind is None else (lambda name: name == kind)

    counted: fill.Report | None = None
    if wants("unused_field") or wants("unused_option"):
        if wants("unused_field"):
            counted = _unused_fields(db, user, active, catalog, found)
        else:
            counted = fill.report(db, user)
        report.notes += [one for one in counted.notes if "표본" in one]
    if wants("interface_candidate") or wants("field_mismatch"):
        _shared_fields(db, active, catalog, found)
    if wants("unused_option") and counted is not None:
        _unused_options(db, active, counted, catalog, found, report.notes)
    if wants("empty_type"):
        _empty_types(db, types, found)
    if wants("unused_relation"):
        _unused_relations(db, found)
    if wants("undescribed_core"):
        _undescribed(types, found)

    found = [one for one in found if kind is None or one.kind == kind]
    for name in KINDS:
        mine = [one for one in found if one.kind == name]
        if not mine:
            continue
        report.counts[name] = len(mine)
        report.items += mine[:PER_KIND]
        if len(mine) > PER_KIND:
            report.notes.append(f"{name}: {len(mine)}건 중 앞 {PER_KIND}건만 실었습니다.")
    if any(one.managed_by for one in report.items):
        report.notes.append(
            "원천이 잠근 정의(managed_by)는 그 원천이 보내는 것이라, 비어 보여도 다음 적재가 "
            "채울 수 있습니다 — 그 원천에 먼저 확인합니다."
        )
    return report
