"""지표 읽기 요청의 쿼리 파라미터 — 표 · 추이 · 코호트와 분석(ADR 0014)이 **같은 규칙**으로
읽는다.

기준 값 거르기는 `d.<기준 이름>=<값>`(빈 값은 「(비어 있음)」), 기간 · 코호트 범위는
`YYYY-MM-DD` 의 「앞까지」 다. 따로 읽으면 화면의 표와 분석이 다른 거르기를 보고, 그 차이는
아무 데도 안 뜬다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from fastapi import Request

from app.modules.metrics import query
from app.shared.errors import AppError, code

#: 기준 값으로 거르는 쿼리 파라미터 — `d.<기준 이름>=<값>`, 빈 값은 「(비어 있음)」.
FILTER_PREFIX = "d."


def parse_date(raw: str | None, what: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as caught:
        raise AppError(
            code("METRICS", 11), f"{what}은(는) YYYY-MM-DD 여야 합니다: {raw}", status=422
        ) from caught


def names(raw: str | None) -> list[str]:
    return [one.strip() for one in (raw or "").split(",") if one.strip()]


def filters_from(items: Iterable[tuple[str, str]]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, value in items:
        if key.startswith(FILTER_PREFIX):
            out[key[len(FILTER_PREFIX) :]] = value if value != "" else None
    return out


def filters_of(request: Request) -> dict[str, str | None]:
    return filters_from(request.query_params.multi_items())


#: 지표 읽기(표 · 추이 · 코호트 · 기준 값)가 받는 쿼리 이름 전부 — 모양마다 그중 일부만 쓴다.
#: `filters` 는 `d.<기준>` 거르기를 가리킨다.
READ_PARAMS = (
    "dims",
    "by",
    "filters",
    "period_from",
    "period_to",
    "cohort_from",
    "cohort_to",
    "split",
    "cumulative",
    "composition",
    "name",
    "q",
)

#: 켜고 끄는 인자 — 끈 값(`false`)은 준 것으로 보지 않는다(쓰든 안 쓰든 답이 같다).
_SWITCHES = ("cumulative", "composition")


def _given(request: Request) -> list[str]:
    """요청에 **값을 준** 읽기 인자의 이름 — 빈 값 · 끈 스위치는 뺀다. `d.<기준>` 은 빈 값도
    거르기(「(비어 있음)」)라 준 것이다."""
    out: list[str] = []
    for key, value in request.query_params.multi_items():
        if key.startswith(FILTER_PREFIX):
            name = "filters"
        elif key in READ_PARAMS:
            text = value.strip().lower()
            if not text or (key in _SWITCHES and text in ("false", "0")):
                continue
            name = key
        else:
            continue
        if name not in out:
            out.append(name)
    return out


def refuse_unused(request: Request, shape: str, used: Iterable[str]) -> None:
    """이 모양이 **쓰지 않는 인자**가 오면 422 — 조용히 버리면 거르기 · 범위가 안 걸린 수를
    읽는 쪽(사람이든 AI 든)이 「걸렀다」 고 믿는다. 추이에 `cohort_from` 을, 기준 값에
    `d.<기준>` 을 주던 MCP `metric_query` 가 그렇게 안 걸러진 수를 받았다(2026-11-28)."""
    allowed = set(used)
    unused = [name for name in _given(request) if name not in allowed]
    if not unused:
        return
    shown = ["d.<기준>" if name == "filters" else name for name in unused]
    accepted = [
        "d.<기준>" if name == "filters" else name for name in READ_PARAMS if name in allowed
    ]
    raise AppError(
        code("METRICS", 13),
        f"{shape} 읽기에는 사용하지 않는 인자입니다: {', '.join(shown)} — 무시하면 걸러지지 "
        "않은 수가 걸러진 수로 읽히므로 거절합니다. 이 인자를 제외하고 다시 요청합니다(이 "
        f"읽기가 사용하는 인자: {', '.join(accepted) or '(없음)'}).",
        status=422,
        details={"shape": shape, "unused": shown, "accepted": accepted},
    )


def ask_from_request(
    request: Request,
    *,
    dims: str | None = None,
    by: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    cohort_from: str | None = None,
    cohort_to: str | None = None,
) -> query.Ask:
    return query.Ask(
        dims=names(dims),
        by=tuple(names(by)),
        filters=filters_of(request),
        period_from=parse_date(period_from, "period_from"),
        period_to=parse_date(period_to, "period_to"),
        cohort_from=parse_date(cohort_from, "cohort_from"),
        cohort_to=parse_date(cohort_to, "cohort_to"),
    )


def ask_from_values(values: Mapping[str, str]) -> query.Ask:
    """저장해 둔 쿼리(경보의 인자, ADR 0016)에서 — 요청과 **같은 규칙**으로."""
    return query.Ask(
        filters=filters_from(values.items()),
        period_from=parse_date(values.get("period_from"), "period_from"),
        period_to=parse_date(values.get("period_to"), "period_to"),
        cohort_from=parse_date(values.get("cohort_from"), "cohort_from"),
        cohort_to=parse_date(values.get("cohort_to"), "cohort_to"),
    )
