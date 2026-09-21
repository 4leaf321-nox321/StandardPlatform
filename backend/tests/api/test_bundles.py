"""묶음 가져오기 — **한 번에 미리 보고, 전부 아니면 무로 넣는다.**

지키는 것: 미리 보기가 정말 아무것도 안 남기나(정의까지), 새 정의로 객체 · 관계를 맞춰 보나,
한 곳이라도 오류면 아무것도 안 들어가나, 바깥에 알리는 것이 진짜로 넣은 뒤에만 나가나,
정의가 든 묶음은 시스템 관리자와 ontology:write 범위를 요구하나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.shared import events
from tests.api.conftest import Signed, bundle_import


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:6]}"


def _bundle(workspace: str, *, bad_dst: bool = False) -> tuple[dict[str, Any], dict[str, str]]:
    """새 타입 둘(기업 ← 툴이 참조)과 관계 종류 하나, 객체 셋, 관계 한 줄."""
    company, tool, competes = _uniq("company"), _uniq("tool"), _uniq("competes")
    bundle: dict[str, Any] = {
        "ontology": {
            "types": [
                {
                    "slug": company,
                    "label": "기업",
                    "key_policy": "required",
                    "properties": [
                        {
                            "key": "country",
                            "label": "국가",
                            "data_type": "enum",
                            "enum_options": ["미국", "한국"],
                        }
                    ],
                },
                {
                    "slug": tool,
                    "label": "툴",
                    "key_policy": "required",
                    "properties": [
                        {
                            "key": "vendor",
                            "label": "개발사",
                            "data_type": "object_ref",
                            "ref_type_slug": company,
                        }
                    ],
                },
            ],
            "relation_types": [
                {
                    "slug": competes,
                    "label": "경쟁",
                    "src_type_slugs": [tool],
                    "dst_type_slugs": [tool],
                }
            ],
        },
        # **참조되는 타입이 먼저다** — 툴의 개발사가 기업을 찾아야 한다.
        "objects": [
            {
                "type_slug": company,
                "workspace_slug": workspace,
                "rows": [{"key": "C-1", "label": "미국사", "country": "미국"}],
            },
            {
                "type_slug": tool,
                "workspace_slug": workspace,
                "rows": [
                    {"key": "T-1", "label": "툴1", "vendor": "C-1"},
                    {"key": "T-2", "label": "툴2", "vendor": "C-1"},
                ],
            },
        ],
        "relations": [
            {
                "type_slug": tool,
                "rows": [
                    {
                        "src": "T-1",
                        "relation": competes,
                        "dst": "T-404" if bad_dst else "T-2",
                        "evidence_note": "벤더 비교표",
                    }
                ],
            }
        ],
    }
    return bundle, {"company": company, "tool": tool, "competes": competes}


def _send(
    client: TestClient, who: Signed, bundle: dict[str, Any], *, apply: bool = False
) -> dict[str, Any]:
    return bundle_import(client, who, {**bundle, "apply": apply})


def _exists(client: TestClient, who: Signed, type_slug: str) -> bool:
    got = client.get(f"/api/objects/{type_slug}", headers=who.headers)
    return bool(got.status_code == 200)


def test_한_번에_미리_보고_아무것도_안_남긴다(client: TestClient, admin: Signed) -> None:
    """정의를 먼저 적용하지 않아도 **그 정의로** 객체와 관계를 맞춰 본다."""
    bundle, names = _bundle(admin.workspace)
    body = _send(client, admin, bundle)

    assert body["ok"] is True and body["applied"] is False, body
    created = {c["slug"] for c in body["ontology"]["changes"] if c["action"] == "create"}
    assert {names["company"], names["tool"], names["competes"]} <= created
    assert body["counts"]["objects_create"] == 3
    assert body["counts"]["relations_create"] == 1
    # 롤백된 스냅샷을 가리키면 없는 되돌릴 자리를 약속하는 것이다.
    assert body["snapshot_id"] is None

    # **정의까지 안 남는다.**
    assert not _exists(client, admin, names["company"])
    assert not _exists(client, admin, names["tool"])


def test_적용하면_전부_들어가고_다시_보면_그대로다(client: TestClient, admin: Signed) -> None:
    bundle, names = _bundle(admin.workspace)
    body = _send(client, admin, bundle, apply=True)

    assert body["ok"] is True and body["applied"] is True, body
    assert body["snapshot_id"]
    tools = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()
    assert tools["total"] == 2

    # 같은 묶음을 다시 보면 **그대로** — 두 번 넣어도 두 벌이 안 된다.
    again = _send(client, admin, bundle)
    assert again["ok"] is True
    assert again["counts"]["ontology_changes"] == 0
    assert again["counts"]["objects_unchanged"] == 3
    assert again["counts"]["relations_unchanged"] == 1


def test_한_곳이라도_오류면_아무것도_안_들어간다(client: TestClient, admin: Signed) -> None:
    """관계 한 줄이 틀리면 정의도 객체도 안 들어간다 — 절반만 들어간 묶음은 어느 절반인지
    아무도 모른다."""
    bundle, names = _bundle(admin.workspace, bad_dst=True)
    body = _send(client, admin, bundle, apply=True)

    assert body["ok"] is False and body["applied"] is False
    rows = body["relations"][0]["plan"]["rows"]
    assert rows[0]["action"] == "error" and rows[0]["message"]
    assert not _exists(client, admin, names["company"])


def test_바깥에_알리는_것은_진짜로_넣은_뒤에만_나간다(
    client: TestClient, admin: Signed
) -> None:
    """미리 보기는 전부 롤백이다 — 그 사이에 웹훅 · 알림이 나가면 일어나지 않은
    변경을 알린다."""
    seen: list[str] = []

    def listen(staged: list[events.ChangeEvent]) -> None:
        seen.extend(one.action for one in staged)

    events.register_listener(listen)
    try:
        bundle, _ = _bundle(admin.workspace)
        _send(client, admin, bundle)
        assert seen == []

        _send(client, admin, bundle, apply=True)
        assert "object.create" in seen and "bundle.import" in seen
    finally:
        events._listeners.remove(listen)


def test_정의가_든_묶음은_시스템_관리자만(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    bundle, names = _bundle(manager.workspace)
    denied = _send(client, manager, bundle)
    assert denied["ok"] is False
    assert "시스템 관리자" in denied["errors"][0]

    # 정의는 관리자가 넣고, 부서 관리자는 **객체만** 자기 부서에 넣는다.
    _send(client, admin, {"ontology": bundle["ontology"]}, apply=True)
    objects_only = {"objects": bundle["objects"], "relations": bundle["relations"]}
    allowed = _send(client, manager, objects_only, apply=True)
    assert allowed["ok"] is True and allowed["applied"] is True, allowed
    assert _exists(client, manager, names["tool"])


def test_토큰은_정의가_들면_ontology_범위도_필요하다(
    client: TestClient, admin: Signed
) -> None:
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("objects-only"), "scopes": ["read", "objects:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    token = {"Authorization": f"Bearer {made.json()['token']}"}
    bundle, _ = _bundle(admin.workspace)

    denied = client.post("/api/bundles/import", json=bundle, headers=token)
    assert denied.status_code == 403
    assert "BUNDLES-0001" in denied.json()["error"]["code"]

    # 정의가 없으면 경로의 범위(objects:write)로 통과한다 — 모르는 타입은 묶음의 오류로.
    objects_only = bundle_import(client, admin, {"objects": bundle["objects"]}, headers=token)
    assert "찾을 수 없습니다" in objects_only["objects"][0]["error"]


def test_빈_묶음은_거절한다(client: TestClient, admin: Signed) -> None:
    got = client.post("/api/bundles/import", json={}, headers=admin.headers)
    assert got.status_code == 409
