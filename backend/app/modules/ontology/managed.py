"""허브가 관리하는 정의와 그 객체 — **받는 쪽에서는 받기만 한다.**

PLM 기준정보는 허브 플랫폼이 원천(PLM)에서 받아 쌍둥이 플랫폼에 내려준다. 쌍둥이가 받은 정의 ·
객체를 제 화면에서 고치면 다음 받기가 그 값을 덮어쓰고, 그 사실은 고친 사람에게 안 보인다 —
그래서 **고치는 길 자체를 막는다.** 받기는 묶음 가져오기에 `source` 를 적어서만 한다.

- 막는 것: 그 타입의 객체 만들기 · 고치기 · 지우기 · 합치기 · 되돌리기 · 별칭 · 연도, 파일로
  넣기 · 여럿 고치기 · 데이터 소스 동기화, 그 관계 종류의 줄, 정의(타입 · 속성 · 관계 종류)
  고치기.
- 막지 않는 것: 이 설치의 관계가 그 객체를 **가리키는 것**(참여 과제 · 근거 문서), 지켜보기.
"""

from __future__ import annotations

from app.modules.ontology.models import ObjectType, RelationType
from app.shared.errors import Conflict, code


def owner_of(row: ObjectType | RelationType) -> str:
    return (row.managed_by or "").strip()


def objects_refusal(
    object_type: ObjectType, *, source: str = "", what: str = "고치지"
) -> str | None:
    """막아야 하면 사람에게 할 말, 아니면 None — 계획(행 오류)에 싣는 쪽이 쓴다."""
    owner = owner_of(object_type)
    if not owner or owner == source:
        return None
    return (
        f"{object_type.label}은(는) {owner} 가 관리하는 타입이라 여기서 {what} 않습니다 — "
        f"{owner} 에서 고친 뒤 받으세요."
    )


def require_objects_editable(
    object_type: ObjectType, *, source: str = "", what: str = "고치지"
) -> None:
    refused = objects_refusal(object_type, source=source, what=what)
    if refused:
        raise Conflict(
            code("OBJECTS", 87), refused, details={"managed_by": owner_of(object_type)}
        )


def require_relation_editable(kind: RelationType, *, source: str = "") -> None:
    owner = owner_of(kind)
    if owner and owner != source:
        raise Conflict(
            code("OBJECTS", 88),
            f"관계 종류 {kind.label}은(는) {owner} 가 관리해 여기서 잇거나 끊지 않습니다 — "
            f"{owner} 에서 고친 뒤 받으세요.",
            details={"managed_by": owner},
        )


def require_definition_editable(row: ObjectType | RelationType) -> None:
    owner = owner_of(row)
    if owner:
        raise Conflict(
            code("ONTOLOGY", 82),
            f"{row.label}은(는) {owner} 가 관리하는 정의라 여기서 바꾸지 않습니다 — "
            f"{owner} 에서 고친 뒤 받으세요.",
            details={"managed_by": owner},
        )
