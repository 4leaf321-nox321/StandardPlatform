"""정의 지우기의 미리 보기 — **삭제 경로와 같은 함수가 센다.**

`GET /api/ontology/delete-plan` 이 막는다고 하면 삭제가 **같은 코드 · 같은 말로**
거절되고, 된다고 하면 지워진다. 둘이 따로 세면 「미리 보기는 된다는데 지우면 거절」 이
되고, 기계(MCP)가 지울 수 있게 된 뒤로는 그 어긋남이 기계 속도로 난다. 지우기 직전
정의는 스냅샷으로 남는다.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.files.models import Attachment
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import OntologySnapshot
from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_interfaces import _add_common, _code, _make_interface, _props
from tests.api.test_ontology import _make_object, _make_property, _make_type, _uniq


def _plan(
    client: TestClient, who: Signed, kind: str, slug: str, key: str | None = None
) -> dict[str, Any]:
    params = {"kind": kind, "slug": slug, **({"key": key} if key else {})}
    got = client.get("/api/ontology/delete-plan", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _only(db: Session, snapshot_id: str, slug: str) -> str:
    """그 스냅샷에서 **이 타입만** 담은 스냅샷 — 시험 DB 는 스위트가 함께 써서, 통째로 되돌리면
    다른 시험이 남긴 정의(등록 안 된 원 표 등)에 걸린다."""
    whole = db.get(OntologySnapshot, uuid.UUID(snapshot_id))
    assert whole is not None
    types = [one for one in (whole.schema or {}).get("types", []) if one["slug"] == slug]
    assert types, f"스냅샷에 {slug} 가 없습니다"
    for one in types:
        one.pop("nav_group_slug", None)
    row = OntologySnapshot(actor_label="시험", reason=whole.reason, schema={"types": types})
    db.add(row)
    db.commit()
    return str(row.id)


def _snapshots(client: TestClient, admin: Signed) -> list[dict[str, Any]]:
    got = client.get("/api/ontology/snapshots", headers=admin.headers)
    assert got.status_code == 200, got.text
    return list(got.json())


def test_객체가_든_타입은_계획이_막고_삭제도_같은_말로_거절한다(
    client: TestClient, admin: Signed
) -> None:
    kind = _make_type(client, admin, label="부품")
    _make_property(client, admin, kind, key="w", label="무게", data_type="number")
    _make_object(client, admin, kind, label="볼트")

    plan = _plan(client, admin, "type", kind)
    assert plan["allowed"] is False
    assert [one["code"] for one in plan["blocking"]] == [code("ONTOLOGY", 38)]
    assert plan["purge_deleted"] == 0, "살아 있는 것이 있으면 영구 삭제를 말하지 않는다"

    refused = client.delete(
        f"/api/ontology/types/{kind}", params={"purge_deleted": "true"}, headers=admin.headers
    )
    assert refused.status_code == 409
    assert _code(refused) == code("ONTOLOGY", 38)
    assert refused.json()["error"]["message"] == plan["blocking"][0]["message"]


# --- 지운 객체만 남은 타입 (ADR 0008) ------------------------------------------------


def test_지운_객체만_남은_타입은_확인을_받고_영구_삭제하며_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """예전에는 객체를 한 번이라도 넣은 타입은 영영 못 지웠다 — 지운 객체의 행이 타입을
    RESTRICT 로 붙든다. 이제는 그것까지 영구 삭제하며 지운다. 되돌릴 수 없으니 확인을
    받는다."""
    kind = _make_type(client, admin, label="부품")
    _make_property(client, admin, kind, key="w", label="무게", data_type="number")
    bolt = _make_object(client, admin, kind, label="볼트")
    attached = client.post(
        "/api/attachments",
        data={
            "owner_table": "objects",
            "owner_id": bolt["id"],
            "workspace_slug": admin.workspace,
        },
        files={"file": ("도면.txt", io.BytesIO(b"draw"), "text/plain")},
        headers=admin.headers,
    )
    assert attached.status_code == 201, attached.text
    gone = client.delete(f"/api/objects/{kind}/{bolt['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text

    plan = _plan(client, admin, "type", kind)
    assert plan["allowed"] is True and plan["purge_deleted"] == 1
    assert plan["removes"] == [
        "지운 객체 1개 — 영구 삭제(되돌릴 수 없습니다)",
        "첨부(파일 목록의 행) 1개",
        "속성 정의 1개",
    ]
    assert plan["keeps"] == ["감사 기록 — 누가 언제 무엇을 했는지(이름과 함께)"]

    # 확인 없이는 안 지운다 — 무엇이 사라지는지 함께 말한다.
    unconfirmed = client.delete(f"/api/ontology/types/{kind}", headers=admin.headers)
    assert unconfirmed.status_code == 409 and _code(unconfirmed) == code("ONTOLOGY", 84)
    assert unconfirmed.json()["error"]["details"]["deleted_objects"] == 1

    done = client.delete(
        f"/api/ontology/types/{kind}", params={"purge_deleted": "true"}, headers=admin.headers
    )
    assert done.status_code == 204, done.text
    assert db.get(ObjectInstance, uuid.UUID(bolt["id"])) is None
    assert db.get(Attachment, uuid.UUID(attached.json()["id"])) is None
    entry = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "ontology.type.delete", AuditEntry.target_label == kind
        )
    ).one()
    assert (entry.changes or {})["purged_objects"] == 1
    # 그 객체를 만들고 지운 기록은 남는다 — 누가 언제 무엇을.
    assert db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "object.delete", AuditEntry.target_id == uuid.UUID(bolt["id"])
        )
    ).one()


def test_살아_있는_객체가_지운_객체를_가리키면_영구_삭제하지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="부품")
    holder = _make_type(client, admin, label="조립")
    _make_property(
        client,
        admin,
        holder,
        key="part",
        label="부품",
        data_type="object_ref",
        ref_type_slug=kind,
    )
    bolt = _make_object(client, admin, kind, label="볼트")
    asm = _make_object(client, admin, holder, label="조립1")
    gone = client.delete(f"/api/objects/{kind}/{bolt['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    # 「(지워짐)」 을 가리키는 칸 — 품질 화면이 찾는 그것. 정상 경로로는 막혀 있어 직접 만든다.
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == uuid.UUID(asm["id"]))
        .values(properties={"part": bolt["id"]})
    )
    db.commit()

    plan = _plan(client, admin, "type", kind)
    assert [one["code"] for one in plan["blocking"]] == [code("ONTOLOGY", 85)]
    assert f"{holder}.part 1개" in plan["blocking"][0]["message"]
    refused = client.delete(
        f"/api/ontology/types/{kind}", params={"purge_deleted": "true"}, headers=admin.headers
    )
    assert refused.status_code == 409 and _code(refused) == code("ONTOLOGY", 85)
    assert db.get(ObjectInstance, uuid.UUID(bolt["id"])) is not None


def test_데이터_소스가_넣고_있는_타입은_먼저_말하고_막는다(
    client: TestClient, admin: Signed
) -> None:
    """FK 가 RESTRICT 라 예전에는 지우는 자리에서 500 이 났다."""
    kind = _make_type(client, admin, label="공급사")
    made = client.post(
        "/api/datasources",
        json={
            "slug": f"plm_{uuid.uuid4().hex[:6]}",
            "name": "PLM 공급사",
            "base_url": "http://plm.local/odata",
            "entity_set": "Suppliers",
            "type_slug": kind,
            "workspace_slug": admin.workspace,
            "mapping": {
                "external_key": "VendorNo",
                "columns": [
                    {"source": "VendorNo", "target": "key"},
                    {"source": "Name", "target": "label"},
                ],
            },
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    plan = _plan(client, admin, "type", kind)
    assert [one["code"] for one in plan["blocking"]] == [code("ONTOLOGY", 86)]
    refused = client.delete(f"/api/ontology/types/{kind}", headers=admin.headers)
    assert refused.status_code == 409 and _code(refused) == code("ONTOLOGY", 86)


def test_지표가_세는_타입도_먼저_말하고_막는다(client: TestClient, admin: Signed) -> None:
    """지표 정의도 원천 타입을 RESTRICT 로 붙든다 — 계획은 「지울 수 있다」 고 말하고 지우는
    자리에서 500 이 났다(2026-10-08)."""
    kind = _make_type(client, admin, label="기록", usage="log")
    _make_property(client, admin, kind, key="received", label="접수일", data_type="date")
    made = client.post(
        "/api/metrics",
        params={"recompute": "false"},
        json={
            "slug": _uniq("m"),
            "label": "월별 접수",
            "source_type_slug": kind,
            "spec": {
                "measure": "count",
                "time": {"address": "properties.received", "grain": "month"},
            },
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    plan = _plan(client, admin, "type", kind)
    assert [one["code"] for one in plan["blocking"]] == [code("ONTOLOGY", 88)]
    refused = client.delete(f"/api/ontology/types/{kind}", headers=admin.headers)
    assert refused.status_code == 409 and _code(refused) == code("ONTOLOGY", 88)


def test_빈_타입은_무엇이_사라지고_무엇이_가리키는지_말하고_지우면_스냅샷이_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="부품")
    _make_property(client, admin, kind, key="w", label="무게", data_type="number")
    user_of = _make_type(client, admin, label="조립")
    _make_property(
        client,
        admin,
        user_of,
        key="part",
        label="부품",
        data_type="object_ref",
        ref_type_slug=kind,
    )

    plan = _plan(client, admin, "type", kind)
    assert plan["allowed"] is True and plan["blocking"] == []
    assert plan["removes"] == ["속성 정의 1개"]
    assert any(f"{user_of}.part" in one for one in plan["warnings"])

    before = len(_snapshots(client, admin))
    done = client.delete(f"/api/ontology/types/{kind}", headers=admin.headers)
    assert done.status_code == 204, done.text
    snapshots = _snapshots(client, admin)
    assert len(snapshots) == before + 1
    assert snapshots[0]["reason"] == f"삭제 직전: 타입 {kind}"

    # 그 스냅샷이 되살린다 — 미리 보기는 아무것도 안 바꾼다.
    restore = f"/api/ontology/snapshots/{_only(db, snapshots[0]['id'], kind)}/restore"
    preview = client.post(restore, params={"dry_run": "true"}, headers=admin.headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["applied"] is False
    assert any(
        one["slug"] == kind and one["action"] == "create" for one in preview.json()["changes"]
    )
    assert (
        client.get(f"/api/ontology/types/{kind}/properties", headers=admin.headers).status_code
        == 404
    )
    after_preview = len(_snapshots(client, admin))
    assert after_preview == before + 2, "미리 보기는 스냅샷을 안 남긴다(하나는 시험이 만든 것)"

    applied = client.post(restore, headers=admin.headers)
    assert applied.status_code == 200 and applied.json()["applied"] is True, applied.text
    assert set(_props(client, admin, kind)) == {"w"}


def test_속성을_지워도_저장값은_남고_되살리면_다시_보인다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="부품")
    _make_property(client, admin, kind, key="w", label="무게", data_type="number")
    bolt = _make_object(client, admin, kind, label="볼트", properties={"w": 3})

    plan = _plan(client, admin, "property", kind, "w")
    assert plan["allowed"] is True
    assert plan["removes"] == ["속성 정의"]
    assert plan["keeps"] == [
        "저장값 1개 — 화면에서는 안 보이고, 같은 키로 정의를 되살리면 돌아옵니다"
    ]
    assert plan["core_consumers"] == []

    done = client.delete(f"/api/ontology/types/{kind}/properties/w", headers=admin.headers)
    assert done.status_code == 204, done.text
    # 스냅샷 목록은 설치 전체의 것이다 — 시험 DB 를 함께 쓰면 같은 때 남의 스냅샷이 앞에 선다.
    # 내 타입 이름으로 찾는다.
    reason = f"삭제 직전: 속성 {kind}.w"
    snapshot = next(one for one in _snapshots(client, admin) if one["reason"] == reason)

    restored = client.post(
        f"/api/ontology/snapshots/{_only(db, snapshot['id'], kind)}/restore",
        headers=admin.headers,
    )
    assert restored.status_code == 200 and restored.json()["applied"] is True, restored.text
    got = client.get(f"/api/objects/{kind}/{bolt['id']}", headers=admin.headers)
    assert got.json()["object"]["properties"] == {"w": 3}


def test_공통_속성은_타입에서_막고_인터페이스에서_지우면_구현_타입의_것으로_남는다(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin, label="설비")
    common = {"key": "maker", "label": "제조사", "data_type": "text"}
    assert _add_common(client, admin, iface, **common).status_code == 201
    kind = _make_type(client, admin, label="시험장비", interface_slugs=[iface])

    on_type = _plan(client, admin, "property", kind, "maker")
    assert on_type["allowed"] is False
    assert [one["code"] for one in on_type["blocking"]] == [code("ONTOLOGY", 8)]
    refused = client.delete(
        f"/api/ontology/types/{kind}/properties/maker", headers=admin.headers
    )
    assert _code(refused) == code("ONTOLOGY", 8)

    # 구현 타입이 있으면 인터페이스 자체는 못 지운다.
    whole = _plan(client, admin, "interface", iface)
    assert [one["code"] for one in whole["blocking"]] == [code("ONTOLOGY", 9)]
    assert whole["removes"] == ["공통 속성 정의 1개"]

    on_iface = _plan(client, admin, "interface_property", iface, "maker")
    assert on_iface["allowed"] is True
    assert on_iface["removes"] == ["공통 속성 정의"]
    assert kind in on_iface["keeps"][0]
    done = client.delete(
        f"/api/ontology/interfaces/{iface}/properties/maker", headers=admin.headers
    )
    assert done.status_code == 204, done.text
    assert _props(client, admin, kind)["maker"]["interface_slug"] is None


def test_묶음과_관계_종류도_삭제와_같은_말로_막는다(client: TestClient, admin: Signed) -> None:
    group = _uniq("grp")
    made = client.post(
        "/api/ontology/groups", json={"slug": group, "label": "기계"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    child = _uniq("sub")
    made = client.post(
        "/api/ontology/groups",
        json={"slug": child, "label": "부품 묶음", "parent_slug": group},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    kind = _make_type(client, admin, label="부품", nav_group_slug=group)

    plan = _plan(client, admin, "group", group)
    assert [one["code"] for one in plan["blocking"]] == [code("ONTOLOGY", 39)]
    assert plan["warnings"] == ["하위 묶음 부품 묶음 은(는) 최상위 묶음이 됩니다."]
    refused = client.delete(f"/api/ontology/groups/{group}", headers=admin.headers)
    assert _code(refused) == code("ONTOLOGY", 39)

    relation = _uniq("uses")
    made = client.post(
        "/api/ontology/relation-types",
        json={"slug": relation, "label": "사용", "inverse_label": "쓰임"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    one = _make_object(client, admin, kind, label="볼트")
    two = _make_object(client, admin, kind, label="너트")
    linked = client.post(
        f"/api/objects/{kind}/{one['id']}/relations",
        json={"relation": relation, "dst_object_id": two["id"]},
        headers=admin.headers,
    )
    assert linked.status_code == 201, linked.text

    plan = _plan(client, admin, "relation_type", relation)
    assert [one["code"] for one in plan["blocking"]] == [code("ONTOLOGY", 44)]
    refused = client.delete(f"/api/ontology/relation-types/{relation}", headers=admin.headers)
    assert _code(refused) == code("ONTOLOGY", 44)


def test_미리_보기는_시스템_관리자만이고_속성은_키가_있어야_한다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    kind = _make_type(client, admin, label="부품")
    denied = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "type", "slug": kind},
        headers=manager.headers,
    )
    assert denied.status_code == 403

    no_key = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "property", "slug": kind},
        headers=admin.headers,
    )
    assert no_key.status_code == 409 and _code(no_key) == code("ONTOLOGY", 83)

    unknown = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "table", "slug": kind},
        headers=admin.headers,
    )
    assert unknown.status_code == 422
