"""준비됨(`/api/health/ready`, ADR 0077) — 살아 있음(`/api/health`)과 달리 DB ·
마이그레이션까지 본다.

살아 있음은 DB 가 끊겨도 200 이라 배포 스크립트가 「응답함」 이라 말했다. 여기서 보는 것: DB 에
닿고 판이 맞으면 200 · 빈 DB · 덜 올라간 판은 503 과 그 까닭 · 코드보다 새 판은 준비됨 · DB
오류의 원문(호스트 · 계정)은 안 나감 · DB 가 답하지 않으면 시간 안에 끊고, 앞 확인이 매달려
있으면 새로 묻지 않음 · 요청 시간 기록에 안 남음.

시험 DB 는 모델로 표를 만들어 마이그레이션 기록(`alembic_version`)이 없다 — 판을 보는 시험은 그
표를 직접 세우고 끝나면 지운다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import readiness, schema_version
from app.modules.audit.models import RequestTiming
from app.shared.request_timing import writer


@pytest.fixture
def stamped(db: Session) -> Iterator[Any]:
    """마이그레이션 기록 표 — `stamp(판)` 으로 그 판을 적는다. 끝나면 표를 지운다."""
    assert db.scalar(text("SELECT to_regclass('alembic_version')")) is None, (
        "시험 DB 에 마이그레이션 기록이 이미 있다 — 다른 시험이 남겼다"
    )
    db.execute(text("CREATE TABLE alembic_version (version_num varchar(64) NOT NULL)"))
    db.commit()

    def stamp(revision: str) -> None:
        db.execute(text("DELETE FROM alembic_version"))
        db.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": revision})
        db.commit()

    yield stamp
    db.rollback()
    db.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.commit()


def _ready(client: TestClient) -> tuple[int, dict[str, Any]]:
    got = client.get("/api/health/ready")
    return got.status_code, dict(got.json())


def _check(body: dict[str, Any], name: str) -> dict[str, Any]:
    (found,) = [one for one in body["checks"] if one["name"] == name]
    return dict(found)


def test_빈_DB_면_준비_안_됨이고_DB_는_닿는다(client: TestClient, db: Session) -> None:
    assert db.scalar(text("SELECT to_regclass('alembic_version')")) is None
    status, body = _ready(client)
    assert status == 503 and body["status"] == "not_ready"
    assert body["version"]
    assert _check(body, "db")["ok"] is True
    assert _check(body, "db")["detail"].startswith("응답 ")
    schema = _check(body, "schema")
    assert schema["ok"] is False and "빈 DB" in schema["detail"]
    # 살아 있음은 그대로 200 이다 — 둘은 다른 물음이다.
    assert client.get("/api/health").status_code == 200


def test_판이_맞으면_준비됨_덜_올라가면_503_새_판이면_준비됨(
    client: TestClient, stamped: Any
) -> None:
    head = schema_version.code_head()
    assert head is not None
    older = sorted(schema_version.known_revisions() - {head})[-1]

    stamped(head)
    status, body = _ready(client)
    assert (status, body["status"]) == (200, "ready")
    assert _check(body, "schema") == {
        "name": "schema",
        "ok": True,
        "detail": f"마이그레이션 {head}",
    }

    stamped(older)
    status, body = _ready(client)
    assert status == 503
    detail = _check(body, "schema")["detail"]
    assert "덜 올라갔습니다" in detail and older in detail and head in detail
    assert "deploy.sh update" in detail

    # 더 새 판을 깔았다가 되돌린 서버 — 대개 그대로 돈다(칸을 더하는 쪽이다).
    stamped("9999_from_the_future")
    status, body = _ready(client)
    assert status == 200
    assert "새것입니다" in _check(body, "schema")["detail"]


def test_DB_오류의_원문은_안_나간다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """로그인 없이 묻는 자리다 — 접속 오류의 원문에는 DB 호스트 · 계정이 든다."""

    def refuse(_engine: Any) -> tuple[float, str | None]:
        raise OperationalError(
            "connect",
            {},
            Exception('connection to server at "10.9.8.7", user "sp_owner" failed'),
        )

    monkeypatch.setattr(readiness, "_probe", refuse)
    got = client.get("/api/health/ready")
    assert got.status_code == 503
    assert "10.9.8.7" not in got.text and "sp_owner" not in got.text
    body = got.json()
    assert _check(body, "db")["ok"] is False and "app.log" in _check(body, "db")["detail"]
    assert _check(body, "schema")["ok"] is False


def test_DB_가_답하지_않으면_시간_안에_끊고_매달린_확인은_다시_묻지_않는다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """패킷을 버리는 DB 서버 — 접속이 몇 분을 기다린다. 감시가 몇 초마다 물어도 매달린 접속을
    쌓지 않는다."""
    release = threading.Event()
    asked: list[int] = []

    def hang(_engine: Any) -> tuple[float, str | None]:
        asked.append(1)
        release.wait(10)
        return 1.0, None

    monkeypatch.setattr(readiness, "_probe", hang)
    monkeypatch.setattr(readiness, "DB_SECONDS", 0.2)
    try:
        status, body = _ready(client)
        assert status == 503
        assert _check(body, "db") == {
            "name": "db",
            "ok": False,
            "detail": "DB 가 0.2초 안에 답하지 않습니다",
        }
        again, _ = _ready(client)
        assert again == 503
        assert len(asked) == 1  # 앞 확인이 아직 매달려 있다 — 새로 묻지 않았다
    finally:
        release.set()
        pending = readiness._pending
        if pending is not None:
            pending.result(timeout=5)


def test_요청_시간_기록에_남지_않는다(client: TestClient, db: Session) -> None:
    """감시가 몇 초마다 부르는 자리 — 살아 있음처럼 기록에서 뺀다."""
    got = client.get("/api/health/ready")
    writer.flush()
    found = db.query(RequestTiming).filter(
        RequestTiming.request_id == got.headers["x-request-id"]
    )
    assert found.count() == 0
