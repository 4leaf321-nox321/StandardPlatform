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
