"""지표 응답이 싣는 **같은 조건의 화면 주소**(`link`) — 바깥 AI 가 MCP 로 받은 표 · 추이 ·
코호트 · 분석을, 사람이 눌러 같은 화면에서 확인하게 한다.

주소는 지표 화면(`frontend/src/modules/metrics/MetricDetailPage.tsx`)이 **처음 한 번 읽는
이름**으로만 짓는다. 요청에는 있었지만 화면이 주소로 받지 못하는 인자는 주소에서 빼고 그 이름을
`link_missing` 으로 함께 낸다 — 말없이 빼면 사람은 연 화면이 답과 같은 조건이라고 믿고, 수가
다를 때 어느 쪽이 맞는지 가릴 길이 없다.

- 경로는 라우터 기준(`/metrics/<slug>?…`, 접두어 없음) — 경보 · 눈여겨볼 것의 `link` 와 같은
  모양 · 인코딩이다(`href` 하나로 짓는다).
- `compact`(MCP 의 줄인 답)는 화면에 뜻이 없어 늘 뺀다.
- API 가 받지 않는 인자(추이에 준 `dims` 처럼 다른 모양의 것)는 답에도 안 쓰였으므로 조용히
  뺀다 — 빠진 것으로 세지 않는다.
- 화면의 기본이 API 의 기본과 다른 인자(표의 `dims`, 코호트의 `cumulative`, 수명의 `basis`)는
  요청이 비웠어도 API 가 쓴 값을 **적는다** — 안 적으면 화면이 제 기본으로 다른 것을 연다.
- 화면이 못 받는 인자라도 API 의 기본값과 같으면 잃은 것이 없다 — 빠진 것으로 세지 않는다.

**화면을 고치면 여기도.** 화면이 주소에서 새 이름을 읽게 되면 `_table` · `_series` ·
`_cohort_tab` 또는 `ANALYSES` 의 그 레시피에 더한다 — 안 더하면 그 인자는 화면이 읽는데도 계속
「빠짐」 으로 나가고, AI 는 사람에게 없는 차이를 말한다. API 에 인자를 더하면 `ANALYSES` 에도
더한다(시험
`tests/unit/test_metric_links.py` 가 분석 경로의 인자와 이 표를 견준다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode

from fastapi import Request

from app.modules.metrics.params import FILTER_PREFIX, names
from app.modules.metrics.recipes import assoc, changes, forecast, sprt
from app.modules.metrics.schemas import ReadHeader

#: API 기본값의 종류 — 쿼리 글자를 이 값과 견준다.
Default = str | int | float | bool | None

#: 표의 「기간으로 묶기」 가 고를 수 있는 것 — 화면은 하나만, `age` 는 없다.
TABLE_BY = ("period", "cohort")

#: 기간 범위 — 상세 화면 위의 「기간 시작 · 끝」 이 읽고, 표 · 추이 · 분석에 함께 건다.
PERIOD_RANGE = ("period_from", "period_to")

#: 코호트 범위 — 화면 어디에도 고르는 자리가 없다.
_COHORT: dict[str, Default] = {"cohort_from": None, "cohort_to": None}

_TRUE = {"1", "true", "t", "yes", "y", "on"}


@dataclass(frozen=True)
class Screen:
    """분석 하나를 화면(`analysis/*View.tsx`)이 주소에서 어떻게 여는가."""

    reads: frozenset[str]
    """분석 보기가 `initial` 에서 읽는 인자 — 보기의 `initial.<이름>`."""
    unread: Mapping[str, Default] = field(default_factory=dict)
    """API 는 받지만 보기가 안 읽는 인자와 그 API 기본값."""
    ranged: bool = True
    """API 가 기간 범위(`period_from` · `period_to`)를 받나 — 화면은 위의 기간으로 건다."""
    fill: Mapping[str, str] = field(default_factory=dict)
    """요청이 비웠을 때 API 가 쓴 값 — 보기의 기본이 그것과 달라 늘 적는다."""
    bools: frozenset[str] = frozenset()
    """참 · 거짓 인자 — 화면은 `'true'` 만 참으로 읽으므로 그 글자로 고쳐 적는다."""
    drops: tuple[str, ...] = ()
    """이 인자가 고른 기준(들)의 거르기를 보기가 빼고 보낸다 — 그 `d.<기준>` 은 빠진다."""


#: 레시피(분석 경로의 이름 — 값마다 훑기는 `<레시피>/scan`)마다. **보기를 고치면 여기도**
#: (`ParetoView` · `LifeView` · `ControlView` · `ChangesView` · `SprtView` · `GroupsView` ·
#: `CutinView` · `ForecastView` · `CoverageView` · `RecurrenceView` · `ProfileView` ·
#: `LogitView` · `AssocView`).
ANALYSES: dict[str, Screen] = {
    "pareto": Screen(
        reads=frozenset({"dim", "by_period", "compare_from", "compare_to"}),
        unread={"top": 30, "include_empty": False, **_COHORT},
        bools=frozenset({"by_period"}),
    ),
    # 보기는 방문 차례 기준이 있으면 첫 방문만 세는 것이 기본 — API 의 기본(기록마다)과 다르다.
    # 첫 방문 기준일 때 보기가 방문 차례의 거르기를 빼고 보내도 잃는 것이 없다 — 서버가 그
    # 거르기를 1 로 덮는다(life._prepare). 그래서 빠진 것으로 세지 않는다.
    "life": Screen(
        reads=frozenset({"model", "max_age", "basis"}),
        unread=_COHORT,
        ranged=False,
        fill={"basis": "records"},
    ),
    "control": Screen(
        reads=frozenset({"axis", "window", "split", "baseline_to"}),
        unread=_COHORT,
    ),
    "changes": Screen(reads=frozenset({"axis", "window"}), unread=_COHORT),
    "changes/scan": Screen(
        reads=frozenset({"by", "axis", "window"}),
        unread={"top": changes.SCAN_TOP, **_COHORT},
    ),
    # 모델 기준은 보기가 분모 짝의 첫 기준으로 정한다 — 그 값을 준 요청은 `same` 으로 받는다.
    "sprt": Screen(
        reads=frozenset({"target", "reference", "reference_via", "rho"}),
        unread={"dim": None, "alpha": sprt.ALPHA, "beta": sprt.BETA},
        ranged=False,
    ),
    "sprt/scan": Screen(
        reads=frozenset({"by", "target", "reference", "reference_via", "rho"}),
        unread={"dim": None, "alpha": sprt.ALPHA, "beta": sprt.BETA, "top": sprt.SCAN_TOP},
        ranged=False,
    ),
    "groups": Screen(
        reads=frozenset({"dim", "axis", "window"}),
        unread=_COHORT,
        drops=("dim",),
    ),
    "cutin": Screen(
        reads=frozenset({"at", "axis", "window", "skip_first", "effect"}),
        unread=_COHORT,
    ),
    "forecast": Screen(
        reads=frozenset({"horizon", "warranty", "cost"}),
        unread={
            "model": "auto",
            "basis": "records",
            "backtest": forecast.BACKTEST,
            **_COHORT,
        },
        ranged=False,
    ),
    "coverage": Screen(reads=frozenset({"levels", "via"}), unread=_COHORT),
    "recurrence": Screen(reads=frozenset({"generation", "signature", "via"}), unread=_COHORT),
    "profile": Screen(reads=frozenset({"dim", "value"}), unread=_COHORT, drops=("dim",)),
    "logit": Screen(
        reads=frozenset({"factors", "min_count"}),
        unread=_COHORT,
        drops=("factors",),
    ),
    "assoc": Screen(
        reads=frozenset({"rows", "cols"}),
        unread={"min_count": assoc.MIN_COUNT, **_COHORT},
        drops=("rows", "cols"),
    ),
}


def href(slug: str, pairs: Iterable[tuple[str, str]]) -> str:
    """지표 화면 주소 — 경보 · 눈여겨볼 것 · 읽기 응답의 `link` 가 모두 이것으로 짓는다(같은
    인코딩이어야 같은 인자가 같은 주소가 된다)."""
    return f"/metrics/{slug}?{urlencode(list(pairs))}"


def truthy(value: str | None) -> bool:
    """FastAPI 가 참으로 읽는 글자 — 거짓 · 빈 값 · 없음은 거짓."""
    return (value or "").strip().lower() in _TRUE


def _same(value: str, default: Default) -> bool:
    """준 값이 API 기본값과 같나 — 같으면 화면이 그 인자를 못 받아도 잃은 것이 없다."""
    if default is None:
        return value == ""
    if isinstance(default, bool):
        return truthy(value) is default
    if isinstance(default, int | float):
        try:
            return float(value) == float(default)
        except ValueError:
            return False
    return value == default


def _ranges(asked: Mapping[str, str], keys: Iterable[str]) -> list[tuple[str, str]]:
    return [(key, asked[key]) for key in keys if asked.get(key)]


def _missing(asked: Mapping[str, str], keys: Iterable[str]) -> list[str]:
    return [key for key in keys if asked.get(key)]


def _table(asked: Mapping[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    # 화면의 기본은 첫 기준으로 묶기, API 의 기본은 안 묶기 — 늘 적는다(비운 것은 `dims=`).
    pairs = [("dims", ",".join(names(asked.get("dims"))))]
    missing: list[str] = []
    by = names(asked.get("by"))
    if len(by) == 1 and by[0] in TABLE_BY:
        pairs.append(("by", by[0]))
    elif by:
        missing.append("by")  # `age` · 여럿 — 화면의 「기간으로 묶기」 는 하나만 고른다
    if asked.get("composition"):
        pairs.append(("composition", asked["composition"]))
    pairs += _ranges(asked, PERIOD_RANGE)
    missing += _missing(asked, _COHORT)
    return pairs, missing


def _series(asked: Mapping[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    pairs: list[tuple[str, str]] = []
    if asked.get("split"):
        pairs.append(("split", asked["split"]))
    if truthy(asked.get("composition")):
        pairs.append(("composition", "true"))
    pairs += _ranges(asked, PERIOD_RANGE)
    return pairs, []


def _cohort_tab(asked: Mapping[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    # 화면의 기본은 누적, API 의 기본은 누적 안 함 — 늘 적는다.
    pairs = [("cumulative", "true" if truthy(asked.get("cumulative")) else "false")]
    return pairs, _missing(asked, _COHORT)


_TABS = {"table": _table, "series": _series, "cohort": _cohort_tab}


def _analysis(
    recipe: str,
    asked: Mapping[str, str],
    same: Mapping[str, str],
    chosen: Mapping[str, str] | None = None,
) -> tuple[list[tuple[str, str]], list[str], set[str]]:
    """(주소의 인자, 빠진 이름, 보기가 빼고 보내는 거르기).

    `chosen` 은 요청이 비워 **서버가 고른 값**(집단 비교의 기준처럼) — 보기가 읽는 이름이면
    주소에 적는다. 안 적으면 보기는 제 기본으로 다른 것을 골라, 같은 화면인 척하는 다른 분석이
    열린다(2026-11-07 검토)."""
    screen = ANALYSES[recipe]
    picked = {
        key: value
        for key, value in (chosen or {}).items()
        if value and not asked.get(key) and key in screen.reads
    }
    pairs: list[tuple[str, str]] = []
    missing: list[str] = []
    for key, value in asked.items():
        if key in screen.reads:
            if value != "":
                pairs.append(
                    (
                        key,
                        ("true" if truthy(value) else "false")
                        if key in screen.bools
                        else value,
                    )
                )
        elif key in PERIOD_RANGE and screen.ranged:
            if value:
                pairs.append((key, value))
        elif (
            key in screen.unread
            and not _same(value, screen.unread[key])
            and value != same.get(key)
        ):
            missing.append(key)
        # 그 밖은 API 도 안 받은 이름 — 답에 안 쓰였으니 빠진 것이 아니다.
    pairs += list(picked.items())
    for key, value in screen.fill.items():
        if not asked.get(key) and key not in picked:
            pairs.append((key, value))
    used = {**asked, **picked}
    dropped = {
        f"{FILTER_PREFIX}{name}" for key in screen.drops for name in names(used.get(key))
    }
    return pairs, missing, dropped


def screen_link(
    slug: str,
    tab: str,
    params: Iterable[tuple[str, str]],
    *,
    recipe: str | None = None,
    same: Mapping[str, str] | None = None,
    chosen: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """(화면 주소, 화면이 주소로 받지 못해 빠진 인자 이름).

    `tab` 은 `table` · `series` · `cohort` · `analysis`, 분석이면 `recipe` 에 경로의 이름
    (`pareto` · `sprt/scan` …). `params` 는 요청의 쿼리 그대로
    (`request.query_params.multi_items()` — 같은 이름이 두 번이면 FastAPI 처럼 뒤의 것).
    `same` 은 보기가 스스로 고르는 값 — 못 받는 인자라도 그 값이면 빠진 것이 아니다(순차 검정의
    모델 기준). `chosen` 은 요청이 비워 서버가 고른 값 — 주소에 적는다(집단 비교의 기준).
    """
    asked: dict[str, str] = {}
    for key, value in params:
        if key != "compact":
            asked[key] = value
    filters = [(key, value) for key, value in asked.items() if key.startswith(FILTER_PREFIX)]
    rest = {key: value for key, value in asked.items() if not key.startswith(FILTER_PREFIX)}
    if tab == "analysis":
        if recipe is None or recipe not in ANALYSES:
            raise ValueError(f"모르는 분석입니다: {recipe}")
        pairs, missing, dropped = _analysis(recipe, rest, same or {}, chosen)
        head = [("tab", "analysis"), ("recipe", recipe.split("/")[0])]
    else:
        pairs, missing = _TABS[tab](rest)
        dropped = set()
        head = [("tab", tab)]
    for key, value in filters:
        if key in dropped:
            missing.append(key)
        else:
            pairs.append((key, value))  # 빈 값은 「(비어 있음)」 — 그대로 둔다
    return href(slug, [*head, *pairs]), missing


def attach[Header: ReadHeader](
    out: Header,
    request: Request,
    tab: str,
    *,
    recipe: str | None = None,
    same: Mapping[str, str] | None = None,
    chosen: Mapping[str, str] | None = None,
) -> Header:
    """응답 머리에 화면 주소를 싣는다 — 읽기 · 분석 경로가 돌려주기 직전에."""
    out.link, out.link_missing = screen_link(
        out.slug,
        tab,
        request.query_params.multi_items(),
        recipe=recipe,
        same=same,
        chosen=chosen,
    )
    return out
