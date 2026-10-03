"""경보(ADR 0016)의 순수한 부분 — 인자 거르기 · 「새로」 가르기 · 링크(DB 없이)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.modules.metrics import alerts
from app.shared.errors import AppError


def test_인자는_분석의_이름만_받고_빈_값은_뺀다() -> None:
    kept = alerts.clean(
        "control",
        {"axis": "period", "split": "", "d.factory": "", "d.model": "S", "recent": "2"},
    )
    # 거르기의 빈 값은 「(비어 있음)」 이라 남는다.
    assert kept == {"axis": "period", "d.factory": "", "d.model": "S", "recent": "2"}
    with pytest.raises(AppError) as caught:
        alerts.clean("control", {"target": "x"})
    assert caught.value.code.endswith("METRICS-0041")
    with pytest.raises(AppError) as caught:
        alerts.clean("life", {})
    assert caught.value.code.endswith("METRICS-0040")


def test_순차_검정은_모델_하나나_훑기_하나와_전작이_있어야_한다() -> None:
    for bad in (
        {"reference": "a"},
        {"target": "s", "launched_within": "6", "reference": "a"},
        {"target": "s"},
    ):
        with pytest.raises(AppError) as caught:
            alerts.clean("sprt", bad)
        assert caught.value.code.endswith("METRICS-0042")
    assert alerts.clean("sprt", {"launched_within": "6", "reference_via": "prev"}) == {
        "launched_within": "6",
        "reference_via": "prev",
    }


def _outcome(keys: list[str], positions: dict[str, int] | None = None) -> alerts.Outcome:
    return alerts.Outcome(
        [alerts.Finding(key, key) for key in keys], [], None, positions or {}
    )


def test_본_열쇠는_다시_안_보고_한_확인_안의_같은_열쇠도_한_번() -> None:
    found = alerts.fresh(_outcome(["worse:s", "worse:t", "worse:t"]), {"worse:s"}, "sprt")
    assert [one.key for one in found] == ["worse:t"]


def test_같은_방향의_변화점이_두_기간_안에서_움직이면_같은_것이다() -> None:
    positions = {f"2026-{month:02d}-01": month for month in range(1, 13)}
    seen = {"up:2026-05-01"}
    found = alerts.fresh(
        _outcome(
            ["up:2026-06-01", "up:2026-07-01", "down:2026-06-01", "up:2026-08-01"], positions
        ),
        seen,
        "changes",
    )
    # 6 · 7 월은 5 월의 오름과 두 기간 안 — 같은 변화점. 내림은 다른 방향, 8 월은 셋 떨어졌다.
    assert [one.key for one in found] == ["down:2026-06-01", "up:2026-08-01"]
    # 관리도에는 이 느슨함이 없다 — 부분군 · 규칙이 다르면 다른 신호다.
    assert len(alerts.fresh(_outcome(["up:2026-06-01"], positions), seen, "control")) == 1


def test_링크는_분석_탭을_그_인자로_연다() -> None:
    link = alerts.link_of("cases", "sprt", {"target": "s 1", "d.factory": "F1"})
    parts = urlsplit(link)
    assert parts.path == "/metrics/cases"
    assert parse_qs(parts.query, keep_blank_values=True) == {
        "tab": ["analysis"],
        "recipe": ["sprt"],
        "target": ["s 1"],
        "d.factory": ["F1"],
    }
