"""기록 끝 · 빈 닫힌 기간의 규칙(`query.has_gap` · `query.data_through_of` ·
`query.source_through_of`)과 분석 주의의 말(`recipes.common.gap_note`) — DB 없음.

규칙: (원천) 기록 끝의 다음 기간 시작 < (닫힌 기간의 경계 - 여유). 닫힌 기간의 경계는 워터마크
날짜 - 닫힘 일수다(`query.closed_before`). 여유는 한 기간 — 막 닫힌 기간이 비어 있는 것은 아직
들어오는 중일 수 있다. 일 단위만 닷새(주말 · 설 · 추석 연휴).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef, MetricRun
from app.modules.metrics.recipes import common


def _before(watermark: date, settle_days: int) -> date | None:
    run = MetricRun(
        metric_id=uuid.uuid4(),
        watermark=datetime(watermark.year, watermark.month, watermark.day, 12, tzinfo=UTC),
        stats={},
    )
    return query.closed_before(run, settle_days)


@pytest.mark.parametrize(
    ("through", "watermark", "gap"),
    [
        # 10월이 막 닫힌 날(11-01) — 10월이 비어도 아직 들어오는 중일 수 있다.
        (date(2026, 9, 1), date(2026, 11, 1), False),
        # 하루 더 지나면 빈 구간(10-01 ~ 11-02)이 한 달보다 길다.
        (date(2026, 9, 1), date(2026, 11, 2), True),
        # 10월까지 들어왔으면 11월 말까지도 한 기간 안의 지연이다.
        (date(2026, 10, 1), date(2026, 11, 30), False),
        (date(2026, 10, 1), date(2026, 12, 2), True),
        # 이번 달까지 들어왔으면 당연히 아니다.
        (date(2026, 11, 1), date(2026, 11, 8), False),
    ],
)
def test_월_단위_닫힘_0일(through: date, watermark: date, gap: bool) -> None:
    assert query.has_gap(through, _before(watermark, 0), "month") is gap


@pytest.mark.parametrize(
    ("through", "watermark", "gap"),
    [
        # 닫힘 10일 — 11-08 의 경계는 10-29 라 10월은 아직 안 닫혔다. 9월까지면 정상이다.
        (date(2026, 9, 1), date(2026, 11, 8), False),
        # 8월까지면 닫힌 9월이 통째로 비었다(빈 구간 09-01 ~ 10-29).
        (date(2026, 8, 1), date(2026, 11, 8), True),
        # 9월이 막 닫힌 날(경계 10-01) · 그 다음 날.
        (date(2026, 8, 1), date(2026, 10, 11), False),
        (date(2026, 8, 1), date(2026, 10, 12), True),
    ],
)
def test_닫힘_일수만큼_경계가_당겨진다(through: date, watermark: date, gap: bool) -> None:
    assert query.has_gap(through, _before(watermark, 10), "month") is gap


def test_주는_한_기간을_넘겨_준다() -> None:
    monday = date(2026, 11, 2)
    week_after = monday + timedelta(days=7)
    # 주 — 기록 끝이 지난주(10-26 주)면 이번 주 월요일(11-02)이 막 닫힌 경계다.
    assert query.has_gap(monday - timedelta(days=7), week_after, "week") is False
    assert query.has_gap(monday - timedelta(days=7), week_after + timedelta(days=1), "week")


@pytest.mark.parametrize(
    ("through", "before", "gap"),
    [
        # 주말 — 금요일(11-06)까지 들어오고 월요일(11-09) 새벽 계산: 빈 닫힌 날 이틀.
        (date(2026, 11, 6), date(2026, 11, 9), False),
        # 월 ~ 수 사흘 연휴가 주말에 붙은 주(토 ~ 수 닷새) — 다음 날 새벽 · 아침의 손
        # 재계산에도 아직 아니다.
        (date(2026, 9, 18), date(2026, 9, 24), False),
        # 엿새째 빈 닫힌 날부터 멎음이다.
        (date(2026, 9, 18), date(2026, 9, 25), True),
        # 어제까지 들어온 것은 당연히 아니다.
        (date(2026, 11, 7), date(2026, 11, 8), False),
    ],
)
def test_일_단위는_주말_연휴만큼_넘겨_준다(through: date, before: date, gap: bool) -> None:
    assert query.DAY_SLACK == 5
    assert query.has_gap(through, before, "day") is gap


def test_모르는_것은_빈_것이_아니다() -> None:
    assert query.has_gap(None, date(2027, 1, 1), "month") is False
    assert query.has_gap(date(2020, 1, 1), None, "month") is False
    assert query.has_gap(date(2020, 1, 1), date(2027, 1, 1), None) is False


def test_실행의_기록_끝을_읽는다() -> None:
    def run(stats: dict[str, object]) -> MetricRun:
        return MetricRun(metric_id=uuid.uuid4(), stats=stats)

    assert query.data_through_of(None) is None
    assert query.data_through_of(run({})) is None  # 이 칸이 생기기 전의 실행
    assert query.data_through_of(run({"data_through": "2026-09-01"})) == date(2026, 9, 1)
    assert query.data_through_of(run({"data_through": "망가짐"})) is None
    assert query.data_through_of(run({"data_through": 20260901})) is None
    # 원천 기록 끝은 따로 — 지표의 기록 끝만 있는 실행은 원천 쪽을 모른다.
    assert query.source_through_of(run({"data_through": "2026-09-01"})) is None
    assert query.source_through_of(run({"source_through": "2026-10-01"})) == date(2026, 10, 1)


# --- 분석 주의의 말 ------------------------------------------------------------------------

_WATERMARK = datetime(2026, 11, 12, 12, tzinfo=UTC)
_TAIL = "으므로, 데이터 소스의 수집 현황을 확인해야 합니다."


def _run_with(**stats: Any) -> MetricRun:
    return MetricRun(metric_id=uuid.uuid4(), watermark=_WATERMARK, stats=stats)


def _built(*, stay: bool = False) -> spec_module.Built:
    """`gap_note` 가 읽는 것만 — 기간 단위와 머무는 기간이 있나."""
    return cast(
        spec_module.Built, SimpleNamespace(grain="month", stay=object() if stay else None)
    )


def _den(
    *, time: str | None, source_through: str, period_to: date | None
) -> query.Denominator:
    run = _run_with(source_through=source_through)
    return query.Denominator(
        metric=MetricDef(slug="units", label="판매 대수"),
        spec=spec_module.MetricSpec.model_validate(
            {"time": {"address": "properties.month", "grain": "month"}}
        ),
        on=[],
        time=time,
        per=1.0,
        values={},
        truncated=False,
        run=run,
        before=query.closed_before(run, 0),
        period_to=period_to,
    )


def _note(
    built: spec_module.Built,
    run: MetricRun,
    *,
    period_to: date | None = None,
    den: query.Denominator | None = None,
) -> str | None:
    frame = query.Frame([], False, den, run, query.closed_before(run, 0), period_to=period_to)
    caveats = common.Caveats()
    common.gap_note(built, frame, caveats)
    found = [one for one in caveats.out() if one.code == "data_gap"]
    return found[0].message if found else None


def test_주의는_원천_기록_끝으로_판정하고_지표의_끝은_안_쓴다() -> None:
    # 지표의 끝은 3월이어도 원천이 10월까지면 11-12 에 빈 기간이 아니다.
    alive = _run_with(data_through="2026-03-01", source_through="2026-10-01")
    assert _note(_built(), alive) is None
    stopped = _run_with(data_through="2026-03-01", source_through="2026-08-01")
    assert _note(_built(), stopped) == (
        "2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) — 이 구간의 0건 · "
        "감소는 수집 지연 때문일 수 있" + _TAIL
    )


def test_머무는_기간_지표는_0건이_아니라_새_기록이_빠졌다고_말한다() -> None:
    assert _note(_built(stay=True), _run_with(source_through="2026-08-01")) == (
        "2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) — 이 구간의 값에는 "
        "새 기록이 빠졌을 수 있" + _TAIL
    )


def test_읽은_범위가_원천_기록_끝_안에서_끝나면_말하지_않는다() -> None:
    run = _run_with(source_through="2026-08-01")
    # 「앞까지」 9월 1일 = 8월까지 — 보여 준 기간은 모두 기록 끝 안이다.
    assert _note(_built(), run, period_to=date(2026, 9, 1)) is None
    assert _note(_built(), run, period_to=date(2026, 5, 1)) is None
    assert _note(_built(), run, period_to=date(2026, 10, 1)) is not None


def test_분모를_기간으로_짝짓지_않으면_분모_합을_말한다() -> None:
    alive = _run_with(source_through="2026-10-01")
    paired = _den(time="period", source_through="2026-08-01", period_to=None)
    assert _note(_built(), alive, den=paired) == (
        "분모 「판매 대수」 의 2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) "
        "— 이 구간의 비율은 분모가 빠졌거나 덜 반영된 값일 수 있" + _TAIL
    )
    loose = _den(time=None, source_through="2026-08-01", period_to=None)
    assert _note(_built(), alive, den=loose) == (
        "분모 「판매 대수」 의 2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) "
        "— 분모 합이 덜 반영되었을 수 있" + _TAIL
    )
    # 분모를 읽은 범위가 분모의 원천 기록 끝 안이면 그쪽도 말하지 않는다.
    ranged = _den(time=None, source_through="2026-08-01", period_to=date(2026, 9, 1))
    assert _note(_built(), alive, den=ranged) is None
    # 둘 다 비면 한 주의에 두 문장 — 앞쪽은 「…수 있습니다.」 로 끝내고 할 일은 끝에 한 번.
    both = _note(_built(), _run_with(source_through="2026-08-01"), den=paired)
    assert both == (
        "2026-09 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-08) — 이 구간의 0건 · "
        "감소는 수집 지연 때문일 수 있습니다. 분모 「판매 대수」 의 2026-09 이후는 미반영 "
        "구간입니다(원천 최종 반영 기간 2026-08) — 이 구간의 비율은 분모가 빠졌거나 덜 반영된 "
        "값일 수 있" + _TAIL
    )
