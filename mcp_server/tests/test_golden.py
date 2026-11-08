"""정답 셋(`eval/golden/sp-golden-1.jsonl`)이 도구와 어긋나지 않는다 — 도구 이름 · 인자가
바뀌면 정답 호출이 조용히 낡는다. 값은 보지 않는다(자료에 따라 바뀐다 — 채점 때 정답 호출을
돌려 견준다)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from eval import golden

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
        for need in one["must_call"]:
            for name in need if isinstance(need, list) else [need]:
                assert hasattr(server, name), f"{one['id']}: must_call 의 없는 도구 {name}"


# --- 채점 규칙(`eval/golden.py`) — 서버 없이 ------------------------------------------------


def _item(how: str, **check: Any) -> dict[str, Any]:
    return {"id": "x", "q": "?", "kind": "k", "check": {"how": how, **check}}


def test_문제지는_모든_문항에_답의_꼴을_붙인다() -> None:
    items = _items()
    assert {one["check"]["how"] for one in items} <= set(golden.FORMS)
    sheet = golden.prompts(items)
    assert all(f"## {one['id']} " in sheet for one in items) and sheet.count("```json") == len(
        items
    )


def test_답의_마지막_json_블록을_읽는다() -> None:
    text = '앞\n```json\n{"total": 1}\n```\n뒤\n```json\n{"total": 2}\n```'
    assert golden.answer_json(text) == {"total": 2}
    assert golden.answer_json("json 없음") is None
    assert golden.answer_json("```json\n{깨짐}\n```") is None


def test_짐작은_확인을_청했을_때만_맞다() -> None:
    out = {"match": "none", "near": [{"label": "F5 폴드"}]}
    item = _item("near_first")
    assert golden.judge(item, out, {"asked": True, "candidate": "F5 폴드"})[0]
    assert not golden.judge(item, out, {"asked": False, "candidate": "F5 폴드"})[0]
    assert not golden.judge(item, out, None)[0]


def test_B10_이_없으면_숫자를_말한_답이_틀린다() -> None:
    item = _item("life_b10")
    gone = {"b_lives": [{"q": 0.1, "status": "unreachable", "age": None}]}
    assert golden.judge(item, gone, {"b10": None})[0]
    assert not golden.judge(item, gone, {"b10": 41.0})[0]
    seen = {"b_lives": [{"q": 0.1, "status": "observed", "age": 30.0}]}
    assert golden.judge(item, seen, {"b10": 32.0})[0]  # ±10%
    assert not golden.judge(item, seen, {"b10": 40.0})[0]


def test_관리도_신호는_빠짐도_더함도_없어야_맞다() -> None:
    item = _item("signals")
    out = {
        "charts": [
            {"label": "타이응우옌", "points": [{"label": "2025-04", "signals": [1]}]},
            {"label": "구미", "points": [{"label": "2025-04", "signals": []}]},
        ]
    }
    assert golden.judge(item, out, {"signals": {"타이응우옌": ["2025-04"]}})[0]
    assert not golden.judge(
        item, out, {"signals": {"타이응우옌": ["2025-04"], "구미": ["2025-04"]}}
    )[0]
    assert not golden.judge(item, out, {"signals": {}})[0]


def test_변화점은_한_달_어긋남까지_맞다() -> None:
    item = _item("change_near")
    out = {"changes": [{"label": "2025-11", "before": 0.7, "after": 1.3}]}
    assert golden.judge(item, out, {"changes": [{"at": "2025-12", "direction": "up"}]})[0]
    assert not golden.judge(item, out, {"changes": [{"at": "2025-11", "direction": "down"}]})[
        0
    ]
    assert not golden.judge(item, out, {"changes": [{"at": "2026-03", "direction": "up"}]})[0]


def test_위험_요인의_1위는_기준_불안정_그_밖을_뺀다() -> None:
    item = _item("logit_top")
    out = {
        "factors": [
            {
                "levels": [
                    {"label": "부품 교체", "odds_ratio": 1.0, "reference": True},
                    {"label": "점검 후 반환", "odds_ratio": 2.07},
                    {"label": "그 밖", "odds_ratio": 5.19, "pooled": 1},
                    {"label": "환불", "odds_ratio": 9.0, "unstable": True},
                ]
            }
        ]
    }
    assert golden.judge(item, out, {"top": "점검 후 반환"})[0]
    assert not golden.judge(item, out, {"top": "그 밖"})[0]


def test_세기는_칸마다_수가_같아야_맞다() -> None:
    item = _item("groups", top=2)
    out = {"buckets": [{"label": "대한민국", "count": 10}, {"label": "미국", "count": 7}]}
    assert golden.judge(item, out, {"groups": {"대한민국": 10, "미국": 7, "인도": 3}})[0]
    assert not golden.judge(item, out, {"groups": {"대한민국": 10, "미국": 8}})[0]
    assert not golden.judge(item, {"error": "x"}, {"groups": {}})[0]


def test_필수_도구는_목록_안의_목록이면_그중_하나다() -> None:
    item = {"must_call": ["object_resolve", ["metric_query", "metric_analyze"]]}
    assert golden.must_called(item, ["object_resolve", "metric_analyze"]) == []
    assert golden.must_called(item, ["metric_query"]) == ["object_resolve"]
    assert golden.must_called(item, ["object_resolve"]) == ["metric_query 또는 metric_analyze"]
