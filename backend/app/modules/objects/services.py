"""객체의 정합 — **DB 제약으로 못 거는 것들.**

`key` 의 유니크 범위가 타입마다 다르고(`key_scope`), 참조가 가리키는 객체는
JSONB 안에 있다. 둘 다 DB 가 안 잡아 주므로 여기서 잡는다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, Text, func, or_, select
from sqlalchemy.orm import Session

from app.modules.objects import system
from app.modules.objects.models import ObjectInstance, ObjectLink, ObjectYear
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import InvalidValue, object_ref_ids
from app.shared import extensions
from app.shared.errors import Conflict, code


def properties_of(db: Session, type_id: uuid.UUID) -> list[PropertyDef]:
    return list(
        db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "type", PropertyDef.owner_id == type_id)
            .order_by(PropertyDef.sort_order, PropertyDef.label)
        )
    )


def normalize_key(object_type: ObjectType, key: str | None) -> str | None:
    """타입의 `key_policy` 에 비추어 식별자를 받아들이거나 거절한다."""
    key = (key or "").strip() or None

    if object_type.key_policy == "none":
        if key is not None:
            raise InvalidValue(
                code("OBJECTS", 1),
                f"{object_type.label}은 식별자를 쓰지 않는 타입입니다. 이름만 넣으세요.",
            )
        return None

    if object_type.key_policy == "required" and key is None:
        raise InvalidValue(
            code("OBJECTS", 2), f"{object_type.label}은 식별자가 반드시 있어야 합니다."
        )
    return key


def require_key_free(
    db: Session,
    object_type: ObjectType,
    key: str | None,
    *,
    owner_workspace_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """같은 식별자가 이미 있는가.

    **범위가 타입마다 달라 DB 유니크로 못 건다.** `global` 은 전사에서 하나,
    `workspace` 는 부서 안에서 하나다. 외부 시스템과 맞물리는 축은 `global`
    이어야 한다 — 부서마다 같은 부품번호가 다른 것을 가리키면, 그 데이터로는
    아무 질문에도 답할 수 없다.
    """
    if key is None:
        return

    stmt = select(ObjectInstance.id).where(
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.key == key,
        ObjectInstance.deleted_at.is_(None),
    )
    if object_type.key_scope == "workspace":
        if owner_workspace_id is None:
            stmt = stmt.where(ObjectInstance.owner_workspace_id.is_(None))
        else:
            stmt = stmt.where(ObjectInstance.owner_workspace_id == owner_workspace_id)
    if exclude_id is not None:
        stmt = stmt.where(ObjectInstance.id != exclude_id)

    if db.scalar(stmt) is not None:
        where = "이 부서에" if object_type.key_scope == "workspace" else "이미"
        raise Conflict(
            code("OBJECTS", 3),
            f"같은 식별자가 {where} 있습니다: {key}. "
            "찾아서 고치는 편이 낫습니다 — 같은 것이 둘이 되면 둘 다 못 믿게 됩니다.",
        )


def require_refs_exist(db: Session, defs: list[PropertyDef], values: dict[str, Any]) -> None:
    """참조가 가리키는 객체가 실제로 있는가.

    **없는 것을 가리키는 참조를 저장하면 화면에 빈 칸으로 나오고**, 그것이
    「값이 없음」 인지 「가리키던 것이 사라짐」 인지 구별할 수 없다.
    """
    if not object_ref_ids(defs, values):
        return
    # 상대가 system 타입이면 원 표에서, 아니면 객체 표에서 — `system.py` 가 가른다.
    missing = system.missing_refs(db, defs, values)
    if missing:
        raise InvalidValue(
            code("OBJECTS", 4),
            f"가리키는 객체를 찾을 수 없습니다: {', '.join(missing)}",
        )


def require_unique_properties(
    db: Session,
    object_type: ObjectType,
    defs: list[PropertyDef],
    values: dict[str, Any],
    *,
    owner_workspace_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """`unique` 인 속성이 이미 쓰이고 있는가.

    **DB 유니크로 못 건다** — 값이 JSONB 안에 있고, 범위가 타입마다 다르다.
    범위는 `key_scope` 를 따른다: 두 벌의 규칙을 만들면 「식별자는 전사인데
    시리얼은 부서」 같은 상태가 생기고, 그것을 기억할 사람이 없다.

    **같은 것이 둘이 되면 둘 다 못 믿게 된다** — 어느 쪽이 맞는지 알 방법이 없다.
    """
    unique_keys = [d.key for d in defs if d.unique and d.key in values]
    if not unique_keys:
        return

    for key in unique_keys:
        value = values.get(key)
        if value is None or value == "" or value == []:
            continue
        stmt = select(ObjectInstance.id).where(
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.properties[key].astext == str(value),
        )
        if object_type.key_scope == "workspace":
            if owner_workspace_id is None:
                stmt = stmt.where(ObjectInstance.owner_workspace_id.is_(None))
            else:
                stmt = stmt.where(ObjectInstance.owner_workspace_id == owner_workspace_id)
        if exclude_id is not None:
            stmt = stmt.where(ObjectInstance.id != exclude_id)

        if db.scalar(stmt) is not None:
            label = next(d.label for d in defs if d.key == key)
            where = "이 부서에" if object_type.key_scope == "workspace" else "이미"
            raise Conflict(
                code("OBJECTS", 5),
                f"{label}에 같은 값이 {where} 있습니다: {value}. "
                "같은 것이 둘이 되면 둘 다 못 믿게 됩니다 — 찾아서 고치는 편이 낫습니다.",
            )


def apply_search(stmt: Select[Any], object_type: ObjectType, term: str) -> Select[Any]:
    """`list_view.search` 가 가리키는 자리들을 훑는다.

    안 정해 뒀으면 이름과 식별자를 본다 — **빈 결과보다 그럴듯한 기본이 낫다.**
    """
    view = object_type.list_view or {}
    fields = view.get("search") or ["label", "key"]
    pattern = f"%{term}%"

    clauses = []
    for field in fields:
        if field == "label":
            clauses.append(ObjectInstance.label.ilike(pattern))
        elif field == "key":
            clauses.append(ObjectInstance.key.ilike(pattern))
        elif field.startswith("properties."):
            key = field.split(".", 1)[1]
            clauses.append(ObjectInstance.properties[key].astext.ilike(pattern))
    if not clauses:
        return stmt
    return stmt.where(or_(*clauses))


def apply_property_filters(stmt: Select[Any], filters: dict[str, str]) -> Select[Any]:
    """`?p.<key>=<값>` 으로 온 거르기. **문자열 비교만 한다** — 범위 질의는
    아직 없다. 없는 것을 있는 척하지 않는다."""
    for key, value in filters.items():
        stmt = stmt.where(ObjectInstance.properties[key].astext == value)
    return stmt


def apply_sort(stmt: Select[Any], object_type: ObjectType) -> Select[Any]:
    """`list_view.sort` 대로. 안 정해 뒀으면 이름순."""
    view = object_type.list_view or {}
    sort = view.get("sort") or {}
    field = sort.get("field") or "label"
    descending = (sort.get("dir") or "asc") == "desc"

    if field == "label":
        column: Any = ObjectInstance.label
    elif field == "key":
        column = ObjectInstance.key
    elif field == "updated_at":
        column = ObjectInstance.updated_at
    elif field.startswith("properties."):
        column = ObjectInstance.properties[field.split(".", 1)[1]].astext
    else:
        column = ObjectInstance.label

    return stmt.order_by(column.desc() if descending else column.asc())


def count_of(db: Session, stmt: Select[Any]) -> int:
    """**total 을 함께 준다.** 없으면 화면이 「다음 쪽이 있는지」 를 알려고 한 건
    더 요청하는 편법을 쓰고, 그 편법은 화면마다 달라진다."""
    return int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


def workspace_reference(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceReference]:
    """부서 삭제 확인에 뜨는 줄.

    **안 걸면 부서를 지울 때 이 표가 목록에 안 나타나고**, 사람은 아무것도 안
    걸린 줄 안다. FK 가 RESTRICT 라 그때 서버가 500 을 낸다 — 화면은 지울 수
    있다고 말해 놓고서.

    지운 객체(`deleted_at`)도 센다. **행이 남아 있으면 FK 는 그대로 붙든다** —
    사람 눈에 안 보이는 것과 DB 가 놓아 주는 것은 다른 일이다.
    """
    count = (
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.owner_workspace_id == workspace_id)
        )
        or 0
    )
    # 온톨로지가 이 부서를 **가리키는** 것 — 관계 선(`object_links`)과 참조 칸. FK 가
    # 없어 DB 는 안 막지만, 지우면 「담당 부서」 가 빈 칸이 되고 그 이유는 안 뜬다.
    linked = (
        db.scalar(
            select(func.count())
            .select_from(ObjectLink)
            .where((ObjectLink.src_id == workspace_id) | (ObjectLink.dst_id == workspace_id))
        )
        or 0
    )
    referring = (
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.properties.cast(Text).contains(str(workspace_id)),
            )
        )
        or 0
    )
    return [
        extensions.WorkspaceReference(
            table="objects", label="객체", count=count, blocks_delete=True
        ),
        extensions.WorkspaceReference(
            table="object_links",
            label="이 부서와 이은 객체(관계)",
            count=int(linked),
            blocks_delete=True,
        ),
        extensions.WorkspaceReference(
            table="objects.properties",
            label="이 부서를 가리키는 객체(속성)",
            count=int(referring),
            blocks_delete=True,
        ),
    ]


def apply_year(
    db: Session, stmt: Select[Any], object_type: ObjectType, year: int
) -> Select[Any]:
    """축의 **시간 정책**대로 연도를 거른다.

        evergreen  필터를 무시한다 (기본값)
        lifecycle  valid_from_year ~ valid_to_year 에 그 해가 들면. NULL 끝 = 열림
        yearly     object_years 에 그 해가 배정돼 있으면 (불연속 가능)
        derived    도메인이 답한다 — 등록이 없으면 무시한다

    **`derived` 에서 등록이 없을 때 빈 목록을 주지 않는 이유**: 빈 목록은
    「데이터가 없다」 로 읽히고, 그러면 사람은 없는 것을 새로 만든다. 필터가
    작동하지 않는 것과 데이터가 없는 것은 다른 일이다.
    """
    kind = object_type.temporal_kind

    if kind == "lifecycle":
        return stmt.where(
            or_(
                ObjectInstance.valid_from_year.is_(None),
                ObjectInstance.valid_from_year <= year,
            ),
            or_(
                ObjectInstance.valid_to_year.is_(None),
                ObjectInstance.valid_to_year >= year,
            ),
        )

    if kind == "yearly":
        assigned = select(ObjectYear.object_id).where(ObjectYear.year == year)
        return stmt.where(ObjectInstance.id.in_(assigned))

    if kind == "derived":
        # 도메인이 등록한 것이 없으면 **거르지 않는다.**
        if not extensions.has_temporal_source():
            return stmt
        candidates = list(db.scalars(stmt.with_only_columns(ObjectInstance.id)))
        years = extensions.temporal_years(db, candidates)
        wanted = [one for one in candidates if year in years.get(one, set())]
        return stmt.where(ObjectInstance.id.in_(wanted))

    # evergreen — 연도 무관.
    return stmt
