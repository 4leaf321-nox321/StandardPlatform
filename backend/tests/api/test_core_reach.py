"""코어 창구에 닿기(ADR 0085 보강) — 카탈로그가 큰 타입을 다 훑지 않고, 주소를 잘못 적으면
까닭이 화면에 닿는다.

- 카탈로그의 「타입마다 최근 수정 시각」 은 타입 색인(`type_id, updated_at`)의 끝 한 줄을
  집는다 — `GROUP BY` 의 `max` 는 공개 타입의 행을 전부 읽어 2,000만 건에서 14.5초였다.
- 형제 코어에 연결하는 대기는 10초다(응답 읽기는 60초 그대로) — 응답하지 않는 주소에서
  앞단의 시간 제한(504)이 먼저 끝나 「주소가 틀렸다」 가 안 보이던 것.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.modules.coreapi import services as core_services
from app.modules.datasources import fetchers, odata, retry
from app.modules.ontology.models import ObjectType
from app.shared.errors import AppError, code
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_type


def _plan(db: Session, stmt: Any) -> str:
    compiled = stmt.compile(
        dialect=db.get_bind().dialect, compile_kwargs={"render_postcompile": True}
    )
    connection = db.connection()
    connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
    rows = connection.exec_driver_sql(f"EXPLAIN {compiled}", compiled.params)
    return "\n".join(str(one[0]) for one in rows)


def test_카탈로그의_최근_수정_시각은_타입_색인의_끝을_집는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="큰 기록")
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == kind))
    assert type_id is not None
    plan = _plan(db, core_services.latest_changes([type_id], true()))
    db.rollback()
    assert "ix_objects_type_updated" in plan, plan
    # 묶음마다 다 읽는 꼴(집계 위의 훑기)이 아니다.
    assert "GroupAggregate" not in plan and "HashAggregate" not in plan, plan


def test_코어_창구에_연결하는_대기는_짧고_까닭을_말한다() -> None:
    with fetchers._core_client(odata.Auth(), None) as client:
        assert client.timeout.connect == fetchers.CONNECT_SECONDS == 10.0
        assert client.timeout.read == odata.TIMEOUT_SECONDS

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(AppError) as caught:
        fetchers.core_catalog(
            base_url="http://wrong-host.local/api",
            auth=odata.Auth(kind="bearer", secret="x"),
            transport=httpx.MockTransport(refuse),
        )
    assert caught.value.code == code("DATASOURCES", 38)
    assert "10초 안에 연결하지 못했습니다" in caught.value.message
    assert "주소" in caught.value.message
    # 연결 실패로 센다 — 정기 동기화가 짧게 다시 넣는다(`retry.CONNECT`).
    assert retry.failure_kind(caught.value) == retry.CONNECT

    def silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AppError) as slow:
        fetchers.core_definitions(
            base_url="http://busy-hub.local/api",
            auth=odata.Auth(kind="bearer", secret="x"),
            transport=httpx.MockTransport(silent),
            groups=[],
            known="",
        )
    assert "60초 안에 답하지 않았습니다" in slow.value.message
