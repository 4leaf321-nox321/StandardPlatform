"""부분군의 줄 — 기간 축 또는 코호트 창의 건수 · 대수 · 닫힘(관리도 · 변화점이 함께 쓴다).

- **기간 축** — 접수 기간마다의 건수. 분모가 기간과 짝(`time=period`)이면 그 기간의 대수.
- **코호트 축** — 생산 · 판매월 코호트마다 **출고 K 기간 안**의 건수와 그 달의 대수(분모가
  코호트와 짝일 때). 창의 마지막 경과(K-1)가 닫힌 코호트만 닫혔다 — 같은 창으로 재야 늦게
  만든 달이 덜 들어온 것을 품질로 읽지 않는다.

분모가 그 축과 짝이 아니면 대수 없이 건수로 본다(`exposure` = 1) — 그렇게 말한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes import common
from app.modules.metrics.schemas import DrillOut
from app.modules.objects import axes

Axis = Literal["period", "cohort"]
#: 창의 길이를 말할 때의 단위 — 「3개월」 · 「2분기」.
SPAN = {"day": "일", "week": "주", "month": "개월", "quarter": "분기", "year": "년"}


@dataclass
class Subgroup:
    when: date
    count: int
    exposure: float
    """대수 — 분모가 없으면 1(건수로 본다)."""
    closed: bool


@dataclass
class Series:
    key: str | None
    label: str
    subgroups: list[Subgroup]
    total: int


@dataclass
class SeriesSet:
    axis: Axis
    grain: str
    window: int
    split: str | None
    split_dim: spec_module.Dim | None
    den: query.Denominator | None
    frame: query.Frame
    ask: query.Ask
    series: list[Series]
    other_groups: int
    excluded: dict[str, int]

    @property
    def per(self) -> float:
        return self.den.per if self.den is not None else 1.0

    def rate(self, subgroup: Subgroup) -> float | None:
        if subgroup.exposure <= 0:
            return None
        return subgroup.count / subgroup.exposure * self.per


def default_axis(built: spec_module.Built) -> Axis:
    """분모가 코호트와 짝이면 코호트 축(출고 K 기간 안 비율), 아니면 기간 축."""
    den = built.spec.denominator
    paired = den is not None and den.time == "cohort"
    if built.cohort is not None and built.time is not None and paired:
        return "cohort"
    return "period" if built.time is not None else "cohort"


def _axis(built: spec_module.Built, axis: Axis | None) -> tuple[Axis, str]:
    axis = axis or default_axis(built)
    if axis == "cohort" and (built.cohort is None or built.time is None):
        raise common.refuse(
            26,
            "코호트 축은 코호트 칸(생산 · 판매일)과 시간 칸(접수일)이 함께 있어야 「출고 K "
            "기간 안」 을 셉니다 — axis=period 로 봅니다.",
        )
    if axis == "period" and built.time is None:
        raise common.refuse(26, "시간 칸이 없는 지표입니다 — axis=cohort 로 봅니다.")
    axis_spec = built.cohort if axis == "cohort" else built.time
    assert axis_spec is not None
    return axis, axis_spec.grain


def read(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    axis: Axis | None,
    window: int,
    split: str | None,
    limit: int,
    shared: bool = False,
) -> SeriesSet:
    """셀을 읽어 기준 값마다(나누지 않으면 하나) 부분군의 줄을 만든다 — 건수 많은 순 `limit`
    개. 빈 기간은 0 건이다(대수가 있으면 진짜 관측이다)."""
    axis, grain = _axis(built, axis)
    den_in = built.spec.denominator
    uses_den = den_in is not None and den_in.time == axis
    target = common.dim_of(built, split) if split is not None else None
    # `shared` — 값마다 훑기: 대수를 나누지 않는 기준(증상)은 같은 대수로 나눈다(분모가 그
    # 기준을 모르면 그대로 펼쳐진다 — `read_denominator`).
    if (
        target is not None
        and uses_den
        and den_in is not None
        and split not in den_in.on
        and not shared
    ):
        raise common.refuse(
            26,
            f"「{target.axis.label}」 로 나누면 분모도 그 기준으로 나뉘어야 합니다 — 분모 짝"
            f"(on)에 없습니다. 거르기(d.{split}=값)로 하나씩 보거나, 분모 정의의 짝에 그 "
            "기준을 넣습니다.",
        )
    dims = [split] if split is not None else []
    if axis == "cohort":
        ask = replace(ask, dims=dims, by=("cohort", "age"), age_from=0, age_to=window)
    else:
        ask = replace(ask, dims=dims, by=("period",))
    common.require_exact_counts(built, ask)
    frame = query.frame(db, user, metric, built, ask, with_denominator=uses_den)
    common.require_whole(frame)
    den = frame.denominator if uses_den else None
    excluded = {"missing_denominator": 0, "open": 0}

    grouped: dict[str | None, dict[date, int]] = {}
    for cell in frame.cells:
        when = cell.cohort if axis == "cohort" else cell.period
        if when is None:
            continue
        key = cell.dims.get(split) if split is not None else None
        bucket = grouped.setdefault(key, {})
        bucket[when] = bucket.get(when, 0) + cell.count
    keys = sorted(grouped, key=lambda one: -sum(grouped[one].values()))
    other_groups = max(0, len(keys) - limit)
    keys = keys[:limit]
    every = sorted({when for bucket in grouped.values() for when in bucket})
    timeline = (
        query.dense(every[0], axes.next_period(every[-1], grain), grain) if every else []
    )
    labels = query.labels_for(db, built, [split], frame.cells) if split is not None else {}
    found = SeriesSet(
        axis=axis,
        grain=grain,
        window=window,
        split=split,
        split_dim=target,
        den=den,
        frame=frame,
        ask=ask,
        series=[],
        other_groups=other_groups,
        excluded=excluded,
    )
    for key in keys:
        bucket = grouped[key]
        found.series.append(
            Series(
                key=key,
                label=query.label_of(labels, split, key) if split is not None else "전체",
                subgroups=_subgroups(found, bucket, timeline, key),
                total=sum(bucket.values()),
            )
        )
    return found


def _subgroups(
    found: SeriesSet, bucket: dict[date, int], timeline: list[date], key: str | None
) -> list[Subgroup]:
    out: list[Subgroup] = []
    den, grain, axis = found.den, found.grain, found.axis
    for when in timeline:
        count = bucket.get(when, 0)
        exposure = 1.0
        den_closed = True
        if den is not None:
            units = den.lookup(_cell(found, key, when, count), grain)
            if not units:
                found.excluded["missing_denominator"] += count
                continue
            exposure = float(units)
            den_closed = query.is_closed(when, grain, den.before)
        last = query.advance(when, found.window - 1, grain) if axis == "cohort" else when
        closed = query.is_closed(last, grain, found.frame.before) and den_closed
        if not closed:
            found.excluded["open"] += count
        out.append(Subgroup(when, count, exposure, closed))
    return out


def _cell(found: SeriesSet, key: str | None, when: date, count: int) -> query.Cell:
    return query.Cell(
        {found.split: key} if found.split is not None else {},
        when if found.axis == "period" else None,
        when if found.axis == "cohort" else None,
        None,
        count,
        0,
        None,
        None,
        None,
    )


@dataclass
class Totals:
    """값마다 닫힌 부분군의 합 — 집단 비교. `read` 와 같은 줄(첫 기록부터 마지막 기록까지의
    부분군 · 닫힘 · 대수 없는 부분군 빼기)을 펴지 않고 DB 에서 더한다: 집단이 수천이어도 읽기
    한 번에 집단 수만큼의 줄이다. 기록이 하나도 없는 값도 대수가 있으면 0 건으로 든다."""

    axis: Axis
    grain: str
    window: int
    den: query.Denominator
    frame: query.Frame
    ask: query.Ask
    """닫힌 부분군의 범위로 좁힌 읽기 — 건 보기가 쓴다."""
    groups: list[query.Paired]
    excluded: dict[str, int]
    other_groups: int = 0

    @property
    def per(self) -> float:
        return self.den.per


def totals(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: query.Ask,
    *,
    axis: Axis | None,
    window: int,
    split: str,
) -> Totals | None:
    """기준 값마다 닫힌 부분군의 건수 · 대수 합. 분모가 이 축과 짝이 아니면 None — 대수 없는
    합은 견줄 수 없다."""
    axis, grain = _axis(built, axis)
    den_in = built.spec.denominator
    den_metric = built.denominator
    if den_in is None or den_metric is None or den_in.time != axis:
        return None
    if axis == "cohort":
        ask = replace(ask, dims=[], by=("cohort",), age_from=0, age_to=window)
    else:
        ask = replace(ask, dims=[], by=("period",))
    common.require_exact_counts(built, replace(ask, dims=[split]))
    # 부분군의 줄은 값을 가리지 않은 첫 기록부터 마지막 기록까지 — 기간 수만큼의 셀이다.
    frame = query.frame(db, user, metric, built, ask, with_denominator=False)
    common.require_whole(frame)
    whens: set[date] = set()
    for cell in frame.cells:
        when = cell.cohort if axis == "cohort" else cell.period
        if when is not None:
            whens.add(when)
    den_spec = spec_module.MetricSpec.model_validate(den_metric.spec)
    den_run = query.current_run(db, den_metric)
    den = query.Denominator(
        den_metric,
        den_spec,
        [split],
        axis,
        den_in.per,
        {},
        False,
        run=den_run,
        before=query.closed_before(den_run, den_spec.settle_days),
    )
    found = Totals(
        axis=axis,
        grain=grain,
        window=window,
        den=den,
        frame=replace(frame, denominator=den),
        ask=ask,
        groups=[],
        excluded={"missing_denominator": 0, "open": 0},
    )
    if not whens:
        return found
    first, end = min(whens), axes.next_period(max(whens), grain)
    # 닫힘은 시간에 따라 한 번만 바뀐다(앞은 닫히고 뒤는 열린다) — 처음 열린 부분군에서 끊는다.
    cut = first
    while cut < end and _whole(found, cut):
        cut = axes.next_period(cut, grain)
    if axis == "cohort":
        stop = min(end, ask.cohort_to) if ask.cohort_to is not None else end
        found.ask = replace(ask, cohort_from=first, cohort_to=stop)
    else:
        stop = min(end, ask.period_to) if ask.period_to is not None else end
        found.ask = replace(ask, period_from=first, period_to=stop)
    found.groups, den.truncated = query.paired_totals(
        db, user, metric, built, found.ask, dim=split, when=axis, closed_to=cut
    )
    common.require_whole(found.frame)
    found.excluded["missing_denominator"] = sum(one.missing for one in found.groups)
    found.excluded["open"] = sum(one.open for one in found.groups)
    # 건 보기는 닫힌 부분군만 — 열린 부분군의 기록은 수에 안 들었다.
    if axis == "cohort":
        found.ask = replace(found.ask, cohort_to=min(cut, stop))
    else:
        found.ask = replace(found.ask, period_to=min(cut, stop))
    return found


def group_drill(
    found: Totals, built: spec_module.Built, key: str | None, count: int
) -> DrillOut:
    """값 하나의 근거 — 닫힌 부분군의 범위. 코호트 축의 「출고 K 기간 안」 은 코호트마다 접수
    범위가 달라 목록 조건 하나로 못 적는다(`age` — 목록이 더 많을 수 있다)."""
    cell = query.Cell({found.den.on[0]: key}, None, None, None, count, 0, None, None, None)
    out = query.drill(built, cell, by=(), ask=found.ask)
    if found.axis == "cohort":
        out.partial.append("age")
    return out


def _whole(found: Totals, when: date) -> bool:
    """부분군이 닫혔나 — 분자(코호트 축이면 창의 마지막 경과까지)와 분모가 함께."""
    last = (
        query.advance(when, found.window - 1, found.grain) if found.axis == "cohort" else when
    )
    return query.is_closed(last, found.grain, found.frame.before) and query.is_closed(
        when, found.grain, found.den.before
    )


def drill(
    found: SeriesSet, built: spec_module.Built, key: str | None, subgroup: Subgroup
) -> DrillOut:
    """부분군의 근거 — 기간 축이면 그 기간, 코호트 축이면 그 코호트의 창 안 접수(읽을 때 건
    접수 범위와 겹친다)."""
    cell = _cell(found, key, subgroup.when, subgroup.count)
    ask = found.ask
    if found.axis == "period":
        return query.drill(built, cell, by=("period",), ask=ask)
    end = query.advance(subgroup.when, found.window, found.grain)
    start = (
        max(subgroup.when, ask.period_from) if ask.period_from is not None else subgroup.when
    )
    stop = min(end, ask.period_to) if ask.period_to is not None else end
    ranged = replace(ask, period_from=start, period_to=stop)
    return query.drill(built, cell, by=("cohort",), ask=ranged)


def caveats(found: SeriesSet | Totals, caveats_: common.Caveats) -> None:
    """줄을 읽으며 생긴 주의 — 건수로 봄 · 분모 없음 · 열림 · 창."""
    if found.den is None:
        caveats_.add(
            "count_basis",
            "분모(대수)가 이 축과 짝지어져 있지 않아 건수로 봅니다 — 판매 · 생산이 늘면 "
            "건수도 늘어 신호처럼 보입니다.",
        )
    if found.excluded["missing_denominator"]:
        caveats_.add(
            "missing_denominator",
            "대수가 없는 부분군의 기록을 뺐습니다.",
            count=found.excluded["missing_denominator"],
        )
    if found.excluded["open"]:
        caveats_.add(
            "open_excluded",
            "아직 닫히지 않은 부분군은 그리되 계산에서 뺐습니다 — 더 들어올 수 있습니다.",
            level="info",
            count=found.excluded["open"],
        )
    if found.axis == "cohort":
        caveats_.add(
            "window_basis",
            f"코호트마다 출고 뒤 {found.window}{SPAN.get(found.grain, found.grain)} 안의 "
            "건수입니다 — 그 창이 닫힌 코호트만 계산에 씁니다.",
            level="info",
        )
    if found.other_groups:
        caveats_.add(
            "other_groups",
            f"건수가 적은 {found.other_groups}개 값은 싣지 않았습니다 — 거르기로 봅니다.",
            level="info",
            count=found.other_groups,
        )
