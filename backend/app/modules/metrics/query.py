"""읽기 — **셀 위에서** 비율 · 누적 · 비교 · 건 보기.

저장된 것은 기초 집계뿐이다. 분모 결합 · 경과 누적 · 전기 · 전년 동기는 상한(설정
`metrics_max_read_cells`) 안의 셀을 받은 뒤 Python 에서 낸다 — 셀은 기록보다 수백 배 적고,
동적 창 함수 SQL 보다 단순하며 시험이 쉽다.

## 한 문장 = 한 스냅샷

`run_id = (SELECT current_run_id …)` 를 같은 문장에 넣는다. 바꿔 끼우는 트랜잭션과 겹쳐도
문장 하나는 한 스냅샷을 보므로 옛 실행과 새 실행이 섞이지 않는다.

## 보이는 것만 더한다

셀은 기록의 소유 부서별 부분합이라 `visible_owner_clause` 로 거른 뒤 더한다 — 목록 · 통계와
같은 규칙. `count` · `sum` · `min` · `max` 는 더해지고, 평균은 `sum / value_count` 로 다시
낸다.

## 숫자마다 근거로 돌아간다

셀마다 `drill`(그 수를 이룬 기록의 목록 조건)을 붙인다 — 기간은 버킷 시작일의 범위, 기준은
같은 주소의 `eq`. 조건으로 못 적는 축(부서 · 못 읽은 날짜)은 `partial` 에 적는다.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.metrics import schemas
from app.modules.metrics import spec as spec_module
from app.modules.metrics import visits as visits_module
from app.modules.metrics.models import MetricDef, MetricRun, MetricValue
from app.modules.objects import axes
from app.modules.objects.summary import METRIC_LABELS
from app.shared.errors import AppError, code
from app.shared.permissions import visible_owner_clause

#: 추이에서 세부 기준의 선 수 상한 — 그보다 많으면 색이 겹쳐 못 읽는다.
MAX_LINES = 24
#: 기준 값의 이름을 한 번에 푸는 상한.
MAX_LABELS = 5000
#: 기준 값 목록(`/dims`)의 상한.
MAX_DIM_VALUES = 200
#: 셀의 진짜 칸 — 요청의 `by` 가 고른다.
BY_KEYS = ("period", "cohort", "age")


@dataclass
class Ask:
    """읽기 한 번의 요청."""

    dims: list[str] = field(default_factory=list)
    """묶을 기준 이름들(정의의 부분집합)."""
    by: tuple[str, ...] = ()
    """셀의 진짜 칸 중 묶을 것 — `period` · `cohort` · `age`."""
    filters: dict[str, str | None] = field(default_factory=dict)
    """기준 값으로 거르기. None 은 「(비어 있음)」."""
    period_from: date | None = None
    period_to: date | None = None
    cohort_from: date | None = None
    cohort_to: date | None = None
    age_from: int | None = None
    age_to: int | None = None
    """경과 범위 — 「앞까지」. 관리도 · 순차 검정은 K 경과 앞까지만 본다(셀 상한 안에 들게)."""


@dataclass
class Cell:
    dims: dict[str, str | None]
    period: date | None
    cohort: date | None
    age: int | None
    count: int
    value_count: int
    sum: float | None
    min: float | None
    max: float | None

    @property
    def avg(self) -> float | None:
        if self.sum is None or not self.value_count:
            return None
        return self.sum / self.value_count

    def measure(self, name: str) -> float | None:
        if name == "count":
            return float(self.count)
        if name == "sum":
            return self.sum
        if name == "avg":
            return self.avg
        if name == "min":
            return self.min
        return self.max


def _bad(number: int, message: str) -> AppError:
    return AppError(code("METRICS", number), message, status=422)


# --- 기간 셈 -----------------------------------------------------------------------


def _add_months(start: date, months: int) -> date:
    total = start.year * 12 + (start.month - 1) + months
    year, month = divmod(total, 12)
    day = min(start.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def advance(start: date, steps: int, grain: str) -> date:
    """코호트 시작일에서 `steps` 기간 뒤 — 코호트 x 경과 셀의 기간."""
    if grain == "day":
        return start + timedelta(days=steps)
    if grain == "week":
        return start + timedelta(days=7 * steps)
    return _add_months(start, steps * {"month": 1, "quarter": 3, "year": 12}[grain])


def year_before(start: date, grain: str) -> date:
    """한 해 전 같은 기간의 시작일 — 전년 동기. 주는 52주 전(요일이 맞는다)."""
    if grain == "week":
        return start - timedelta(days=364)
    if grain == "year":
        return date(start.year - 1, 1, 1)
    return _add_months(start, -12)


def dense(start: date, stop: date, grain: str) -> list[date]:
    """`start` 부터 `stop` **직전**까지의 기간 시작일 — 빈 기간을 0 으로 채우려고."""
    out: list[date] = []
    current = start
    while current < stop and len(out) < 10_000:
        out.append(current)
        current = axes.next_period(current, grain)
    return out


def _iso(when: date | None) -> str | None:
    return when.isoformat() if when is not None else None


# --- 셀 읽기 ---------------------------------------------------------------------


def read(db: Session, user: User, metric: MetricDef, ask: Ask) -> tuple[list[Cell], bool]:
    """보이는 셀을 요청한 축으로 묶어 — (셀들, 잘렸나)."""
    limit = get_settings().metrics_max_read_cells
    current = (
        select(MetricDef.current_run_id).where(MetricDef.id == metric.id).scalar_subquery()
    )
    columns: list[Any] = []
    group: list[Any] = []
    for name in ask.dims:
        expr = MetricValue.dims[name].astext
        columns.append(expr.label(f"d_{name}"))
        group.append(expr)
    for key in ask.by:
        column = getattr(MetricValue, key)
        columns.append(column.label(key))
        group.append(column)
    stmt = select(
        *columns,
        func.sum(MetricValue.count).label("n"),
        func.sum(MetricValue.value_count).label("vn"),
        func.sum(MetricValue.sum).label("vs"),
        func.min(MetricValue.min).label("vmin"),
        func.max(MetricValue.max).label("vmax"),
    ).where(
        MetricValue.metric_id == metric.id,
        MetricValue.run_id == current,
        visible_owner_clause(user, MetricValue.workspace_id),
    )
    for name, value in ask.filters.items():
        if value is None:
            stmt = stmt.where(MetricValue.dims[name].astext.is_(None))
        else:
            stmt = stmt.where(MetricValue.dims.contains({name: value}))
    if ask.period_from is not None:
        stmt = stmt.where(MetricValue.period >= ask.period_from)
    if ask.period_to is not None:
        stmt = stmt.where(MetricValue.period < ask.period_to)
    if ask.cohort_from is not None:
        stmt = stmt.where(MetricValue.cohort >= ask.cohort_from)
    if ask.cohort_to is not None:
        stmt = stmt.where(MetricValue.cohort < ask.cohort_to)
    if ask.age_from is not None:
        stmt = stmt.where(MetricValue.age >= ask.age_from)
    if ask.age_to is not None:
        stmt = stmt.where(MetricValue.age < ask.age_to)
    if group:
        stmt = stmt.group_by(*group)
    rows = db.execute(stmt.limit(limit + 1)).all()
    truncated = len(rows) > limit
    cells: list[Cell] = []
    for row in rows[:limit]:
        if row.n is None:
            # 묶는 축이 없고 셀도 없으면 집계가 NULL 한 줄로 온다 — 셀이 아니다.
            continue
        found = row._mapping
        cells.append(
            Cell(
                dims={name: found[f"d_{name}"] for name in ask.dims},
                period=found["period"] if "period" in ask.by else None,
                cohort=found["cohort"] if "cohort" in ask.by else None,
                age=found["age"] if "age" in ask.by else None,
                count=int(row.n),
                value_count=int(row.vn or 0),
                sum=float(row.vs) if row.vs is not None else None,
                min=float(row.vmin) if row.vmin is not None else None,
                max=float(row.vmax) if row.vmax is not None else None,
            )
        )
    return cells, truncated


def check_ask(built: spec_module.Built, ask: Ask) -> None:
    """모르는 기준 · 없는 축은 422 — 조용히 무시하면 거르기가 안 걸린 수가 「전부」 로
    읽힌다."""
    for name in [*ask.dims, *ask.filters]:
        if built.dim(name) is None:
            raise _bad(
                8,
                f"이 지표에 없는 기준입니다: {name}. 있는 것: "
                f"{', '.join(one.name for one in built.dims) or '(없음)'}",
            )
    for key in ask.by:
        if key not in BY_KEYS:
            raise _bad(9, f"묶을 수 있는 축은 {', '.join(BY_KEYS)} 입니다: {key}")
    if ("period" in ask.by or ask.period_from or ask.period_to) and built.time is None:
        raise _bad(7, "이 지표에는 시간 칸이 없습니다 — 기간으로 묶거나 거를 수 없습니다.")
    if (
        "cohort" in ask.by or "age" in ask.by or ask.cohort_from or ask.cohort_to
    ) and built.cohort is None:
        raise _bad(7, "이 지표에는 코호트 칸이 없습니다.")


# --- 분모 ----------------------------------------------------------------------


@dataclass
class Denominator:
    metric: MetricDef
    spec: spec_module.MetricSpec
    on: list[str]
    time: str | None
    """`period` · `cohort` · None — 이번 요청에서 실제로 짝지은 시간축."""
    per: float
    values: dict[tuple[tuple[str | None, ...], date | None], float | None]
    truncated: bool
    missing: int = 0
    run: MetricRun | None = None
    """분모 지표의 지금 실행 — 분석이 분모 쪽 기간도 닫혔는지 본다(판매가 덜 들어온 달)."""
    before: date | None = None

    def lookup(self, cell: Cell, grain: str | None) -> float | None:
        when: date | None = None
        if self.time == "period":
            when = cell.period
            if when is None and cell.cohort is not None and cell.age is not None and grain:
                when = advance(cell.cohort, cell.age, grain)
        elif self.time == "cohort":
            when = cell.cohort
        key = (tuple(cell.dims.get(name) for name in self.on), when)
        return self.values.get(key)

    def ratio(self, numerator: float | None, cell: Cell, grain: str | None) -> float | None:
        found = self.lookup(cell, grain)
        if numerator is None or not found:
            self.missing += 1
            return None
        return numerator / found * self.per

    def out(self) -> schemas.DenominatorOut:
        return schemas.DenominatorOut(
            metric=self.metric.slug,
            label=self.metric.label,
            measure=self.spec.measure,
            measure_label=METRIC_LABELS[self.spec.measure],
            time=self.time,
            per=self.per,
            missing=self.missing,
            truncated=self.truncated,
        )


def read_denominator(
    db: Session, user: User, built: spec_module.Built, ask: Ask
) -> Denominator | None:
    """분모 지표를 **같은 함수로 두 번째 질의** — 묶음은 `on` 중 이번에 요청한 기준과
    시간축 규칙으로. 분자에만 있는 기준은 펼쳐진다(키에 없으므로 같은 값이 든다)."""
    den = built.denominator
    den_in = built.spec.denominator
    if den is None or den_in is None:
        return None
    den_spec = spec_module.MetricSpec.model_validate(den.spec)
    on = [name for name in den_in.on if name in ask.dims]
    time: str | None = None
    period_from = period_to = None
    if den_in.time == "period" and "period" in ask.by:
        time = "period"
        period_from, period_to = ask.period_from, ask.period_to
    elif den_in.time == "period" and "cohort" in ask.by and "age" in ask.by:
        time = "period"
    elif den_in.time == "cohort" and "cohort" in ask.by:
        time = "cohort"
        period_from, period_to = ask.cohort_from, ask.cohort_to
    # 거르기는 **묶지 않아도** 건넨다 — 분자를 S 모델로 걸렀으면 분모도 S 의 판매 대수다.
    den_ask = Ask(
        dims=on,
        by=("period",) if time else (),
        filters={name: value for name, value in ask.filters.items() if name in den_in.on},
        period_from=period_from,
        period_to=period_to,
    )
    cells, truncated = read(db, user, den, den_ask)
    values: dict[tuple[tuple[str | None, ...], date | None], float | None] = {}
    for cell in cells:
        key = (tuple(cell.dims.get(name) for name in on), cell.period if time else None)
        values[key] = cell.measure(den_spec.measure)
    den_run = current_run(db, den)
    return Denominator(
        den,
        den_spec,
        on,
        time,
        den_in.per,
        values,
        truncated,
        run=den_run,
        before=closed_before(den_run, den_spec.settle_days),
    )


# --- 분석이 쓰는 묶음 읽기 ---------------------------------------------------------------


@dataclass
class Frame:
    """셀 · 분모 · 실행 · 닫힘 — 분석 하나가 셀 위에서 추론하는 데 드는 것 전부(ADR 0014)."""

    cells: list[Cell]
    truncated: bool
    denominator: Denominator | None
    run: MetricRun | None
    before: date | None
    """이 날 앞에 끝나는 기간은 닫혔다 — 계산 시각 - 닫힘 일수."""


def frame(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: Ask,
    *,
    with_denominator: bool = True,
) -> Frame:
    check_ask(built, ask)
    cells, truncated = read(db, user, metric, ask)
    den = read_denominator(db, user, built, ask) if with_denominator else None
    run = current_run(db, metric)
    return Frame(cells, truncated, den, run, closed_before(run, built.spec.settle_days))


def snapshot(db: Session) -> None:
    """이 요청의 읽기를 **한 스냅샷**으로 — 분석은 주체 · 기준 · 분모를 여러 번 읽는데, 그
    사이 재계산이 `current_run_id` 를 바꿔 끼우면 옛 실행과 새 실행이 섞인다. 실행 번호를
    붙잡아 두는 것으로는 안 된다 — 바꿔 끼우는 트랜잭션이 옛 실행의 셀을 지운다.

    인증이 이미 연 트랜잭션은 닫고, 새 트랜잭션의 첫 문장으로 격리 수준을 정한다."""
    db.rollback()
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))


def visible_share(db: Session, user: User, metric: MetricDef) -> float | None:
    """지금 실행의 기록 중 이 사람에게 보이는 몫 — 1 보다 작으면 분자는 줄었는데 분모(판매
    대수)는 전사일 수 있어 비율이 낮게 나온다. 시스템 관리자는 다 본다."""
    if user.is_system_admin:
        return 1.0
    current = (
        select(MetricDef.current_run_id).where(MetricDef.id == metric.id).scalar_subquery()
    )
    seen = visible_owner_clause(user, MetricValue.workspace_id)
    total, visible = db.execute(
        select(
            func.coalesce(func.sum(MetricValue.count), 0),
            func.coalesce(func.sum(MetricValue.count).filter(seen), 0),
        ).where(MetricValue.metric_id == metric.id, MetricValue.run_id == current)
    ).one()
    return float(visible) / float(total) if total else None


# --- 건 보기 · 이름 · 닫힘 --------------------------------------------------------------


def drill(
    built: spec_module.Built,
    cell: Cell,
    *,
    by: tuple[str, ...],
    ask: Ask | None = None,
    period: date | None = None,
) -> schemas.DrillOut:
    """셀 → 목록 조건. 정의의 거르기는 그대로 덧붙이되 **기준이 정한 조건이 이긴다** —
    기준 값은 거르기를 통과한 값이라 겹쳐도 뜻이 같다. `by` 에 없는 축은 그 축의 전부다.

    **읽을 때 건 거르기(`ask`)도 조건이 된다** — 기본 모델로 거른 코호트의 셀은 그 기본 모델의
    기록이다. 셀이 묶지 않은 기준의 거르기 값, 묶지 않은 기간 · 코호트의 범위를 덧붙인다.
    빠뜨리면 「N건 보기」 가 전체 기본 모델의 기록을 열면서 정확하다고 말한다(0.4.32 의
    버그 — 거른 화면에서만 드러났다)."""
    params: dict[str, str] = {}
    partial: list[str] = []
    for name, value in cell.dims.items():
        dim = built.dim(name)
        if dim is not None:
            _dim_condition(params, partial, dim, value)
    if ask is not None:
        for name, value in ask.filters.items():
            dim = built.dim(name)
            if dim is not None and name not in cell.dims:
                _dim_condition(params, partial, dim, value)
    if built.time is not None and period is not None:
        _range_condition(params, partial, "period", built.time, period)
    elif built.time is not None and "period" in by:
        _range_condition(params, partial, "period", built.time, cell.period)
    elif built.time is not None and ask is not None:
        _bounds(params, built.time, ask.period_from, ask.period_to)
    if built.cohort is not None and "cohort" in by:
        _range_condition(params, partial, "cohort", built.cohort, cell.cohort)
    elif built.cohort is not None and ask is not None:
        _bounds(params, built.cohort, ask.cohort_from, ask.cohort_to)
    for cond in built.conds:
        params.setdefault(f"f.{cond.field}.{cond.op}", cond.value)
    return schemas.DrillOut(type_slug=built.source.slug, params=params, partial=partial)


def _bounds(
    params: dict[str, str],
    axis: spec_module.TimeAxis,
    start: date | None,
    stop: date | None,
) -> None:
    """묶지 않은 축의 읽기 범위 — 날짜 칸의 `gte` · `lt`. 범위의 끝은 「앞까지」 다."""
    if start is not None:
        params[f"f.{axis.key}.gte"] = start.isoformat()
    if stop is not None:
        params[f"f.{axis.key}.lt"] = stop.isoformat()


def _range_condition(
    params: dict[str, str],
    partial: list[str],
    what: str,
    axis: spec_module.TimeAxis,
    when: date | None,
) -> None:
    if when is None:
        partial.append(what)
        return
    params[f"f.{axis.key}.gte"] = when.isoformat()
    params[f"f.{axis.key}.lt"] = axes.next_period(when, axis.grain).isoformat()


def _dim_condition(
    params: dict[str, str], partial: list[str], dim: spec_module.Dim, value: str | None
) -> None:
    address = dim.address
    if address in visits_module.ADDRESSES:
        # 방문 차례 · 재방문은 같은 시리얼의 다른 기록에 달린 값이라 목록 조건이 없다.
        partial.append(dim.name)
        return
    own = address.startswith("properties.")
    field_name = address.split(".", 1)[1] if own else address
    if value is None:
        if own or address in ("label", "key"):
            params[f"f.{field_name}.empty"] = "1"
        else:
            # 걸음 너머의 빈 값은 「이어진 것이 없다」 와 「이어진 것의 칸이 비었다」 가 섞여
            # 조건 하나로 못 적는다. 부서는 조건이 없다.
            partial.append(dim.name)
        return
    if dim.grain is not None and dim.axis.kind in axes.DATE_KINDS:
        found = axes.period_range(value, dim.grain)
        if found is None:
            partial.append(dim.name)
            return
        params[f"f.{field_name}.gte"], params[f"f.{field_name}.lt"] = found
        return
    if address == "status":
        params["status"] = value
    elif address == "created_year":
        params["year"] = value
    elif address == "workspace":
        partial.append(dim.name)
    else:
        params[f"f.{field_name}.eq"] = value


def labels_for(
    db: Session, built: spec_module.Built, names: list[str], cells: list[Cell]
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name in names:
        dim = built.dim(name)
        if dim is None:
            continue
        distinct: set[str] = set()
        for one in cells:
            value = one.dims.get(name)
            if value is not None:
                distinct.add(value)
        keys = sorted(distinct)[:MAX_LABELS]
        found = axes.labels(db, dim.axis, keys)
        out[name] = {key: found.get(key, key) for key in keys}
    return out


def label_of(labels: dict[str, dict[str, str]], name: str, value: str | None) -> str:
    if value is None:
        return axes.EMPTY_LABEL
    return labels.get(name, {}).get(value, value)


def current_run(db: Session, metric: MetricDef) -> MetricRun | None:
    if metric.current_run_id is None:
        return None
    return db.get(MetricRun, metric.current_run_id)


def is_stale(metric: MetricDef, now: datetime | None = None) -> bool:
    """세 주기가 지나도록 안 셌거나, 아직 한 번도 안 셌다."""
    if metric.last_run_at is None:
        return True
    if metric.interval_hours <= 0:
        return False
    now = now or datetime.now(UTC)
    return metric.last_run_at + timedelta(hours=metric.interval_hours * 3) < now


def closed_before(run: MetricRun | None, settle_days: int) -> date | None:
    if run is None or run.watermark is None:
        return None
    return run.watermark.astimezone(UTC).date() - timedelta(days=settle_days)


def is_closed(period: date | None, grain: str | None, before: date | None) -> bool:
    if period is None or grain is None or before is None:
        return False
    return axes.next_period(period, grain) <= before


def header_of(
    metric: MetricDef,
    built: spec_module.Built,
    run: MetricRun | None,
    *,
    truncated: bool,
    denominator: Denominator | None,
) -> dict[str, Any]:
    stats = run.stats if run is not None else {}
    return {
        "slug": metric.slug,
        "label": metric.label,
        "measure": built.spec.measure,
        "measure_label": METRIC_LABELS[built.spec.measure],
        "grain": built.grain,
        "cohort_grain": built.cohort.grain if built.cohort is not None else None,
        "settle_days": built.spec.settle_days,
        "computed_at": run.finished_at if run is not None else None,
        "watermark": run.watermark if run is not None else None,
        "stale": is_stale(metric),
        "overlap": built.overlap,
        "unbucketed": int(stats.get("unbucketed", 0)),
        "unbucketed_cohort": int(stats.get("unbucketed_cohort", 0)),
        "negative_age": int(stats.get("negative_age", 0)),
        "truncated": truncated or (denominator.truncated if denominator else False),
        "denominator": denominator.out() if denominator is not None else None,
    }


def _sort_key(cell: Cell, dims: list[str]) -> tuple[Any, ...]:
    def dated(when: date | None) -> tuple[int, date]:
        return (1, date.min) if when is None else (0, when)

    return (
        dated(cell.period),
        dated(cell.cohort),
        (1, 0) if cell.age is None else (0, cell.age),
        *[(1, "") if cell.dims.get(name) is None else (0, cell.dims[name]) for name in dims],
    )


# --- 세 모양 ---------------------------------------------------------------------


def table(
    db: Session, user: User, metric: MetricDef, built: spec_module.Built, ask: Ask
) -> schemas.TableOut:
    """요청한 기준(과 기간 · 코호트)별 셀 — 비율 · 건 보기 포함."""
    check_ask(built, ask)
    cells, truncated = read(db, user, metric, ask)
    denominator = read_denominator(db, user, built, ask)
    labels = labels_for(db, built, ask.dims, cells)
    run = current_run(db, metric)
    before = closed_before(run, built.spec.settle_days)
    measure = built.spec.measure
    out: list[schemas.CellOut] = []
    total_value = 0.0 if measure in ("count", "sum") else None
    for cell in sorted(cells, key=lambda one: _sort_key(one, ask.dims)):
        value = cell.measure(measure)
        if total_value is not None and value is not None:
            total_value += value
        ratio = denominator.ratio(value, cell, built.grain) if denominator else None
        out.append(
            schemas.CellOut(
                dims=cell.dims,
                labels={
                    name: label_of(labels, name, value) for name, value in cell.dims.items()
                },
                period=_iso(cell.period),
                period_label=(
                    axes.period_label(cell.period.isoformat(), built.grain or "day")
                    if cell.period is not None
                    else (axes.EMPTY_LABEL if "period" in ask.by else None)
                ),
                cohort=_iso(cell.cohort),
                cohort_label=(
                    axes.period_label(cell.cohort.isoformat(), built.cohort.grain)
                    if cell.cohort is not None and built.cohort is not None
                    else (axes.EMPTY_LABEL if "cohort" in ask.by else None)
                ),
                age=cell.age,
                count=cell.count,
                value_count=cell.value_count,
                sum=cell.sum,
                min=cell.min,
                max=cell.max,
                avg=cell.avg,
                value=value,
                ratio=ratio,
                denominator=denominator.lookup(cell, built.grain) if denominator else None,
                closed=(
                    is_closed(cell.period, built.grain, before) if "period" in ask.by else None
                ),
                drill=drill(built, cell, by=ask.by, ask=ask),
            )
        )
    header = header_of(metric, built, run, truncated=truncated, denominator=denominator)
    return schemas.TableOut(
        **header,
        dims=list(ask.dims),
        by=list(ask.by),
        cells=out,
        total_count=sum(one.count for one in cells),
        total_value=total_value,
    )


def series(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: Ask,
    *,
    split: str | None,
) -> schemas.SeriesOut:
    """기간순 추이 — 빈 기간은 0, 전기 · 전년 동기 · 닫힘. 세부 기준이 있으면 선이 여럿."""
    if built.time is None:
        raise _bad(7, "이 지표에는 시간 칸이 없어 추이를 낼 수 없습니다.")
    ask.dims = [split] if split else []
    ask.by = ("period",)
    check_ask(built, ask)
    grain = built.time.grain
    cells, truncated = read(db, user, metric, ask)
    denominator = read_denominator(db, user, built, ask)
    labels = labels_for(db, built, ask.dims, cells)
    run = current_run(db, metric)
    before = closed_before(run, built.spec.settle_days)
    measure = built.spec.measure

    dated = [one for one in cells if one.period is not None]
    by_line: dict[str | None, dict[date, Cell]] = {}
    for cell in dated:
        key = cell.dims.get(split) if split else None
        assert cell.period is not None
        by_line.setdefault(key, {})[cell.period] = cell
    start = ask.period_from or (
        min(one.period for one in dated if one.period) if dated else None
    )
    stop = ask.period_to or (
        axes.next_period(max(one.period for one in dated if one.period), grain)
        if dated
        else None
    )
    periods = dense(start, stop, grain) if start is not None and stop is not None else []

    order = sorted(
        by_line,
        key=lambda key: (-sum(one.count for one in by_line[key].values()), key or ""),
    )
    lines_truncated = len(order) > MAX_LINES
    lines: list[schemas.LineOut] = []
    for key in order[:MAX_LINES]:
        found = by_line[key]
        values: dict[date, float | None] = {}
        points: list[schemas.PointOut] = []
        previous: float | None = None
        for when in periods:
            cell = found.get(when) or Cell(
                {split: key} if split else {}, when, None, None, 0, 0, None, None, None
            )
            value = cell.measure(measure)
            values[when] = value
            points.append(
                schemas.PointOut(
                    period=when.isoformat(),
                    label=axes.period_label(when.isoformat(), grain),
                    count=cell.count,
                    value=value,
                    ratio=denominator.ratio(value, cell, grain) if denominator else None,
                    prev=previous,
                    yoy=values.get(year_before(when, grain)),
                    closed=is_closed(when, grain, before),
                    drill=drill(built, cell, by=("period",), ask=ask),
                )
            )
            previous = value
        lines.append(
            schemas.LineOut(
                key=key,
                label=label_of(labels, split, key) if split else metric.label,
                points=points,
            )
        )
    header = header_of(metric, built, run, truncated=truncated, denominator=denominator)
    return schemas.SeriesOut(
        **header, split=split, lines=lines, lines_truncated=lines_truncated
    )


def cohort(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    ask: Ask,
    *,
    cumulative: bool,
) -> schemas.CohortOut:
    """코호트 x 경과 행렬 — `age >= 0` 만, 빈 칸은 0, 누적이면 코호트마다 경과순 누적(분자만
    누적 ÷ 분모)."""
    if built.cohort is None:
        raise _bad(7, "이 지표에는 코호트 칸이 없어 코호트 행렬을 낼 수 없습니다.")
    ask.dims = []
    ask.by = ("cohort", "age")
    check_ask(built, ask)
    grain = built.cohort.grain
    cells, truncated = read(db, user, metric, ask)
    denominator = read_denominator(db, user, built, ask)
    run = current_run(db, metric)
    before = closed_before(run, built.spec.settle_days)
    measure = built.spec.measure

    matrix: dict[date, dict[int, Cell]] = {}
    for cell in cells:
        if cell.cohort is None or cell.age is None or cell.age < 0:
            continue
        matrix.setdefault(cell.cohort, {})[cell.age] = cell
    cohorts: list[date] = []
    if matrix:
        start = ask.cohort_from or min(matrix)
        stop = ask.cohort_to or axes.next_period(max(matrix), grain)
        cohorts = dense(start, stop, grain)
    max_age = max((age for ages in matrix.values() for age in ages), default=-1)
    ages = list(range(max_age + 1))

    rows: list[schemas.CohortRowOut] = []
    for when in cohorts:
        found = matrix.get(when, {})
        running = 0.0
        out_cells: list[schemas.CohortCellOut] = []
        row_cell = Cell({}, None, when, 0, 0, 0, None, None, None)
        for age in ages:
            cell = found.get(age) or Cell({}, None, when, age, 0, 0, None, None, None)
            value = cell.measure(measure)
            if value is not None:
                running += value
            total = running if cumulative else value
            period = advance(when, age, grain)
            out_cells.append(
                schemas.CohortCellOut(
                    age=age,
                    count=cell.count,
                    value=value,
                    cumulative=running,
                    ratio=denominator.ratio(total, cell, grain) if denominator else None,
                    closed=is_closed(period, grain, before),
                    drill=drill(built, cell, by=("cohort",), ask=ask, period=period),
                )
            )
        rows.append(
            schemas.CohortRowOut(
                cohort=when.isoformat(),
                label=axes.period_label(when.isoformat(), grain),
                denominator=(
                    denominator.lookup(row_cell, grain)
                    if denominator is not None and denominator.time == "cohort"
                    else None
                ),
                cells=out_cells,
            )
        )
    header = header_of(metric, built, run, truncated=truncated, denominator=denominator)
    return schemas.CohortOut(**header, cumulative=cumulative, ages=ages, rows=rows)


def dim_values(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    name: str,
    *,
    q: str | None,
) -> schemas.DimValuesOut:
    """기준 하나의 값들 — 화면 고르개. 많은 순, 상한 안에서."""
    dim = built.dim(name)
    if dim is None:
        raise _bad(8, f"이 지표에 없는 기준입니다: {name}")
    current = (
        select(MetricDef.current_run_id).where(MetricDef.id == metric.id).scalar_subquery()
    )
    expr = MetricValue.dims[name].astext
    stmt = (
        select(expr.label("v"), func.sum(MetricValue.count).label("n"))
        .where(
            MetricValue.metric_id == metric.id,
            MetricValue.run_id == current,
            visible_owner_clause(user, MetricValue.workspace_id),
        )
        .group_by(expr)
        .order_by(func.sum(MetricValue.count).desc(), expr)
        .limit(MAX_DIM_VALUES + 1)
    )
    rows = db.execute(stmt).all()
    keys = [str(row.v) for row in rows if row.v is not None]
    labels = axes.labels(db, dim.axis, keys[:MAX_LABELS])
    values = [
        schemas.DimValueOut(
            value=row.v,
            label=axes.EMPTY_LABEL if row.v is None else labels.get(str(row.v), str(row.v)),
            count=int(row.n or 0),
        )
        for row in rows[:MAX_DIM_VALUES]
    ]
    if q:
        needle = q.casefold()
        values = [one for one in values if needle in one.label.casefold()]
    return schemas.DimValuesOut(
        name=name,
        label=dim.axis.label,
        kind=dim.axis.kind,
        values=values,
        truncated=len(rows) > MAX_DIM_VALUES,
    )
