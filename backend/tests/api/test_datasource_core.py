"""형제 설치에서 **코어를 당겨온다** — 지난번 이후만, 무덤까지.

가짜 코어 창구를 httpx 에 끼워 소켓 없이 본다. 여기서 지키는 것: 지난번 이후만 받나,
**사라진 것이 이쪽에서 사용 중지가 되나**, 끝까지 받았을 때만 시계를 옮기나, 그리고
카탈로그를 읽어 대응을 제안하나.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.modules.datasources import services
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_property, _make_type


class FakeCore:
    """형제 설치의 `/api/core` 흉내 — `since` 로 거르고, `limit` 만큼 쪽을 나눈다."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = [
            {
                "key": "V-001",
                "label": "ANSYS",
                "status": "active",
                "updated_at": "2026-09-01T00:00:00.000000Z",
                "deleted": False,
                "properties": {"country": "미국", "rating": 92},
            },
            {
                "key": "V-002",
                "label": "Altair",
                "status": "active",
                "updated_at": "2026-09-02T00:00:00.000000Z",
                "deleted": False,
                "properties": {"country": "미국", "rating": 80},
            },
        ]
        self.as_of = "2026-09-02T12:00:00.000000Z"
        self.requests: list[httpx.Request] = []
        self.token = "Bearer sibling-token"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != self.token:
            return httpx.Response(401, json={"error": {"message": "범위가 없습니다"}})
        if request.url.path.endswith("/core"):
            return httpx.Response(
                200,
                json={
                    "system": "sibling",
                    "revision": "2-0000000001",
                    "as_of": self.as_of,
                    "types": [
                        {
                            "slug": "vendor",
                            "label": "공급사",
                            "count": len(self.items),
                            "updated_at": self.as_of,
                            "endpoint": "http://sibling.local/api/core/vendor",
                            "properties": [
                                {"key": "country", "label": "국가", "data_type": "text"},
                                {"key": "rating", "label": "점수", "data_type": "number"},
                                {"key": "ghost", "label": "유령", "data_type": "text"},
                            ],
                        }
                    ],
                },
            )
        if not request.url.path.endswith("/core/vendor"):
            return httpx.Response(404, json={"error": {"message": "안 연 타입"}})

        query = parse_qs(request.url.query.decode())
        since = query.get("since", [""])[0]
        limit = int(query.get("limit", ["500"])[0])
        cursor = query.get("cursor", [""])[0]
        rows = [one for one in self.items if not since or one["updated_at"] > since]
        # 처음 받아 가는 쪽에는 무덤을 안 보낸다 — 없던 것을 지우라고 할 이유가 없다.
        if not since:
            rows = [one for one in rows if not one.get("deleted")]
        start = int(cursor or 0)
        page = rows[start : start + limit]
        more = start + limit < len(rows)
        return httpx.Response(
            200,
            json={
                "type_slug": "vendor",
                "since": since or None,
                # **쪽이 남으면 시계를 안 준다** — 받는 쪽이 중간에서 멈춰도 잃지 않는다.
                "as_of": None if more else self.as_of,
                "next": str(start + limit) if more else None,
                "items": page,
            },
        )


@pytest.fixture
def sibling() -> Iterator[FakeCore]:
    fake = FakeCore()
    services.transport = httpx.MockTransport(fake)
    try:
        yield fake
    finally:
        services.transport = None


def _vendor_type(client: TestClient, admin: Signed) -> str:
    vendor = _make_type(
        client, admin, label=f"공급사{uuid.uuid4().hex[:6]}", key_policy="optional"
    )
    _make_property(client, admin, vendor, key="country", label="국가", data_type="text")
    _make_property(client, admin, vendor, key="rating", label="점수", data_type="number")
    return vendor


def _source(client: TestClient, admin: Signed, vendor: str, **kw: Any) -> dict[str, Any]:
    body = {
        "slug": f"sib_{uuid.uuid4().hex[:6]}",
        "name": "형제 설치 공급사",
        "kind": "sp_core",
        "base_url": "http://sibling.local/api",
        "entity_set": "vendor",
        "type_slug": vendor,
        "workspace_slug": admin.workspace,
        "auth_kind": "bearer",
        "auth_secret": "sibling-token",
        "page_size": 50,
        "mapping": {
            "external_key": "key",
            "columns": [
                {"source": "key", "target": "key"},
                {"source": "label", "target": "label"},
                {"source": "country", "target": "properties.country"},
                {"source": "rating", "target": "properties.rating"},
            ],
        },
        **kw,
    }
    made = client.post("/api/datasources", json=body, headers=admin.headers)
    assert made.status_code == 201, made.text
    return dict(made.json())


def _sync(client: TestClient, who: Signed, slug: str, *, apply: bool = True) -> dict[str, Any]:
    started = client.post(
        f"/api/datasources/{slug}/sync",
        params={"apply": "true" if apply else "false"},
        headers=who.headers,
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, who, started.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _rows(client: TestClient, who: Signed, slug: str) -> dict[str, str]:
    got = client.get(f"/api/objects/{slug}?limit=200", headers=who.headers).json()
    return {one["key"]: one["status"] for one in got["items"]}


def test_처음엔_전부_받고_그_다음엔_바뀐_것만(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)

    first = _sync(client, admin, source["slug"])
    assert first["counts"]["create"] == 2
    assert _rows(client, admin, vendor) == {"V-001": "active", "V-002": "active"}

    # **시계를 받아 적어 둔다** — 다음 호출이 그것을 그대로 돌려준다.
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["since_mark"] == sibling.as_of

    again = _sync(client, admin, source["slug"])
    assert again["counts"]["create"] == 0 and again["counts"]["update"] == 0
    assert parse_qs(str(sibling.requests[-1].url.query.decode()))["since"] == [sibling.as_of]

    # 상대에서 하나가 바뀌면 그 하나만 온다.
    sibling.items[0]["label"] = "Ansys Inc."
    sibling.items[0]["updated_at"] = "2026-09-03T00:00:00.000000Z"
    sibling.as_of = "2026-09-03T12:00:00.000000Z"
    delta = _sync(client, admin, source["slug"])
    assert delta["counts"]["update"] == 1 and delta["counts"]["create"] == 0


def test_상대에서_사라진_것은_이쪽에서_사용_중지가_된다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """목록만 받으면 받는 쪽은 **삭제를 영영 모른다** — 무덤이 그래서 온다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    _sync(client, admin, source["slug"])

    sibling.items[1] = {
        "key": "V-002",
        "label": "Altair",
        "status": "deprecated",
        "updated_at": "2026-09-04T00:00:00.000000Z",
        "deleted": True,
        "merged_into": "V-001",
        "properties": {},
    }
    sibling.as_of = "2026-09-04T12:00:00.000000Z"

    done = _sync(client, admin, source["slug"])
    assert done["counts"]["deprecated"] == 1
    assert _rows(client, admin, vendor) == {"V-001": "active", "V-002": "deprecated"}

    # **지우지 않는다** — 가리키는 참조와 첨부가 밖에 남아 있다. 까닭은 이력에 적힌다.
    found = client.get(f"/api/objects/{vendor}?q=Altair", headers=admin.headers).json()
    object_id = found["items"][0]["id"]
    history = client.get(
        f"/api/objects/{vendor}/{object_id}/history", headers=admin.headers
    ).json()
    assert any("합쳐져" in (one["reason"] or "") for one in history)


def test_쪽이_남으면_시계를_안_옮긴다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """중간에서 멈춘 쪽이 남은 쪽을 영영 안 받는 일을 막는다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, page_size=1)

    done = _sync(client, admin, source["slug"])
    assert done["counts"]["create"] == 2  # 쪽을 이어 받아 둘 다 들어왔다
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["since_mark"] == sibling.as_of


def test_이번에_안_온_것_중지는_켤_수_없다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """증분에서 켜면 첫 동기화 다음 날 **멀쩡한 객체 전부가 사용 중지**가 된다."""
    vendor = _vendor_type(client, admin)
    denied = client.post(
        "/api/datasources",
        json={
            "slug": f"sib_{uuid.uuid4().hex[:6]}",
            "name": "형제",
            "kind": "sp_core",
            "base_url": "http://sibling.local/api",
            "entity_set": "vendor",
            "type_slug": vendor,
            "deprecate_missing": True,
            "mapping": {
                "external_key": "key",
                "columns": [{"source": "label", "target": "label"}],
            },
        },
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "무덤으로 알려" in denied.json()["error"]["message"]


def test_카탈로그를_읽어_대응을_제안한다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """옮겨 적게 하면 상대가 칸을 하나 더하는 날 그 대응이 조용히 뒤처진다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)

    got = client.post(f"/api/datasources/{source['slug']}/core-suggest", headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["system"] == "sibling" and body["type_label"] == "공급사"
    assert body["mapping"]["external_key"] == "key"
    targets = {one["source"]: one["target"] for one in body["mapping"]["columns"]}
    assert targets["country"] == "properties.country"
    assert targets["rating"] == "properties.rating"
    # **못 이은 것은 까닭과 함께 돌려준다** — 조용히 빼면 사람은 그 칸이 온 줄 안다.
    ghost = next(one for one in body["properties"] if one["key"] == "ghost")
    assert ghost["target"] is None and "없습니다" in ghost["note"]
    assert any("이쪽에 없는 칸" in one for one in body["notes"])


def test_토큰이_틀리면_상대의_말을_그대로_옮긴다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, auth_secret="wrong")
    done = _sync(client, admin, source["slug"], apply=False)
    assert done["run"]["status"] == "failed"
    assert "범위가 없습니다" in " ".join(str(one) for one in done["run"]["errors"])
