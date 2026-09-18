"""자취 점수 — **수가 맞아야 추세를 믿는다.**

수를 잘못 세면 「좋아졌다」 가 거짓이 되고, 그 거짓 위에서 다음 결정이 내려진다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval import score as scoring


def _rows(*items: tuple[float, str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"ts": ts, "tool": tool, "ms": 10, **rest} for ts, tool, rest in items]


def test_조용하면_다른_대화로_센다() -> None:
    rows = _rows(
        (0.0, "get_guide", {"outcome": "ok"}),
        (5.0, "objects_list", {"outcome": "ok", "total": 3}),
        (900.0, "objects_list", {"outcome": "ok", "total": 3}),
    )
    got = scoring.score(rows)
    assert got["talks"] == 2
    # 둘 중 하나만 가이드로 시작했다.
    assert got["guide_first"] == 0.5


def test_후보가_여럿인데_그대로_쓰면_잡힌다() -> None:
    """**이것이 0 이 아니면 안내로는 안 막힌 것이다** — 그때는 서버가 막아야 한다."""
    rows = _rows(
        (0.0, "object_resolve", {"outcome": "ok", "match": "candidates"}),
        (1.0, "object_create", {"outcome": "ok"}),
    )
    got = scoring.score(rows)
    assert got["unresolved_write"] == 1
    assert got["blind_write"] == 0  # 해소는 했다 — 결과를 안 지켰을 뿐

    blind = _rows((0.0, "object_create", {"outcome": "ok"}))
    assert scoring.score(blind)["blind_write"] == 1


def test_빈_결과와_오류의_비율을_센다() -> None:
    rows = _rows(
        (0.0, "objects_list", {"outcome": "empty", "total": 0, "reason": "filters"}),
        (1.0, "objects_list", {"outcome": "ok", "total": 2}),
        (2.0, "object_create", {"outcome": "error", "code": "APP-OBJ-0007"}),
        (3.0, "object_get", {"outcome": "ok"}),
    )
    got = scoring.score(rows)
    assert got["empty"] == 0.25 and got["error"] == 0.25
    counts = {name: count for name, count, _empty, _error in scoring.by_tool(rows)}
    assert counts["objects_list"] == 2


def test_지난번과_견주어_방향을_말한다() -> None:
    now = {"empty": 0.1, "calls_per_talk": 8.0}
    before = {"empty": 0.3, "calls_per_talk": 6.0}
    table = scoring.report(now, before)
    assert "| empty | 0.1 | 0.3 | 좋아짐 |" in table
    assert "| calls_per_talk | 8.0 | 6.0 | 나빠짐 |" in table


def test_자취가_없으면_이유를_말하고_멈춘다(tmp_path: Path, capsys: Any) -> None:
    assert scoring.main([str(tmp_path / "없는파일.jsonl")]) == 2
    assert "MCP_TRACE_FILE" in capsys.readouterr().err


def test_깨진_줄이_있어도_나머지를_센다(tmp_path: Path) -> None:
    """자취는 서버가 돌다 죽어도 남는다 — 마지막 줄이 잘려 있을 수 있고, 그것 때문에
    앞의 자취를 통째로 못 읽으면 측정이 사라진다."""
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps({"ts": 1, "tool": "get_guide", "outcome": "ok"}) + "\n{잘린 줄\n",
        encoding="utf-8",
    )
    assert scoring.score(scoring.read(path))["calls"] == 1
