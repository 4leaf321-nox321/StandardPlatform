"""원천 잠금과 원천 기록(ADR 0087).

- 정의 가져오기도 `source` 를 받는다 — 세운 타입이 그 원천의 것으로 잠긴다(묶음과 같다).
  토큰(기계)이 `source` 없이 새 타입을 세우면 계획이 「잠금이 없다」 고 말한다(화면의
  사람에게는 말하지 않는다).
- 묶음의 `origin`(원천 파이프라인의 실행)이 묶음 판에 남고, 그 판이 넣은 객체의 이력이 그 판 ·
  원천 · 이 객체의 출처를 원천에서 보는 주소(`trace` 의 `{type}` · `{key}`)를 보인다. 주소는
  `http(s)` 만.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.modules.ontology.models import ObjectType
from tests.api.conftest import Signed, bundle_import


def _tag() -> str:
    return uuid.uuid4().hex[:6]


def _token(client: TestClient, admin: Signed, scopes: list[str]) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"relay-{_tag()}", "scopes": scopes},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _schema(slug: str) -> dict[str, Any]:
    return {"types": [{"slug": slug, "label": "원천 부품", "key_policy": "required"}]}


def test_정의_가져오기의_source_는_잠그고_토큰이_빠뜨리면_말한다(
    client: TestClient, admin: Signed
) -> None:
    locked = f"src{_tag()}"
    got = client.post(
        "/api/ontology/import",
        params={"dry_run": "false", "source": "mes"},
        json=_schema(locked),
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    with SessionLocal() as db:
        row = db.scalar(select(ObjectType).where(ObjectType.slug == locked))
        assert row is not None and row.managed_by == "mes"
    # 잠긴 타입은 화면 · MCP 의 정의 수정이 막힌다.
    refused = client.patch(
        f"/api/ontology/types/{locked}", json={"label": "바꿈"}, headers=admin.headers
    )
    assert refused.status_code == 409

    machine = _token(client, admin, ["read", "ontology:write"])
    loose = f"src{_tag()}"
    plan = client.post(
        "/api/ontology/import",
        params={"dry_run": "true"},
        json=_schema(loose),
        headers=machine,
    )
    assert plan.status_code == 200, plan.text
    assert any("source 없이" in one and loose in one for one in plan.json()["warnings"])
    # 사람이 화면에서 제 정의를 만들 때는 말하지 않는다 — 그 경고는 곧 안 읽힌다.
    human = client.post(
        "/api/ontology/import",
        params={"dry_run": "true"},
        json=_schema(loose),
        headers=admin.headers,
    )
    assert not any("source 없이" in one for one in human.json()["warnings"])
    # source 를 달면 말하지 않는다.
    named = client.post(
        "/api/ontology/import",
        params={"dry_run": "true", "source": "mes"},
        json=_schema(loose),
        headers=machine,
    )
    assert not any("source 없이" in one for one in named.json()["warnings"])
    # 글자 규칙은 묶음의 source 와 같다.
    bad = client.post(
        "/api/ontology/import",
        params={"dry_run": "true", "source": "MES 시스템"},
        json=_schema(loose),
        headers=machine,
    )
    assert bad.status_code == 422


def test_묶음의_origin_은_판과_객체_이력에_남고_원천의_추적으로_잇는다(
    client: TestClient, admin: Signed
) -> None:
    slug = f"org{_tag()}"
    origin = {
        "system": "datarelay",
        "pipeline": "mes-parts",
        "run": "20261118T153000123456Z",
        "url": "https://relay.example/relay/pipelines/mes-parts",
        "trace": "https://relay.example/relay/pipelines/mes-parts?trace_type={type}&trace_key={key}",
    }
    got = bundle_import(
        client,
        admin,
        {
            "ontology": _schema(slug),
            "objects": [{"type_slug": slug, "rows": [{"key": "P/1 A", "label": "부품 1"}]}],
            "source": "mes",
            "origin": origin,
            "apply": True,
        },
    )
    assert got["applied"] is True, got
    runs = client.get("/api/bundles/runs", headers=admin.headers).json()
    mine = next(one for one in runs if one["id"] == got["run_id"])
    assert mine["origin"] == origin and mine["source"] == "mes"

    listed = client.get(f"/api/objects/{slug}", headers=admin.headers).json()
    object_id = listed["items"][0]["id"]
    history = client.get(f"/api/objects/{slug}/{object_id}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    made = next(one for one in history.json() if one["action"] == "object.create")
    run = made["run"]
    assert run["id"] == got["run_id"] and run["source"] == "mes"
    assert run["origin"]["run"] == origin["run"]
    # 이 객체 하나의 출처 — 틀의 {type} · {key} 를 이 객체로(식별자는 주소에 맞게 바꿔).
    assert run["trace_url"] == (
        "https://relay.example/relay/pipelines/mes-parts"
        f"?trace_type={slug}&trace_key=P%2F1%20A"
    )
    # 판 번호 표식은 사람이 읽을 칸으로 서지 않는다.
    assert "_run" not in made["changes"]


def test_원천의_주소는_http_만_받는다(client: TestClient, admin: Signed) -> None:
    slug = f"org{_tag()}"
    started = client.post(
        "/api/bundles/import",
        json={
            "ontology": _schema(slug),
            "origin": {"system": "x", "url": "javascript:alert(1)"},
        },
        headers=admin.headers,
    )
    assert started.status_code == 422, started.text


def test_origin_없는_묶음의_이력은_판만_말한다(client: TestClient, admin: Signed) -> None:
    slug = f"org{_tag()}"
    got = bundle_import(
        client,
        admin,
        {
            "ontology": _schema(slug),
            "objects": [{"type_slug": slug, "rows": [{"key": "A", "label": "부품 A"}]}],
            "apply": True,
        },
    )
    listed = client.get(f"/api/objects/{slug}", headers=admin.headers).json()
    history = client.get(
        f"/api/objects/{slug}/{listed['items'][0]['id']}/history", headers=admin.headers
    ).json()
    run = next(one for one in history if one["action"] == "object.create")["run"]
    assert run["id"] == got["run_id"]
    assert run["origin"] == {} and run["trace_url"] is None
