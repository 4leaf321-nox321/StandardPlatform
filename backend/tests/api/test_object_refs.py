"""참조 색인(`object_refs`) — **어느 길로 써도 맞는다**(ADR 0010).

색인은 트리거가 유지한다. 쓰는 길이 열 곳이 넘어 코드로 맞추면 한 길이 빠지고, 빠진 길로 쓴
값은 「관련 객체」 에서 조용히 사라진다 — 그래서 여기서는 길마다 색인이 값과 같은지 본다.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import bulk, refindex
from app.modules.objects.models import ObjectRef
from app.modules.ontology.models import ObjectType
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


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


def _old_updated_function() -> str:
    """0049 가 걸었던 고치기 트리거(되돌리면 0075 가 거는 것) — 견줄 기준."""
    spec = importlib.util.spec_from_file_location("m0049", MIGRATIONS / "0049_object_refs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text_ = str(module.FUNCTIONS)
    start = text_.index("CREATE OR REPLACE FUNCTION object_refs_updated()")
    return text_[start : text_.index("END $$;", start) + len("END $$;")]


def test_고치기_트리거는_옛_트리거와_같은_색인을_남긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """옛 트리거(0049 — 속성이 바뀐 행의 줄을 통째로 다시 씀)와 새 트리거(0075 — 달라진 줄만)를
    **같은 갱신들**에 돌려 색인을 견준다. 한 줄 문장(정적 SQL)과 70줄 문장(그때 계획하는
    `EXECUTE`)을 모두, 그리고 한 줄 문장이 먼저 계획을 굳힌 같은 연결에서. 타입을 바꿔도 같은
    (누가 · 칸 · 누구를)이 남는 경우(두 타입이 같은 키의 참조 칸을 가진다)까지.
    한 트랜잭션 안에서 하고 되돌린다 — 함수 바꿔치기도 함께 사라진다."""
    w = _world(client, admin)
    other = _make_type(client, admin, label="다른 기록")
    _make_property(
        client,
        admin,
        other,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=w["model"],
    )
    _make_property(client, admin, w["case"], key="cost", label="비용", data_type="number")
    three = _make_object(client, admin, w["model"], key="M-3", label="M-3")["id"]
    case = db.scalar(select(ObjectType).where(ObjectType.slug == w["case"]))
    other_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == other))
    user = db.scalar(select(User).where(User.email == admin.email))
    owner = db.scalar(select(Workspace.id).where(Workspace.slug == admin.workspace))
    assert case is not None and other_id is not None and user is not None
    plan = bulk.apply_objects(
        db,
        user,
        case,
        [
            {"label": f"건 {n}", "model": "M-1", "parts": "M-1;M-2", "cost": str(n)}
            for n in range(71)
        ],
        owner_workspace_id=owner,
    )
    assert plan.ok, [one.message for one in plan.rows]
    ids = [one.object_id for one in plan.rows]
    steps = [
        "UPDATE objects SET properties = properties || '{\"cost\": 9}' WHERE id = ANY(:ids)",
        "UPDATE objects SET properties = properties || jsonb_build_object('model', "
        "CAST(:two AS text)) WHERE id = ANY(:half)",
        "UPDATE objects SET properties = properties || jsonb_build_object('parts', "
        "jsonb_build_array(CAST(:three AS text), CAST(:three AS text), CAST(:one AS text))) "
        "WHERE id = ANY(:ids)",
        "UPDATE objects SET properties = (properties - 'parts') || '{\"model\": 12}' "
        "WHERE id = ANY(:half)",
        "UPDATE objects SET updated_at = now() WHERE id = ANY(:ids)",
        "UPDATE objects SET deleted_at = now() WHERE id = ANY(:half)",
        "UPDATE objects SET deleted_at = now() + interval '1 second' WHERE id = ANY(:half)",
        "UPDATE objects SET deleted_at = NULL WHERE id = ANY(:ids)",
        "UPDATE objects SET type_id = :other WHERE id = ANY(:ids)",
        "UPDATE objects SET type_id = :case WHERE id = ANY(:ids)",
    ]

    def snapshot(part: list[Any]) -> list[tuple[Any, ...]]:
        r = ObjectRef
        return [
            tuple(one)
            for one in db.execute(
                select(r.src_id, r.src_type_id, r.key, r.dst_id)
                .where(r.src_id.in_(part))
                .order_by(r.src_id, r.key, r.dst_id)
            )
        ]

    results: dict[str, list[list[tuple[Any, ...]]]] = {}
    try:
        for name, function in (
            ("옛", _old_updated_function()),
            ("새", refindex.UPDATED_FUNCTION),
        ):
            nested = db.begin_nested()
            db.execute(text(function))
            seen: list[list[tuple[Any, ...]]] = []
            # 한 줄(정적 SQL) 다음에 70줄(EXECUTE) — 한 줄짜리 계획이 먼저 굳은 연결이다.
            for part in (ids[:1], ids[1:]):
                args = {
                    "ids": part,
                    "half": part[: max(1, len(part) // 2)],
                    "one": w["one"],
                    "two": w["two"],
                    "three": three,
                    "other": other_id,
                    "case": case.id,
                }
                for step in steps:
                    db.execute(text(step), args)
                    seen.append(snapshot(part))
                # 끝 상태는 처음부터 다시 뽑은 것과 같다.
                again = db.execute(
                    text(refindex.rows_of("(SELECT * FROM objects WHERE id = ANY(:ids))")),
                    {"ids": part},
                )
                assert sorted(tuple(one) for one in again) == sorted(seen[-1])
            results[name] = seen
            nested.rollback()
    finally:
        db.rollback()
    assert results["새"] == results["옛"]
    # 빈 것끼리 같다는 시험이 아니다 — 한 줄은 참조 칸을 비우기 전까지, 70줄은 끝까지(절반만
    # 비우고 지운다) 줄이 남아 있다.
    assert all(results["새"][:3]) and all(results["새"][len(steps) :])
