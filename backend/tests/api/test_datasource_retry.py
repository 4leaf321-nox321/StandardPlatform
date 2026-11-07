"""연결 오류는 짧게 다시 묻는다 — 하루 간격 소스가 몇 초의 장애로 하루를 잃지 않게.

- 쪽 하나가 잠깐 실패하면(연결 오류 · 503) 그 쪽만 다시 묻는다 — 실행은 성공한다.
- 다시 물어도 안 되면 실행 기록에 `error_kind=connect` 를 적고, 타이머가 정기 간격 대신
  15 → 30분 뒤 다시 넣는다. 첫 실패 알림은 「연결 오류」 라고 말한다.
- 인증 거절은 다시 묻지 않고(같은 답이다), 정기 간격대로 둔다 — 사람이 비밀을 고칠 일이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.datasources import services
from tests.api.conftest import Signed, notifications_of
from tests.api.test_datasources import FakeOData, _source, _sync, _vendor_type


class Flaky(FakeOData):
    """앞에서부터 `fails` 를 하나씩 꺼내 그 요청에 낸다(상태 코드 · 예외). 비면 정상.
    `skip_from` 이 있으면 그 `$skip` 부터만 실패한다(둘째 쪽부터)."""

    def __init__(self) -> None:
        super().__init__()
        self.fails: list[int | type[httpx.TransportError]] = []
        self.always: int | type[httpx.TransportError] | None = None
        self.skip_from: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("$skip", "0"))
        if self.skip_from is None or skip >= self.skip_from:
            failure = self.fails.pop(0) if self.fails else self.always
            if failure is not None:
                self.requests.append(request)
                if isinstance(failure, int):
                    return httpx.Response(failure, text="잠시 후 다시")
                raise failure("이어지지 않음", request=request)
        return super().__call__(request)


@pytest.fixture
def flaky() -> Iterator[Flaky]:
    fake = Flaky()
    services.transport = httpx.MockTransport(fake)
    try:
        yield fake
    finally:
        services.transport = None


def _runs(client: TestClient, admin: Signed, slug: str) -> list[dict[str, Any]]:
    got = client.get(f"/api/datasources/{slug}/runs", headers=admin.headers)
    assert got.status_code == 200, got.text
    return list(got.json())


def _due_after(slug: str, minutes: int) -> bool:
    """마지막 실행에서 `minutes` 분 뒤 — 타이머가 이 소스를 넣나."""
    from app.database import SessionLocal
    from app.modules.datasources.models import DataSource

    with SessionLocal() as db:
        source = db.scalars(select(DataSource).where(DataSource.slug == slug)).one()
        assert source.last_run_at is not None
        now = source.last_run_at + timedelta(minutes=minutes)
        return slug in {one.slug for one in services.due(db, now=now)}


def test_잠깐의_503_과_연결_오류는_그_쪽만_다시_물어_실행이_성공한다(
    client: TestClient, admin: Signed, flaky: Flaky, _retry_without_waiting: list[float]
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, interval_minutes=1440)
    flaky.skip_from = 2  # 둘째 쪽(쪽 크기 2)에서 한 번은 503, 한 번은 연결이 끊긴다
    flaky.fails = [503, httpx.ConnectError]
    done = _sync(client, admin, source["slug"], apply=True)
    assert done.status_code == 200, done.text
    run = done.json()["run"]
    assert run["status"] == "ok" and run["error_kind"] is None, run
    assert done.json()["counts"]["create"] == 3
    assert _retry_without_waiting == [2.0, 8.0]
    first_pages = [one for one in flaky.requests if "$skip" not in str(one.url)]
    assert len(first_pages) == 1  # 받은 쪽은 다시 묻지 않는다 — 실패한 쪽부터


def test_다시_물어도_안_되면_연결_실패로_적고_정기_간격보다_짧게_다시_넣는다(
    client: TestClient, admin: Signed, flaky: Flaky
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, interval_minutes=1440)
    slug = source["slug"]
    before = len(notifications_of(client, admin, "datasource.failed"))
    flaky.always = httpx.ConnectError
    failed = _sync(client, admin, slug, apply=True)
    run = failed.json()["run"]
    assert run["status"] == "failed" and run["error_kind"] == "connect", run
    assert any("약 15분 뒤 다시 시도합니다" in one for one in run["errors"]), run
    assert len(flaky.requests) == 3  # 한 쪽을 세 번
    note = notifications_of(client, admin, "datasource.failed")[0]
    assert len(notifications_of(client, admin, "datasource.failed")) == before + 1
    assert (note["body"] or "").startswith("연결 오류입니다")
    # 정기 간격은 하루지만 15분(여유 5분을 빼고)이면 다시 넣는다.
    assert not _due_after(slug, 9) and _due_after(slug, 11)

    flaky.always = 503  # 둘째도 실패 — 503 이 세 번이어도 연결 실패다
    again = _sync(client, admin, slug, apply=True).json()["run"]
    assert again["error_kind"] == "connect"
    assert any("약 30분 뒤" in one for one in again["errors"]), again
    assert not _due_after(slug, 24) and _due_after(slug, 26)
    assert [one["error_kind"] for one in _runs(client, admin, slug)][:2] == ["connect"] * 2

    flaky.always = None  # 돌아오면 정기 간격으로
    assert _sync(client, admin, slug, apply=True).json()["run"]["status"] == "ok"
    assert not _due_after(slug, 120)


def test_인증_거절은_다시_묻지_않고_정기_간격대로_둔다(
    client: TestClient, admin: Signed, flaky: Flaky, _retry_without_waiting: list[float]
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, interval_minutes=1440)
    slug = source["slug"]
    flaky.auth_required = "Bearer 없는것"
    run = _sync(client, admin, slug, apply=True).json()["run"]
    assert run["status"] == "failed" and run["error_kind"] == "auth", run
    assert any("다시 시도해도 같으므로" in one for one in run["errors"]), run
    assert len(flaky.requests) == 1 and _retry_without_waiting == []
    note = notifications_of(client, admin, "datasource.failed")[0]
    assert (note["body"] or "").startswith("인증이 거절됐습니다")
    assert not _due_after(slug, 60)


def test_정기_실행이_아닌_소스는_연결_실패에도_타이머가_돌리지_않는다(
    client: TestClient, admin: Signed, flaky: Flaky
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, interval_minutes=0)
    flaky.always = httpx.ReadTimeout
    run = _sync(client, admin, source["slug"], apply=True).json()["run"]
    assert run["error_kind"] == "connect"
    assert not any("분 뒤 다시 시도" in one for one in run["errors"]), run
    assert not _due_after(source["slug"], 20)
