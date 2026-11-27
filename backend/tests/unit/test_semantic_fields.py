"""뜻으로 찾기의 고른 칸이 없어졌을 때(ADR 0098) — 고른 칸이 모두 지워지거나 글이 아닌 종류로
바뀌면 글이 비어, 맞추기가 그 타입의 조각을 하루 안에 오류 없이 다 지웠다. 이제 그 타입은
건너뛰고(조각을 지킨다) 까닭을 남긴다."""

from __future__ import annotations

from app.modules.ontology.models import PropertyDef
from app.modules.semantic.services import _fields_gone


def _defs(**kinds: str) -> list[PropertyDef]:
    return [PropertyDef(key=key, data_type=kind) for key, kind in kinds.items()]


def test_고른_칸이_하나라도_읽히면_돈다() -> None:
    assert _fields_gone(_defs(symptom="text", qty="number"), ["symptom", "qty"]) == ""
    assert _fields_gone(_defs(paper="document"), ["paper"]) == ""


def test_이름을_고르거나_고른_것이_없으면_늘_돈다() -> None:
    assert _fields_gone([], ["label"]) == ""
    assert _fields_gone([], None) == ""
    assert _fields_gone([], []) == ""


def test_고른_칸이_모두_없거나_글이_아니면_건너뛴다() -> None:
    assert "symptom" in _fields_gone(_defs(), ["symptom"])
    assert "qty" in _fields_gone(_defs(qty="number"), ["qty"])
