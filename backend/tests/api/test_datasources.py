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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.datasources import services
from tests.api.conftest import (
    Signed,
    bundle_import,
    finish_job,
    maintenance_counts,
    notifications_of,
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
