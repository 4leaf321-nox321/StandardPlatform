"""경보 — 저장한 분석을 계산마다 다시 돌려 **새로 나온 것**을 알린다(ADR 0016).

## 언제 도나

지표 계산이 커밋된 뒤 그 지표의 켜진 경보를 차례로(`after_recompute` —
`services.run_recompute` 가 부른다). 셀이 그대로면 결과도 그대로라 따로 타이머를 두지 않는다.
**경보 하나의 실패는 계산을 실패로 만들지 않는다** — 경보에 적고 처음 실패만 주인에게 알린다.

## 누구의 눈으로

주인의 눈으로 — 분석 함수에 주인을 사용자로 넘긴다. 알림도 주인에게만. 다른 사람에게 보내면 그
사람이 못 보는 부서의 수가 새고, 알림을 눌러 열면 다른 수가 보인다.

## 무엇이 「새로」 인가

발생마다 열쇠를 짓고 `(경보, 열쇠)` 를 유일로 적는다. 처음 보는 열쇠만 알린다.

- 순차 검정 — `worse:<모델>`(고르면 `not_worse:<모델>` 도). 결론은 누적이라 한 번 서면 계속
  서 있다.
- 관리도 — `<차트>:<부분군>:<규칙>`, 끝에서 `recent` 개 닫힌 부분군만. 한계는 자료가 늘면
  움직여서 지난 부분군의 신호가 새로 생기거나 사라진다 — 그것을 알리면 잡음이다.
- 변화점 — `<up|down>:<변화점>`, 끝에서 `recent` 기간 안의 것만. 같은 방향이 `CHANGE_SLACK`
  기간 안에서 움직인 것은 같은 변화점이다(자료가 한 기간 늘면 최적 분할의 자리가 한 칸 움직일
  수 있다).

처음 확인(만들 때)에서 본 것은 「처음부터 있던 것」(`baseline`)으로 적고 알리지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.metrics import compute, params, query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import ALERT_RECIPES, MetricAlert, MetricAlertEvent, MetricDef
from app.modules.metrics.recipes import changes, common, control, registry, series, sprt
from app.modules.notifications import services as notifications
from app.shared.errors import AppError, code

log = logging.getLogger(__name__)

RANGE_KEYS = frozenset({"period_from", "period_to", "cohort_from", "cohort_to"})
#: 분석마다 받는 인자 — 분석 경로의 쿼리와 같은 이름. **그 밖의 이름은 거절한다** — 오타가
#: 조용히 무시되면 경보가 사람이 본 것과 다른 것을 지켜본다.
ALLOWED: dict[str, frozenset[str]] = {
    "sprt": frozenset(
        {
            "target",
            "launched_within",
            "reference",
            "reference_via",
            "dim",
            "rho",
            "alpha",
            "beta",
            "notify_not_worse",
            # 값마다 훑기(증상마다) — `target` 과 함께만.
            "by",
            "top",
        }
    ),
    "control": frozenset({"axis", "window", "split", "baseline_to", "recent"}) | RANGE_KEYS,
    "changes": frozenset({"axis", "window", "recent", "by", "top"}) | RANGE_KEYS,
}
#: 끝에서 몇 부분군 · 기간 안의 것을 「새로」 로 보나.
RECENT_DEFAULT = {"control": 1, "changes": 6}
#: 같은 방향의 변화점이 이 기간 수 안에서 움직이면 같은 것이다.
CHANGE_SLACK = 2
#: 새 모델 훑기 — 한 번 확인에 모델마다 셀 읽기가 두 번이다. 넘으면 좁히라고 거절한다.
LAUNCH_MAX = 30
#: 알림 본문에 싣는 줄 수 — 나머지는 「외 N건」.
BODY_LINES = 5
#: 같은 경보를 동시에 확인한 다른 쪽과 부딪치면 새 스냅샷으로 다시 하는 횟수(`check_one`).
RACE_RETRIES = 2
#: 부딪침의 SQLSTATE — 같은 열쇠를 먼저 적음(유일 제약) · 같은 경보 줄을 먼저 고침(직렬화
#: 실패) · 교착.
RACE_STATES = frozenset({"23505", "40001", "40P01"})


def _refuse(number: int, message: str) -> AppError:
    return AppError(code("METRICS", number), message, status=422)


@dataclass
class Finding:
    key: str
    title: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    findings: list[Finding]
    notes: list[str]
    run_id: uuid.UUID | None
    positions: dict[str, int] = field(default_factory=dict)
    """변화점 — 부분군 시작일 → 차례. 「두 기간 안에서 움직인 같은 변화점」 을 가른다."""


# --- 인자 -------------------------------------------------------------------------------


def _int(values: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        found = int(raw)
    except ValueError as caught:
        raise _refuse(41, f"{name} 은(는) 정수여야 합니다: {raw}") from caught
    if not low <= found <= high:
        raise _refuse(41, f"{name} 은(는) {low}~{high} 사이여야 합니다: {raw}")
    return found


def _float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as caught:
        raise _refuse(41, f"{name} 은(는) 수여야 합니다: {raw}") from caught


def _flag(values: Mapping[str, str], name: str) -> bool:
    return values.get(name, "").lower() in {"1", "true", "yes", "on"}


def _axis(values: Mapping[str, str]) -> series.Axis | None:
    raw = values.get("axis")
    if raw is None:
        return None
    if raw not in ("period", "cohort"):
        raise _refuse(41, f"axis 는 period · cohort 중 하나입니다: {raw}")
    return cast(series.Axis, raw)


def clean(recipe: str, raw: Mapping[str, str]) -> dict[str, str]:
    """저장할 인자 — 빈 값은 뺀다(거르기 `d.<기준>` 의 빈 값은 「(비어 있음)」 이라 남긴다)."""
    if recipe not in ALERT_RECIPES:
        raise _refuse(
            40,
            f"경보는 {', '.join(ALERT_RECIPES)} 분석에서만 됩니다: {recipe} — 「새로 나온 "
            "것」 의 뜻이 서는 분석만 지켜봅니다.",
        )
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith(params.FILTER_PREFIX):
            out[key] = value
            continue
        if value == "":
            continue
        if key not in ALLOWED[recipe]:
            raise _refuse(
                41,
                f"{recipe} 경보에 없는 인자입니다: {key}. 있는 것: "
                f"{', '.join(sorted(ALLOWED[recipe]))}, d.<기준>",
            )
        out[key] = value
    if recipe == "sprt":
        if "by" in out and "target" not in out:
            raise _refuse(
                45,
                "값마다 훑기(by)는 새 모델(target)을 하나 정해야 합니다 — 새 모델 훑기와 함께 "
                "못 씁니다.",
            )
        if ("target" in out) == ("launched_within" in out):
            raise _refuse(
                42,
                "순차 검정 경보는 새 모델 하나(target) 또는 최근 출시 모델 훑기"
                "(launched_within) 중 하나를 줍니다.",
            )
        if "reference" not in out and "reference_via" not in out:
            raise _refuse(
                42,
                "전작을 reference=<값> 또는 reference_via=<전작을 가리키는 칸> 으로 줍니다.",
            )
    return out


def link_of(metric_slug: str, recipe: str, values: Mapping[str, str]) -> str:
    """분석 탭을 그 인자 그대로 연다 — 눌러서 본 것이 알림이 말한 것과 같아야 한다."""
    query_string = urlencode({"tab": "analysis", "recipe": recipe, **values})
    return f"/metrics/{metric_slug}?{query_string}"


def event_link(
    metric_slug: str, recipe: str, values: Mapping[str, str], detail: Mapping[str, Any]
) -> str:
    """발생 하나를 연다 — 새 모델 훑기면 **그 모델로**(훑기 자체는 분석 탭에 없다)."""
    target = detail.get("target")
    if recipe == "sprt" and "launched_within" in values and target:
        values = {
            **{key: value for key, value in values.items() if key != "launched_within"},
            "target": str(target),
        }
    return link_of(metric_slug, recipe, values)


# --- 분석마다의 열쇠 ----------------------------------------------------------------------


def _launched(
    db: Session,
    user: User,
    built: spec_module.Built,
    ask: query.Ask,
    dim: str,
    within: int,
) -> list[str]:
    """분모(판매 대수)의 첫 코호트가 최근 `within` 기간 안인 값 — 세 번 읽되 모두 작다
    (기간 수 · 모델 수만큼)."""
    den = built.denominator
    assert den is not None and built.cohort is not None
    measure = spec_module.MetricSpec.model_validate(den.spec).measure
    on = built.spec.denominator.on if built.spec.denominator is not None else []
    filters = {name: value for name, value in ask.filters.items() if name in on}
    limit = query.frame_limit()
    periods, _ = query.read(
        db, user, den, query.Ask(by=("period",), filters=filters), limit=limit
    )
    starts = [
        cell.period for cell in periods if cell.period is not None and cell.measure(measure)
    ]
    if not starts:
        return []
    since = query.advance(max(starts), -(within - 1), built.cohort.grain)

    def having(found: list[query.Cell]) -> set[str]:
        return {
            str(cell.dims[dim])
            for cell in found
            if cell.dims.get(dim) is not None and cell.measure(measure)
        }

    recent, _ = query.read(
        db, user, den, query.Ask(dims=[dim], filters=filters, period_from=since), limit=limit
    )
    earlier, _ = query.read(
        db, user, den, query.Ask(dims=[dim], filters=filters, period_to=since), limit=limit
    )
    return sorted(having(recent) - having(earlier))


def _sprt(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    values: Mapping[str, str],
) -> Outcome:
    reason = sprt.available(built)
    if reason is not None:
        raise common.refuse(28, reason)
    ask = params.ask_from_values(values)
    den_in = built.spec.denominator
    assert den_in is not None
    dim = values.get("dim") or den_in.on[0]
    rho = _float(values, "rho", sprt.RHO)
    alpha = _float(values, "alpha", sprt.ALPHA)
    beta = _float(values, "beta", sprt.BETA)
    if not (rho > 1.0 and 0.0 < alpha < 0.5 and 0.0 < beta < 0.5):
        raise _refuse(41, "rho 는 1 보다 크고, alpha · beta 는 0 과 0.5 사이입니다.")
    if values.get("by"):
        return _sprt_scan(db, user, metric, built, values, ask, dim, rho, alpha, beta)
    notes: list[str] = []
    if "target" in values:
        targets = [values["target"]]
        each = alpha
    else:
        within = _int(values, "launched_within", 6, 1, 120)
        if dim not in den_in.on:
            raise common.refuse(29, f"모델 기준 {dim} 이(가) 분모 짝(on)에 없습니다.")
        targets = _launched(db, user, built, ask, dim, within)
        if len(targets) > LAUNCH_MAX:
            raise _refuse(
                43,
                f"최근 {within}기간 안에 출시된 값이 {len(targets)}개라 한 번에 보기에 "
                f"많습니다(상한 {LAUNCH_MAX}) — launched_within 을 줄이거나 d.<기준> 으로 "
                "좁힙니다.",
            )
        # **여러 모델을 한꺼번에 보니 α 를 나눈다**(본페로니) — 열 개 중 하나가 우연히 「나쁨」
        # 이 되는 것을 막는다. 보수적이라 놓치는 쪽으로 기운다는 것을 알림 화면에 적는다.
        each = alpha / max(len(targets), 1)
        if not targets:
            notes.append(f"최근 {within}기간 안에 처음 팔린 모델이 없습니다.")
        elif len(targets) == 1:
            notes.append(f"최근 {within}기간 안에 처음 팔린 모델 1개를 봤습니다.")
        else:
            notes.append(
                f"최근 {within}기간 안에 처음 팔린 모델 {len(targets)}개를 봤습니다 — 여럿을 "
                "한꺼번에 보므로 잘못 「나쁨」 이라 할 확률을 모델 수로 나눴습니다"
                f"(모델마다 {each:.4f})."
            )
    findings: list[Finding] = []
    skipped: list[str] = []
    run_id: uuid.UUID | None = metric.current_run_id
    for target in targets:
        try:
            out = sprt.run(
                db,
                user,
                metric,
                built,
                ask,
                target=target,
                reference=values.get("reference"),
                reference_via=values.get("reference_via"),
                dim=dim,
                rho=rho,
                alpha=each,
                beta=beta,
                compact=True,
            )
        except AppError as caught:
            if "target" in values:
                raise
            skipped.append(f"{target}: {caught.message}")
            continue
        run_id = out.run_id
        if out.decision == "continue" or (
            out.decision == "not_worse" and not _flag(values, "notify_not_worse")
        ):
            continue
        numbers = f"실제 {out.observed:g}건 · 기대 {out.expected:.1f}건" + (
            f" · 표준화 비 {out.smr:.2f}" if out.smr is not None else ""
        )
        verdict = (
            f"전작 {out.reference_label} 보다 나쁨"
            if out.decision == "worse"
            else f"전작 {out.reference_label} 의 {rho:g}배만큼 나쁘지는 않음"
        )
        findings.append(
            Finding(
                key=f"{out.decision}:{target}",
                title=f"{out.target_label}: {verdict} — {numbers}",
                detail={
                    "target": target,
                    "target_label": out.target_label,
                    "reference": out.reference,
                    "reference_label": out.reference_label,
                    "decision": out.decision,
                    "decided_at": out.decided_at,
                    "observed": out.observed,
                    "expected": out.expected,
                    "smr": out.smr,
                },
            )
        )
    if skipped:
        notes.append(
            f"전작을 못 찾았거나 볼 수 없어 건너뛴 모델 {len(skipped)}개 — {skipped[0]}"
        )
    return Outcome(findings, notes, run_id)


def _sprt_scan(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    values: Mapping[str, str],
    ask: query.Ask,
    dim: str,
    rho: float,
    alpha: float,
    beta: float,
) -> Outcome:
    """증상마다 — 새 모델 하나를 전작과 값마다 견준다. 새 모델 훑기와 겹치면 수가 곱으로 늘어
    `target` 과 함께만 받는다."""
    if "target" not in values:
        raise _refuse(
            45,
            "값마다 훑기(by)는 새 모델(target)을 하나 정해야 합니다 — 새 모델 훑기와 함께 "
            "못 씁니다.",
        )
    out = sprt.scan(
        db,
        user,
        metric,
        built,
        ask,
        by=values["by"],
        target=values["target"],
        reference=values.get("reference"),
        reference_via=values.get("reference_via"),
        dim=dim,
        rho=rho,
        alpha=alpha,
        beta=beta,
        top=_int(values, "top", sprt.SCAN_TOP, 1, sprt.SCAN_MAX),
    )
    findings: list[Finding] = []
    for item in out.items:
        if item.decision == "continue" or (
            item.decision == "not_worse" and not _flag(values, "notify_not_worse")
        ):
            continue
        numbers = f"실제 {item.observed:g}건 · 기대 {item.expected:.1f}건" + (
            f" · 표준화 비 {item.smr:.2f}" if item.smr is not None else " · 전작에 없던 값"
        )
        verdict = (
            f"전작 {out.reference_label} 보다 나쁨"
            if item.decision == "worse"
            else f"전작 {out.reference_label} 의 {rho:g}배만큼 나쁘지는 않음"
        )
        findings.append(
            Finding(
                key=f"{item.decision}:{out.target}@{item.key}",
                title=f"{out.target_label} · {item.label}: {verdict} — {numbers}",
                detail={
                    "target": out.target,
                    "target_label": out.target_label,
                    "reference": out.reference,
                    "reference_label": out.reference_label,
                    "by": out.by,
                    "value": item.key,
                    "value_label": item.label,
                    "decision": item.decision,
                    "decided_at": item.decided_at,
                    "observed": item.observed,
                    "expected": item.expected,
                    "smr": item.smr,
                },
            )
        )
    notes = [f"{out.by_label} {out.scanned}개를 봤습니다 — 값마다 α {out.alpha_each:.4f}."]
    if out.skipped:
        notes.append(f"견주지 못한 값 {len(out.skipped)}개 — {out.skipped[0]}")
    return Outcome(findings, notes, out.run_id)


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.3g}"


def _control(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    values: Mapping[str, str],
) -> Outcome:
    recent = _int(values, "recent", RECENT_DEFAULT["control"], 1, 24)
    out = control.run(
        db,
        user,
        metric,
        built,
        params.ask_from_values(values),
        axis=_axis(values),
        window=_int(values, "window", 3, 1, 120),
        split=values.get("split"),
        baseline_to=params.parse_date(values.get("baseline_to"), "baseline_to"),
    )
    findings: list[Finding] = []
    for chart in out.charts:
        closed = [point for point in chart.points if point.closed]
        for point in closed[-recent:]:
            for rule in point.signals:
                shown = point.rate if point.rate is not None else float(point.count)
                rule_label = control.RULES.get(rule, str(rule))
                findings.append(
                    Finding(
                        key=f"{chart.key or '-'}:{point.when}:{rule}",
                        title=(
                            f"{chart.label} · {point.label}: {rule_label} — "
                            f"{_number(shown)}(한계 {_number(point.lcl)}~{_number(point.ucl)})"
                        ),
                        detail={
                            "chart": chart.key,
                            "chart_label": chart.label,
                            "when": point.when,
                            "rule": rule,
                            "count": point.count,
                            "rate": point.rate,
                            "lcl": point.lcl,
                            "ucl": point.ucl,
                        },
                    )
                )
    return Outcome(findings, [], out.run_id)


def _changes(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    values: Mapping[str, str],
) -> Outcome:
    recent = _int(values, "recent", RECENT_DEFAULT["changes"], 1, 60)
    if values.get("by"):
        return _changes_scan(db, user, metric, built, values, recent)
    out = changes.run(
        db,
        user,
        metric,
        built,
        params.ask_from_values(values),
        axis=_axis(values),
        window=_int(values, "window", 3, 1, 120),
    )
    closed = [point.when for point in out.points if point.closed]
    since = closed[-recent] if len(closed) >= recent else (closed[0] if closed else None)
    findings: list[Finding] = []
    for change in out.changes:
        if since is None or change.at < since:
            continue
        up = change.after > change.before
        ratio = f" · {change.ratio:.2f}배" if change.ratio is not None else ""
        findings.append(
            Finding(
                key=f"{'up' if up else 'down'}:{change.at}",
                title=(
                    f"{change.label}부터 {'올라감' if up else '내려감'}: "
                    f"{_number(change.before)} → {_number(change.after)}{ratio}"
                    + (" (잠정)" if change.provisional else "")
                ),
                detail={
                    "at": change.at,
                    "before": change.before,
                    "after": change.after,
                    "ratio": change.ratio,
                    "provisional": change.provisional,
                },
            )
        )
    positions = {point.when: index for index, point in enumerate(out.points)}
    return Outcome(findings, [], out.run_id, positions)


def _changes_scan(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    values: Mapping[str, str],
    recent: int,
) -> Outcome:
    """값마다 — 끝 몇 기간 안의 변화점. 열쇠에 값을 붙여, 같은 값의 이웃한 변화점만 같은 것으로
    본다(`_same_change`)."""
    out = changes.scan(
        db,
        user,
        metric,
        built,
        params.ask_from_values(values),
        by=values["by"],
        axis=_axis(values),
        window=_int(values, "window", 3, 1, 120),
        top=_int(values, "top", changes.SCAN_TOP, 1, changes.SCAN_MAX),
    )
    closed = [period.when for period in out.periods if period.closed]
    since = closed[-recent] if len(closed) >= recent else (closed[0] if closed else None)
    findings: list[Finding] = []
    positions: dict[str, int] = {}
    for item in out.items:
        for index, period in enumerate(out.periods):
            positions[f"{period.when}@{item.key}"] = index
        for change in item.changes:
            if since is None or change.at < since:
                continue
            up = change.after > change.before
            ratio = f" · {change.ratio:.2f}배" if change.ratio is not None else ""
            findings.append(
                Finding(
                    key=f"{'up' if up else 'down'}:{change.at}@{item.key}",
                    title=(
                        f"{item.label} — {change.label}부터 {'올라감' if up else '내려감'}: "
                        f"{_number(change.before)} → {_number(change.after)}{ratio}"
                        + (" (잠정)" if change.provisional else "")
                    ),
                    detail={
                        "by": out.by,
                        "value": item.key,
                        "value_label": item.label,
                        "at": change.at,
                        "before": change.before,
                        "after": change.after,
                        "ratio": change.ratio,
                        "provisional": change.provisional,
                    },
                )
            )
    notes = [
        f"{out.by_label} {out.scanned}개를 봤습니다 — 변화점 벌점 +{out.extra_penalty:.1f}."
    ]
    return Outcome(findings, notes, out.run_id, positions)


def evaluate(
    db: Session, user: User, metric: MetricDef, recipe: str, values: Mapping[str, str]
) -> Outcome:
    """분석 하나를 `user` 의 눈으로 돌려 열쇠 붙은 결론을 낸다 — 적지도 알리지도 않는다."""
    built = compute.built_of(db, metric)
    if recipe == "sprt":
        return _sprt(db, user, metric, built, values)
    if recipe == "control":
        return _control(db, user, metric, built, values)
    if recipe == "changes":
        return _changes(db, user, metric, built, values)
    raise _refuse(40, f"경보가 모르는 분석입니다: {recipe}")


def _same_change(key: str, seen: set[str], positions: dict[str, int]) -> bool:
    """같은 방향의 변화점이 `CHANGE_SLACK` 기간 안에 이미 있나."""
    direction, _, at = key.partition(":")
    here = positions.get(at)
    if direction not in ("up", "down") or here is None:
        return False
    value = at.partition("@")[2]  # 값마다 훑기면 그 값 — 다른 값의 변화점은 다른 것이다
    for old in seen:
        old_direction, _, old_at = old.partition(":")
        there = positions.get(old_at)
        if (
            old_at.partition("@")[2] == value
            and old_direction == direction
            and there is not None
            and abs(here - there) <= CHANGE_SLACK
        ):
            return True
    return False


#: 열쇠는 이만큼만 적는다(표의 칸 길이) — 견줄 때도 **같은 길이로** 견준다.
KEY_MAX = 300


def fresh(outcome: Outcome, seen: set[str], recipe: str) -> list[Finding]:
    """처음 보는 결론 — 한 확인 안의 같은 열쇠도 한 번만. 열쇠는 적는 길이로 잘라 견준다 —
    긴 기준 값(관리도의 나눔 · 증상 글)이면 자르지 않은 열쇠가 적어 둔 것과 안 맞아 같은
    결론을 다시 넣다 유일 제약에 걸렸고, 그 경보는 그 뒤로 매번 실패했다(2026-10-08)."""
    out: list[Finding] = []
    taken = set(seen)
    for one in outcome.findings:
        key = one.key[:KEY_MAX]
        if key in taken:
            continue
        if recipe == "changes" and _same_change(one.key, taken, outcome.positions):
            continue
        taken.add(key)
        out.append(one)
    return out


# --- 적기 · 알리기 --------------------------------------------------------------------------


def seen_keys(db: Session, alert_id: uuid.UUID) -> set[str]:
    return set(
        db.scalars(select(MetricAlertEvent.key).where(MetricAlertEvent.alert_id == alert_id))
    )


def record(
    db: Session, alert: MetricAlert, metric: MetricDef, outcome: Outcome, *, notify: bool
) -> list[MetricAlertEvent]:
    """새 결론을 적고(처음 확인이면 `baseline`) 알린다. **부르는 쪽이 커밋한다.**"""
    first = alert.last_checked_at is None
    added: list[MetricAlertEvent] = []
    for one in fresh(outcome, seen_keys(db, alert.id), alert.recipe):
        row = MetricAlertEvent(
            alert_id=alert.id,
            run_id=outcome.run_id,
            key=one.key[:KEY_MAX],
            title=one.title[:300],
            detail=one.detail,
            baseline=first,
        )
        db.add(row)
        added.append(row)
    if added and notify and not first:
        lines = [row.title for row in added[:BODY_LINES]]
        if len(added) > BODY_LINES:
            lines.append(f"외 {len(added) - BODY_LINES}건")
        notifications.notify(
            db,
            user_id=alert.owner_id,
            kind=notifications.METRIC_ALERT,
            title=f"{alert.name}: 새 결과 {len(added)}건"[:200],
            body="\n".join(lines),
            link=(
                event_link(metric.slug, alert.recipe, alert.params, added[0].detail)
                if len(added) == 1
                else link_of(metric.slug, alert.recipe, alert.params)
            ),
        )
    alert.last_checked_at = datetime.now(UTC)
    alert.last_run_id = outcome.run_id
    alert.last_status = "ok"
    alert.last_error = None
    return added


def _mark_failed(db: Session, alert_id: uuid.UUID, message: str) -> None:
    """실패를 **새 트랜잭션**으로 — 처음 실패만 알린다(데이터 소스와 같은 무늬)."""
    alert = db.get(MetricAlert, alert_id)
    if alert is None:
        return
    metric = db.get(MetricDef, alert.metric_id)
    if alert.last_status != "failed" and metric is not None:
        notifications.notify(
            db,
            user_id=alert.owner_id,
            kind=notifications.METRIC_ALERT_FAILED,
            title=f"경보를 확인하지 못했습니다: {alert.name}"[:200],
            body=message[:1000],
            link=link_of(metric.slug, alert.recipe, alert.params),
        )
    alert.last_checked_at = datetime.now(UTC)
    alert.last_status = "failed"
    alert.last_error = message
    db.commit()


def _raced(caught: Exception) -> bool:
    """같은 경보를 동시에 확인한 다른 쪽과 부딪쳤나(`RACE_STATES`)."""
    if not isinstance(caught, IntegrityError | OperationalError):
        return False
    return getattr(caught.orig, "sqlstate", None) in RACE_STATES


def check_one(db: Session, alert_id: uuid.UUID) -> int:
    """경보 하나 — 한 스냅샷으로 읽고, 새 결론을 적고 알리고 커밋한다. 새 결론 수를 돌려준다.
    실패는 경보에 적고 삼킨다(계산을 실패로 만들지 않는다).

    **같은 경보를 동시에 확인하면**(계산 둘이 겹침 · 증분과 전량) 둘 다 같은 스냅샷에서 같은
    새 열쇠를 보고 적는다 — 늦은 쪽은 유일 제약이나 직렬화 실패로 부딪친다. 그것은 실패가
    아니라 다른 쪽이 먼저 적은 것이다: 새 스냅샷으로 다시 돌면 그 열쇠는 이미 본 것이라
    건너뛴다. 예전에는 경보 전체를 「실패」 로 적고 실패 알림까지 보냈다(2026-10-08)."""
    for attempt in range(RACE_RETRIES + 1):
        query.snapshot(db)
        alert = db.get(MetricAlert, alert_id)
        if alert is None or not alert.is_active:
            db.rollback()
            return 0
        owner = db.get(User, alert.owner_id)
        metric = db.get(MetricDef, alert.metric_id)
        if owner is None or owner.status != "active" or metric is None:
            db.rollback()
            return 0
        try:
            outcome = evaluate(db, owner, metric, alert.recipe, alert.params)
            added = record(db, alert, metric, outcome, notify=True)
            db.commit()
        except AppError as caught:
            db.rollback()
            _mark_failed(db, alert_id, f"[{caught.code}] {caught.message}")
            return 0
        except job_services.Cancelled:
            # 작업을 빼앗겼다(`Lost` — 커밋 앞 검사) — 경보의 실패가 아니다. 「실패」 를 적으면
            # 그 커밋도 같은 검사에 막히고, 이어받은 워커가 다시 확인한다.
            db.rollback()
            raise
        except Exception as caught:
            db.rollback()
            if _raced(caught):
                if attempt < RACE_RETRIES:
                    continue
                # 다른 확인이 거듭 먼저 적는다 — 그쪽이 적고 알렸다. 이 경보의 실패가 아니다.
                log.warning("경보 확인이 다른 확인과 거듭 부딪쳐 넘깁니다: %s", alert_id)
                return 0
            log.exception("경보 확인 실패: %s", alert_id)
            _mark_failed(db, alert_id, f"{type(caught).__name__}: {caught}"[:2000])
            return 0
        return len(added)
    return 0  # pragma: no cover - 위 반복이 늘 돌려준다


def after_recompute(db: Session, metric_id: uuid.UUID) -> dict[str, int]:
    """계산이 커밋된 뒤 — 그 지표의 켜진 경보를 차례로, **경보마다 커밋.**"""
    ids = list(
        db.scalars(
            select(MetricAlert.id)
            .where(MetricAlert.metric_id == metric_id, MetricAlert.is_active.is_(True))
            .order_by(MetricAlert.created_at)
        )
    )
    db.rollback()
    found = 0
    for alert_id in ids:
        found += check_one(db, alert_id)
    return {"alerts": len(ids), "new": found}


# --- 읽기 -------------------------------------------------------------------------------


def recipe_label(recipe: str) -> str:
    found = registry.RECIPES.get(recipe)
    return found.label if found is not None else recipe


def event_counts(db: Session, alert_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not alert_ids:
        return {}
    rows = db.execute(
        select(MetricAlertEvent.alert_id, func.count())
        .where(MetricAlertEvent.alert_id.in_(alert_ids))
        .group_by(MetricAlertEvent.alert_id)
    ).tuples()
    return dict(rows.all())
