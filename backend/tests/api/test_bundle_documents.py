"""묶음의 문서 본문 · 원천 삭제 표식의 보강(ADR 0098) — 0.4.106 점검에서 찾은 것.

- 문서 본문을 실은 묶음을 되돌리면 그 묶음이 만든 객체도 지운다(문서 단계가 「고친 때」 를
  늦게 적어, 되돌리기가 「그 뒤에 고쳐졌다」 로 보고 남겼다).
- 식별자 범위가 부서인 타입은 부서로 가른다 — 문서 본문도 삭제 표식도.
- 같은 칸의 본문 둘은 줄 오류(전에는 작업 전체가 DB 오류).
- 원천이 관리하는 타입의 문서 칸은 그 원천의 묶음만 쓴다(화면 저장도 막는다).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.workspaces.models import Workspace
from app.shared.errors import code
from tests.api.conftest import Signed, bundle_import, finish_job

SOURCE = "relay-doc"


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:12]}"


def _type(
    client: TestClient, admin: Signed, *, key_scope: str = "global", source: str = ""
) -> str:
    slug = _uniq("doc_owner")
    made = client.post(
        "/api/ontology/import",
        params={"dry_run": "false", **({"source": source} if source else {})},
        json={
            "types": [
                {
                    "slug": slug,
                    "label": "보고 대상",
                    "key_policy": "required",
                    "key_scope": key_scope,
                    "properties": [{"key": "paper", "label": "백서", "data_type": "document"}],
                }
            ]
        },
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    assert not made.json()["errors"], made.json()
    return slug


def _doc(slug: str, key: str, body: str = "# 본문", **extra: Any) -> dict[str, Any]:
    return {"type_slug": slug, "key": key, "property": "paper", "body": body, **extra}


def test_문서를_실은_묶음을_되돌리면_만든_객체도_지운다(
    client: TestClient, admin: Signed
) -> None:
    slug = _type(client, admin)
    rows = {
        "type_slug": slug,
        "workspace_slug": admin.workspace,
        "rows": [{"key": "D-1", "label": "하나"}],
    }
    done = bundle_import(
        client, admin, {"objects": [rows], "documents": [_doc(slug, "D-1")], "apply": True}
    )
    assert done["applied"] is True, done
    started = client.post(
        f"/api/bundles/runs/{done['run_id']}/undo",
        params={"apply": True},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    undone = finish_job(client, admin, started.json())
    assert undone["status"] == "done", undone
    left = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"]
    assert [one["key"] for one in left] == [], (undone["result"], left)


def test_같은_칸의_본문_둘은_줄_오류다(client: TestClient, admin: Signed) -> None:
    slug = _type(client, admin)
    rows = {
        "type_slug": slug,
        "workspace_slug": admin.workspace,
        "rows": [{"key": "D-1", "label": "하나"}],
    }
    plan = bundle_import(
        client,
        admin,
        {
            "objects": [rows],
            "documents": [_doc(slug, "D-1", "첫째"), _doc(slug, "D-1", "둘째")],
        },
    )
    actions = [(one["action"], one["message"]) for one in plan["documents"]]
    assert actions[0] == ("create", ""), plan
    assert actions[1][0] == "error" and "또 있습니다" in actions[1][1], plan


def test_부서_범위_식별자는_부서로_가른다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    slug = _type(client, admin, key_scope="workspace")
    other = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="다른 팀")
    db.add(other)
    db.commit()
    for workspace in (admin.workspace, other.slug):
        made = client.post(
            f"/api/objects/{slug}",
            json={"key": "K-1", "label": f"{workspace} 의 것", "workspace_slug": workspace},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text

    unsure = bundle_import(client, admin, {"documents": [_doc(slug, "K-1")]})
    assert "부서 여럿" in unsure["documents"][0]["message"], unsure
    picked = bundle_import(
        client,
        admin,
        {"documents": [_doc(slug, "K-1", workspace_slug=other.slug)], "apply": True},
    )
    assert picked["applied"] is True and picked["documents"][0]["action"] == "create", picked
    items = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"]
    there = next(one for one in items if one["label"] == f"{other.slug} 의 것")
    doc = client.get(
        f"/api/objects/{slug}/{there['id']}/documents/paper", headers=admin.headers
    ).json()
    assert doc["revision"] == 1, doc

    # 원천의 삭제 표식도 — 여럿이면 줄 오류(엉뚱한 부서의 것을 지우지 않는다).
    graves = bundle_import(
        client,
        admin,
        {"tombstones": {"objects": [{"type_slug": slug, "key": "K-1"}]}, "source": SOURCE},
    )
    rows = graves["tombstones"]["rows"]
    assert rows[0]["action"] == "error" and "부서 여럿" in rows[0]["message"], graves


def test_원천이_관리하는_타입의_문서는_그_원천만_쓴다(
    client: TestClient, admin: Signed
) -> None:
    slug = _type(client, admin, source=SOURCE)
    rows = {
        "type_slug": slug,
        "workspace_slug": admin.workspace,
        "rows": [{"key": "M-1", "label": "하나"}],
    }
    loaded = bundle_import(client, admin, {"objects": [rows], "source": SOURCE, "apply": True})
    assert loaded["applied"] is True, loaded

    local = bundle_import(client, admin, {"documents": [_doc(slug, "M-1")]})
    assert local["documents"][0]["action"] == "error", local
    assert SOURCE in local["documents"][0]["message"], local
    mine = bundle_import(client, admin, {"documents": [_doc(slug, "M-1")], "source": SOURCE})
    assert mine["documents"][0]["action"] == "create", mine

    item = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"][0]
    refused = client.put(
        f"/api/objects/{slug}/{item['id']}/documents/paper",
        json={"body": "# 화면에서", "base_revision": 0},
        headers=admin.headers,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == code("OBJECTS", 87), refused.json()
