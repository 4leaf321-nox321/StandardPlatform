"""관계 종류의 **수량 칸**(ADR 0040) — 무엇이 수량 칸이 될 수 있나를 가르는 한 자리.

수량 칸(`RelationType.quantity_key`)은 그 관계 종류의 **숫자 · 값 하나** 관계 속성의 키다.
전개(`objects/explode.py`)가 선마다 그 값을 「부모 하나에 자식 몇 개」 로 읽고 경로를 따라
곱한다. 비우면 그 종류의 선은 모두 1개다.

검사가 서는 자리:

    관계 종류 만들기 · 수정(API)          `require` — ONTOLOGY-0096
    정의 가져오기 · 스냅샷 복원 · 묶음    `problem` — 계획의 오류로(적용 전에 막힌다)

수량 칸인 속성을 숫자 아닌 것 · 여러 값으로 바꾸는 정의도 같은 계획에서 거절한다 — 바꾸면
전개가 모든 선을 「수량 없음」(1개)으로 세고, 그 총수량은 틀린 수인데 화면은 「수량 없음 n개」
만 말한다. (관계 종류의 속성은 지금 종류를 바꾸지 않는다 — `importer._plan_properties` 가 먼저
막는다. 그래도 여기서 따로 보는 것은 여러 값으로 바꾸는 길이 열려 있고, 종류 변경이 관계 속성에
열리는 날 이 검사가 그대로 서게 하려는 것이다.)

**관계 속성 하나만 삭제하는 길은 지금 없다** — 정의 가져오기는 더하고 수정만 하고, 관계 종류를
삭제하면 그 속성이 함께 간다(수량 칸도 함께 사라지므로 걸릴 것이 없다). 그 길을 만들면 삭제
계획이 「수량 칸이면 막음」 을 함께 가져야 한다 — 막지 않으면 수량 칸이 없는 키를 가리킨 채
남고, 전개는 모든 선을 「수량 없음」 으로 센다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ontology.models import PropertyDef, RelationType
from app.modules.ontology.services import InvalidValue
from app.shared.errors import code


@dataclass(frozen=True)
class Shape:
    """수량 칸 검사가 관계 속성에서 읽는 것."""

    label: str
    data_type: str
    multi: bool


def shapes(db: Session, kind_id: uuid.UUID) -> dict[str, Shape]:
    """그 관계 종류의 속성 — 키 → 모양."""
    return {
        row.key: Shape(label=row.label, data_type=row.data_type, multi=bool(row.multi))
        for row in db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind_id
            )
        )
    }


def problem(key: str, found: Shape | None, *, kind_label: str) -> str | None:
    """그 키가 수량 칸이 될 수 없으면 까닭(사람이 읽을 말), 되면 None. 빈 키는 늘 된다."""
    if not key:
        return None
    if found is None:
        return (
            f"{kind_label}에 관계 속성 {key!r} 이(가) 없습니다 — 수량 칸은 그 관계 종류의 "
            "숫자 관계 속성 하나입니다(관계 속성은 정의 가져오기로 추가합니다)."
        )
    if found.data_type != "number":
        return (
            f"수량 칸은 숫자 관계 속성이어야 합니다 — {found.label}({key})은(는) "
            f"{found.data_type} 입니다."
        )
    if found.multi:
        return (
            f"수량 칸은 값이 하나인 숫자여야 합니다 — {found.label}({key})은(는) 여러 값을 "
            "받습니다."
        )
    return None


def require(db: Session, kind: RelationType, key: str) -> str:
    """API 가 받은 수량 칸 — 될 수 없으면 거절(ONTOLOGY-0096). 빈 글자는 「수량 칸 없음」."""
    key = key.strip()
    message = problem(key, shapes(db, kind.id).get(key), kind_label=kind.label)
    if message:
        raise InvalidValue(code("ONTOLOGY", 96), message)
    return key


def breaking(key: str, found: Shape | None, *, kind_label: str) -> str | None:
    """수량 칸인 속성을 바꾸는 정의가 그것을 수량 칸이 될 수 없게 만들면 그 까닭."""
    message = problem(key, found, kind_label=kind_label)
    if message is None:
        return None
    return (
        f"속성 {key} 은(는) {kind_label}의 수량 칸이라 숫자 · 값 하나로 두어야 합니다 — "
        f"{message} 바꾸려면 수량 칸을 먼저 비우거나 다른 속성으로 정하세요."
    )
