"""뜻으로 찾기의 바탕이 준비됐는지(ADR 0050) — 서버 화면이 말한다.

배포는 Ollama · 모델을 깔다 실패해도 멈추지 않는다(앱은 모델 없이도 돈다) — 그 사실이 드러날
자리가 서버 화면이다. 여기서는 가짜 Ollama 로 그 판단을 본다. 확장(pgvector)은 시험 DB 에
진짜로 있다(개발 PC 는 `scripts/dev_pgvector.sh`, CI 는 pgvector 이미지).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.shared import embedding, vectors
from tests.api.conftest import Signed


def _ollama(answer: Callable[[httpx.Request], httpx.Response]) -> list[dict[str, Any]]:
    """가짜 Ollama 를 끼우고, 받은 물음을 모은다."""
    asked: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append({"path": request.url.path, **json.loads(request.content or b"{}")})
        return answer(request)

    embedding.transport = httpx.MockTransport(handle)
    return asked


def _status(client: TestClient, admin: Signed) -> dict[str, Any]:
    got = client.get("/api/server/status", headers=admin.headers)
    assert got.status_code == 200, got.text
    shown: dict[str, Any] = got.json()["embedding"]
    return shown


def test_확장은_extensions_스키마에_있고_스키마를_붙여_쓴다(db: Session) -> None:
    found = vectors.installed(db)
    assert found is not None and found[1] == vectors.SCHEMA
    assert vectors.problem(found) is None
    near = db.execute(
        text(
            "SELECT '[1,0]'::extensions.vector(2) OPERATOR(extensions.<=>) "
            "'[0,1]'::extensions.vector(2)"
        )
    ).scalar()
    assert near == pytest.approx(1.0)
    # HNSW(가까운 것을 빨리 찾는 색인)가 선다 — 0.5 아래에는 없다.
    db.execute(text(f"CREATE TEMP TABLE probe (at extensions.vector({vectors.DIM}))"))
    db.execute(text("CREATE INDEX ON probe USING hnsw (at extensions.vector_cosine_ops)"))
    db.rollback()


def test_모델이_답하고_차원이_맞으면_준비됨(client: TestClient, admin: Signed) -> None:
    asked = _ollama(
        lambda _: httpx.Response(200, json={"model_info": {"bert.embedding_length": 1024}})
    )
    shown = _status(client, admin)
    assert shown["state"] == "ready" and shown["problem"] is None
    assert shown["dim"] == 1024 and shown["model"] == "bge-m3"
    assert shown["vector_version"]
    # 모델을 메모리에 올리지 않는 물음 — 화면이 기다리지 않는다.
    assert asked == [{"path": "/api/show", "model": "bge-m3"}]


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (
            lambda _: httpx.Response(404, json={"error": "model not found"}),
            "모델 bge-m3 이 없습니다",
        ),
        (
            lambda _: httpx.Response(200, json={"model_info": {"bert.embedding_length": 768}}),
            "768",
        ),
        (lambda _: httpx.Response(500, text="boom"), "500"),
    ],
)
def test_모델이_없거나_차원이_다르면_무엇이_빠졌는지_말한다(
    client: TestClient,
    admin: Signed,
    answer: Callable[[httpx.Request], httpx.Response],
    said: str,
) -> None:
    _ollama(answer)
    shown = _status(client, admin)
    assert shown["state"] == "problem"
    assert said in shown["problem"]


def test_Ollama_에_닿지_않으면_깔_명령을_말한다(client: TestClient, admin: Signed) -> None:
    """시험의 기본 — 진짜 Ollama 에 닿지 않는다(`_no_real_ollama`)."""
    shown = _status(client, admin)
    assert shown["state"] == "problem"
    assert (
        "닿지 않습니다" in shown["problem"] and "sudo ./deploy.sh ollama" in shown["problem"]
    )


def test_끈_설치는_묻지_않고_끔이라_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _ollama(lambda _: httpx.Response(200, json={}))
    monkeypatch.setattr(get_settings(), "embedding_backend", "off")
    shown = _status(client, admin)
    assert shown["state"] == "off" and shown["problem"] is None
    assert asked == []


@pytest.mark.parametrize(
    ("found", "said"),
    [
        (None, "sudo ./scripts/dev_pgvector.sh"),
        (("0.6.0", "public"), "public 스키마"),
        (("0.4.4", "extensions"), "0.5 이상"),
    ],
)
def test_확장이_없거나_자리_판이_다르면_말한다(
    found: tuple[str, str] | None, said: str
) -> None:
    why = vectors.problem(found)
    assert why and said in why
