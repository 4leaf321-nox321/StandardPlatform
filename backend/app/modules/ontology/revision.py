"""계보의 **정의 검사**(ADR 0041) — 관계 종류의 원천 끝과 타입의 개정 칸이 될 수 있나를 가르는
한 자리. ADR 0040 의 수량 칸(`quantity.py`)과 같은 무늬다.

## 원천 끝(`RelationType.lineage_source`)

`""`(계보 아님) · `"src"`(출발이 원천) · `"dst"`(도착이 원천). **방향 있는 종류만** — 방향 없는
선은 어느 끝이 원천인지 정할 수 없다(맺는 사람마다 다른 쪽을 출발로 고른다).

## 개정 칸(`ObjectType.revision_key`)

그 타입의 **글자 · 고르기 · 숫자, 값 하나** 속성의 키. 비면 개정 칸 없음(바뀐 때로 판정한다).
날짜 · 참조 · 파일 · 여러 값은 「판」 으로 견줄 하나의 값이 아니다.

검사가 서는 자리:

    타입 만들기 · 수정(API)                    `require` — ONTOLOGY-0098
    관계 종류 만들기 · 수정(API)               `require_source` — ONTOLOGY-0097
    정의 가져오기 · 스냅샷 복원 · 묶음         `problem` · `source_problem` — 계획의 오류로
    속성 수정 · 종류 변경 · 삭제(API)          `require_property_change` ·
    `require_not_revision`
                                               — ONTOLOGY-0099

**쓰이는 칸을 개정 칸이 될 수 없는 것으로 바꾸는 정의는 거절한다**(종류 변경 · 여러 값 ·
삭제) — 바꾸게 두면 그 타입을 원천으로 둔 모든 파생물의 판정이 「값이 비었다」 로 흔들리고(값이
없어진 원천은 고정한 값과 달라 전부 낡음이 된다), 그 까닭은 화면 어디에도 안 뜬다. 바꾸려면
개정 칸을 먼저 비우거나 다른 속성으로 정한다 — 그때 고정은 판정을 지키며 옮겨진다
(`objects/pinning.py` 의 `object_types_revision`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.services import InvalidValue
from app.shared.errors import code

#: 원천 끝 — 빈 글자는 「계보 관계 아님」.
SOURCES = ("", "src", "dst")
#: 개정 칸이 될 수 있는 값 종류.
REVISION_TYPES = ("text", "enum", "number")


def source_problem(source: str, *, directed: bool, kind_label: str) -> str | None:
    """그 원천 끝이 될 수 없으면 까닭, 되면 None."""
    if source not in SOURCES:
        return (
            f"{kind_label}: 계보의 원천 끝은 src(출발) · dst(도착) · "
            f"빈 값(계보 아님) 중 하나입니다: {source!r}"
        )
    if source and not directed:
        return (
            f"{kind_label}: 방향이 없는 관계는 계보 관계가 될 수 없습니다 — "
            "어느 끝이 원천(만드는 데 쓰인 것)인지 정할 수 없습니다. "
            "방향을 켜거나 계보를 끄세요."
        )
    return None


def require_source(source: str | None, *, directed: bool, kind_label: str) -> str:
    """API 가 받은 원천 끝 — 될 수 없으면 거절(ONTOLOGY-0097)."""
    value = (source or "").strip()
    message = source_problem(value, directed=directed, kind_label=kind_label)
    if message:
        raise InvalidValue(code("ONTOLOGY", 97), message)
    return value


@dataclass(frozen=True)
class Shape:
    """개정 칸 검사가 속성에서 읽는 것."""

    label: str
    data_type: str
    multi: bool


def shapes(db: Session, type_id: uuid.UUID) -> dict[str, Shape]:
    """그 타입의 속성 — 키 → 모양."""
    return {
        row.key: Shape(label=row.label, data_type=row.data_type, multi=bool(row.multi))
        for row in db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type", PropertyDef.owner_id == type_id
            )
        )
    }


def problem(key: str, found: Shape | None, *, type_label: str) -> str | None:
    """그 키가 개정 칸이 될 수 없으면 까닭(사람이 읽을 말), 되면 None. 빈 키는 늘 된다."""
    if not key:
        return None
    if found is None:
        return (
            f"{type_label}에 속성 {key!r} 이(가) 없습니다 — "
            "개정 칸은 그 타입의 글자 · 고르기 · 숫자 속성 하나입니다."
        )
    if found.data_type not in REVISION_TYPES:
        return (
            "개정 칸은 글자 · 고르기 · 숫자 속성이어야 합니다 — "
            f"{found.label}({key})은(는) {found.data_type} 입니다."
        )
    if found.multi:
        return (
            f"개정 칸은 값이 하나여야 합니다 — {found.label}({key})은(는) 여러 값을 받습니다."
        )
    return None


def require(db: Session, object_type: ObjectType, key: str | None) -> str:
    """API 가 받은 개정 칸 — 될 수 없으면 거절(ONTOLOGY-0098). 빈 글자는 「개정 칸 없음」."""
    value = (key or "").strip()
    message = problem(
        value, shapes(db, object_type.id).get(value), type_label=object_type.label
    )
    if message:
        raise InvalidValue(code("ONTOLOGY", 98), message)
    return value


def breaking(key: str, found: Shape | None, *, type_label: str) -> str | None:
    """개정 칸인 속성을 바꾸는 정의가 그것을 개정 칸이 될 수 없게 만들면 그 까닭."""
    message = problem(key, found, type_label=type_label)
    if message is None:
        return None
    return (
        f"속성 {key} 은(는) {type_label}의 개정 칸이라 "
        f"글자 · 고르기 · 숫자, 값 하나로 두어야 합니다 — {message} "
        "바꾸려면 개정 칸을 먼저 비우거나 다른 속성으로 정하세요."
    )


def require_property_change(
    object_type: ObjectType, key: str, *, data_type: str, multi: bool, label: str
) -> None:
    """개정 칸인 속성을 고치기 전 — 개정 칸이 될 수 없게 바꾸면 거절(ONTOLOGY-0099)."""
    if not object_type.revision_key or object_type.revision_key != key:
        return
    message = breaking(
        key,
        Shape(label=label, data_type=data_type, multi=multi),
        type_label=object_type.label,
    )
    if message:
        raise InvalidValue(code("ONTOLOGY", 99), message)


def require_not_revision(object_type: ObjectType, key: str, *, what: str) -> None:
    """개정 칸인 속성을 지우거나 다른 것으로 옮기기 전 — 거절(ONTOLOGY-0099)."""
    if object_type.revision_key and object_type.revision_key == key:
        raise InvalidValue(
            code("ONTOLOGY", 99),
            f"속성 {key} 은(는) {object_type.label}의 개정 칸이라 {what} 수 없습니다 — "
            "개정 칸을 먼저 비우거나 다른 속성으로 정하세요"
            "(고정은 판정을 지키며 옮겨집니다).",
        )
