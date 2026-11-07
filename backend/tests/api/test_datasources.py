"""데이터 소스 — **OData 에서 읽어 온톨로지를 채운다. 규칙은 파일과 같다.**

가짜 OData 서버(v4 쪽 넘김 + v2 봉투)를 httpx 에 끼워 소켓 없이 본다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.datasources import services
from app.modules.datasources.models import DataSource
from tests.api.conftest import (
    Signed,
    bundle_import,
    finish_job,
    maintenance_counts,
    notifications_of,
    work_until,
)
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
        if request.url.path.endswith("/export.csv"):
            lines = ["VendorNo,Name,Short,CountryCd,Rating"] + [
                f"{r['VendorNo']},{r['Name']},{r['Short']},{r['CountryCd']},{r['Rating']}"
                for r in self.rows
            ]
            return httpx.Response(200, text="\n".join(lines))
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


class _Done:
    """옛 동기 응답 흉내 — 시험이 `.status_code` · `.text` · `.json()` 으로 읽는다."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        return self._body


def _sync(client: TestClient, who: Signed, slug: str, *, apply: bool = False) -> _Done:
    """동기화는 **작업**이다 — 넣고(202), 워커를 이 프로세스에서 돌리고, 결과를 옛 모양으로."""
    started = client.post(
        f"/api/datasources/{slug}/sync",
        params={"apply": "true" if apply else "false"},
        headers=who.headers,
    )
    if started.status_code != 202:
        return _Done(started.status_code, started.json())
    done = finish_job(client, who, started.json())
    if done["status"] != "done":
        return _Done(500, {"error": done.get("error")})
    return _Done(200, done["result"])


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


def _lock(client: TestClient, admin: Signed, type_slug: str, owner: str) -> None:
    """그 타입을 **허브가 관리하는 것**으로 만든다 — 묶음에 `source` 를 붙여 받으면 잠긴다.

    화면에는 잠그는 단추가 없다(`managed_by` 는 받기만 적는다) — 그래서 시험도 받는 길로
    잠근다.
    """
    done = bundle_import(
        client,
        admin,
        {
            "ontology": {"types": [{"slug": type_slug, "label": "공급사"}]},
            "source": owner,
            "apply": True,
        },
    )
    assert done["applied"] is True, done
    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert types[type_slug]["managed_by"] == owner


def test_잠긴_타입에도_지정한_출처의_적재는_들어온다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """**막기만 하면 받기도 막힌다.** 잠근 뜻은 「아무나 고치지 마라」 이고 「허브가 준 것도
    들어오지 마라」 가 아니다 — 그런데 동기화가 자기 출처 이름을 안 넘겨서 둘이 함께 막혔다.

    적재는 자기 이름을 말하고, 그것이 `managed_by` 와 같을 때만 통과한다. 이름을 적는 칸은
    slug 와 따로다 — slug 는 별칭 `source:<slug>` 에 박혀 바꿀 수 없어서, 그 칸이 없으면
    소스를 지우고 다시 만들어야 하고 그러면 외부 식별자를 전부 잃는다.
    """
    vendor = _vendor_type(client, admin)
    _lock(client, admin, vendor, "hub")

    # 1) 출처 이름을 안 적었다 — slug 가 이름이 되고, 그것은 `hub` 가 아니라서 막힌다.
    source = _source(client, admin, vendor)
    blocked = _sync(client, admin, source["slug"], apply=True)
    assert blocked.status_code == 200, blocked.text
    run = blocked.json()["run"]
    assert run["status"] == "failed", run
    joined = " ".join(run["errors"])
    # 「허브에서 고쳐라」 로 끝나면 운영자는 이 화면에서 할 일을 모른다 — 적을 이름을 말한다.
    assert "hub" in joined and source["slug"] in joined, run["errors"]
    assert "출처 이름" in joined, run["errors"]
    # **바깥 표를 부르기도 전에** 끝낸다 — 읽고 나서 거절하면 그쪽 시스템을 헛되게 부른다.
    assert run["rows_seen"] == 0, run

    # 2) 허브가 적은 이름을 적으면 그 소스의 적재만 통과한다.
    fixed = client.patch(
        f"/api/datasources/{source['slug']}",
        json={"source_name": "hub"},
        headers=admin.headers,
    )
    assert fixed.status_code == 200 and fixed.json()["source_name"] == "hub"
    done = _sync(client, admin, source["slug"], apply=True)
    assert done.status_code == 200, done.text
    assert done.json()["run"]["status"] == "ok", done.json()["run"]
    items = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    assert {one["key"] for one in items} == {"V-001", "V-002", "V-003"}

    # 3) 화면에서 고치는 길은 **여전히 막혀 있다** — 잠금이 풀린 것이 아니다.
    target = next(one for one in items if one["key"] == "V-001")
    refused = client.patch(
        f"/api/objects/{vendor}/{target['id']}",
        json={"label": "손으로 고침"},
        headers=admin.headers,
    )
    assert refused.status_code == 409, refused.text
    assert "hub" in refused.json()["error"]["message"]


def test_출처_이름은_묶음과_같은_글자만_받는다(client: TestClient, admin: Signed) -> None:
    """묶음 가져오기의 `source` 와 **같은 규칙**이다. 두 벌로 두면 한쪽에서만 쓸 수 있는
    이름이 생기고, 그 이름은 영영 `managed_by` 와 안 맞는다."""
    vendor = _vendor_type(client, admin)
    bad = client.post(
        "/api/datasources",
        json={
            "slug": f"plm_{uuid.uuid4().hex[:6]}",
            "name": "x",
            "base_url": "http://a/b",
            "entity_set": "S",
            "type_slug": vendor,
            "source_name": "Hub 플랫폼",
            "mapping": {"external_key": "VendorNo"},
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422, bad.text


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
    planned = _sync(client, admin, source["slug"])
    assert planned.status_code == 200, planned.text
    assert planned.json()["applied"] is False
    assert planned.json()["counts"] == {"create": 3, "update": 0, "unchanged": 0, "error": 0}
    assert client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["total"] == 0
    assert len(plm.requests) >= 2

    # 적용.
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True, done
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    by_key = {one["key"]: one for one in listed}
    assert by_key["V-001"]["label"] == "ANSYS Inc." and by_key["V-001"]["aliases"] == ["Ansys"]
    assert by_key["V-001"]["properties"] == {"country": "미국", "rating": 92}
    assert by_key["V-001"]["external_ids"] == {source["slug"]: "V-001"}

    # 바깥에서 이름이 바뀌고 우리 쪽 이름을 사람이 고쳐도 — 외부 식별자로 같은 객체를 찾는다.
    client.patch(
        f"/api/objects/{vendor}/{by_key['V-001']['id']}",
        json={"label": "Ansys(우리 이름)"},
        headers=admin.headers,
    )
    plm.rows[0]["Name"] = "ANSYS, Inc."
    plm.rows[0]["Rating"] = 95
    again = _sync(client, admin, source["slug"], apply=True).json()
    assert again["counts"]["update"] == 1 and again["counts"]["create"] == 0
    changed = client.get(
        f"/api/objects/{vendor}/{by_key['V-001']['id']}", headers=admin.headers
    ).json()
    # **사람이 화면에서 고친 이름은 동기화가 되돌리지 않는다**(2026-10-02 · 계획 ⑮).
    # 밤마다 도는 동기화가 사람의 수정을 지우면 그 사람은 다음부터 안 고친다. 칸 단위라
    # 사람이 안 건드린 Rating 은 그대로 따라온다 — 객체가 통째로 잠기는 것이 아니다.
    assert (
        changed["object"]["label"] == "Ansys(우리 이름)"
        and changed["object"]["properties"]["rating"] == 95
    )
    assert client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["total"] == 3

    runs = client.get(f"/api/datasources/{source['slug']}/runs", headers=admin.headers).json()
    # **셋이 남았나** — 계획 하나와 적용 둘. 차례는 여기서 따지지 않는다: 목록의 순서는
    # `started_at desc, id desc` 로 못 박혀 있고(라우터), 실행 행이 표에 박히는 시각은
    # 이 시험이 한 세션을 함께 쓰는 탓에 요청 차례와 어긋날 수 있다 — 그것을 순서로 재면
    # 간헐로 실패하고, 그 실패는 플랫폼의 문제가 아니다.
    statuses = [r["status"] for r in runs][:3]
    assert sorted(statuses) == ["ok", "ok", "planned"], [
        (r["status"], r["started_at"], r.get("applied")) for r in runs[:4]
    ]
    assert [r["applied"] for r in runs[:3]].count(True) == 2


def test_값_대응표에_없는_값은_오류_행이고_아무것도_안_넣는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    plm.rows[2]["CountryCd"] = "JP"
    done = _sync(client, admin, source["slug"], apply=True).json()
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
    plan = _sync(client, admin, source["slug"], apply=True).json()
    assert plan["applied"] is True, plan
    got = client.get(f"/api/objects/{vendor}/{ours['id']}", headers=admin.headers).json()[
        "object"
    ]
    assert got["key"] == "V-001" and got["properties"]["country"] == "미국"
    assert list(got["external_ids"].values()) == ["V-001"]


def test_이름으로_찾을_때_이미_다른_항목과_이어진_객체는_후보가_아니다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """옛 이름이 별칭으로 남은 객체에 **같은 이름의 새 항목**이 붙으면, 바깥의 두 항목이 우리
    객체 하나로 겹친다 — 그 뒤로 그 객체가 두 항목의 값을 번갈아 받는다. 이름 · 별칭으로
    붙이는 것은 사람이 먼저 만든 것과 합류하려는 것이지, 이 소스의 다른 항목을 덮는 것이
    아니다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    assert _sync(client, admin, source["slug"], apply=True).json()["applied"] is True
    # V-001 「ANSYS Inc.」 는 「Ansys」 를 별칭으로 가졌다(Short 칸). 바깥에 그 이름의 새 항목.
    plm.rows.append(
        {"VendorNo": "V-009", "Name": "Ansys", "Short": "", "CountryCd": "KR", "Rating": 50}
    )
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True, done
    assert done["run"]["counts"]["create"] == 1, done["run"]["counts"]

    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    by_key = {one["key"]: one for one in listed}
    assert by_key["V-009"]["label"] == "Ansys"
    assert by_key["V-001"]["label"] == "ANSYS Inc."
    old = client.get(f"/api/objects/{vendor}/{by_key['V-001']['id']}", headers=admin.headers)
    assert list(old.json()["object"]["external_ids"].values()) == ["V-001"]


def test_이름이_같은_새_항목_둘은_한_객체를_나눠_갖지_않는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """사람이 만든 「Hexagon」 하나에 바깥의 새 항목 둘이 같은 이름으로 오면 — 앞의 것이
    합류하고 뒤의 것은 새로 선다. 예전에는 둘 다 같은 객체를 가리켜 실행 전체가 막혔다."""
    vendor = _vendor_type(client, admin)
    ours = _make_object(client, admin, vendor, label="Hexagon")
    plm.rows = [
        {"VendorNo": "V-010", "Name": "Hexagon", "Short": "", "CountryCd": "KR", "Rating": 1},
        {"VendorNo": "V-011", "Name": "Hexagon", "Short": "", "CountryCd": "KR", "Rating": 2},
    ]
    source = _source(client, admin, vendor)
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True, done

    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    by_key = {one["key"]: one for one in listed}
    assert by_key["V-010"]["id"] == ours["id"]
    assert by_key["V-011"]["id"] != ours["id"]


def test_사라진_행은_기본_그대로_켜면_사용_중지(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    manual = _make_object(client, admin, vendor, label="손으로 만든 것")
    _sync(client, admin, source["slug"], apply=True)
    plm.rows.pop()  # 마이다스가 바깥에서 사라짐

    _sync(client, admin, source["slug"], apply=True)
    rows = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    assert all(one["status"] == "active" for one in rows)  # 기본: 건드리지 않는다

    client.patch(
        f"/api/datasources/{source['slug']}",
        json={"deprecate_missing": True},
        headers=admin.headers,
    )
    done = _sync(client, admin, source["slug"], apply=True).json()
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
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True, done
    assert done["counts"]["create"] == 2

    # 비밀이 틀리면 연결 오류가 기록에 남는다 — 무엇이 실패했는지 말하며.
    client.patch(
        f"/api/datasources/{source['slug']}",
        json={"auth_secret": "bad"},
        headers=admin.headers,
    )
    failed = _sync(client, admin, source["slug"]).json()
    assert failed["run"]["status"] == "failed" and "인증" in failed["errors"][0]


def test_nextLink_없는_서버는_skip_으로_넘긴다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, base_url="http://plm.local/plain")
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["run"]["rows_seen"] == 3 and done["counts"]["create"] == 3
    skips = [parse_qs(r.url.query.decode()).get("$skip", ["0"])[0] for r in plm.requests]
    assert skips == ["0", "2"]  # 2행씩: 꽉 찬 첫 쪽 → 다음, 덜 찬 둘째 쪽 → 끝


def test_바깥을_정본으로_켜면_비운_칸을_비우고_뺀_별칭을_뺀다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """기본은 「빈 칸은 안 건드림」 — 바깥 표에는 우리가 안 채운 칸이 흔해, 빈 칸으로 우리 값을
    지우면 안 되는 곳이 많다. 바깥이 정본인 표는 켠다(`options.mirror`) — 그러면 바깥에서 지운
    값 · 뺀 별칭이 이쪽에서도 빠진다."""
    vendor = _vendor_type(client, admin)
    plain = _source(client, admin, vendor)
    assert _sync(client, admin, plain["slug"], apply=True).json()["applied"] is True
    mirror = _source(client, admin, vendor, options={"mirror": True})

    def ansys() -> dict[str, Any]:
        listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
        one = next(row for row in listed if row["key"] == "V-001")
        got = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
        return dict(got["object"])

    assert ansys()["properties"]["rating"] == 92 and ansys()["aliases"] == ["Ansys"]

    # 바깥에서 점수와 줄임 이름을 지웠다.
    plm.rows[0]["Rating"] = None
    plm.rows[0]["Short"] = ""
    # 기본 소스는 그대로 둔다.
    assert _sync(client, admin, plain["slug"], apply=True).json()["applied"] is True
    assert ansys()["properties"]["rating"] == 92 and ansys()["aliases"] == ["Ansys"]
    # 정본으로 켠 소스는 비운다.
    done = _sync(client, admin, mirror["slug"], apply=True).json()
    assert done["applied"] is True, done
    got = ansys()
    assert "rating" not in got["properties"], got["properties"]
    assert got["aliases"] == []
    assert got["properties"]["country"] == "미국"


def test_nextLink_로_넘기던_서버의_마지막_쪽이_꽉_차도_두_번_받지_않는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """행 수가 쪽 크기의 배수면 마지막 쪽이 꽉 찬다. 서버가 nextLink 로 넘기던 중이면 nextLink
    가 없는 쪽이 끝이다 — 꽉 찼다고 `$skip` 으로 더 물으면 이미 받은 쪽을 또 받아, 같은 id 두
    번으로 동기화 전체가 실패했다."""
    plm.rows.append(
        {"VendorNo": "V-004", "Name": "Hexagon", "Short": "", "CountryCd": "KR", "Rating": 1}
    )
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)  # 쪽 크기 2 · 행 4
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True, done
    assert done["run"]["rows_seen"] == 4 and done["run"]["counts"]["create"] == 4
    assert len(plm.requests) == 2  # 쪽 둘 — 다시 묻지 않는다


def test_타이머가_돌릴_차례(client: TestClient, admin: Signed, plm: FakeOData) -> None:
    from app.database import SessionLocal

    vendor = _vendor_type(client, admin)
    every = _source(client, admin, vendor, interval_minutes=60)
    manual = _source(client, admin, vendor, interval_minutes=0)
    with SessionLocal() as db:
        slugs = {one.slug for one in services.due(db)}
    assert every["slug"] in slugs and manual["slug"] not in slugs
    _sync(client, admin, every["slug"], apply=True)
    with SessionLocal() as db:
        assert every["slug"] not in {one.slug for one in services.due(db)}
    json.dumps(ROWS)  # 자료가 JSON 으로 나가는 모양인지 — 가짜 서버가 그대로 쓴다


# --- REST 와 파일 ---------------------------------------------------------------


class FakeRest:
    """REST 흉내 — 쪽 넘김 세 방식(page·offset·cursor)과 헤더 인증."""

    def __init__(self) -> None:
        self.rows = [dict(one) for one in ROWS]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("X-API-Key") != "k3y":
            return httpx.Response(401, json={"error": "no key"})
        query = parse_qs(request.url.query.decode())
        size = int(query.get("limit", query.get("page_size", ["100"]))[0])
        path = request.url.path
        if path.endswith("/page"):
            page = int(query.get("page", ["1"])[0])
            start = (page - 1) * size
            return httpx.Response(
                200, json={"data": {"items": self.rows[start : start + size]}}
            )
        if path.endswith("/offset"):
            start = int(query.get("offset", ["0"])[0])
            return httpx.Response(200, json={"items": self.rows[start : start + size]})
        if path.endswith("/cursor"):
            start = int(query.get("cursor", ["0"])[0])
            body: dict[str, Any] = {"items": self.rows[start : start + size], "meta": {}}
            if start + size < len(self.rows):
                body["meta"]["next"] = str(start + size)
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=self.rows)


@pytest.fixture
def rest() -> Iterator[FakeRest]:
    fake = FakeRest()
    services.transport = httpx.MockTransport(fake)
    try:
        yield fake
    finally:
        services.transport = None


def _rest_source(
    client: TestClient, admin: Signed, vendor: str, path: str, options: dict[str, Any]
) -> dict[str, Any]:
    return _source(
        client,
        admin,
        vendor,
        kind="rest",
        base_url="http://api.local/v1",
        entity_set=path,
        options=options,
        auth_kind="header",
        auth_user="X-API-Key",
        auth_secret="k3y",
    )


@pytest.mark.parametrize(
    ("path", "options"),
    [
        ("suppliers", {"rows_path": ""}),
        (
            "suppliers/page",
            {"rows_path": "data.items", "paging": "page", "size_param": "limit"},
        ),
        (
            "suppliers/offset",
            {"rows_path": "items", "paging": "offset", "size_param": "limit"},
        ),
        (
            "suppliers/cursor",
            {
                "rows_path": "items",
                "paging": "cursor",
                "cursor_path": "meta.next",
                "size_param": "limit",
            },
        ),
    ],
)
def test_REST_는_행_자리와_쪽_넘김을_정의가_적는다(
    client: TestClient, admin: Signed, rest: FakeRest, path: str, options: dict[str, Any]
) -> None:
    vendor = _vendor_type(client, admin)
    source = _rest_source(client, admin, vendor, path, options)
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True, done
    assert done["run"]["rows_seen"] == 3 and done["counts"]["create"] == 3
    assert rest.requests[0].headers["X-API-Key"] == "k3y"


def test_REST_행_자리가_틀리면_무엇을_적어야_하는지_말한다(
    client: TestClient, admin: Signed, rest: FakeRest
) -> None:
    vendor = _vendor_type(client, admin)
    source = _rest_source(client, admin, vendor, "suppliers/offset", {"rows_path": "wrong"})
    failed = _sync(client, admin, source["slug"]).json()
    assert failed["run"]["status"] == "failed" and "rows_path" in failed["errors"][0]


def test_파일_CSV_와_Excel_과_JSON(
    client: TestClient, admin: Signed, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csv
    import io

    from openpyxl import Workbook

    from app.config import get_settings

    # 허용 폴더 아래의 파일만 읽는다.
    monkeypatch.setattr(get_settings(), "datasource_dir", tmp_path)
    (tmp_path / "erp").mkdir()
    header = ["VendorNo", "Name", "Short", "CountryCd", "Rating"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    writer.writerows(ROWS)
    (tmp_path / "erp" / "suppliers.csv").write_text("﻿" + buffer.getvalue(), encoding="utf-8")
    book = Workbook()
    sheet = book.active
    sheet.title = "Suppliers"
    sheet.append(header)
    for one in ROWS:
        sheet.append([one[name] for name in header])
    book.save(tmp_path / "erp" / "suppliers.xlsx")
    (tmp_path / "erp" / "suppliers.json").write_text(
        json.dumps({"rows": ROWS}, ensure_ascii=False), encoding="utf-8"
    )

    vendor = _vendor_type(client, admin)
    for name, options in (
        ("suppliers.csv", {}),
        ("suppliers.xlsx", {"sheet": "Suppliers"}),
        ("suppliers.json", {}),
    ):
        source = _source(
            client,
            admin,
            vendor,
            kind="file",
            base_url="",
            entity_set=f"erp/{name}",
            options=options,
        )
        done = _sync(client, admin, source["slug"], apply=True).json()
        assert done["applied"] is True, (name, done)
        assert done["run"]["rows_seen"] == 3
    # 같은 세 행이 세 소스에서 왔으니 객체는 셋뿐이다 — 별칭·이름으로 합류했다.
    assert client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["total"] == 3

    # 폴더 밖은 안 읽는다.
    outside = _source(
        client,
        admin,
        vendor,
        kind="file",
        base_url="",
        entity_set="../../etc/passwd",
        options={"format": "csv"},
    )
    failed = _sync(client, admin, outside["slug"]).json()
    assert failed["run"]["status"] == "failed" and "아래에서만" in failed["errors"][0]


def test_파일_폴더가_안_정해졌으면_URL_로만(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    vendor = _vendor_type(client, admin)
    local = _source(client, admin, vendor, kind="file", base_url="", entity_set="x.csv")
    failed = _sync(client, admin, local["slug"]).json()
    assert failed["run"]["status"] == "failed" and "DATASOURCE_DIR" in failed["errors"][0]


def test_파일을_URL_로_받는다(client: TestClient, admin: Signed, plm: FakeOData) -> None:
    vendor = _vendor_type(client, admin)
    source = _source(
        client,
        admin,
        vendor,
        kind="file",
        base_url="",
        entity_set="http://plm.local/export.csv",
    )
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True and done["counts"]["create"] == 3


# --- 조용히 멎는 것 ------------------------------------------------------------


def test_동기화가_실패하면_홈과_종이_말한다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """**실패가 표에만 적히면 아무도 모른다** — 잘 도는 동안에는 그 화면을 안 연다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    slug = source["slug"]

    before = len(notifications_of(client, admin, "datasource.failed"))
    # 시험 DB 는 스위트가 함께 쓴다 — 남이 남긴 실패가 이미 있을 수 있어 **차이로 본다.**
    quiet = maintenance_counts(client, admin).get("datasource_failed", 0)
    plm.auth_required = "Bearer 없는것"  # 서버가 401 을 낸다
    failed = _sync(client, admin, slug, apply=True)
    assert failed.status_code == 200, failed.text
    assert failed.json()["run"]["status"] == "failed"

    assert maintenance_counts(client, admin)["datasource_failed"] == quiet + 1
    after = notifications_of(client, admin, "datasource.failed")
    assert len(after) == before + 1
    assert "PLM 공급사" in after[0]["title"]

    # **같은 실패를 두 번 알리지 않는다.** 타이머가 5분마다 돌면 하루에 288개가 쌓이고,
    # 그러면 사람은 이 종류를 통째로 안 읽게 된다.
    _sync(client, admin, slug, apply=True)
    assert len(notifications_of(client, admin, "datasource.failed")) == before + 1

    # 복구도 알린다 — 안 알리면 사람이 손으로 확인하러 간다.
    plm.auth_required = None
    done = _sync(client, admin, slug, apply=True)
    assert done.json()["run"]["status"] == "ok", done.text
    assert notifications_of(client, admin, "datasource.recovered")
    assert maintenance_counts(client, admin).get("datasource_failed", 0) == quiet


def test_멎은_것은_시스템_관리자에게만_뜬다(
    client: TestClient, member: Signed, admin: Signed, plm: FakeOData
) -> None:
    """못 고치는 사람에게 띄우면 그 줄은 못 지우는 숫자가 된다."""
    vendor = _vendor_type(client, admin)
    slug = _source(client, admin, vendor)["slug"]
    plm.auth_required = "Bearer 없는것"
    _sync(client, admin, slug, apply=True)

    assert maintenance_counts(client, admin).get("datasource_failed", 0) >= 1
    assert "datasource_failed" not in maintenance_counts(client, member)


def test_한_행이_선_하나인_원천도_받는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """**객체만 받으면 점만 있고 선이 없다.**

    BOM · 매핑 표처럼 한 행이 선 하나인 원천이 사내에 많다. 그것을 객체 소스로 받으면 사람이
    같은 표를 두 번 읽어 관계 파일을 따로 만들어야 했다. 관계 대응(`mapping.relations`)을
    적으면 같은 길(검증 · 권한 · 감사)로 선이 들어온다 — 말은 정제 도구의 관계 파일과 같다.
    """
    vendor = _vendor_type(client, admin)
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
                    "properties": [{"key": "share", "label": "지분", "data_type": "number"}],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text

    # 양 끝이 될 객체를 먼저 넣는다 — 선은 양 끝이 있어야 선다.
    for key, label in (("V-001", "ANSYS Inc."), ("V-002", "Altair Engineering")):
        _make_object(client, admin, vendor, label=label, key=key)

    # 원천의 한 행이 선 하나다(출발 · 도착 · 지분).
    plm.rows = [
        {"VendorNo": "V-001", "Name": "V-002", "Short": "", "CountryCd": "", "Rating": 30}
    ]
    source = _source(
        client,
        admin,
        vendor,
        mapping={
            "relations": {
                "relation": kind,
                "src": {"column": "VendorNo"},
                "dst": {"column": "Name"},
                "evidence_note": {"value": "PLM 협력사 표"},
                "properties": {"share": {"column": "Rating"}},
            }
        },
    )

    planned = _sync(client, admin, source["slug"]).json()
    assert planned["counts"]["relations_create"] == 1, planned["counts"]
    # 계획은 아무것도 안 바꾼다.
    listed = client.get(f"/api/objects/{vendor}", headers=admin.headers).json()["items"]
    one = next(row for row in listed if row["key"] == "V-001")
    detail = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
    assert detail["related"] == []

    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["counts"]["relations_create"] == 1, done["counts"]
    detail = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers).json()
    edge = next(row for row in detail["related"] if row["relation"] == kind)
    assert edge["evidence_note"] == "PLM 협력사 표"
    assert edge["properties"] == {"share": 30}

    # 두 번 받아도 두 겹이 안 된다.
    again = _sync(client, admin, source["slug"], apply=True).json()
    assert again["counts"]["relations_create"] == 0, again["counts"]
    assert again["counts"]["relations_unchanged"] == 1, again["counts"]


def test_선_소스의_맞춤은_그_출발점_범위만_끊는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """`mode: replace` 는 **온 목록에 나온 (출발 객체 · 관계 종류)** 범위에서만 끊는다 —
    표에 아예 안 나온 객체의 선을 끊으면, 일부만 담은 표가 나머지 전부를 지운다."""
    vendor = _vendor_type(client, admin)
    kind = f"partner_{uuid.uuid4().hex[:6]}"
    client.post(
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
    rows = {}
    for key, label in (("V-001", "ANSYS"), ("V-002", "Altair"), ("V-003", "마이다스")):
        rows[key] = _make_object(client, admin, vendor, label=label, key=key)

    plm.rows = [
        {"VendorNo": "V-001", "Name": "V-002", "Short": "", "CountryCd": "", "Rating": 0},
        {"VendorNo": "V-003", "Name": "V-002", "Short": "", "CountryCd": "", "Rating": 0},
    ]
    source = _source(
        client,
        admin,
        vendor,
        mapping={
            "relations": {
                "relation": kind,
                "src": {"column": "VendorNo"},
                "dst": {"column": "Name"},
                "mode": "replace",
            }
        },
    )
    assert (
        _sync(client, admin, source["slug"], apply=True).json()["counts"]["relations_create"]
        == 2
    )

    # 원천에서 V-001 의 상대가 바뀌었다 — 그 출발점의 옛 선은 끊기고, V-003 것은 그대로.
    plm.rows = [
        {"VendorNo": "V-001", "Name": "V-003", "Short": "", "CountryCd": "", "Rating": 0},
        {"VendorNo": "V-003", "Name": "V-002", "Short": "", "CountryCd": "", "Rating": 0},
    ]
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["counts"]["relations_unlink"] == 1, done["counts"]

    first = client.get(
        f"/api/objects/{vendor}/{rows['V-001']['id']}", headers=admin.headers
    ).json()
    assert [one["object_label"] for one in first["related"] if one["relation"] == kind] == [
        "마이다스"
    ]
    third = client.get(
        f"/api/objects/{vendor}/{rows['V-003']['id']}", headers=admin.headers
    ).json()
    assert any(one["relation"] == kind for one in third["related"]), "안 나온 출발점은 그대로"


# --- 차례 — 가리키는 쪽부터 한 줄로 ----------------------------------------------------


def _bare(**kw: Any) -> dict[str, Any]:
    """식별자 · 이름만 받는 대응 — 타입마다 속성이 달라도 같은 가짜 서버를 읽는다."""
    return {
        "mapping": {
            "external_key": "VendorNo",
            "columns": [
                {"source": "VendorNo", "target": "key"},
                {"source": "Name", "target": "label"},
            ],
        },
        **kw,
    }


def test_차례는_가리키는_쪽부터_한_줄로_돌고_하나가_실패해도_다음으로_간다(
    client: TestClient, admin: Signed, db: Session, plm: FakeOData
) -> None:
    """소스마다 작업을 따로 넣으면 순서가 없다 — 「고장 모드」 가 「메커니즘」 보다 먼저 돌면
    그것을 가리키는 칸 · 선이 끝점을 못 찾았다. 타이머는 이제 차례 하나를 넣고, 그 작업이
    가리키는 쪽부터 돌린다. 한 소스가 실패해도 차례는 끝까지 간다."""
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    mechanism = _make_type(client, admin, label="메커니즘", key_policy="optional")
    mode = _make_type(client, admin, label="고장 모드", key_policy="optional")
    _make_property(
        client,
        admin,
        mode,
        key="mechanism",
        label="메커니즘",
        data_type="object_ref",
        ref_type_slug=mechanism,
    )
    # 이름 순이면 고장 모드가 앞이다 — 가리키는 쪽(메커니즘)이 먼저 돌아야 한다.
    tag = uuid.uuid4().hex[:6]
    first = _source(client, admin, mode, **_bare(slug=f"a_mode_{tag}"))
    later = _source(client, admin, mechanism, **_bare(slug=f"z_mech_{tag}"))
    broken = _source(
        client, admin, mechanism, **_bare(slug=f"m_file_{tag}", kind="file", base_url="")
    )

    job = job_services.enqueue(
        db,
        kind="datasource_sync_round",
        params={"slugs": [first["slug"], later["slug"], broken["slug"]], "apply": True},
        user=None,  # 타이머가 넣는다
        workspace_id=None,
    )
    db.commit()
    while job_services.process_one("test-worker"):
        db.expire_all()
        if db.scalar(select(Job.status).where(Job.id == job.id)) in ("done", "failed"):
            break
    db.expire_all()
    done = db.scalar(select(Job).where(Job.id == job.id))
    assert done is not None and done.status == "done", done.error if done else None
    result = done.result or {}
    order = result["order"]
    assert order.index(later["slug"]) < order.index(first["slug"]), order
    statuses = {one["slug"]: one["status"] for one in result["sources"]}
    assert statuses == {first["slug"]: "ok", later["slug"]: "ok", broken["slug"]: "failed"}
    assert result["failed"] == 1


def test_순서는_참조_칸_선_선만_받는_소스를_보고_순환은_이름_순으로_끊는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    from app.modules.datasources.models import DataSource
    from app.modules.ontology.models import ObjectType

    def type_id(slug: str) -> uuid.UUID:
        found = db.scalar(select(ObjectType.id).where(ObjectType.slug == slug))
        assert found is not None
        return uuid.UUID(str(found))

    mechanism = _make_type(client, admin, label="메커니즘", key_policy="optional")
    mode = _make_type(client, admin, label="고장 모드", key_policy="optional")
    group = _make_type(client, admin, label="시험군", key_policy="optional")
    kind = f"cause_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "원인",
                    "src_type_slugs": [mode],
                    "dst_type_slugs": [mechanism],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    _make_property(
        client,
        admin,
        group,
        key="mode",
        label="고장 모드",
        data_type="object_ref",
        ref_type_slug=mode,
    )

    def source(slug: str, of: str, **kw: Any) -> DataSource:
        body: dict[str, Any] = {"kind": "odata", "options": {}, "mapping": {}, **kw}
        return DataSource(slug=slug, type_id=type_id(of), **body)

    # 선을 받는 고장 모드 소스는 메커니즘 뒤, 그것을 가리키는 시험군은 고장 모드 뒤.
    mode_src = source("a_mode", mode, kind="sp_core", options={"relations": True})
    group_src = source("b_group", group)
    mech_src = source("c_mech", mechanism)
    # 선만 받는 소스는 출발점(고장 모드)의 객체 소스 뒤.
    edges = source(
        "a_edges", mode, mapping={"relations": {"src": "s", "relation": kind, "dst": "d"}}
    )
    got = services.sync_order(db, [group_src, edges, mode_src, mech_src])
    assert [one.slug for one in got] == ["c_mech", "a_mode", "a_edges", "b_group"]

    # 선을 안 받는 고장 모드 소스는 메커니즘을 기다리지 않는다 — 이름 순.
    plain = source("a_mode", mode)
    assert [one.slug for one in services.sync_order(db, [mech_src, plain])] == [
        "a_mode",
        "c_mech",
    ]

    # 서로 가리키면 — 둘 다 돌고, 이름 순으로 끊는다.
    _make_property(
        client,
        admin,
        mechanism,
        key="mode",
        label="고장 모드",
        data_type="object_ref",
        ref_type_slug=mode,
    )
    _make_property(
        client,
        admin,
        mode,
        key="mechanism",
        label="메커니즘",
        data_type="object_ref",
        ref_type_slug=mechanism,
    )
    assert [one.slug for one in services.sync_order(db, [mech_src, plain])] == [
        "a_mode",
        "c_mech",
    ]


# --- 사람이 합친 객체 · 사라졌다 다시 온 것 · 한꺼번에 빈 응답 ------------------------


def _by_key(client: TestClient, admin: Signed, vendor: str) -> dict[str, dict[str, Any]]:
    listed = client.get(f"/api/objects/{vendor}?limit=200", headers=admin.headers).json()
    return {one["key"]: one for one in listed["items"]}


def _saved(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    return dict(client.get(f"/api/datasources/{slug}", headers=admin.headers).json())


def test_같은_소스에서_온_둘을_사람이_합쳐도_동기화가_멈추지_않는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """합치기는 진 쪽의 외부 식별자를 이긴 쪽으로 옮긴다 — 이긴 쪽이 이 소스의 식별자를 둘
    갖는다. 예전에는 다음 동기화가 바깥 두 행에 같은 `id` 를 박아 일괄 입력이 「같은 id 가
    N행에도 있습니다」 로 **전부** 거절했다 — 그 소스가 영영 멈췄다(2026-10-08). 사람의
    합치기가 이긴다: 값은 한 행에서만, 나머지 행은 「합친 객체」 로 건너뛴다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    assert _sync(client, admin, source["slug"], apply=True).json()["applied"] is True
    before = _by_key(client, admin, vendor)
    merged = client.post(
        f"/api/objects/{vendor}/{before['V-002']['id']}/merge",
        json={"into": before["V-001"]["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text

    plm.rows[1]["Rating"] = 81  # 진 쪽의 행이 바뀌어도 값은 한 행(V-001)에서만 받는다
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["applied"] is True and done["run"]["status"] == "ok", done
    skipped = [one for one in done["rows"] if "합친 객체" in (one["message"] or "")]
    assert [one["label"] for one in skipped] == ["V-002"], done["rows"]
    after = _by_key(client, admin, vendor)
    assert sorted(after) == ["V-001", "V-003"]
    assert after["V-001"]["properties"]["rating"] == 92

    # 두 외부 식별자가 다 남아 다음에도 그 객체를 찾는다 — 새 객체가 안 생긴다.
    again = _sync(client, admin, source["slug"], apply=True).json()
    assert again["run"]["status"] == "ok" and again["counts"]["create"] == 0, again


def test_바깥에서_사라져_중지된_것이_다시_오면_되살리고_사람이_중지한_것은_그대로다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """상태 열을 대응하지 않는 소스는 다시 온 행으로 상태를 못 돌린다 — 예전에는 바깥에 다시
    올라와도 영영 중지로 남았다(2026-10-08). 되살리는 것은 **이 소스가 「사라져」 중지시킨
    것**뿐이다 — 사람이 중지한 것은 그 사람의 결정이 이긴다."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, deprecate_missing=True)
    _sync(client, admin, source["slug"], apply=True)
    gone = plm.rows.pop()  # 마이다스(V-003)가 바깥에서 사라짐
    assert _sync(client, admin, source["slug"], apply=True).json()["counts"]["deprecated"] == 1

    by_key = _by_key(client, admin, vendor)
    stopped = client.patch(
        f"/api/objects/{vendor}/{by_key['V-002']['id']}",
        json={"status": "deprecated"},
        headers=admin.headers,
    )
    assert stopped.status_code == 200, stopped.text

    plm.rows.append(gone)  # 다시 올라온다
    back = _sync(client, admin, source["slug"], apply=True).json()
    assert back["run"]["status"] == "ok", back["run"]
    assert back["counts"]["revived"] == 1, back["counts"]
    after = _by_key(client, admin, vendor)
    assert after["V-003"]["status"] == "active"
    assert after["V-002"]["status"] == "deprecated"
    history = client.get(
        f"/api/objects/{vendor}/{after['V-003']['id']}/history", headers=admin.headers
    ).json()
    assert any("다시 나타나" in (one["reason"] or "") for one in history)


def test_바깥이_한꺼번에_비면_사용_중지하지_않고_멈춘다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """필터 · 권한이 바뀌어 바깥이 한 번 빈 응답을 주면, 「이번에 안 온 것을 중지」 가 이
    소스가 넣은 것 전부를 중지시켰다 — RA 보고서의 「원본에서 내려감」 과 같은 안전장치가
    없었다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    plm.rows = [
        {"VendorNo": f"V-{n:03}", "Name": f"공급사 {n}", "Short": "", "CountryCd": "KR"}
        for n in range(1, 13)
    ]
    source = _source(client, admin, vendor, **_bare(deprecate_missing=True))
    assert _sync(client, admin, source["slug"], apply=True).json()["counts"]["create"] == 12

    plm.rows = []
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["run"]["status"] == "failed", done["run"]
    assert done["counts"]["gone_held_back"] == 12 and done["counts"]["deprecated"] == 0
    assert any("절반이 넘어" in one for one in done["errors"])
    assert {one["status"] for one in _by_key(client, admin, vendor).values()} == {"active"}


def test_바깥_식별자가_바뀐_행은_같은_객체를_고치고_사용_중지하지_않는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """우리 식별자로 다시 찾은 행은 외부 식별자를 갈아 끼운다. 예전에는 그 변경이 아직
    flush 되지 않은 채(`autoflush=False`) 「이번에 안 온 것」 을 옛 값으로 골라, 방금 고친
    객체를 사용 중지했다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    for one in plm.rows:
        one["ExtId"] = f"E-{one['VendorNo']}"
    mapping = {
        "external_key": "ExtId",
        "columns": [
            {"source": "VendorNo", "target": "key"},
            {"source": "Name", "target": "label"},
        ],
    }
    source = _source(client, admin, vendor, mapping=mapping, deprecate_missing=True)
    _sync(client, admin, source["slug"], apply=True)

    plm.rows[0]["ExtId"] = "E-NEW-001"  # 바깥이 식별자를 바꿨다(우리 식별자는 그대로)
    done = _sync(client, admin, source["slug"], apply=True).json()
    assert done["counts"]["deprecated"] == 0 and done["counts"]["create"] == 0, done
    after = _by_key(client, admin, vendor)
    assert after["V-001"]["status"] == "active"
    assert after["V-001"]["external_ids"] == {source["slug"]: "E-NEW-001"}


def test_이름으로_찾을_때_다른_소스의_외부_식별자는_이름이_아니다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """예전에는 이름 색인에 모든 별칭을 넣어, 바깥 행의 이름이 다른 소스의 식별자 글자와
    같으면 그 소스의 객체에 붙었다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    _sync(client, admin, _source(client, admin, vendor)["slug"], apply=True)
    other = _source(
        client,
        admin,
        vendor,
        mapping={
            "external_key": "Name",
            "columns": [{"source": "VendorNo", "target": "label"}],
        },
    )
    done = _sync(client, admin, other["slug"], apply=True).json()
    assert done["counts"]["create"] == 3 and done["counts"]["update"] == 0, done


# --- 실패는 늘 기록에 남는다 ----------------------------------------------------------------


def test_적용_도중_예외가_나도_기록과_상태가_남고_외부_식별자는_조각과_함께_들어간다(
    client: TestClient, admin: Signed, plm: FakeOData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """조각은 일괄 입력이 커밋하는데 실행 행은 flush 만 했었다 — 그 뒤 예외가 나면 객체는
    들어가고 외부 식별자 · 사용 중지 · `last_status` · 알림은 사라졌고, 기록은 「계획 ·
    미완료」 였다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    before = len(notifications_of(client, admin, "datasource.failed"))

    def boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("사용 중지 단계에서 터짐")

    monkeypatch.setattr(services, "_bury", boom)
    done = _sync(client, admin, source["slug"], apply=True)
    assert done.status_code == 200, done.text
    run = done.json()["run"]
    assert run["status"] == "failed" and run["applied"] is True, run
    assert any("도중에 멈췄습니다" in one and "터짐" in one for one in run["errors"]), run
    by_key = _by_key(client, admin, vendor)
    assert {key: one["external_ids"] for key, one in by_key.items()} == {
        key: {source["slug"]: key} for key in ("V-001", "V-002", "V-003")
    }
    assert _saved(client, admin, source["slug"])["last_status"] == "failed"
    assert len(notifications_of(client, admin, "datasource.failed")) == before + 1

    monkeypatch.undo()
    again = _sync(client, admin, source["slug"], apply=True).json()
    assert again["run"]["status"] == "ok" and again["counts"]["create"] == 0, again


def test_대응이_틀려_시작도_못_해도_기록과_상태가_남는다(
    client: TestClient, admin: Signed, plm: FakeOData, db: Session
) -> None:
    """대응 · 타입 · 대신 돌릴 관리자는 실행 기록보다 먼저 봤다 — 그 오류는 기록도 실패
    알림도 없이 작업만 실패시켰다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor)
    db.execute(
        update(DataSource)
        .where(DataSource.slug == source["slug"])
        .values(mapping={"columns": []})
    )
    db.commit()
    done = _sync(client, admin, source["slug"], apply=True)
    assert done.status_code == 200, done.text
    assert done.json()["run"]["status"] == "failed"
    assert any("external_key" in one for one in done.json()["run"]["errors"])
    runs = client.get(f"/api/datasources/{source['slug']}/runs", headers=admin.headers).json()
    assert runs[0]["status"] == "failed"
    assert _saved(client, admin, source["slug"])["last_status"] == "failed"


# --- 정의 고치기 · 겹쳐 넣기 · 차례 ---------------------------------------------------------


def test_종류만_형제_코어로_바꿔도_안_온_것_중지를_다시_본다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """보낸 칸만 검사하던 때는 「안 온 것을 중지」 를 켜 둔 채 종류만 바꿀 수 있었다 — 그러면
    두 번째 증분에서 안 바뀐 객체가 전부 사용 중지된다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    source = _source(client, admin, vendor, deprecate_missing=True)
    refused = client.patch(
        f"/api/datasources/{source['slug']}", json={"kind": "sp_core"}, headers=admin.headers
    )
    assert refused.status_code == 422, refused.text
    assert "무덤으로 알려" in refused.json()["error"]["message"]
    changed = client.patch(
        f"/api/datasources/{source['slug']}",
        json={"kind": "sp_core", "deprecate_missing": False},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text


def test_같은_소스의_적용을_겹쳐_넣지_않는다(
    client: TestClient, admin: Signed, plm: FakeOData, db: Session
) -> None:
    """화면의 「동기화」 가 줄에 있는 작업을 안 봤다 — 워커가 둘이면 둘 다 같은 새 행을
    만들었다(2026-10-08)."""
    from app.modules.jobs import services as job_services

    vendor = _vendor_type(client, admin)
    slug = _source(client, admin, vendor)["slug"]
    first = client.post(
        f"/api/datasources/{slug}/sync", params={"apply": "true"}, headers=admin.headers
    )
    again = client.post(
        f"/api/datasources/{slug}/sync", params={"apply": "true"}, headers=admin.headers
    )
    assert first.status_code == again.status_code == 202, again.text
    assert again.json()["id"] == first.json()["id"]
    finish_job(client, admin, first.json())

    # 타이머의 차례가 이 소스를 들고 있으면 그렇다고 말한다.
    round_job = job_services.enqueue(
        db,
        kind="datasource_sync_round",
        params={"slugs": [slug], "apply": True},
        user=None,
        workspace_id=None,
    )
    db.commit()
    busy = client.post(
        f"/api/datasources/{slug}/sync", params={"apply": "true"}, headers=admin.headers
    )
    assert busy.status_code == 409, busy.text
    assert "차례" in busy.json()["error"]["message"]
    work_until(db, round_job.id)


def test_차례는_타이머의_간격만큼_봐준다(
    client: TestClient, admin: Signed, plm: FakeOData, db: Session
) -> None:
    """`last_run_at` 은 끝난 때라 여유 없이 견주면 차례가 늘 다음 깸으로 밀렸다 — 5분 소스가
    실제로는 10분마다 돌았다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    slug = _source(client, admin, vendor, interval_minutes=60)["slug"]
    now = datetime.now(UTC)

    def ran(minutes_ago: int) -> set[str]:
        db.execute(
            update(DataSource)
            .where(DataSource.slug == slug)
            .values(last_run_at=now - timedelta(minutes=minutes_ago))
        )
        db.commit()
        return {one.slug for one in services.due(db, now=now)}

    assert slug in ran(57)  # 타이머가 3분 뒤에 깨면 63분이 된다 — 지금 돌린다
    assert slug not in ran(50)


def test_한_번도_안_돈_소스도_멎음에_뜬다(
    client: TestClient, admin: Signed, plm: FakeOData, db: Session
) -> None:
    """`last_run_at` 이 없으면 빼서, 타이머 · 워커가 처음부터 없던 설치의 소스는 영영 「멎음」
    에 안 걸렸다(2026-10-08)."""
    vendor = _vendor_type(client, admin)
    slug = _source(client, admin, vendor, interval_minutes=60)["slug"]
    before = maintenance_counts(client, admin).get("datasource_stale", 0)
    db.execute(
        update(DataSource)
        .where(DataSource.slug == slug)
        .values(created_at=datetime.now(UTC) - timedelta(days=1))
    )
    db.commit()
    assert maintenance_counts(client, admin)["datasource_stale"] == before + 1


def _round(db: Session, slugs: list[str]) -> dict[str, Any]:
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    job = job_services.enqueue(
        db,
        kind="datasource_sync_round",
        params={"slugs": slugs, "apply": True},
        user=None,
        workspace_id=None,
    )
    db.commit()
    work_until(db, job.id)
    db.expire_all()
    done = db.scalar(select(Job).where(Job.id == job.id))
    assert done is not None and done.status == "done", done.error if done else None
    return dict(done.result or {})


def test_차례에서_뒤_소스가_터져도_앞_소스의_지표_작업은_남는다(
    client: TestClient,
    admin: Signed,
    plm: FakeOData,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """지표 다시 세기 작업(`_after_ingest`)은 flush 만 했다 — 다음 소스의 예외가 부른 롤백이
    앞 소스의 그 작업까지 버렸다(2026-10-08)."""
    from app.modules.jobs import kinds
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    tag = uuid.uuid4().hex[:6]
    first = _source(client, admin, _vendor_type(client, admin), **_bare(slug=f"a_one_{tag}"))
    later = _source(
        client,
        admin,
        _make_type(client, admin, label="다른 것", key_policy="optional"),
        **_bare(slug=f"b_two_{tag}"),
    )
    marker = f"marker_{tag}"

    def after_ingest(session: Session, _type_id: uuid.UUID, _reason: str) -> None:
        job_services.enqueue(
            session,
            kind="datasource_sync",
            params={"slug": marker, "apply": False},
            user=None,
            workspace_id=None,
        )

    real = services.sync

    def sync(session: Session, user: Any, source: Any, *, apply: bool) -> Any:
        if source.slug == later["slug"]:
            raise RuntimeError("뒤 소스가 터짐")
        return real(session, user, source, apply=apply)

    monkeypatch.setattr(kinds, "_after_ingest", after_ingest)
    monkeypatch.setattr(services, "sync", sync)
    result = _round(db, [first["slug"], later["slug"]])
    statuses = {one["slug"]: one["status"] for one in result["sources"]}
    assert statuses == {first["slug"]: "ok", later["slug"]: "failed"}
    kept = select(Job).where(
        Job.kind == "datasource_sync", Job.params["slug"].astext == marker
    )
    assert len(list(db.scalars(kept))) == 1
    db.execute(
        update(Job)
        .where(Job.kind == "datasource_sync", Job.params["slug"].astext == marker)
        .values(status="cancelled")
    )
    db.commit()


def test_차례는_소스를_돌기_직전에_다시_읽는다(
    client: TestClient,
    admin: Signed,
    plm: FakeOData,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """세션이 커밋에 값을 안 버려(`expire_on_commit=False`), 차례가 도는 사이 관리자가 소스를
    꺼도 처음 읽은 값으로 그대로 돌았다(2026-10-08)."""
    tag = uuid.uuid4().hex[:6]
    first = _source(client, admin, _vendor_type(client, admin), **_bare(slug=f"a_one_{tag}"))
    later = _source(
        client,
        admin,
        _make_type(client, admin, label="다른 것", key_policy="optional"),
        **_bare(slug=f"b_two_{tag}"),
    )
    real = services.sync

    def sync(session: Session, user: Any, source: Any, *, apply: bool) -> Any:
        if source.slug == first["slug"]:
            # 차례가 도는 사이 관리자가 뒤 소스를 끈다(다른 세션).
            off = client.patch(
                f"/api/datasources/{later['slug']}",
                json={"is_active": False},
                headers=admin.headers,
            )
            assert off.status_code == 200, off.text
        return real(session, user, source, apply=apply)

    monkeypatch.setattr(services, "sync", sync)
    result = _round(db, [first["slug"], later["slug"]])
    statuses = {one["slug"]: one["status"] for one in result["sources"]}
    assert statuses == {first["slug"]: "ok", later["slug"]: "skipped"}


# --- 마지막 성공 · 신선도 — 자료가 언제까지 들어왔나 ----------------------------------------


def _when(text: str | None) -> datetime | None:
    """응답의 시각 — 글자로 견주지 않는다(작업 결과는 UTC, 다시 읽은 것은 DB 의 시간대로
    온다)."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def test_마지막_성공은_받아서_적용까지_성공한_실행만_옮긴다(
    client: TestClient, admin: Signed, plm: FakeOData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`last_run_at` 은 실패해도 옮겨 가서, 사흘째 실패 중인 소스와 방금 받은 소스가 같아
    보였다. 계획 · 실패 · 일부만 들어간 실패 · 한꺼번에 사라져 멈춘 것은 「들어왔다」 가
    아니다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    plm.rows = [
        {"VendorNo": f"V-{n:03}", "Name": f"공급사 {n}", "Short": "", "CountryCd": "KR"}
        for n in range(1, 13)
    ]
    slug = _source(client, admin, vendor, **_bare(deprecate_missing=True))["slug"]

    planned = _sync(client, admin, slug).json()
    assert planned["run"]["status"] == "planned", planned["run"]
    assert _saved(client, admin, slug)["last_success_at"] is None

    done = _sync(client, admin, slug, apply=True).json()
    assert done["run"]["status"] == "ok", done["run"]
    success = _when(_saved(client, admin, slug)["last_success_at"])
    assert success is not None and success == _when(done["run"]["finished_at"])

    def unchanged(run: dict[str, Any]) -> None:
        saved = _saved(client, admin, slug)
        assert _when(saved["last_success_at"]) == success, run
        assert _when(saved["last_run_at"]) == _when(run["finished_at"]), run

    # 계획만 본 것 — `last_run_at` 도 안 옮긴다(실패일 때만).
    again = _sync(client, admin, slug).json()["run"]
    assert again["status"] == "planned"
    assert _when(_saved(client, admin, slug)["last_success_at"]) == success

    # 바깥에 못 닿았다.
    plm.auth_required = "Bearer 없는것"
    failed = _sync(client, admin, slug, apply=True).json()["run"]
    assert failed["status"] == "failed" and failed["applied"] is False, failed
    unchanged(failed)
    plm.auth_required = None

    # 조각은 들어갔는데 뒤에서 멈췄다(「그 전에 들어간 조각」) — 들어간 것이 있어도 실패다.
    def boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("사용 중지 단계에서 터짐")

    monkeypatch.setattr(services, "_bury", boom)
    crashed = _sync(client, admin, slug, apply=True).json()["run"]
    assert crashed["status"] == "failed" and crashed["applied"] is True, crashed
    unchanged(crashed)
    monkeypatch.undo()

    # 바깥이 한꺼번에 비어 멈췄다(held back) — 받은 행은 반영했어도 성공이 아니다.
    plm.rows = []
    held = _sync(client, admin, slug, apply=True).json()
    assert held["run"]["status"] == "failed" and held["counts"]["gone_held_back"] == 12, held
    unchanged(held["run"])


def test_선만_받는_소스도_적용에_성공해야_마지막_성공을_적는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """선만 받는 소스는 객체 소스와 다른 길(`_sync_body` 앞쪽)로 끝난다 — 그 길도 같은 규칙."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
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
    for key in ("V-001", "V-002"):
        _make_object(client, admin, vendor, label=key, key=key)
    plm.rows = [{"VendorNo": "V-001", "Name": "V-002", "Short": "", "CountryCd": ""}]
    slug = _source(
        client,
        admin,
        vendor,
        mapping={
            "relations": {
                "relation": kind,
                "src": {"column": "VendorNo"},
                "dst": {"column": "Name"},
            }
        },
    )["slug"]

    assert _sync(client, admin, slug).json()["run"]["status"] == "planned"
    assert _saved(client, admin, slug)["last_success_at"] is None
    done = _sync(client, admin, slug, apply=True).json()["run"]
    assert done["status"] == "ok", done
    success = _when(_saved(client, admin, slug)["last_success_at"])
    assert success == _when(done["finished_at"])

    plm.auth_required = "Bearer 없는것"
    failed = _sync(client, admin, slug, apply=True).json()["run"]
    assert failed["status"] == "failed", failed
    saved = _saved(client, admin, slug)
    assert _when(saved["last_success_at"]) == success
    assert _when(saved["last_run_at"]) == _when(failed["finished_at"])


def _aged(db: Session, slug: str, **values: Any) -> None:
    db.execute(update(DataSource).where(DataSource.slug == slug).values(**values))
    db.commit()


def test_목록_통계_상세가_그_타입에_들어오는_원천의_신선도를_싣는다(
    client: TestClient, admin: Signed, plm: FakeOData, db: Session
) -> None:
    """원천 하나가 멎어도 목록 · 통계의 숫자는 맞아 보인다 — 틀린 값이 아니라 옛 값이라서다.
    그 타입을 읽는 응답이 원천마다 마지막 성공 · 실패 중 · 멎음을 함께 싣는다. 끈 소스도
    「꺼짐」 으로 싣고(빼면 「원천이 없는 타입」 으로 읽힌다 — 2026-11-08 검토) 실패 · 멎음은
    안 세운다. 순서는 이름."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    tag = uuid.uuid4().hex[:6]
    fresh = _source(client, admin, vendor, **_bare(name=f"나 ERP {tag}", interval_minutes=60))
    stopped = _source(
        client, admin, vendor, **_bare(name=f"가 PLM {tag}", interval_minutes=60)
    )
    failing = _source(
        client, admin, vendor, **_bare(name=f"라 MES {tag}", interval_minutes=60)
    )
    off = _source(client, admin, vendor, **_bare(name=f"다 끔 {tag}", is_active=False))

    ok = _sync(client, admin, fresh["slug"], apply=True).json()["run"]
    assert ok["status"] == "ok", ok
    assert _sync(client, admin, stopped["slug"], apply=True).json()["run"]["status"] == "ok"
    plm.auth_required = "Bearer 없는것"
    assert (
        _sync(client, admin, failing["slug"], apply=True).json()["run"]["status"] == "failed"
    )
    plm.auth_required = None

    # 「가 PLM」 은 하루째 안 돌았다(타이머가 멎었다). 「라 MES」 도 하루 전이지만 실패 중이라
    # 멎음은 안 선다 — 홈 「남은 일」 과 같은 규칙(실패는 따로 센다).
    stale_before = maintenance_counts(client, admin).get("datasource_stale", 0)
    day_ago = datetime.now(UTC) - timedelta(days=1)
    _aged(db, stopped["slug"], last_run_at=day_ago)
    _aged(db, failing["slug"], last_run_at=day_ago)
    assert maintenance_counts(client, admin)["datasource_stale"] == stale_before + 1

    page = client.get(f"/api/objects/{vendor}", headers=admin.headers)
    assert page.status_code == 200, page.text
    got = page.json()["freshness"]
    assert [one["slug"] for one in got] == [
        stopped["slug"],
        fresh["slug"],
        off["slug"],
        failing["slug"],
    ]
    by_slug = {one["slug"]: one for one in got}
    assert by_slug[off["slug"]]["active"] is False
    assert (by_slug[off["slug"]]["failing"], by_slug[off["slug"]]["stale"]) == (False, False)
    assert all(by_slug[one["slug"]]["active"] for one in (stopped, fresh, failing))
    assert {one["type_slug"] for one in got} == {vendor}
    assert {one["kind"] for one in got} == {"odata"}
    assert by_slug[stopped["slug"]]["stale"] is True
    assert by_slug[stopped["slug"]]["failing"] is False
    assert by_slug[stopped["slug"]]["last_success_at"] is not None
    assert by_slug[fresh["slug"]]["stale"] is False
    assert by_slug[fresh["slug"]]["failing"] is False
    assert _when(by_slug[fresh["slug"]]["last_success_at"]) == _when(ok["finished_at"])
    assert by_slug[fresh["slug"]]["interval_minutes"] == 60
    assert by_slug[failing["slug"]]["failing"] is True
    assert by_slug[failing["slug"]]["stale"] is False
    assert by_slug[failing["slug"]]["last_success_at"] is None
    assert by_slug[failing["slug"]]["last_run_at"] is not None

    summary = client.get(f"/api/objects/{vendor}/summary", headers=admin.headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["freshness"] == got
    one = page.json()["items"][0]
    detail = client.get(f"/api/objects/{vendor}/{one['id']}", headers=admin.headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["freshness"] == got

    # 들어오는 원천이 없는 타입(손으로 · MCP 로 넣는다)은 빈 목록이다.
    manual = _make_type(client, admin, label="손으로", key_policy="optional")
    made = _make_object(client, admin, manual, label="하나")
    assert (
        client.get(f"/api/objects/{manual}", headers=admin.headers).json()["freshness"] == []
    )
    assert (
        client.get(f"/api/objects/{manual}/summary", headers=admin.headers).json()["freshness"]
        == []
    )
    assert (
        client.get(f"/api/objects/{manual}/{made['id']}", headers=admin.headers).json()[
            "freshness"
        ]
        == []
    )


def test_인터페이스_목록은_구현_타입_전부의_원천을_싣는다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """인터페이스 목록은 여러 타입이 섞인다 — 원천도 구현 타입마다이고, 줄마다 넣는 타입을
    말한다."""
    from tests.api.test_interfaces import _make_interface

    iface = _make_interface(client, admin, label="설비")
    tester = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    meter = _make_type(
        client, admin, label="계측기", key_policy="optional", interface_slugs=[iface]
    )
    tag = uuid.uuid4().hex[:6]
    first = _source(client, admin, meter, **_bare(name=f"가 계측 {tag}"))
    second = _source(client, admin, tester, **_bare(name=f"나 장비 {tag}"))

    page = client.get(f"/api/objects/{iface}", headers=admin.headers)
    assert page.status_code == 200, page.text
    want = [(first["slug"], meter), (second["slug"], tester)]
    assert [(one["slug"], one["type_slug"]) for one in page.json()["freshness"]] == want
    summary = client.get(f"/api/objects/{iface}/summary", headers=admin.headers)
    assert summary.status_code == 200, summary.text
    assert [(one["slug"], one["type_slug"]) for one in summary.json()["freshness"]] == want
    # 한 번도 안 돌았다 — 마지막 성공이 비어 있다(간격 0 이라 멎음도 아니다).
    assert {one["last_success_at"] for one in page.json()["freshness"]} == {None}
    assert {one["stale"] for one in page.json()["freshness"]} == {False}


def test_다른_표를_비추는_타입은_신선도가_비어_있다(client: TestClient, admin: Signed) -> None:
    """투영(system) 타입은 원천이 그 표 자체다 — 데이터 소스도 못 단다."""
    dept = _make_type(
        client, admin, "dept", label="부서", kind_class="system", system_source="workspace"
    )
    page = client.get(f"/api/objects/{dept}", headers=admin.headers)
    assert page.status_code == 200, page.text
    assert page.json()["freshness"] == []
    one = page.json()["items"][0]
    detail = client.get(f"/api/objects/{dept}/{one['id']}", headers=admin.headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["freshness"] == []


def test_신선도는_꺼_둔_소스까지_한_번의_질의로_읽는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """목록 · 통계마다 붙는 것이라 소스 수만큼 질의가 늘면 안 된다 — 서비스 함수를 바로
    본다."""
    from sqlalchemy import event

    from app.modules.ontology.models import ObjectType

    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    tag = uuid.uuid4().hex[:6]
    for name in ("다", "가", "나"):
        _source(client, admin, vendor, **_bare(name=f"{name} {tag}"))
    _source(client, admin, vendor, **_bare(name=f"마 끔 {tag}", is_active=False))
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == vendor))
    assert type_id is not None
    db.expire_all()

    seen: list[str] = []

    def count(*args: Any) -> None:
        seen.append(str(args[2]))

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        got = services.freshness(db, [type_id, type_id])
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert [one.name for one in got] == [f"가 {tag}", f"나 {tag}", f"다 {tag}", f"마 끔 {tag}"]
    assert [one.active for one in got] == [True, True, True, False]
    assert len(seen) == 1, seen
    assert services.freshness(db, []) == []


def test_소스의_대상_타입을_바꾸면_마지막_성공을_비운다(
    client: TestClient, admin: Signed, plm: FakeOData
) -> None:
    """2026-11-08 검토 — 옛 타입으로 성공한 때가 새 타입의 신선도로 실리면 「그 타입의 자료는
    그때까지 들어왔다」 는 거짓이 된다. 새 타입으로는 아직 한 번도 성공하지 않았다."""
    first = _make_type(client, admin, label="공급사", key_policy="optional")
    second = _make_type(client, admin, label="거래처", key_policy="optional")
    source = _source(client, admin, first, **_bare(name=f"ERP {uuid.uuid4().hex[:6]}"))
    assert _sync(client, admin, source["slug"], apply=True).json()["run"]["status"] == "ok"
    before = client.get(f"/api/objects/{first}", headers=admin.headers).json()["freshness"]
    assert before[0]["last_success_at"] is not None

    # 같은 타입을 다시 적으면 그대로다.
    same = client.patch(
        f"/api/datasources/{source['slug']}", json={"type_slug": first}, headers=admin.headers
    )
    assert same.status_code == 200, same.text
    assert same.json()["last_success_at"] is not None
    moved = client.patch(
        f"/api/datasources/{source['slug']}", json={"type_slug": second}, headers=admin.headers
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["last_success_at"] is None
    after = client.get(f"/api/objects/{second}", headers=admin.headers).json()["freshness"]
    assert [(one["slug"], one["last_success_at"]) for one in after] == [(source["slug"], None)]


def test_인터페이스를_타입으로_좁히면_그_타입의_원천만_싣는다(
    client: TestClient, admin: Signed
) -> None:
    """2026-11-08 검토 — `types=a|b` 로 좁혀도 구현 타입 전부의 원천을 실어, 고르지 않은 타입
    원천의 실패가 답에 「주의」 로 붙었다. 목록 · 통계 모두 좁힌 타입만."""
    from tests.api.test_interfaces import _make_interface

    iface = _make_interface(client, admin, label="설비")
    tester = _make_type(client, admin, label="시험장비", interface_slugs=[iface])
    gauge = _make_type(client, admin, label="계측기", interface_slugs=[iface])
    tag = uuid.uuid4().hex[:6]
    mine = _source(client, admin, tester, **_bare(name=f"시험 {tag}"))
    _source(client, admin, gauge, **_bare(name=f"계측 {tag}"))

    for path in (f"/api/objects/{iface}", f"/api/objects/{iface}/summary"):
        every = client.get(path, headers=admin.headers)
        assert every.status_code == 200, every.text
        assert {one["type_slug"] for one in every.json()["freshness"]} == {tester, gauge}
        narrowed = client.get(path, params={"types": tester}, headers=admin.headers)
        assert narrowed.status_code == 200, narrowed.text
        assert [one["slug"] for one in narrowed.json()["freshness"]] == [mine["slug"]]
