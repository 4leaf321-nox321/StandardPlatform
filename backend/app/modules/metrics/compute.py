"""계산 — 지표마다 `INSERT … SELECT … GROUP BY` **한 문장.**

원천을 한 번만 훑는 것이 가장 싸다. 덩어리로 나눠 넣으면 소스를 여러 번 읽고, Python 으로
끌어와 세면 200만 행이 메모리를 지난다. 묶는 식은 통계와 **같은 빌더**(`objects/axes.py`)가
만든다 — 같은 주소가 같은 수를 낸다.

## 바꿔 끼운다

**전량**은 새 실행의 셀을 다 쓴 뒤 `current_run_id` · `cells_run_id` 를 바꾸고 옛 실행의 셀을
지운다 — 같은 트랜잭션. 읽는 쪽은 반쯤 쓰인 값을 보지 않고, 실패하면 롤백되어 옛 값이 남는다.
지표 하나가 끝날 때마다 커밋한다(`run_recompute`) — 열 개 중 아홉이 되고 하나가 실패했으면
아홉은 새 값이다.

**증분**(`incremental.py`)은 셀이 든 실행(`cells_run_id`)에서 바뀐 기간의 셀만 지우고 다시
넣는다 — 역시 한 트랜잭션. 새 실행 행은 「언제 · 어디까지 봤나」(워터마크)만 새로 하고 셀은
전량 실행의 것을 이어 쓴다.

## 가시성은 안 건다

시스템이 세는 것이라 사람의 가시성이 없다. 대신 **기록의 소유 부서별로** 묶어 두고 읽을 때
보이는 부서만 더한다.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import chain
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    Float,
    Insert,
    Integer,
    String,
    and_,
    cast,
    delete,
    func,
    insert,
    literal,
    null,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.audit.models import AuditEntry
from app.modules.coreapi import services as coreapi_services
from app.modules.metrics import incremental, query, timeindex
from app.modules.metrics import spec as spec_module
from app.modules.metrics import stay as stay_module
from app.modules.metrics import visits as visits_module
from app.modules.metrics.models import MetricDef, MetricRun, MetricValue
from app.modules.objects import axes, conditions
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared.errors import AppError, code

#: `SET LOCAL work_mem` 에 그대로 들어가는 값 — 설정이 이상하면 안 건드린다.
_WORK_MEM_RE = re.compile(r"^[0-9]+(kB|MB|GB|TB)?$")


def lock_key(slug: str) -> int:
    """지표마다 자문 잠금 번호 — 타이머와 손이 같은 지표를 동시에 세지 않게."""
    digest = hashlib.sha256(f"metric:{slug}".encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def _once_per_record(inner: Any, columns: list[Any], value: Any) -> Any:
    """(기록, 셀 열들, 값)을 한 번씩 남기고 그 위에서 센다 — 열 이름은 `statement` 의 안쪽과
    같다(`ws` · `period` · `cohort` · `d<n>` · `w` · `n` · `vn` · `vs` · `vmin` · `vmax`). 셀
    열 전부로 묶는다 — 기간 · 코호트가 없으면 그 열은 NULL 하나라 묶어도 같다."""
    picked = [ObjectInstance.id.label("oid"), *columns]
    if value is not None:
        picked.append(value.label("val"))
    pairs = inner.with_only_columns(*picked).distinct().subquery("once")
    names = [one.name for one in columns]
    measured = pairs.c.val if value is not None else None
    measures: list[Any] = [
        func.count().label("n"),
        (func.count(measured) if measured is not None else cast(literal(0), BigInteger)).label(
            "vn"
        ),
        (func.sum(measured) if measured is not None else cast(null(), Float)).label("vs"),
        (func.min(measured) if measured is not None else cast(null(), Float)).label("vmin"),
        (func.max(measured) if measured is not None else cast(null(), Float)).label("vmax"),
    ]
    return select(*[pairs.c[name] for name in names], *measures).group_by(
        *[pairs.c[name] for name in names]
    )


def statement(
    built: spec_module.Built,
    *,
    metric_id: uuid.UUID,
    run_id: uuid.UUID,
    cutoff: date | None = None,
    until: date | None = None,
    cap: int | None = None,
    periods: list[date | None] | None = None,
    indexed: bool = True,
) -> Insert:
    """`INSERT INTO metric_values … SELECT … GROUP BY`.

    안쪽이 (부서, 기간, 코호트, 기준 값들)로 묶고, 바깥이 그 위에 경과 · 기준 JSON · 해시를
    얹는다 — 바깥은 셀 수만큼만 돈다. NULL 은 **종류를 박아** 넣는다: 서브쿼리의 맨
    NULL 은 text 가 되어 date 열에 못 들어간다.

    `cutoff` 는 방문 기준의 닫힘선(워터마크 - 닫힘 일수) — 재방문 창이 이것을 넘으면 「아직
    열림」 이다. `until` 은 머무는 기간의 미래 자르기(워터마크 날짜) — 그 날 뒤에 시작하는
    기간은 만들지 않는다. `cap` 이 있으면 셀을 그 수 + 1 개까지만 넣는다 — 상한을 넘는 계산을
    끝까지 쓰고 나서 버리지 않게(넘었는지는 넣은 수로 안다). `periods` 가 있으면 그 기간에
    드는 원천 기록만 센다(증분 — 시간 칸이 있는 지표만). `indexed` 는 날짜 칸 색인이 있나 —
    거르는 식의 모양이 달라진다(`incremental.period_filter`).
    """
    value = built.value
    ws = ObjectInstance.owner_workspace_id
    period: Any = built.time.expr if built.time is not None else cast(null(), Date)
    cohort: Any = built.cohort.expr if built.cohort is not None else cast(null(), Date)
    columns: list[Any] = [ws.label("ws"), period.label("period"), cohort.label("cohort")]
    group: list[Any] = [ws]
    if built.time is not None:
        group.append(built.time.expr)
    if built.cohort is not None:
        group.append(built.cohort.expr)
    for index, dim in enumerate(built.dims):
        columns.append(dim.axis.expr.label(f"d{index}"))
        group.append(dim.axis.expr)
    stay = built.stay
    if stay is not None:
        length = stay_module.length(stay)
        columns.append(length.label("w"))
        group.append(length)
    if value is None:
        measures: list[Any] = [
            func.count().label("n"),
            cast(literal(0), BigInteger).label("vn"),
            cast(null(), Float).label("vs"),
            cast(null(), Float).label("vmin"),
            cast(null(), Float).label("vmax"),
        ]
    else:
        measures = [
            func.count().label("n"),
            func.count(value).label("vn"),
            func.sum(value).label("vs"),
            func.min(value).label("vmin"),
            func.max(value).label("vmax"),
        ]
    inner = (
        select(*columns, *measures)
        .select_from(ObjectInstance)
        .where(ObjectInstance.type_id == built.source.id, ObjectInstance.deleted_at.is_(None))
    )
    if periods is not None:
        inner = inner.where(incremental.period_filter(built, periods, indexed=indexed))
    joining = [one.axis for one in built.dims]
    if stay is not None and stay.source is not None:
        joining.append(stay.source)
    inner = axes.joined(inner, *joining)
    inner = conditions.apply(inner, built.scope.defs, built.conds, built.plan.resolver)
    if any(one.axis.tree is not None and one.axis.multi for one in built.dims):
        # **여러 값 칸을 계층으로 접으면** 두 값이 같은 조상으로 접힐 수 있다 — 그러면 한
        # 기록이 같은 셀에 두 번 센다(부품 [p1, p2] 가 모두 뿌리 R 아래면 R 이 2). (기록, 셀)을
        # 한 번씩 남긴 뒤 센다 — 통계의 막대가 같은 일을 한다(`summary._grouped`, 2026-10-24).
        grouped = _once_per_record(inner, columns, value).subquery("g")
    else:
        grouped = inner.group_by(*group).subquery("g")
    if stay is not None:
        assert built.time is not None
        grouped = _spread(grouped, len(built.dims), built.time.grain, stay)

    if built.dims:
        pairs = chain.from_iterable(
            (literal(one.name, String), grouped.c[f"d{index}"])
            for index, one in enumerate(built.dims)
        )
        dims_json: Any = func.jsonb_build_object(*pairs)
    else:
        dims_json = cast(literal("{}", String), JSONB)
    age: Any = (
        axes.age_expr(grouped.c.period, grouped.c.cohort, built.cohort.grain)
        if built.cohort is not None
        else cast(null(), Integer)
    )

    def as_text(column: Any) -> Any:
        return func.coalesce(cast(column, String), literal("", String))

    cell_hash = cast(
        func.md5(
            func.concat_ws(
                literal("|", String),
                as_text(grouped.c.ws),
                as_text(grouped.c.period),
                as_text(grouped.c.cohort),
                cast(dims_json, String),
            )
        ),
        PgUUID(as_uuid=True),
    )
    outer = select(
        literal(metric_id, PgUUID(as_uuid=True)),
        literal(run_id, PgUUID(as_uuid=True)),
        cell_hash,
        grouped.c.ws,
        grouped.c.period,
        grouped.c.cohort,
        age,
        dims_json,
        grouped.c.n,
        grouped.c.vn,
        grouped.c.vs,
        grouped.c.vmin,
        grouped.c.vmax,
    )
    if built.visits is not None and cutoff is not None:
        outer = outer.params({visits_module.CUTOFF: cutoff})
    if stay is not None and until is not None:
        outer = outer.params({stay_module.UNTIL: until})
    if cap is not None:
        outer = outer.limit(cap + 1)
    return insert(MetricValue).from_select(
        [
            MetricValue.metric_id,
            MetricValue.run_id,
            MetricValue.cell_hash,
            MetricValue.workspace_id,
            MetricValue.period,
            MetricValue.cohort,
            MetricValue.age,
            MetricValue.dims,
            MetricValue.count,
            MetricValue.value_count,
            MetricValue.sum,
            MetricValue.min,
            MetricValue.max,
        ],
        outer,
    )


def _spread(grouped: Any, dims: int, grain: str, stay: stay_module.Stay) -> Any:
    """머무는 기간 — 묶은 셀을 0 ~ w-1 기간 뒤로 펼쳐 다시 묶는다. 날짜를 못 읽은 셀(기간
    NULL)은 펼치지 않고, 계산 시점의 기간을 넘는 미래는 만들지 않는다."""
    # FROM 안의 함수는 앞 표(g)의 칸을 그냥 본다 — LATERAL 이 필요 없다. 열 이름은 붙여야
    # 한다(`AS stay_steps(k)`) — 안 붙이면 열이 함수 별칭 이름이 된다.
    steps = (
        func.generate_series(0, grouped.c.w - 1)
        .table_valued("k")
        .render_derived(name="stay_steps")
    )
    period = stay_module.shifted(grouped.c.period, steps.c.k, grain)
    keys = [grouped.c.ws, grouped.c.cohort, *(grouped.c[f"d{i}"] for i in range(dims))]
    spread = (
        select(
            *keys,
            period.label("period"),
            grouped.c.n,
            grouped.c.vn,
            grouped.c.vs,
            grouped.c.vmin,
            grouped.c.vmax,
        )
        .select_from(grouped.join(steps, literal(True)))
        .where(
            or_(
                and_(grouped.c.period.is_not(None), period <= stay.until),
                and_(grouped.c.period.is_(None), steps.c.k == 0),
            )
        )
        .subquery("sp")
    )
    regrouped = [spread.c.ws, spread.c.period, spread.c.cohort]
    regrouped.extend(spread.c[f"d{i}"] for i in range(dims))
    return (
        select(
            *regrouped,
            func.sum(spread.c.n).label("n"),
            func.sum(spread.c.vn).label("vn"),
            func.sum(spread.c.vs).label("vs"),
            func.min(spread.c.vmin).label("vmin"),
            func.max(spread.c.vmax).label("vmax"),
        )
        .group_by(*regrouped)
        .subquery("g")
    )


def data_through(
    db: Session,
    metric: MetricDef,
    built: spec_module.Built,
    *,
    cells_run: uuid.UUID,
    seen: date,
) -> date | None:
    """**기록 끝** — 건수가 있는 마지막 기간의 시작일(`stats.data_through`). 시간 칸이 없으면
    None.

    읽기의 닫힌 기간 경계는 계산 시각에서 나와 원천이 멎어도 앞으로 간다 — 이 값이 「이
    지표의 기록은 어디까지 있나」 를 따로 남겨 머리 · 분석이 보여 준다. **빈 기간의 판정은
    이것이 아니라 원천 기록 끝(`source_through`)으로 한다** — 지표의 셀은 정의의 거르기를 거친
    기록이라, 「소음」 만 센 지표 · 끝난 모델만 센 지표 · 대책 뒤 정말 0건 · 드문 지표(월 λ=1
    이면 37% 의 달이 0건)는 원천이 살아 있어도 끝이 멈춘다. **계산 시점의 기간보다 뒤로는 안
    간다** — 날짜 칸의 오타(2062-09)가 끝을 미래로 밀면 멎은 원천이 늘 「들어오는 중」 으로
    보인다. 기간 시작일이 워터마크 날짜 이하면 그 날의 기간 이하다.

    셀 표의 `max(period)` 한 번이라 싸다(`ix_metric_values_period`). **머무는 기간 지표만은
    원천에서 잰다** — 셀이 기록을 계산 시점까지 펼쳐 두어(`_spread`) 셀로는 기록이 멎은 기간을
    모른다. 그 지표는 늘 전량이라(증분 안 됨) 원천을 한 번 더 읽는 값이 계산 한 번의 몫 안이다.
    """
    if built.time is None:
        return None
    if built.stay is not None:
        raw = incremental.date_expr(built.time.key)
        found = db.scalar(
            built.base()
            .with_only_columns(func.max(raw))
            .select_from(ObjectInstance)
            .where(_within(raw, seen, built.time.grain))
        )
        return query.floor_period(found, built.time.grain) if isinstance(found, date) else None
    last = db.scalar(
        select(func.max(MetricValue.period)).where(
            MetricValue.metric_id == metric.id,
            MetricValue.run_id == cells_run,
            MetricValue.count > 0,
            MetricValue.period <= seen,
        )
    )
    return last if isinstance(last, date) else None


def _within(raw: Any, seen: date, grain: str) -> Any:
    """날짜가 **계산 시점의 기간 안까지**인가 — 셀에서 잰 기록 끝(기간 시작일 ≤ 워터마크
    날짜)과 같은 선. 날로 자르면(`<= seen`) 워터마크가 UTC 날짜라 한국 시각 새벽에 「오늘」
    접수가 빠져, 원천 기록 끝이 지표의 기록 끝보다 앞섰다(시험이 잡았다 — 월 지표에서 11월
    대신 3월)."""
    after = incremental.next_period(query.floor_period(seen, grain), grain)
    return raw < after if after is not None else raw <= seen


def source_through_statement(built: spec_module.Built, seen: date) -> Any:
    """원천 기록 끝을 묻는 문장 — **원천 타입 전체**(정의의 거르기 없이)의 시간 칸 날짜 최댓값,
    계산 시점의 기간 안까지(`_within`) · 지운 것 제외. `built.time` 이 있어야 한다.

    큰 타입은 날짜 칸 색인(`timeindex` — `sp_date(properties ->> '<칸>')` 에 `WHERE type_id =
    '<원천>' AND deleted_at IS NULL`)을 타야 2,000만 건에서도 한 줄 읽기다: 날짜 식은 색인 식과
    **글자 그대로 같은** `incremental.date_expr`, 원천 타입도 매개변수가 아니라 글자로(부분
    색인의 조건을 플래너가 증명하게 — `incremental.period_filter` 와 같은 까닭), 지운 것 제외도
    그 색인의 조건이다. 그러면 플래너가 max 를 색인의 거꾸로 읽기 한 줄(`LIMIT 1`)로 바꾼다.
    색인이 없으면 타입 전체를 훑는다 — 2,000만 건에서 25초(2026-11-08 실측, 병렬 읽기). 켜진
    지표의 시간 칸이면 큰 타입에는 타이머가 색인을 세운다(`timeindex.wanted`)."""
    assert built.time is not None
    raw = incremental.date_expr(built.time.key)
    typed = text(f"objects.type_id = '{uuid.UUID(str(built.source.id))}'::uuid")
    return (
        select(func.max(raw))
        .select_from(ObjectInstance)
        .where(
            typed, ObjectInstance.deleted_at.is_(None), _within(raw, seen, built.time.grain)
        )
    )


def source_through(db: Session, built: spec_module.Built, *, seen: date) -> date | None:
    """**원천 기록 끝** — 원천 타입 전체에서 시간 칸 날짜가 있는 마지막 기간의 시작일
    (`stats.source_through`). 시간 칸이 없거나 날짜 있는 기록이 없으면 None.

    빈 기간(`query.has_gap`)은 이것으로 판정한다 — 지표의 기록 끝(`data_through`)은 거르기를
    거친 기록이라 좁은 지표 · 드문 지표 · 정말 0건인 끝에서 원천이 살아 있어도 멈춘다. 원천
    타입 전체가 멎어야 「아직 안 들어옴」 이다. 날짜 칸의 오타는 자른다(계산 시점의 기간 뒤 —
    지표의 기록 끝과 같은 선이라 원천 기록 끝은 그보다 앞서지 않는다)."""
    if built.time is None:
        return None
    found = db.scalar(source_through_statement(built, seen))
    return query.floor_period(found, built.time.grain) if isinstance(found, date) else None


def built_of(db: Session, metric: MetricDef) -> spec_module.Built:
    """정의 행 → 지은 것. 정의가 깨졌으면(칸이 지워짐 · 분모가 사라짐) 그 이유를 말한다."""
    source = db.get(ObjectType, metric.source_type_id)
    if source is None:
        raise AppError(
            code("METRICS", 10),
            f"지표 「{metric.label}」 의 원천 타입이 없습니다.",
            status=422,
        )
    return spec_module.build(
        db, source, spec_module.MetricSpec.model_validate(metric.spec), self_slug=metric.slug
    )


def rules_versions(
    db: Session, metric: MetricDef, built: spec_module.Built
) -> list[dict[str, Any]]:
    """이 계산이 쓴 **규칙 판** — 규칙 기준마다 지문 · 규칙 수, 그 규칙을 저장한 사람 · 때(ADR
    0015). 「지난달과 왜 다른가」 에서 규칙이 바뀌어서인지 기록이 바뀌어서인지를 가른다.
    사람 · 때는 그 지문을 처음 남긴 감사(`metric.defined` · `metric.updated` 의 `rules`)에서
    — 같은 규칙으로 돌아왔으면 가장 최근의 것이다."""
    out: list[dict[str, Any]] = []
    for dim in built.dims:
        if dim.rules is None:
            continue
        saved = db.execute(
            select(AuditEntry.created_at, AuditEntry.actor_label)
            .where(
                AuditEntry.target_table == "metric_defs",
                AuditEntry.target_id == metric.id,
                AuditEntry.changes["rules"][dim.name]["to"].astext == dim.rules.fingerprint,
            )
            .order_by(AuditEntry.created_at.desc())
            .limit(1)
        ).first()
        out.append(
            {
                "name": dim.name,
                "fingerprint": dim.rules.fingerprint,
                "rules": len(dim.rules.labels),
                "changed_at": saved.created_at.isoformat() if saved is not None else None,
                "changed_by": saved.actor_label if saved is not None else None,
            }
        )
    return out


def run_one(
    db: Session,
    metric: MetricDef,
    *,
    job_id: uuid.UUID | None,
    progress: Callable[[str, int, int], None],
    full: bool = True,
) -> MetricRun:
    """지표 하나를 센다 — 끝에 커밋한다. 실패하면 예외가 나가고 호출자가 롤백한다(옛 값은
    그대로). `full` 이 아니면 **되는 때만** 바뀐 기간만 센다(`incremental.plan` — 안 되면 그
    까닭을 실행 기록에 남기고 전량)."""
    # 타이머와 손이 같은 지표를 동시에 세면 둘째가 기다린다 — 트랜잭션이 끝나면 풀린다.
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key(metric.slug)})
    # 잠금을 기다리는 사이 다른 계산이나 정의 고치기가 바꿨을 수 있다 — 그 뒤의 모습으로
    # 짓고 판단한다. 잠금 전에 지으면 옛 정의로 세고 새 정의의 지문을 남겨, 그 뒤 증분이 옛
    # 정의의 셀을 이어 썼다(2026-10-08).
    db.refresh(metric)
    built = built_of(db, metric)
    work_mem = get_settings().metrics_work_mem
    if _WORK_MEM_RE.match(work_mem):
        # 묶기가 디스크로 넘어가면 열 배 느리다. 이 트랜잭션에서만.
        db.execute(text(f"SET LOCAL work_mem = '{work_mem}'"))
    watermark = coreapi_services.watermark(db)
    step: incremental.Increment | str = (
        "전량 요청" if full else incremental.plan(db, metric, built, now=datetime.now(UTC))
    )
    partial = isinstance(step, incremental.Increment)
    run = MetricRun(
        metric_id=metric.id,
        job_id=job_id,
        status="running",
        watermark=watermark,
        spec_hash=incremental.spec_hash(metric),
        mode="incremental" if partial else "full",
    )
    db.add(run)
    db.flush()
    # 읽기의 닫힘(`query.closed_before`)과 같은 선 — 방문의 「아직 열림」 이 그것을 따른다.
    seen = (watermark or datetime.now(UTC)).astimezone(UTC).date()
    cutoff = seen - timedelta(days=built.spec.settle_days)
    cap = get_settings().metrics_max_cells
    old_cells = metric.cells_run_id or metric.current_run_id
    if isinstance(step, incremental.Increment):
        assert old_cells is not None
        cells_run = old_cells
        progress(f"{metric.label} 바뀐 기간 계산", 0, 0)
        run.periods = [one.isoformat() if one else None for one in step.periods]
        run.note = f"바뀐 기록 {step.changed:,}건 · 기간 {len(step.periods)}개"
        if step.periods:
            dated = [one for one in step.periods if one is not None]
            stale = [MetricValue.period.in_(dated)] if dated else []
            if None in step.periods:
                stale.append(MetricValue.period.is_(None))
            db.execute(
                delete(MetricValue).where(
                    MetricValue.metric_id == metric.id,
                    MetricValue.run_id == cells_run,
                    or_(*stale),
                )
            )
            db.execute(
                statement(
                    built,
                    metric_id=metric.id,
                    run_id=cells_run,
                    cutoff=cutoff,
                    until=seen,
                    cap=cap,
                    periods=step.periods,
                    indexed=built.time is not None
                    and timeindex.has(db, built.source.id, built.time.key),
                )
            )
    else:
        cells_run = run.id
        progress(f"{metric.label} 계산", 0, 0)
        run.note = step
        db.execute(
            statement(
                built, metric_id=metric.id, run_id=run.id, cutoff=cutoff, until=seen, cap=cap
            )
        )

    mine = (MetricValue.metric_id == metric.id, MetricValue.run_id == cells_run)
    totals = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(MetricValue.count), 0),
            func.coalesce(func.sum(MetricValue.count).filter(MetricValue.period.is_(None)), 0),
            func.coalesce(func.sum(MetricValue.count).filter(MetricValue.cohort.is_(None)), 0),
            func.coalesce(func.sum(MetricValue.count).filter(MetricValue.age < 0), 0),
        ).where(*mine)
    ).one()
    cells = int(totals[0])
    if cells > cap:
        # 계획의 어림은 표본이라 틀릴 수 있다 — 진짜 상한은 여기다. 넣은 것은 호출자가 되돌리고
        # 옛 값이 남는다.
        raise AppError(
            code("METRICS", 12),
            f"셀이 상한 {cap:,}개를 넘었습니다 — 이번 계산을 버리고 옛 값을 둡니다. 기준을 "
            "줄이거나 거르기를 더합니다.",
            status=422,
        )
    stats: dict[str, Any] = {
        "unbucketed": int(totals[2]) if built.time is not None else 0,
        "unbucketed_cohort": int(totals[3]) if built.cohort is not None else 0,
        "negative_age": int(totals[4]),
    }
    through = data_through(db, metric, built, cells_run=cells_run, seen=seen)
    if through is not None:
        stats["data_through"] = through.isoformat()
    source = source_through(db, built, seen=seen)
    if source is not None:
        stats["source_through"] = source.isoformat()
    versions = rules_versions(db, metric, built)
    if versions:
        stats["rules"] = versions

    now = datetime.now(UTC)
    run.status = "ok"
    run.finished_at = now
    run.rows = int(totals[1])
    run.cells = cells
    run.stats = stats
    metric.current_run_id = run.id
    metric.cells_run_id = cells_run
    metric.last_run_at = now
    metric.last_status = "ok"
    metric.last_error = None
    metric.cells = cells
    metric.overlap = built.overlap
    if old_cells is not None and old_cells != cells_run:
        db.execute(
            delete(MetricValue).where(
                MetricValue.metric_id == metric.id, MetricValue.run_id == old_cells
            )
        )
    db.commit()
    return run
