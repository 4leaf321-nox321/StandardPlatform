"""POST 지만 읽기인 자리 — **읽기 토큰으로 된다**(`main.py` 의 `register_read_only_post*`).

등록하지 않은 POST 는 토큰으로는 막힌다(모르는 것은 막는다). 그 기본값이 맞지만, 하는 일이
「묻기」 뿐인 자리를 빠뜨리면 읽기 토큰으로 들어온 AI 가 그 물음을 영영 못 한다 — Cypher 는
**어떤 범위로도**(AUTH-105), 태그로 비슷한 기록 찾기는 `objects:write` 없이는(AUTH-106) 막혔다
(2026-11-28).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.modules.graph import cypher
from app.shared import scopes
from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_type


def _reader(client: TestClient, who: Signed) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"reader-{uuid.uuid4().hex[:8]}", "scopes": ["read"]},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _code(response: object) -> str:
    try:
        return str(response.json()["error"]["code"])  # type: ignore[attr-defined]
    except (ValueError, KeyError, TypeError):
        return ""


def test_읽기_토큰으로_Cypher_와_태그로_비슷한_기록을_묻는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = _reader(client, admin)
    # 사본이 없는 설치 — 그래도 범위에서 막히지 않고 사본의 말(503)까지 간다.
    monkeypatch.setattr(get_settings(), "graphdb_url", "")
    cypher.close()
    asked = client.post(
        "/api/graph/cypher", json={"query": "MATCH (n) RETURN n"}, headers=reader
    )
    assert _code(asked) not in (code("AUTH", 105), code("AUTH", 106)), asked.text
    assert asked.status_code == 503, asked.text
    assert "그래프 사본" in asked.json()["error"]["message"]

    part = _make_type(client, admin, label="부품")
    similar = client.post(f"/api/objects/{part}/similar", json={"tags": {}}, headers=reader)
    assert similar.status_code == 200, similar.text

    # 읽기로 연 것은 **그 주소 · 그 모양의 POST 만** — 옆의 쓰기는 그대로 쓰기 범위다.
    made = client.post(f"/api/objects/{part}", json={"label": "볼트"}, headers=reader)
    assert made.status_code == 403 and _code(made) == code("AUTH", 106), made.text
    assert scopes.is_reading("POST", "/api/graph/cypher") is True
    assert scopes.is_reading("POST", "/api/graph/cypher/schema") is False
    assert scopes.is_reading("POST", f"/api/objects/{part}/similar") is True
    assert scopes.is_reading("POST", f"/api/objects/{part}/x/similar") is False
    assert scopes.is_reading("PATCH", f"/api/objects/{part}/similar") is False
