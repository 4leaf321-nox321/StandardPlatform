"""값 변환 — 저장된 값을 **정해진 글자로 읽은 뒤** 새 속성 종류로 해석한다(ADR 0007).

종류 변경(화면 · 정의 가져오기 · 허브 묶음 · 스냅샷 복원)과 일괄 입력 · 조건 · 이력 되돌리기가
**같은 규칙**을 쓴다. 쌍마다 규칙을 따로 두면 한 쌍만 고쳐지고, 그때 같은 값이 길에 따라
다르게 변환된다. 세션을 모른다 — DB 는 `retype.py` 가 본다.

    글 · 긴 글 · 주소   그대로(주소는 http(s) 검사 · 패턴은 정의대로)
    숫자              부호 · 천 단위 쉼표(엄격) · 소수 · 지수. 단위 · nan · inf · 밑줄은 거절.
                      정수면 정확한 int(긴 숫자가 float 을 거치며 깎이지 않게)
    날짜              2024-03-05 · 2024.3.5 · 2024/03/05 · 20240305 · 2024년 3월 5일 → ISO.
                      시각을 주면 날짜 부분(00:00 이 아니면 손실로 센다)
    시각              **시간대 없는** YYYY-MM-DDTHH:MM[:SS] — 화면의 입력
                      (datetime-local)이 시간대 값을 못 보여 주고, 그러면 다음 저장에서
                      지워진다. 시간대가 붙은 값은 변환할 수 없다
    참/거짓           한 어휘(아래 `TRUE_WORDS` · `FALSE_WORDS`)
    고를 값           앞뒤 공백을 뗀 **정확한 일치**
    참조              글 · 긴 글 · 고를 값과 오간다(ADR 0009). 글 → 참조는 일괄 입력과
                      같은 이름 풀이(식별자 → 별칭 → 이름 → id, 이름이 여럿이면 실패),
                      참조 → 글은 상대의 식별자(없으면 이름) — 되돌리면 같은 객체로 다시
                      풀린다. 이름 풀이는 DB 를 아는 쪽(`retype.RefLinker`)이 준다

빈 글자 · 빈 목록은 실패가 아니라 **값이 없는 것**이다 — 키를 지운다(남기면 숫자 조건의
cast 가 빈 글자에서 터진다). 여러 값은 원소마다 변환하고, 빈 결과는 빼고, 변환으로 생긴
중복은 하나로 친다.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.modules.ontology.models import PropertyDef
from app.modules.ontology.services import InvalidValue, check_value

#: 참/거짓으로 읽는 말 — 일괄 입력 · 조건 · 종류 변경이 함께 쓴다(소문자로 견준다).
TRUE_WORDS: frozenset[str] = frozenset({"true", "1", "y", "yes", "예", "참", "o"})
FALSE_WORDS: frozenset[str] = frozenset(
    {"false", "0", "n", "no", "아니오", "아니요", "거짓", "x"}
)

#: 종류 변경이 오가는 값의 종류. 참조는 `LINKABLE` 과만 오가고, 파일은 양쪽 다 아니다.
SUPPORTED: tuple[str, ...] = (
    "text",
    "text_long",
    "url",
    "number",
    "date",
    "datetime",
    "bool",
    "enum",
)

#: 참조와 오가는 종류 — 글자로 읽히는 것만. 숫자 · 날짜를 거치면 식별자 「007」 이 「7」 이
#: 된다.
LINKABLE: tuple[str, ...] = ("text", "text_long", "enum")

#: 화면(JavaScript)의 숫자가 정확히 담는 정수의 끝. 넘으면 화면에서 끝자리가 깎인다.
SAFE_INTEGER = 2**53

_TEXT_KINDS = ("text", "text_long")

_NUMBER = re.compile(r"^[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DATE_SEP = re.compile(r"^(\d{4})([-./])\s*(\d{1,2})\2\s*(\d{1,2})\.?$")
_DATE_KO = re.compile(r"^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일$")
_DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$")


class Unconvertible(ValueError):
    """규칙으로는 바꿀 수 없는 값 — `reason` 이 사람이 읽는 까닭이다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- 읽기 -----------------------------------------------------------------------


def as_text(raw: Any) -> str:
    """저장값 하나를 **정해진 글자**로. 대체 값의 열쇠도 이것이다.

    참/거짓은 「예」 · 「아니오」(화면이 그렇게 보여 준다), 정수인 실수는 「3」(「3.0」 이면
    고를 값 「3」 과 안 맞는다), 나머지 실수는 지수 없는 표기.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bool):
        return "예" if raw else "아니오"
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise Unconvertible("유한한 숫자가 아닙니다")
        if raw.is_integer():
            return str(int(raw))
        return format(Decimal(repr(raw)), "f")
    raise Unconvertible("글자로 읽을 수 없는 값입니다(목록 · 표)")


def key_of(raw: Any) -> str:
    """변환할 수 없는 값을 가리키는 글자 — 대체 값 표의 열쇠. 목록 · 표는 정렬한 JSON 으로."""
    try:
        return as_text(raw)
    except Unconvertible:
        return json.dumps(raw, ensure_ascii=False, sort_keys=True)


# --- 종류마다 해석 ---------------------------------------------------------------


def parse_number(text: str) -> int | float:
    """숫자로. **반올림하지 않는다** — 자릿수 · 범위는 정의가 본다(`check_value`)."""
    value = text.strip()
    if not _NUMBER.match(value):
        raise Unconvertible("숫자가 아닙니다")
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:  # pragma: no cover - 위 식이 걸러 낸다
        raise Unconvertible("숫자가 아닙니다") from None
    if not number.is_finite():
        raise Unconvertible("유한한 숫자가 아닙니다")
    if number == number.to_integral_value():
        return int(number)
    out = float(number)
    if not math.isfinite(out):
        raise Unconvertible("너무 큰 숫자입니다")
    return out


def parse_bool(text: str) -> bool:
    low = text.strip().lower()
    if low in TRUE_WORDS:
        return True
    if low in FALSE_WORDS:
        return False
    raise Unconvertible("참/거짓으로 읽을 수 없습니다")


def _date_of(text: str) -> date | None:
    for pattern, groups in (
        (_DATE_SEP, (1, 3, 4)),
        (_DATE_KO, (1, 2, 3)),
        (_DATE_COMPACT, (1, 2, 3)),
    ):
        found = pattern.match(text)
        if found is None:
            continue
        year, month, day = (int(found.group(one)) for one in groups)
        try:
            return date(year, month, day)
        except ValueError:
            raise Unconvertible("없는 날짜입니다") from None
    return None


def parse_date(text: str) -> tuple[str, bool]:
    """날짜로 — (ISO 날짜, 시각을 버렸나)."""
    value = text.strip()
    found = _date_of(value)
    if found is not None:
        return found.isoformat(), False
    moment = _moment_of(value)
    if moment is None:
        raise Unconvertible("날짜가 아닙니다")
    dropped = (moment.hour, moment.minute, moment.second, moment.microsecond) != (0, 0, 0, 0)
    return moment.date().isoformat(), dropped


def _moment_of(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = None
    if moment is None:
        # 「2024.3.5 13:05」 처럼 날짜 표기가 ISO 가 아닌 것.
        head, _, tail = text.partition(" ")
        day = _date_of(head) if tail else None
        clock = _TIME.match(tail.strip()) if day is not None else None
        if day is None or clock is None:
            return None
        hour, minute = int(clock.group(1)), int(clock.group(2))
        second = int(clock.group(3) or 0)
        try:
            moment = datetime(day.year, day.month, day.day, hour, minute, second)
        except ValueError:
            raise Unconvertible("없는 시각입니다") from None
    if moment.tzinfo is not None:
        raise Unconvertible(
            "시간대가 붙은 시각입니다 — 화면이 시간대를 못 보여 주므로 "
            "시간대 없는 시각으로 적으세요"
        )
    return moment


def parse_datetime(text: str) -> tuple[str, bool]:
    """시각으로 — (시간대 없는 표기, 초 아래를 버렸나). 날짜만이면 00:00."""
    value = text.strip()
    day = _date_of(value)
    if day is not None:
        return f"{day.isoformat()}T00:00", False
    moment = _moment_of(value)
    if moment is None:
        raise Unconvertible("날짜와 시각이 아닙니다")
    shown = moment.strftime("%Y-%m-%dT%H:%M")
    if moment.second:
        shown += f":{moment.second:02d}"
    return shown, bool(moment.microsecond)


# --- 값 하나 · 저장값 하나 --------------------------------------------------------


class Linker(Protocol):
    """참조를 오가는 변환의 이름 풀이 — 세션을 아는 쪽이 준다(`retype.RefLinker`)."""

    def resolve(self, target: str, text: str) -> str:
        """글자 → `target`(타입 · 인터페이스)의 객체 id. 하나로 안 풀리면 `Unconvertible`."""
        ...

    def text_of(self, object_id: str) -> str:
        """객체 id → 식별자(없으면 이름). 가리키던 것이 없으면 `Unconvertible`."""
        ...


@dataclass
class Converted:
    """저장값 하나(여러 값이면 그 목록)를 바꾼 결과."""

    value: Any = None
    remove: bool = False
    """키를 지운다 — 비었거나(빈 글자 · 빈 목록) 값 삭제만 남았다."""
    changed: bool = False
    failures: list[tuple[str, str]] = field(default_factory=list)
    """(대체 값 열쇠, 까닭) — 원소마다."""
    mapped: list[str] = field(default_factory=list)
    """대체 값을 쓴 열쇠."""
    lossy: list[str] = field(default_factory=list)
    """잃는 것의 종류 — `time` 시각을 버림 · `subsecond` 초 아래를 버림 · `precision` 화면
    정밀도 · `duplicate` 변환으로 겹친 원소."""


def _blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def _read(raw: Any, linker: Linker | None, from_ref: bool) -> str:
    """저장된 원소를 글자로 — 참조였으면 상대의 식별자(없으면 이름)."""
    if not from_ref:
        return as_text(raw)
    if linker is None:
        raise Unconvertible("참조는 이름 풀이와 함께만 바꿉니다")
    return linker.text_of(as_text(raw))


def _one(
    target: PropertyDef, text: str, lossy: list[str], linker: Linker | None = None
) -> Any:
    """글자 하나를 대상 종류로 해석하고 정의로 검사한다. 안 되면 `Unconvertible`."""
    kind = target.data_type
    value: Any
    if kind in _TEXT_KINDS:
        value = text
    elif kind == "url":
        value = text.strip()
    elif kind == "number":
        value = parse_number(text)
        if isinstance(value, int) and abs(value) > SAFE_INTEGER:
            lossy.append("precision")
    elif kind == "bool":
        value = parse_bool(text)
    elif kind == "date":
        value, dropped = parse_date(text)
        if dropped:
            lossy.append("time")
    elif kind == "datetime":
        value, dropped = parse_datetime(text)
        if dropped:
            lossy.append("subsecond")
    elif kind == "enum":
        value = text.strip()
    elif kind == "object_ref":
        if linker is None:
            raise Unconvertible("참조로는 이름 풀이와 함께만 바꿉니다")
        value = linker.resolve(target.ref_type_slug or "", text)
    else:
        raise Unconvertible(f"{kind} 로는 바꾸지 않습니다")
    try:
        return check_value(target, value)
    except InvalidValue as caught:
        raise Unconvertible(caught.message) from None


def convert_element(
    target: PropertyDef,
    raw: Any,
    mapping: Mapping[str, str | None] | None = None,
    *,
    linker: Linker | None = None,
    from_ref: bool = False,
) -> tuple[Any, bool, str]:
    """원소 하나 — (새 값 · 지우나 · 쓴 대체 값 열쇠).

    안 되면 `Unconvertible`(열쇠는 `key_of` — 참조였으면 상대의 id). 대체 값은 **새 종류의
    글자**다 — 참조로 바꿀 때는 상대의 식별자 · 이름 · id."""
    key = key_of(raw)
    if mapping and key in mapping:
        replacement = mapping[key]
        if replacement is None or replacement.strip() == "":
            return None, True, key
        try:
            return _one(target, replacement, [], linker), False, key
        except Unconvertible as caught:
            raise Unconvertible(
                f"대체 값 「{replacement}」 도 변환할 수 없습니다 — {caught.reason}"
            ) from None
    if _blank(raw):
        return None, True, ""
    return _one(target, _read(raw, linker, from_ref), [], linker), False, ""


def convert_stored(
    target: PropertyDef,
    raw: Any,
    mapping: Mapping[str, str | None] | None = None,
    *,
    linker: Linker | None = None,
    from_ref: bool = False,
) -> Converted:
    """저장값 하나를 새 정의로. 원소 하나라도 실패하면 `failures` 가 찬다(값은 쓰지 않는다).

    `from_ref` 면 저장값이 참조(상대의 id)다 — 원소마다 상대의 식별자로 읽은 뒤 바꾼다."""
    out = Converted()
    items: list[Any]
    if target.multi:
        # 여러 값 칸에 값 하나가 남아 있으면(옛 데이터) 원소 하나로 읽는다.
        items = raw if isinstance(raw, list) else [] if _blank(raw) else [raw]
    else:
        if isinstance(raw, list):
            out.failures.append((key_of(raw), "값 하나만 담는 속성에 목록이 있습니다"))
            return out
        items = [raw]

    kept: list[Any] = []
    for item in items:
        key = key_of(item)
        try:
            if mapping and key in mapping:
                value, drop, used = convert_element(
                    target, item, mapping, linker=linker, from_ref=from_ref
                )
            else:
                if _blank(item):
                    continue
                notes: list[str] = []
                text = _read(item, linker, from_ref)
                value, drop, used = _one(target, text, notes, linker), False, ""
                out.lossy.extend(notes)
        except Unconvertible as caught:
            out.failures.append((key, caught.reason))
            continue
        if used:
            out.mapped.append(used)
        if drop:
            continue
        if target.multi and value in kept:
            out.lossy.append("duplicate")
            continue
        kept.append(value)

    if out.failures:
        return out
    if not kept:
        out.remove = True
        out.changed = raw is not None
        return out
    out.value = kept if target.multi else kept[0]
    out.changed = out.value != raw or type(out.value) is not type(raw)
    return out
