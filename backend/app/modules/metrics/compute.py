"""계산 — 지표마다 `INSERT … SELECT … GROUP BY` **한 문장.**

원천을 한 번만 훑는 것이 가장 싸다. 덩어리로 나눠 넣으면 소스를 여러 번 읽고, Python 으로
끌어와 세면 200만 행이 메모리를 지난다. 묶는 식은 통계와 **같은 빌더**(`objects/axes.py`)가
만든다 — 같은 주소가 같은 수를 낸다.

## 바꿔 끼운다

새 실행의 셀을 다 쓴 뒤 `current_run_id` 를 바꾸고 옛 실행의 셀을 지운다 — 같은 트랜잭션.
읽는 쪽은 반쯤 쓰인 값을 보지 않고, 실패하면 롤백되어 옛 값이 남는다. 지표 하나가 끝날
때마다 커밋한다(`run_recompute`) — 열 개 중 아홉이 되고 하나가 실패했으면 아홉은 새 값이다.

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
from app.modules.coreapi import services as coreapi_services
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


def statement(
    built: spec_module.Built,
    *,
    metric_id: uuid.UUID,
    run_id: uuid.UUID,
    cutoff: date | None = None,
    until: date | None = None,
) -> Insert:
    """`INSERT INTO metric_values … SELECT … GROUP BY`.

    안쪽이 (부서, 기간, 코호트, 기준 값들)로 묶고, 바깥이 그 위에 경과 · 기준 JSON · 해시를
    얹는다 — 바깥은 셀 수만큼만 돈다. NULL 은 **종류를 박아** 넣는다: 서브쿼리의 맨
    NULL 은 text 가 되어 date 열에 못 들어간다.

    `cutoff` 는 방문 기준의 닫힘선(워터마크 - 닫힘 일수) — 재방문 창이 이것을 넘으면 「아직
    열림」 이다. `until` 은 머무는 기간의 미래 자르기(워터마크 날짜) — 그 날 뒤에 시작하는
    기간은 만들지 않는다.
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
    joining = [one.axis for one in built.dims]
    if stay is not None and stay.source is not None:
        joining.append(stay.source)
    inner = axes.joined(inner, *joining)
    inner = conditions.apply(inner, built.scope.defs, built.conds, built.plan.resolver)
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


def run_one(
    db: Session,
    metric: MetricDef,
    *,
    job_id: uuid.UUID | None,
    progress: Callable[[str, int, int], None],
) -> MetricRun:
    """지표 하나를 **전부 다시** 센다 — 끝에 커밋한다. 실패하면 예외가 나가고 호출자가
    롤백한다(옛 값은 그대로)."""
    built = built_of(db, metric)
    # 타이머와 손이 같은 지표를 동시에 세면 둘째가 기다린다 — 트랜잭션이 끝나면 풀린다.
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key(metric.slug)})
    work_mem = get_settings().metrics_work_mem
    if _WORK_MEM_RE.match(work_mem):
        # 묶기가 디스크로 넘어가면 열 배 느리다. 이 트랜잭션에서만.
        db.execute(text(f"SET LOCAL work_mem = '{work_mem}'"))
    watermark = coreapi_services.watermark(db)
    run = MetricRun(
        metric_id=metric.id,
        job_id=job_id,
        status="running",
        watermark=watermark,
    )
    db.add(run)
    db.flush()
    progress(f"{metric.label} 계산", 0, 0)
    # 읽기의 닫힘(`query.closed_before`)과 같은 선 — 방문의 「아직 열림」 이 그것을 따른다.
    seen = (watermark or datetime.now(UTC)).astimezone(UTC).date()
    cutoff = seen - timedelta(days=built.spec.settle_days)
    db.execute(statement(built, metric_id=metric.id, run_id=run.id, cutoff=cutoff, until=seen))

    mine = (MetricValue.metric_id == metric.id, MetricValue.run_id == run.id)
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
    stats = {
        "unbucketed": int(totals[2]) if built.time is not None else 0,
        "unbucketed_cohort": int(totals[3]) if built.cohort is not None else 0,
        "negative_age": int(totals[4]),
    }

    old = metric.current_run_id
    now = datetime.now(UTC)
    run.status = "ok"
    run.finished_at = now
    run.rows = int(totals[1])
    run.cells = cells
    run.stats = stats
    metric.current_run_id = run.id
    metric.last_run_at = now
    metric.last_status = "ok"
    metric.last_error = None
    metric.cells = cells
    metric.overlap = built.overlap
    if old is not None:
        db.execute(
            delete(MetricValue).where(
                MetricValue.metric_id == metric.id, MetricValue.run_id == old
            )
        )
    db.commit()
    return run
