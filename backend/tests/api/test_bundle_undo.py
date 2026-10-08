"""묶음 한 판 **되돌리기** — 백필이 틀렸을 때의 길.

지키는 것: 만든 객체 · 이은 선이 사라지나, 고친 값이 넣기 전으로 돌아가나, **그 사이 남이
고친 줄은 건드리지 않나**, 밖에서 가리키는 것이 생긴 객체를 안 지우나, 남의 판을 못
되돌리나, 두 번 되돌리지 않나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectLink
from tests.api.conftest import Signed, bundle_import, finish_job


def _uniq(base: str) -> str:
    # 6자리면 한 번의 전체 시험에서 만드는 수천 개끼리 부딪친다(2026-10-05, 409) — 12자리.
    return f"{base}_{uuid.uuid4().hex[:12]}"


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


# --- 전부 아니면 무 · 다시 잇기 · 만든 것 지우기 (2026-10-08) -------------------------


def _tools(
    client: TestClient, who: Signed, tool: str, rows: list[dict[str, Any]], **more: Any
) -> dict[str, Any]:
    loaded = bundle_import(
        client,
        who,
        {
            "objects": [{"type_slug": tool, "workspace_slug": who.workspace, "rows": rows}],
            "apply": True,
            **more,
        },
    )
    assert loaded["applied"] is True, loaded
    return loaded


def _links(
    client: TestClient, who: Signed, tool: str, rows: list[dict[str, Any]], mode: str = "add"
) -> dict[str, Any]:
    loaded = bundle_import(
        client,
        who,
        {"relations": [{"type_slug": tool, "rows": rows, "mode": mode}], "apply": True},
    )
    assert loaded["applied"] is True, loaded
    return loaded


def _edges(client: TestClient, who: Signed, tool: str, object_id: str) -> set[str]:
    """그 객체에서 나가는 선의 도착점 이름."""
    got = client.get(f"/api/objects/{tool}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return {one["object_label"] for one in got.json()["related"] if one["outgoing"] is True}


def test_오류_줄이_있으면_앞_줄도_안_되돌린다(client: TestClient, admin: Signed) -> None:
    """**`apply=true` 로 곧장 부르면 일부만 커밋되고 「적용 안 됨」 이라 말했다.**

    거꾸로 읽으며 줄마다 flush 하는데, 오류 줄을 만나도 루프가 계속 돌았고 워커는 결과가
    무엇이든 커밋했다. 이 판이 만든 객체는 지워지고, 값을 되돌릴 줄은 오류인 채로 남았다 —
    판은 「되돌리지 않음」 이라 다시 되돌릴 수도 없었다.
    """
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    tool = names["tool"]
    made = client.post(
        f"/api/ontology/types/{tool}/properties",
        json={
            "key": "grade",
            "label": "등급",
            "data_type": "enum",
            "enum_options": ["A", "B"],
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    _tools(client, admin, tool, [{"key": "T-1", "label": "툴1", "grade": "A"}])
    # 이 판은 T-1 의 등급을 B 로 고치고 T-2 를 새로 만든다 — 거꾸로 읽으면 T-2 를 먼저 지운다.
    second = _tools(
        client,
        admin,
        tool,
        [{"key": "T-1", "label": "툴1", "grade": "B"}, {"key": "T-2", "label": "툴2"}],
    )
    # 그 뒤 관리자가 고를 값 A 를 뺐다 — T-1 을 A 로 되돌리는 줄은 지금 정의에 안 맞는다.
    narrowed = client.patch(
        f"/api/ontology/types/{tool}/properties/grade",
        json={"key": "grade", "label": "등급", "data_type": "enum", "enum_options": ["B"]},
        headers=admin.headers,
    )
    assert narrowed.status_code == 200, narrowed.text

    done = _undo(client, admin, second["run_id"], apply=True)
    assert done["applied"] is False and done["ok"] is False, done
    assert any(one["action"] == "error" for one in done["rows"]), done["rows"]
    # 앞 줄(T-2 지우기)도 안 바뀌었다.
    assert {one["key"] for one in _rows(client, admin, tool)} == {"T-1", "T-2"}
    runs = client.get("/api/bundles/runs", headers=admin.headers).json()
    mine = next(one for one in runs if one["id"] == second["run_id"])
    assert mine["undoable"] is True and mine["undone_at"] is None, mine


def _moved(client: TestClient, admin: Signed) -> tuple[dict[str, str], str, str]:
    """툴 T-1 의 소속을 맞춤(`replace`)으로 미국사 → 한국사로 옮긴 판 — (이름들, 판, T-1 id).

    맞춤은 새 선을 이은 뒤 옛 선을 끊는다. 소속은 그때 「여럿」 이었다 — 「하나만」 이면 새
    선을 잇는 자리에서 막혀 옮기지 못한다.
    """
    base, names = _world(admin.workspace)
    owner = _uniq("ownedby")
    base["ontology"]["relation_types"].append(
        {
            "slug": owner,
            "label": "소속",
            "src_type_slugs": [names["tool"]],
            "dst_type_slugs": [names["company"]],
        }
    )
    base["objects"][0]["rows"].extend(
        [{"key": "C-2", "label": "한국사"}, {"key": "C-3", "label": "일본사"}]
    )
    assert bundle_import(client, admin, base)["applied"] is True
    tool = names["tool"]
    _tools(client, admin, tool, [{"key": "T-1", "label": "툴1"}])
    _links(client, admin, tool, [{"src": "T-1", "relation": owner, "dst": "C-1"}])
    moved = _links(
        client, admin, tool, [{"src": "T-1", "relation": owner, "dst": "C-2"}], mode="replace"
    )
    t1 = str(_rows(client, admin, tool)[0]["id"])
    assert _edges(client, admin, tool, t1) == {"한국사"}
    return {**names, "owner": owner}, str(moved["run_id"]), t1


def _tighten(client: TestClient, admin: Signed, slug: str) -> None:
    """그 뒤 관리자가 소속을 「하나만」 으로 조였다."""
    tightened = client.patch(
        f"/api/ontology/relation-types/{slug}",
        json={"cardinality": "many_to_one"},
        headers=admin.headers,
    )
    assert tightened.status_code == 200, tightened.text


def test_되돌리며_끊을_선은_개수_제약에_안_센다(client: TestClient, admin: Signed) -> None:
    """**거꾸로 읽으면 옛 선을 먼저 되살린다** — 그때 새 선은 아직 있지만 곧 이 되돌리기가
    끊는다. 그것을 막는 것으로 세면 「하나만」 으로 조인 관계의 판은 하나도 못 되돌린다."""
    names, run_id, t1 = _moved(client, admin)
    tool = names["tool"]
    _tighten(client, admin, names["owner"])

    plan = _undo(client, admin, run_id)
    assert plan["ok"] is True, plan["rows"]
    done = _undo(client, admin, run_id, apply=True)
    assert done["applied"] is True, done
    assert _edges(client, admin, tool, t1) == {"미국사"}


def test_같은_선이_다시_이어졌으면_그대로_둔다(client: TestClient, admin: Signed) -> None:
    """그 사이 **같은 선이 새 id 로** 이어졌으면, 되살리면 유일 제약에 걸려 작업이 죽었다 —
    그 판은 영영 못 되돌렸다."""
    base, names = _world(admin.workspace)
    base["objects"][0]["rows"].append({"key": "C-2", "label": "한국사"})
    assert bundle_import(client, admin, base)["applied"] is True
    tool, made_by = names["tool"], names["made_by"]
    _tools(client, admin, tool, [{"key": "T-1", "label": "툴1"}])
    _links(client, admin, tool, [{"src": "T-1", "relation": made_by, "dst": "C-1"}])
    moved = _links(
        client,
        admin,
        tool,
        [{"src": "T-1", "relation": made_by, "dst": "C-2"}],
        mode="replace",
    )
    t1 = _rows(client, admin, tool)[0]["id"]
    c1 = next(
        one["id"] for one in _rows(client, admin, names["company"]) if one["key"] == "C-1"
    )
    # 사람이 끊긴 선을 손으로 다시 이었다(새 id).
    again = client.post(
        f"/api/objects/{tool}/{t1}/relations",
        json={"relation": made_by, "dst_object_id": c1},
        headers=admin.headers,
    )
    assert again.status_code == 201, again.text

    done = _undo(client, admin, moved["run_id"], apply=True)
    assert done["applied"] is True, done
    row = next(one for one in done["rows"] if "미국사" in one["label"])
    assert row["action"] == "unchanged" and "이미" in row["message"], row
    assert _edges(client, admin, tool, t1) == {"미국사"}


def test_되살리면_개수_제약을_깨는_선은_오류다(client: TestClient, admin: Signed) -> None:
    """**「하나만」 관계에 둘째가 조용히 들어갔다** — 되살리기가 id 로만 있나를 보고 개수
    제약 · 순환 · 관계 종류를 안 봤다. 오류 줄이고, 계획에도 같은 말이 뜬다."""
    names, run_id, t1 = _moved(client, admin)
    tool, owner = names["tool"], names["owner"]
    moved = {"run_id": run_id}
    # 그 뒤 사람이 C-2 선을 끊고 C-3 에 이었다 — 이 판 밖의 선이다.
    profile = client.get(f"/api/objects/{tool}/{t1}", headers=admin.headers).json()
    edge = next(one for one in profile["related"] if one["object_label"] == "한국사")
    cut = client.delete(
        f"/api/objects/{tool}/{t1}/relations/{edge['relation_id']}", headers=admin.headers
    )
    assert cut.status_code == 204, cut.text
    c3 = next(
        one["id"] for one in _rows(client, admin, names["company"]) if one["key"] == "C-3"
    )
    linked = client.post(
        f"/api/objects/{tool}/{t1}/relations",
        json={"relation": owner, "dst_object_id": c3},
        headers=admin.headers,
    )
    assert linked.status_code == 201, linked.text
    _tighten(client, admin, owner)

    plan = _undo(client, admin, moved["run_id"])
    assert plan["ok"] is False, plan["rows"]
    row = next(one for one in plan["rows"] if "미국사" in one["label"])
    assert row["action"] == "error" and "하나만" in row["message"], row
    done = _undo(client, admin, moved["run_id"], apply=True)
    assert done["applied"] is False, done
    assert _edges(client, admin, tool, t1) == {"일본사"}


def test_만든_객체도_그_사이_고쳤으면_안_지운다(client: TestClient, admin: Signed) -> None:
    """문서는 「남의 변경을 덮지 않는다」 고 약속하는데 **만든 줄에는 그 검사가 없었다** —
    사람이 그 뒤 고친 객체를 통째로 지웠다."""
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    tool = names["tool"]
    loaded = _tools(client, admin, tool, [{"key": "T-1", "label": "툴1"}])
    made = _rows(client, admin, tool)[0]
    changed = client.patch(
        f"/api/objects/{tool}/{made['id']}",
        json={"properties": {"note": "사람이 적음"}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text

    plan = _undo(client, admin, loaded["run_id"])
    row = next(one for one in plan["rows"] if one["object_id"] == made["id"])
    assert row["action"] == "unchanged" and "고쳐져" in row["message"], row
    done = _undo(client, admin, loaded["run_id"], apply=True)
    assert done["applied"] is True, done
    assert [one["key"] for one in _rows(client, admin, tool)] == ["T-1"]


def test_원_표에서_오는_선이_있으면_안_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """원 표(계정 · 부서)가 출발점인 선은 **이 객체가 도착점**이다 — 출발점만 봐서, 지우면 그
    선이 가리킬 곳 없이 남았다."""
    base, names = _world(admin.workspace)
    assert bundle_import(client, admin, base)["applied"] is True
    tool = names["tool"]
    loaded = _tools(client, admin, tool, [{"key": "T-1", "label": "툴1"}])
    made = _rows(client, admin, tool)[0]
    db.add(
        ObjectLink(
            src_type="user",
            src_id=uuid.uuid4(),
            dst_type=tool,
            dst_id=uuid.UUID(made["id"]),
            relation=names["made_by"],
        )
    )
    db.commit()

    plan = _undo(client, admin, loaded["run_id"])
    row = next(one for one in plan["rows"] if one["object_id"] == made["id"])
    assert row["action"] == "unchanged" and "가리켜 안 지웁니다" in row["message"], row


def test_되살리면_순환이_생기는_선은_오류다(client: TestClient, admin: Signed) -> None:
    """순환을 막는 관계에 **돌아오는 길**이 그 사이 생겼으면, 끊었던 선을 되살리는 순간 트리가
    무한히 돈다 — 되살리기가 그것을 안 봤다."""
    base, names = _world(admin.workspace)
    follows = _uniq("follows")
    base["ontology"]["relation_types"].append(
        {
            "slug": follows,
            "label": "뒤따름",
            "transitive": True,
            "acyclic": True,
            "src_type_slugs": [names["tool"]],
            "dst_type_slugs": [names["tool"]],
        }
    )
    assert bundle_import(client, admin, base)["applied"] is True
    tool = names["tool"]
    _tools(
        client,
        admin,
        tool,
        [{"key": f"T-{n}", "label": f"툴{n}"} for n in (1, 2, 3)],
    )
    _links(client, admin, tool, [{"src": "T-1", "relation": follows, "dst": "T-2"}])
    moved = _links(
        client,
        admin,
        tool,
        [{"src": "T-1", "relation": follows, "dst": "T-3"}],
        mode="replace",
    )
    ids = {one["key"]: one["id"] for one in _rows(client, admin, tool)}
    # 그 뒤 사람이 T-2 → T-1 을 이었다 — T-1 → T-2 가 되살아나면 고리다.
    back = client.post(
        f"/api/objects/{tool}/{ids['T-2']}/relations",
        json={"relation": follows, "dst_object_id": ids["T-1"]},
        headers=admin.headers,
    )
    assert back.status_code == 201, back.text

    plan = _undo(client, admin, moved["run_id"])
    row = next(one for one in plan["rows"] if one["label"].endswith("툴2"))
    assert row["action"] == "error" and "순환" in row["message"], plan["rows"]
    done = _undo(client, admin, moved["run_id"], apply=True)
    assert done["applied"] is False, done
    assert _edges(client, admin, tool, ids["T-1"]) == {"툴3"}
