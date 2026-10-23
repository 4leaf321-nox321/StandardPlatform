"""장표 — 틀을 세워 보고, 장표를 그린다(ADR 0037).

**영역의 기록은 목록과 같은 길로 센다** — `objects/conditions.py` 의 거르기, `paths.Resolver`
의
걸음, 가시성(`visible_owner_clause`), 읽기 시간 상한(`querycap`). 따로 적으면 「장표에는 있는데
목록에는 없는」 기록이 생기고, 어느 쪽이 맞는지 아무도 모른다. 영역마다 「목록에서 보기」 가
같은
조건의 목록을 여는 것도 그래서다.

**고장 나도 장표는 선다.** 칸이 지워졌거나 조건이 이제 안 맞으면 그 영역만 까닭을 말하고
나머지는
그린다 — 틀을 고친 날 장표가 통째로 안 열리면 무엇이 틀렸는지 볼 자리조차 없다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import conditions, paths, system
from app.modules.objects.models import ObjectInstance
from app.modules.objects.scope import as_scope
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import InvalidValue
from app.modules.slides.schemas import (
    AreaIn,
    AreaOut,
    FieldOut,
    LinkItemOut,
    LinkOut,
    RecordOut,
    TemplateIn,
)
from app.shared import querycap
from app.shared.errors import AppError, NotFound, code
from app.shared.permissions import visible_owner_clause

#: 고정 칸 — 속성이 아니라 객체 자체의 것. 목록의 조건 고르개와 같은 이름이다.
FIXED = {"label": "이름", "key": "식별자"}

#: 영역의 차례 — 기본은 새것부터.
_ORDERS = {
    "newest": (ObjectInstance.created_at.desc(), ObjectInstance.id),
    "oldest": (ObjectInstance.created_at.asc(), ObjectInstance.id),
    "label": (ObjectInstance.label.asc(), ObjectInstance.id),
}


def type_by_slug(db: Session, slug: str, *, what: str) -> ObjectType:
    found = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if found is None:
        raise NotFound(code("SLIDES", 1), f"{what} 타입을 찾을 수 없습니다: {slug}")
    if found.kind_class == "system":
        raise InvalidValue(
            code("SLIDES", 2),
            f"{found.label}은(는) 원 표를 비추는 타입이라 장표에 사용할 수 없습니다.",
        )
    return found


# --- 틀 세워 보기 -------------------------------------------------------------


def validate(db: Session, payload: TemplateIn) -> ObjectType:
    """틀을 **저장하기 전에 세워 본다** — 격자 · 짝 · 타입 · 칸 · 조건. 틀린 것은 어느 영역의
    무엇인지 말하며 거절한다. 주제 타입을 돌려준다."""
    subject_type = type_by_slug(db, payload.subject_type_slug, what="주제")
    areas = {one.key: one for one in payload.areas}
    if len(areas) != len(payload.areas):
        raise InvalidValue(code("SLIDES", 3), "영역의 키가 겹칩니다 — 영역마다 달라야 합니다.")
    _check_grid(payload)
    for area in payload.areas:
        _check_area(db, area, areas, subject_type)
    return subject_type


def _check_grid(payload: TemplateIn) -> None:
    taken: dict[tuple[int, int], str] = {}
    for area in payload.areas:
        if area.row + area.row_span > payload.rows or area.col + area.col_span > payload.cols:
            raise InvalidValue(
                code("SLIDES", 4),
                f"영역 「{area.title}」 이 격자({payload.rows}행 {payload.cols}열) "
                "밖으로 나갑니다.",
            )
        for r in range(area.row, area.row + area.row_span):
            for c in range(area.col, area.col + area.col_span):
                if (r, c) in taken:
                    raise InvalidValue(
                        code("SLIDES", 5),
                        f"영역 「{area.title}」 과 「{taken[(r, c)]}」 이 "
                        f"{r + 1}행 {c + 1}열에서 겹칩니다.",
                    )
                taken[(r, c)] = area.title


def _check_area(
    db: Session, area: AreaIn, areas: dict[str, AreaIn], subject_type: ObjectType
) -> None:
    if area.kind == "text":
        return
    if area.pair_of is not None:
        primary = _check_pair(area, areas)
        defs = _defs_of(db, type_by_slug(db, primary.type_slug or "", what="기록"))
        _check_fields(area, defs)
        return
    if not area.type_slug:
        raise InvalidValue(
            code("SLIDES", 6), f"기록 영역 「{area.title}」 에 기록 타입이 없습니다."
        )
    record_type = type_by_slug(db, area.type_slug, what="기록")
    defs = _defs_of(db, record_type)
    _check_fields(area, defs)
    by_key = {one.key: one for one in defs}
    for key in area.ref_line:
        found = by_key.get(key)
        if found is None or found.data_type != "object_ref":
            raise InvalidValue(
                code("SLIDES", 7),
                f"영역 「{area.title}」 의 연결 항목 줄에 참조 칸이 아닌 것이 있습니다: {key}",
            )
    if area.fill == "condition":
        # 주소 · 칸 · 연산을 **실제로 세워 본다** — 아무 주제나 넣어 문장을 짓는다(돌리지
        # 않는다).
        try:
            _statement(db, None, record_type, area, uuid.uuid4())
        except AppError as error:
            raise InvalidValue(
                code("SLIDES", 8), f"영역 「{area.title}」 의 조건: {error.message}"
            ) from error


def _check_pair(area: AreaIn, areas: dict[str, AreaIn]) -> AreaIn:
    """짝 영역 — 짝은 기록 영역이고, 짝끼리 **같은 행 · 같은 높이로 맞붙어** 있어야 줄이
    맞는다."""
    primary = areas.get(area.pair_of or "")
    if primary is None or primary.key == area.key:
        raise InvalidValue(
            code("SLIDES", 9), f"영역 「{area.title}」 의 짝이 없습니다: {area.pair_of}"
        )
    if primary.kind != "records" or primary.pair_of is not None:
        raise InvalidValue(
            code("SLIDES", 9),
            f"영역 「{area.title}」 의 짝 「{primary.title}」 은 짝이 없는 기록 영역이어야 "
            "합니다.",
        )
    group = sorted(
        (
            one
            for one in areas.values()
            if one.key == primary.key or one.pair_of == primary.key
        ),
        key=lambda one: one.col,
    )
    for left, right in pairwise(group):
        if (
            right.row != left.row
            or right.row_span != left.row_span
            or right.col != left.col + left.col_span
        ):
            raise InvalidValue(
                code("SLIDES", 10),
                f"짝 영역 「{left.title}」 · 「{right.title}」 은 같은 행 · 같은 높이로 "
                "맞붙어 있어야 줄이 맞습니다.",
            )
    return primary


def _check_fields(area: AreaIn, defs: list[PropertyDef]) -> None:
    by_key = {one.key: one for one in defs}
    for key in area.fields:
        if key in FIXED:
            continue
        found = by_key.get(key)
        if found is None:
            raise InvalidValue(
                code("SLIDES", 11), f"영역 「{area.title}」 에 없는 칸이 있습니다: {key}"
            )
        if found.data_type == "file":
            raise InvalidValue(
                code("SLIDES", 12),
                f"영역 「{area.title}」 — 파일 칸({found.label})은 아직 장표에 "
                "표시하지 못합니다.",
            )


def _defs_of(db: Session, object_type: ObjectType) -> list[PropertyDef]:
    return as_scope(db, object_type).defs


# --- 그리기 -------------------------------------------------------------------


@dataclass
class _Rows:
    rows: list[ObjectInstance] = field(default_factory=list)
    total: int = 0
    problem: str | None = None
    list_params: dict[str, str] = field(default_factory=dict)


def render(
    db: Session,
    user: User,
    areas: Iterable[dict[str, Any]],
    subject: ObjectInstance | None,
    content: dict[str, Any],
) -> list[AreaOut]:
    """영역마다 그린다. 짝 영역은 짝의 기록을 같은 차례로 — 칸만 다르다."""
    querycap.cap(db)
    parsed = [AreaIn.model_validate(one) for one in areas]
    found: dict[str, _Rows] = {}
    for area in parsed:
        if area.kind == "records" and area.pair_of is None:
            found[area.key] = _rows_of(db, user, area, subject, content)
    by_key = {one.key: one for one in parsed}
    out: list[AreaOut] = []
    for area in parsed:
        if area.kind == "text":
            out.append(
                _shell(
                    area, None, [], text=str((content.get(area.key) or {}).get("text") or "")
                )
            )
            continue
        primary = by_key.get(area.pair_of or "") if area.pair_of else area
        rows = found.get(primary.key if primary else "", _Rows(problem="짝 영역이 없습니다."))
        type_slug = primary.type_slug if primary else None
        out.append(_records(db, user, area, type_slug, rows))
    return out


def _rows_of(
    db: Session,
    user: User,
    area: AreaIn,
    subject: ObjectInstance | None,
    content: dict[str, Any],
) -> _Rows:
    try:
        record_type = type_by_slug(db, area.type_slug or "", what="기록")
    except AppError as error:
        return _Rows(problem=error.message)
    order = _ORDERS[area.order]
    if area.fill == "pick":
        picked = _uuids((content.get(area.key) or {}).get("picks") or [])
        if not picked:
            return _Rows()
        stmt = _base(db, user, record_type).where(ObjectInstance.id.in_(picked))
        alive = {one.id: one for one in db.scalars(stmt)}
        ordered = [alive[one] for one in picked if one in alive]
        return _Rows(rows=ordered[: area.limit], total=len(ordered))
    if area.subject_field and subject is None:
        return _Rows(problem="장표의 주제가 없습니다 — 주제가 영구 삭제됐습니다.")
    try:
        stmt = _statement(db, user, record_type, area, subject.id if subject else None)
    except AppError as error:
        return _Rows(problem=error.message)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.order_by(*order).limit(area.limit)))
    return _Rows(rows=rows, total=int(total), list_params=_list_params(area, subject))


def _base(db: Session, user: User | None, record_type: ObjectType) -> Any:
    scope = as_scope(db, record_type)
    stmt = select(ObjectInstance).where(scope.clause(), ObjectInstance.deleted_at.is_(None))
    if user is not None:
        stmt = stmt.where(visible_owner_clause(user, ObjectInstance.owner_workspace_id))
    return stmt


def _statement(
    db: Session,
    user: User | None,
    record_type: ObjectType,
    area: AreaIn,
    subject_id: uuid.UUID | None,
) -> Any:
    """조건 영역의 문장 — 목록과 같은 조건(`conditions.apply`)에 「주제와 이어지는 칸」 하나를
    더한다."""
    asked = [
        conditions.Condition(field=one.field, op=one.op, value=one.value, exclude=one.exclude)
        for one in area.conditions
    ]
    if area.subject_field and subject_id is not None:
        asked.append(
            conditions.Condition(
                field=area.subject_field, op=area.subject_op, value=str(subject_id)
            )
        )
    scope = as_scope(db, record_type)
    return conditions.apply(
        _base(db, user, record_type),
        scope.defs,
        asked,
        paths.Resolver(db, scope, viewer=user),
    )


def _list_params(area: AreaIn, subject: ObjectInstance | None) -> dict[str, str]:
    """「목록에서 보기」 의 주소 — 같은 조건. 같은 칸 · 연산이 둘이면 마지막 것만 실린다(목록
    주소의
    한계 — 장표의 조건은 영역마다 짧다)."""
    params = {
        (f"nf{one.exclude}" if one.exclude else "f") + f".{one.field}.{one.op}": one.value
        for one in area.conditions
    }
    if area.subject_field and subject is not None:
        params[f"f.{area.subject_field}.{area.subject_op}"] = str(subject.id)
    return params


def _records(
    db: Session, user: User, area: AreaIn, type_slug: str | None, rows: _Rows
) -> AreaOut:
    record_type = (
        db.scalar(select(ObjectType).where(ObjectType.slug == type_slug))
        if type_slug
        else None
    )
    defs = _defs_of(db, record_type) if record_type is not None else []
    by_key = {one.key: one for one in defs}
    shown = [
        FieldOut(
            key=key, label=FIXED.get(key) or (by_key[key].label if key in by_key else key)
        )
        for key in area.fields
    ]
    ref_line = [by_key[key] for key in area.ref_line if key in by_key]
    wanted = [by_key[key] for key in (*area.fields, *area.ref_line) if key in by_key]
    labels = system.ref_labels(
        db, wanted, [one.properties or {} for one in rows.rows], viewer=user
    )
    records = [
        RecordOut(
            id=row.id,
            label=row.label,
            type_slug=type_slug or "",
            values={key: _text(row, key, by_key.get(key), labels) for key in area.fields},
            links=[_link(row, one, labels) for one in ref_line],
        )
        for row in rows.rows
    ]
    out = _shell(area, record_type, shown, records=records)
    out.total = rows.total
    out.problem = rows.problem
    out.list_params = rows.list_params if area.pair_of is None else {}
    out.type_slug = type_slug
    return out


def _shell(
    area: AreaIn,
    record_type: ObjectType | None,
    fields: list[FieldOut],
    *,
    text: str = "",
    records: list[RecordOut] | None = None,
) -> AreaOut:
    return AreaOut(
        key=area.key,
        title=area.title,
        row=area.row,
        col=area.col,
        row_span=area.row_span,
        col_span=area.col_span,
        kind=area.kind,
        fill=area.fill,
        pair_of=area.pair_of,
        type_slug=record_type.slug if record_type else None,
        type_label=record_type.label if record_type else None,
        fields=fields,
        text=text,
        records=records or [],
    )


def _text(
    row: ObjectInstance,
    key: str,
    definition: PropertyDef | None,
    labels: dict[uuid.UUID, str],
) -> str:
    """칸 하나를 글로 — 참조는 이름, 예/아니오는 말로, 여러 값은 「, 」 로."""
    if key == "label":
        return row.label
    if key == "key":
        return row.key or ""
    raw = (row.properties or {}).get(key)
    if raw is None or raw == "" or raw == []:
        return ""
    values = raw if isinstance(raw, list) else [raw]
    kind = definition.data_type if definition is not None else ""
    out: list[str] = []
    for value in values:
        if kind == "object_ref":
            target = _uuid(value)
            out.append(labels.get(target, str(value)) if target else str(value))
        elif kind == "bool":
            out.append("예" if value else "아니오")
        else:
            out.append(str(value))
    return ", ".join(out)


def _link(
    row: ObjectInstance, definition: PropertyDef, labels: dict[uuid.UUID, str]
) -> LinkOut:
    raw = (row.properties or {}).get(definition.key)
    values = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
    items = [
        LinkItemOut(
            id=str(value),
            label=labels.get(target, str(value)) if (target := _uuid(value)) else str(value),
            type_slug=definition.ref_type_slug,
        )
        for value in values
    ]
    return LinkOut(label=definition.label, items=items)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _uuids(values: Iterable[Any]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values:
        found = _uuid(value)
        if found is not None and found not in out:
            out.append(found)
    return out
