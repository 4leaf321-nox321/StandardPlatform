"""플랫폼의 자기소개 — **같은 도구를 가진 플랫폼 여럿 가운데 어디에 물을지** 고르는 단서.

같은 틀로 띄운 플랫폼 여럿이 한 에이전트에 도구로 붙으면 도구 이름 · 설명이 전부 같다. MCP
서버가 이 소개를 읽어 안내문 첫머리에 싣는데, 안내문은 토큰을 싣기 전에 서야 하므로 **로그인
없이** 읽혀야 한다. 고치는 것은 시스템 관리자뿐이고, 토큰이면 `ontology:write` 가 든다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.audit.models import AuditEntry
from app.modules.server.models import PlatformProfile
from tests.api.conftest import Signed

PATH = "/api/server/profile"


@pytest.fixture
def fresh(db: Session) -> Iterator[None]:
    """한 행짜리 표다 — 시험 사이에 남기지 않는다."""
    db.query(PlatformProfile).delete()
    db.commit()
    yield
    db.query(PlatformProfile).delete()
    db.commit()


def _token(client: TestClient, admin: Signed, scopes: list[str]) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"t-{'-'.join(scopes)}", "scopes": scopes},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def test_로그인_없이_읽히고_아직_안_적었으면_빈_소개다(
    client: TestClient, fresh: None
) -> None:
    got = client.get(PATH)
    assert got.status_code == 200, got.text
    body = got.json()
    settings = get_settings()
    assert body["slug"] == settings.app_slug and body["name"] == settings.app_name
    assert body["summary"] == "" and body["notes"] == "" and body["updated_at"] is None


def test_시스템_관리자만_고치고_감사에_전후가_남는다(
    client: TestClient, admin: Signed, member: Signed, db: Session, fresh: None
) -> None:
    payload = {
        "summary": "CAE 그룹의 보고서 · 해석 기록 · 개발모델",
        "notes": "코어 기준정보(개발모델 · 과제)의 정본은 허브다.",
    }
    refused = client.put(PATH, json=payload, headers=member.headers)
    assert refused.status_code == 403

    padded = {**payload, "summary": f"  {payload['summary']}  "}
    saved = client.put(PATH, json=padded, headers=admin.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["summary"] == payload["summary"]  # 앞뒤 공백은 떼고
    assert client.get(PATH).json()["notes"] == payload["notes"]

    entry = db.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == "server.profile")
        .order_by(AuditEntry.seq.desc())
    ).first()
    assert entry is not None
    assert entry.changes["after"]["summary"] == payload["summary"]
    assert entry.changes["before"] == {"summary": "", "notes": ""}

    too_long = client.put(PATH, json={"summary": "가" * 301}, headers=admin.headers)
    assert too_long.status_code == 422


def test_토큰은_정의를_바꾸는_범위가_있어야_고친다(
    client: TestClient, admin: Signed, fresh: None
) -> None:
    """에이전트가 이 글로 플랫폼을 고른다 — 정의를 바꾸는 일과 같은 무게. 같은 서버 화면의
    다른 쓰기(확장 켜기)는 토큰에 열리지 않는다."""
    reader = _token(client, admin, ["read"])
    assert client.put(PATH, json={"summary": "x"}, headers=reader).status_code == 403
    writer = _token(client, admin, ["read", "ontology:write"])
    done = client.put(PATH, json={"summary": "보고서 쌍둥이"}, headers=writer)
    assert done.status_code == 200, done.text
    toggled = client.patch(
        "/api/server/extensions/sample", json={"enabled": True}, headers=writer
    )
    assert toggled.status_code == 403
