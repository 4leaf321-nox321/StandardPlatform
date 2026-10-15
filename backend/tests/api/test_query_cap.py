"""읽기 질의의 시간 상한(ADR 0031) — 넘으면 504 「좁히거나 지표로」, 작업에는 안 걸린다.

상한은 화면 · AI 가 기다리는 읽기(목록 · 통계 · 추이 · 0건 진단)에만 건다. 기록 2,000만 건을
지나가는 네 걸음 통계는 3분이고, 그동안 DB 연결과 API 작업자가 묶인다. 내보내기는 작업이라
사람이 기다리지 않고, 큰 CSV 는 원래 몇 분이다 — 거기 걸면 내보내기가 안 된다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.objects import summary as summary_service
from app.shared import querycap
from tests.api.conftest import Signed, export_file
from tests.api.test_ontology import _make_object, _make_type


def test_상한은_그_트랜잭션에만_걸리고_계획기를_넓힌다(db: Session) -> None:
    before = {
        name: db.execute(text(f"SHOW {name}")).scalar()
        for name in ("statement_timeout", "join_collapse_limit", "work_mem")
    }
    db.rollback()
    querycap.cap(db)
    assert db.execute(text("SHOW statement_timeout")).scalar() == "1min"
    assert db.execute(text("SHOW join_collapse_limit")).scalar() == "20"
    assert db.execute(text("SHOW geqo_threshold")).scalar() == "20"
    assert db.execute(text("SHOW work_mem")).scalar() == "64MB"
    assert db.execute(text("SHOW max_parallel_workers_per_gather")).scalar() == "4"
    db.rollback()
    # `SET LOCAL` — 트랜잭션이 끝나면 풀린다. 같은 연결을 받는 다음 요청에 남지 않는다.
    after = {name: db.execute(text(f"SHOW {name}")).scalar() for name in before}
    assert after == before


def test_메모리_병렬은_env_로_정하고_틀린_값은_서버_설정을_따른다(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "read_work_mem", "256MB")
    monkeypatch.setattr(settings, "read_parallel_workers", 8)
    assert querycap.tuning()["work_mem"] == "256MB"
    assert querycap.tuning()["max_parallel_workers_per_gather"] == "8"
    monkeypatch.setattr(settings, "read_work_mem", "64MB; DROP TABLE objects")
    monkeypatch.setattr(settings, "read_parallel_workers", -1)
    monkeypatch.setattr(settings, "read_query_seconds", 0)
    assert set(querycap.tuning()) == {
        "join_collapse_limit",
        "from_collapse_limit",
        "geqo_threshold",
    }
    querycap.cap(db)  # 거는 것이 계획기뿐이어도 한 번에 간다
    assert db.execute(text("SHOW join_collapse_limit")).scalar() == "20"
    db.rollback()


def test_상한을_넘은_통계는_끊고_할_일을_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="부품")
    _make_object(client, admin, kind, label="볼트")

    def slow(db: Session, *args: Any, **kwargs: Any) -> Any:
        db.execute(text("SELECT pg_sleep(3)"))
        raise AssertionError("상한이 이 질의를 끊었어야 한다")

    monkeypatch.setattr(summary_service, "summarize", slow)
    monkeypatch.setattr(get_settings(), "read_query_seconds", 0.2)
    got = client.get(f"/api/objects/{kind}/summary", headers=admin.headers)
    # 503 이 아니다 — 이중화의 nginx 는 503 이면 같은 요청을 다른 서버에 다시 보낸다.
    assert got.status_code == 504, got.text
    error = got.json()["error"]
    assert error["code"].endswith("COMMON-0504")
    assert "0.2초" in error["message"] and "지표" in error["message"]
    assert error["details"] == {"seconds": 0.2}
    # 끊긴 뒤에도 서버는 멀쩡하다 — 같은 연결로 다음 요청이 돈다.
    monkeypatch.undo()
    assert client.get(f"/api/objects/{kind}/summary", headers=admin.headers).status_code == 200


def test_작업으로_도는_내보내기에는_상한을_안_건다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="부품")
    _make_object(client, admin, kind, label="볼트")
    calls: list[str] = []
    original = querycap.cap

    def counted(db: Session) -> None:
        calls.append("cap")
        original(db)

    monkeypatch.setattr(querycap, "cap", counted)
    got = export_file(client, admin, f"{kind}/export", {"format": "json"})
    assert [row["label"] for row in got.json()["rows"]] == ["볼트"]
    assert calls == [], "내보내기 작업의 거르기가 읽기 상한을 걸었다"
    client.get(f"/api/objects/{kind}", headers=admin.headers)
    assert calls == ["cap"]
