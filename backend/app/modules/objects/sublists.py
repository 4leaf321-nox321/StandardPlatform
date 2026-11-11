"""부분 목록 — **다른 목록의 결과를 조건의 값으로**.

    ?f.id.under=@1&s1.type=model&s1.f.out.sold_in.eq=<EU id>

「EU 에 파는 모델에 들어간 부품 전부」 를 예전에는 모델 목록(2,030개)을 받아 그 id 를 다시
주소에 넣었다 — 주소가 4만 ~ 7만 자가 되어 실제 HTTP 에서 끊기고(합성 견주기 2026-11-11), 그
사이에 id 수천 개가 AI 의 맥락을 지나간다. 부분 목록은 서버가 **같은 요청 안에서** 그 목록을
세우고 값으로 쓴다.

- `s<번호>.type=<slug>`(타입 또는 인터페이스, 꼭) · `s<번호>.q` · `s<번호>.status` ·
  `s<번호>.p.<칸>` · `s<번호>.f.…` / `nf…` / `fe…` — 목록과 **같은 거르기**다(같은 함수로
  세운다).
- 값 `@<번호>` 는 id 를 값으로 받는 연산에만 — `in`(그 중 하나) · `under`(하위 전체) ·
  `above`(위 전부). 자리는 참조 칸 · 관계 자체 · 나를 가리키는 것 · 목록의 객체 자신(`id`).
- 보는 사람이 볼 수 있는 것만 든다(목록과 같다). `MAX_IDS` 를 넘으면 좁히라고 말한다.
- 부분 목록 안에 부분 목록은 아직 안 받는다.
"""

from __future__ import annotations

import re
from dataclasses import replace

from app.modules.objects.conditions import Condition
from app.modules.ontology.services import InvalidValue
from app.shared.errors import code

PREFIX = "s"
_KEY = re.compile(r"^s([0-9]{1,2})\.(.+)$")
_REF = re.compile(r"^@([0-9]{1,2})$")
#: 부분 목록 하나의 상한 — 그 id 를 배열 하나로 넘긴다(`graph.any_of`).
MAX_IDS = 100_000
#: `@<번호>` 를 받는 연산 — id 를 값으로 받는 것.
OPS = ("in", "under", "above")


def reference(value: str) -> int | None:
    """조건 값이 `@<번호>` 면 그 번호."""
    found = _REF.match(value.strip())
    return int(found.group(1)) if found else None


def is_key(key: str) -> bool:
    return _KEY.match(key) is not None


def collect(pairs: list[tuple[str, str]]) -> dict[int, list[tuple[str, str]]]:
    """`s<번호>.<키>` 를 번호마다 앞머리를 뗀 (키, 값)으로."""
    out: dict[int, list[tuple[str, str]]] = {}
    for key, value in pairs:
        found = _KEY.match(key)
        if found is None:
            continue
        number, rest = int(found.group(1)), found.group(2)
        if _KEY.match(rest) is not None:
            raise InvalidValue(
                code("OBJECTS", 153),
                f"부분 목록 안에 부분 목록은 아직 못 넣습니다: {key}",
            )
        out.setdefault(number, []).append((rest, value))
    return out


def wanted(asked: list[Condition]) -> set[int]:
    """조건들이 부르는 부분 목록 번호 — 연산도 여기서 본다."""
    out: set[int] = set()
    for one in asked:
        number = reference(one.value)
        if number is None:
            continue
        if one.op not in OPS:
            raise InvalidValue(
                code("OBJECTS", 153),
                f"{one.field}: 부분 목록(@{number})은 {' · '.join(OPS)} 에만 값으로 씁니다 — "
                f"「{one.op}」 에는 못 씁니다.",
            )
        out.add(number)
    return out


def substitute(asked: list[Condition], found: dict[int, list[str]]) -> list[Condition]:
    """`@<번호>` 를 그 목록의 id 들(`|`)로 — 조건 쪽은 값 여럿과 같은 길을 탄다."""
    out: list[Condition] = []
    for one in asked:
        number = reference(one.value)
        out.append(one if number is None else replace(one, value="|".join(found[number])))
    return out


def too_many(number: int, type_label: str) -> InvalidValue:
    return InvalidValue(
        code("OBJECTS", 153),
        f"부분 목록 @{number}({type_label})이 {MAX_IDS:,}개를 넘습니다 — 그 목록의 조건을 "
        "좁히세요.",
    )


def missing_type(number: int) -> InvalidValue:
    return InvalidValue(
        code("OBJECTS", 153),
        f"부분 목록 @{number} 의 타입이 없습니다 — s{number}.type=<타입 slug> 를 함께 "
        "보냅니다.",
    )
