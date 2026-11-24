"""허브가 관리하는 정의와 그 객체 — **받는 쪽에서는 받기만 한다.**

PLM 기준정보는 허브 플랫폼이 원천(PLM)에서 받아 쌍둥이 플랫폼에 내려준다. 쌍둥이가 받은 정의 ·
객체를 제 화면에서 고치면 다음 받기가 그 값을 덮어쓰고, 그 사실은 고친 사람에게 안 보인다 —
그래서 **고치는 길 자체를 막는다.** 받기는 묶음 가져오기에 `source` 를 적어서만 한다.

- 막는 것: 그 타입의 객체 만들기 · 고치기 · 지우기 · 합치기 · 되돌리기 · 별칭 · 연도, 파일로
  넣기 · 여럿 고치기 · 데이터 소스 동기화, 그 관계 종류의 줄, 정의(타입 · 속성 · 관계 종류 ·
  인터페이스) 고치기.
- 막지 않는 것: 이 설치의 관계가 그 객체를 **가리키는 것**(참여 과제 · 근거 문서), 지켜보기.
- 원천 자신은 연다: 그 원천의 이름(`source`)을 단 적재 · 삭제 표식(ADR 0090), 정의 삭제(같은
  ADR 의 보강 — `DELETE …?source=`).
"""

from __future__ import annotations

from app.modules.ontology.models import ObjectInterface, ObjectType, RelationType
from app.shared.errors import Conflict, code


def owner_of(row: ObjectType | RelationType | ObjectInterface) -> str:
    return (row.managed_by or "").strip()


def objects_refusal(
    object_type: ObjectType, *, source: str = "", what: str = "수정하지"
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
    object_type: ObjectType, *, source: str = "", what: str = "수정하지"
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


def require_definition_editable(row: ObjectType | RelationType | ObjectInterface) -> None:
    owner = owner_of(row)
    if owner:
        raise Conflict(
            code("ONTOLOGY", 82),
            f"{row.label}은(는) {owner} 가 관리하는 정의라 여기서 바꾸지 않습니다 — "
            f"{owner} 에서 고친 뒤 받으세요.",
            details={"managed_by": owner},
        )


def require_definition_deletable(
    row: ObjectType | RelationType | ObjectInterface, *, source: str = ""
) -> None:
    """정의(와 그 속성)를 지울 때 — **잠근 원천의 이름으로 부르면 지운다.**

    원천이 보낸 정의는 그 원천만 고친다. 고치기는 다시 보내면 되지만, 지우기는 보내서 할 길이
    없다 — 그래서 지우는 경로가 `source` 를 받는다(ADR 0090 보강). 이름이 다르거나 없으면 같은
    오류로 막는다. 사람은 화면의 「잠금 해제」 로 푼 뒤 지운다."""
    owner = owner_of(row)
    if owner and owner != source:
        raise Conflict(
            code("ONTOLOGY", 82),
            f"{row.label}은(는) {owner} 가 관리하는 정의라 여기서 삭제하지 않습니다 — "
            f"{owner} 가 원천 이름(source)을 적어 삭제하거나, 시스템 관리자가 「잠금 해제」 "
            "한 뒤 삭제하십시오.",
            details={"managed_by": owner},
        )


def deleted_by_source(
    row: ObjectType | RelationType | ObjectInterface, source: str
) -> str | None:
    """잠근 원천의 이름으로 지웠으면 감사 기록에 남길 까닭 — 잠금을 넘은 일이라 따로 적는다."""
    owner = owner_of(row)
    return f"원천({owner})의 이름으로 삭제" if owner and owner == source else None
