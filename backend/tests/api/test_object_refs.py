"""참조 색인(`object_refs`) — **어느 길로 써도 맞는다**(ADR 0010).

색인은 트리거가 유지한다. 쓰는 길이 열 곳이 넘어 코드로 맞추면 한 길이 빠지고, 빠진 길로 쓴
값은 「관련 객체」 에서 조용히 사라진다 — 그래서 여기서는 길마다 색인이 값과 같은지 본다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectRef
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _refs(db: Session, src: str) -> set[tuple[str, str]]:
    db.expire_all()
    rows = db.execute(
        select(ObjectRef.key, ObjectRef.dst_id).where(ObjectRef.src_id == uuid.UUID(src))
    )
    return {(key, str(dst)) for key, dst in rows}


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    case = _make_type(client, admin, label="시장 서비스")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
    )
    _make_property(
        client,
        admin,
        case,
        key="parts",
        label="연관 모델",
        data_type="object_ref",
        ref_type_slug=model,
        multi=True,
    )
    one = _make_object(client, admin, model, key="M-1", label="M-1")
    two = _make_object(client, admin, model, key="M-2", label="M-2")
    return {"model": model, "case": case, "one": one["id"], "two": two["id"]}


def test_만들고_고치고_비우면_색인이_따라간다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    made = _make_object(
        client,
        admin,
        w["case"],
        label="건 1",
        properties={"model": w["one"], "parts": [w["one"], w["two"]]},
    )
    assert _refs(db, made["id"]) == {
        ("model", w["one"]),
        ("parts", w["one"]),
        ("parts", w["two"]),
    }

    changed = client.patch(
        f"/api/objects/{w['case']}/{made['id']}",
        json={"properties": {"model": w["two"], "parts": None}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text
    assert _refs(db, made["id"]) == {("model", w["two"])}

    # 이름만 고쳐도 색인은 그대로다.
    renamed = client.patch(
        f"/api/objects/{w['case']}/{made['id']}",
        json={"label": "건 1a"},
        headers=admin.headers,
    )
    assert renamed.status_code == 200
    assert _refs(db, made["id"]) == {("model", w["two"])}


def test_지운_객체는_가리키지_않고_영구_삭제는_줄도_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    made = _make_object(client, admin, w["case"], label="건 1", properties={"model": w["one"]})
    assert _refs(db, made["id"]) == {("model", w["one"])}

    gone = client.delete(f"/api/objects/{w['case']}/{made['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    assert _refs(db, made["id"]) == set(), "지운 객체는 가리키지 않는다"

    purged = client.delete(
        f"/api/ontology/types/{w['case']}",
        params={"purge_deleted": "true"},
        headers=admin.headers,
    )
    assert purged.status_code == 204, purged.text
    assert (
        db.scalar(select(ObjectRef).where(ObjectRef.src_id == uuid.UUID(made["id"]))) is None
    )


def test_속성을_지우면_줄이_빠지고_되살리면_남은_값으로_다시_선다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    made = _make_object(client, admin, w["case"], label="건 1", properties={"model": w["one"]})

    removed = client.delete(
        f"/api/ontology/types/{w['case']}/properties/model", headers=admin.headers
    )
    assert removed.status_code == 204, removed.text
    assert _refs(db, made["id"]) == set()

    # 값은 JSONB 에 남아 있다 — 같은 키로 정의를 되살리면 색인도 다시 선다.
    _make_property(
        client,
        admin,
        w["case"],
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=w["model"],
    )
    assert _refs(db, made["id"]) == {("model", w["one"])}


def test_일괄_입력과_부서를_가리키는_칸도_색인에_든다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    dept = _make_type(
        client, admin, label="부서", kind_class="system", system_source="workspace"
    )
    _make_property(
        client,
        admin,
        w["case"],
        key="owner",
        label="담당 부서",
        data_type="object_ref",
        ref_type_slug=dept,
    )
    done = client.post(
        f"/api/objects/{w['case']}/import-rows",
        json={
            "rows": [{"label": "건 9", "model": "M-1", "owner": admin.workspace}],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    )
    assert done.status_code == 200 and done.json()["applied"], done.text
    listed = client.get(
        f"/api/objects/{w['case']}", params={"q": "건 9"}, headers=admin.headers
    )
    made = listed.json()["items"][0]
    refs = _refs(db, made["id"])
    assert ("model", w["one"]) in refs
    assert any(key == "owner" for key, _ in refs), "원 표(부서)를 가리키는 칸도 든다"
