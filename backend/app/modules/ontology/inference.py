"""표에서 타입을 추론한다 — **CSV·JSON 하나로 타입과 데이터가 함께 생긴다.**

파일 가져오기는 타입이 먼저 있어야 한다. 그래서 「이 표를 그냥 넣어 줘」 는 정의를 손으로
먼저 만들어야 했다. 여기서는 열을 보고 정의를 **제안**한다 — 숫자·날짜·참/거짓·고를 값·주소·
글자. 사람이 계획에서 종류를 고치고 나서 적용한다.

**보수적으로 맞힌다.** 애매하면 글자다. 틀린 추론은 조용히 데이터를 망친다 — 「고를 값」 으로
잘못 잡힌 열은 새 값이 올 때마다 거절되고, 「숫자」 로 잘못 잡힌 열은 문자를 잃는다. 글자로
두면 나중에 언제든 좁힐 수 있지만, 그 반대는 코드표 승격 같은 절차가 필요하다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.modules.objects.bulk import MULTI_SEP, NULL_MARK
from app.modules.ontology.services import KEY_RE

#: 열의 역할 — 고정 칸이거나 속성이거나 무시.
ROLES = ("label", "key", "description", "aliases", "property", "ignore")

#: 이 이름의 열은 고정 칸으로 본다(대소문자 무시).
FIXED_HEADERS = {
    "label": "label",
    "name": "label",
    "이름": "label",
    "명칭": "label",
    "title": "label",
    "key": "key",
    "id": "key",
    "code": "key",
    "식별자": "key",
    "코드": "key",
    "description": "description",
    "설명": "description",
    "비고": "description",
    "aliases": "aliases",
    "alias": "aliases",
    "별칭": "aliases",
}

#: 고를 값으로 보는 조건 — 행이 이만큼은 있고, 서로 다른 값이 이 수 이하이며, 행 수의
#: 이 비율 이하.
ENUM_MIN_ROWS = 20
ENUM_MAX_OPTIONS = 12
ENUM_MAX_RATIO = 0.3
LONG_TEXT = 200
SAMPLES = 5

_TRUE = {"true", "yes", "y", "예", "참"}
_FALSE = {"false", "no", "n", "아니오", "거짓"}
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass
class ColumnGuess:
    header: str
    role: str
    key: str
    label: str
    data_type: str
    multi: bool = False
    enum_options: list[str] = field(default_factory=list)
    decimals: int | None = None
    filled: int = 0
    distinct: int = 0
    samples: list[str] = field(default_factory=list)
    note: str = ""
    """왜 이렇게 맞혔나 — 사람이 계획에서 읽고 고칠 근거."""


@dataclass
class Inferred:
    rows: int
    columns: list[ColumnGuess]


def _texts(rows: list[dict[str, Any]], header: str) -> list[str]:
    out: list[str] = []
    for row in rows:
        raw = row.get(header)
        if raw is None:
            continue
        text = str(raw).strip()
        if text == "" or text == NULL_MARK:
            continue
        out.append(text)
    return out


def _is_number(text: str) -> bool:
    return bool(_NUMBER_RE.match(text.replace(",", "")))


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _is_datetime(text: str) -> bool:
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def slug_of(header: str, taken: set[str], fallback_index: int) -> str:
    """헤더를 속성 키로 — 소문자 영숫자·밑줄. 한글 헤더는 규칙에 안 맞으니 `col_N`.
    사람이 계획에서 바꾼다(라벨은 원래 헤더 그대로 남으니 뜻은 안 잃는다)."""
    # 한글이 섞인 헤더(「무게(kg)」)에서 영문 조각만 남기면 「kg」 처럼 뜻이 다른 키가 된다 —
    # 그때는 번호로 두고 사람이 정한다.
    ascii_only = not re.search(r"[^\x00-\x7f]", header)
    base = re.sub(r"[^a-z0-9_]+", "_", header.strip().lower()).strip("_") if ascii_only else ""
    if not base or not base[0].isalpha():
        base = f"col_{fallback_index}"
    base = base[:48]
    candidate = base
    n = 2
    while candidate in taken or not KEY_RE.match(candidate):
        candidate = f"{base[:44]}_{n}"
        n += 1
    taken.add(candidate)
    return candidate


def guess_type(values: list[str], total_rows: int) -> tuple[str, dict[str, Any], str]:
    """(종류, 덧붙일 것, 이유). 값이 하나도 없으면 글자."""
    if not values:
        return "text", {}, "값이 없어 글자로 둡니다"
    lowered = [one.lower() for one in values]
    if all(one in _TRUE | _FALSE for one in lowered):
        return "bool", {}, "예/아니오·true/false 만 있습니다"
    if all(_is_number(one) for one in values):
        decimals = max(
            (len(one.replace(",", "").split(".")[1]) if "." in one else 0) for one in values
        )
        return "number", ({"decimals": decimals} if decimals else {}), "전부 숫자입니다"
    if all(_is_date(one) for one in values):
        return "date", {}, "전부 날짜(YYYY-MM-DD)입니다"
    if all(_is_datetime(one) for one in values):
        return "datetime", {}, "전부 날짜·시각입니다"
    if all(_URL_RE.match(one) for one in values):
        return "url", {}, "전부 http(s) 주소입니다"
    distinct = sorted(set(values))
    if (
        total_rows >= ENUM_MIN_ROWS
        and len(distinct) <= ENUM_MAX_OPTIONS
        and len(distinct) / max(1, len(values)) <= ENUM_MAX_RATIO
    ):
        return (
            "enum",
            {"enum_options": distinct},
            f"서로 다른 값이 {len(distinct)}개뿐입니다 — 고를 값으로 둡니다. "
            "새 값이 올 수 있으면 글자로 바꾸세요",
        )
    if any(len(one) > LONG_TEXT for one in values):
        return "text_long", {}, f"{LONG_TEXT}자를 넘는 값이 있습니다"
    return "text", {}, ""


def infer(
    rows: list[dict[str, Any]],
    *,
    roles: dict[str, str] | None = None,
) -> Inferred:
    """열마다 역할과 종류를 제안한다. `roles` 로 사람이 정한 역할이 있으면 그것을 따른다."""
    headers: list[str] = []
    for row in rows:
        for name in row:
            if name and name not in headers:
                headers.append(name)
    forced = roles or {}
    taken: set[str] = set()
    out: list[ColumnGuess] = []
    label_taken = any(role == "label" for role in forced.values())
    for index, header in enumerate(headers, start=1):
        values = _texts(rows, header)
        role = forced.get(header) or FIXED_HEADERS.get(header.strip().lower(), "property")
        if role == "label" and label_taken and forced.get(header) != "label":
            role = "property"
        if role == "label":
            label_taken = True
        guess = ColumnGuess(
            header=header,
            role=role,
            key="",
            label=header.strip(),
            data_type="text",
            filled=len(values),
            distinct=len(set(values)),
            samples=list(dict.fromkeys(values))[:SAMPLES],
        )
        if role == "property":
            # 여러 값(`;`)이 흔하면 다중 칸 — 항목 하나하나로 종류를 맞힌다.
            multi = sum(MULTI_SEP in one for one in values) >= max(2, len(values) // 5)
            items = (
                [
                    part.strip()
                    for one in values
                    for part in one.split(MULTI_SEP)
                    if part.strip()
                ]
                if multi
                else values
            )
            kind, extra, note = guess_type(items, len(rows))
            guess.key = slug_of(header, taken, index)
            guess.data_type = kind
            guess.multi = multi
            guess.enum_options = list(extra.get("enum_options", []))
            guess.decimals = extra.get("decimals")
            guess.note = note
        out.append(guess)
    if not label_taken:
        # 이름 열이 없으면 첫 글자 열을 이름으로 — 이름 없는 객체는 만들 수 없다.
        for guess in out:
            if (
                guess.role == "property"
                and guess.data_type == "text"
                and guess.filled == len(rows)
            ):
                guess.role = "label"
                guess.note = "이름 열이 없어 이 열을 이름으로 둡니다"
                break
    return Inferred(rows=len(rows), columns=out)


def schema_of(
    inferred: Inferred,
    *,
    slug: str,
    label: str,
    nav_group_slug: str | None,
    key_policy: str,
) -> dict[str, Any]:
    """추론을 `importer` 가 받는 스키마로."""
    properties: list[dict[str, Any]] = []
    for column in inferred.columns:
        if column.role != "property":
            continue
        one: dict[str, Any] = {
            "key": column.key,
            "label": column.label,
            "data_type": column.data_type,
            "multi": column.multi,
        }
        if column.data_type == "enum":
            one["enum_options"] = column.enum_options
        if column.decimals:
            one["decimals"] = column.decimals
        properties.append(one)
    body: dict[str, Any] = {
        "slug": slug,
        "label": label,
        "key_policy": key_policy,
        "properties": properties,
    }
    if nav_group_slug:
        body["nav_group_slug"] = nav_group_slug
    return {"types": [body]}


def rows_of(inferred: Inferred, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """파일의 행을 **파일 가져오기의 행**으로 — 헤더를 역할·키로 바꾼다."""
    out: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {}
        for column in inferred.columns:
            if column.role == "ignore":
                continue
            raw = row.get(column.header)
            if raw is None:
                continue
            text = str(raw).strip() if not isinstance(raw, list | dict) else raw
            if text == "":
                continue
            if column.role == "property":
                value: Any = text
                if column.data_type == "bool" and isinstance(text, str):
                    value = text.lower() in _TRUE
                elif column.data_type == "number" and isinstance(text, str):
                    value = (
                        float(text.replace(",", ""))
                        if "." in text
                        else int(text.replace(",", ""))
                    )
                    if column.multi:
                        value = text
                record[column.key] = value
            else:
                record[column.role] = text
        out.append(record)
    return out
