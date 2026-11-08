"""정답 셋(`eval/golden/sp-golden-1.jsonl`)이 도구와 어긋나지 않는다 — 도구 이름 · 인자가
바뀌면 정답 호출이 조용히 낡는다. 값은 보지 않는다(자료에 따라 바뀐다 — 채점 때 정답 호출을
돌려 견준다)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import server

GOLDEN = Path(__file__).resolve().parent.parent / "eval" / "golden" / "sp-golden-1.jsonl"
KEYS = {"id", "q", "kind", "gold", "check", "must_call", "must_not", "truth"}


def _items() -> list[dict[str, Any]]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()]


def test_문항마다_칸이_다_있고_번호가_겹치지_않는다() -> None:
    items = _items()
    assert len(items) >= 10
    for one in items:
        assert set(one) == KEYS, one["id"]
        assert one["q"].strip() and one["check"].get("how"), one["id"]
    assert len({one["id"] for one in items}) == len(items)


def test_정답_호출의_도구와_인자가_지금_도구와_맞는다() -> None:
    for one in _items():
        tool = getattr(server, one["gold"]["tool"], None)
        assert tool is not None, f"{one['id']}: 없는 도구 {one['gold']['tool']}"
        params = set(inspect.signature(tool).parameters) - {"ctx"}
        unknown = set(one["gold"]["args"]) - params
        assert not unknown, f"{one['id']}: {one['gold']['tool']} 이 모르는 인자 {unknown}"
        for name in one["must_call"]:
            assert hasattr(server, name), f"{one['id']}: must_call 의 없는 도구 {name}"
