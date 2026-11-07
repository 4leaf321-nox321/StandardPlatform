"""지표 응답의 화면 주소(`links.screen_link`)의 순수한 부분 — 화면이 읽는 이름만 싣고, 못 받는
인자는 `link_missing` 으로 말한다(DB 없이). 분석 경로의 인자와 표가 어긋나지 않는지도 본다."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from fastapi.routing import APIRoute
from pydantic_core import PydanticUndefined

from app.modules.metrics import alerts, links
from app.modules.metrics.recipes import routes as recipe_routes


def _query(link: str) -> list[tuple[str, str]]:
    parts = urlsplit(link)
    return parse_qsl(parts.query, keep_blank_values=True)


def _open(
    tab: str,
    params: list[tuple[str, str]],
    *,
    recipe: str | None = None,
    same: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], list[str]]:
    link, missing = links.screen_link("cases", tab, params, recipe=recipe, same=same)
    return urlsplit(link).path, dict(_query(link)), missing


def test_표는_묶을_기준을_늘_적고_기간_묶기는_하나만_싣는다() -> None:
    path, got, missing = _open("table", [("compact", "true"), ("d.factory", "")])
    # 화면의 기본은 첫 기준으로 묶기, API 의 기본은 안 묶기 — 비운 것은 `dims=` 로 적는다.
    assert path == "/metrics/cases"
    assert got == {"tab": "table", "dims": "", "d.factory": ""} and missing == []
    _, got, missing = _open(
        "table",
        [
            ("dims", "symptom, factory"),
            ("by", "period"),
            ("composition", "symptom"),
            ("period_from", "2026-01-01"),
            ("period_to", "2026-04-01"),
            ("split", "factory"),  # 표 경로가 안 받는다 — 답에도 안 쓰였다
        ],
    )
    assert got == {
        "tab": "table",
        "dims": "symptom,factory",
        "by": "period",
        "composition": "symptom",
        "period_from": "2026-01-01",
        "period_to": "2026-04-01",
    }
    assert missing == []
    for by in ("age", "period,cohort"):
        _, got, missing = _open(
            "table", [("dims", "symptom"), ("by", by), ("cohort_from", "2025-01-01")]
        )
        assert "by" not in got and "cohort_from" not in got
        assert missing == ["by", "cohort_from"]


def test_추이는_선_나누기와_구성비를_싣는다() -> None:
    _, got, missing = _open(
        "series",
        [
            ("split", "symptom"),
            ("composition", "1"),
            ("dims", "x"),
            ("cohort_from", "2025-01-01"),
        ],
    )
    # 추이 경로가 안 받는 `dims` · `cohort_from` 은 조용히 빠진다(빠진 것이 아니다).
    assert got == {"tab": "series", "split": "symptom", "composition": "true"}
    assert missing == []
    _, got, _ = _open("series", [("composition", "false")])
    assert got == {"tab": "series"}


def test_코호트는_누적을_늘_적는다() -> None:
    # 화면의 기본은 누적, API 의 기본은 누적 안 함 — 안 적으면 화면이 다른 것을 연다.
    assert _open("cohort", [])[1] == {"tab": "cohort", "cumulative": "false"}
    assert _open("cohort", [("cumulative", "true")])[1]["cumulative"] == "true"
    _, got, missing = _open(
        "cohort",
        [("cumulative", "1"), ("cohort_to", "2026-01-01"), ("period_from", "2026-01-01")],
    )
    assert got == {"tab": "cohort", "cumulative": "true"} and missing == ["cohort_to"]


def test_분석은_보기가_읽는_인자만_싣고_기본값과_같으면_빠진_것이_아니다() -> None:
    _, got, missing = _open(
        "analysis",
        [
            ("dim", "symptom"),
            ("by_period", "1"),
            ("top", "30"),
            ("include_empty", "false"),
            ("period_from", "2026-01-01"),
            ("d.base_model", "S"),
            ("compact", "true"),
        ],
        recipe="pareto",
    )
    assert got == {
        "tab": "analysis",
        "recipe": "pareto",
        "dim": "symptom",
        "by_period": "true",
        "period_from": "2026-01-01",
        "d.base_model": "S",
    }
    assert missing == []
    _, got, missing = _open(
        "analysis",
        [
            ("dim", "symptom"),
            ("top", "10"),
            ("include_empty", "true"),
            ("cohort_from", "2026-01-01"),
        ],
        recipe="pareto",
    )
    assert set(got) == {"tab", "recipe", "dim"}
    assert missing == ["top", "include_empty", "cohort_from"]


def test_수명은_세는_기준을_늘_적고_기간_범위를_안_싣는다() -> None:
    _, got, missing = _open("analysis", [("period_from", "2026-01-01")], recipe="life")
    # 보기의 기본(방문 차례가 있으면 첫 방문)이 API 의 기본(기록마다)과 달라 늘 적는다.
    assert got == {"tab": "analysis", "recipe": "life", "basis": "records"} and missing == []
    _, got, _ = _open(
        "analysis", [("basis", "first_visits"), ("model", "weibull")], recipe="life"
    )
    assert got["basis"] == "first_visits" and got["model"] == "weibull"


def test_순차_검정은_보기가_고르는_모델_기준이면_빠진_것이_아니다() -> None:
    asked = [("target", "S"), ("reference", "A"), ("dim", "base_model"), ("alpha", "0.050")]
    _, got, missing = _open("analysis", asked, recipe="sprt", same={"dim": "base_model"})
    assert got == {"tab": "analysis", "recipe": "sprt", "target": "S", "reference": "A"}
    assert missing == []
    _, _, missing = _open(
        "analysis", [*asked, ("alpha", "0.01")], recipe="sprt", same={"dim": "model"}
    )
    assert missing == ["dim", "alpha"]
    # 값마다 훑기 — 주소는 같은 분석에 `by` 로(보기가 훑기로 연다).
    _, got, missing = _open(
        "analysis", [("by", "symptom"), ("target", "S"), ("top", "5")], recipe="sprt/scan"
    )
    assert got == {"tab": "analysis", "recipe": "sprt", "by": "symptom", "target": "S"}
    assert missing == ["top"]


def test_변화점은_훑기일_때만_by_를_싣는다() -> None:
    _, got, missing = _open(
        "analysis", [("by", "symptom"), ("top", "20"), ("window", "6")], recipe="changes/scan"
    )
    assert got == {
        "tab": "analysis",
        "recipe": "changes",
        "by": "symptom",
        "window": "6",
    }
    assert missing == []
    # 훑기가 아닌 경로는 `by` 를 안 받는다 — 실으면 화면이 훑기로 연다.
    _, got, _ = _open("analysis", [("by", "symptom")], recipe="changes")
    assert "by" not in got


def test_보기가_빼고_보내는_거르기는_빠진_것으로_말한다() -> None:
    _, got, missing = _open(
        "analysis",
        [("factors", "factory, symptom"), ("d.factory", "F1"), ("d.model", "S")],
        recipe="logit",
    )
    assert "d.factory" not in got and got["d.model"] == "S"
    assert missing == ["d.factory"]
    _, got, missing = _open(
        "analysis",
        [("rows", "symptom"), ("cols", "part"), ("min_count", "5"), ("d.part", "P1")],
        recipe="assoc",
    )
    assert got == {"tab": "analysis", "recipe": "assoc", "rows": "symptom", "cols": "part"}
    assert missing == ["d.part"]
    _, _, missing = _open(
        "analysis", [("rows", "symptom"), ("cols", "part"), ("min_count", "3")], recipe="assoc"
    )
    assert missing == ["min_count"]


def test_집단_비교는_서버가_고른_기준을_주소에_적는다() -> None:
    """2026-11-07 검토 — 기준을 비우면 서버는 분모 짝의 첫 기준을, 보기는 제 첫 후보를 골라
    서로 다른 집단을 견줄 수 있었다. 서버가 쓴 기준을 적고, 그 기준의 거르기는 보기가 빼므로
    빠진 것."""
    link, missing = links.screen_link(
        "cases",
        "analysis",
        [("d.base_model", "S"), ("d.country", "KR")],
        recipe="groups",
        chosen={"dim": "country"},
    )
    got = dict(_query(link))
    assert got["dim"] == "country" and got["d.base_model"] == "S"
    assert "d.country" not in got and missing == ["d.country"]
    # 요청이 기준을 줬으면 그것 — 서버가 고른 값은 쓰지 않는다.
    link, _ = links.screen_link(
        "cases", "analysis", [("dim", "base_model")], recipe="groups", chosen={"dim": "x"}
    )
    assert dict(_query(link))["dim"] == "base_model"


def test_같은_이름이_두_번이면_뒤의_것_FastAPI_와_같다() -> None:
    _, got, _ = _open("series", [("split", "a"), ("split", "b"), ("d.x", "1"), ("d.x", "2")])
    assert got["split"] == "b" and got["d.x"] == "2"


def test_모르는_분석은_거절한다() -> None:
    with pytest.raises(ValueError):
        links.screen_link("cases", "analysis", [], recipe="nothing")


def test_경보의_링크는_그대로다() -> None:
    values = {"target": "s 1", "d.factory": "소음", "reference_via": "pre"}
    old = urlencode({"tab": "analysis", "recipe": "sprt", **values})
    assert alerts.link_of("cases", "sprt", values) == f"/metrics/cases?{old}"


def _analysis_routes() -> dict[str, APIRoute]:
    out: dict[str, APIRoute] = {}
    for route in recipe_routes.router.routes:
        assert isinstance(route, APIRoute)
        name = route.path.split("/analysis/", 1)[1]
        if name != "coverage/ways":  # 길 고르개 — 머리가 없는 응답
            out[name] = route
    return out


def test_분석_경로의_인자와_표가_어긋나지_않는다() -> None:
    """경로에 인자를 더하고 표에 안 더하면 그 인자는 주소에서 조용히 빠진다 — 빠졌다는 말도
    없이. 기본값이 바뀌면 「같으면 빠진 것이 아니다」 가 틀린 값과 견준다."""
    routes = _analysis_routes()
    assert set(routes) == set(links.ANALYSES)
    for name, route in routes.items():
        screen = links.ANALYSES[name]
        accepted = {one.alias for one in route.dependant.query_params} - {"compact"}
        ranges = set(links.PERIOD_RANGE) if screen.ranged else set()
        known = set(screen.reads) | set(screen.unread) | ranges
        assert accepted == known, (name, accepted ^ known)
        defaults = {one.alias: one.field_info.default for one in route.dependant.query_params}
        for key, default in screen.unread.items():
            wanted = None if defaults[key] is PydanticUndefined else defaults[key]
            assert wanted == default, (name, key, wanted, default)
            assert type(wanted) is type(default), (name, key)
        for key in screen.fill:
            assert defaults[key] == screen.fill[key], (name, key)
