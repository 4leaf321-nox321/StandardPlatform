"""인스턴스의 정합 — **DB 제약으로 못 거는 것들.**

`key` 의 유니크 범위가 타입마다 다르고(`key_scope`), 참조가 가리키는 객체는
JSONB 안에 있다. 둘 다 DB 가 안 잡아 주므로 여기서 잡는다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
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
    wanted = object_ref_ids(defs, values)
    if not wanted:
        return
    found = set(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.id.in_(wanted), ObjectInstance.deleted_at.is_(None)
            )
        )
    )
    missing = [str(x) for x in wanted if x not in found]
    if missing:
        raise InvalidValue(
            code("OBJECTS", 4),
            f"가리키는 객체를 찾을 수 없습니다: {', '.join(missing)}",
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
    return [
        extensions.WorkspaceReference(
            table="objects", label="객체", count=count, blocks_delete=True
        )
    ]
