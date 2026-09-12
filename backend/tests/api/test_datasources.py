"""데이터 소스 — **OData 에서 읽어 온톨로지를 채운다. 규칙은 파일과 같다.**

가짜 OData 서버(v4 쪽 넘김 + v2 봉투)를 httpx 에 끼워 소켓 없이 본다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.modules.datasources import services
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type

ROWS: list[dict[str, Any]] = [
    {
        "VendorNo": "V-001",
        "Name": "ANSYS Inc.",
        "Short": "Ansys",
        "CountryCd": "US",
        "Rating": 92,
    },
    {
        "VendorNo": "V-002",
        "Name": "Altair Engineering",
        "Short": "Altair",
        "CountryCd": "US",
        "Rating": 80,
    },
    {
        "VendorNo": "V-003",
        "Name": "마이다스아이티",
        "Short": "",
        "CountryCd": "KR",
        "Rating": 75,
    },
]


class FakeOData:
    """v4 서버 흉내 — `$top` 만큼씩 `@odata.nextLink` 로 넘긴다. `/v2/` 아래는 v2 봉투."""

    def __init__(self) -> None:
        self.rows = [dict(one) for one in ROWS]
        self.requests: list[httpx.Request] = []
        self.auth_required: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # `/plain/` 아래는 nextLink 를 안 주는 서버 흉내 — `$top` 이 상한일 뿐인 곳(Northwind
        # 공개 표본이 그렇다). 그러면 우리가 `$skip` 으로 넘겨야 전부 온다.
        plain = request.url.path.startswith("/plain/")
        if self.auth_required and request.headers.get("Authorization") != self.auth_required:
            return httpx.Response(401, json={"error": "no"})
        query = parse_qs(request.url.query.decode())
        top = int(query.get("$top", ["500"])[0])
        skip = int(query.get("$skip", ["0"])[0])
        rows = self.rows
        if "$filter" in query and "Rating ge 80" in query["$filter"][0]:
            rows = [one for one in rows if one["Rating"] >= 80]
        page = rows[skip : skip + top]
        if request.url.path.startswith("/v2/"):
            body: dict[str, Any] = {"d": {"results": page}}
            if skip + top < len(rows):
                body["d"]["__next"] = (
                    f"http://plm.local/v2/Suppliers?$top={top}&$skip={skip + top}"
                )
            return httpx.Response(200, json=body)
        body = {"value": page}
        if skip + top < len(rows) and not plain:
            body["@odata.nextLink"] = (
                f"http://plm.local/odata/Suppliers?$top={top}&$skip={skip + top}"
            )
        return httpx.Response(200, json=body)


@pytest.fixture
def plm() -> Iterator[FakeOData]:
    fake = FakeOData()
    services.transport = httpx.MockTransport(fake)
    try:
        yield fake
    finally:
        services.transport = None


def _vendor_type(client: TestClient, admin: Signed) -> str:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    _make_property(
        client,
        admin,
        vendor,
        key="country",
        label="나라",
        data_type="enum",
        enum_options=["한국", "미국"],
    )
    _make_property(client, admin, vendor, key="rating", label="점수", data_type="number")
    return vendor


def _source(client: TestClient, admin: Signed, vendor: str, **kw: Any) -> dict[str, Any]:
    body = {
        "slug": f"plm_{uuid.uuid4().hex[:6]}",
        "name": "PLM 공급사",
        "base_url": "http://plm.local/odata",
        "entity_set": "Suppliers",
        "type_slug": vendor,
        "workspace_slug": admin.workspace,
        "page_size": 2,
        "mapping": {
            "external_key": "VendorNo",
            "columns": [
                {"source": "VendorNo", "target": "key"},
                {"source": "Name", "target": "label"},
                {"source": "Short", "target": "alias"},
                {
                    "source": "CountryCd",
                    "target": "properties.country",
                    "values": {"US": "미국", "KR": "한국"},
                },
                {"source": "Rating", "target": "properties.rating"},
            ],
        },
        **kw,
    }
    made = client.post("/api/datasources", json=body, headers=admin.headers)
    assert made.status_code == 201, made.text
    return dict(made.json())


def test_시스템_관리자만_정의한다(client: TestClient, member: Signed) -> None:
    denied = client.get("/api/datasources", headers=member.headers)
    assert denied.status_code == 403


def test_칸_대응이_틀리면_저장에서_말한다(client: TestClient, admin: Signed) -> None:
    vendor = _vendor_type(client, admin)
    bad = client.post(
        "/api/datasources",
        json={
            "slug": "x",
            "name": "x",
            "base_url": "http://a/b",
            "entity_set": "S",
            "type_slug": vendor,
            "mapping": {
                "external_key": "Id",
                "columns": [{"source": "N", "target": "properties.nope"}],
            },
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422 and "nope" in bad.json()["error"]["message"]


def test_계획을_보고_적용하면_같은_객체를_다시_찾는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)

    # 미리 보기 — 그대로와 대응한 뒤.
    preview = client.post(f"/api/datasources/{source['slug']}/preview", headers=admin.headers)
    assert preview.status_code == 200, preview.text
    assert "VendorNo" in preview.json()["columns"]
    assert preview.json()["mapped"][0]["row"]["country"] == "미국"

    # 계획 — 아무것도 안 바뀐다. 쪽 넘김이 끝까지 따라간다(page_size 2, 3행).
    planned = client.post(f"/api/datasources/{source['slug']}/sync", headers=admin.headers)
    assert planned.status_code == 200, planned.text
    assert planned.json()["applied"] is False
    assert planned.json()["counts"] == {"create": 3, "update": 0, "unchanged": 0, "error": 0}
    assert client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["total"] == 0
    assert len(plm.requests) >= 2

    # 적용.
    done = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    by_key = {one["key"]: one for one in listed}
    assert by_key["V-001"]["label"] == "ANSYS Inc." and by_key["V-001"]["aliases"] == ["Ansys"]
    assert by_key["V-001"]["properties"] == {"country": "미국", "rating": 92}
    assert by_key["V-001"]["external_ids"] == {source["slug"]: "V-001"}

    # 바깥에서 이름이 바뀌고 우리 쪽 식별자를 고쳐도 — 외부 식별자로 같은 객체를 찾는다.
    client.patch(
        f"/api/objects/{vendor}/{by_key['V-001']['id']}",
        json={"label": "Ansys(우리 이름)"},
        headers=admin.headers,
    )
    plm.rows[0]["Name"] = "ANSYS, Inc."
    plm.rows[0]["Rating"] = 95
    again = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert again["counts"]["update"] == 1 and again["counts"]["create"] == 0
    changed = client.get(
        f"/api/objects/{vendor}/{by_key['V-001']['id']}", headers=admin.headers
    ).json()
    assert (
        changed["object"]["label"] == "ANSYS, Inc."
        and changed["object"]["properties"]["rating"] == 95
    )
    assert client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["total"] == 3

    runs = client.get(f"/api/datasources/{source['slug']}/runs", headers=admin.headers).json()
    assert [r["status"] for r in runs][:3] == ["ok", "ok", "planned"]


def test_값_대응표에_없는_값은_오류_행이고_아무것도_안_넣는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    plm.rows[2]["CountryCd"] = "JP"
    done = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert done["applied"] is False
    assert done["counts"]["error"] == 1
    assert any("JP" in e for e in done["errors"])
    assert client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["total"] == 0


def test_처음_만날_때는_별칭과_이름으로_찾고_겹치면_거절한다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """이미 사람이 만든 「Ansys」 가 있으면 새로 만들지 않고 그것을 채운다."""
    vendor = _vendor_type(client, admin)
    ours = _make_object(client, admin, vendor, label="Ansys")
    client.put(
        f"/api/objects/{vendor}/{ours['id']}/aliases",
        json={"aliases": ["ANSYS Inc."]},
        headers=admin.headers,
    )
    source = _source(client, admin, vendor)
    plan = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is True, plan
    got = client.get(f"/api/objects/{vendor}/{ours['id']}", headers=admin.headers).json()[
        "object"
    ]
    assert got["key"] == "V-001" and got["properties"]["country"] == "미국"
    assert list(got["external_ids"].values()) == ["V-001"]


def test_사라진_행은_기본_그대로_켜면_사용_중지(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    manual = _make_object(client, admin, vendor, label="손으로 만든 것")
    client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    )
    plm.rows.pop()  # 마이다스가 바깥에서 사라짐

    client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    )
    rows = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    assert all(one["status"] == "active" for one in rows)  # 기본: 건드리지 않는다

    client.patch(
        f"/api/datasources/{source['slug']}",
        json={"deprecate_missing": True},
        headers=admin.headers,
    )
    done = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert done["counts"]["deprecated"] == 1
    rows = {
        one["label"]: one["status"]
        for one in client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    }
    assert rows["마이다스아이티"] == "deprecated"
    assert rows["손으로 만든 것"] == "active"  # 사람이 만든 것은 안 건드린다
    assert rows[manual["label"]] == "active"


def test_v2_봉투와_인증과_필터(client: TestClient, admin: Signed, plm: FakeOData) -> None:
    vendor = _vendor_type(client, admin)
    plm.auth_required = "Bearer tok"
    source = _source(
        client,
        admin,
        vendor,
        base_url="http://plm.local/v2",
        auth_kind="bearer",
        auth_secret="tok",
        filter="Rating ge 80",
    )
    assert source["has_secret"] is True and source["auth_kind"] == "bearer"
    done = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    assert done["counts"]["create"] == 2

    # 비밀이 틀리면 연결 오류가 기록에 남는다 — 무엇이 실패했는지 말하며.
    client.patch(
        f"/api/datasources/{source['slug']}",
        json={"auth_secret": "bad"},
        headers=admin.headers,
    )
    failed = client.post(
        f"/api/datasources/{source['slug']}/sync", headers=admin.headers
    ).json()
    assert failed["run"]["status"] == "failed" and "인증" in failed["errors"][0]


def test_nextLink_없는_서버는_skip_으로_넘긴다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, base_url="http://plm.local/plain")
    done = client.post(
        f"/api/datasources/{source['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    ).json()
    assert done["run"]["rows_seen"] == 3 and done["counts"]["create"] == 3
    skips = [parse_qs(r.url.query.decode()).get("$skip", ["0"])[0] for r in plm.requests]
    assert skips == ["0", "2"]  # 2행씩: 꽉 찬 첫 쪽 → 다음, 덜 찬 둘째 쪽 → 끝


def test_타이머가_돌릴_차례(client: TestClient, admin: Signed, plm: FakeOData) -> None:
    from app.database import SessionLocal

    vendor = _vendor_type(client, admin)
    every = _source(client, admin, vendor, interval_minutes=60)
    manual = _source(client, admin, vendor, interval_minutes=0)
    with SessionLocal() as db:
        slugs = {one.slug for one in services.due(db)}
    assert every["slug"] in slugs and manual["slug"] not in slugs
    client.post(
        f"/api/datasources/{every['slug']}/sync",
        params={"apply": "true"},
        headers=admin.headers,
    )
    with SessionLocal() as db:
        assert every["slug"] not in {one.slug for one in services.due(db)}
    json.dumps(ROWS)  # 자료가 JSON 으로 나가는 모양인지 — 가짜 서버가 그대로 쓴다
