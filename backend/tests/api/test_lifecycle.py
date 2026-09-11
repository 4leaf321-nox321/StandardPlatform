"""지우기와 합치기.

**누르기 전에 무엇이 걸렸는지 말하나, 고른 대로 하나, 옛 링크가 새 것으로 가나.**
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
from tests.api.conftest import Signed
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """공급사 ACME 를 부품 둘이 속성으로 가리키고, 관계 하나가 걸려 있다."""
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
    _make_property(
        client,
        admin,
        part,
        key="vendors",
        label="공급사들",
        data_type="object_ref",
        ref_type_slug=vendor,
        multi=True,
    )
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    acme = _make_object(client, admin, vendor, label="ACME")
    other = _make_object(client, admin, vendor, label="OTHER")
    bolt = _make_object(
        client, admin, part, key="P-1", label="볼트", properties={"vendor": acme["id"]}
    )
    nut = _make_object(
        client,
        admin,
        part,
        key="P-2",
        label="너트",
        properties={"vendors": [acme["id"], other["id"]]},
    )
    assert _link(client, admin, part, bolt["id"], kind, acme["id"]).status_code == 201
    return {
        "vendor": vendor,
        "part": part,
        "kind": kind,
        "acme": acme,
        "other": other,
        "bolt": bolt,
        "nut": nut,
    }


def test_지우기_전에_가리키는_것을_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    refs = client.get(
        f"/api/objects/{w['vendor']}/{w['acme']['id']}/references", headers=admin.headers
    ).json()
    assert refs["total"] == 3
    assert {(one["label"], one["property_label"]) for one in refs["property_refs"]} == {
        ("볼트", "공급사"),
        ("너트", "공급사들"),
    }
    assert [one["other_label"] for one in refs["relations"]] == ["볼트"]


def test_기본은_막고_무엇이_걸렸는지_말한다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    response = client.delete(
        f"/api/objects/{w['vendor']}/{w['acme']['id']}", headers=admin.headers
    )
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["details"] == {"property_refs": 2, "relations": 1}
    # 안 지워졌다.
    assert (
        client.get(
            f"/api/objects/{w['vendor']}/{w['acme']['id']}", headers=admin.headers
        ).status_code
        == 200
    )
    # 걸린 것이 없는 것은 그냥 지워진다.
    lonely = _make_object(client, admin, w["vendor"], label="아무도 안 가리킴")
    assert (
        client.delete(
            f"/api/objects/{w['vendor']}/{lonely['id']}", headers=admin.headers
        ).status_code
        == 204
    )


def test_비우고_지우면_가리키던_칸이_비고_기록이_남는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    response = client.delete(
        f"/api/objects/{w['vendor']}/{w['acme']['id']}",
        params={"mode": "detach"},
        headers=admin.headers,
    )
    assert response.status_code == 204
    bolt = client.get(
        f"/api/objects/{w['part']}/{w['bolt']['id']}", headers=admin.headers
    ).json()
    assert "vendor" not in bolt["object"]["properties"]
    assert bolt["related"] == []
    nut = client.get(
        f"/api/objects/{w['part']}/{w['nut']['id']}", headers=admin.headers
    ).json()
    # 다중값은 그 하나만 빠진다.
    assert nut["object"]["properties"]["vendors"] == [w["other"]["id"]]
    # 가리키던 객체마다 「왜 이 칸이 비었지」 의 답이 남는다.
    log = client.get(
        "/api/audit/entries",
        params={"action": "object.update", "limit": 200},
        headers=admin.headers,
    ).json()
    assert any(
        one["target_id"] == w["bolt"]["id"] and "참조를 비움" in (one.get("reason") or "")
        for one in log["items"]
    )


def test_합치면_참조와_관계가_옮겨_가고_옛_링크가_새_것으로_간다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    result = client.post(
        f"/api/objects/{w['vendor']}/{w['acme']['id']}/merge",
        json={"into": w["other"]["id"]},
        headers=admin.headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["property_refs"] == 2
    assert result.json()["relations_moved"] == 1

    bolt = client.get(
        f"/api/objects/{w['part']}/{w['bolt']['id']}", headers=admin.headers
    ).json()
    assert bolt["object"]["properties"]["vendor"] == w["other"]["id"]
    assert bolt["object"]["ref_labels"][w["other"]["id"]] == "OTHER"
    assert [one["object_id"] for one in bolt["related"]] == [w["other"]["id"]]
    nut = client.get(
        f"/api/objects/{w['part']}/{w['nut']['id']}", headers=admin.headers
    ).json()
    # 다중값에 이긴 쪽이 이미 있었으니 겹치지 않고 하나.
    assert nut["object"]["properties"]["vendors"] == [w["other"]["id"]]

    # 옛 주소 — 어디로 갔는지 말한다.
    gone = client.get(f"/api/objects/{w['vendor']}/{w['acme']['id']}", headers=admin.headers)
    assert gone.status_code == 404
    assert gone.json()["error"]["details"]["merged_into"] == w["other"]["id"]
    assert (
        client.get(f"/api/objects/{w['vendor']}", headers=admin.headers).json()["total"] == 1
    )


def test_다른_타입이나_자기_자신에는_못_합친다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    same = client.post(
        f"/api/objects/{w['vendor']}/{w['acme']['id']}/merge",
        json={"into": w["acme"]["id"]},
        headers=admin.headers,
    )
    assert same.status_code == 409
    cross = client.post(
        f"/api/objects/{w['vendor']}/{w['acme']['id']}/merge",
        json={"into": w["bolt"]["id"]},
        headers=admin.headers,
    )
    assert cross.status_code == 404  # 부품은 공급사 타입에서 안 보인다 — 없는 것과 같은 말


def test_지워진_것을_가리키는_참조는_지워졌다고_적힌다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """이름만 보이면 살아 있는 줄 안다.

    API 로는 이 상태를 못 만든다(막히니까) — 옛 데이터나 DB 직접 조작으로만 생긴다.
    그래서 여기서는 DB 를 직접 만진다.
    """
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
    profile = client.get(f"/api/objects/{part}/{bolt['id']}", headers=admin.headers).json()
    assert profile["object"]["ref_labels"][acme["id"]] == "ACME (지워짐)"


def test_남의_부서가_가리키면_수만_말한다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """안 말하면 「아무것도 안 걸렸다」 로 읽고 지운다. 무엇인지는 안 말한다."""
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
    slug = f"other-{_make_object.__name__[:0]}{admin.workspace[-4:]}x"
    made = client.post(
        "/api/workspaces", json={"slug": slug, "name": "남"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    acme = _make_object(client, admin, vendor, label="ACME")  # 전역
    _make_object(
        client,
        admin,
        part,
        key="P-9",
        label="남의 부품",
        properties={"vendor": acme["id"]},
        workspace_slug=slug,
    )

    refs = client.get(
        f"/api/objects/{vendor}/{acme['id']}/references", headers=member.headers
    ).json()
    assert refs["property_refs"] == []
    assert refs["hidden_property_refs"] == 1
    assert refs["total"] == 1
