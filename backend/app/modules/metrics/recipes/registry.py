"""레시피 등록부 — 이름 하나가 분석 하나다. 레시피 모듈이 import 될 때 스스로 등록한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.modules.metrics import spec as spec_module
from app.modules.metrics.schemas import AnalysisAvailOut


@dataclass(frozen=True)
class Recipe:
    name: str
    label: str
    available: Callable[[spec_module.Built], str | None]
    """이 지표에서 안 되면 그 이유 한 줄 — 화면이 고르개를 끄고 말로 보여 준다."""


RECIPES: dict[str, Recipe] = {}


def register(recipe: Recipe) -> Recipe:
    RECIPES[recipe.name] = recipe
    return recipe


def availability(
    built: spec_module.Built | None, broken: str | None = None
) -> list[AnalysisAvailOut]:
    out: list[AnalysisAvailOut] = []
    for recipe in RECIPES.values():
        reason = (
            f"정의가 지금 안 지어집니다 — {broken}"
            if built is None
            else recipe.available(built)
        )
        out.append(
            AnalysisAvailOut(
                recipe=recipe.name, label=recipe.label, ok=reason is None, reason=reason
            )
        )
    return out
