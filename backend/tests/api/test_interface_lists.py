"""인터페이스 목록 — **구현 타입 전부를 한 목록으로 읽는다**(ADR 0006).

`/api/objects/<인터페이스>` 의 읽기 경로(목록 · 조건 · 검색 · 통계 · 이어진 칸 · 이름 풀이 ·
진단 · 상세)는 타입과 같은 말로 답하고, 쓰기 경로(만들기 · 트리 · 저장된 뷰 · 가져오기)는
이유를 말하며 거절한다. 같은 키 · 같은 모양이라 조건 식은 그대로고 타입 조건만 넓어진다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_interfaces import _add_common, _code, _make_interface
from tests.api.test_ontology import _make_object, _make_type, _uniq


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """설비(인터페이스) — 시험장비(기간이 있는 타입) · 계측기(상시 타입)."""
    iface = _make_interface(client, admin, label="설비")
    _add_common(
        client, admin, iface, key="maker", label="제조사", data_type="text"
    ).raise_for_status()
    _add_common(
        client,
        admin,
        iface,
        key="country",
        label="국가",
        data_type="enum",
        enum_options=["KR", "US"],
    ).raise_for_status()
    _add_common(
        client, admin, iface, key="power", label="출력", data_type="number"
    ).raise_for_status()
    tester = _make_type(
        client,
        admin,
        label="시험장비",
        key_policy="optional",
        temporal_kind="lifecycle",
        interface_slugs=[iface],
    )
    meter = _make_type(
        client, admin, label="계측기", key_policy="optional", interface_slugs=[iface]
    )
    t1 = _make_object(
        client,
        admin,
        tester,
        label="시험기 1",
        key="K1",
        properties={"maker": "A사", "country": "KR", "power": 10},
        valid_from_year=2020,
        valid_to_year=2021,
    )
    m1 = _make_object(
        client,
        admin,
        meter,
        label="계측기 1",
        key="K1",
        properties={"maker": "B사", "country": "US", "power": 3},
    )
    return {"iface": iface, "tester": tester, "meter": meter, "t1": t1, "m1": m1}


def _list(client: TestClient, who: Signed, slug: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_구현_타입_전부가_한_목록에_서고_줄마다_제_타입을_말한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    page = _list(client, admin, w["iface"])
    assert page["total"] == 2
    rows = {one["label"]: one for one in page["items"]}
    assert rows["시험기 1"]["type_slug"] == w["tester"]
    assert rows["계측기 1"]["type_slug"] == w["meter"]

    # 같은 키 · 같은 모양이라 **조건 식이 그대로** 구현 타입 전부에 걸린다.
    kr = _list(client, admin, w["iface"], **{"f.country.eq": "KR"})
    assert [one["label"] for one in kr["items"]] == ["시험기 1"]
    big = _list(client, admin, w["iface"], **{"f.power.gte": "5"})
    assert [one["label"] for one in big["items"]] == ["시험기 1"]
    found = _list(client, admin, w["iface"], q="계측")
    assert [one["label"] for one in found["items"]] == ["계측기 1"]

    # 그중 몇 타입만 — `types=`.
    only = _list(client, admin, w["iface"], types=w["meter"])
    assert [one["label"] for one in only["items"]] == ["계측기 1"]
    stranger = client.get(
        f"/api/objects/{w['iface']}", params={"types": "nope"}, headers=admin.headers
    )
    assert stranger.status_code == 422 and _code(stranger).endswith("OBJECTS-0093")


def test_연도는_구현_타입마다_제_시간_정책대로_거른다(
    client: TestClient, admin: Signed
) -> None:
    """시험장비는 기간(2020~2021)이 있고 계측기는 상시다 — 하나로 몰면 상시 타입이 통째로
    빠지거나 기간 타입이 거르지 않은 채 섞인다."""
    w = _world(client, admin)
    later = _list(client, admin, w["iface"], year=2025)
    assert [one["label"] for one in later["items"]] == ["계측기 1"]
    within = _list(client, admin, w["iface"], year=2020)
    assert {one["label"] for one in within["items"]} == {"시험기 1", "계측기 1"}


def test_통계는_타입으로도_공통_속성으로도_묶는다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    by_type = client.get(
        f"/api/objects/{w['iface']}/summary",
        params={"group_by": "type"},
        headers=admin.headers,
    )
    assert by_type.status_code == 200, by_type.text
    body = by_type.json()
    assert {one["label"]: one["count"] for one in body["buckets"]} == {
        "시험장비": 1,
        "계측기": 1,
    }
    assert body["group_options"][0]["field"] == "type"

    by_country = client.get(
        f"/api/objects/{w['iface']}/summary",
        params={"group_by": "properties.country"},
        headers=admin.headers,
    )
    assert {one["key"]: one["count"] for one in by_country.json()["buckets"]} == {
        "KR": 1,
        "US": 1,
    }
    # 타입 목록에는 「타입」 축이 없다 — 한 타입뿐이다.
    plain = client.get(
        f"/api/objects/{w['tester']}/summary",
        params={"group_by": "type"},
        headers=admin.headers,
    )
    assert plain.status_code == 422


def test_같은_식별자가_두_타입에_있으면_짐작하지_않고_후보로_돌려준다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    got = client.get(
        f"/api/objects/{w['iface']}/resolve", params={"name": "K1"}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["match"] == "candidates"
    assert {one["type_slug"] for one in body["candidates"]} == {w["tester"], w["meter"]}


def test_인터페이스로는_읽기만_하고_쓰기는_이유를_말하며_거절한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    made = client.post(
        f"/api/objects/{w['iface']}",
        json={"label": "새 설비", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert made.status_code == 409 and _code(made).endswith("OBJECTS-0092")
    assert "구현 타입" in made.json()["error"]["message"]
    for path in ("tree", "views"):
        refused = client.get(f"/api/objects/{w['iface']}/{path}", headers=admin.headers)
        assert refused.status_code == 409, (path, refused.text)
    under = client.get(
        f"/api/objects/{w['iface']}", params={"under": w["t1"]["id"]}, headers=admin.headers
    )
    assert under.status_code == 409


def test_인터페이스_주소로_연_상세는_객체의_실제_타입으로_답한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    got = client.get(f"/api/objects/{w['iface']}/{w['m1']['id']}", headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.json()["object"]["type_slug"] == w["meter"]
    # 구현 타입이 아닌 객체는 그 주소로 안 열린다.
    other = _make_type(client, admin)
    stranger = _make_object(client, admin, other, label="남")
    assert (
        client.get(
            f"/api/objects/{w['iface']}/{stranger['id']}", headers=admin.headers
        ).status_code
        == 404
    )


def test_통합_검색을_인터페이스로_좁히면_구현_타입_전부에서_찾는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    got = client.get(
        "/api/search", params={"q": "기 1", "type": w["iface"]}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    assert {one["type_slug"] for one in got.json()["items"]} == {w["tester"], w["meter"]}


def test_구현_타입이_없는_인터페이스의_0건은_그렇다고_말한다(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin, slug=_uniq("lonely"))
    assert _list(client, admin, iface)["total"] == 0
    got = client.get(f"/api/objects/{iface}/diagnose", headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.json()["reason"] == "no_implementers"


def test_좁힌_타입이_0건이면_진단이_그_조건을_든다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    got = client.get(
        f"/api/objects/{w['iface']}/diagnose",
        params={"types": w["meter"], "f.country.eq": "KR"},
        headers=admin.headers,
    )
    body = got.json()
    assert body["reason"] == "filters", body
    assert any(one["name"] == "types" for one in body["filters"])
