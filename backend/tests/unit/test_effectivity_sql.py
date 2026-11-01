"""선의 트리거의 **두 벌이 같은 글인가** — 모델 쪽 DDL(시험이 스키마를 세울 때,
`objects/effectivity.py` · `objects/pinning.py`)과 운영이 마이그레이션으로 거는 사본
(`0086_relation_periods` · `0088_lineage`).

시험은 모델로 스키마를 세우므로 사본이 어긋나도 시험은 다 통과하고, 그 어긋남은 운영에서만
드러난다 — 종류의 사본(`object_relations.effectivity`)이 안 맞으면 기간이 겹치는 같은 선이
조용히 들어가고(ADR 0039), 계보 선의 고정이 안 박히면 그 아래는 영영 낡지 않는다(ADR 0041).
그래서 글을 그대로 견준다 — 함수 · 트리거마다 **가장 나중 마이그레이션의 사본**과. 함수를
고치면 새 마이그레이션에 같은 글을 싣는다.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from app.modules.objects import effectivity, pinning

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
#: 사본을 실은 마이그레이션 — 차례대로(뒤가 앞을 덮는다).
COPIES = ("0086_relation_periods", "0088_lineage")

_FUNCTION = re.compile(r"CREATE OR REPLACE FUNCTION (\w+)\(")
_TRIGGER = re.compile(r"CREATE TRIGGER (\w+)")


def _migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _blocks(sql: str, pattern: re.Pattern[str]) -> dict[str, str]:
    """이름 → 그 문장(다음 같은 문장 앞까지, 앞뒤 공백 없이)."""
    found = list(pattern.finditer(sql))
    out: dict[str, str] = {}
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(sql)
        body = sql[match.start() : end]
        if pattern is _TRIGGER:
            body = body.split(";", 1)[0]
        out[match.group(1)] = body.strip()
    return out


def _latest(attr: str, pattern: re.Pattern[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for name in COPIES:
        merged.update(_blocks(getattr(_migration(name), attr), pattern))
    return merged


def test_함수는_가장_나중_마이그레이션의_사본과_같은_글이다() -> None:
    latest = _latest("FUNCTIONS", _FUNCTION)
    mine = _blocks(effectivity.FUNCTIONS + pinning.FUNCTIONS, _FUNCTION)
    assert set(mine) <= set(latest), set(mine) - set(latest)
    for name, body in mine.items():
        assert body == latest[name], name


def test_트리거는_가장_나중_마이그레이션의_사본과_같은_글이다() -> None:
    latest = _latest("TRIGGERS", _TRIGGER)
    mine = _blocks(effectivity.TRIGGERS + pinning.TRIGGERS, _TRIGGER)
    assert set(mine) <= set(latest), set(mine) - set(latest)
    for name, body in mine.items():
        assert body == latest[name], name
    # 0088 이 0086 의 트리거를 이름째 바꿨다 — 모델 쪽에 옛 이름이 다시 서면 둘이 함께 돈다.
    assert "object_relations_effectivity" not in mine


def test_트리거_글에_퍼센트가_없다() -> None:
    """시험은 `exec_driver_sql` 로 건다 — 드라이버가 글의 퍼센트를 자리표로 읽어 스키마를 못
    세웠다(2026-10-30). 메시지는 이어 붙인다."""
    for sql in (
        effectivity.FUNCTIONS,
        effectivity.TRIGGERS,
        pinning.FUNCTIONS,
        pinning.TRIGGERS,
    ):
        assert "%" not in sql
