"""눈여겨볼 것 — 플랫폼이 스스로 지표를 훑어 결론을 줄 세운다(ADR 0032).

경보(`alerts.py`)는 사람이 고른 분석 하나를 그 사람의 눈으로 돌려 알림으로 보낸다. 이것은
지표마다 **되는 분석 중 「변화 · 차이」 를 보는 것**(변화점 · 새 모델 순차 검정 · 집단 비교)을
기준 값마다 돌려, 결론을 경보와 같은 열쇠로 적고 점수로 줄 세운다. 알림은 보내지 않는다 —
화면 · AI 가 읽는다.

- **언제** — 지표를 다시 센 뒤 작업(`metric_insights`)으로. 계산 안에서 돌리지 않는다.
- **누구의 눈** — 시스템 관리자(전체). 그래서 결론은 **그 지표의 셀이 든 부서를 모두 보는
  사람**에게만 보인다(`visible_metric_ids`) — 일부만 보는 사람에게 보이면 못 보는 부서의 수가
  섞여 샌다.
- **「새로」** — (지표, 열쇠)가 유일. 처음 훑기의 것도 보인다(경보와 다르다). 이번에 다시 안
  나온 것은 「지남」.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from time import monotonic
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.metrics import alerts, compute, params, query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef, MetricInsight, MetricValue
from app.modules.metrics.recipes import changes, groups, series, sprt
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType
from app.modules.workspaces.models import WorkspaceMember
from app.shared.errors import AppError

log = logging.getLogger(__name__)

JOB_KIND = "metric_insights"

KIND_LABELS = {
    "change": "변화점",
    "worse": "새 모델이 전작보다 나쁨",
    "high": "다른 집단보다 높음",
}

#: 새 모델 훑기 — 최근 몇 기간 안에 처음 팔린 모델(경보의 기본과 같다).
LAUNCHED_WITHIN = 6
#: 집단 비교에서 「높음」 으로 적는 문턱(BH q).
GROUP_Q = 0.05
#: 같은 방향의 변화점이 이만큼 기간 안에서 움직이면 같은 것(경보와 같다).
CHANGE_SLACK = alerts.CHANGE_SLACK
#: 변화점은 끝에서 이만큼 기간 안의 것까지 본다 — 경보(6)보다 길다: 처음 훑기는 아무도 안 본
#: 곳이라 지난 한두 해의 변화도 소식이다. 오래된 것은 점수를 줄인다(`RECENT_FULL` ·
#: `HALF_LIFE`).
CHANGE_RECENT = 24
RECENT_FULL = 6
HALF_LIFE = 12
#: 조합 훑기 — 나누지 않는 기준(증상 · 부품 …)의 값을 많은 순으로 이만큼, 그 값마다 걸러 다시
#: 훑는다. 심은 신호는 대개 조합이다(「F5 폴드 x 힌지」, 「한 공장 x 충전 단자 x 몇 달」) —
#: 기준 하나씩 보면 전체 비율에 묻힌다.
COMBO_TOP = 15
#: 지표 하나의 훑기가 이보다 길면 조합 훑기를 거기서 멈추고 그렇다고 적는다.
SCAN_BUDGET_SECONDS = 600.0


@dataclass
class Plan:
    """돌릴 분석 하나 — 분석 · 인자 · 결론의 종류."""

    recipe: str
    values: dict[str, str]
    """분석의 인자 — 거르기(`d.<기준>`)까지. 링크가 그대로 연다."""
    kind: str
    tag: str = ""
    """조합 훑기면 거른 값(`|<기준>=<값>`) — 열쇠에 붙여 값마다 다른 결론이 되게."""
    prefix: str = ""
    """조합 훑기면 제목 앞의 거른 값(「주요 불량 힌지 · 」)."""
    looks: int = 1
    """같은 물음을 몇 번 거른 값으로 봤나 — 집단 비교의 문턱을 이것으로 나눈다."""
    combo: bool = False


@dataclass
class Found:
    key: str
    kind: str
    recipe: str
    values: dict[str, str]
    title: str
    detail: dict[str, Any]
    score: float
    run_id: uuid.UUID | None
    cell: frozenset[tuple[str, str]] = frozenset()
    """이 결론이 가리키는 기준 값들(거른 것 + 훑은 값) — 같은 칸을 가리키는 결론을 하나로."""
    combo: bool = False
    direction: str = ""
    """`up` · `down`(변화점) · `high`(집단) · `worse`."""
    at: str | None = None
    ratio: float | None = None
    support: float | None = None
    provisional: bool = False
    weight: float = 1.0
    """오래된 변화점의 줄임(`_recency`)."""


@dataclass
class ScanResult:
    plans: int = 0
    found: int = 0
    new: int = 0
    failed: list[str] = field(default_factory=list)


# --- 점수 ----------------------------------------------------------------------------------


def score_of(ratio: float | None, support: float | None, provisional: bool = False) -> float:
    """세기 x 근거 — **「몇 배 · 몇 건」 으로 설명되는 식만.** 세기는 |ln 비|(비를 모르면 1 —
    0 에서 생긴 변화처럼), 근거는 log10(1 + 건수), 잠정이면 절반."""
    strength = abs(math.log(ratio)) if ratio is not None and ratio > 0 else 1.0
    evidence = math.log10(1.0 + max(float(support or 0.0), 0.0))
    value = strength * evidence
    return round(value * (0.5 if provisional else 1.0), 4)


# --- 무엇을 돌리나 ------------------------------------------------------------------


def _reference_via(db: Session, dim: spec_module.Dim) -> str | None:
    """그 기준의 상대 타입에 **그 타입 자신을 가리키는 참조 칸**(전작)이 하나뿐이면 그 칸."""
    axis = dim.axis
    if axis.kind != "object_ref" or axis.ref_def is None or not axis.ref_def.ref_type_slug:
        return None
    slug = axis.ref_def.ref_type_slug
    target = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if target is None:
        return None
    selves = [
        one.key
        for one in properties_of(db, target.id)
        if one.data_type == "object_ref" and one.ref_type_slug == slug and not one.multi
    ]
    return selves[0] if len(selves) == 1 else None


def plans_of(
    db: Session, viewer: User, metric: MetricDef, built: spec_module.Built
) -> list[Plan]:
    """이 지표에서 돌릴 것 — 되는 분석만, 기준 값마다(ADR 0032 결정 2). 뒤의 것(조합)일수록
    비싸다 — 시간 상한이 거기서 끊는다."""
    out: list[Plan] = []
    spec = built.spec
    share = spec.measure == spec_module.SHARE
    rate = spec.denominator is not None or share
    can_change = rate and changes.available(built) is None
    can_group = groups.available(built) is None
    den = spec.denominator
    on = list(den.on) if den is not None else []
    # 변화점의 축은 분석의 기본(`series.default_axis`) — 분모가 코호트(생산월 · 판매월)와
    # 짝이면 코호트 축이다. 그때는 제목에 그 축을 적는다(「생산일 기준 · …」).
    axis_prefix = ""
    if series.default_axis(built) == "cohort" and built.cohort is not None:
        axis_prefix = f"{built.cohort.definition.label} 기준 · "
    change_axes: list[tuple[dict[str, str], str, str]] = [({}, "", axis_prefix)]
    # 변화점 — 비율 · 조건 비율만. 분모 없는 건수의 변화는 「판매가 늘어서」 와 못 가른다.
    if can_change:
        for axis_values, axis_tag, axis_prefix in change_axes:
            out.append(
                Plan("changes", dict(axis_values), "change", tag=axis_tag, prefix=axis_prefix)
            )
            out.extend(
                Plan(
                    "changes",
                    {"by": one.name, **axis_values},
                    "change",
                    tag=axis_tag,
                    prefix=axis_prefix,
                )
                for one in built.dims
            )
    # 새 모델 순차 검정 — 분모 짝 기준의 타입에 전작 칸이 있을 때.
    if sprt.available(built) is None and den is not None:
        for name in on:
            dim = built.dim(name)
            via = _reference_via(db, dim) if dim is not None else None
            if via is not None:
                out.append(
                    Plan(
                        "sprt",
                        {
                            "launched_within": str(LAUNCHED_WITHIN),
                            "dim": name,
                            "reference_via": via,
                        },
                        "worse",
                    )
                )
    # 집단 비교 — 분모 짝 기준마다(조건 비율이면 기준마다).
    compared = [one.name for one in built.dims] if share else on
    if can_group:
        out.extend(Plan("groups", {"dim": name}, "high") for name in compared)
    # 조합 — 나누지 않는 기준의 많은 값마다 걸러, 견주는 기준으로 다시.
    splits = list(built.dims) if share else [one for one in built.dims if one.name not in on]
    for split in splits:
        if not compared or not (can_group or can_change):
            break
        try:
            found = query.dim_values(db, viewer, metric, built, split.name, q=None)
        except AppError:
            continue
        top = [one for one in found.values if one.value is not None][:COMBO_TOP]
        for value in top:
            filters = {f"{params.FILTER_PREFIX}{split.name}": str(value.value)}
            tag = f"|{split.name}={value.value}"
            prefix = f"{found.label} {value.label} · "
            for name in compared:
                if name == split.name:
                    continue
                if can_group:
                    out.append(
                        Plan(
                            "groups",
                            {"dim": name, **filters},
                            "high",
                            tag=tag,
                            prefix=prefix,
                            looks=len(top),
                            combo=True,
                        )
                    )
                if can_change:
                    out.extend(
                        Plan(
                            "changes",
                            {"by": name, **filters, **axis_values},
                            "change",
                            tag=f"{tag}{axis_tag}",
                            prefix=f"{axis_prefix}{prefix}",
                            looks=len(top),
                            combo=True,
                        )
                        for axis_values, axis_tag, axis_prefix in change_axes
                    )
    return out


# --- 돌리기 --------------------------------------------------------------------------


def _recency(at: str | None, grain: str | None, today: date) -> float:
    """오래된 변화점일수록 점수를 줄인다 — 끝 `RECENT_FULL` 기간 안은 그대로, 그 뒤로는
    `HALF_LIFE` 기간마다 절반."""
    if not at:
        return 1.0
    try:
        when = date.fromisoformat(at[:10])
    except ValueError:
        return 1.0
    periods = (today - when).days / _PERIOD_DAYS.get(grain or "month", 31)
    return float(0.5 ** (max(periods - RECENT_FULL, 0.0) / HALF_LIFE))


def _from_outcome(
    plan: Plan, outcome: alerts.Outcome, grain: str | None, today: date
) -> list[Found]:
    out: list[Found] = []
    for one in alerts.fresh(outcome, set(), plan.recipe):
        detail = dict(one.detail)
        weight = 1.0
        if plan.recipe == "sprt":
            ratio = detail.get("smr")
            support = detail.get("observed")
            provisional = False
        else:
            ratio = detail.get("ratio")
            if ratio is None and detail.get("before"):
                ratio = float(detail.get("after") or 0) / float(detail["before"])
            support = detail.get("support")
            provisional = bool(detail.get("provisional"))
            weight = _recency(detail.get("at"), grain, today)
        values = dict(plan.values)
        if plan.recipe == "sprt" and detail.get("target"):
            # 결론 하나를 연다 — 훑기가 아니라 그 모델로(경보 발생의 링크와 같다).
            values = {
                **{key: value for key, value in values.items() if key != "launched_within"},
                "target": str(detail["target"]),
            }
        pinned = set(_filters(plan.values))
        if plan.recipe == "sprt" and detail.get("target"):
            pinned.add((plan.values.get("dim", ""), str(detail["target"])))
        elif detail.get("by") and detail.get("value") is not None:
            pinned.add((str(detail["by"]), str(detail["value"])))
        out.append(
            Found(
                key=f"{one.key}{plan.tag}"[: alerts.KEY_MAX],
                kind=plan.kind,
                recipe=plan.recipe,
                values=values,
                title=f"{plan.prefix}{one.title}"[:300],
                detail=detail,
                score=round(score_of(ratio, support, provisional) * weight, 4),
                run_id=outcome.run_id,
                cell=frozenset(pinned),
                combo=plan.combo,
                direction=one.key.partition(":")[0],
                at=detail.get("at"),
                ratio=ratio,
                support=support,
                provisional=provisional,
                weight=weight,
            )
        )
    return out


def _filters(values: dict[str, str]) -> list[tuple[str, str]]:
    return [
        (key[len(params.FILTER_PREFIX) :], value)
        for key, value in values.items()
        if key.startswith(params.FILTER_PREFIX)
    ]


def _groups(
    db: Session, user: User, metric: MetricDef, built: spec_module.Built, plan: Plan
) -> list[Found]:
    """집단 비교의 「높음」 — 조합으로 K 번 거른 값으로 봤으면 문턱을 K 로 나눈다(본페로니)."""
    result = groups.run(
        db,
        user,
        metric,
        built,
        params.ask_from_values(plan.values),
        dim=plan.values["dim"],
    )
    threshold = GROUP_Q / max(plan.looks, 1)
    out: list[Found] = []
    for row in result.rows:
        if row.flag != "high" or row.q_value >= threshold:
            continue
        ratio = row.ratio
        shown = f"{ratio:.2f}배" if ratio is not None else "높음"
        out.append(
            Found(
                key=f"high:{result.dim}@{row.key}{plan.tag}"[: alerts.KEY_MAX],
                kind=plan.kind,
                recipe="groups",
                values=dict(plan.values),
                title=(
                    f"{plan.prefix}{result.dim_label} {row.label}: 전체의 {shown} — "
                    f"{row.count:,}건 (q={row.q_value:.3g})"
                )[:300],
                detail={
                    "dim": result.dim,
                    "dim_label": result.dim_label,
                    "value": row.key,
                    "value_label": row.label,
                    "count": row.count,
                    "rate": row.rate,
                    "shrunk": row.shrunk,
                    "ratio": ratio,
                    "q_value": row.q_value,
                    "looks": plan.looks,
                },
                score=score_of(ratio, row.count),
                run_id=result.run_id,
                cell=frozenset({*_filters(plan.values), (result.dim, str(row.key))}),
                combo=plan.combo,
                direction="high",
                ratio=ratio,
                support=row.count,
            )
        )
    return out


def _near(a: str | None, b: str | None, periods: float, grain: str | None) -> bool:
    if not a or not b:
        return False
    try:
        days = abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return False
    return days <= periods * _PERIOD_DAYS.get(grain or "month", 31)


#: 전체 변화와 이만큼 기간 안의 같은 방향 변화는 전체가 움직인 만큼 빼고 본다.
EXPLAIN_PERIODS = 6


def _rest_ratio(base: Found, one: Found) -> float | None:
    """전체에서 그 값을 뺀 나머지의 앞뒤 비 — 같은 대수를 나누는 기준이라 비율이 더해질
    때만."""
    if one.combo:
        return None
    try:
        before = float(base.detail["before"]) - float(one.detail["before"])
        after = float(base.detail["after"]) - float(one.detail["after"])
    except (KeyError, TypeError, ValueError):
        return None
    if before <= 0 or after <= 0:
        return None
    return after / before


def tidy(
    founds: list[Found], grain: str | None, shared: frozenset[str] = frozenset()
) -> list[Found]:
    """결론을 다듬는다 — **사람이 읽을 줄만 남긴다**(ADR 0032).

    1. 전체가 움직인 만큼은 뺀다 — 전체(거르지도 훑지도 않은 것)에 같은 방향 변화가 가까이
       있으면 값마다의 비를 **나머지**의 비로 나눠 점수를 다시 낸다. 같은 대수를 나누는
       기준(`shared` — 증상처럼 분모 짝에 없는 것)은 비율이 더해지므로 「그 값을 뺀 나머지」
       의 비, 아니면(모델처럼 대수가 따로) 전체의 비. 그 값 때문에 전체가 오른 것을 그 값에서
       빼면 안 된다. 안 그러면 전체의 한 변화가 나라 · 센터 · 모델마다 수십 줄로 반복된다(개발
       DB 의 NTF 몫).
    2. 같은 칸을 가리키는 결론은 하나 — 「브라질 · P5」 와 「P5 · 브라질」.
    3. 거른 값이 아무것도 더하지 않는 조합은 뺀다 — 그 값 하나로도 이미 보이는 결론이면.
    """
    overall = [one for one in founds if one.kind == "change" and not one.cell and one.ratio]
    # 변화점의 축(기간 · 코호트)이 다르면 다른 물음이다 — 섞어 견주지 않는다.
    axis_of = {id(one): one.values.get("axis", "period") for one in founds}
    for one in founds:
        if one.kind != "change" or not one.cell or one.ratio is None:
            continue
        for base in overall:
            if axis_of[id(base)] != axis_of[id(one)]:
                continue
            if base.direction != one.direction or not _near(
                base.at, one.at, EXPLAIN_PERIODS, grain
            ):
                continue
            assert base.ratio is not None
            rest = _rest_ratio(base, one) if one.detail.get("by") in shared else None
            net = one.ratio / (rest if rest is not None else base.ratio)
            one.detail["overall_ratio"] = base.ratio
            if rest is not None:
                one.detail["rest_ratio"] = round(rest, 4)
            one.score = round(score_of(net, one.support, one.provisional) * one.weight, 4)
            break
    best: dict[tuple[Any, ...], Found] = {}
    for one in founds:
        signature = (one.kind, one.direction, one.at, one.cell, axis_of[id(one)])
        if signature not in best or best[signature].score < one.score:
            best[signature] = one
    kept = list(best.values())
    singles = [one for one in kept if not one.combo]
    out: list[Found] = []
    for one in kept:
        if one.combo and any(
            single.kind == one.kind
            and axis_of[id(single)] == axis_of[id(one)]
            and single.direction == one.direction
            and single.cell
            and single.cell < one.cell
            and (one.kind != "change" or _near(single.at, one.at, CHANGE_SLACK, grain))
            for single in singles
        ):
            continue
        out.append(one)
    return out


def system_viewer(db: Session) -> User | None:
    """훑기의 눈 — 처음 만든 활성 시스템 관리자(전체를 본다)."""
    return db.scalar(
        select(User)
        .where(User.is_system_admin.is_(True), User.status == "active")
        .order_by(User.created_at)
        .limit(1)
    )


def _last_period(db: Session, metric: MetricDef, built: spec_module.Built) -> date | None:
    """자료의 마지막 기간 — 「오래된 변화」 는 오늘이 아니라 이것에서 잰다(자료가 늦게 들어오는
    설치에서 오늘로 재면 모든 변화가 오래돼 보인다)."""
    run_id = metric.cells_run_id or metric.current_run_id
    if run_id is None:
        return None
    column = MetricValue.period if built.time is not None else MetricValue.cohort
    found = db.scalar(
        select(func.max(column)).where(
            MetricValue.metric_id == metric.id, MetricValue.run_id == run_id
        )
    )
    return found if isinstance(found, date) else None


def _owners(db: Session, metric: MetricDef) -> list[str | None]:
    run_id = metric.cells_run_id or metric.current_run_id
    if run_id is None:
        return []
    found = db.scalars(
        select(MetricValue.workspace_id)
        .where(MetricValue.metric_id == metric.id, MetricValue.run_id == run_id)
        .distinct()
    )
    return sorted((str(one) if one is not None else None for one in found), key=str)


#: 기간 하나의 대략의 날 수 — 변화점이 「두 기간 안에서」 움직였나를 날짜로 본다.
_PERIOD_DAYS = {"day": 1, "week": 7, "month": 31, "quarter": 92, "year": 366}


def _same_change(
    found: Found, existing: dict[str, MetricInsight], grain: str | None
) -> MetricInsight | None:
    """같은 방향 · 같은 값의 변화점이 `CHANGE_SLACK` 기간 안에 이미 있나 — 자료가 한 기간 늘면
    최적 분할의 자리가 한 칸 움직인다(경보 결정 4)."""
    if found.kind != "change":
        return None
    slack = CHANGE_SLACK * _PERIOD_DAYS.get(grain or "month", 31)
    direction, _, rest = found.key.partition(":")
    at, _, value = rest.partition("@")
    for row in existing.values():
        if row.kind != "change" or row.values != found.values:
            continue
        old_direction, _, old_rest = row.key.partition(":")
        old_at, _, old_value = old_rest.partition("@")
        if old_direction != direction or old_value != value:
            continue
        try:
            days = abs((datetime.fromisoformat(at) - datetime.fromisoformat(old_at)).days)
        except ValueError:
            continue
        if days <= slack:
            return row
    return None


def scan(db: Session, metric_id: uuid.UUID) -> ScanResult:
    """지표 하나를 훑어 적는다 — **분석 하나의 실패는 훑기를 멈추지 않는다**(그 줄만
    적는다)."""
    result = ScanResult()
    metric = db.get(MetricDef, metric_id)
    if metric is None or not metric.is_active or metric.insights_off:
        return result
    if metric.current_run_id is None:
        return result
    viewer = system_viewer(db)
    if viewer is None:
        result.failed.append("훑을 눈(활성 시스템 관리자)이 없습니다.")
        return result
    built = compute.built_of(db, metric)
    grain = (
        built.time.grain
        if built.time is not None
        else (built.cohort.grain if built.cohort is not None else None)
    )
    today = _last_period(db, metric, built) or datetime.now(UTC).date()
    started = monotonic()
    founds: list[Found] = []
    for plan in plans_of(db, viewer, metric, built):
        if plan.combo and monotonic() - started > SCAN_BUDGET_SECONDS:
            result.failed.append(
                f"시간 상한({SCAN_BUDGET_SECONDS:g}초)에 걸려 조합 훑기를 여기서 멈췄습니다."
            )
            break
        result.plans += 1
        try:
            if plan.recipe == "groups":
                founds.extend(_groups(db, viewer, metric, built, plan))
            else:
                asked = dict(plan.values)
                if plan.recipe == "changes":
                    asked["recent"] = str(CHANGE_RECENT)
                outcome = alerts.evaluate(db, viewer, metric, plan.recipe, asked)
                founds.extend(_from_outcome(plan, outcome, grain, today))
        except AppError as caught:
            result.failed.append(f"{plan.recipe} {plan.values}: {caught.message}")
        except Exception as caught:
            db.rollback()
            log.exception("눈여겨볼 것 훑기 실패: %s %s", metric.slug, plan)
            result.failed.append(f"{plan.recipe} {plan.values}: {type(caught).__name__}")
    now = datetime.now(UTC)
    existing = {
        row.key: row
        for row in db.scalars(
            select(MetricInsight).where(MetricInsight.metric_id == metric.id)
        )
    }
    taken: set[str] = set()
    den = built.spec.denominator
    shared = (
        frozenset(one.name for one in built.dims if one.name not in den.on)
        if den is not None and built.spec.measure != spec_module.SHARE
        else frozenset()
    )
    for found in sorted(tidy(founds, grain, shared), key=lambda one: -one.score):
        if found.key in taken:
            continue
        taken.add(found.key)
        row = existing.get(found.key) or _same_change(found, existing, grain)
        if row is None:
            row = MetricInsight(metric_id=metric.id, key=found.key, first_seen_at=now)
            db.add(row)
            existing[found.key] = row
            result.new += 1
        row.kind = found.kind
        row.recipe = found.recipe
        row.values = found.values
        row.title = found.title
        row.detail = {k: v for k, v in found.detail.items() if v is not None}
        row.score = found.score
        row.run_id = found.run_id
        row.last_seen_at = now
        result.found += 1
    metric.insights_scanned_at = now
    metric.insights_owners = _owners(db, metric)
    db.commit()
    return result


# --- 작업 · 보이는 범위 ----------------------------------------------------------


def request(db: Session, metric: MetricDef) -> Job | None:
    """그 지표의 훑기 작업을 넣는다 — 같은 지표의 대기 작업이 있으면 그것을. 부르는 쪽이
    커밋한다."""
    if metric.insights_off or not metric.is_active:
        return None
    waiting = db.scalar(
        select(Job).where(
            Job.kind == JOB_KIND,
            Job.status == "queued",
            Job.params["slug"].astext == metric.slug,
        )
    )
    if waiting is not None:
        return waiting
    return job_services.enqueue(
        db, kind=JOB_KIND, params={"slug": metric.slug}, user=None, workspace_id=None
    )


def visible_metric_ids(db: Session, user: User, metrics: list[MetricDef]) -> set[uuid.UUID]:
    """결론이 보이는 지표 — **셀이 든 부서를 모두 보는 사람에게만**(ADR 0032 결정 3)."""
    if user.is_system_admin:
        return {one.id for one in metrics}
    mine = {
        str(one)
        for one in db.scalars(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
        )
    }
    out: set[uuid.UUID] = set()
    for metric in metrics:
        owners = metric.insights_owners or []
        if all(owner is None or owner in mine for owner in owners):
            out.add(metric.id)
    return out


def link_of(metric_slug: str, row: MetricInsight) -> str:
    return alerts.link_of(metric_slug, row.recipe, row.values or {})
