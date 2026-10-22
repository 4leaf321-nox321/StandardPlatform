"""누가 무엇을 보나 — **기본은 부서와 상관없이 누구나 전부**(ADR 0034).

보는 것과 고치는 것은 다른 축이다. 조회를 전원에게 열어도 고치는 것은 그대로 소유 부서의
관리자다 — 둘이 함께 열리면 「보이니까 고쳐도 되는 줄」 알게 된다.

조회를 부서로 좁힌 설치(`VIEW_SCOPE=department`)의 가리기는 `test_object_visibility.py` 등이
`department_scope` 를 켜고 지킨다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from tests.api.conftest import Signed
from tests.api.test_core_api import _open, _token
from tests.api.test_ontology import _make_object, _make_type


def _elsewhere(client: TestClient, admin: Signed) -> str:
    """내가 속하지 않은 부서 하나."""
    slug = f"far-{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/workspaces", json={"slug": slug, "name": "먼 부서"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    return slug


def _get(client: TestClient, who: Signed | dict[str, str], url: str, **params: Any) -> Any:
    headers = who if isinstance(who, dict) else who.headers
    got = client.get(url, params=params, headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def test_기본은_누구나_전부_본다() -> None:
    """설정을 안 주면 전원 공개다 — 운영 설치가 이 값으로 뜬다."""
    assert Settings().view_scope == "all"


def test_남의_부서_객체도_목록_상세_검색에서_보이지만_고치지는_못한다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    far = _elsewhere(client, admin)
    made = _make_object(client, admin, part, label=f"먼부품{tag}", workspace_slug=far)

    for who in (member, manager):
        labels = {one["label"] for one in _get(client, who, f"/api/objects/{part}")["items"]}
        assert f"먼부품{tag}" in labels
        detail = _get(client, who, f"/api/objects/{part}/{made['id']}")
        assert detail["object"]["label"] == f"먼부품{tag}"
        found = _get(client, who, "/api/search", q=f"먼부품{tag}")
        assert found["total"] == 1

    # 고치는 것은 소유 부서의 관리자다 — 내 부서의 관리자여도 남의 부서 것은 못 고친다.
    edit = client.patch(
        f"/api/objects/{part}/{made['id']}",
        json={"description": "남이 고침"},
        headers=manager.headers,
    )
    assert edit.status_code == 403, edit.text


def test_코어_API_도_토큰_주인의_부서와_상관없이_전부_낸다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """바깥 플랫폼이 받아 가는 것도 같다 — 토큰 주인의 소속이 받는 범위를 줄이지 않는다."""
    tag = uuid.uuid4().hex[:6]
    vendor = _make_type(client, admin, label=f"공급사{tag}", key_policy="optional")
    _open(client, admin, vendor)
    far = _elsewhere(client, admin)
    _make_object(
        client, admin, vendor, label="먼 공급사", key=f"FAR-{tag}", workspace_slug=far
    )

    # 시스템 관리자가 아닌 **멤버의** 토큰이어야 뜻이 있다 — 관리자는 원래 전부 본다.
    token = _token(client, member, f"kit-{tag}", ["core:read"])
    rows = _get(client, {"Authorization": f"Bearer {token}"}, f"/api/core/{vendor}")
    assert f"FAR-{tag}" in {one["key"] for one in rows["items"]}


def test_부서에는_공개_범위_칸이_없다(client: TestClient, admin: Signed) -> None:
    """예전의 「가입자 전원 / 멤버만」 은 아무 데도 안 걸렸다 — 칸째 지웠다(ADR 0034)."""
    far = _elsewhere(client, admin)
    body = _get(client, admin, "/api/workspaces", all=True)
    row = next(one for one in body if one["slug"] == far)
    assert "restricted" not in row
