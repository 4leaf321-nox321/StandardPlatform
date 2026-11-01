"""목록에 이어진 칸 싣기 — `columns=<주소>`(ADR 0043).

    제품 ─구성(bom, 유효 기간 · 수량)→ 부품 ─공급사(참조)→ 업체(국가)
    서비스 기록 ─제품(참조)→ 제품          P1 에 기록 25건(상한 20 을 넘는다) · P2 에 3건

주소는 조건과 같은 문법 · 같은 해석기다(넷까지 · 선의 칸 · 기준일). 칸마다 20개까지 싣고 더
있으면 `more` — 줄마다 `LIMIT 21` 이라 다 세지 않는다. 볼 수 없는 객체는 걷지 않고(조건과
같다), 끝의 값이 객체를 가리키면 이름은 목록의 참조 칸과 같은 규칙으로 가린다.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, export_file, import_file
from tests.api.test_edge_fields import FEB, MAR, _bom_kind
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type

HIDDEN = "(볼 수 없는 객체)"


def _world(
    client: TestClient, admin: Signed, *, hidden_in: Workspace | None = None
) -> dict[str, Any]:
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
    kind = _bom_kind(client, admin, product, part)
    svc = _make_type(client, admin, label="서비스 기록", usage="log")
    _make_property(
        client,
        admin,
        svc,
        key="product",
        label="제품",
        data_type="object_ref",
        ref_type_slug=product,
    )
    _make_property(client, admin, svc, key="symptom", label="증상", data_type="text")
    away = {"workspace_slug": hidden_in.slug} if hidden_in is not None else {}
    kr = _make_object(client, admin, vendor, label="한국 업체", properties={"country": "KR"})
    us = _make_object(
        client, admin, vendor, label="미국 업체", properties={"country": "US"}, **away
    )
    parts = {
        "X": _make_object(client, admin, part, label="X", properties={"vendor": kr["id"]}),
        "Y": _make_object(client, admin, part, label="Y", properties={"vendor": us["id"]}),
        "H": _make_object(client, admin, part, label="숨은 부품", **away),
    }
    products = {
        name: _make_object(client, admin, product, label=name)
        for name in ("P1", "P2", "P3", "P4")
    }
    for src, dst, props, period in (
        ("P1", "X", {"qty": 1}, {}),
        ("P1", "Y", {"qty": 5}, {}),
        ("P1", "H", {"qty": 9}, {}),
        ("P2", "X", {"qty": 4}, {}),
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
    for n in range(25):
        _make_object(
            client,
            admin,
            svc,
            label=f"C{n:02d}",
            properties={"product": products["P1"]["id"], "symptom": f"S{n % 3}"},
        )
    for n in range(3):
        _make_object(
            client,
            admin,
            svc,
            label=f"D{n}",
            properties={"product": products["P2"]["id"], "symptom": "S9"},
        )
    return {
        "product": product,
        "part": part,
        "svc": svc,
        "kind": kind,
        "products": {name: one["id"] for name, one in products.items()},
        "parts": {name: one["id"] for name, one in parts.items()},
        "vendors": {"KR": kr["id"], "US": us["id"]},
    }


def _page(
    client: TestClient, who: Signed, slug: str, columns: list[str], **params: Any
) -> dict[str, Any]:
    got = client.get(
        f"/api/objects/{slug}",
        params={"columns": columns, "limit": 200, **params},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _cells(page: dict[str, Any], ids: dict[str, str]) -> dict[str, dict[str, Any]]:
    """{줄 이름: {주소: {labels, more}}}."""
    names = {value: key for key, value in ids.items()}
    return {
        names.get(object_id, object_id): {
            address: {
                "labels": [one["label"] for one in cell["values"]],
                "more": cell["more"],
            }
            for address, cell in by_address.items()
        }
        for object_id, by_address in page["linked"].items()
    }


def test_줄마다_이어진_칸의_값을_싣는다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    kind = w["kind"]
    columns = [
        f"out.{kind}.label",
        f"out.{kind}~qty",
        f"out.{kind}.ref.vendor.country",
        f"out.{kind}",
    ]
    page = _page(client, admin, w["product"], columns)
    heads = {one["field"]: one for one in page["linked_columns"]}
    assert [one["field"] for one in page["linked_columns"]] == columns
    assert heads[f"out.{kind}~qty"]["label"] == "구성품 관계 › 수량"
    assert heads[f"out.{kind}"]["data_type"] == "object"
    assert heads[f"out.{kind}.label"]["multi"] is True
    cells = _cells(page, w["products"])
    assert cells["P1"][f"out.{kind}.label"] == {
        "labels": ["X", "Y", "숨은 부품"],
        "more": False,
    }
    # 선의 칸 — 그 선의 수량(숫자는 숫자로).
    qty = page["linked"][w["products"]["P1"]][f"out.{kind}~qty"]["values"]
    assert sorted(one["value"] for one in qty) == [1, 5, 9]
    assert cells["P1"][f"out.{kind}.ref.vendor.country"]["labels"] == ["KR", "US"]
    # 관계로 이어진 것 자체 — 값은 상대의 id, 말은 이름.
    whole = page["linked"][w["products"]["P2"]][f"out.{kind}"]["values"]
    assert whole == [{"value": w["parts"]["X"], "label": "X"}]
    # 이어진 것이 없는 줄은 칸이 없다(빈 칸).
    assert "P3" not in cells


def test_많이_이어진_칸은_상한까지만_싣고_더_있음을_말한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    back = f"in.{w['svc']}:product"
    page = _page(client, admin, w["product"], [back, f"{back}.symptom"])
    cells = _cells(page, w["products"])
    assert len(cells["P1"][back]["labels"]) == 20 and cells["P1"][back]["more"] is True
    assert cells["P2"][back] == {"labels": ["D0", "D1", "D2"], "more": False}
    # 이어진 것마다 값 하나 — 같은 값이 여러 번 실린다(몇 종류인지는 통계의 몫).
    assert cells["P2"][f"{back}.symptom"] == {"labels": ["S9", "S9", "S9"], "more": False}
    assert len(cells["P1"][f"{back}.symptom"]["labels"]) == 20
    assert cells["P1"][f"{back}.symptom"]["more"] is True


def test_이어진_칸은_기준일을_따른다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    column = f"out.{w['kind']}~qty"
    today = _cells(_page(client, admin, w["product"], [column]), w["products"])
    assert today["P4"][column]["labels"] == ["2"]
    feb = _cells(_page(client, admin, w["product"], [column], as_of=FEB), w["products"])
    assert feb["P4"][column]["labels"] == ["6"]


@pytest.mark.usefixtures("department_scope")
def test_볼_수_없는_상대의_값은_새지_않는다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    other = Workspace(slug=f"o-{uuid.uuid4().hex[:8]}", name="다른 부서")
    db.add(other)
    db.commit()
    w = _world(client, admin, hidden_in=other)
    kind = w["kind"]
    columns = [
        f"out.{kind}.label",
        f"out.{kind}~qty",
        f"out.{kind}.ref.vendor.country",
        f"out.{kind}.vendor",
    ]
    mine = _cells(_page(client, member, w["product"], columns), w["products"])
    # 볼 수 없는 부품(숨은 부품, 9개)은 걷지 않는다 — 이름도 선의 수량도 없다.
    assert mine["P1"][f"out.{kind}.label"]["labels"] == ["X", "Y"]
    assert mine["P1"][f"out.{kind}~qty"]["labels"] == ["1", "5"]
    # 볼 수 없는 업체(미국 업체)의 국가는 안 실린다.
    assert mine["P1"][f"out.{kind}.ref.vendor.country"]["labels"] == ["KR"]
    # 끝의 값이 객체를 가리키면 목록의 참조 칸처럼 — 볼 수 없으면 이름을 가린다.
    assert mine["P1"][f"out.{kind}.vendor"]["labels"] == [HIDDEN, "한국 업체"]
    # 시스템 관리자는 전부 본다.
    every = _cells(_page(client, admin, w["product"], columns), w["products"])
    assert every["P1"][f"out.{kind}.label"]["labels"] == ["X", "Y", "숨은 부품"]
    assert every["P1"][f"out.{kind}.vendor"]["labels"] == ["미국 업체", "한국 업체"]
    # 내보내기는 가린 이름조차 싣지 않는다.
    got = export_file(
        client, member, f"{w['product']}/export", {"columns": [f"out.{kind}.vendor"]}
    )
    rows = {
        one["label"]: one
        for one in csv.DictReader(io.StringIO(got.content.decode("utf-8-sig")))
    }
    assert rows["P1"][f"out.{kind}.vendor"] == "한국 업체"


def test_모르는_주소는_무엇이_틀렸는지_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    for columns, said in (
        (["out.nope.label"], "out.nope"),
        (["label"], "ref.<참조 칸>"),
        ([f"out.{w['kind']}~nope"], "nope"),
        ([f"out.{w['kind']}.label.x"], "칸 이름이 아닙니다"),
        ([f"in.{w['svc']}:product.c{n}" for n in range(11)], "10개까지"),
    ):
        got = client.get(
            f"/api/objects/{w['product']}",
            params={"columns": columns},
            headers=admin.headers,
        )
        assert got.status_code == 422, (columns, got.text)
        assert said in got.json()["error"]["message"], (columns, got.json())
    # 내보내기도 맡기기 전에 거절한다.
    got = client.post(
        f"/api/objects/{w['product']}/export",
        params={"columns": ["out.nope.label"]},
        headers=admin.headers,
    )
    assert got.status_code == 422, got.text


def test_내보내기도_이어진_칸을_싣고_다시_올릴_수_있다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    kind, back = w["kind"], f"in.{w['svc']}:product"
    got = export_file(
        client,
        admin,
        f"{w['product']}/export",
        {"columns": [f"out.{kind}.label", back]},
    )
    text = got.content.decode("utf-8-sig")
    rows = {one["label"]: one for one in csv.DictReader(io.StringIO(text))}
    assert rows["P1"][f"out.{kind}.label"] == "X | Y | 숨은 부품"
    assert rows["P2"][back] == "D0 | D1 | D2"
    # 상한을 넘으면 끝에 「…」 — 정확한 수는 세지 않았다.
    assert rows["P1"][back].endswith(" | …") and rows["P1"][back].count(" | ") == 20
    assert rows["P3"][back] == ""
    as_json = export_file(
        client,
        admin,
        f"{w['product']}/export",
        {"columns": [back], "format": "json"},
    ).json()["rows"]
    p1 = next(one for one in as_json if one["label"] == "P1")
    assert len(p1[back]["values"]) == 20 and p1[back]["more"] is True
    # 내보낸 파일을 그대로 올리면 이어진 칸은 건너뛴다(읽기 전용 열).
    plan = import_file(client, admin, w["product"], text)
    assert plan["errors"] == [], plan["errors"]
