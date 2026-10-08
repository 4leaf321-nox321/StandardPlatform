"""**바뀐 기간만** 다시 센다 — 지난 계산 뒤에 바뀐 원천 기록이 든 기간의 셀만 지우고 다시
넣는다.

큰 자료에서는 다시 세는 시간이 상한이다 — 2,000만 건에서 큰 지표 하나가 5 ~ 7분, 열 개면 밤마다
한 시간 남짓(ADR 0023). 그런데 밤사이 바뀌는 기록은 대개 최근 몇 기간에 몰린다.

## 무엇을 다시 세나

지난 계산의 워터마크(`MetricRun.watermark` — 그때 아직 안 끝난 트랜잭션보다 앞선 시각) 뒤에
바뀐 원천 기록(`objects.updated_at` — 지운 것도 시각이 오른다)을 찾아, 그 기록이 **지금** 드는
기간과 **그 사이에** 들었던 기간을 모은다. 그 사이의 기간은 날짜 칸이 바뀐 기록에서만 다르다 —
감사 기록(`object.update` 가 고치기 전 · 뒤의 속성을 남긴다)에서 읽는다. 워터마크 뒤의 기록을
**전부** 본다 — 지난 계산이 두 번 고친 것 중 첫 번째만 봤을 수 있다(그때 세던 사이에 고쳐졌다).
그 기간들의 셀을 지우고 다시 넣는다 — 한 트랜잭션이라 읽는 쪽은 반쯤 고친 값을 안 본다.

## 전량으로 가는 때 — 틀린 값보다 느린 값이 낫다

- 정의가 바뀌었다(셀을 만든 전량 실행과 정의 지문이 다르다) · 전량을 센 지 오래됐다(기본 7일 —
  아래의 것들이 놓친 것을 주기적으로 바로잡는다).
- 시간 칸이 없다 · 방문(visits — 한 기록이 같은 시리얼의 다른 기간을 바꾼다) · 머무는 기간(stay
  — 한 기록이 여러 기간에 든다).
- **다른 객체에서 오는 값**(참조 · 관계 너머의 기준 · 거르기 · 측정값)인데 그 객체들이나 관계가
  바뀌었다 — 그런 변경은 원천 기록의 시각을 안 올린다. 원 표를 비추는 타입(부서 · 계정)을
  거치면 바뀐 것을 알 길이 없어 늘 전량이다.
- 바뀐 기록이 많다(기본 원천의 20% 넘게 — 그러면 전량이 오히려 싸다).
- 워터마크 전부터 있던 기록인데 그 뒤의 감사 기록이 없다 — 옛 날짜를 모른다(줄마다 감사를 안
  남기는 백필 등).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, and_, exists, func, literal_column, or_, select, text, union
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.audit.models import AuditEntry
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef, MetricRun
from app.modules.objects import axes
from app.modules.objects.models import ObjectInstance, ObjectRelation, ObjectRelationTombstone
from app.modules.objects.paths import HOP_KINDS
from app.modules.ontology.interfaces import load_ends
from app.modules.ontology.models import ObjectType

#: 원천이 작으면 바뀐 비율을 안 본다 — 그 크기면 어느 쪽이든 싸다.
SMALL = 1000


def spec_hash(metric: MetricDef) -> str:
    """정의의 지문 — 원천 타입과 정의(JSON, 키 순서 고정)."""
    body = json.dumps(
        {"source": str(metric.source_type_id), "spec": metric.spec},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class Increment:
    """다시 셀 기간 — 날짜를 못 읽은 칸은 None."""

    periods: list[date | None]
    changed: int
    """바뀐 원천 기록 수."""


def _foreign_addresses(spec: spec_module.MetricSpec) -> list[str]:
    """다른 객체를 거치는 주소(`ref.` · `out.` · `in.` 로 시작)."""
    found = [one.address for one in spec.dimensions]
    found += [one.field for one in [*spec.filters, *spec.share_when]]
    if spec.measure_field:
        found.append(spec.measure_field)
    return [one for one in found if one.split(".", 1)[0] in HOP_KINDS]


def _foreign_change(
    db: Session, built: spec_module.Built, addresses: list[str], since: datetime
) -> str | None:
    """거쳐 가는 객체 타입 · 관계 종류에 `since` 뒤로 바뀐 것이 있으면 그 까닭."""
    slugs: set[str] = set()
    relations: set[str] = set()
    for address in addresses:
        try:
            chain = built.plan.resolver.parse_chain(address)
        except Exception:
            return f"주소를 다시 풀 수 없다({address})"
        for hop in chain.hops:
            slugs.update(hop.target_slugs)
            if hop.relation is not None:
                relations.add(hop.relation.slug)
    # 상대가 **인터페이스**면 그 자리의 slug 는 인터페이스다 — 구현 타입으로 펴야 그 객체들의
    # 변경을 본다(펴지 않으면 아무 타입도 안 맞아 너머의 변경을 놓쳤다, 2026-10-08).
    slugs = set(load_ends(db).types_of(sorted(slugs)))
    types = list(
        db.execute(
            select(ObjectType.id, ObjectType.kind_class).where(ObjectType.slug.in_(slugs))
        )
    )
    if any(kind == "system" for _id, kind in types):
        return "원 표를 비추는 타입(부서 · 계정)을 거친다"
    ids = [type_id for type_id, _kind in types]
    if ids and db.scalar(
        select(ObjectInstance.id)
        .where(ObjectInstance.type_id.in_(ids), ObjectInstance.updated_at > since)
        .limit(1)
    ):
        return "참조 · 관계 너머의 객체가 바뀌었다"
    if relations and (
        db.scalar(
            select(ObjectRelation.id)
            .where(ObjectRelation.relation.in_(relations), ObjectRelation.updated_at > since)
            .limit(1)
        )
        or db.scalar(
            select(ObjectRelationTombstone.id)
            .where(
                ObjectRelationTombstone.relation.in_(relations),
                ObjectRelationTombstone.removed_at > since,
            )
            .limit(1)
        )
    ):
        return "거쳐 가는 관계가 바뀌었다"
    return None


def _audited_periods(
    db: Session, built: spec_module.Built, recent: Any, since: datetime
) -> set[date | None]:
    """`since` 뒤 감사 기록에 남은 날짜 칸의 값(고치기 전 · 뒤) 전부의 기간 — **DB 안에서.**
    바뀐 기록이 수십만이어도 값을 파이썬으로 끌어오지 않는다. 기간은 계산과 같은 식
    (`sp_date` · `date_trunc`)으로 자른다."""
    assert built.time is not None
    key = built.time.key
    props = AuditEntry.changes["properties"]
    sides = [
        select(props[side][key].astext.label("raw"))
        .select_from(AuditEntry)
        .join(ObjectInstance, ObjectInstance.id == AuditEntry.target_id)
        .where(
            AuditEntry.target_table == "objects",
            AuditEntry.created_at > since,
            recent,
            # 만들기의 before 는 null — 그 앞의 값이 없다.
            func.jsonb_typeof(props[side]) == "object",
            # 다른 칸만 바꾼 기록(`rewrite.changed_property` — `keys` 가 그 칸)은 뺀다.
            or_(props["keys"].is_(None), props["keys"].contains([key])),
        )
        for side in ("before", "after")
    ]
    raws = union(*sides).subquery("raws")
    bucket = axes.bucket(axes.date_or_null(raws.c.raw), built.time.grain)
    return set(db.scalars(select(bucket).select_from(raws).distinct()))


def plan(
    db: Session,
    metric: MetricDef,
    built: spec_module.Built,
    *,
    now: datetime,
) -> Increment | str:
    """증분이 되나 — 되면 다시 셀 기간, 안 되면 **전량으로 가는 까닭**(실행 기록에 남는다)."""
    settings = get_settings()
    if metric.current_run_id is None:
        return "처음 센다"
    current = db.get(MetricRun, metric.current_run_id)
    cells = db.get(MetricRun, metric.cells_run_id or metric.current_run_id)
    if current is None or cells is None or current.status != "ok" or current.watermark is None:
        return "지난 계산이 없다"
    if cells.spec_hash != spec_hash(metric):
        return "정의가 바뀌었다(또는 예전 판에서 센 셀이다)"
    if cells.started_at < now - timedelta(days=settings.metrics_full_every_days):
        return f"전량으로 센 지 {settings.metrics_full_every_days}일이 지났다"
    if built.time is None:
        return "시간 칸이 없는 지표다"
    if built.visits is not None:
        return "방문 지표다(한 기록이 다른 기간을 바꾼다)"
    if built.stay is not None:
        return "머무는 기간 지표다(한 기록이 여러 기간에 든다)"
    since = current.watermark
    foreign = _foreign_addresses(built.spec)
    if foreign:
        reason = _foreign_change(db, built, foreign, since)
        if reason is not None:
            return reason

    # (type_id, updated_at) 색인을 탄다.
    recent = and_(ObjectInstance.type_id == built.source.id, ObjectInstance.updated_at > since)
    changed = int(
        db.scalar(select(func.count()).select_from(ObjectInstance).where(recent)) or 0
    )
    if not changed:
        return Increment(periods=[], changed=0)
    if changed > SMALL:
        total = db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id == built.source.id, ObjectInstance.deleted_at.is_(None)
            )
        )
        if changed > (total or 0) * settings.metrics_incremental_max_share:
            return f"바뀐 기록이 많다({changed:,}건)"

    if db.scalar(
        select(AuditEntry.id)
        .where(AuditEntry.action == "bundle.import", AuditEntry.created_at > since)
        .limit(1)
    ):
        # 묶음은 줄마다 감사를 끌 수 있다(`audit="summary"`) — 그러면 날짜를 고쳐도 옛 값이
        # 어디에도 없다. 드문 일이라 그때는 전부 센다.
        return "묶음 가져오기가 있었다(줄마다 감사를 안 남겼을 수 있다)"
    # 워터마크 뒤에 감사가 **하나라도** 있으면 옛 날짜를 안다고 본다 — 속성을 고치는 길은
    # 모두 감사에 고치기 전 · 뒤를 남기고(속성이 없는 기록은 지우기 · 별칭 · 첨부처럼 날짜를
    # 안 바꾼 것이다), 감사를 끄는 길(묶음)은 바로 위에서 전량으로 보냈다.
    audited = (
        AuditEntry.target_table == "objects",
        AuditEntry.target_id == ObjectInstance.id,
        AuditEntry.created_at > since,
    )
    unknown = int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                recent,
                ObjectInstance.created_at <= since,
                ~exists().where(*audited),
            )
        )
        or 0
    )
    if unknown:
        return f"바뀐 기록 {unknown:,}건의 옛 날짜를 모른다(감사 기록이 없다)"
    periods: set[date | None] = set(
        db.scalars(
            select(built.time.expr).select_from(ObjectInstance).where(recent).distinct()
        )
    )
    periods |= _audited_periods(db, built, recent, since)
    return Increment(
        periods=sorted(periods, key=lambda one: (one is None, one or date.min)),
        changed=changed,
    )


def date_expr(key: str) -> Any:
    """`sp_date(properties ->> '<키>')` — **키를 글자로 박는다.** 날짜 칸의 식 색인
    (`timeindex.py`)이 이 식을 그대로 담는다 — 키가 매개변수면 플래너가 색인과 맞춰 보지
    못한다(키는 정의의 속성 키라 따옴표만 막으면 된다)."""
    quoted = key.replace("'", "''")
    return literal_column(f"sp_date(objects.properties ->> '{quoted}')", Date)


def period_filter(
    built: spec_module.Built, periods: list[date | None], *, indexed: bool = True
) -> Any:
    """`periods` 의 기간에 드는 원천 기록만.

    - **색인이 있으면**(`indexed`) 기간을 **날짜 범위**로 건다 — 기간 = 날짜를 그 단위로 자른
      것이므로 같은 뜻이고, 범위라야 색인을 탄다(기간마다 범위 하나, 플래너가 묶어 읽는다).
    - **없으면** 기간 식(계산이 묶는 것과 같은 식)을 기록마다 **한 번** 계산해 목록과 견준다.
      범위로 걸면 범위마다 날짜를 다시 읽는다(`sp_date` 는 plpgsql) — 200만 건에서 기간
      아홉이면 전부 세는 것보다 두 배 느렸다(실측, ADR 0024).
    """
    assert built.time is not None
    if not periods:
        return literal_column("false")
    if not indexed:
        # NULL 은 IN 에 안 걸린다 — 날짜를 못 읽은 기간은 「무한」 으로 바꿔 함께 견준다
        # (`sp_date` 는 무한을 내지 않는다).
        never = literal_column("'infinity'::date", Date)
        keys: list[Any] = [one for one in periods if one is not None]
        if None in periods:
            keys.append(never)
        return func.coalesce(built.time.expr, never).in_(keys)
    raw = date_expr(built.time.key)
    ranges: list[Any] = []
    for one in periods:
        if one is None:
            ranges.append(raw.is_(None))
        else:
            after = next_period(one, built.time.grain)
            # 9999년의 마지막 기간은 다음이 없다(「종료 없음」 으로 흔히 쓴다) — 끝을 연다.
            ranges.append((raw >= one) & (raw < after) if after is not None else raw >= one)
    # 부분 색인(`WHERE type_id = '<uuid>' AND deleted_at IS NULL`)이 맞물리게 원천 타입도
    # 글자로 한 번 더 — 매개변수로는 플래너가 색인의 조건을 증명하지 못할 때가 있다.
    typed = text(f"objects.type_id = '{uuid.UUID(str(built.source.id))}'::uuid")
    return and_(typed, or_(*ranges))


def next_period(start: date, grain: str) -> date | None:
    """다음 기간의 시작일 — 날짜의 끝(9999년)을 넘으면 None."""
    try:
        if grain == "day":
            return start + timedelta(days=1)
        if grain == "week":
            return start + timedelta(days=7)
        months = {"month": 1, "quarter": 3, "year": 12}[grain]
        total = start.year * 12 + (start.month - 1) + months
        return date(total // 12, total % 12 + 1, 1)
    except (OverflowError, ValueError):
        return None
