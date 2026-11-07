"""원천 칸 변화 — 대응에 쓴 칸이 원천에서 사라지면 말하고(거울이면 적용을 멈추고), 원천에 새
칸이 생기면 한 번 적는다(ADR 0061, `datasources/drift.py`)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.modules.datasources import services
from tests.api.conftest import Signed
from tests.api.test_datasources import FakeRest, _rest_source, _sync, _vendor_type


@pytest.fixture
def upstream() -> Iterator[FakeRest]:
    fake = FakeRest()
    services.transport = httpx.MockTransport(fake)
    try:
        yield fake
    finally:
        services.transport = None


def _rename(rows: list[dict[str, Any]], old: str, new: str) -> None:
    for row in rows:
        row[new] = row.pop(old)


def _apply(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    done = _sync(client, admin, slug, apply=True)
    assert done.status_code == 200, done.text
    return dict(done.json())


def test_대응한_칸이_원천에서_사라지면_말하고_값은_그대로_둔다(
    client: TestClient, admin: Signed, upstream: FakeRest
) -> None:
    vendor = _vendor_type(client, admin)
    slug = _rest_source(client, admin, vendor, "suppliers", {"rows_path": ""})["slug"]
    first = _apply(client, admin, slug)
    assert first["run"]["status"] == "ok" and first["counts"]["create"] == 3
    assert not any("칸" in one for one in first["run"]["errors"]), first["run"]

    _rename(upstream.rows, "Rating", "Score")  # 원천이 칸 이름을 바꿨다
    again = _apply(client, admin, slug)
    run = again["run"]
    assert run["status"] == "ok" and again["counts"]["unchanged"] == 3, again["counts"]
    (gone,) = [one for one in run["errors"] if "원천에 대응한 칸이 없습니다" in one]
    assert gone.startswith("원천에 대응한 칸이 없습니다: Rating — 받은 3행 어디에도")
    assert "그 속성은 건드리지 않았습니다" in gone
    (new,) = [one for one in run["errors"] if "새 칸" in one]
    assert new.startswith("원천에 새 칸이 생겼습니다(대응에 없음): Score")


def test_거울은_대응한_칸이_사라지면_적용을_멈춘다(
    client: TestClient, admin: Signed, upstream: FakeRest
) -> None:
    """거울(바깥을 정본으로)은 빈 칸을 「비움」 으로 읽는다 — 그대로 적용하면 그 속성의 값을
    모두 지운다. 사람이 대응을 고치기 전에는 넣지 않는다."""
    vendor = _vendor_type(client, admin)
    options = {"rows_path": "", "mirror": True}
    slug = _rest_source(client, admin, vendor, "suppliers", options)["slug"]
    assert _apply(client, admin, slug)["run"]["status"] == "ok"

    _rename(upstream.rows, "Rating", "Score")
    stopped = _apply(client, admin, slug)["run"]
    assert stopped["status"] == "failed" and stopped["applied"] is False, stopped
    assert any("적용하지 않았습니다" in one for one in stopped["errors"]), stopped
    planned = _sync(client, admin, slug).json()["run"]  # 계획만 봐도 같은 말
    assert planned["status"] == "failed"

    _rename(upstream.rows, "Score", "Rating")  # 원천이 되돌렸다 — 다시 넣는다
    assert _apply(client, admin, slug)["run"]["status"] == "ok"


def test_원천에_새_칸이_생기면_한_번_적는다(
    client: TestClient, admin: Signed, upstream: FakeRest
) -> None:
    vendor = _vendor_type(client, admin)
    slug = _rest_source(client, admin, vendor, "suppliers", {"rows_path": ""})["slug"]
    assert _apply(client, admin, slug)["run"]["status"] == "ok"  # 처음은 기준만 남긴다
    for row in upstream.rows:
        row["Phone"] = "02-000-0000"
    noted = _apply(client, admin, slug)["run"]
    assert any(
        one.startswith("원천에 새 칸이 생겼습니다(대응에 없음): Phone")
        for one in noted["errors"]
    ), noted
    quiet = _apply(client, admin, slug)["run"]
    assert not any("새 칸" in one for one in quiet["errors"]), quiet
