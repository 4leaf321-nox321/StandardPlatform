"""해소와 진단 — **AI 가 짐작하지 않게 하는 두 자리.**

이름이 하나로 안 정해지면 정해지지 않았다고 말하고, 0건이면 왜 0건인지 말한다.
둘 다 「그럴듯한 답」 보다 「모른다」 가 나은 자리다 — 그럴듯한 답은 그대로 저장된다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _resolve(client: TestClient, who: Signed, type_slug: str, name: str) -> Any:
    got = client.get(
        f"/api/objects/{type_slug}/resolve", params={"name": name}, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return got.json()


def _diagnose(client: TestClient, who: Signed, type_slug: str, params: dict[str, Any]) -> Any:
    got = client.get(f"/api/objects/{type_slug}/diagnose", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return got.json()


def test_식별자와_별칭과_이름은_하나로_정해진다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys", key="V-001")
    client.put(
        f"/api/objects/{vendor}/{ansys['id']}/aliases",
        json={"aliases": ["앤시스"]},
        headers=admin.headers,
    )

    for name, how in (("V-001", "key"), ("앤시스", "alias"), ("ansys", "label")):
        got = _resolve(client, admin, vendor, name)
        assert got["match"] == "exact", (name, got)
        assert got["object"]["id"] == ansys["id"]
        assert got["object"]["matched_by"] == how


def test_이름이_여럿에_맞으면_후보만_주고_고르지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """**AI 는 첫 줄을 집는다 — 틀린 줄도 첫 줄이면 집는다.** 그래서 목록이 아니라
    「정해지지 않았다」 를 준다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    _make_object(client, admin, vendor, label="한국소재")
    _make_object(client, admin, vendor, label="한국소재부품")

    got = _resolve(client, admin, vendor, "한국소재")
    assert got["match"] == "exact"  # 이름이 그대로 같은 것은 하나뿐이다

    partial = _resolve(client, admin, vendor, "한국")
    assert partial["match"] == "candidates"
    assert {one["label"] for one in partial["candidates"]} == {"한국소재", "한국소재부품"}
    assert partial["object"] is None
    assert "사람에게" in partial["hint"]


def test_포함으로_하나만_걸려도_확정이_아니다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    _make_object(client, admin, vendor, label="한국소재부품")

    got = _resolve(client, admin, vendor, "소재")
    assert got["match"] == "candidates"
    assert got["candidates"][0]["matched_by"] == "contains"


def test_없으면_없다고_말한다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    _make_object(client, admin, vendor, label="Ansys")

    got = _resolve(client, admin, vendor, "없는회사")
    assert got["match"] == "none"
    assert got["candidates"] == []
    assert "짐작" in got["hint"]


def test_빈_타입과_좁은_조건은_다른_이유로_0건이다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")

    empty = _diagnose(client, admin, part, {})
    assert empty["reason"] == "empty_type"
    assert empty["type_total"] == 0
    assert empty["next_steps"]

    _make_object(client, admin, part, label="볼트", properties={"grade": "A"})
    _make_object(client, admin, part, label="너트")  # 등급이 비어 있다

    narrow = _diagnose(client, admin, part, {"f.grade.eq": "Z"})
    assert narrow["reason"] == "filters"
    assert narrow["type_total"] == 2
    effect = narrow["filters"][0]
    assert effect["remaining"] == 2
    # 「조건에 안 맞아 빠진 것」 과 「값이 없어 빠진 것」 은 다른 일이다.
    assert effect["unknown"] == 1


def test_결과가_있으면_진단은_있다고만_말한다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_object(client, admin, part, label="볼트")
    got = _diagnose(client, admin, part, {})
    assert got["reason"] == "has_rows"
    assert got["total"] == 1


def test_부서_밖이라_안_보이는_것은_없는_것과_다르다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """빈 목록을 「없다」 로 읽으면 사람은 없는 것을 새로 만든다 — 그러면 같은 것이 둘이
    된다. 권한 때문에 안 보이는 것은 그렇게 말해야 한다."""
    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()

    part = _make_type(client, admin, label="부품", key_policy="optional")
    made = client.post(
        f"/api/objects/{part}",
        json={"workspace_slug": other.slug, "label": "볼트"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    got = _diagnose(client, member, part, {})
    assert got["reason"] == "not_visible"
    assert got["hidden"] == 1
    assert "권한" in got["message"]


def test_다른_타입의_칸으로_건_조건도_진단이_선다(client: TestClient, admin: Signed) -> None:
    """`ref.vendor.country` 처럼 **다른 타입의 칸**으로 건 조건도 같이 진단한다 —
    「공급사를 아직 안 적은 부품」 과 「공급사가 독일이 아닌 부품」 은 다른 일이다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
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
    made = _make_object(client, admin, vendor, label="Ansys", properties={"country": "미국"})
    _make_object(client, admin, part, label="볼트", properties={"vendor": made["id"]})
    _make_object(client, admin, part, label="너트")  # 공급사를 아직 안 적었다

    got = _diagnose(client, admin, part, {"f.ref.vendor.country.eq": "독일"})
    assert got["reason"] == "filters"
    assert got["filters"][0]["remaining"] == 2
    assert got["filters"][0]["unknown"] == 1
