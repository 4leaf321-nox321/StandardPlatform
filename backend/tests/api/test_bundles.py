"""묶음 가져오기 — **한 번에 미리 보고, 전부 아니면 무로 넣는다.**

지키는 것: 미리 보기가 정말 아무것도 안 남기나(정의까지), 새 정의로 객체 · 관계를 맞춰 보나,
한 곳이라도 오류면 아무것도 안 들어가나, 바깥에 알리는 것이 진짜로 넣은 뒤에만 나가나,
정의가 든 묶음은 시스템 관리자와 ontology:write 범위를 요구하나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles.models import BundleRun
from app.shared import events
from tests.api.conftest import Signed, bundle_import, finish_job
from tests.api.test_ontology import _make_type


def _uniq(base: str) -> str:
    # 6자리면 한 번의 전체 시험에서 만드는 수천 개끼리 부딪친다(2026-10-05, 409) — 12자리.
    return f"{base}_{uuid.uuid4().hex[:12]}"


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


def test_내보내기는_읽기_토큰으로_된다(client: TestClient, admin: Signed) -> None:
    """**내보내기는 읽기다.** 작업 한 줄을 남기니 표로는 쓰기지만, 하는 일은 「가진 것을 파일로
    받기」 다 — 읽기 토큰으로 못 하게 두면 허브에서 정의를 **받아만 가는** 쌍둥이에게 쓰기
    토큰을 주게 된다. 쌍둥이 리허설에서 403 으로 걸려 고쳤다."""
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("reader"), "scopes": ["read"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    reader = {"Authorization": f"Bearer {made.json()['token']}"}

    part = _make_type(client, admin, label="부품", key_policy="required")
    group = client.post(
        "/api/ontology/groups",
        json={"slug": _uniq("g"), "label": "묶음"},
        headers=admin.headers,
    )
    assert group.status_code == 201, group.text

    for path, body in (
        ("/api/bundles/export", {"group": group.json()["slug"]}),
        (f"/api/objects/{part}/export", None),
        (f"/api/objects/{part}/relations/export", None),
    ):
        got = client.post(path, json=body, headers=reader)
        assert got.status_code == 202, f"{path}: {got.status_code} {got.text}"

    # **넣는 것은 여전히 못 한다** — 읽기 토큰이 쓰기로 새면 범위를 가른 뜻이 없다.
    denied = client.post("/api/bundles/import", json={"objects": []}, headers=reader)
    assert denied.status_code in (403, 409), denied.text


def test_읽기로_연_자리는_마디와_메서드까지_맞아야_한다(
    client: TestClient, admin: Signed
) -> None:
    """읽기로 연 POST(내보내기 · 계획)는 **그 자리의 POST 만** 이다. 끝(`/export`)이나 글자
    앞머리(`/api/metrics/plan`)만 보던 때는 읽기 토큰으로 ① slug 가 `export` 인 타입에 객체를
    만들고 ② `plan…` 으로 시작하는 지표를 고치고 지울 수 있었다(2026-10-08)."""
    from app.shared import scopes

    assert scopes.is_reading("POST", "/api/objects/part/export")
    assert scopes.is_reading("POST", "/api/ontology/export")
    assert scopes.is_reading("POST", "/api/metrics/plan")
    assert not scopes.is_reading("POST", "/api/objects/export")  # 타입 `export` 에 만들기
    assert not scopes.is_reading("PATCH", "/api/ontology/types/export")
    assert not scopes.is_reading("DELETE", "/api/metrics/plan")
    assert not scopes.is_reading("POST", "/api/metrics/plant_yield/recompute")
    assert not scopes.is_reading("PATCH", "/api/metrics/plant_yield")
    assert scopes.needed_scope("/api/objectsx") is None

    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("reader"), "scopes": ["read"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    reader = {"Authorization": f"Bearer {made.json()['token']}"}
    denied = client.post("/api/objects/export", json={"label": "몰래"}, headers=reader)
    assert denied.status_code == 403, denied.text


def test_가벼운_미리보기는_적용을_돌리지_않는다(client: TestClient, admin: Signed) -> None:
    """**미리 보기가 적용을 끝까지 돌린 뒤 되돌렸다** — 그래야 뒤 묶음이 앞 묶음의 객체를
    찾는데, 그 때문에 대량 백필은 시간이 두 배였다.

    `preview="plan"` 은 계획만 본다. 이 묶음이 **만들** 객체를 가리키는 칸 · 끝점은 오류가
    아니라 「적용할 때 풀린다」 로 줄에 적는다 — 그것을 오류로 내면 백필은 미리 볼 수가 없다.
    """
    bundle, names = _bundle(admin.workspace)
    light = bundle_import(client, admin, {**bundle, "preview": "plan"})
    assert light["ok"] is True, light
    assert light["applied"] is False

    # 툴은 같은 묶음이 만드는 기업을 가리킨다 — 그 사실이 줄에 적혔다.
    tools = next(one for one in light["objects"] if one["type_slug"] == names["tool"])
    row = tools["plan"]["rows"][0]
    assert row["action"] == "create"
    assert "이 묶음이 만드는 객체입니다" in row["message"], row

    # 관계의 끝점도 마찬가지다.
    if light["relations"]:
        edge = light["relations"][0]["plan"]["rows"][0]
        assert edge["action"] == "create"
        assert "이 묶음이 만드는 객체입니다" in edge["message"], edge

    # **아무것도 안 남았다** — 미리 보기다.
    assert (
        client.get(f"/api/objects/{names['tool']}", headers=admin.headers).status_code == 404
    )

    # 그대로 적용하면 들어간다.
    done = bundle_import(client, admin, {**bundle, "apply": True})
    assert done["applied"] is True, done
    rows = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()["items"]
    made = next(one for one in rows if one["key"] == "T-1")
    # 계획에서 비워 뒀던 참조가 적용에서는 풀렸다.
    detail = client.get(
        f"/api/objects/{names['tool']}/{made['id']}", headers=admin.headers
    ).json()
    assert detail["object"]["properties"]["vendor"]


def test_가벼운_미리보기_뒤에도_적용이_된다(client: TestClient, admin: Signed) -> None:
    """**지문이 거의 항상 안 맞았다.**

    지문은 「미리 본 것과 같은가」 를 묻는 자리인데, 계획만 본 것은 판정 자체가 적용과 다르다
    (이 묶음이 만들 객체를 가리키는 칸은 계획에서 비워 두고 적용에서 푼다). 그래서 가벼운
    미리 보기에는 지문을 붙이지 않고, **적용이 그 시점에 다시 판정한다** — 전부 아니면 무는
    그대로다. 백필이 같은 묶음 안의 객체를 가리키면 예전에는 여기서 늘 막혔다.
    """
    bundle, names = _bundle(admin.workspace)
    started = client.post(
        "/api/bundles/import", json={**bundle, "preview": "plan"}, headers=admin.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done", done
    assert done["result"]["preview"] == "plan"
    assert "다시 판정합니다" in done["result"]["note"]

    # **그 계획으로 그대로 적용된다** — 예전에는 「그 사이에 누군가 바꿨습니다」 였다.
    applied = client.post(f"/api/jobs/{done['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    finished = finish_job(client, admin, applied.json())
    assert finished["status"] == "done", finished
    assert finished["result"]["applied"] is True, finished["result"]
    rows = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()["items"]
    made = next(one for one in rows if one["key"] == "T-1")
    detail = client.get(
        f"/api/objects/{names['tool']}/{made['id']}", headers=admin.headers
    ).json()
    # 계획에서 비워 뒀던 참조가 적용에서 풀렸다.
    assert detail["object"]["properties"]["vendor"]


def _pair(workspace: str, *, acyclic: bool = False) -> tuple[dict[str, Any], dict[str, str]]:
    """타입 둘(기업 · 툴)과 **타입이 다른** 관계(툴 → 기업) 하나. 객체는 안 넣는다."""
    company, tool, made_by = _uniq("company"), _uniq("tool"), _uniq("madeby")
    ontology: dict[str, Any] = {
        "types": [
            {"slug": company, "label": "기업", "key_policy": "required"},
            {"slug": tool, "label": "툴", "key_policy": "required"},
        ],
        "relation_types": [
            {
                "slug": made_by,
                "label": "만든 곳",
                "src_type_slugs": [tool],
                "dst_type_slugs": [company] if not acyclic else [tool],
                "acyclic": acyclic,
            }
        ],
    }
    return {"ontology": ontology}, {"company": company, "tool": tool, "made_by": made_by}


def test_가벼운_미리보기가_새_출발점의_기존_도착점을_막지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """**새로 만드는 출발점 + 이미 있는 도착점**이 거짓 오류로 막혔다.

    계획만 볼 때 「이 묶음이 만들 것」 목록을 먼저 봤기 때문이다. 못 찾은 끝점이 하나라도
    있으면 도착 타입을 **출발 타입으로 짐작**해 끝 타입 검사를 했고, 타입이 다른 관계
    (툴 → 기업)는 전부 「도착은 기업만 됩니다」 로 걸렸다. 백필의 거의 모든 줄이 그 모양이다.
    """
    base, names = _pair(admin.workspace)
    first = _send(
        client,
        admin,
        {
            **base,
            "objects": [
                {
                    "type_slug": names["company"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "C-1", "label": "미국사"}],
                }
            ],
        },
        apply=True,
    )
    assert first["applied"] is True, first

    light = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "T-9", "label": "툴9"}],
                }
            ],
            "relations": [
                {
                    "type_slug": names["tool"],
                    "rows": [{"src": "T-9", "relation": names["made_by"], "dst": "C-1"}],
                }
            ],
            "preview": "plan",
        },
    )
    assert light["ok"] is True, light
    edge = light["relations"][0]["plan"]["rows"][0]
    assert edge["action"] == "create", edge
    assert "이 묶음이 만드는 객체입니다" in edge["message"], edge


def test_재적재는_가벼운_미리보기에서도_그대로다(client: TestClient, admin: Signed) -> None:
    """**두 번째로 넣는 같은 파일**이 전부 「새로 만든다」 로 보였다.

    객체가 이미 있는데도 「이 묶음이 만들 것」 목록을 먼저 봤기 때문이다. 그래서 백필을 다시
    돌릴 때 계획이 거짓말을 했고, 「파일대로 맞춤」 을 미리 보면 **이미 있는 선을 전부
    끊는 것**으로 보였다(그 계획에는 살아남을 선이 하나도 없다).
    """
    bundle, _names = _bundle(admin.workspace)
    assert _send(client, admin, bundle, apply=True)["applied"] is True

    again = bundle_import(client, admin, {**bundle, "preview": "plan"})
    assert again["ok"] is True, again
    assert again["counts"].get("objects_create", 0) == 0, again["counts"]
    assert again["counts"].get("relations_create", 0) == 0, again["counts"]
    assert again["counts"]["objects_unchanged"] == 3, again["counts"]
    assert again["counts"]["relations_unchanged"] == 1, again["counts"]

    # 「파일대로 맞춤」 이어도 파일에 있는 선은 끊지 않는다.
    replaced = bundle_import(
        client,
        admin,
        {
            **bundle,
            "preview": "plan",
            "relations": [{**bundle["relations"][0], "mode": "replace"}],
        },
    )
    assert replaced["ok"] is True, replaced
    assert replaced["counts"].get("relations_unlink", 0) == 0, replaced["counts"]


def test_가벼운_미리보기가_파일_안_순환을_본다(client: TestClient, admin: Signed) -> None:
    """아직 없는 끝점끼리도 고리를 만들 수 있다 — 계획이 그것을 봐야 한다.

    못 보면 계획은 「둘 다 새로 잇습니다」 라고 말하고, 적용이 마지막 줄에서 터져 묶음
    전체가 롤백된다. 사람은 통과한 계획을 보고 적용을 누른 것이다.
    """
    base, names = _pair(admin.workspace, acyclic=True)
    light = bundle_import(
        client,
        admin,
        {
            **base,
            "objects": [
                {
                    "type_slug": names["tool"],
                    "workspace_slug": admin.workspace,
                    "rows": [
                        {"key": "M-1", "label": "모델1"},
                        {"key": "M-2", "label": "모델2"},
                    ],
                }
            ],
            "relations": [
                {
                    "type_slug": names["tool"],
                    "rows": [
                        {"src": "M-1", "relation": names["made_by"], "dst": "M-2"},
                        {"src": "M-2", "relation": names["made_by"], "dst": "M-1"},
                    ],
                }
            ],
            "preview": "plan",
        },
    )
    assert light["ok"] is False, light
    rows = light["relations"][0]["plan"]["rows"]
    assert [one["action"] for one in rows] == ["create", "error"], rows
    assert "순환" in rows[1]["message"], rows


def test_필수_참조를_못_찾으면_그_줄만_건너뛴다(client: TestClient, admin: Signed) -> None:
    """`missing_refs=blank` 가 **필수 참조 칸에는 아무 소용이 없었다.**

    빈 값으로 두면 「값이 필요합니다」 가 되어 그 줄이 오류가 되고, 오류 한 줄이 묶음 전체를
    막는다 — 막으려고 켠 옵션인데 정작 필수 칸에서 안 들었다. 이제 그 줄만 건너뛴다.
    """
    company, tool = _uniq("company"), _uniq("tool")
    body = bundle_import(
        client,
        admin,
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
                                "required": True,
                            }
                        ],
                    },
                ]
            },
            "objects": [
                {
                    "type_slug": company,
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "C-1", "label": "미국사"}],
                },
                {
                    "type_slug": tool,
                    "workspace_slug": admin.workspace,
                    "rows": [
                        {"key": "T-1", "label": "툴1", "vendor": "C-1"},
                        {"key": "T-2", "label": "툴2", "vendor": "없는기업"},
                    ],
                },
            ],
            "missing_refs": "blank",
            "apply": True,
        },
    )
    assert body["ok"] is True and body["applied"] is True, body
    tools = next(one for one in body["objects"] if one["type_slug"] == tool)
    rows = tools["plan"]["rows"]
    assert rows[0]["action"] == "create", rows
    assert rows[1]["action"] == "unchanged", rows
    assert "필수 참조를 찾지 못해 이 줄을 건너뜁니다" in rows[1]["message"], rows

    keys = {
        one["key"]
        for one in client.get(f"/api/objects/{tool}", headers=admin.headers).json()["items"]
    }
    assert keys == {"T-1"}, keys


def test_바뀐_것이_없는_무덤만의_묶음은_판을_안_남긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """**적은 줄이 없으면 판도 없다**(`journal.finish`). 무덤만 담은 묶음이 아무것도 안 바꾸면
    판이 한 번도 DB 에 안 써진 채로 지우려 해 작업이 실패하거나, 빈 판이 목록에 남았다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    actor = db.scalars(select(User.id).where(User.email == admin.email)).one()

    def runs() -> int:
        return int(
            db.scalar(
                select(func.count()).select_from(BundleRun).where(BundleRun.actor_id == actor)
            )
            or 0
        )

    before = runs()
    done = bundle_import(
        client,
        admin,
        {
            "tombstones": {
                "objects": [{"type_slug": part, "key": "없는 것"}],
                "relations": [],
            },
            "source": "hub",
            "apply": True,
        },
    )
    assert done["applied"] is True and done["run_id"] is None, done
    assert done["tombstones"]["rows"][0]["action"] == "unchanged", done
    db.expire_all()
    assert runs() == before
