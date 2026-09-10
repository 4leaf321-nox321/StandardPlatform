"""속성 값 검증 — **JSONB 에는 제약을 못 건다. 그래서 여기가 유일한 자리다.**

`objects.properties` 는 JSONB 한 칸이라 유니크·FK·CHECK 가 없다. 그 대가를 알고
고른 것이고([ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md)), 대신
**검증을 한 곳에 모은다.** 라우트마다 흩뿌리면 「목록에는 보이는데 저장은 안 되는」
값이 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from app.modules.ontology.models import (
    DATA_TYPES,
    PropertyDef,
)
from app.shared.errors import AppError, code

#: slug 는 **바뀌면 안 되는 식별자**다. URL·관계의 허용 타입·MCP 도구 이름이
#: 여기 물리므로, 나중에 이스케이프가 필요해지는 문자를 처음부터 막는다.
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: 속성 키도 같은 규칙이다. `properties` 의 키이자 `list_view` 가
#: `properties.<key>` 로 가리키는 이름이라, 점이나 공백이 들어가면 그 표기가 깨진다.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class InvalidValue(AppError):
    """속성 값이 정의와 안 맞는다.

    422 인 이유: 형식은 맞는데 **의미가 안 맞는** 것이다. 400 으로 내면 「본문이
    깨졌다」 와 구별이 안 되고, 그러면 화면이 무엇을 고쳐야 할지 못 정한다.
    """

    def __init__(self, code: str, message: str, **kw: Any) -> None:
        super().__init__(code, message, status=422, **kw)


def require_slug(value: str, *, what: str) -> str:
    """소문자로 시작하는 영숫자·밑줄. **바꿀 수 없는 값이므로 들어올 때 막는다.**"""
    value = (value or "").strip()
    if not SLUG_RE.match(value):
        raise InvalidValue(
            code("ONTOLOGY", 1),
            f"{what}는 소문자로 시작하는 영문·숫자·밑줄 32자 이내여야 합니다: {value!r}",
        )
    return value


def require_key(value: str) -> str:
    value = (value or "").strip()
    if not KEY_RE.match(value):
        raise InvalidValue(
            code("ONTOLOGY", 2),
            f"속성 키는 소문자로 시작하는 영문·숫자·밑줄 48자 이내여야 합니다: {value!r}",
        )
    return value


def require_choice(value: str, allowed: tuple[str, ...], *, what: str) -> str:
    if value not in allowed:
        raise InvalidValue(
            code("ONTOLOGY", 3),
            f"{what}는 {', '.join(allowed)} 중 하나여야 합니다: {value!r}",
        )
    return value


# --- 값 하나 ----------------------------------------------------------------


def _coerce_one(definition: PropertyDef, raw: Any) -> Any:
    """값 하나를 그 `data_type` 으로 받아들이거나 거절한다.

    **받아들일 때 모양을 고정한다.** 숫자를 문자열로 받아 두면 정렬이 사전순이
    되고, 그 목록은 틀렸다는 말을 하지 않는다.
    """
    label = definition.label
    kind = definition.data_type

    if kind == "text":
        if not isinstance(raw, str):
            raise InvalidValue(code("ONTOLOGY", 10), f"{label}: 글자여야 합니다.")
        return raw

    if kind == "number":
        # bool 은 파이썬에서 int 의 하위형이다. 걸러 내지 않으면 True 가 1 로
        # 저장되고, 그 값은 나중에 아무도 설명할 수 없다.
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise InvalidValue(code("ONTOLOGY", 11), f"{label}: 숫자여야 합니다.")
        return raw

    if kind == "bool":
        if not isinstance(raw, bool):
            raise InvalidValue(code("ONTOLOGY", 12), f"{label}: 예/아니오 값이어야 합니다.")
        return raw

    if kind == "date":
        if not isinstance(raw, str):
            raise InvalidValue(code("ONTOLOGY", 13), f"{label}: 날짜(YYYY-MM-DD)여야 합니다.")
        try:
            date.fromisoformat(raw)
        except ValueError:
            raise InvalidValue(
                code("ONTOLOGY", 13), f"{label}: 날짜(YYYY-MM-DD)여야 합니다: {raw!r}"
            ) from None
        return raw

    if kind == "enum":
        options = definition.enum_options or []
        if raw not in options:
            raise InvalidValue(
                code("ONTOLOGY", 14),
                f"{label}: {', '.join(options) or '(정의된 값 없음)'} 중에서 고르세요.",
            )
        return raw

    if kind == "object_ref":
        if not isinstance(raw, str):
            raise InvalidValue(code("ONTOLOGY", 15), f"{label}: 객체를 고르세요.")
        try:
            uuid.UUID(raw)
        except ValueError:
            raise InvalidValue(code("ONTOLOGY", 15), f"{label}: 객체를 고르세요.") from None
        # **가리키는 객체가 실제로 있는지는 여기서 안 본다.** DB 를 알아야 하는
        # 일이라 objects 쪽 서비스가 본다 — 이 모듈은 세션을 모른다.
        return raw

    if kind == "file":
        # 첨부는 `properties` 에 안 들어간다(`attachments.owner_field`). 여기로
        # 값이 왔다는 것은 화면이나 API 사용자가 잘못 알고 있다는 뜻이라,
        # **조용히 버리지 않고 말해 준다.**
        raise InvalidValue(
            code("ONTOLOGY", 16),
            f"{label}: 파일 속성은 첨부로 올립니다. properties 에 넣지 않습니다.",
        )

    raise InvalidValue(code("ONTOLOGY", 17), f"{label}: 모르는 속성 종류입니다: {kind}")


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


# --- 값 묶음 ----------------------------------------------------------------


def merge_properties(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """부분 수정 — **「안 보낸 것」 과 「비운 것」 을 구별한다.**

    통째로 덮으면 이름 하나 바꿀 때마다 다른 속성이 함께 날아가고, **그 손실은
    저장한 사람 눈에 안 보인다.** 보낸 키만 병합하고, 지우려면 `null` 을 명시한다.
    """
    merged = dict(current)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def validate_properties(defs: list[PropertyDef], values: dict[str, Any]) -> dict[str, Any]:
    """정의에 비추어 값 묶음 전체를 검증하고, 저장할 모양으로 돌려준다.

    **모르는 키는 거절한다.** 조용히 저장하면 어느 화면에도 안 나오는 데이터가
    쌓이고, 그것이 있다는 사실은 아무도 모른다.
    """
    by_key = {d.key: d for d in defs}

    unknown = sorted(set(values) - set(by_key))
    if unknown:
        raise InvalidValue(
            code("ONTOLOGY", 20),
            f"정의되지 않은 속성입니다: {', '.join(unknown)}. "
            "속성을 먼저 정의하거나 키를 확인하세요.",
        )

    cleaned: dict[str, Any] = {}
    for key, definition in by_key.items():
        raw = values.get(key)

        if _is_empty(raw):
            if definition.required and definition.data_type != "file":
                raise InvalidValue(
                    code("ONTOLOGY", 21), f"{definition.label}: 값이 필요합니다."
                )
            continue

        if definition.multi:
            if not isinstance(raw, list):
                raise InvalidValue(
                    code("ONTOLOGY", 22), f"{definition.label}: 여러 값의 목록이어야 합니다."
                )
            cleaned[key] = [_coerce_one(definition, item) for item in raw]
        else:
            if isinstance(raw, list):
                raise InvalidValue(
                    code("ONTOLOGY", 23), f"{definition.label}: 값 하나만 넣습니다."
                )
            cleaned[key] = _coerce_one(definition, raw)

    return cleaned


def object_ref_ids(defs: list[PropertyDef], values: dict[str, Any]) -> list[uuid.UUID]:
    """값 묶음이 가리키는 객체 id 들. **존재 확인은 부르는 쪽이 한다.**

    가리키는 객체가 없는 참조를 저장하면 화면에는 빈 칸으로 나오고, 그것이
    「값이 없는 것」 인지 「가리키는 것이 사라진 것」 인지 구별할 수 없다.
    """
    out: list[uuid.UUID] = []
    by_key = {d.key: d for d in defs}
    for key, raw in values.items():
        definition = by_key.get(key)
        if definition is None or definition.data_type != "object_ref":
            continue
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            try:
                out.append(uuid.UUID(str(item)))
            except ValueError:  # pragma: no cover - _coerce_one 이 이미 막는다
                continue
    return out


def known_data_types() -> tuple[str, ...]:
    return DATA_TYPES
