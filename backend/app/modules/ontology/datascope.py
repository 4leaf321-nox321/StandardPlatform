"""자료 범위 표지(ADR 0083) — **응답이 어느 범위의 자료로 센 것인가.**

타입 정의의 `scope`(「국내」 · 「해외」 · 「plm」 — 설치가 정하는 말)를 응답마다 모은다. 같은
「시장 서비스」 라도 국내 기록과 해외 기록은 다른 자료이고, 국내 판매로 해외 인입을 나누면
비율이 틀린다. 숫자만 받은 AI 는 그것을 모른다 — 그래서 목록 · 통계 · 지표 · 분석이 모두 같은
칸을 싣는다.

- 관련 타입이 모두 같은 값이면 그 값.
- 값이 둘 이상이면 `mixed` — 무엇이 섞였는지는 `scope_mix`(값마다, 미지정은 `MISSING`).
- 모두 비었으면 `None`(미지정) — 표지를 안 쓰는 설치는 지금과 같다.

미지정과 지정이 섞여도 `mixed` 다. 미지정은 「모름」 이라, 「국내」 하나와 섞이면 국내라고 말할
수 없다.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.ontology.models import ObjectType

MIXED = "mixed"
#: `scope_mix` 에서 미지정 타입을 가리키는 말.
MISSING = "(미지정)"


def clean(value: str | None) -> str:
    """타입 정의에 적을 값 — 앞뒤 공백을 떼고, 안에 든 공백은 하나로. 비우면 미지정."""
    return " ".join((value or "").split())


def tag(types: Iterable[ObjectType | None]) -> tuple[str | None, list[str]]:
    """(scope, scope_mix) — 섞이지 않았으면 `scope_mix` 는 빈 목록."""
    values = sorted({(one.scope or "").strip() for one in types if one is not None})
    if not values or values == [""]:
        return None, []
    if len(values) == 1:
        return values[0], []
    return MIXED, [one or MISSING for one in values]
