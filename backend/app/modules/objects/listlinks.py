"""목록 · 통계의 **화면 주소** — 바깥 AI 가 받은 답과 같은 조건의 목록 화면.

MCP 로 목록 · 수를 받은 AI 는 답과 함께 이 주소를 내놓고, 사람은 눌러서 같은 화면에서 그
수를 확인한다. 그러려면 API 가 받은 인자를 **화면이 읽는 주소**로 옮겨야 하는데, 둘은 같지
않다:

- `p.<칸>=값` 은 화면이 안 읽는다 — **뜻이 같을 때만** `f.<칸>.eq=값` 으로 옮긴다. API 의
  `p.` 는 정의를 안 보는 글자 비교(`properties[칸] = 값`)이고, 조건의 `eq` 는 칸의 종류대로
  견준다(여러 값은 「담고 있나」, 숫자는 수로, 참/거짓은 어휘로, `label` · `key` · `status` 는
  고정 칸, 정의가 없으면 422). 둘이 같은 것은 한 값 글자 칸(글 · 긴 글 · 주소 · 고를 값)뿐이다
  — 나머지는 빼고 `missing` 에 `p.<칸>` 을 적는다(2026-11-07 검토: 여러 값 칸에서 답 0건 ↔
  링크 N건이었다).
- 연도 — API 는 안 주면 전부, 화면은 안 주면 **올해**다. 안 줬으면 `year=all` 을 붙인다. 안
  붙이면 답은 몇 해치인데 연 화면은 올해만 보여 두 수가 어긋나고, 사람은 어느 쪽이 맞는지
  모른다.
- 트리에서 고른 가지(`under` · `deep`)는 화면이 주소로 못 받는다(화면 안의 상태다) — 빼고
  `missing` 에 이름을 적는다. 말없이 빼면 사람은 넓어진 목록을 답과 같은 것으로 읽는다.
- 쪽(`limit` · `offset`)과 이어진 칸(`columns`)은 줄 집합을 안 바꾸거나 화면이 1쪽부터 연다 —
  버린다.

값은 `urlencode` 의 기본(빈칸은 `+`)으로 적는다 — 지표 경보의 링크(`metrics.alerts.link_of`)와
같고, 화면은 `URLSearchParams` 로 읽는다. 조건 키는 `conditions.parse` 가 아는 꼴
(`f.<칸>.<연산>` · `nf<n>.` · `fe<n>.`)만 싣는다 — 화면의 `urlConditions` 가 같은 꼴을 읽는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from app.modules.objects import conditions

if TYPE_CHECKING:
    from app.modules.ontology.models import PropertyDef

#: 묶음 조건의 앞머리 — `nf<n>.`(제외) · `fe<n>.`(같은 관계). `conditions.parse` 와 같은 꼴.
_GROUPED = re.compile(
    rf"^(?:{conditions.EXCLUDE_PREFIX}|{conditions.EDGE_PREFIX})[0-9]{{1,2}}\."
)
#: 타입 목록 화면(`TypeListPage`)이 주소에서 읽는 거르기.
_TYPE_KEYS = ("q", "status", "as_of", "types")
#: 인터페이스 목록 화면(`InterfaceListPage`)이 읽는 거르기 — 상태 · 기준일은 안 읽는다.
_INTERFACE_KEYS = ("q", "types")
#: 통계 설정 — `group=1` 과 함께면 타입 목록이 처음 열 때 이것으로 통계를 편다.
SUMMARY_KEYS = ("group_by", "split_by", "metric", "metric_field", "order", "grain")
#: 줄 집합을 안 바꾸거나(이어진 칸) 화면이 1쪽부터 연다(쪽) — 버린다.
_DROPPED = frozenset({"limit", "offset", "columns"})
#: 하나만 받는 인자 — API 는 같은 키가 여럿이면 **마지막 것**을 쓴다(`QueryParams.get`).
#: 화면(`URLSearchParams.get`)은 첫 것을 읽으므로 마지막 것 하나만 싣는다.
_SCALARS = frozenset({*_TYPE_KEYS, *SUMMARY_KEYS, "year", "under", "deep"})
#: `p.<칸>` 의 글자 비교와 조건 `eq` 가 같은 뜻인 칸 종류 — 한 값 글자 칸.
_TEXTUAL = frozenset({"text", "text_long", "url", "enum"})
#: 조건이 속성이 아니라 다른 것으로 읽는 이름 — 고정 칸 · 낡음 · id.
_RESERVED = frozenset({*conditions.FIXED_FIELDS, "lineage", "id"})


def _same_equals(field: str, defs: dict[str, PropertyDef] | None) -> bool:
    """`p.<칸>=값` 을 `f.<칸>.eq=값` 으로 옮겨도 같은 줄이 걸리나."""
    if defs is None or not field or "." in field or field in _RESERVED:
        return False
    one = defs.get(field)
    return one is not None and not one.multi and one.data_type in _TEXTUAL


def is_condition_key(key: str) -> bool:
    """조건 키인가 — `conditions.parse` 가 읽는 것(`f.` · `nf<n>.` · `fe<n>.`)."""
    return key.startswith(conditions.PREFIX) or _GROUPED.match(key) is not None


def _year(raw: str) -> str:
    """해를 화면이 읽는 꼴(네 자리)로 — API 는 `02026` 도 2026 으로 받지만 화면은 못 읽는다."""
    text = raw.strip()
    return str(int(text)) if text.isdigit() else text


def list_link(
    type_slug: str,
    params: Iterable[tuple[str, str]],
    *,
    interface: bool,
    summary: bool = False,
    projection: bool = False,
    defs: Iterable[PropertyDef] | None = None,
) -> tuple[str | None, list[str]]:
    """API 인자(`request.query_params.multi_items()` 그대로) → (화면 주소, 빠진 인자 이름).

    주소는 라우터 기준의 상대 경로(`/o/<slug>?…`)이고, 빠진 인자는 **요청에 있었지만 화면이
    주소로 받지 못해 뺀 것**이다(없으면 빈 목록). `interface` 면 인터페이스 목록 화면이 읽는
    것만(검색어 · 조건 · 타입 좁히기 · 연도), `summary` 면 통계 설정까지(`group=1`),
    `projection`(원 표를 비추는 타입)이면 목록 API 가 검색어만 쓰므로 검색어만 싣는다. `defs`
    는 그 범위의 속성 정의 — `p.<칸>` 을 옮겨도 뜻이 같은지 본다(없으면 옮기지 않는다).
    """
    by_key = None if defs is None else {one.key: one for one in defs}
    pairs = [(str(key), str(value)) for key, value in params]
    base = f"/o/{type_slug}"
    last = dict(pairs)
    if projection:
        q = last.get("q", "")
        return (f"{base}?{urlencode([('q', q)])}" if q else base), []

    readable = _INTERFACE_KEYS if interface else _TYPE_KEYS
    # 「같음」 거르기 — API(`_filtered`)가 키마다 마지막 값 하나를 쓴다.
    equals = {key[2:]: value for key, value in pairs if key.startswith("p.")}
    out: list[tuple[str, str]] = []
    settings: list[tuple[str, str]] = []
    missing: list[str] = []
    seen: set[str] = set()
    seen_fields: set[str] = set()
    for key, value in pairs:
        if is_condition_key(key):
            out.append((key, value))
            continue
        if key.startswith("p."):
            field = key[2:]
            if field not in seen_fields:
                seen_fields.add(field)
                if _same_equals(field, by_key):
                    out.append((f"{conditions.PREFIX}{field}.eq", equals[field]))
                else:
                    missing.append(key)
            continue
        if key in _DROPPED or key not in _SCALARS or key in seen:
            continue
        seen.add(key)
        value = last[key]
        if key == "year":
            out.append((key, _year(value)))
        elif key in readable:
            out.append((key, value))
        elif key in SUMMARY_KEYS:
            # 목록 API 는 통계 설정을 안 읽는다 — 줄을 안 바꾸므로 버린다. 인터페이스 목록
            # 화면은 통계 설정을 주소로 안 받는다.
            if summary and interface:
                missing.append(key)
            elif summary:
                settings.append((key, value))
        elif key == "deep":
            # 가지 없이 온 「아래 것까지」 는 줄을 안 바꾼다 — 빠졌다고 적으면 거짓 경고다.
            if "under" in last:
                missing.append(key)
        else:
            missing.append(key)
    if "year" not in seen:
        out.append(("year", "all"))
    if summary and not interface:
        out += [("group", "1"), *settings]
    return f"{base}?{urlencode(out)}", missing
