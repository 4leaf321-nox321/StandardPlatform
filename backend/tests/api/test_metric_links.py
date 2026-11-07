"""지표 응답의 화면 주소 — 진짜 앱으로. 표 · 추이 · 코호트 · 분석 · 값마다 훑기가 「같은 조건의
화면」(`link`)과 「화면이 못 받아 빠진 인자」(`link_missing`)를 싣는다. 바깥 AI 가 MCP 로 받은
답을 사람이 눌러 확인하는 자리다 — 주소를 이루는 규칙은 단위 시험
(`tests/unit/test_metric_links.py`)이, 여기서는 경로마다 실리는 것을 본다.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_metric_alerts import _sprt_world
from tests.api.test_metrics import _read, _two, _world


def _get(client: TestClient, who: Signed, path: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/metrics/{path}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _opened(found: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """(경로, 쿼리) — 라우터 기준의 상대 주소다(접두어 없이 `/` 로 시작)."""
    link = found["link"]
    assert link.startswith("/metrics/"), link
    parts = urlsplit(link)
    return parts.path, dict(parse_qsl(parts.query, keep_blank_values=True))


def test_표_추이_코호트는_화면의_고르기로_연다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    slug = cases["slug"]

    plain = _read(client, admin, slug)
    path, asked = _opened(plain)
    # 요청이 안 묶었으면 `dims=` — 화면의 기본(첫 기준으로 묶기)으로 열리지 않게.
    assert path == f"/metrics/{slug}" and asked == {"tab": "table", "dims": ""}
    assert plain["link_missing"] == []

    narrowed = _read(
        client,
        admin,
        slug,
        dims="symptom",
        by="age",
        cohort_from="2026-01-01",
        compact="true",
        **{"d.base_model": w["s_base"]},
    )
    _, asked = _opened(narrowed)
    assert asked == {"tab": "table", "dims": "symptom", "d.base_model": w["s_base"]}
    # 화면은 경과로 묶지 못하고 코호트 범위를 고를 자리가 없다 — 그렇게 말한다.
    assert narrowed["link_missing"] == ["by", "cohort_from"]

    split = _read(client, admin, slug, "series", split="symptom", composition="true")
    assert _opened(split)[1] == {"tab": "series", "split": "symptom", "composition": "true"}
    assert split["link_missing"] == []

    cohort = _read(client, admin, slug, "cohort", cohort_to="2026-03-01")
    # 코호트는 누적을 늘 적는다 — 화면의 기본(누적)과 API 의 기본(누적 안 함)이 다르다.
    assert _opened(cohort)[1] == {"tab": "cohort", "cumulative": "false"}
    assert cohort["link_missing"] == ["cohort_to"]

    # 기준 값 목록(화면 고르개)은 화면이 없으니 안 싣는다.
    dims = _get(client, admin, f"{slug}/dims", name="symptom")
    assert "link" not in dims


def test_분석은_그_분석을_그_인자로_연다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    _, cases = _two(client, admin, w)
    slug = cases["slug"]

    pareto = _get(
        client,
        admin,
        f"{slug}/analysis/pareto",
        dim="symptom",
        top=5,
        include_empty="false",
        period_to="2026-04-01",
        compact="true",
    )
    _, asked = _opened(pareto)
    assert asked == {
        "tab": "analysis",
        "recipe": "pareto",
        "dim": "symptom",
        "period_to": "2026-04-01",
    }
    # `top` 은 고르는 자리가 없어 빠진다 — `include_empty=false` 는 기본값이라 잃은 것이 없다.
    assert pareto["link_missing"] == ["top"]

    assoc = _get(client, admin, f"{slug}/analysis/assoc", rows="symptom", cols="factory")
    assert _opened(assoc)[1] == {
        "tab": "analysis",
        "recipe": "assoc",
        "rows": "symptom",
        "cols": "factory",
    }
    assert assoc["link_missing"] == []


def test_순차_검정과_값마다_훑기는_보기가_고르는_모델_기준을_빠짐으로_세지_않는다(
    client: TestClient, admin: Signed
) -> None:
    w, cases, plant = _sprt_world(client, admin)
    plant(2)
    slug = cases["slug"]
    single = _get(
        client,
        admin,
        f"{slug}/analysis/sprt",
        target=w["s_base"],
        reference=w["a_base"],
        dim="base_model",  # 분모 짝의 첫 기준 — 보기가 스스로 고르는 것과 같다
        alpha="0.01",
        compact="true",
    )
    _, asked = _opened(single)
    assert asked == {
        "tab": "analysis",
        "recipe": "sprt",
        "target": w["s_base"],
        "reference": w["a_base"],
    }
    assert single["link_missing"] == ["alpha"]

    scan = _get(
        client,
        admin,
        f"{slug}/analysis/sprt/scan",
        by="symptom",
        target=w["s_base"],
        reference=w["a_base"],
    )
    _, asked = _opened(scan)
    assert asked["recipe"] == "sprt" and asked["by"] == "symptom"
    assert scan["link_missing"] == []

    life = _get(client, admin, f"{slug}/analysis/life", compact="true")
    # 수명 보기는 방문 차례가 있으면 첫 방문이 기본이다 — API 가 쓴 「기록마다」 를 적는다.
    assert _opened(life)[1] == {"tab": "analysis", "recipe": "life", "basis": "records"}
    assert life["link_missing"] == []
