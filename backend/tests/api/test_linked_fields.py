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


def test_고르개는_두_걸음까지_늘어놓고_되짚는_걸음은_뺀다(
    client: TestClient, admin: Signed
) -> None:
    """한 걸음 고르개로는 「경쟁 툴의 개발사 국가」 · 「개발사가 공급하는 툴」 에 안 닿았다.
    같은 관계를 되짚는 걸음은 끝이 출발한 쪽이라 늘어놓지 않는다."""
    w = _world(client, admin)
    competes, resells = w["competes"], w["resells"]
    got = client.get(f"/api/objects/{w['tool']}/fields", headers=admin.headers)
    assert got.status_code == 200, got.text
    fields = {one["field"]: one for one in got.json()}

    two = fields[f"out.{competes}.ref.developer.country"]
    assert two["label"] == "경쟁 › 개발사 › 국가"
    assert two["heading"] == "경쟁 › 개발사 (기업)"
    assert two["data_type"] == "enum" and two["enum_options"] == ["미국", "한국"]
    supplied = fields[f"ref.developer.out.{resells}"]
    assert supplied["data_type"] == "object_ref" and supplied["ref_type_slug"] == w["tool"]
    assert fields[f"ref.developer.out.{resells}.label"]["label"] == "개발사 › 공급 › 이름"
    # 되짚기 — 방향 있는 관계를 반대로, 방향 없는 관계를 같은 쪽으로.
    assert not any(key.startswith(f"in.{resells}.out.{resells}") for key in fields)
    assert not any(key.startswith(f"out.{competes}.out.{competes}") for key in fields)
    # 세 걸음은 늘어놓지 않는다(주소로는 받는다).
    assert max(len(key.split(".")) for key in fields) == 5

    # 통계 기준에도 같은 주소로 — 관계 자체는 「이어진 것」 기준이다.
    options = {
        one["field"]: one for one in _summary(client, admin, w["tool"])["group_options"]
    }
    assert options[f"out.{competes}.ref.developer.country"]["kind"] == "enum"
    assert options[f"ref.developer.out.{resells}"]["kind"] == "related"
    # 그 주소로 세고, 막대와 같은 수로 거른다 — 툴1 은 한국사의 툴3 과 경쟁한다.
    by = _summary(client, admin, w["tool"], group_by=f"out.{competes}.ref.developer.country")
    counts = {one["label"]: one["count"] for one in by["buckets"]}
    assert counts["한국"] == 1
    assert _labels(
        client, admin, w["tool"], **{f"f.out.{competes}.ref.developer.country.eq": "한국"}
    ) == ["툴1"]
    # 개발사가 툴1 을 공급하는 툴 — 미국사 · 한국사 둘 다 툴1 을 공급하니 그들이 만든 툴 셋.
    assert _labels(
        client, admin, w["tool"], **{f"f.ref.developer.out.{resells}.eq": w["툴1"]}
    ) == ["툴1", "툴2", "툴3"]


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


# --- 걸음을 잇는다 ------------------------------------------------------------------
#
# 고르개는 한 걸음까지지만 주소는 셋까지 받는다(ADR 0013) — 지표가 「SKU → 기본 모델」 로 접고,
# 그 막대가 조건으로 돌아올 때 같은 주소가 필요하다. 같은 걸음을 두 기준이 지나도 행이 곱으로
# 불지 않아야 한다.


def _deep_world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """기록 ─model→ SKU ─base→ 기본 모델(계열) ─제조(관계)→ 제조사(국가). 기록은 태그 여럿."""
    maker = _make_type(client, admin, label="제조사")
    _make_property(
        client,
        admin,
        maker,
        key="country",
        label="국가",
        data_type="enum",
        enum_options=["KR", "US"],
    )
    base = _make_type(client, admin, label="기본 모델")
    _make_property(
        client,
        admin,
        base,
        key="series",
        label="계열",
        data_type="enum",
        enum_options=["S", "A"],
    )
    made_by = _make_relation(
        client, admin, label="제조", src_type_slugs=[base], dst_type_slugs=[maker]
    )
    sku = _make_type(client, admin, label="SKU")
    _make_property(
        client,
        admin,
        sku,
        key="base",
        label="기본 모델",
        data_type="object_ref",
        ref_type_slug=base,
    )
    tag = _make_type(client, admin, label="태그")
    _make_property(
        client,
        admin,
        tag,
        key="kind",
        label="종류",
        data_type="enum",
        enum_options=["색", "용량"],
    )
    case = _make_type(client, admin, label="기록", usage="log")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=sku,
    )
    _make_property(
        client,
        admin,
        case,
        key="tags",
        label="태그",
        data_type="object_ref",
        ref_type_slug=tag,
        multi=True,
    )
    kr = _make_object(client, admin, maker, label="한국사", properties={"country": "KR"})["id"]
    us = _make_object(client, admin, maker, label="미국사", properties={"country": "US"})["id"]
    s_base = _make_object(client, admin, base, label="S기본", properties={"series": "S"})["id"]
    a_base = _make_object(client, admin, base, label="A기본", properties={"series": "A"})["id"]
    _relate(client, admin, base, s_base, made_by, kr)
    _relate(client, admin, base, a_base, made_by, us)
    s1 = _make_object(client, admin, sku, label="S-1", properties={"base": s_base})["id"]
    s2 = _make_object(client, admin, sku, label="S-2", properties={"base": s_base})["id"]
    a1 = _make_object(client, admin, sku, label="A-1", properties={"base": a_base})["id"]
    red = _make_object(client, admin, tag, label="빨강", properties={"kind": "색"})["id"]
    big = _make_object(client, admin, tag, label="256G", properties={"kind": "용량"})["id"]
    for index, (model, tags) in enumerate(
        [(s1, [red, big]), (s2, [red]), (a1, [big]), (a1, [red, big]), (s1, [])]
    ):
        _make_object(
            client, admin, case, label=f"건{index}", properties={"model": model, "tags": tags}
        )
    return {
        "case": case,
        "sku": sku,
        "base": base,
        "s_base": s_base,
        "a_base": a_base,
        "made_by": made_by,
        "s1": s1,
        "s2": s2,
        "a1": a1,
    }


def test_걸음을_이어_접고_같은_주소로_거른다(client: TestClient, admin: Signed) -> None:
    w = _deep_world(client, admin)
    rel = w["made_by"]
    # 한 걸음의 참조 칸: SKU 를 기본 모델로 접는다(값은 id, 이름은 상대의 이름).
    found = _summary(client, admin, w["case"], group_by="ref.model.base")
    assert found["group_label"] == "모델 › 기본 모델"
    assert found["overlap"] is False
    assert {one["label"]: one["count"] for one in found["buckets"]} == {"S기본": 3, "A기본": 2}
    # 두 걸음(참조 → 참조 너머의 칸): 기본 모델의 계열.
    series = _summary(client, admin, w["case"], group_by="ref.model.ref.base.series")
    assert series["group_label"] == "모델 › 기본 모델 › 계열"
    assert {one["label"]: one["count"] for one in series["buckets"]} == {"S": 3, "A": 2}
    # 세 걸음(참조 → 참조 → 관계 너머의 칸): 제조사의 국가.
    deep = _summary(client, admin, w["case"], group_by=f"ref.model.ref.base.out.{rel}.country")
    assert {one["label"]: one["count"] for one in deep["buckets"]} == {"KR": 3, "US": 2}
    # 막대의 키가 같은 주소로 조건이 된다 — 묶은 수와 거른 수가 같다.
    s_key = next(one["key"] for one in found["buckets"] if one["label"] == "S기본")
    listed = client.get(
        f"/api/objects/{w['case']}",
        params={"f.ref.model.base.eq": s_key},
        headers=admin.headers,
    ).json()
    assert listed["total"] == 3
    by_series = client.get(
        f"/api/objects/{w['case']}",
        params={"f.ref.model.ref.base.series.eq": "A"},
        headers=admin.headers,
    ).json()
    assert by_series["total"] == 2
    by_country = client.get(
        f"/api/objects/{w['case']}",
        params={f"f.ref.model.ref.base.out.{rel}.country.eq": "US"},
        headers=admin.headers,
    ).json()
    assert by_country["total"] == 2


def test_같은_여럿_걸음을_두_기준이_지나도_곱으로_불지_않는다(
    client: TestClient, admin: Signed
) -> None:
    w = _deep_world(client, admin)
    # 태그(여러 값 참조)의 종류로 묶고 같은 태그의 이름으로 나눈다 — 태그마다 한 줄이지
    # 태그 x 태그가 아니다. 건 5개의 태그는 모두 6개.
    found = _summary(
        client, admin, w["case"], group_by="ref.tags.kind", split_by="ref.tags.label"
    )
    assert found["overlap"] is True
    counts = {one["label"]: one["count"] for one in found["buckets"]}
    assert counts == {"색": 3, "용량": 3, "(비어 있음)": 1}
    for bucket in found["buckets"]:
        assert sum(part["count"] for part in bucket["parts"]) == bucket["count"]


def test_걸음은_셋까지_모르는_걸음은_이유를_말한다(client: TestClient, admin: Signed) -> None:
    w = _deep_world(client, admin)
    rel = w["made_by"]
    too_long = client.get(
        f"/api/objects/{w['case']}/summary",
        params={"group_by": f"ref.model.ref.base.out.{rel}.out.{rel}.country"},
        headers=admin.headers,
    )
    assert too_long.status_code == 422
    assert "걸음" in too_long.json()["error"]["message"]
    unknown = client.get(
        f"/api/objects/{w['case']}/summary",
        params={"group_by": "ref.model.ref.nothing.x"},
        headers=admin.headers,
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"].endswith("OBJECTS-0086")
    # 참조 칸 걸음으로 끝나면 **고쳐 쓸 주소**를 그대로 말한다 — 「걸음을 잇는다」 고 생각하면
    # 끝까지 `ref.<칸>` 으로 적기 쉽다(문서에도 그렇게 잘못 적은 적이 있다).
    ref_end = client.get(
        f"/api/objects/{w['case']}/summary",
        params={"group_by": "ref.model.ref.base"},
        headers=admin.headers,
    )
    assert ref_end.status_code == 422
    assert "「ref.model.base」 로 씁니다" in ref_end.json()["error"]["message"]
    fixed = client.get(
        f"/api/objects/{w['case']}/summary",
        params={"group_by": "ref.model.base"},
        headers=admin.headers,
    )
    assert fixed.status_code == 200, fixed.text


# --- 나를 가리키는 것(들어오는 참조, ADR 0017) ---------------------------------------


def test_나를_가리키는_것을_고르개에_세우고_되짚는_걸음은_뺀다(
    client: TestClient, admin: Signed
) -> None:
    w = _deep_world(client, admin)
    case, sku = w["case"], w["sku"]
    back = f"in.{case}:model"
    fields = {
        one["field"]: one
        for one in client.get(f"/api/objects/{sku}/fields", headers=admin.headers).json()
    }
    itself = fields[back]
    assert itself["heading"] == "가리키는 것 · 기록(모델)"
    assert itself["data_type"] == "object_ref" and itself["ref_type_slug"] == case
    assert fields[f"{back}.label"]["label"] == "기록(모델) › 이름"
    # 두 걸음 — 가리키는 기록의 태그의 종류.
    assert fields[f"{back}.ref.tags.kind"]["label"] == "기록(모델) › 태그 › 종류"
    # 되짚기 — SKU → 가리키는 기록 → 그 기록의 모델(다시 SKU)은 늘어놓지 않는다.
    assert not any(key.startswith(f"{back}.ref.model") for key in fields)
    # 기본 모델에서는 두 걸음 모두 거꾸로 — SKU 가 가리키고, 그 SKU 를 기록이 가리킨다.
    base_fields = client.get(f"/api/objects/{w['base']}/fields", headers=admin.headers).json()
    assert f"in.{sku}:base.{back}.label" in {one["field"] for one in base_fields}

    # 통계 기준에는 그 걸음 자체가 없다(기록마다 막대가 선다) — 주소로 물으면 이유와 함께 거절.
    options = {one["field"] for one in _summary(client, admin, sku)["group_options"]}
    assert back not in options and f"{back}.ref.tags.label" in options
    refused = client.get(
        f"/api/objects/{sku}/summary", params={"group_by": back}, headers=admin.headers
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"].endswith("OBJECTS-0099")


def test_나를_가리키는_것으로_거르고_객체마다_한_번_센다(
    client: TestClient, admin: Signed
) -> None:
    w = _deep_world(client, admin)
    case, sku = w["case"], w["sku"]
    back = f"in.{case}:model"
    _make_object(client, admin, sku, label="B-1")  # 가리키는 기록이 없다
    assert _labels(client, admin, sku, **{f"f.{back}.empty": ""}) == ["B-1"]
    assert _labels(client, admin, sku, **{f"f.{back}.notempty": ""}) == ["A-1", "S-1", "S-2"]
    # 「256G 태그가 달린 기록이 있는 SKU」 — 건0(S-1) · 건2 · 건3(A-1).
    assert _labels(client, admin, sku, **{f"f.{back}.ref.tags.label.eq": "256G"}) == [
        "A-1",
        "S-1",
    ]

    # 태그 이름별 SKU 수 — A-1 은 256G 기록이 둘이어도 그 막대에서 하나다.
    found = _summary(client, admin, sku, group_by=f"{back}.ref.tags.label")
    assert found["overlap"] is True and found["total"] == 4
    counts = {one["label"]: one["count"] for one in found["buckets"]}
    assert counts == {"빨강": 3, "256G": 2, "(비어 있음)": 2}
    # 막대를 누르면 그 수만큼 걸린다.
    big = next(one for one in found["buckets"] if one["label"] == "256G")
    assert (
        len(_labels(client, admin, sku, **{f"f.{back}.ref.tags.label.eq": "256G"}))
        == big["count"]
    )
    # 두 걸음 모두 거꾸로 — 태그 이름별 기본 모델 수.
    bases = _summary(client, admin, w["base"], group_by=f"in.{sku}:base.{back}.ref.tags.label")
    assert {one["label"]: one["count"] for one in bases["buckets"]} == {
        "빨강": 2,
        "256G": 2,
        "(비어 있음)": 1,
    }
    # 세부 기준으로 나눠도 조각의 합이 칸과 같다(객체마다 한 번).
    split = _summary(
        client, admin, sku, group_by=f"{back}.ref.tags.label", split_by=f"{back}.ref.tags.kind"
    )
    for bucket in split["buckets"]:
        assert sum(part["count"] for part in bucket["parts"]) == bucket["count"]


def test_가리키는_기록은_보이는_것만_잇는다(
    client: TestClient, admin: Signed, member: Signed, db: Any
) -> None:
    """남의 부서 기록이 있다는 사실이 「기록이 있는 SKU」 로 새지 않는다."""
    import uuid

    from app.modules.workspaces.models import Workspace

    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    w = _deep_world(client, admin)
    case, sku = w["case"], w["sku"]
    hidden_model = _make_object(client, admin, sku, label="H-1")["id"]
    made = client.post(
        f"/api/objects/{case}",
        json={
            "workspace_slug": other.slug,
            "label": "남의 건",
            "properties": {"model": hidden_model, "tags": []},
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    back = f"in.{case}:model"
    assert "H-1" in _labels(client, admin, sku, **{f"f.{back}.notempty": ""})
    assert "H-1" not in _labels(client, member, sku, **{f"f.{back}.notempty": ""})
    assert "H-1" in _labels(client, member, sku, **{f"f.{back}.empty": ""})


def test_지표의_기준으로는_나를_가리키는_것을_안_받는다(
    client: TestClient, admin: Signed
) -> None:
    w = _deep_world(client, admin)
    got = client.post(
        "/api/metrics/plan",
        json={
            "source_type_slug": w["sku"],
            "spec": {
                "measure": "count",
                "dimensions": [
                    {"name": "tag", "address": f"in.{w['case']}:model.ref.tags.label"}
                ],
            },
        },
        headers=admin.headers,
    )
    assert "나를 가리키는 것" in got.text, got.text
