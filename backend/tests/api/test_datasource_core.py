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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.datasources import odata, services
from app.modules.datasources.models import DataSource
from app.shared import singleton
from app.shared.errors import AppError
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
        #: 선 — 봉투 이름이 그대로 관계 적재의 칸 이름이다(`src` · `relation` · `dst`).
        self.edges: list[dict[str, Any]] = []
        self.edges_as_of = "2026-09-02T12:00:00.000000Z"
        #: 참이면 선 창구가 「처음부터 다시 받아라」 로 답한다(무덤의 보관 기간이 지났다).
        self.edges_reset = False
        self.requests: list[httpx.Request] = []
        self.token = "Bearer sibling-token"
        #: 참이면 SSO 앞단처럼 로그인 화면(HTML)을 200 으로 준다.
        self.login_page = False
        #: 선 창구가 돌려줄 HTTP 상태 — 500 이면 선만 못 받는다.
        self.edges_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.login_page:
            return httpx.Response(200, text="<html><body>사내 로그인</body></html>")
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
        if request.url.path.endswith("/core/vendor/relations"):
            return self._relations(request)
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

    def _relations(self, request: httpx.Request) -> httpx.Response:
        """선 창구 — 객체와 같은 규칙(`since` · `as_of`), 끊긴 선은 `deleted`. reset 은 진짜
        코어처럼 **시계를 준 물음에만** 보낸다(빈 시계는 처음부터 받는 것이라 보낼 까닭이
        없다 — `coreapi/services.py`)."""
        if self.edges_status != 200:
            return httpx.Response(
                self.edges_status, json={"error": {"message": "선 창구 고장"}}
            )
        since = parse_qs(request.url.query.decode()).get("since", [""])[0]
        if self.edges_reset and since:
            return httpx.Response(
                200,
                json={
                    "type_slug": "vendor",
                    "as_of": None,
                    "items": [],
                    "reset": True,
                    "reset_reason": "30일보다 오래된 시점부터는 끊긴 선을 알려 줄 수 없습니다",
                },
            )
        rows = [one for one in self.edges if not since or one["updated_at"] > since]
        if not since:
            rows = [one for one in rows if not one.get("deleted")]
        query = parse_qs(request.url.query.decode())
        limit = int(query.get("limit", ["500"])[0])
        start = int(query.get("cursor", ["0"])[0])
        more = start + limit < len(rows)
        return httpx.Response(
            200,
            json={
                "type_slug": "vendor",
                "since": since or None,
                "as_of": None if more else self.edges_as_of,
                "next": str(start + limit) if more else None,
                "items": rows[start : start + limit],
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


def _partner_kind(client: TestClient, admin: Signed, vendor: str) -> str:
    """공급사끼리 잇는 관계 하나 — 양 끝이 같은 타입이라 한 소스로 끝난다."""
    kind = f"partner_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "협력",
                    "src_type_slugs": [vendor],
                    "dst_type_slugs": [vendor],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    return kind


def test_선도_함께_받는다(client: TestClient, admin: Signed, sibling: FakeCore) -> None:
    """**객체만 받으면 받는 쪽은 점만 있고 선이 없다.**

    그것을 제 쪽에서 다시 만들려면 상대가 이미 쥔 관계를 추측해야 한다. 상대의 코어 창구는
    선도 같은 규칙으로 열어 준다(`/core/<타입>/relations`) — 켜면 한 실행에서 객체 뒤에 받는다.
    """
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [
        {
            "src": "V-001",
            "relation": kind,
            "dst": "V-002",
            "dst_type": "vendor",
            "evidence_note": "상대 설치의 계약서",
            "updated_at": "2026-09-02T00:00:00.000000Z",
            "deleted": False,
        }
    ]
    source = _source(client, admin, vendor, options={"relations": True})

    first = _sync(client, admin, source["slug"])
    assert first["counts"]["create"] == 2, first["counts"]
    assert first["counts"]["relations_create"] == 1, first["counts"]

    # 선이 정말 섰나 — 객체 상세의 「이어진 것」 으로 본다.
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    one = next(row for row in listed if row["key"] == "V-001")
    detail = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
    assert [edge["relation"] for edge in detail["related"]] == [kind], detail["related"]

    # **선의 시계는 따로 움직인다** — 한 칸을 나눠 쓰면 그 사이에 바뀐 선을 영영 안 받는다.
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["relations_since_mark"] == sibling.edges_as_of
    assert saved["since_mark"] == sibling.as_of

    # 다시 돌리면 선도 「그대로」 다 — 두 번 받아도 두 겹이 안 된다.
    again = _sync(client, admin, source["slug"])
    assert again["counts"].get("relations_create", 0) == 0, again["counts"]


def test_상대가_끊은_선은_이쪽에서도_끊긴다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """선은 행을 정말 지우므로, 무덤이 없으면 받는 쪽은 **끊긴 것을 영영 모른다.**"""
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [
        {
            "src": "V-001",
            "relation": kind,
            "dst": "V-002",
            "dst_type": "vendor",
            "updated_at": "2026-09-02T00:00:00.000000Z",
            "deleted": False,
        }
    ]
    source = _source(client, admin, vendor, options={"relations": True})
    assert _sync(client, admin, source["slug"])["counts"]["relations_create"] == 1

    # 상대가 끊었다 — 무덤으로 온다.
    sibling.edges[0]["deleted"] = True
    sibling.edges[0]["updated_at"] = "2026-09-03T00:00:00.000000Z"
    sibling.edges_as_of = "2026-09-03T12:00:00.000000Z"
    cut = _sync(client, admin, source["slug"])
    assert cut["counts"]["relations_unlink"] == 1, cut["counts"]

    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    one = next(row for row in listed if row["key"] == "V-001")
    detail = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
    assert detail["related"] == [], detail["related"]


def test_선은_reset_을_받으면_처음부터_다시_받는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """상대가 「그 시점부터는 끊긴 선을 알려 줄 수 없다」 고 하면(무덤의 보관 기간이 지났다)
    빈 쪽을 「바뀐 것 없음」 으로 읽으면 안 된다 — 시계를 비우고 전량을 다시 받아
    **파일대로 맞춤**으로 넣는다(온 목록에 나온 출발 객체 · 관계 종류 범위에서)."""
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [
        {
            "src": "V-001",
            "relation": kind,
            "dst": "V-002",
            "dst_type": "vendor",
            "updated_at": "2026-09-02T00:00:00.000000Z",
            "deleted": False,
        }
    ]
    source = _source(client, admin, vendor, options={"relations": True})
    assert _sync(client, admin, source["slug"])["counts"]["relations_create"] == 1

    before = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()

    # 상대가 reset 을 보낸다. **미리 보기는 그 신호를 먹지 않는다** — 예전에는 계획이 시계를
    # 비우고 커밋해, 상대가 빈 시계에는 reset 을 안 보내니 다음 적용이 「더하기」 로 받아
    # 「파일대로 맞춤」 이 영영 안 일어났다(2026-10-08).
    sibling.edges_reset = True
    sibling.edges_as_of = "2026-09-03T12:00:00.000000Z"
    planned = _sync(client, admin, source["slug"], apply=False)
    assert any("처음부터 다시" in one for one in planned["run"]["errors"])
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["relations_since_mark"] == before["relations_since_mark"] != ""

    reset = _sync(client, admin, source["slug"])
    assert any("처음부터 다시" in one for one in reset["run"]["errors"]), reset["run"][
        "errors"
    ]
    # 맞춤이 끝나면 새 시계로 — 다음부터는 바뀐 것만.
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["relations_since_mark"] == "2026-09-03T12:00:00.000000Z"


def test_선_계획_전체가_거절되면_시계를_안_옮긴다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """한 번에 넣는 상한을 넘거나 모르는 열이 오면 계획 **전체**가 거절된다 — 줄이 하나도
    없어 `relations_error` 셈은 0 이다. 셈만 보던 때는 그것을 성공으로 알고 시계를 옮겨 그
    증분의 선을 영영 놓쳤다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [
        {
            "src": "V-001",
            "relation": kind,
            "dst": "V-002",
            "dst_type": "vendor",
            "updated_at": "2026-09-02T00:00:00.000000Z",
            "deleted": False,
            # 이쪽 관계 종류에 없는 속성 — 「모르는 열」
            "properties": {"weight_kg": 3},
        }
    ]
    source = _source(client, admin, vendor, options={"relations": True})
    done = _sync(client, admin, source["slug"])
    # 객체는 들어가도 선이 거절됐으면 실행은 「실패」 다 — 「ok」 면 선이 빠진 줄 모른다.
    assert done["run"]["status"] == "failed", done["run"]
    assert any("모르는 열" in one for one in done["run"]["errors"]), done["run"]["errors"]
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["relations_since_mark"] == ""


# --- 끝점이 아직 없는 선 — 나머지를 막지 않고, 다음 동기화에서 다시 -------------------------


def _edge(src: str, kind: str, dst: str, at: str, *, deleted: bool = False) -> dict[str, Any]:
    return {
        "src": src,
        "relation": kind,
        "dst": dst,
        "dst_type": "vendor",
        "updated_at": at,
        "deleted": deleted,
    }


def test_끝점이_아직_없는_선은_기다렸다가_다음_동기화에서_선다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """타입마다 소스가 따로라, 선이 가리키는 쪽이 아직 안 들어왔을 수 있다(고장 모드 →
    메커니즘). 예전에는 그런 줄 하나가 **그 소스의 선 전부**를 막았고 시계도 못 옮겼다 —
    끝점이 끝내 안 오면 그 소스의 선은 영영 안 섰다. 이제는 나머지를 넣고, 못 찾은 줄만
    남겼다가 다음 동기화가 다시 넣는다(상대가 그 선을 다시 보내지 않아도)."""
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [
        _edge("V-001", kind, "V-002", "2026-09-02T00:00:00.000000Z"),
        _edge("V-001", kind, "V-404", "2026-09-02T00:00:00.000000Z"),  # 아직 없는 끝점
    ]
    source = _source(client, admin, vendor, options={"relations": True})

    first = _sync(client, admin, source["slug"])
    assert first["run"]["status"] == "ok", first["run"]
    assert first["counts"]["relations_create"] == 1, first["counts"]
    assert first["counts"]["relations_waiting"] == 1, first["counts"]
    assert first["counts"].get("relations_error", 0) == 0, first["counts"]
    assert any("기다립니다" in one and "V-404" in one for one in first["run"]["errors"])
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    # 시계는 옮긴다 — 못 찾은 줄은 따로 남겼다.
    assert saved["relations_since_mark"] == sibling.edges_as_of
    assert saved["relations_waiting"] == 1

    # 끝점이 들어온다. 선 창구는 새로 보낼 것이 없다(그 선은 지난번에 이미 왔다).
    sibling.items.append(
        {
            "key": "V-404",
            "label": "Hexagon",
            "status": "active",
            "updated_at": "2026-09-03T00:00:00.000000Z",
            "deleted": False,
            "properties": {},
        }
    )
    sibling.as_of = "2026-09-03T12:00:00.000000Z"
    second = _sync(client, admin, source["slug"])
    assert second["counts"]["relations_create"] == 1, second["counts"]
    assert second["counts"].get("relations_waiting", 0) == 0, second["counts"]
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["relations_waiting"] == 0

    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    one = next(row for row in listed if row["key"] == "V-001")
    detail = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
    assert sorted(edge["object_label"] for edge in detail["related"]) == ["Altair", "Hexagon"]


def test_기다리던_선을_상대가_끊으면_더_기다리지_않는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.edges = [_edge("V-001", kind, "V-404", "2026-09-02T00:00:00.000000Z")]
    source = _source(client, admin, vendor, options={"relations": True})
    assert _sync(client, admin, source["slug"])["counts"]["relations_waiting"] == 1

    sibling.edges = [
        _edge("V-001", kind, "V-404", "2026-09-03T00:00:00.000000Z", deleted=True)
    ]
    sibling.edges_as_of = "2026-09-03T12:00:00.000000Z"
    after = _sync(client, admin, source["slug"])
    assert after["counts"].get("relations_waiting", 0) == 0, after["counts"]
    saved = client.get(f"/api/datasources/{source['slug']}", headers=admin.headers).json()
    assert saved["relations_waiting"] == 0


# --- 거울 — 상대에서 비우고 뺀 것도 따라온다 ---------------------------------------------


def test_상대에서_비운_칸_뺀_별칭_사용_중지도_따라온다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """동기화가 **더하고 바꾸기만** 했다. 허브는 빈 칸을 키째 빼고 보내는데(「비었다」 를
    가르는 것은 받는 쪽 몫 — 카탈로그에 칸 목록이 있다), 받는 쪽이 그것을 「안 건드림」 으로
    읽어 허브에서 지운 값이 쌍둥이에 영영 남았다. 별칭은 더하기만 해 뺀 것이 남고, 사용
    중지는 대응할 자리가 없어 안 따라왔다. 상대가 보내는 한 줄은 그 객체의 지금 모습 전부다."""
    vendor = _vendor_type(client, admin)
    sibling.items[0]["properties"]["aliases"] = ["Ansys", "앤시스"]
    source = _source(client, admin, vendor)
    assert _sync(client, admin, source["slug"])["counts"]["create"] == 2

    def detail(key: str) -> dict[str, Any]:
        listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
        one = next(row for row in listed if row["key"] == key)
        got = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
        return dict(got["object"])

    first = detail("V-001")
    assert first["properties"]["rating"] == 92 and first["aliases"] == ["Ansys", "앤시스"]

    # 허브에서 점수를 지우고(키째 빠진다), 별칭 하나를 빼고, V-002 를 사용 중지한다.
    del sibling.items[0]["properties"]["rating"]
    sibling.items[0]["properties"]["aliases"] = ["Ansys"]
    sibling.items[1]["status"] = "deprecated"
    for one in sibling.items:
        one["updated_at"] = "2026-09-03T00:00:00.000000Z"
    sibling.as_of = "2026-09-03T12:00:00.000000Z"
    after = _sync(client, admin, source["slug"])
    assert after["run"]["status"] == "ok", after["run"]
    assert after["counts"]["update"] == 2, after["counts"]

    got = detail("V-001")
    assert "rating" not in got["properties"], got["properties"]
    assert got["properties"]["country"] == "미국"  # 온 것은 그대로
    assert got["aliases"] == ["Ansys"]
    assert _rows(client, admin, vendor)["V-002"] == "deprecated"

    # 별칭을 다 빼면 다 빠진다.
    del sibling.items[0]["properties"]["aliases"]
    sibling.items[0]["updated_at"] = "2026-09-04T00:00:00.000000Z"
    sibling.as_of = "2026-09-04T12:00:00.000000Z"
    _sync(client, admin, source["slug"])
    assert detail("V-001")["aliases"] == []


def test_필수_칸은_상대가_비워도_비우지_않는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """비우면 그 줄이 「값이 필요합니다」 오류가 되고, 오류 한 줄이 동기화 전체를 막는다."""
    vendor = _make_type(
        client, admin, label=f"공급사{uuid.uuid4().hex[:6]}", key_policy="optional"
    )
    _make_property(client, admin, vendor, key="country", label="국가", data_type="text")
    _make_property(
        client, admin, vendor, key="rating", label="점수", data_type="number", required=True
    )
    source = _source(client, admin, vendor)
    assert _sync(client, admin, source["slug"])["counts"]["create"] == 2

    del sibling.items[0]["properties"]["rating"]
    sibling.items[0]["updated_at"] = "2026-09-03T00:00:00.000000Z"
    sibling.as_of = "2026-09-03T12:00:00.000000Z"
    after = _sync(client, admin, source["slug"])
    assert after["run"]["status"] == "ok", after["run"]
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    one = next(row for row in listed if row["key"] == "V-001")
    assert one["properties"]["rating"] == 92


# --- 많이 와서 끊길 때 — 받은 만큼 넣고 끊은 자리에서 잇는다 --------------------------------


def _item(key: str, label: str, at: str, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": "active",
        "updated_at": at,
        "deleted": False,
        "properties": {},
        **extra,
    }


def _saved(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    return dict(client.get(f"/api/datasources/{slug}", headers=admin.headers).json())


def test_상한에서_끊기면_받은_만큼_넣고_다음_차례가_끊은_자리에서_잇는다(
    client: TestClient, admin: Signed, sibling: FakeCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """예전에는 끊기면 시계도 자리도 안 남겨, 다음 차례가 같은 `since` 로 같은 5만 행을 다시
    받아 **영영 앞으로 못 갔다** — 안내는 「`$filter` 로 나눠」 였는데 코어 소스는 그 칸을
    쓰지도 않는다(2026-10-08)."""
    monkeypatch.setattr(odata, "MAX_ROWS", 3)
    vendor = _vendor_type(client, admin)
    sibling.items = [
        _item(f"V-00{n}", f"공급사 {n}", f"2026-09-0{n}T00:00:00.000000Z") for n in range(1, 6)
    ]
    source = _source(client, admin, vendor, page_size=2)

    first = _sync(client, admin, source["slug"])
    assert first["run"]["status"] == "ok", first["run"]
    assert first["counts"]["create"] == 4, first["counts"]  # 쪽 경계(2행씩)에서 끊는다
    assert any("끊은 자리에서 잇습니다" in one for one in first["run"]["errors"])
    saved = _saved(client, admin, source["slug"])
    assert saved["since_mark"] == ""  # 다 받기 전에는 시계를 안 옮긴다
    assert not any(key.startswith("_") for key in saved["options"])  # 화면의 설정이 아니다

    first_clock = sibling.as_of
    sibling.as_of = "2026-09-09T12:00:00.000000Z"  # 그 사이 상대의 시계가 갔다
    second = _sync(client, admin, source["slug"])
    assert second["run"]["status"] == "ok", second["run"]
    assert second["counts"]["create"] == 1, second["counts"]
    assert any(
        parse_qs(one.url.query.decode()).get("cursor") == ["4"] for one in sibling.requests
    )
    assert sorted(_rows(client, admin, vendor)) == [f"V-00{n}" for n in range(1, 6)]
    # 다 받은 뒤의 시계는 **처음 끊었을 때 상대의 시계** — 여러 차례에 걸쳐 받는 사이 늦게
    # 커밋된 것 · 받은 뒤에 지워진 것을 그 시계에서 다시 받는다.
    assert _saved(client, admin, source["slug"])["since_mark"] == first_clock


def test_선도_상한에서_끊기면_받은_만큼_넣고_다음_차례가_잇는다(
    client: TestClient, admin: Signed, sibling: FakeCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """선 쪽도 같았다 — 끊기면 시계를 그대로 두어, 다음 차례가 같은 줄만 다시 받고 「다음
    동기화가 같은 자리에서 잇습니다」 는 사실이 아니었다(2026-10-08)."""
    monkeypatch.setattr(odata, "MAX_ROWS", 3)
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.items = [
        _item(f"V-00{n}", f"공급사 {n}", f"2026-09-0{n}T00:00:00.000000Z") for n in range(1, 5)
    ]
    sibling.edges = [
        _edge(src, kind, dst, "2026-09-02T00:00:00.000000Z")
        for src, dst in (
            ("V-001", "V-002"),
            ("V-001", "V-003"),
            ("V-001", "V-004"),
            ("V-002", "V-003"),
            ("V-002", "V-004"),
        )
    ]
    source = _source(client, admin, vendor, page_size=2, options={"relations": True})

    first = _sync(client, admin, source["slug"])
    assert first["run"]["status"] == "ok", first["run"]
    assert first["counts"]["create"] == 4 and first["counts"]["relations_create"] == 4
    assert any("4줄까지" in one for one in first["run"]["errors"]), first["run"]["errors"]
    assert _saved(client, admin, source["slug"])["relations_since_mark"] == ""

    second = _sync(client, admin, source["slug"])
    assert second["counts"]["relations_create"] == 1, second["counts"]
    assert _saved(client, admin, source["slug"])["relations_since_mark"] == sibling.edges_as_of


def test_로그인_화면이_JSON_대신_오면_무엇이_왔는지_기록에_남긴다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """SSO 앞단은 로그인 화면(HTML)을 200 으로 준다. 예전에는 `JSONDecodeError` 가 작업을
    통째로 죽여 실행 기록도 `last_status` 도 안 남았다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    sibling.login_page = True
    done = _sync(client, admin, source["slug"])
    assert done["run"]["status"] == "failed", done["run"]
    said = " ".join(done["run"]["errors"])
    assert "JSON 이 아닙니다" in said and "사내 로그인" in said
    assert _saved(client, admin, source["slug"])["last_status"] == "failed"


def test_선_창구에_못_닿아도_객체와_외부_식별자는_남고_실행은_실패로_적힌다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """선 창구의 연결 오류가 예외로 빠져나가면 마지막 커밋(외부 식별자 · 객체의 시계)이
    롤백되고 기록은 「계획 · 미완료」 로 남았다 — 다음 동기화가 같은 객체를 이름으로 다시
    찾거나 새로 만들었다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    sibling.edges_status = 500
    source = _source(client, admin, vendor, options={"relations": True})
    done = _sync(client, admin, source["slug"])
    assert done["run"]["status"] == "failed" and done["run"]["applied"] is True, done["run"]
    assert any("선을 받지 못했습니다" in one for one in done["run"]["errors"])
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    assert {one["key"]: one["external_ids"] for one in listed} == {
        "V-001": {source["slug"]: "V-001"},
        "V-002": {source["slug"]: "V-002"},
    }
    saved = _saved(client, admin, source["slug"])
    assert saved["last_status"] == "failed"
    assert saved["since_mark"] == sibling.as_of and saved["relations_since_mark"] == ""

    # 고쳐지면 다음 차례가 선을 받는다 — 객체를 새로 만들지 않는다.
    sibling.edges_status = 200
    again = _sync(client, admin, source["slug"])
    assert again["run"]["status"] == "ok", again["run"]
    assert again["counts"]["create"] == 0


def test_지웠다_같은_키로_다시_만들면_산_것이_이긴다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """한 증분에 무덤과 산 행이 함께 온다. 예전에는 산 행으로 고친 객체를 무덤이 곧바로
    사용 중지했다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    _sync(client, admin, source["slug"])

    sibling.items[1] = _item("V-002", "Altair", "2026-09-03T00:00:00.000000Z", deleted=True)
    sibling.items.append(_item("V-002", "Altair (새로)", "2026-09-03T01:00:00.000000Z"))
    sibling.as_of = "2026-09-04T12:00:00.000000Z"
    done = _sync(client, admin, source["slug"])
    assert done["counts"]["deprecated"] == 0, done["counts"]
    assert _rows(client, admin, vendor)["V-002"] == "active"


def test_기다리던_선이_넣을_수_없게_되면_빼고_나머지를_막지_않는다(
    client: TestClient, admin: Signed, sibling: FakeCore
) -> None:
    """기다리던 줄이 그 사이 오류 줄이 되면(관계 종류가 지워짐) 그 한 줄이 계획 전체를
    막았고, 그 줄은 적용에 성공해야만 빠지므로 **그 소스의 선이 영영 안 섰다**(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    doomed = _partner_kind(client, admin, vendor)
    sibling.edges = [_edge("V-001", doomed, "V-404", "2026-09-02T00:00:00.000000Z")]
    source = _source(client, admin, vendor, options={"relations": True})
    assert _sync(client, admin, source["slug"])["counts"]["relations_waiting"] == 1

    gone = client.delete(f"/api/ontology/relation-types/{doomed}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    sibling.edges = [_edge("V-001", kind, "V-002", "2026-09-03T00:00:00.000000Z")]
    sibling.edges_as_of = "2026-09-03T12:00:00.000000Z"
    after = _sync(client, admin, source["slug"])
    assert after["run"]["status"] == "ok", after["run"]
    assert after["counts"]["relations_create"] == 1, after["counts"]
    assert any("더 기다리지 않습니다" in one for one in after["run"]["errors"])
    saved = _saved(client, admin, source["slug"])
    assert saved["relations_waiting"] == 0
    assert saved["relations_since_mark"] == sibling.edges_as_of


def test_같은_소스의_적용은_한_곳에서만_돈다(
    client: TestClient, admin: Signed, sibling: FakeCore, db: Session
) -> None:
    """둘이 함께 돌면 같은 새 행을 둘 다 만들고, 늦은 쪽은 외부 식별자의 유일 제약에서
    터진다 — 그 사이 같은 객체가 둘이 된다(2026-10-08). 계획은 아무것도 안 바꾸므로 막지
    않는다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    row = db.scalar(select(DataSource).where(DataSource.slug == source["slug"]))
    assert row is not None
    with singleton.held(f"datasource:{source['slug']}") as locked:
        assert locked is not None
        with pytest.raises(AppError) as caught:
            services.sync(db, None, row, apply=True)
        assert caught.value.code == services.BUSY
        planned = services.sync(db, None, row, apply=False)
        assert planned.run.status == "planned"
    assert _rows(client, admin, vendor) == {}
