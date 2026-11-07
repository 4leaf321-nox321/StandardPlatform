"""분석이 함께 쓰는 것 — 거절(422) · 주의(caveat) · 응답 머리.

**틀린 수를 낼 자리는 거절한다.** 셀 읽기가 상한에서 잘리면(순서 없이 잘린다) 그 위의 통계는
틀리고, 여러 값 기준을 묶지도 거르지도 않으면 한 기록이 값마다 한 번씩 들어 건수가 부푼다. 이
둘은 주의로 적고 넘어갈 일이 아니다 — 무엇을 바꾸면 되는지 말하고 거절한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query, schemas
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes.schemas import CaveatOut
from app.modules.objects import axes
from app.modules.objects.models import ObjectInstance
from app.modules.objects.schemas import FreshnessOut
from app.shared.errors import AppError, code
from app.shared.permissions import visible_owner_clause


def refuse(number: int, message: str) -> AppError:
    return AppError(code("METRICS", number), message, status=422)


class Caveats:
    """주의 모음 — 같은 코드는 한 번만, 경고가 앞에."""

    def __init__(self) -> None:
        self._items: dict[str, CaveatOut] = {}

    def add(
        self,
        code_value: str,
        message: str,
        *,
        level: Literal["info", "warn"] = "warn",
        count: int | None = None,
    ) -> None:
        if code_value not in self._items:
            self._items[code_value] = CaveatOut(
                code=code_value, level=level, message=message, count=count
            )

    def out(self) -> list[CaveatOut]:
        return sorted(self._items.values(), key=lambda one: one.level != "warn")


def require_whole(frame: query.Frame) -> None:
    if frame.truncated or (frame.denominator is not None and frame.denominator.truncated):
        raise refuse(
            20,
            "셀이 읽기 상한에서 잘렸습니다 — 잘린 셀 위의 통계는 틀립니다. 기준 값이나 "
            "기간으로 좁혀 다시 묻습니다.",
        )


def require_exact_counts(built: spec_module.Built, ask: query.Ask) -> None:
    """여러 값 기준은 묶거나(값마다 따로) 거르거나(그 값 하나) 해야 건수가 기록 수다."""
    for dim in built.dims:
        if dim.axis.multi and dim.name not in ask.dims and dim.name not in ask.filters:
            raise refuse(
                21,
                f"「{dim.axis.label}」 은 여러 값 기준이라 묶지도 거르지도 않으면 한 기록이 "
                f"값마다 한 번씩 셉니다 — 그 기준으로 거르거나(d.{dim.name}=값) 그 기준의 "
                "파레토를 봅니다.",
            )


def overlap_note(built: spec_module.Built, ask: query.Ask, caveats: Caveats) -> None:
    """나왔나만 보는 분석(커버리지 · 재발)은 여러 값 기준을 묶지 않아도 된다 — 조합이
    나왔는지는 그대로다. 다만 건수에는 한 기록이 그 기준의 값마다 들어간다 — 그렇게 말한다."""
    loose = [
        one.axis.label
        for one in built.dims
        if one.axis.multi and one.name not in ask.dims and one.name not in ask.filters
    ]
    if loose:
        caveats.add(
            "overlap_counts",
            f"건수에 겹침이 있습니다 — 「{', '.join(loose)}」 이 여러 값 기준이라 한 기록이 "
            "그 값마다 셉니다(나왔는지 · 다뤘는지는 그대로).",
            level="info",
        )


def dim_of(built: spec_module.Built, name: str) -> spec_module.Dim:
    """분석이 이름으로 고른 기준 — 없으면 있는 것을 말하고 거절한다."""
    found = built.dim(name)
    if found is None:
        raise refuse(
            23,
            f"이 지표에 없는 기준입니다: {name}. 있는 것: "
            f"{', '.join(one.name for one in built.dims) or '(없음)'}",
        )
    return found


def hidden_objects(db: Session, user: User, values: Iterable[str | None]) -> set[str]:
    """값 중 **보는 사람이 못 보는 객체**를 가리키는 것. 셀에서 온 값은 보이는 기록의 값이지만
    요청에 적힌 값(모델 고르기 · 요약할 값 · 첫 기준 거르기)은 아무 id 나 될 수 있다 — 그
    이름을 풀어 주면 안 보이는 부서 객체의 이름이 샜다(2026-10-08)."""
    if user.is_system_admin:
        return set()
    ids: dict[uuid.UUID, str] = {}
    for value in values:
        if value is None:
            continue
        try:
            ids[uuid.UUID(value)] = value
        except ValueError:
            continue
    if not ids:
        return set()
    rows = db.scalars(
        select(ObjectInstance.id).where(
            ObjectInstance.id.in_(list(ids)),
            ~visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    return {ids[one] for one in rows}


def given_labels(
    db: Session,
    user: User,
    built: spec_module.Built,
    name: str,
    values: Iterable[str | None],
) -> dict[str, dict[str, str]]:
    """요청에 적힌 값의 이름 — `query.labels_for` 와 같은 모양(`query.label_of` 로 읽는다).
    못 보는 객체는 이름을 풀지 않는다 — 받은 값 그대로 나간다."""
    wanted = [one for one in values if one is not None]
    hidden = hidden_objects(db, user, wanted)
    cells = [
        query.Cell({name: one}, None, None, None, 0, 0, None, None, None)
        for one in wanted
        if one not in hidden
    ]
    return query.labels_for(db, built, [name], cells)


def _covered(period_to: date | None, through: date, grain: str | None) -> bool:
    """읽은 범위(「앞까지」)가 원천 기록 끝의 다음 기간 이하에서 끝나나 — 그러면 보여 준
    기간은 모두 기록 끝 안이라 빈 기간이 없다."""
    if period_to is None or grain is None:
        return False
    after = axes.next_period(through, grain)
    return after is not None and period_to <= after


def _gap_head(through: date, grain: str) -> str:
    """미반영 구간의 머리 — 「2026-08 이후는 미반영 구간입니다(원천 최종 반영 기간 2026-07)」.
    「이후」 는 그 기간을 넣는다(원천 최종 반영 기간의 다음 기간부터)."""
    after = axes.next_period(through, grain)
    return (
        f"{axes.period_label(after.isoformat(), grain)} 이후는 미반영 구간입니다"
        f"(원천 최종 반영 기간 {axes.period_label(through.isoformat(), grain)})"
    )


def _gap_message(sides: list[str]) -> str:
    """쪽마다의 문장(「…수 있」 으로 끝난다) → 주의 한 줄. 앞쪽은 「…수 있습니다.」, 마지막은
    「…수 있으므로, 데이터 소스의 수집 현황을 확인해야 합니다.」 — 화면 `dataGapNotice` 와
    같은 말."""
    *before, last = sides
    return " ".join(
        [
            *(f"{one}습니다." for one in before),
            f"{last}으므로, 데이터 소스의 수집 현황을 확인해야 합니다.",
        ]
    )


def gap_note(built: spec_module.Built, frame: query.Frame, caveats: Caveats) -> None:
    """**원천 기록이 끊긴 뒤의 닫힌 기간**을 말한다(`query.has_gap` — 원천 기록 끝으로 판정)
    — 수는 그대로 둔다. 끝쪽 0건이 진짜일 때도 있다(대책 뒤 0건인 달들, `series` 의 설명).
    다만 원천이 멎어 생긴 0건이면 관리도 · 변화점 · 전후 비교 · 집단 비교가 그것을 하락으로
    읽으므로, 읽는 쪽이 원천을 먼저 확인하게 한다. 모든 분석이 이 머리를 지나므로 여기 한
    자리에서.

    - **읽은 범위가 원천 기록 끝 안에서 끝나면 그쪽은 말하지 않는다** — 보여 준 기간에는 빈
      기간이 없다(머리의 `data_gap` 은 지표 단위의 사실이라 그대로 선다).
    - 머무는 기간 지표는 셀 값이 0 이 아니다(기록을 N기간 펼쳐 둔다) — 「0건」 이 아니라 「새
      기록이 빠졌을 수 있다」.
    - 분모를 기간으로 짝짓지 않았으면(`time` 없음 — 전부 합 · 범위의 합) 기간마다의 분모가
      아니라 **분모 합**이 덜 든 것이다."""
    sides: list[str] = []
    through = query.source_through_of(frame.run)
    if (
        through is not None
        and built.grain is not None
        and query.has_gap(through, frame.before, built.grain)
        and not _covered(frame.period_to, through, built.grain)
    ):
        sides.append(
            _gap_head(through, built.grain)
            + (
                " — 이 구간의 값에는 새 기록이 빠졌을 수 있"
                if built.stay is not None
                else " — 이 구간의 0건 · 감소는 수집 지연 때문일 수 있"
            )
        )
    den = frame.denominator
    den_through = den.source_through() if den is not None else None
    if (
        den is not None
        and den_through is not None
        and den.grain is not None
        and den.gap()
        and not _covered(den.period_to, den_through, den.grain)
    ):
        sides.append(
            f"분모 「{den.metric.label}」 의 "
            + _gap_head(den_through, den.grain)
            + (
                " — 이 구간의 비율은 분모가 빠졌거나 덜 반영된 값일 수 있"
                if den.time is not None
                else " — 분모 합이 덜 반영되었을 수 있"
            )
        )
    if sides:
        caveats.add("data_gap", _gap_message(sides))


def _source_problem(one: FreshnessOut) -> str | None:
    """원천 하나의 수집 이상 — 수집 실패 · 수집 지연 · 적재 진행 중. 비활성 소스는 일부러
    멈춘 것이라 말하지 않는다(`freshness` 의 `active`)."""
    if not one.active:
        return None
    if one.failing:
        state = "수집 실패"
    elif one.stale:
        state = "수집 지연"
    elif one.catching_up:
        state = "적재 진행 중"
    else:
        return None
    last = (
        one.last_success_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if one.last_success_at is not None
        else "없음"
    )
    return f"원천 「{one.name}」 {state}(최종 수집 시각 {last})"


def source_note(
    freshness: list[FreshnessOut], den: schemas.DenominatorOut | None, caveats: Caveats
) -> None:
    """**원천 하나만 멎은 것**을 말한다(`source_issue` — 수집 이상) — 원천이 여럿이면 하나가
    멎어도 다른 것이 들어와 원천 기록 끝(최댓값)이 새것이라 빈 기간이 안 선다. 그 타입 · 분모
    타입에 넣는 데이터 소스 중 수집 실패 · 수집 지연 · 적재 진행 중인 것을 한 줄로. 수는 그대로
    둔다."""
    parts = [found for one in freshness if (found := _source_problem(one))]
    if den is not None:
        parts += [
            f"분모 「{den.label}」 의 {found}"
            for one in den.freshness
            if (found := _source_problem(one))
        ]
    if parts:
        caveats.add(
            "source_issue",
            " · ".join(parts) + " — 해당 원천의 새 기록이 집계에 빠졌을 수 있습니다.",
        )


def header(
    db: Session,
    user: User,
    metric: MetricDef,
    built: spec_module.Built,
    frame: query.Frame,
    *,
    recipe: str,
    method: str,
    params: dict[str, Any],
    caveats: Caveats,
    excluded: dict[str, int],
) -> dict[str, Any]:
    share = query.visible_share(db, user, metric)
    if share is not None and share < 0.999:
        caveats.add(
            "partial_visibility",
            f"이 지표의 기록 중 {share:.0%} 만 보입니다 — 분모가 전사 것이면 비율이 낮게 "
            "나옵니다. 공식 판단은 모든 부서를 보는 사람이 돌린 결과로 합니다.",
        )
    base = query.header_of(
        db, metric, built, frame.run, truncated=frame.truncated, denominator=frame.denominator
    )
    if base["data_gap"]:
        gap_note(built, frame, caveats)
    source_note(base["freshness"], base["denominator"], caveats)
    return {
        **base,
        "recipe": recipe,
        "method": method,
        "params": params,
        "run_id": frame.run.id if frame.run is not None else None,
        "caveats": caveats.out(),
        "excluded": {key: value for key, value in excluded.items() if value},
        "visible_share": share,
    }
