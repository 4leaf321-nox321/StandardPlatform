"""분석이 함께 쓰는 것 — 거절(422) · 주의(caveat) · 응답 머리.

**틀린 수를 낼 자리는 거절한다.** 셀 읽기가 상한에서 잘리면(순서 없이 잘린다) 그 위의 통계는
틀리고, 여러 값 기준을 묶지도 거르지도 않으면 한 기록이 값마다 한 번씩 들어 건수가 부푼다. 이
둘은 주의로 적고 넘어갈 일이 아니다 — 무엇을 바꾸면 되는지 말하고 거절한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes.schemas import CaveatOut
from app.modules.objects.models import ObjectInstance
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
        metric, built, frame.run, truncated=frame.truncated, denominator=frame.denominator
    )
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
