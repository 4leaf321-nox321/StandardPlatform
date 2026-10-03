"""분석이 함께 쓰는 것 — 거절(422) · 주의(caveat) · 응답 머리.

**틀린 수를 낼 자리는 거절한다.** 셀 읽기가 상한에서 잘리면(순서 없이 잘린다) 그 위의 통계는
틀리고, 여러 값 기준을 묶지도 거르지도 않으면 한 기록이 값마다 한 번씩 들어 건수가 부푼다. 이
둘은 주의로 적고 넘어갈 일이 아니다 — 무엇을 바꾸면 되는지 말하고 거절한다.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.metrics import query
from app.modules.metrics import spec as spec_module
from app.modules.metrics.models import MetricDef
from app.modules.metrics.recipes.schemas import CaveatOut
from app.shared.errors import AppError, code


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
