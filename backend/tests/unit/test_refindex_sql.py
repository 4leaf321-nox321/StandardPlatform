"""참조 색인 트리거의 **두 벌이 같은 글인가** — 모델 쪽 DDL(시험이 스키마를 세울 때)과 운영이
마이그레이션으로 거는 사본(`0049_object_refs` · `0075_refs_trigger`).

시험은 모델로 스키마를 세우므로 사본이 어긋나도 시험은 다 통과하고, 그 어긋남은 운영에서만
드러난다 — 그것도 오류가 아니라 「관련 객체」 에서 조용히 빠지는 줄로(ADR 0010). 그래서 글을
그대로 견준다. 함수를 고치면 새 마이그레이션에 같은 글을 싣고 여기서 그 판을 본다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.modules.objects import refindex

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_고치기_트리거는_0075_와_같은_글이다() -> None:
    assert _migration("0075_refs_trigger").UPDATED_FUNCTION == refindex.UPDATED_FUNCTION


def test_넣기_정의바꿈_트리거와_트리거_정의는_0049_와_같은_글이다() -> None:
    first = _migration("0049_object_refs")
    assert refindex.INSERTED_FUNCTION.strip() in first.FUNCTIONS
    assert refindex.REDEFINED_FUNCTION.strip() in first.FUNCTIONS
    assert refindex.TRIGGERS.strip() == first.TRIGGERS.strip()


def test_0075_를_되돌리면_0049_의_고치기_트리거다() -> None:
    first = _migration("0049_object_refs").FUNCTIONS
    old = first[first.index("CREATE OR REPLACE FUNCTION object_refs_updated()") :]
    old = old[: old.index("END $$;") + len("END $$;")]
    assert _migration("0075_refs_trigger").OLD_UPDATED_FUNCTION.strip() == old


def test_큰_문장은_그때_계획하고_작은_문장은_정적_SQL_이다() -> None:
    """두 갈래가 **같은 SQL** 을 돈다 — 한쪽만 고치면 크기에 따라 결과가 갈린다."""
    body = refindex.UPDATED_FUNCTION
    # plpgsql 의 갈래(줄 하나짜리 `ELSE`) — SQL 안의 `CASE … ELSE` 와 섞이지 않게 줄로 찾는다.
    dynamic = body[body.index("EXECUTE $sql$") : body.index("\n    ELSE\n")]
    static = body[body.index("\n    ELSE\n") : body.index("\n    END IF;")]
    for part in (dynamic, static):
        assert refindex._UPDATE_DELETE in part
        assert refindex._UPDATE_INSERT in part
    assert f"> {refindex.PLAN_EACH_ABOVE} THEN" in body
