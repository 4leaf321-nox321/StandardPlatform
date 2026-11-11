"""선의 칸으로 거르기 — `out.<관계>~<관계 속성>` 와 같은 관계 묶음(ADR 0043).

    제품 ─구성(bom, 유효 기간)→ 부품. 선의 칸: 수량 qty(숫자) · 옵션 opts(고를 값 여럿) ·
    메모 memo(글자), 그리고 시작일 · 끝날.

    P1 ─ X(1개, A) · Y(5개, A|B)       「X 가 4개 이상」 이 아니다 — X 는 1개, 5개는 Y
    P2 ─ X(4개, B, 메모 「주력」) · Z(1개, B)
    P3 ─ Y(2개)
    P4 ─ X(6개, 3월 1일 전까지) · X(2개, 3월 1일부터)
    P5 ─ (없음)

가장 중요한 함정: 조건마다 「이어진 것 중 하나라도」 이므로, 묶지 않은 `out.bom = X` 와
`out.bom~qty ≥ 4` 는 **다른 선으로** P1 에 걸린다. 그 뜻은 그대로 두고(저장된 뷰 · 지표가 그
뜻이다), 같은 관계 묶음(`fe<번호>.`)으로 묶은 조건만 같은 선 하나에서 함께 맞아야 걸린다.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed, clock_settled, export_file
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type

FEB = "2026-02-15"
MAR = "2026-03-01"


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:10]}"


def _bom_kind(client: TestClient, admin: Signed, product: str, part: str) -> str:
    """제품 → 부품의 「구성」 — 유효 기간을 받고, 관계 속성 셋."""
    slug = _uniq("bom")
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": slug,
                    "label": "구성품",
                    "inverse_label": "쓰인 곳",
                    "effectivity": True,
                    "src_type_slugs": [product],
                    "dst_type_slugs": [part],
                    "properties": [
                        {"key": "qty", "label": "수량", "data_type": "number"},
                        {
                            "key": "opts",
                            "label": "적용 옵션",
                            "data_type": "enum",
                            "multi": True,
                            "enum_options": ["A", "B", "C"],
                        },
                        {"key": "memo", "label": "메모", "data_type": "text"},
                    ],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    return slug


@pytest.fixture
def world(client: TestClient, admin: Signed) -> dict[str, Any]:
    vendor = _make_type(client, admin, label="업체")
    _make_property(client, admin, vendor, key="country", label="국가", data_type="text")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    product = _make_type(client, admin, label="제품", key_policy="optional")
    _make_property(client, admin, product, key="launched", label="출시일", data_type="date")
    kind = _bom_kind(client, admin, product, part)
    kr = _make_object(client, admin, vendor, label="한국 업체", properties={"country": "KR"})
    us = _make_object(client, admin, vendor, label="미국 업체", properties={"country": "US"})
    parts = {
        "X": _make_object(client, admin, part, label="X", properties={"vendor": kr["id"]}),
        "Y": _make_object(client, admin, part, label="Y", properties={"vendor": us["id"]}),
        "Z": _make_object(client, admin, part, label="Z", properties={"vendor": kr["id"]}),
    }
    products = {
        name: _make_object(
            client, admin, product, label=name, properties={"launched": f"2026-0{n}-10"}
        )
        for n, name in enumerate(["P1", "P2", "P3", "P4", "P5"], 1)
    }
    edges: dict[str, str] = {}
    for src, dst, props, period in (
        ("P1", "X", {"qty": 1, "opts": ["A"]}, {}),
        ("P1", "Y", {"qty": 5, "opts": ["A", "B"]}, {}),
        ("P2", "X", {"qty": 4, "opts": ["B"], "memo": "주력 구성"}, {}),
        ("P2", "Z", {"qty": 1, "opts": ["B"]}, {}),
        ("P3", "Y", {"qty": 2}, {}),
        ("P4", "X", {"qty": 6}, {"valid_to": MAR}),
        ("P4", "X", {"qty": 2}, {"valid_from": MAR}),
    ):
        got = _link(
            client,
            admin,
            product,
            products[src]["id"],
            kind,
            parts[dst]["id"],
            properties=props,
            **period,
        )
        assert got.status_code == 201, got.text
        edges[f"{src}-{dst}{period.get('valid_from', '')}"] = got.json()["relation_id"]
    return {
        "product": product,
        "part": part,
        "vendor": vendor,
        "kind": kind,
        "parts": {name: one["id"] for name, one in parts.items()},
        "products": {name: one["id"] for name, one in products.items()},
        "edges": edges,
    }


def _names(client: TestClient, who: Signed, slug: str, params: dict[str, str]) -> list[str]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, (params, got.text)
    return sorted(one["label"] for one in got.json()["items"])


def _refused(client: TestClient, who: Signed, slug: str, params: dict[str, str]) -> str:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 422, (params, got.text)
    return str(got.json()["error"]["message"])


# --- 같은 선 묶기 ------------------------------------------------------------------


def test_같은_관계_묶음은_다른_선의_조합을_거른다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, x = world["product"], world["parts"]["X"]
    kind = world["kind"]
    unbound = {f"f.out.{kind}.eq": x, f"f.out.{kind}~qty.gte": "4"}
    bound = {f"fe1.out.{kind}.eq": x, f"fe1.out.{kind}~qty.gte": "4"}
    # 묶지 않으면 예전 뜻 — P1 은 X(1개)와 Y(5개)가 **다른 선으로** 걸린다.
    assert _names(client, admin, product, unbound) == ["P1", "P2"]
    # 묶으면 같은 선 하나 — X 가 4개 이상인 것만. P4 는 오늘 X 2개.
    assert _names(client, admin, product, bound) == ["P2"]
    # 기준일 — 2월에는 P4 의 X 가 6개인 선이 살아 있다.
    assert _names(client, admin, product, {**bound, "as_of": FEB}) == ["P2", "P4"]
    assert _names(client, admin, product, {**unbound, "as_of": FEB}) == ["P1", "P2", "P4"]
    # 상대의 칸으로 묶어도 같다 — 「이름이 X 인 부품이 4개 이상」.
    named = {f"fe2.out.{kind}.label.eq": "X", f"fe2.out.{kind}~qty.gte": "4"}
    assert _names(client, admin, product, named) == ["P2"]
    # 상대 너머(공급사의 국가)는 그 선의 상대에서 「하나라도」 — 한국 업체 부품이 4개 이상.
    beyond = {
        f"fe1.out.{kind}~qty.gte": "4",
        f"fe1.out.{kind}.ref.vendor.country.eq": "KR",
    }
    assert _names(client, admin, product, beyond) == ["P2"]
    # 묶음 둘은 서로 다른 선이어도 된다 — X(옵션 B)도 있고 Z 도 있는 것.
    two = {
        f"fe1.out.{kind}.eq": x,
        f"fe1.out.{kind}~opts.eq": "B",
        f"fe2.out.{kind}.eq": world["parts"]["Z"],
    }
    assert _names(client, admin, product, two) == ["P2"]


def test_묶지_않은_선의_칸은_그중_하나라도(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind = world["product"], world["kind"]
    # 숫자 — 형별 규칙이 객체의 칸과 같다.
    assert _names(client, admin, product, {f"f.out.{kind}~qty.gt": "4"}) == ["P1"]
    assert _names(client, admin, product, {f"f.out.{kind}~qty.lte": "1"}) == ["P1", "P2"]
    assert _names(client, admin, product, {f"f.out.{kind}~qty.in": "2|4"}) == [
        "P2",
        "P3",
        "P4",
    ]
    # 「다름」 — 그 값이 아닌 선이 하나라도(이어진 것이 없는 P5 는 안 걸린다).
    assert _names(client, admin, product, {f"f.out.{kind}~qty.ne": "1"}) == [
        "P1",
        "P2",
        "P3",
        "P4",
    ]
    # 고를 값 여럿 — 「담고 있나」.
    assert _names(client, admin, product, {f"f.out.{kind}~opts.eq": "B"}) == ["P1", "P2"]
    assert _names(client, admin, product, {f"f.out.{kind}~opts.empty": ""}) == ["P3", "P4"]
    # 글자.
    assert _names(client, admin, product, {f"f.out.{kind}~memo.contains": "주력"}) == ["P2"]
    # 선의 시작일 · 끝날 — 유효 기간을 받는 종류의 선의 칸.
    assert _names(client, admin, product, {f"f.out.{kind}~valid_from.gte": MAR}) == ["P4"]
    assert _names(
        client, admin, product, {f"f.out.{kind}~valid_to.notempty": "", "as_of": FEB}
    ) == ["P4"]
    assert _names(client, admin, product, {f"f.out.{kind}~valid_to.notempty": ""}) == []


def test_옵션_B_에서만_들어가는_부품(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """부품 쪽에서 들어오는 선(`in.<관계>~<칸>`) — 「B 가 든 선이 있고, B 가 없는 선은
    없다」."""
    part, kind = world["part"], world["kind"]
    only_b = {f"f.in.{kind}~opts.eq": "B", f"nf1.in.{kind}~opts.ne": "B"}
    assert _names(client, admin, part, only_b) == ["Z"]


def test_다름은_자리마다_뜻이_다르다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """MCP 가이드(`find` — 「`ne` 는 자리마다 뜻이 다르다」)가 적은 그대로 — 바뀌면 가이드도.

    관계 자체의 `ne X` 는 「X 가 하나도 없다」(`eq X` 의 반대, 이어진 것이 없는 P5 도 든다),
    너머의 칸 · 같은 관계 묶음 안의 `ne X` 는 「그중 하나라도 X 가 아니다」. 견주기(2026-11-11)
    에서 AI 가 「단독 공급」 을 `eq S` + `ne S` 로 물어 0건 — 그 차를 빼고 틀린 답을 냈다."""
    product, kind, parts = world["product"], world["kind"], world["parts"]
    x, y = parts["X"], parts["Y"]
    rel = f"out.{kind}"
    assert _names(client, admin, product, {f"f.{rel}.ne": x}) == ["P3", "P5"]
    assert _names(client, admin, product, {f"f.{rel}.eq": x, f"f.{rel}.ne": x}) == []
    # 「X 만 든 제품」 — X 가 있고, X 가 아닌 선이 하나라도 있는 것은 뺀다.
    only_x = {f"f.{rel}.eq": x, f"nf1.fe1.{rel}.ne": x}
    assert _names(client, admin, product, only_x) == ["P4"]
    # 너머의 칸 — X 가 아닌 부품이 하나라도(X 뿐인 P4 · 없는 P5 는 안 걸린다).
    assert _names(client, admin, product, {f"f.{rel}.label.ne": "X"}) == ["P1", "P2", "P3"]
    # 같은 칸에 둘을 걸어도 AND — 「X 와 Y 가 함께 든 제품」. 「또는」 은 `in`.
    both = client.get(
        f"/api/objects/{product}",
        params=[(f"f.{rel}.eq", x), (f"f.{rel}.eq", y)],
        headers=admin.headers,
    )
    assert both.status_code == 200, both.text
    assert [one["label"] for one in both.json()["items"]] == ["P1"]
    assert _names(client, admin, product, {f"f.{rel}.in": f"{x}|{y}"}) == [
        "P1",
        "P2",
        "P3",
        "P4",
    ]


def test_제외_묶음_안의_같은_관계_묶음은_한_조건이다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind, x = world["product"], world["kind"], world["parts"]["X"]
    every = _names(client, admin, product, {})
    assert every == ["P1", "P2", "P3", "P4", "P5"]
    hit = {f"out.{kind}.eq": x, f"out.{kind}~qty.gte": "4"}
    # 「X 가 4개 이상 든 것은 뺀다」 = NOT EXISTS(선: X AND 4개 이상).
    rest = _names(
        client, admin, product, {f"nf1.fe1.{key}": value for key, value in hit.items()}
    )
    assert rest == ["P1", "P3", "P4", "P5"]
    bound = _names(client, admin, product, {f"fe1.{key}": value for key, value in hit.items()})
    assert sorted(bound + rest) == every
    # 묶지 않은 제외 묶음은 예전 뜻 — 「X 가 있고, 4개 이상인 선도 있는 것」 을 뺀다(P1 도).
    loose = _names(client, admin, product, {f"nf1.{key}": value for key, value in hit.items()})
    assert loose == ["P3", "P4", "P5"]
    # 제외 묶음 안의 같은 관계 묶음과 다른 조건 — 「(X 4개 이상) 이고 3월 이후 출시」 를 뺀다.
    mixed = _names(
        client,
        admin,
        product,
        {
            f"nf1.fe1.out.{kind}.eq": x,
            f"nf1.fe1.out.{kind}~qty.gte": "4",
            "nf1.launched.gte": "2026-03-01",
        },
    )
    assert mixed == every  # P2 는 2월 출시라 묶음 전체에는 안 맞는다
    mixed = _names(
        client,
        admin,
        product,
        {
            f"nf1.fe1.out.{kind}.eq": x,
            f"nf1.fe1.out.{kind}~qty.gte": "4",
            "nf1.launched.lt": "2026-03-01",
        },
    )
    assert mixed == ["P1", "P3", "P4", "P5"]
    # 같은 번호가 두 자리(보통 조건 · 제외 묶음)에 서면 거절한다.
    said = _refused(
        client,
        admin,
        product,
        {f"fe1.out.{kind}.eq": x, f"nf2.fe1.out.{kind}~qty.gte": "4"},
    )
    assert "같은 관계 묶음 1" in said and "제외 묶음 2" in said


def test_틀린_선의_칸과_묶음은_무엇이_틀렸는지_말한다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind = world["product"], world["kind"]
    for params, said in (
        ({f"f.out.{kind}~nope.eq": "1"}, "nope"),
        ({f"f.out.{kind}~qty.label.eq": "1"}, "주소 끝에 하나만"),
        ({f"f.out.{kind}~qty.contains": "1"}, "contains"),
        ({f"f.out.{kind}~qty.eq": "많이"}, "숫자"),
        ({f"f.out.{kind}~valid_from.gte": "3월"}, "YYYY-MM-DD"),
        ({"f.ref.nope~qty.eq": "1"}, "관계 속성이 없습니다"),
        ({f"fe0.out.{kind}.eq": "x"}, "1 부터"),
        ({f"fe1.nf1.out.{kind}.eq": "x"}, "제외 묶음이 앞에"),
        ({"fe1.label.eq": "P1"}, "관계 걸음을 지나는 주소가 아닙니다"),
        ({f"fe1.out.{kind}.empty": ""}, "비어 있음"),
    ):
        got = client.get(f"/api/objects/{product}", params=params, headers=admin.headers)
        assert got.status_code == 422, (params, got.text)
        assert said in got.json()["error"]["message"], (params, got.json())
    # 「~」 를 모르는 칸 · 부품 쪽의 참조 칸 너머에는 선이 없다.
    got = client.get(
        f"/api/objects/{world['part']}",
        params={"f.ref.vendor~qty.eq": "1"},
        headers=admin.headers,
    )
    assert got.status_code == 422 and "참조 칸(vendor)" in got.json()["error"]["message"]


def test_고르개가_선의_칸_주소를_낸다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind = world["product"], world["kind"]
    fields = client.get(f"/api/objects/{product}/fields", headers=admin.headers)
    assert fields.status_code == 200, fields.text
    edge = {one["field"]: one for one in fields.json() if one["edge"]}
    assert set(edge) >= {
        f"out.{kind}~qty",
        f"out.{kind}~opts",
        f"out.{kind}~memo",
        f"out.{kind}~valid_from",
        f"out.{kind}~valid_to",
    }
    assert edge[f"out.{kind}~qty"]["label"] == "구성품 관계 › 수량"
    assert edge[f"out.{kind}~qty"]["data_type"] == "number"
    assert edge[f"out.{kind}~opts"]["enum_options"] == ["A", "B", "C"]
    assert edge[f"out.{kind}~valid_from"]["data_type"] == "date"
    # 같은 제목(그 관계 걸음) 아래 — 상대의 칸과 함께.
    assert edge[f"out.{kind}~qty"]["heading"] == "관계 · 구성품 (부품)"
    # 통계 기준에도 선다.
    summary = client.get(
        f"/api/objects/{product}/summary",
        params={"group_by": "status"},
        headers=admin.headers,
    )
    options = {one["field"] for one in summary.json()["group_options"]}
    assert f"out.{kind}~qty" in options and f"out.{kind}~valid_from" in options


def test_통계_기준과_세부_기준이_같은_선을_본다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind = world["product"], world["kind"]
    got = client.get(
        f"/api/objects/{product}/summary",
        params={"group_by": f"out.{kind}~qty", "order": "key"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    body = got.json()
    # 한 제품이 선마다 그 막대에 든다(겹침) — 제품마다 한 번.
    assert {one["key"]: one["count"] for one in body["buckets"]} == {
        "1": 2,
        "2": 2,
        "4": 1,
        "5": 1,
        None: 1,
    }
    assert body["overlap"] is True
    # 상대의 이름 x 선의 수량 — 같은 걸음은 한 번만 붙여 **같은 선**의 수량이다.
    got = client.get(
        f"/api/objects/{product}/summary",
        params={"group_by": f"out.{kind}.label", "split_by": f"out.{kind}~qty"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    cells = {
        (bucket["label"], part["key"]): part["count"]
        for bucket in got.json()["buckets"]
        for part in bucket["parts"]
    }
    assert cells == {
        ("X", "1"): 1,
        ("X", "4"): 1,
        ("X", "2"): 1,
        ("Y", "5"): 1,
        ("Y", "2"): 1,
        ("Z", "1"): 1,
        ("(비어 있음)", None): 1,  # P5 — 이어진 부품이 없다
    }, cells
    # 막대의 조건으로 돌아가면 같은 수.
    assert _names(client, admin, product, {f"f.out.{kind}~qty.eq": "2"}) == ["P3", "P4"]
    # 시작일 기준 — 기간 단위로 묶는다.
    got = client.get(
        f"/api/objects/{product}/summary",
        params={"group_by": f"out.{kind}~valid_from", "grain": "month"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    months = {one["key"]: one["count"] for one in got.json()["buckets"]}
    assert months["2026-03-01"] == 1


def test_저장된_뷰가_묶음을_담고_예전_뷰는_그대로다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind, x = world["product"], world["kind"], world["parts"]["X"]
    made = client.post(
        f"/api/objects/{product}/views",
        json={
            "name": "X 4개 이상",
            "query": {
                "q": "",
                "status": None,
                "conditions": [
                    {"field": f"out.{kind}", "op": "eq", "value": x, "edge": 1},
                    {"field": f"out.{kind}~qty", "op": "gte", "value": "4", "edge": 1},
                    {"field": "launched", "op": "gte", "value": "2026-01-01"},
                ],
            },
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    saved = made.json()["query"]["conditions"]
    assert [one["edge"] for one in saved] == [1, 1, 0]
    # 주소로 돌리면 같은 목록(화면이 뷰를 여는 길).
    params = {
        (f"fe{one['edge']}." if one["edge"] else "f.") + f"{one['field']}.{one['op']}": one[
            "value"
        ]
        for one in saved
    }
    assert _names(client, admin, product, params) == ["P2"]
    # 틀린 선의 칸은 저장 전에 거절한다.
    bad = client.post(
        f"/api/objects/{product}/views",
        json={
            "name": "틀림",
            "query": {
                "q": "",
                "status": None,
                "conditions": [{"field": f"out.{kind}~nope", "op": "eq", "value": "1"}],
            },
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422 and "nope" in bad.json()["error"]["message"]
    # 예전 뷰(묶음 번호 없음)는 그대로 — 저장된 모양에 edge 가 없어도 0 으로 읽는다.
    old = client.post(
        f"/api/objects/{product}/views",
        json={
            "name": "예전",
            "query": {
                "q": "",
                "status": None,
                "conditions": [
                    {"field": f"out.{kind}", "op": "eq", "value": x},
                    {"field": f"out.{kind}.label", "op": "eq", "value": "X", "exclude": 1},
                ],
            },
        },
        headers=admin.headers,
    )
    assert old.status_code == 201, old.text
    assert [one["edge"] for one in old.json()["query"]["conditions"]] == [0, 0]


def test_내보내기도_같은_묶음으로_거른다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    product, kind, x = world["product"], world["kind"], world["parts"]["X"]
    got = export_file(
        client,
        admin,
        f"{product}/export",
        {f"fe1.out.{kind}.eq": x, f"fe1.out.{kind}~qty.gte": "4"},
    )
    rows = list(csv.DictReader(io.StringIO(got.content.decode("utf-8-sig"))))
    assert [one["label"] for one in rows] == ["P2"]


# --- 지표 -------------------------------------------------------------------------


def test_지표가_묶음으로_거르고_증분이_선의_칸_변경을_잡는다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """선의 칸을 고쳐도 제품(원천)의 시각은 그대로다 — 증분이 그 선의 `updated_at` 으로 보고
    전량으로 가야 한다. 놓치면 「X 가 4개 이상」 인 제품 수가 낡은 채 남는다."""
    from tests.api.test_metric_incremental import _recount
    from tests.api.test_metrics import _define, _listed, _read

    product, kind, x = world["product"], world["kind"], world["parts"]["X"]
    spec = {
        "measure": "count",
        "time": {"address": "properties.launched", "grain": "month"},
        "filters": [
            {"field": f"out.{kind}", "op": "eq", "value": x, "edge": 1},
            {"field": f"out.{kind}~qty", "op": "gte", "value": "4", "edge": 1},
        ],
        "dimensions": [
            {
                "name": "kind",
                "label": "구성",
                "rules": [
                    {
                        "label": "Y 다량",
                        "when": [
                            {
                                "field": f"out.{kind}.label",
                                "op": "eq",
                                "value": "Y",
                                "edge": 1,
                            },
                            {"field": f"out.{kind}~qty", "op": "gte", "value": "4", "edge": 1},
                        ],
                    },
                    {
                        "label": "옵션 B",
                        "when": [{"field": f"out.{kind}~opts", "op": "eq", "value": "B"}],
                    },
                ],
                "else": "기타",
            }
        ],
    }
    # 계산의 워터마크가 심은 선보다 뒤에 찍혀야 다음 계산이 증분이다 — 시계가 뒤로 튀면 앞에
    # 찍혀 「관계가 바뀌었다」 로 전량에 갔다(전체 시험에서 가끔).
    clock_settled()
    metric = _define(client, admin, source=product, spec=spec, label="X 4개 이상")
    slug = metric["slug"]
    table = _read(client, admin, slug, dims="kind")
    assert table["total_count"] == 1  # P2 — P1 은 X 가 1개
    assert {one["labels"]["kind"]: one["count"] for one in table["cells"]} == {"옵션 B": 1}
    for cell in table["cells"]:
        assert cell["drill"]["partial"] == [], cell["drill"]
        assert _listed(client, admin, product, cell["drill"]["params"]) == cell["count"]
    # 저장된 정의 — 묶지 않은 조건은 예전 모양 그대로(edge 가 붙지 않는다 → 지문이 안 바뀐다).
    saved = client.get(f"/api/metrics/{slug}", headers=admin.headers).json()["spec"]
    assert saved["filters"][0]["edge"] == 1
    assert "edge" not in saved["dimensions"][0]["rules"][1]["when"][0]

    assert _recount(client, admin, slug)["mode"] == "incremental"
    # P1 의 X 선 수량을 1 → 4 로 — 제품 객체는 안 바뀐다.
    edge = world["edges"]["P1-X"]
    got = client.patch(
        f"/api/objects/{product}/{world['products']['P1']}/relations/{edge}",
        json={"properties": {"qty": 4}},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    # 이 계산의 워터마크도 고친 선 뒤에 — 그래야 마지막 「다시 증분」 이 흔들리지 않는다.
    clock_settled()
    run = _recount(client, admin, slug)
    assert run["mode"] == "full" and "관계가 바뀌었다" in run["note"], run
    table = _read(client, admin, slug, dims="kind")
    assert table["total_count"] == 2
    # P1 은 Y 가 5개 — 「Y 다량」(같은 선의 Y 와 수량) 규칙이 먼저 잡는다.
    counts = {one["labels"]["kind"]: one["count"] for one in table["cells"]}
    assert counts == {"Y 다량": 1, "옵션 B": 1}
    for cell in table["cells"]:
        assert _listed(client, admin, product, cell["drill"]["params"]) == cell["count"]
    # 아무것도 안 바뀌면 다시 증분.
    assert _recount(client, admin, slug)["mode"] == "incremental"


# --- 위 전부 · 이어진 것의 개수 ---------------------------------------------------------


def test_위_전부는_값의_트리로_올라간다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """「이 부품을 담은 제품」 — 부품마다 「어디에 쓰였나」 를 따로 부르지 않고 조건 하나로.
    트리는 값(부품)의 타입의 것이라 제품 타입에는 트리가 없어도 된다."""
    product, part, kind, parts = world["product"], world["part"], world["kind"], world["parts"]
    x, y = parts["X"], parts["Y"]
    # 트리가 없으면 말한다(조용히 0건이 아니다).
    assert "위 전부" in _refused(client, admin, product, {"f.id.above": x})
    tree = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"tree": {"relation": kind, "parent": "src"}}},
        headers=admin.headers,
    )
    assert tree.status_code == 200, tree.text
    assert _names(client, admin, product, {"f.id.above": x}) == ["P1", "P2", "P4"]
    assert _names(client, admin, product, {"f.id.above": f"{x}|{y}"}) == [
        "P1",
        "P2",
        "P3",
        "P4",
    ]
    # 자기 자신도 든다(「하위 전체」 의 거울) — 부품 목록이면 X 그 자체.
    assert _names(client, admin, part, {"f.id.above": x}) == ["X"]
    # 다른 조건과 AND — X 를 담은 것 중 Y 도 담은 것.
    assert _names(client, admin, product, {"f.id.above": x, f"f.out.{kind}.eq": y}) == ["P1"]


def test_이어진_것의_개수로_거른다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """「공급사가 하나뿐인 부품」 「부품이 둘 이상인 제품」 — 하나씩 열어 세지 않게. 서로 다른
    상대를 센다(P4 는 X 와의 선이 기간 둘이지만 X 하나)."""
    product, part, vendor, kind = (
        world["product"],
        world["part"],
        world["vendor"],
        world["kind"],
    )
    rel = f"out.{kind}"
    assert _names(client, admin, product, {f"f.{rel}.count_eq": "1"}) == ["P3", "P4"]
    assert _names(client, admin, product, {f"f.{rel}.count_gte": "2"}) == ["P1", "P2"]
    assert _names(client, admin, product, {f"f.{rel}.count_eq": "0"}) == ["P5"]
    assert _names(client, admin, product, {f"f.{rel}.count_lte": "1"}) == ["P3", "P4", "P5"]
    # 나를 가리키는 것의 수 — 부품 둘(X · Z)이 가리키는 한국 업체.
    back = f"in.{part}:vendor"
    assert _names(client, admin, vendor, {f"f.{back}.count_gte": "2"}) == ["한국 업체"]
    # 참조 칸 자체도.
    assert _names(client, admin, part, {"f.vendor.count_eq": "1"}) == ["X", "Y", "Z"]

    # 같은 관계 묶음 안이면 **묶음의 다른 조건에 맞는 것만** 센다 — 한국 업체 부품이 둘 이상.
    korean = {f"fe1.{rel}.count_gte": "2", f"fe1.{rel}.ref.vendor.country.eq": "KR"}
    assert _names(client, admin, product, korean) == ["P2"]
    # 나를 가리키는 것으로 묶으면 같은 기록 하나 — 「Z 라는 부품」 이 하나 가리키는 업체.
    named = {f"fe1.{back}.count_eq": "1", f"fe1.{back}.label.eq": "Z"}
    assert _names(client, admin, vendor, named) == ["한국 업체"]
    # 제외 묶음과도 — 부품이 둘 이상인 제품은 뺀다.
    assert _names(client, admin, product, {f"nf1.{rel}.count_gte": "2"}) == ["P3", "P4", "P5"]

    assert "0 이상의 정수" in _refused(client, admin, product, {f"f.{rel}.count_eq": "-1"})
    assert "0 이상의 정수" in _refused(client, admin, product, {f"f.{rel}.count_gte": "둘"})
    beyond = {f"fe1.{rel}.label.count_eq": "1", f"fe1.{rel}.eq": world["parts"]["X"]}
    assert "count_eq" in _refused(client, admin, product, beyond)
    # 개수는 모양이 다르다 — 글자 칸에는 못 건다.
    assert "count_eq" in _refused(client, admin, vendor, {"f.country.count_eq": "1"})


# --- 부분 목록 · 긴 읽기 ------------------------------------------------------------------


def test_다른_목록의_결과를_조건의_값으로_쓴다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """「한국 업체 부품이 든 제품」 · 「1 · 2월 출시 제품에 든 부품 전부」 — 앞 목록의 id 를
    받아 다시 주소에 넣지 않는다(수천 개면 주소가 4만 자를 넘어 끊긴다)."""
    product, part, vendor, kind = (
        world["product"],
        world["part"],
        world["vendor"],
        world["kind"],
    )
    korean = {"s1.type": part, "s1.f.ref.vendor.country.eq": "KR"}
    assert _names(client, admin, product, {f"f.out.{kind}.in": "@1", **korean}) == [
        "P1",
        "P2",
        "P4",
    ]
    # 나를 가리키는 것 · 목록의 자신에도.
    named_x = {"s1.type": part, "s1.f.label.eq": "X"}
    assert _names(client, admin, vendor, {f"f.in.{part}:vendor.in": "@1", **named_x}) == [
        "한국 업체"
    ]
    assert _names(client, admin, part, {"f.id.in": "@1", **korean}) == ["X", "Z"]
    # 하위 전체 · 위 전부의 값으로 — 트리는 부품의 구성.
    tree = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"tree": {"relation": kind, "parent": "src"}}},
        headers=admin.headers,
    )
    assert tree.status_code == 200, tree.text
    early = {"s1.type": product, "s1.f.launched.lte": "2026-02-28"}
    assert _names(client, admin, part, {"f.id.under": "@1", **early}) == ["X", "Y", "Z"]
    assert _names(client, admin, product, {"f.id.above": "@1", **korean}) == ["P1", "P2", "P4"]
    # 빈 부분 목록은 아무것도 안 가리킨다(전부가 아니다).
    nobody = {"s1.type": part, "s1.f.label.eq": "없는 이름"}
    assert _names(client, admin, product, {f"f.out.{kind}.in": "@1", **nobody}) == []

    # 통계도 같은 거르기 — 화면은 부분 목록을 아직 못 받는다고 적는다.
    counted = client.get(
        f"/api/objects/{product}/summary",
        params={"group_by": "status", f"f.out.{kind}.in": "@1", **korean},
        headers=admin.headers,
    )
    assert counted.status_code == 200, counted.text
    assert counted.json()["total"] == 3
    assert "s1" in counted.json()["link_missing"]
    assert f"f.out.{kind}.in" in counted.json()["link_missing"]

    # 틀린 모양은 무엇이 틀렸는지 말한다.
    assert "s1.type" in _refused(client, admin, product, {f"f.out.{kind}.in": "@1"})
    assert "부분 목록 @1" in _refused(
        client, admin, product, {f"f.out.{kind}.in": "@1", "s1.type": "없는타입"}
    )
    assert "in · under · above" in _refused(
        client, admin, product, {f"f.out.{kind}.eq": "@1", **korean}
    )
    nested = {f"f.out.{kind}.in": "@1", "s1.type": part, "s1.s2.type": vendor}
    assert "부분 목록 안에" in _refused(client, admin, product, nested)


def test_긴_읽기는_POST_로_같은_경로를_탄다(
    client: TestClient, admin: Signed, world: dict[str, Any]
) -> None:
    """id 수천 개를 넣은 조건 — 주소로는 프록시가 끊는다(nginx 8KB). 같은 몸통을 POST 로 보내면
    같은 GET 경로 · 같은 거르기다. 값 여럿은 자리표 하나(배열)로 넘긴다 — 수천 개도 된다."""
    from urllib.parse import urlencode

    product, kind, parts = world["product"], world["kind"], world["parts"]
    strangers = [str(uuid.uuid4()) for _ in range(5000)]
    wanted = "|".join([*strangers, parts["X"]])
    body = urlencode({f"f.out.{kind}.in": wanted})
    assert len(body) > 100_000
    got = client.post(
        f"/api/objects/{product}",
        content=body,
        headers={
            **admin.headers,
            "X-HTTP-Method-Override": "GET",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert got.status_code == 200, got.text
    assert sorted(one["label"] for one in got.json()["items"]) == ["P1", "P2", "P4"]
    # 주소의 쿼리와 몸통이 함께 — 주소에 limit, 몸통에 조건.
    paged = client.post(
        f"/api/objects/{product}/summary?group_by=status",
        content=body,
        headers={
            **admin.headers,
            "X-HTTP-Method-Override": "GET",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert paged.status_code == 200 and paged.json()["total"] == 3, paged.text
    # 몸통 모양이 다르면 415 — 조용히 조건 없는 목록을 주지 않는다.
    wrong = client.post(
        f"/api/objects/{product}",
        json={f"f.out.{kind}.in": wanted},
        headers={**admin.headers, "X-HTTP-Method-Override": "GET"},
    )
    assert wrong.status_code == 415
    assert "x-www-form-urlencoded" in wrong.json()["error"]["message"]
