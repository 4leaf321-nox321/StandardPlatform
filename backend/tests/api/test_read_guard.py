"""오래 도는 읽기의 안전장치(ADR 0048) — 떠나면 멈추고, 요청 하나의 시간 · 동시 개수 · 임시
파일에 상한을 둔다.

운영에서 정적 그리기가 오래 걸려 화면을 닫았는데 서버는 끝까지 돌았고, 다시 열 때마다 하나씩
더 쌓여 서버가 멈췄다(2026-11-02). 「끊겼다」 를 시험 클라이언트로는 만들 수 없어(응답이 끝날
때까지 연결이 산다) 안전장치를 직접 부르고, 라우트에는 시간 · 동시 개수 · 오류 모양을 본다.
진짜 서버(uvicorn)에서 요청을 끊어 DB 질의가 멈추는 것은 ADR 0048 「결과」 에 쟀다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import engine
from app.modules.graph import routes as graph_routes
from app.modules.objects import graphwhole
from app.modules.objects import summary as summary_service
from app.shared import querycap, readguard
from app.shared.errors import AppError
from tests.api.conftest import Signed
from tests.api.test_graph import _hidden_workspace
from tests.api.test_ontology import _make_object, _make_property, _make_type


class _Leaving:
    """`seconds` 뒤에 떠나는 요청한 쪽(None 이면 안 떠난다)."""

    def __init__(self, seconds: float | None) -> None:
        self.at = None if seconds is None else time.monotonic() + seconds

    async def is_disconnected(self) -> bool:
        return self.at is not None and time.monotonic() >= self.at


def _whole(client: TestClient, who: Signed, types: str) -> Any:
    return client.get("/api/graph/whole", params={"types": types}, headers=who.headers)


def _guarded(
    db: Session, work: Callable[[], Any], *, leaves: float | None = None, seconds: float = 0
) -> tuple[Any, float]:
    """(결과 또는 난 오류, 걸린 초)."""
    started = time.monotonic()
    try:
        out: Any = asyncio.run(
            readguard.run(
                _Leaving(leaves),  # type: ignore[arg-type]
                db,
                work,
                seconds=seconds,
                what="시험",
                hint="좁히세요.",
            )
        )
    except AppError as caught:
        out = caught
    return out, time.monotonic() - started


def test_떠나면_도는_질의를_취소한다(db: Session) -> None:
    out, took = _guarded(db, lambda: db.execute(text("SELECT pg_sleep(20)")), leaves=0.3)
    assert isinstance(out, AppError) and out.status == 499
    assert out.code.endswith("COMMON-0499")
    assert took < 5, f"20초 질의를 끊지 못했다({took:.1f}초)"
    # 같은 세션은 되돌리면 다시 쓴다 — 취소는 그 질의만이다.
    db.rollback()
    assert db.scalar(text("SELECT 1")) == 1


def test_멈춘_뒤의_질의는_보내지_않는다(db: Session) -> None:
    """취소는 「지금 도는 질의」 만 끊는다 — 질의 사이(파이썬이 일하는 동안)에 떠나면 다음
    질의를 막아야 멈춘다."""
    sent: list[int] = []

    def work() -> None:
        for step in range(100):
            db.execute(text("SELECT 1"))
            sent.append(step)
            time.sleep(0.1)

    out, took = _guarded(db, work, leaves=0.35)
    assert isinstance(out, AppError) and out.status == 499
    assert isinstance(out.__cause__, readguard.Stopped)
    assert len(sent) < 20 and took < 5


def test_요청_하나의_시간_상한을_넘으면_504(db: Session) -> None:
    out, took = _guarded(db, lambda: db.execute(text("SELECT pg_sleep(20)")), seconds=0.4)
    assert isinstance(out, AppError) and out.status == 504
    assert "0.4초" in out.message and "좁히세요." in out.message
    assert out.details == {"seconds": 0.4}
    assert took < 5


def test_지켜보지_않는_질의는_막지_않는다(db: Session) -> None:
    out, _took = _guarded(db, lambda: db.scalar(text("SELECT 7")))
    assert out == 7
    # 지켜본 요청이 끝난 뒤 같은 스레드 · 세션의 질의는 그대로 간다.
    assert db.scalar(text("SELECT 8")) == 8


def test_정적_그리기도_시간_상한에서_멈춘다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")

    def slow(db: Session, **_kwargs: Any) -> Any:
        db.execute(text("SELECT pg_sleep(20)"))
        raise AssertionError("시간 상한이 이 질의를 끊었어야 한다")

    monkeypatch.setattr(graphwhole, "gather", slow)
    monkeypatch.setattr(get_settings(), "long_read_seconds", 0.5)
    started = time.monotonic()
    got = _whole(client, admin, part)
    assert got.status_code == 504, got.text
    error = got.json()["error"]
    assert error["code"].endswith("COMMON-0504")
    assert "정적 그리기" in error["message"] and "전체 지도" in error["message"]
    assert time.monotonic() - started < 10
    # 끊긴 뒤에도 서버는 멀쩡하다.
    monkeypatch.undo()
    assert _whole(client, admin, part).status_code == 200


def test_정적_그리기는_설치_전체에서_자리만큼만_돈다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자리는 DB 의 자문 잠금이라 다른 서버(여기서는 다른 연결)가 쥔 것도 센다."""
    part = _make_type(client, admin, label="부품")
    monkeypatch.setattr(readguard, "SLOT_WAIT", 0.3)
    monkeypatch.setattr(get_settings(), "whole_slots", 2)
    with engine.connect() as other:
        for at in range(2):
            assert other.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": f"graph_whole:{at}"}
            )
        try:
            busy = _whole(client, admin, part)
            assert busy.status_code == 429, busy.text
            assert busy.json()["error"]["details"] == {"slots": 2}
            assert "2개가 돌고" in busy.json()["error"]["message"]
            # 하나가 비면 바로 돈다.
            other.execute(text("SELECT pg_advisory_unlock(hashtext('graph_whole:1'))"))
            free = _whole(client, admin, part)
            assert free.status_code == 200, free.text
        finally:
            other.execute(text("SELECT pg_advisory_unlock_all()"))
    # 요청이 끝나면 쥔 자리는 풀린다(트랜잭션 잠금) — 연달아 불러도 막히지 않는다.
    for _ in range(3):
        again = _whole(client, admin, part)
        assert again.status_code == 200


def test_임시_파일_상한은_걸_수_있을_때만_건다(monkeypatch: pytest.MonkeyPatch) -> None:
    """superuser 만 바꾸는 설정 — 권한 없이 걸면 그 요청이 통째로 실패한다."""
    settings = get_settings()
    monkeypatch.setattr(querycap, "temp_limit_allowed", lambda _value: True)
    assert querycap.tuning()["temp_file_limit"] == "5GB"
    monkeypatch.setattr(settings, "read_temp_limit", "")
    assert "temp_file_limit" not in querycap.tuning()
    monkeypatch.setattr(settings, "read_temp_limit", "5GB; DROP TABLE objects")
    assert "temp_file_limit" not in querycap.tuning()
    monkeypatch.setattr(settings, "read_temp_limit", "5GB")
    monkeypatch.setattr(querycap, "temp_limit_allowed", lambda _value: False)
    assert "temp_file_limit" not in querycap.tuning()


def test_임시_파일_상한을_걸어_보고_정한다(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이 DB 계정이 걸 수 있는지는 설치마다 다르다 — 어느 쪽이든 읽기는 돈다."""
    monkeypatch.setattr(querycap, "_TEMP_ALLOWED", {})
    allowed = querycap.temp_limit_allowed("5GB")
    querycap.cap(db)
    shown = db.scalar(text("SHOW temp_file_limit"))
    assert (shown == "5GB") if allowed else (shown != "5GB")
    db.rollback()
    assert querycap.temp_limit_allowed("not-a-size") is False
    # 확실한 「못 건다」(권한 없음 · 틀린 값)만 기억한다.
    assert querycap._TEMP_ALLOWED["not-a-size"] is False


def test_DB_가_잠깐_안_닿은_것은_기억하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """기동 직후 한 번 실패한 것을 기억하면 그 프로세스는 재시작까지 상한 없이 돈다."""
    monkeypatch.setattr(querycap, "_TEMP_ALLOWED", {})

    class _Down(Exception):
        sqlstate = "08006"

    class _Engine:
        def connect(self) -> Any:
            raise OperationalError("connect", {}, _Down())

    monkeypatch.setattr("app.database.engine", _Engine())
    assert querycap.temp_limit_allowed("5GB") is False
    assert "5GB" not in querycap._TEMP_ALLOWED


class _TempLimit(Exception):
    sqlstate = querycap.TEMP_LIMIT_EXCEEDED


def test_임시_파일_상한을_넘으면_507_로_좁히라고_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="부품")

    def spilled(db: Session, *args: Any, **kwargs: Any) -> Any:
        raise OperationalError("SELECT …", {}, _TempLimit())

    monkeypatch.setattr(summary_service, "summarize", spilled)
    got = client.get(f"/api/objects/{kind}/summary", headers=admin.headers)
    # 503 이 아니다 — 이중화의 nginx 가 다른 서버에 다시 보내 같은 질의가 또 돈다.
    assert got.status_code == 507, got.text
    error = got.json()["error"]
    assert error["code"].endswith("COMMON-0507")
    assert "5GB" in error["message"] and "좁혀" in error["message"]
    assert error["details"] == {"temp_limit": "5GB"}


# --- 탐색 그림(정적 그리기를 끈 「타입 전체 표시」 · 이웃) ---------------------------------
#
# 2,000만 건 DB 에서 임시 파일을 쓴 것은 이쪽이었다 — 「+N」 의 근거인 「나를 가리키는 참조
# 수」 를 세려고 참조 표와 객체 표(2,000만 줄)를 해시로 이어 디스크에 쏟았다(모델 6천 개에
# 20초 · 임시 파일 1.7GB). 색인에는 살아 있는 출발점만 있으니 모두를 보는 사람은 객체 표를
# 안 거친다.


def _pointed(client: TestClient, admin: Signed, *, workspace: str | None = None) -> Any:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    _make_property(
        client,
        admin,
        part,
        key="maker",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    acme = _make_object(client, admin, vendor, label="ACME")
    extra = {"workspace_slug": workspace} if workspace else {}
    made = [
        _make_object(
            client, admin, part, label=name, properties={"maker": acme["id"]}, **extra
        )
        for name in ("볼트", "너트", "와셔")
    ]
    return part, vendor, acme, made


def test_참조_수는_지운_것을_빼고_센다(client: TestClient, admin: Signed) -> None:
    part, vendor, _acme, made = _pointed(client, admin)
    gone = client.delete(f"/api/objects/{part}/{made[0]['id']}", headers=admin.headers)
    assert gone.status_code in (200, 204), gone.text
    listed = client.get(
        "/api/graph/subgraph", params={"types": vendor}, headers=admin.headers
    ).json()
    (node,) = listed["nodes"]
    assert node["label"] == "ACME" and node["degree"] == 2


@pytest.mark.usefixtures("department_scope")
def test_부서로_좁힌_설치는_보이는_참조만_센다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    other = _hidden_workspace(client, admin)
    part, vendor, acme, _made = _pointed(client, admin, workspace=other)
    mine = _make_object(
        client,
        admin,
        part,
        label="내 볼트",
        properties={"maker": acme["id"]},
        workspace_slug=member.workspace,
    )
    assert mine["id"]

    def degree(who: Signed) -> int:
        got = client.get("/api/graph/subgraph", params={"types": vendor}, headers=who.headers)
        assert got.status_code == 200, got.text
        return int(got.json()["nodes"][0]["degree"])

    assert degree(admin) == 4
    assert degree(member) == 1


def test_타입_전체_표시의_총수는_계수표와_같다(client: TestClient, admin: Signed) -> None:
    part, _vendor, _acme, made = _pointed(client, admin)
    client.delete(f"/api/objects/{part}/{made[1]['id']}", headers=admin.headers)
    got = client.get(
        "/api/graph/subgraph", params={"types": part, "limit": 1}, headers=admin.headers
    ).json()
    assert got["total"] == 2 and got["truncated"] is True
    # 이름으로 좁히면 센다 — 계수표에는 이름이 없다.
    named = client.get(
        "/api/graph/subgraph", params={"types": part, "q": "와셔"}, headers=admin.headers
    ).json()
    assert named["total"] == 1


@pytest.mark.parametrize("route", ["subgraph", "neighborhood"])
def test_탐색_그림도_시간_상한에서_멈춘다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    _part, vendor, acme, _made = _pointed(client, admin)

    def slow(db: Session, *args: Any, **kwargs: Any) -> Any:
        db.execute(text("SELECT pg_sleep(20)"))
        raise AssertionError("시간 상한이 이 질의를 끊었어야 한다")

    monkeypatch.setattr(graph_routes, "_degrees", slow)
    monkeypatch.setattr(get_settings(), "long_read_seconds", 0.5)
    params = {"types": vendor} if route == "subgraph" else {"focus": acme["id"]}
    started = time.monotonic()
    got = client.get(f"/api/graph/{route}", params=params, headers=admin.headers)
    assert got.status_code == 504, got.text
    assert "0.5초" in got.json()["error"]["message"]
    assert time.monotonic() - started < 10
