"""채울 곳 — **어디부터 채우나.**

품질 검사는 「무엇이 나쁜가」 다. 빈 칸은 늘 수천 개라, 그것만으로는 「지금 무엇을 먼저
채워야 하나」 에 답하지 못한다 — 지표가 묶는 칸 · 바깥에 공개한 칸 · 뷰가 거르는 칸이 빈 것과
아무도 안 쓰는 칸이 빈 것은 무게가 다르다(ADR 0025).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.objects import fill
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, _login, _make_user
from tests.api.test_ontology import (
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
    _uniq,
)


def _fill(client: TestClient, who: Signed, **params: Any) -> dict[str, Any]:
    got = client.get("/api/objects/fill-priorities", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _mine(report: dict[str, Any], slug: str) -> list[dict[str, Any]]:
    return [one for one in report["priorities"] if one["type_slug"] == slug]


def test_쓰는_곳이_많은_칸이_위에_서고_무엇이_좋아지는지_말한다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")
    _make_property(client, admin, part, key="memo", label="메모", data_type="text")
    for index in range(4):
        properties = {"grade": "A"} if index < 2 else {"memo": "m"}
        _make_object(client, admin, part, label=f"볼트{index}", properties=properties)
    # 같은 몫(절반)이 비었다 — 등급은 뷰가 거르고, 메모는 아무도 안 쓴다.
    made = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "A 등급",
            "query": {"q": "", "conditions": [{"field": "grade", "op": "eq", "value": "A"}]},
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    report = _fill(client, admin, type=part)
    order = [(one["kind"], one["target"]) for one in _mine(report, part)]
    assert order[:2] == [("field", "grade"), ("field", "memo")]
    grade = _mine(report, part)[0]
    assert grade["missing"] == 2 and grade["total"] == 4
    assert grade["uses"] == ["뷰 1개"] and "뷰 1개" in grade["gain"]
    assert grade["link"] == f"/o/{part}?f.grade.empty="
    memo = _mine(report, part)[1]
    assert memo["uses"] == [] and "쓰는 곳이 없는" in memo["gain"]
    assert grade["score"] > memo["score"]

    (kind,) = report["types"]
    assert kind["type_slug"] == part and kind["objects"] == 4 and kind["estimated"] is False
    assert {one["key"]: one["rate"] for one in kind["fields"]} == {"grade": 0.5, "memo": 0.5}

    # 줄의 주소는 그대로 「빈 것만」 목록이 된다.
    listed = client.get(f"/api/objects/{part}?f.grade.empty=", headers=admin.headers).json()
    assert listed["total"] == 2


def test_필수_칸_코어_공개_지표가_가중을_올린다(client: TestClient, admin: Signed) -> None:
    case = _make_type(client, admin, label="서비스 건", usage="log", core=True)
    _make_property(client, admin, case, key="symptom", label="증상", data_type="text")
    for index in range(3):
        _make_object(
            client,
            admin,
            case,
            label=f"건{index}",
            properties={"symptom": "소음"} if index == 0 else {},
        )
    # 필수가 된 뒤에도 안 채운 옛 것 — 넣을 때만 걸리는 검증은 이것을 못 본다.
    _make_property(
        client, admin, case, key="center", label="센터", data_type="text", required=True
    )
    metric = client.post(
        "/api/metrics",
        params={"recompute": False},
        json={
            "slug": _uniq("m"),
            "label": "증상별 건수",
            "source_type_slug": case,
            "spec": {
                "measure": "count",
                "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
            },
        },
        headers=admin.headers,
    )
    assert metric.status_code == 201, metric.text
    try:
        report = _fill(client, admin, type=case)
    finally:
        slug = metric.json()["metric"]["slug"]
        client.delete(f"/api/metrics/{slug}", headers=admin.headers)

    rows = {one["target"]: one for one in _mine(report, case)}
    assert rows["symptom"]["uses"] == ["지표 「증상별 건수」", "코어 공개"]
    assert "지표 「증상별 건수」" in rows["symptom"]["gain"]
    assert "수신 시스템" in rows["symptom"]["gain"]
    assert rows["center"]["uses"] == ["필수", "코어 공개"]
    assert "수정할 때 거절" in rows["center"]["gain"]
    (kind,) = report["types"]
    assert kind["required_missing"] == 3
    assert kind["no_alias"] is None  # 기록 — 별칭을 세지 않는다
    assert "지표 「증상별 건수」의 원천" in kind["uses"] and "코어 공개" in kind["uses"]


def test_관계는_끝을_정한_종류만_세고_하나인_끝이_무겁다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    vendor = _make_type(client, admin, label="공급사")
    supplied = _make_relation(
        client,
        admin,
        label="공급",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
        cardinality="many_to_one",
    )
    # 끝을 안 정한 관계 — 어느 타입이 그 선을 가져야 하는지 말할 수 없다.
    _make_relation(client, admin, label="관련")
    acme = _make_object(client, admin, vendor, label="ACME")
    linked = _make_object(client, admin, part, label="볼트")
    _make_object(client, admin, part, label="너트")
    # 「와셔」 가 아니다 — 볼트 · 너트 · 와셔가 한 타입에 모두 있으면 표 추론 시험(같은 DB)이
    # 그 열을 이 타입의 참조로 읽는다.
    _make_object(client, admin, part, label="평와셔")
    added = client.post(
        f"/api/objects/{part}/{linked['id']}/relations",
        json={"relation": supplied, "dst_object_id": acme["id"], "evidence_note": "카탈로그"},
        headers=admin.headers,
    )
    assert added.status_code == 201, added.text

    report = _fill(client, admin, type=part)
    (kind,) = report["types"]
    assert [(one["relation"], one["side"], one["missing"]) for one in kind["relations"]] == [
        (supplied, "out", 2)
    ]
    (row,) = [one for one in _mine(report, part) if one["kind"] == "relation"]
    assert row["uses"] == ["개수 제약(하나)"] and row["weight"] == 1 + fill.W_ONE
    assert row["link"] == f"/o/{part}?f.out.{supplied}.empty="
    listed = client.get(
        f"/api/objects/{part}?f.out.{supplied}.empty=", headers=admin.headers
    ).json()
    assert sorted(one["label"] for one in listed["items"]) == ["너트", "평와셔"]

    # 공급사 쪽(받는 쪽)은 「여럿」 이라 무게가 안 붙는다.
    other = _fill(client, admin, type=vendor)
    (back,) = other["types"][0]["relations"]
    assert back["side"] == "in" and back["one"] is False and back["missing"] == 0


def test_객체가_없는_타입과_안_보이는_타입을_가른다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    empty = _make_type(client, admin, label="빈 타입", core=True)
    report = _fill(client, admin, type=empty)
    (row,) = _mine(report, empty)
    assert row["kind"] == "empty_type" and row["link"] == f"/o/{empty}"
    assert "수신 시스템이 빈 목록" in row["gain"]

    # 남의 부서에만 있는 타입 — 비어 있는 것이 아니라 볼 권한이 없는 것이다.
    hidden = _make_type(client, admin, label="남의 것")
    _make_object(client, admin, hidden, label="비밀")
    other = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(other)
    db.commit()
    stranger = _make_user(db, other, label="far", is_system_admin=False, role="member")
    who = Signed(
        email=stranger.email, token=_login(client, stranger.email), workspace=other.slug
    )
    seen = _fill(client, who, type=hidden)
    assert _mine(seen, hidden) == []
    assert seen["types"][0]["objects"] == 0
    assert any("부서 밖이라 안 보이는" in one for one in seen["notes"])


def test_큰_타입은_표본에서_세고_그렇다고_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _make_type(client, admin, label="기록", usage="log")
    _make_property(client, admin, log, key="memo", label="메모", data_type="text")
    for index in range(40):
        _make_object(client, admin, log, label=f"건{index}", properties={"memo": "m"})
    # 0.1% 대신 전부를 훑어 어림하고, 그것이 「크다」 고 보게 문턱을 낮춘다.
    monkeypatch.setattr(fill, "PROBE_FRACTION", 1)
    monkeypatch.setattr(fill, "BIG_ROWS", 10)
    monkeypatch.setattr(fill, "SAMPLE_ROWS", 20)

    report = _fill(client, admin, type=log)
    (kind,) = report["types"]
    assert kind["estimated"] is True and 0 < kind["sample_rows"] < 40
    assert kind["objects"] > 0
    assert any("표본" in one and "기록" in one for one in report["notes"])


def test_없는_타입은_404(client: TestClient, admin: Signed) -> None:
    got = client.get(
        "/api/objects/fill-priorities", params={"type": "nope_x"}, headers=admin.headers
    )
    assert got.status_code == 404


def test_주소가_닿는_칸과_관계를_읽는다() -> None:
    """지표의 주소(`properties.x` · `ref.k.f`)와 뷰 조건의 칸(`x` · `ref.k.f`)을 함께
    읽는다."""
    part = ObjectType(id=uuid.uuid4(), slug="part", label="부품")
    vendor = ObjectType(id=uuid.uuid4(), slug="vendor", label="공급사")
    ref = PropertyDef(
        key="vendor", label="공급사", data_type="object_ref", ref_type_slug="vendor"
    )
    defs = {part.id: {"vendor": ref}}
    types = {"part": part, "vendor": vendor}

    def at(address: str) -> list[tuple[str, uuid.UUID, str, str]]:
        return fill._targets(address, part, defs, types)

    assert at("properties.grade") == [("field", part.id, "grade", "")]
    assert at("grade") == [("field", part.id, "grade", "")]
    assert at("label") == []
    assert at("ref.vendor.country") == [
        ("field", part.id, "vendor", ""),
        ("field", vendor.id, "country", ""),
    ]
    assert at("ref.vendor.properties.country")[1] == ("field", vendor.id, "country", "")
    assert at("out.supplied_by") == [("relation", part.id, "supplied_by", "out")]
    assert at("in.vendor:maker") == [("field", vendor.id, "maker", "")]
    assert fill._metric_addresses(
        {
            "time": {"address": "properties.made"},
            "dimensions": [{"address": "ref.vendor.country"}],
            "filters": [{"field": "grade"}],
            "measure_field": "properties.cost",
        }
    ) == ["properties.made", "ref.vendor.country", "grade", "properties.cost"]
