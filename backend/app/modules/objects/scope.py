"""목록이 묻는 범위 — **타입 하나, 또는 인터페이스의 구현 타입 전부**(ADR 0006).

목록 · 통계 · 이어진 칸 · 이름 풀이 · 빈 결과 진단은 「이 타입의 객체들」 을 물어 왔다.
인터페이스 목록(`/o/<인터페이스>`)은 같은 물음을 **여러 타입에** 던진다. 구현 타입은 공통
속성을 **같은 키 · 같은 모양**으로 가지므로(ADR 0006), 저장된 값을 읽는 식(`properties[key]`)은
그대로 쓰고 **타입 조건만** `type_id IN (…)` 으로 넓히면 된다 — 그 차이를 이 한 곳이 쥔다.

    Scope.of_type   타입 하나 — 지금까지와 같다
    of_interface    인터페이스 — 구현 타입 전부, 속성은 공통 속성(상위 인터페이스까지)

**쓰기는 범위가 아니라 타입이다.** 만들기 · 고치기 · 가져오기 · 트리 · 저장된 뷰는 타입을
받는다(`routes._type` 이 인터페이스를 받으면 이유를 말하고 거절한다). 이 모듈은 읽기만 넓힌다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import properties_of
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectInterface, ObjectType, PropertyDef


@dataclass
class Scope:
    slug: str
    label: str
    kind: str
    """`type` · `interface`."""
    types: list[ObjectType]
    """객체가 들어 있는 타입들 — 타입이면 그것 하나, 인터페이스면 구현 타입 전부."""
    defs: list[PropertyDef]
    """조건 · 정렬 · 검색 · 통계가 쓰는 속성 정의 — 인터페이스면 **공통 속성**."""
    list_view: dict[str, Any]
    object_type: ObjectType | None = None
    interface: ObjectInterface | None = None
    match_slugs: frozenset[str] = field(default_factory=frozenset)
    """관계 종류의 허용 끝과 견줄 slug — 구현 타입과 그 인터페이스들."""

    @property
    def is_interface(self) -> bool:
        return self.kind == "interface"

    @property
    def type_ids(self) -> list[uuid.UUID]:
        return [one.id for one in self.types]

    @property
    def type_slugs(self) -> list[str]:
        return [one.slug for one in self.types]

    @property
    def key_policy(self) -> str:
        """식별자 축을 세울 만한가 — 구현 타입이 **모두** 식별자를 안 쓰면 안 세운다."""
        if self.object_type is not None:
            return self.object_type.key_policy
        if self.types and all(one.key_policy == "none" for one in self.types):
            return "none"
        return "optional"

    def clause(self, column: Any = ObjectInstance.type_id) -> Any:
        """이 범위의 타입 조건. **구현 타입이 없으면 아무것도 안 맞는다** — 빈 목록을
        「제약 없음」 으로 읽으면 인터페이스 목록이 설치 전체를 보여 준다."""
        ids = self.type_ids
        if not ids:
            return false()
        if len(ids) == 1:
            return column == ids[0]
        return column.in_(ids)

    def type_of(self, type_id: uuid.UUID) -> ObjectType | None:
        return next((one for one in self.types if one.id == type_id), None)


def of_type(db: Session, object_type: ObjectType) -> Scope:
    return Scope(
        slug=object_type.slug,
        label=object_type.label,
        kind="type",
        types=[object_type],
        defs=properties_of(db, object_type.id),
        list_view=object_type.list_view or {},
        object_type=object_type,
        match_slugs=frozenset(
            [object_type.slug, *_interfaces_of(db, object_type.interface_slugs or [])]
        ),
    )


def _interfaces_of(db: Session, declared: list[str]) -> list[str]:
    if not declared:
        return []
    return interfaces.closure(declared, interfaces.load(db).extends_of())


def of_interface(db: Session, iface: ObjectInterface) -> Scope:
    catalog = interfaces.load(db)
    implementers = set(interfaces.implementers(catalog, iface.slug))
    types = list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.slug.in_(implementers))
            .order_by(ObjectType.sort_order, ObjectType.label)
        )
    )
    # 공통 속성 — 상위 인터페이스에서 이어받은 것까지, 그것을 정한 인터페이스의 정의로.
    wanted, _ = interfaces.contract(catalog, [iface.slug])
    owners = {
        row.slug: row.id
        for row in db.scalars(
            select(ObjectInterface).where(
                ObjectInterface.slug.in_({owner for _, owner in wanted.values()})
            )
        )
    }
    rows = {
        (row.owner_id, row.key): row
        for row in db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "interface",
                PropertyDef.owner_id.in_(list(owners.values())),
            )
        )
    }
    defs = [
        rows[(owners[owner], key)]
        for key, (_, owner) in wanted.items()
        if owner in owners and (owners[owner], key) in rows
    ]
    defs.sort(key=lambda one: (one.sort_order, one.label))
    extends_of = catalog.extends_of()
    reach = {iface.slug, *interfaces.closure([iface.slug], extends_of)}
    return Scope(
        slug=iface.slug,
        label=iface.label,
        kind="interface",
        types=types,
        defs=defs,
        list_view=iface.list_view or {},
        interface=iface,
        match_slugs=frozenset([*implementers, *reach]),
    )


def find(db: Session, slug: str) -> Scope | None:
    """slug 하나 — 타입이면 그 타입, 인터페이스면 구현 타입 전부. 둘은 slug 를 함께 쓴다."""
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if object_type is not None:
        return of_type(db, object_type)
    iface = db.scalar(select(ObjectInterface).where(ObjectInterface.slug == slug))
    if iface is not None:
        return of_interface(db, iface)
    return None


def as_scope(db: Session, target: ObjectType | Scope) -> Scope:
    return target if isinstance(target, Scope) else of_type(db, target)
