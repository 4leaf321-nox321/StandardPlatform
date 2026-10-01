"""종류 변경 — 속성 종류를 바꾸면서 **저장된 값도 새 종류로** 변환한다(ADR 0007).

규칙(값 하나를 어떻게 읽나)은 `conversion.py` 가 쥐고, 여기는 그 규칙을 **타입의 객체들에**
건다: 무엇이 바뀌고 무엇이 안 되는지 세고(`plan`), 안 되는 것이 없으면 값 · 정의를 함께
고친다(`apply`). 화면 · 정의 가져오기 · 허브 묶음 · 스냅샷 복원이 **이 두 함수를** 부른다 —
두 벌이면 한쪽만 고쳐진다.

    Target    한 타입의 한 속성 — 바뀌기 전 종류와 바뀐 뒤의 정의(저장하지 않은 사본)
    plan      건수 · 변환할 수 없는 값(값마다 건수 · 견본) · 경고 · 오류 — 아무것도 안 바꾼다
    apply     계획을 다시 세워 오류가 없으면 값 · 객체 이력 · 정의 · 롤업 · 판(updated_at) ·
              정의 이력

**변환할 수 없는 값이 하나라도 있으면 적용하지 않는다** — 대체 값(`mapping`)으로 값마다 정해야
한다. 예외는 허브가 관리하는 타입(`clear_failures`): 그 묶음으로 바뀔 때만 비우고 경고한다 —
쌍둥이는 그 객체를 못 고치고, 허브가 변환한 값은 같은 묶음의 객체 단계가 다시 보낸다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import conversion, interfaces, views
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit

#: 화면과 같은 이름 — 문구가 화면의 종류 이름과 같아야 사람이 같은 것으로 읽는다.
KIND_LABELS = {
    "text": "글 (한 줄)",
    "text_long": "글 (여러 줄)",
    "number": "숫자",
    "date": "날짜",
    "datetime": "날짜와 시각",
    "bool": "예/아니오",
    "enum": "선택",
    "url": "주소",
    "object_ref": "객체 참조",
    "file": "파일",
}

#: 「유일」 을 둘 수 있는 종류 — 화면의 속성 창과 같다.
UNIQUEABLE = ("text", "number", "url", "date", "datetime")

#: 변환할 수 없는 값은 이만큼까지(건수가 많은 것부터) — 더 있으면 수만 말한다.
FAILURE_CAP = 300
#: 값마다 견본 객체 수.
SAMPLES = 3
#: 적용할 때 한 번에 읽어 고치는 행 수.
CHUNK = 500
#: 정의 이력에 남기는 대체 값 수.
MAPPED_IN_AUDIT = 50

_LOSSY = {
    "time": "시각을 버리고 날짜만 남기는 값",
    "subsecond": "초 아래를 버리는 값",
    "precision": "화면에서 끝자리가 깎일 수 있는 큰 정수",
    "duplicate": "변환으로 겹쳐 하나로 친 원소",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


@dataclass
class Target:
    """한 타입의 한 속성 — **바뀌기 전 종류**와 **바뀐 뒤의 정의**(저장하지 않은 사본).

    가져오기는 계획을 세운 뒤 정의 행을 먼저 고치고 값을 바꾼다 — 그때 행은 이미 새 종류라,
    바뀌기 전 종류를 여기 들고 있어야 한다.
    """

    type_id: uuid.UUID
    type_slug: str
    type_label: str
    key_scope: str
    key: str
    label: str
    before: str
    after: PropertyDef
    via: str = ""
    """인터페이스를 따라 바뀌면 그 인터페이스."""
    clear_failures: bool = False
    """허브가 관리하는 타입이 그 허브의 묶음으로 바뀔 때 — 변환할 수 없는 값을 비운다."""

    @property
    def name(self) -> str:
        return f"{self.type_slug}.{self.key}"


@dataclass
class Sample:
    type_slug: str
    object_id: uuid.UUID | None
    label: str


@dataclass
class ValueRow:
    value: str
    count: int
    reason: str = ""
    to: str | None = None
    """대체 값 — `None` 이면 값 삭제(대체 값 목록에서만 뜻이 있다)."""
    samples: list[Sample] = field(default_factory=list)


@dataclass
class TypeCount:
    type_slug: str
    type_label: str
    key: str
    via: str
    with_value: int = 0
    converted: int = 0
    unchanged: int = 0
    cleared: int = 0


@dataclass
class RetypePlan:
    counts: list[TypeCount] = field(default_factory=list)
    failures: list[ValueRow] = field(default_factory=list)
    failures_total: int = 0
    """변환할 수 없는 값의 **종류 수**(목록은 `FAILURE_CAP` 까지)."""
    mapped: list[ValueRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    defaults: dict[tuple[uuid.UUID, str], Any] = field(default_factory=dict)
    """(타입, 키) → 바뀐 기본값. 적용이 정의에 적는다."""


# --- 대상 -----------------------------------------------------------------------


def unsupported(before: str, after: str) -> str | None:
    """종류 변경이 안 되는 쌍이면 그 까닭 — 되면 None."""
    if before == after:
        return f"이미 {kind_label(after)} 입니다."
    if before == "enum" and after == "object_ref":
        return "선택 → 객체 참조는 코드표 승격으로 합니다(속성 창의 「코드표로 승격」)."
    if before == "object_ref":
        return (
            "객체 참조의 종류는 바꾸지 않습니다 — 저장값이 객체 id 라서 글로 읽어도 뜻이 "
            "없습니다"
            "(코드표 승격의 반대는 하지 않습니다)."
        )
    if "file" in (before, after):
        return "파일 속성은 값이 첨부라 종류를 바꾸지 않습니다."
    if after not in conversion.SUPPORTED or before not in conversion.SUPPORTED:
        names = " · ".join(kind_label(one) for one in conversion.SUPPORTED)
        return f"종류 변경은 {names} 사이에서만 합니다."
    return None


def target_def(found: PropertyDef, wanted: Mapping[str, Any]) -> tuple[PropertyDef, list[str]]:
    """바뀐 뒤의 정의 — 지금 정의에 **보낸 칸만** 덮고 모양을 정규화한 사본(저장하지 않는다).

    다른 종류에서 남은 칸(패턴 · 최소 · 고를 값)은 지운다 — 남기면 되돌아올 때 조용히 살아난다.
    """
    shape = interfaces.shape_of(wanted, interfaces.shape_of(found))
    notes: list[str] = []
    unique = bool(wanted.get("unique", found.unique))
    if unique and (shape.data_type not in UNIQUEABLE or shape.multi):
        notes.append(
            f"속성 {found.key}: {kind_label(shape.data_type)} 은(는) 유일을 두지 않아 "
            "유일을 끕니다."
        )
        unique = False
    after = PropertyDef(
        owner_kind=found.owner_kind,
        owner_id=found.owner_id,
        key=found.key,
        label=str(wanted.get("label") or found.label),
        unique=unique,
        default_value=wanted.get("default_value", found.default_value),
        **shape.written(),
    )
    return after, notes


def target_for(
    owner: ObjectType,
    found: PropertyDef,
    wanted: Mapping[str, Any],
    *,
    via: str = "",
    clear_failures: bool = False,
) -> tuple[Target, list[str]]:
    after, notes = target_def(found, wanted)
    return (
        Target(
            type_id=owner.id,
            type_slug=owner.slug,
            type_label=owner.label,
            key_scope=owner.key_scope,
            key=found.key,
            label=after.label,
            before=found.data_type,
            after=after,
            via=via,
            clear_failures=clear_failures,
        ),
        notes,
    )


def targets_from_bindings(
    db: Session,
    bindings: Iterable[interfaces.Binding],
    *,
    skip: set[str] | None = None,
    source: str = "",
) -> tuple[list[Target], list[str]]:
    """인터페이스를 따라 **종류가 바뀌는** 구현 타입의 속성 — 파일에 없던 타입도 여기서
    걸린다."""
    out: list[Target] = []
    notes: list[str] = []
    for one in bindings:
        if one.action != "sync" or "data_type" not in one.changed or one.shape is None:
            continue
        name = f"{one.type_slug}.{one.key}"
        if skip and name in skip:
            continue
        owner = db.scalar(select(ObjectType).where(ObjectType.slug == one.type_slug))
        if owner is None:
            continue
        found = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == owner.id,
                PropertyDef.key == one.key,
            )
        )
        if found is None:
            continue
        target, more = target_for(
            owner,
            found,
            one.shape.written(),
            via=one.interface,
            clear_failures=bool(source) and owner.managed_by == source,
        )
        out.append(target)
        notes.extend(more)
    return out, notes


# --- 계획 -----------------------------------------------------------------------


def _rows(db: Session, target: Target) -> list[tuple[uuid.UUID, str, uuid.UUID | None, Any]]:
    """그 키에 값이 있는 살아 있는 객체 — 계획은 그 칸만 읽는다(행 전체를 안 싣는다)."""
    return [
        (row[0], row[1], row[2], row[3])
        for row in db.execute(
            select(
                ObjectInstance.id,
                ObjectInstance.label,
                ObjectInstance.owner_workspace_id,
                ObjectInstance.properties[target.key],
            ).where(
                ObjectInstance.type_id == target.type_id,
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.properties.has_key(target.key),
            )
        )
    ]


def _converted(
    target: Target, raw: Any, mapping: Mapping[str, str | None] | None
) -> conversion.Converted:
    """값 하나 — 허브가 관리하는 타입이면 변환할 수 없는 원소를 지운다(경고는 계획이 낸다)."""
    out = conversion.convert_stored(target.after, raw, mapping)
    if out.failures and target.clear_failures:
        dropped = {**(mapping or {}), **{key: None for key, _ in out.failures}}
        out = conversion.convert_stored(target.after, raw, dropped)
    return out


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def plan(
    db: Session,
    targets: list[Target],
    mapping: Mapping[str, str | None] | None = None,
) -> RetypePlan:
    """무엇이 변환되고 무엇이 안 되는지 — **아무것도 안 바꾼다.**"""
    out = RetypePlan()
    failures: dict[str, ValueRow] = {}
    mapped: dict[str, ValueRow] = {}
    used: set[str] = set()

    for target in targets:
        counts = TypeCount(target.type_slug, target.type_label, target.key, target.via)
        lossy: dict[str, int] = {}
        mine: dict[str, ValueRow] = {}
        before_groups: dict[tuple[Any, str], list[str]] = {}
        after_groups: dict[tuple[Any, str], list[str]] = {}
        for object_id, label, workspace_id, raw in _rows(db, target):
            counts.with_value += 1
            result = conversion.convert_stored(target.after, raw, mapping)
            for name in result.lossy:
                lossy[name] = lossy.get(name, 0) + 1
            for key in result.mapped:
                used.add(key)
                row = mapped.setdefault(key, ValueRow(key, 0, to=(mapping or {}).get(key)))
                row.count += 1
            if result.failures:
                for key, reason in result.failures:
                    row = mine.setdefault(key, ValueRow(key, 0, reason))
                    row.count += 1
                    if len(row.samples) < SAMPLES:
                        row.samples.append(Sample(target.type_slug, object_id, label))
                if target.clear_failures:
                    counts.cleared += 1
                continue
            if result.remove:
                counts.cleared += 1
            elif result.changed:
                counts.converted += 1
            else:
                counts.unchanged += 1
            if target.after.unique and not target.after.multi and not result.remove:
                scope = workspace_id if target.key_scope == "workspace" else None
                before_groups.setdefault((scope, _json(raw)), []).append(label)
                after_groups.setdefault((scope, _json(result.value)), []).append(label)

        # 기본값도 같은 길 — 안 되면 견본 「(기본값)」 으로 같은 표에 선다.
        default = target.after.default_value
        if default is not None:
            result = conversion.convert_stored(target.after, default, mapping)
            if result.failures:
                for key, reason in result.failures:
                    row = mine.setdefault(key, ValueRow(key, 0, reason))
                    row.count += 1
                    row.samples.append(Sample(target.type_slug, None, "(기본값)"))
            else:
                for key in result.mapped:
                    used.add(key)
                out.defaults[(target.type_id, target.key)] = (
                    None if result.remove else result.value
                )

        name = f"속성 {target.name}"
        if mine:
            listed = ", ".join(
                f"「{row.value}」({row.count})"
                for row in sorted(mine.values(), key=lambda one: -one.count)[:5]
            )
            total = sum(row.count for row in mine.values())
            if target.clear_failures:
                out.warnings.append(
                    f"{name}: 허브가 관리하는 타입이라 "
                    f"{kind_label(target.after.data_type)}(으)로 변환할 수 없는 값 "
                    f"{total}건을 비웁니다 — {listed}. 허브의 값이 같은 묶음의 객체로 다시 "
                    "옵니다."
                )
            else:
                out.errors.append(
                    f"{name}: {kind_label(target.before)} → "
                    f"{kind_label(target.after.data_type)}(으)로 변환할 수 없는 값이 "
                    f"{len(mine)}종 {total}건 있습니다 — {listed}."
                )
            for key, row in mine.items():
                shared = failures.setdefault(key, ValueRow(key, 0, row.reason))
                shared.count += row.count
                for sample in row.samples:
                    if len(shared.samples) < SAMPLES:
                        shared.samples.append(sample)

        for kind, count in sorted(lossy.items()):
            out.warnings.append(f"{name}: {_LOSSY.get(kind, kind)} {count}개.")
        if counts.cleared and target.after.required and not target.clear_failures:
            out.warnings.append(
                f"{name}: 필수 속성인데 값을 비우는 객체가 {counts.cleared}개입니다 — "
                "그대로 두지만, 그 객체의 속성을 수정할 때 걸립니다."
            )
        if after_groups:
            fresh = [
                (value, labels)
                for (scope, value), labels in after_groups.items()
                if len(labels) > 1
                and not any(
                    len(old) > 1 and set(labels) <= set(old) for old in before_groups.values()
                )
            ]
            for value, labels in fresh[:5]:
                out.errors.append(
                    f"{name}: 변환하면 유일해야 하는 값 {value} 이(가) 겹칩니다 — "
                    f"{', '.join(labels[:5])}. 대체 값으로 갈라 주세요."
                )
            if any(len(labels) > 1 for labels in before_groups.values()):
                out.warnings.append(f"{name}: 이미 겹쳐 있던 유일 값은 그대로 둡니다.")
        if target.before == "number" and target.after.data_type != "number":
            owner = db.get(ObjectType, target.type_id)
            rollups = (owner.list_view or {}).get("rollups") if owner is not None else None
            if any(
                isinstance(one, dict) and one.get("property") == target.key
                for one in rollups or []
            ):
                out.warnings.append(
                    f"{name}: 목록의 롤업이 이 속성을 모읍니다 — 숫자가 아니게 되므로 "
                    "걷어냅니다."
                )
        out.counts.append(counts)

    ordered = sorted(failures.values(), key=lambda one: (-one.count, one.value))
    out.failures_total = len(ordered)
    out.failures = ordered[:FAILURE_CAP]
    out.mapped = sorted(mapped.values(), key=lambda one: (-one.count, one.value))
    unused = sorted(set(mapping or {}) - used - set(failures))
    if unused:
        shown = ", ".join(f"「{one}」" for one in unused[:10])
        out.warnings.append(f"쓰이지 않은 대체 값 {len(unused)}개 — {shown}.")
    return out


# --- 적용 -----------------------------------------------------------------------


def apply(
    db: Session,
    user: User | None,
    targets: list[Target],
    mapping: Mapping[str, str | None] | None = None,
    *,
    reason: str = "",
) -> RetypePlan:
    """계획을 다시 세워 오류가 없으면 **값과 정의를 함께** 고친다. 커밋하지 않는다.

    객체마다 이력을 남긴다(`object.update`) — 이력 화면이 그것으로 그때 값을 되짚는다. 묶음
    일지에는 안 적는다: 묶음 되돌리기는 정의를 안 되돌리므로, 옛 종류의 값을 새 정의에 다시
    넣게 된다.
    """
    planned = plan(db, targets, mapping)
    if planned.errors:
        return planned
    now = datetime.now(UTC)
    for target in targets:
        owner = db.get(ObjectType, target.type_id)
        if owner is None:  # pragma: no cover - 계획과 같은 트랜잭션이다
            continue
        why = (
            f"속성 종류 변경: 「{target.label}」 {kind_label(target.before)} → "
            f"{kind_label(target.after.data_type)}"
        )
        if target.via:
            why += f" — 인터페이스 {target.via}"
        if reason:
            why += f" — {reason}"
        converted = cleared = 0
        last: uuid.UUID | None = None
        while True:
            stmt = (
                select(ObjectInstance)
                .where(
                    ObjectInstance.type_id == target.type_id,
                    ObjectInstance.deleted_at.is_(None),
                    ObjectInstance.properties.has_key(target.key),
                )
                .order_by(ObjectInstance.id)
                .limit(CHUNK)
                .with_for_update()
            )
            if last is not None:
                stmt = stmt.where(ObjectInstance.id > last)
            rows = list(db.scalars(stmt))
            if not rows:
                break
            for row in rows:
                result = _converted(target, (row.properties or {}).get(target.key), mapping)
                if result.failures or not result.changed:
                    continue
                before = dict(row.properties or {})
                after = dict(before)
                if result.remove:
                    after.pop(target.key, None)
                    cleared += 1
                else:
                    after[target.key] = result.value
                    converted += 1
                row.properties = after
                audit.record(
                    db,
                    action="object.update",
                    actor=user,
                    target_table="objects",
                    target_id=row.id,
                    target_label=f"{owner.slug}:{row.label}",
                    workspace_id=row.owner_workspace_id,
                    changes=audit.diff({"properties": before}, {"properties": after}),
                    reason=why,
                )
            db.flush()
            last = rows[-1].id

        definition = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == target.type_id,
                PropertyDef.key == target.key,
            )
        )
        if definition is None:  # pragma: no cover - 계획과 같은 트랜잭션이다
            continue
        for name, value in interfaces.shape_of(target.after).written().items():
            setattr(definition, name, value)
        definition.unique = bool(target.after.unique)
        definition.default_value = planned.defaults.get((target.type_id, target.key))
        if target.before == "number" and target.after.data_type != "number":
            owner.list_view = views.prune_rollups(owner.list_view or {}, target.key)
        # 값이 하나도 없어도 정의가 바뀌었다 — RDF 판이 타입의 시각으로 안다.
        owner.updated_at = now
        audit.record(
            db,
            action="ontology.property.retype",
            actor=user,
            target_table="property_defs",
            target_id=definition.id,
            target_label=target.name,
            changes={
                "data_type": {"before": target.before, "after": target.after.data_type},
                "converted": converted,
                "cleared": cleared,
                "mapped": dict(list((mapping or {}).items())[:MAPPED_IN_AUDIT]),
                **({"via": target.via} if target.via else {}),
            },
            reason=reason or None,
        )
    db.flush()
    return planned


# --- 딸린 것 --------------------------------------------------------------------


def downstream(db: Session, targets: list[Target]) -> list[str]:
    """이 변경에 **딸려 깨지는 것** — 저장된 뷰 · 홈 위젯, 데이터 소스, 수신 시스템. 고치지
    않고 말한다(뷰는 사람의 것이고, 데이터 소스 · 수신 시스템은 바깥의 일이다)."""
    out = _views_breaking(db, targets)
    out.extend(_sources_touching(db, targets))
    for target in targets:
        owner = db.get(ObjectType, target.type_id)
        if owner is not None and owner.core:
            out.append(
                f"속성 {target.name}: 공개 타입이라 수신 시스템이 이 속성의 종류를 다시 맞출 "
                "때까지 그쪽에서 행 오류가 날 수 있습니다."
            )
    return out


def _sources_touching(db: Session, targets: list[Target]) -> list[str]:
    from app.modules.datasources.models import DataSource

    by_type: dict[uuid.UUID, list[Target]] = {}
    for target in targets:
        by_type.setdefault(target.type_id, []).append(target)
    out: list[str] = []
    for source in db.scalars(select(DataSource).where(DataSource.type_id.in_(list(by_type)))):
        for column in (source.mapping or {}).get("columns") or []:
            if not isinstance(column, dict):
                continue
            for target in by_type[source.type_id]:
                if column.get("target") != f"properties.{target.key}":
                    continue
                values = (
                    " — 열 값 대응(values)도 새 종류에 맞는지 보세요"
                    if column.get("values")
                    else ""
                )
                out.append(
                    f"데이터 소스 「{source.name}」 이 {target.name} 에 넣습니다 — 다음 "
                    f"동기화부터 {kind_label(target.after.data_type)}(으)로 읽습니다{values}."
                )
    return out


def _view_error(db: Session, view: Any) -> str | None:
    """저장된 뷰를 지금 정의로 열면 나는 오류 — 없으면 None. 저장할 때의 검사와 같은 함수다."""
    from app.modules.objects import conditions, paths, summary
    from app.modules.objects.services import properties_of
    from app.shared.errors import AppError

    owner = db.get(ObjectType, view.type_id)
    if owner is None:
        return None
    defs = properties_of(db, owner.id)
    resolver = paths.Resolver(db, owner)
    asked = [
        conditions.Condition(
            str(one.get("field", "")), str(one.get("op", "")), str(one.get("value", ""))
        )
        for one in (view.query or {}).get("conditions") or []
        if isinstance(one, dict)
    ]
    shape = view.summary or {}
    try:
        conditions.apply(select(ObjectInstance), defs, asked, resolver)
        if shape.get("group_by"):
            summary.check_group(defs, str(shape["group_by"]), resolver)
            if shape.get("split_by"):
                summary.check_group(defs, str(shape["split_by"]), resolver)
            summary.check_metric(
                defs, str(shape.get("metric") or "count"), shape.get("metric_field")
            )
    except AppError as caught:
        return caught.message
    return None


def _views_breaking(db: Session, targets: list[Target]) -> list[str]:
    """저장된 뷰 · 홈 위젯 가운데 **이 변경으로 새로** 깨지는 것.

    손으로 견주지 않고 **흉내 낸다** — 세이브포인트 안에서 새 정의를 적고, 저장할 때와 같은
    검사를 다시 돌린 뒤 되돌린다. 칸은 이어진 칸 주소(`ref.개발사.국가`)일 수도 있어서, 손으로
    견주면 다른 타입의 뷰를 놓친다. 원래 깨져 있던 뷰는 이 변경 탓이 아니라 말하지 않는다.
    """
    from app.modules.objects.models import SavedView

    saved = list(db.scalars(select(SavedView)))
    if not saved:
        return []
    before = {one.id: _view_error(db, one) for one in saved}
    savepoint = db.begin_nested()
    try:
        for target in targets:
            definition = db.scalar(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type",
                    PropertyDef.owner_id == target.type_id,
                    PropertyDef.key == target.key,
                )
            )
            if definition is None:
                continue
            for name, value in interfaces.shape_of(target.after).written().items():
                setattr(definition, name, value)
        db.flush()
        after = {one.id: _view_error(db, one) for one in saved}
    finally:
        savepoint.rollback()
    broken = [one for one in saved if before[one.id] is None and after[one.id] is not None]
    out = [
        f"저장된 뷰 「{one.name}」{'(홈 게시)' if one.home_order is not None else ''} 은(는) "
        f"이 변경 뒤 열면 오류가 납니다 — {after[one.id]}"
        for one in broken[:10]
    ]
    if len(broken) > 10:
        out.append(f"그 밖에 저장된 뷰 {len(broken) - 10}개도 이 변경 뒤 열면 오류가 납니다.")
    return out
