"""이어진 것 너머의 칸 — **「미국 기업이 만든 툴만」 을 목록에서 바로.**

지키는 것: 조건과 통계 기준이 **같은 주소**를 쓰고 같은 수를 내나(막대를 누르면 그 수만큼
걸러지나), 이어진 것이 여럿이면 합이 크다고 알리나, 모르는 주소는 이유를 말하나.

    기업(국가)  ←개발사(참조)─  툴  ─경쟁(방향 없음)─  툴
                                 ↑
                    기업 ─공급(방향 있음, 들어오는 쪽은 「공급받음」)
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_relation, _make_type


def _relate(
    client: TestClient, admin: Signed, slug: str, src: str, relation: str, dst: str
) -> None:
    got = client.post(
        f"/api/objects/{slug}/{src}/relations",
        json={"relation": relation, "dst_object_id": dst},
        headers=admin.headers,
    )
    assert got.status_code == 201, got.text


def _world(client: TestClient, admin: Signed) -> dict[str, str]:
    company = _make_type(client, admin, label="기업")
    _make_property(
        client,
        admin,
        company,
        key="country",
        label="국가",
        data_type="enum",
        enum_options=["미국", "한국"],
    )
    tool = _make_type(client, admin, label="툴")
    _make_property(
        client,
        admin,
        tool,
        key="developer",
        label="개발사",
        data_type="object_ref",
        ref_type_slug=company,
    )
    us = _make_object(client, admin, company, label="미국사", properties={"country": "미국"})[
        "id"
    ]
    kr = _make_object(client, admin, company, label="한국사", properties={"country": "한국"})[
        "id"
    ]
    tools = {
        "툴1": _make_object(client, admin, tool, label="툴1", properties={"developer": us})[
            "id"
        ],
        "툴2": _make_object(client, admin, tool, label="툴2", properties={"developer": us})[
            "id"
        ],
        "툴3": _make_object(client, admin, tool, label="툴3", properties={"developer": kr})[
            "id"
        ],
        "툴4": _make_object(client, admin, tool, label="툴4")["id"],
    }
    competes = _make_relation(
        client,
        admin,
        base="competes",
        label="경쟁",
        directed=False,
        src_type_slugs=[tool],
        dst_type_slugs=[tool],
    )
    resells = _make_relation(
        client,
        admin,
        base="resells",
        label="공급",
        inverse_label="공급받음",
        src_type_slugs=[company],
        dst_type_slugs=[tool],
    )
    _relate(client, admin, tool, tools["툴1"], competes, tools["툴3"])
    _relate(client, admin, company, us, resells, tools["툴1"])
    _relate(client, admin, company, kr, resells, tools["툴1"])
    return {
        "company": company,
        "tool": tool,
        "competes": competes,
        "resells": resells,
        "us": us,
        "kr": kr,
        **tools,
    }


def _labels(client: TestClient, who: Signed, slug: str, **params: Any) -> list[str]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return sorted(one["label"] for one in got.json()["items"])


def _summary(client: TestClient, who: Signed, slug: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}/summary", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_참조_너머의_칸으로_거른다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    assert _labels(client, admin, w["tool"], **{"f.ref.developer.country.eq": "미국"}) == [
        "툴1",
        "툴2",
    ]
    # **이어진 것이 없는 툴은 이어진 것의 칸 조건에 안 걸린다** — 개발사가 빈 툴4 는 없다.
    assert _labels(client, admin, w["tool"], **{"f.ref.developer.country.ne": "미국"}) == [
        "툴3"
    ]
    assert _labels(client, admin, w["tool"], **{"f.ref.developer.label.contains": "한국"}) == [
        "툴3"
    ]


def test_참조_너머의_칸이_통계_기준이_되고_막대와_목록이_같다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    found = _summary(client, admin, w["tool"], group_by="ref.developer.country")
    assert found["group_label"] == "개발사 › 국가"
    buckets = {one["label"]: one["count"] for one in found["buckets"]}
    assert buckets == {"미국": 2, "한국": 1, "(비어 있음)": 1}
    # 개발사는 하나뿐이라 한 행이 한 막대 — 합이 크다고 알리지 않는다.
    assert found["overlap"] is False
    # **막대를 누르면 그 주소 그대로 조건이 된다** — 수가 같아야 한다.
    listed = _labels(client, admin, w["tool"], **{"f.ref.developer.country.eq": "미국"})
    assert len(listed) == buckets["미국"]

    options = {one["field"]: one for one in found["group_options"]}
    assert options["ref.developer.country"]["label"] == "개발사 › 국가"
    # 참조 칸과 관계가 같은 이름이어도 제목이 가른다.
    assert options["ref.developer.country"]["heading"] == "개발사 (기업)"
    assert options["status"]["heading"] == ""
    assert options[f"out.{w['competes']}"]["kind"] == "related"


def test_관계로_이어진_것_자체로_거르고_센다(client: TestClient, admin: Signed) -> None:
    """방향 없는 관계는 양쪽 어디서 봐도 이어져 있다."""
    w = _world(client, admin)
    path = f"out.{w['competes']}"
    assert _labels(client, admin, w["tool"], **{f"f.{path}.notempty": ""}) == ["툴1", "툴3"]
    assert _labels(client, admin, w["tool"], **{f"f.{path}.empty": ""}) == ["툴2", "툴4"]
    assert _labels(client, admin, w["tool"], **{f"f.{path}.eq": w["툴3"]}) == ["툴1"]

    found = _summary(client, admin, w["tool"], group_by=path)
    buckets = {one["label"]: one["count"] for one in found["buckets"]}
    assert buckets == {"툴1": 1, "툴3": 1, "(비어 있음)": 2}
    # 막대의 key 는 상대의 id — 누르면 eq 조건이 된다.
    keys = {one["label"]: one["key"] for one in found["buckets"]}
    assert keys["툴3"] == w["툴3"]


def test_들어오는_관계의_칸으로_세고_여럿이면_합이_크다고_알린다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    path = f"in.{w['resells']}.country"
    found = _summary(client, admin, w["tool"], group_by=path)
    assert found["group_label"] == "공급받음 › 국가"
    buckets = {one["label"]: one["count"] for one in found["buckets"]}
    # 툴1 은 두 기업에서 공급받는다 — 미국·한국 막대에 모두 들어 합(5)이 전체(4)보다 크다.
    assert buckets == {"미국": 1, "한국": 1, "(비어 있음)": 3}
    assert found["total"] == 4 and found["overlap"] is True
    # 조건은 「이어진 것 중 하나라도」 — 툴1 은 한 번만 나온다.
    assert _labels(client, admin, w["tool"], **{f"f.{path}.in": "미국|한국"}) == ["툴1"]


def test_고르개가_이어진_칸을_제목별로_준다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    got = client.get(f"/api/objects/{w['tool']}/fields", headers=admin.headers)
    assert got.status_code == 200, got.text
    fields = {one["field"]: one for one in got.json()}

    country = fields["ref.developer.country"]
    assert country["heading"] == "개발사 (기업)"
    assert country["data_type"] == "enum" and country["enum_options"] == ["미국", "한국"]

    competes = fields[f"out.{w['competes']}"]
    assert competes["heading"] == "관계 · 경쟁 (툴)"
    assert competes["ref_type_slug"] == w["tool"]

    assert fields[f"in.{w['resells']}"]["label"] == "공급받음"
    # 조건의 고정 칸은 목록과 같게 이름·식별자뿐 — 상태는 따로 거른다.
    assert "ref.developer.status" not in fields


def test_모르는_주소는_이유를_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    bad = client.get(
        f"/api/objects/{w['tool']}",
        params={"f.ref.developer.nope.eq": "x"},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    assert "없는 칸" in bad.json()["error"]["message"]

    unknown = client.get(
        f"/api/objects/{w['tool']}/summary",
        params={"group_by": "out.nope"},
        headers=admin.headers,
    )
    assert unknown.status_code == 422


def test_뷰에_이어진_칸의_조건과_기준이_담긴다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    saved = client.post(
        f"/api/objects/{w['tool']}/views",
        json={
            "name": "미국 툴",
            "query": {
                "q": "",
                "conditions": [
                    {"field": "ref.developer.country", "op": "eq", "value": "미국"}
                ],
            },
            "summary": {"group_by": f"out.{w['competes']}", "metric": "count"},
        },
        headers=admin.headers,
    )
    assert saved.status_code == 201, saved.text

    broken = client.post(
        f"/api/objects/{w['tool']}/views",
        json={
            "name": "깨진 것",
            "query": {
                "q": "",
                "conditions": [{"field": "ref.nope.x", "op": "eq", "value": "1"}],
            },
        },
        headers=admin.headers,
    )
    assert broken.status_code == 422
