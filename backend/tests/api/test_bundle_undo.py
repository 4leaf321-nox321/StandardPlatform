"""묶음 한 판 **되돌리기** — 백필이 틀렸을 때의 길.

지키는 것: 만든 객체 · 이은 선이 사라지나, 고친 값이 넣기 전으로 돌아가나, **그 사이 남이
고친 줄은 건드리지 않나**, 밖에서 가리키는 것이 생긴 객체를 안 지우나, 남의 판을 못
되돌리나, 두 번 되돌리지 않나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, bundle_import, finish_job


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:6]}"


def _world(workspace: str) -> tuple[dict[str, Any], dict[str, str]]:
    """타입 둘(기업 · 툴), 툴 → 기업 참조와 관계 하나."""
    company, tool, made_by = _uniq("company"), _uniq("tool"), _uniq("madeby")
    return (
        {
            "ontology": {
                "types": [
                    {"slug": company, "label": "기업", "key_policy": "required"},
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
                            },
                            {"key": "note", "label": "메모", "data_type": "text"},
                        ],
                    },
                ],
                "relation_types": [
                    {
                        "slug": made_by,
                        "label": "만든 곳",
                        "src_type_slugs": [tool],
                        "dst_type_slugs": [company],
                    }
                ],
            },
            "objects": [
                {
                    "type_slug": company,
                    "workspace_slug": workspace,
                    "rows": [{"key": "C-1", "label": "미국사"}],
                }
            ],
            "apply": True,
        },
        {"company": company, "tool": tool, "made_by": made_by},
    )


def _rows(client: TestClient, who: Signed, type_slug: str) -> list[dict[str, Any]]:
    got = client.get(f"/api/objects/{type_slug}", headers=who.headers)
    return list(got.json()["items"]) if got.status_code == 200 else []


def _undo(
    client: TestClient, who: Signed, run_id: str, *, apply: bool = False
) -> dict[str, Any]:
    started = client.post(
        f"/api/bundles/runs/{run_id}/undo", params={"apply": apply}, headers=who.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, who, started.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def test_한_판을_통째로_되돌린다(client: TestClient, admin: Signed) -> None:
    """**백필 한 번을 되돌릴 길이 없었다.**

    수만 줄을 넣은 뒤 원천의 열을 잘못 맞춘 것을 알아도, 화면에서 객체를 하나씩 고치는 길밖에
    없었다. 감사 기록으로도 안 된다 — 백필은 `audit="summary"` 로 돌아 줄마다의 기록이 아예
    없다(그것이 켜진 이유다).
    """
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True

    # 백필 — 객체 둘과 선 하나. 줄마다의 기록은 남기지 않는다(백필이 그렇게 돈다).
    loaded = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [
                        {"key": "T-1", "label": "툴1", "vendor": "C-1"},
                        {"key": "T-2", "label": "툴2"},
                    ],
                }
            ],
            "relations": [
                {
                    "type_slug": names["tool"],
                    "rows": [{"src": "T-1", "relation": names["made_by"], "dst": "C-1"}],
                }
            ],
            "apply": True,
            "audit": "summary",
            "events": "summary",
        },
    )
    assert loaded["applied"] is True, loaded
    run_id = loaded["run_id"]
    assert run_id, loaded
    assert {one["key"] for one in _rows(client, admin, names["tool"])} == {"T-1", "T-2"}

    # 목록에 그 판이 있고 되돌릴 수 있다고 말한다.
    runs = client.get("/api/bundles/runs", headers=admin.headers).json()
    mine = next(one for one in runs if one["id"] == run_id)
    assert mine["undoable"] is True and names["tool"] in mine["label"], mine

    # 계획 — 아직 아무것도 안 되돌린다.
    plan = _undo(client, admin, run_id)
    assert plan["ok"] is True and plan["applied"] is False, plan
    assert plan["counts"]["delete"] == 3, plan["counts"]
    assert {one["key"] for one in _rows(client, admin, names["tool"])} == {"T-1", "T-2"}

    # 되돌린다.
    done = _undo(client, admin, run_id, apply=True)
    assert done["applied"] is True and done["ok"] is True, done
    assert _rows(client, admin, names["tool"]) == []
    # 앞 판이 넣은 기업은 그대로다 — 되돌린 것은 그 판뿐이다.
    assert {one["key"] for one in _rows(client, admin, names["company"])} == {"C-1"}

    # 두 번은 안 된다.
    again = client.post(
        f"/api/bundles/runs/{run_id}/undo", params={"apply": True}, headers=admin.headers
    )
    assert again.status_code == 409, again.text
    assert "이미 되돌린" in again.json()["error"]["message"]


def test_고친_값은_넣기_전으로_돌아간다(client: TestClient, admin: Signed) -> None:
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    first = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "T-1", "label": "툴1", "note": "처음"}],
                }
            ],
            "apply": True,
        },
    )
    assert first["applied"] is True

    second = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "T-1", "label": "툴1 고침", "note": "두번째"}],
                }
            ],
            "apply": True,
        },
    )
    assert second["counts"]["objects_update"] == 1, second["counts"]

    done = _undo(client, admin, second["run_id"], apply=True)
    assert done["applied"] is True, done
    made = _rows(client, admin, names["tool"])[0]
    assert made["label"] == "툴1", made
    detail = client.get(
        f"/api/objects/{names['tool']}/{made['id']}", headers=admin.headers
    ).json()
    assert detail["object"]["properties"]["note"] == "처음", detail["object"]["properties"]


def test_그_사이_남이_고친_줄은_안_되돌린다(client: TestClient, admin: Signed) -> None:
    """**남의 변경을 조용히 덮는 것이 되돌리지 않는 것보다 나쁘다.**"""
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "T-1", "label": "툴1", "note": "처음"}],
                }
            ],
            "apply": True,
        },
    )
    second = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "T-1", "label": "툴1", "note": "두번째"}],
                }
            ],
            "apply": True,
        },
    )
    made = _rows(client, admin, names["tool"])[0]
    # 그 뒤에 사람이 화면에서 고쳤다.
    changed = client.patch(
        f"/api/objects/{names['tool']}/{made['id']}",
        json={"properties": {"note": "사람이 고침"}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text

    plan = _undo(client, admin, second["run_id"])
    row = next(one for one in plan["rows"] if one["action"] == "unchanged")
    assert "그 뒤에" in row["message"] and "안 되돌립니다" in row["message"], row

    done = _undo(client, admin, second["run_id"], apply=True)
    assert done["applied"] is True, done
    detail = client.get(
        f"/api/objects/{names['tool']}/{made['id']}", headers=admin.headers
    ).json()
    assert detail["object"]["properties"]["note"] == "사람이 고침"


def test_밖에서_가리키면_안_지운다(client: TestClient, admin: Signed) -> None:
    """이 판이 만든 객체를 그 뒤에 누가 가리켰으면, 지우면 그 참조가 빈 칸이 된다."""
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    loaded = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["company"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "C-9", "label": "새 기업"}],
                }
            ],
            "apply": True,
        },
    )
    assert loaded["applied"] is True
    fresh = next(one for one in _rows(client, admin, names["company"]) if one["key"] == "C-9")

    # 그 뒤에 사람이 그 기업을 가리키는 툴을 만들었다.
    made = client.post(
        f"/api/objects/{names['tool']}",
        json={
            "label": "사람이 만든 툴",
            "key": "T-9",
            "properties": {"vendor": fresh["id"]},
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    plan = _undo(client, admin, loaded["run_id"])
    row = next(one for one in plan["rows"] if one["label"].endswith("새 기업"))
    assert row["action"] == "unchanged", row
    assert "가리켜 안 지웁니다" in row["message"], row
    done = _undo(client, admin, loaded["run_id"], apply=True)
    assert done["applied"] is True, done
    assert {one["key"] for one in _rows(client, admin, names["company"])} == {"C-1", "C-9"}


def test_남의_판은_못_되돌린다(client: TestClient, admin: Signed, member: Signed) -> None:
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    loaded = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "T-1", "label": "툴1"}],
                }
            ],
            "apply": True,
        },
    )
    denied = client.post(f"/api/bundles/runs/{loaded['run_id']}/undo", headers=member.headers)
    assert denied.status_code == 403, denied.text
    # 남의 판은 목록에도 안 보인다.
    mine = client.get("/api/bundles/runs", headers=member.headers).json()
    assert loaded["run_id"] not in {one["id"] for one in mine}
