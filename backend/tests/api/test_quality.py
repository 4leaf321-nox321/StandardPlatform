"""데이터 품질 — **넷을 맞게 세나, 볼 수 있는 것만 세나, 홈에 뜨나.**"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _report(client: TestClient, who: Signed, kind: str | None = None) -> list[dict[str, Any]]:
    params = {"kind": kind} if kind else {}
    response = client.get("/api/objects/quality/report", params=params, headers=who.headers)
    assert response.status_code == 200, response.text
    return list(response.json()["findings"])


def _find(findings: list[dict[str, Any]], kind: str, type_slug: str) -> dict[str, Any] | None:
    return next(
        (one for one in findings if one["kind"] == kind and one["type_slug"] == type_slug),
        None,
    )


def test_필수값이_빈_객체를_센다(client: TestClient, admin: Signed) -> None:
    """필수가 된 뒤에도 안 채운 옛 것 — 만들 때는 필수가 아니었다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    old = _make_object(client, admin, part, key="P-1", label="옛것")
    _make_object(client, admin, part, key="P-2", label="채운것", properties={"weight": 1})
    # 이제 무게를 필수로.
    patched = client.patch(
        f"/api/ontology/types/{part}/properties/weight",
        json={"key": "weight", "label": "무게", "data_type": "number", "required": True},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text

    found = _find(_report(client, admin, "missing_required"), "missing_required", part)
    assert found and found["count"] == 1
    assert found["hits"][0]["id"] == old["id"] and "무게" in found["hits"][0]["detail"]


def test_고아는_관계가_걸릴_수_있는_타입에서_센다(client: TestClient, admin: Signed) -> None:
    """관계가 하나도 정의 안 된 타입에서는 전부가 고아라 세지 않는다 — 다만 시험 DB 는
    스위트가 함께 쓰므로(끝점을 안 정한 관계 종류가 있을 수 있음) 그 쪽은 여기서 못 본다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    kind = _make_relation(
        client, admin, "near", label="가까움", src_type_slugs=[part], dst_type_slugs=[part]
    )
    a = _make_object(client, admin, part, key="P-A", label="A")
    b = _make_object(client, admin, part, key="P-B", label="B")
    _make_object(client, admin, part, key="P-C", label="C")
    assert _link(client, admin, part, a["id"], kind, b["id"]).status_code == 201
    found = _find(_report(client, admin, "orphan"), "orphan", part)
    assert found and found["count"] == 1 and found["hits"][0]["label"] == "C"


def test_지워진_것을_가리키는_칸과_이름_같은_것(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    acme = _make_object(client, admin, vendor, label="ACME")
    bolt = _make_object(
        client, admin, part, key="P-1", label="볼트", properties={"vendor": acme["id"]}
    )
    # 참조를 비우고 지운 뒤 다시 가리키게 만들 수는 없으니(검증이 막는다), 「비우고 지우기」 가
    # 아닌 상황 — 곧 옛 데이터 — 을 detach 로 만들 수 없다. 대신 ACME 를 다른 것에 합쳐 옮긴 뒤
    # ... 도 참조가 옮겨진다. 그래서 여기서는 「없는 객체」 uuid 를 API 로 넣을 수 없어
    # 이름 같은 것만 API 로 검증하고, 깨진 참조는 아래 단위 시험이 본다.
    _make_object(client, admin, vendor, label="acme")  # 정규화하면 같다
    _make_object(client, admin, vendor, label="ＡＣＭＥ ")  # noqa: RUF001 — 전각·공백이 소재다
    _make_object(client, admin, vendor, label="OTHER")
    found = _find(_report(client, admin, "duplicate"), "duplicate", vendor)
    assert found and found["count"] == 3
    assert all(hit["detail"] == "같은 이름 3개" for hit in found["hits"])
    assert _find(_report(client, admin, "broken_ref"), "broken_ref", part) is None
    assert bolt


def test_깨진_참조는_지워진_것을_이름으로_말한다(
    client: TestClient, admin: Signed, db: Any
) -> None:
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import update

    from app.modules.objects.models import ObjectInstance

    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    acme = _make_object(client, admin, vendor, label="ACME")
    bolt = _make_object(
        client, admin, part, key="P-1", label="볼트", properties={"vendor": acme["id"]}
    )
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == uuid.UUID(acme["id"]))
        .values(deleted_at=datetime.now(UTC))
    )
    db.commit()
    found = _find(_report(client, admin, "broken_ref"), "broken_ref", part)
    assert found and found["count"] == 1
    assert found["hits"][0]["id"] == bolt["id"]
    assert found["hits"][0]["detail"] == "공급사 → ACME (지워짐)"


def test_볼_수_있는_것만_세고_홈에는_관리자에게만_뜬다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    slug = f"far-{admin.workspace[-6:]}"
    made = client.post(
        "/api/workspaces", json={"slug": slug, "name": "남"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    _make_object(client, admin, vendor, label="같음", workspace_slug=slug)
    _make_object(client, admin, vendor, label="같음", workspace_slug=slug)
    _make_object(client, admin, vendor, label="같음", workspace_slug=manager.workspace)
    # 관리자(전사)는 셋 다, 부서 관리자는 자기 부서 하나뿐이라 「같은 이름」 이 안 선다.
    assert _find(_report(client, admin, "duplicate"), "duplicate", vendor)["count"] == 3  # type: ignore[index]
    assert _find(_report(client, manager, "duplicate"), "duplicate", vendor) is None

    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    assert any(
        one["key"] == "quality_duplicate" and one["link"] == "/quality#duplicate"
        for one in home
    )
    # 멤버는 고칠 수 없으니 홈에 안 뜬다.
    for_member = client.get("/api/server/maintenance", headers=member.headers).json()
    assert not any(one["key"].startswith("quality_") for one in for_member)


def test_서버_화면에_타입_객체_관계_수가_선다(client: TestClient, admin: Signed) -> None:
    response = client.get("/api/server/status", headers=admin.headers)
    assert response.status_code == 200, response.text
    labels = {one["label"] for one in response.json()["counts"]}
    assert {"타입", "객체", "관계"} <= labels
