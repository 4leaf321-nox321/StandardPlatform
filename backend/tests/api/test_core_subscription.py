"""코어 구독(ADR 0085) — **허브의 코어를 통째로**, 토큰은 한 번만.

허브의 진짜 코어 API 와 쌍둥이의 진짜 받는 쪽을 잇는다(`test_core_subscribe.py` 와 같은 무늬 —
한 시험 DB 에 둘을 두고, 그 사이의 httpx 가 `hub<tag>` ↔ `twin<tag>` 만 바꾼다). 허브의
카탈로그는 이 시험의 타입만 보이게 거른다 — 시험 DB 를 함께 쓰는 다른 시험의 공개 타입이 섞이지
않게.

지키는 것:

- 구독 하나가 정의를 세우고, 공개 타입마다 소스를 만들고, 행 · 선을 끝까지 받는다(정의 묶음
  파일을 따로 받지 않는다).
- 허브에 공개 타입 · 칸이 늘면 **다음 확인이** 정의를 세우고 소스 · 칸 대응을 맞추고 새 칸의
  값을 받는다 — 토큰을 다시 넣지 않는다.
- 저장값을 잃을 수 있는 변경(고를 값 빼기)은 정의 검토 대기로 남기고, 같은 때의 안전한
  변경(새 칸)은 들어간다. 검토한 지문으로 적용하면 들어간다.
- 공개를 해제한 타입은 수신만 멈추고 받은 자료는 남는다 — 다시 공개되면 이어 받는다.
- 이 설치가 만든 같은 이름의 타입은 건드리지 않는다.
- 같은 허브를 가리키던 형제 코어 소스는 구독이 맡고, 토큰은 한 곳에서 바뀐다.
- 정의 창구가 없는 허브(0.4.90 까지)에서는 이 설치에 정의가 있는 타입만 받는다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.modules.datasources import services
from app.modules.datasources.models import CoreSubscription, DataSource
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared.errors import code
from tests.api.conftest import (
    Signed,
    bundle_import,
    clock_settled,
    finish_job,
    maintenance_counts,
)
from tests.api.test_core_subscribe import _hub, _names, _twin


class Hub:
    """쌍둥이의 httpx → 이 앱의 코어 API — slug 를 바꾸고(경로 · 쿼리 · 본문), 카탈로그는 이
    시험의 타입만 남긴다."""

    def __init__(self, client: TestClient, admin: Signed, tag: str) -> None:
        self.client = client
        self.admin = admin
        self.tag = tag
        self.no_definitions = False
        """참이면 0.4.90 까지처럼 정의 창구가 없다(404 — 그 주소를 타입으로 읽는다)."""
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        hub, twin = f"hub{self.tag}", f"twin{self.tag}"
        path = request.url.path.replace(twin, hub)
        if self.no_definitions and path.endswith("/core/sp-definitions"):
            return httpx.Response(
                404,
                json={
                    "error": {
                        "code": code("CORE", 1),
                        "message": "열려 있는 코어 타입이 아닙니다",
                    }
                },
            )
        params = [(k, v.replace(twin, hub)) for k, v in request.url.params.multi_items()]
        got = self.client.get(path, params=params, headers=self.admin.headers)
        body = got.json()
        if path.rstrip("/").endswith("/core") and isinstance(body, dict):
            body["types"] = [one for one in body.get("types") or [] if hub in one["slug"]]
        text = json.dumps(body, ensure_ascii=False).replace(hub, twin)
        return httpx.Response(
            got.status_code,
            content=text.encode(),
            headers={"content-type": "application/json"},
        )

    def definitions_asked(self) -> list[httpx.Request]:
        return [one for one in self.seen if one.url.path.endswith("/sp-definitions")]


def _base(tag: str) -> str:
    """허브 주소 — 시험마다 다르게(구독은 허브마다 하나이고, 시험 DB 를 함께 쓴다)."""
    return f"http://hub-{tag}.local/api"


@pytest.fixture
def world(client: TestClient, admin: Signed) -> Iterator[tuple[str, Hub]]:
    tag = uuid.uuid4().hex[:6]
    _hub(client, admin, tag)
    hub = Hub(client, admin, tag)
    services.transport = httpx.MockTransport(hub)
    try:
        yield tag, hub
    finally:
        services.transport = None


def _create(
    client: TestClient,
    admin: Signed,
    group: str,
    *,
    apply: bool,
    secret: str = "hub-token",
) -> Any:
    tag = group.removeprefix("gtwin")
    return client.post(
        "/api/datasources/core-subscriptions",
        json={
            "base_url": _base(tag),
            "auth_kind": "bearer",
            "auth_secret": secret,
            "groups": [group],
            "interval_minutes": 60,
            "apply": apply,
        },
        headers=admin.headers,
    )


def _subscribed(client: TestClient, admin: Signed, group: str) -> dict[str, Any]:
    """구독을 만들고 첫 동기화 작업을 끝까지 — (구독, 작업 결과)."""
    made = _create(client, admin, group, apply=True)
    assert made.status_code == 200, made.text
    body = made.json()
    done = finish_job(client, admin, {"id": body["job_id"]})
    assert done["status"] == "done", done
    return {"id": body["subscription"]["id"], "result": done["result"]}


def _check(client: TestClient, admin: Signed, subscription_id: str) -> dict[str, Any]:
    started = client.post(
        f"/api/datasources/core-subscriptions/{subscription_id}/sync", headers=admin.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _one(client: TestClient, admin: Signed, subscription_id: str) -> dict[str, Any]:
    listed = client.get("/api/datasources/core-subscriptions", headers=admin.headers)
    assert listed.status_code == 200, listed.text
    return next(one for one in listed.json() if one["id"] == subscription_id)


def _count(slug: str) -> int:
    with SessionLocal() as db:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == slug))
        return int(
            db.scalar(
                select(func.count())
                .select_from(ObjectInstance)
                .where(ObjectInstance.type_id == type_id, ObjectInstance.deleted_at.is_(None))
            )
            or 0
        )


def _value(slug: str, key: str, field: str) -> Any:
    with SessionLocal() as db:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == slug))
        row = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.type_id == type_id, ObjectInstance.key == key
            )
        )
        assert row is not None
        return (row.properties or {}).get(field)


def _property(slug: str, key: str) -> PropertyDef | None:
    with SessionLocal() as db:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == slug))
        return db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == type_id,
                PropertyDef.key == key,
            )
        )


def _sources(subscription_id: str) -> dict[str, DataSource]:
    with SessionLocal() as db:
        return {
            one.entity_set: one
            for one in db.scalars(
                select(DataSource).where(
                    DataSource.subscription_id == uuid.UUID(subscription_id)
                )
            )
        }


def test_정의_창구는_공개_타입만_싣고_지문이_같으면_본문을_뺀다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, _ = world
    n = _names(tag, "hub")
    got = client.get(
        "/api/core/sp-definitions", params={"groups": n["group"]}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["changed"] is True
    slugs = {one["slug"] for one in body["ontology"]["types"]}
    # 공개 안 한 메모는 안 실린다 — 묶음 내보내기는 묶음의 타입을 다 싣는다.
    assert slugs == {n["material"], n["part"]}
    assert [one["slug"] for one in body["ontology"]["relation_types"]] == [n["bom"]]
    assert [one["slug"] for one in body["groups"]] == [n["group"]]
    assert f"{n['part']}.material" in body["added"]

    same = client.get(
        "/api/core/sp-definitions",
        params={"groups": n["group"], "known": body["mark"]},
        headers=admin.headers,
    ).json()
    assert same["changed"] is False and same["ontology"] is None
    assert same["mark"] == body["mark"]

    # 없는 묶음은 그렇다고 — 정의 창구가 없는 옛 허브의 404 와 구별된다.
    missing = client.get(
        "/api/core/sp-definitions", params={"groups": "nope"}, headers=admin.headers
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == code("CORE", 4)

    # 좁은 토큰(`core:read`)으로 읽는다 — 묶음 내보내기는 시스템 관리자 토큰이 필요하다.
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"twin-{tag}", "scopes": ["core:read"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    narrow = client.get(
        "/api/core/sp-definitions",
        params={"groups": n["group"]},
        headers={"Authorization": f"Bearer {made.json()['token']}"},
    )
    assert narrow.status_code == 200, narrow.text


def test_구독은_정의를_세우고_공개_타입마다_소스를_만들어_행과_선을_받는다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, hub = world
    twin = _names(tag, "twin")

    planned = _create(client, admin, twin["group"], apply=False)
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert plan["definitions"] is True
    assert {one["type_slug"]: one["action"] for one in plan["types"]} == {
        twin["material"]: "define",
        twin["part"]: "define",
    }
    assert [one["slug"] for one in plan["groups"]] == [twin["group"]]
    assert plan["held"] == []
    # 미리 보기는 아무것도 안 남긴다.
    with SessionLocal() as db:
        assert db.scalar(select(ObjectType).where(ObjectType.slug == twin["part"])) is None
        assert (
            db.scalar(select(CoreSubscription).where(CoreSubscription.base_url == _base(tag)))
            is None
        )

    made = _subscribed(client, admin, twin["group"])
    assert sorted(made["result"]["created"]) == sorted(
        [f"{twin['material']}_core", f"{twin['part']}_core"]
    )
    with SessionLocal() as db:
        part = db.scalar(select(ObjectType).where(ObjectType.slug == twin["part"]))
        assert part is not None and part.managed_by == "hub"
    assert _count(twin["material"]) == 2
    assert _count(twin["part"]) == 3
    assert _value(twin["part"], "A", "material") is not None
    with SessionLocal() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(ObjectRelation).where(ObjectRelation.relation == twin["bom"])
                    )
                )
            )
            == 2
        )
    sources = _sources(made["id"])
    assert {one.auth.get("secret") for one in sources.values()} == {"hub-token"}

    # 두 번째 확인 — 정의가 그대로면 본문 없이(지문으로) 묻고, 아무것도 새로 안 만든다.
    asked = len(hub.definitions_asked())
    again = _check(client, admin, made["id"])
    assert again["created"] == [] and again["definitions_applied"] == 0
    last = hub.definitions_asked()[asked:]
    assert last and last[-1].url.params.get("known")
    shown = _one(client, admin, made["id"])
    assert shown["last_status"] == "ok" and shown["held"] is None
    assert {one["type_slug"] for one in shown["sources"]} == {twin["material"], twin["part"]}

    # 같은 허브로 둘은 안 만든다.
    twice = _create(client, admin, twin["group"], apply=False)
    assert twice.status_code == 409


def test_허브에_공개_타입과_칸이_늘면_다음_확인이_정의와_소스와_값을_맞춘다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, _ = world
    n, twin = _names(tag, "hub"), _names(tag, "twin")
    made = _subscribed(client, admin, twin["group"])

    # 허브: 메모를 공개하고, 부품에 칸을 하나 더해 값을 채운다.
    clock_settled()
    opened = client.patch(
        f"/api/ontology/types/{n['memo']}", json={"core": True}, headers=admin.headers
    )
    assert opened.status_code == 200, opened.text
    created = client.post(
        f"/api/ontology/types/{n['part']}/properties",
        json={"key": "weight", "label": "무게", "data_type": "number"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    bundle_import(
        client,
        admin,
        {
            "objects": [
                {"type_slug": n["memo"], "rows": [{"key": "M1", "label": "메모 1"}]},
                {
                    "type_slug": n["part"],
                    "rows": [{"key": "A", "label": "부품 A", "weight": 2.5}],
                },
            ],
            "apply": True,
        },
    )

    got = _check(client, admin, made["id"])
    assert got["created"] == [f"{twin['memo']}_core"], got
    assert got["definitions_applied"] > 0
    # 새 칸 — 정의가 서고, 칸 대응에 들어가고, 값이 왔다(처음부터 다시 받지 않고 바뀐 것만).
    assert _property(twin["part"], "weight") is not None
    assert _value(twin["part"], "A", "weight") == 2.5
    assert f"{twin['part']}_core" not in got["restarted"]
    # 새 타입 — 토큰을 다시 넣지 않았는데 같은 토큰으로 받는다.
    assert _count(twin["memo"]) == 1
    sources = _sources(made["id"])
    assert sources[twin["memo"]].auth.get("secret") == "hub-token"
    assert any(
        one.get("source") == "weight" for one in sources[twin["part"]].mapping["columns"]
    )


def test_값을_잃을_수_있는_변경은_정의_검토_대기로_남기고_적용하면_들어간다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, _ = world
    n, twin = _names(tag, "hub"), _names(tag, "twin")
    color = {
        "key": "color",
        "label": "색",
        "data_type": "enum",
        "enum_options": ["빨강", "파랑"],
    }
    assert (
        client.post(
            f"/api/ontology/types/{n['part']}/properties", json=color, headers=admin.headers
        ).status_code
        == 201
    )
    made = _subscribed(client, admin, twin["group"])
    assert _property(twin["part"], "color") is not None
    held_before = maintenance_counts(client, admin).get("core_subscription_held", 0)

    # 허브: 고를 값에서 「파랑」 을 빼고(저장값을 잃을 수 있다), 칸 하나를 더한다(안전하다).
    narrowed = client.patch(
        f"/api/ontology/types/{n['part']}/properties/color",
        json={**color, "enum_options": ["빨강"]},
        headers=admin.headers,
    )
    assert narrowed.status_code == 200, narrowed.text
    assert (
        client.post(
            f"/api/ontology/types/{n['part']}/properties",
            json={"key": "memo", "label": "비고", "data_type": "text"},
            headers=admin.headers,
        ).status_code
        == 201
    )

    _check(client, admin, made["id"])
    shown = _one(client, admin, made["id"])
    assert shown["last_status"] == "held"
    assert shown["held"] and any("파랑" in one for one in shown["held"]["lines"]), shown
    kept = _property(twin["part"], "color")
    assert kept is not None and kept.enum_options == ["빨강", "파랑"]
    assert _property(twin["part"], "memo") is not None  # 안전한 것은 들어갔다
    # 홈의 「남은 일」 이 말한다 — 검토 대기는 받기를 막지 않아 아무도 모르는 채 쌓인다.
    assert (
        maintenance_counts(client, admin).get("core_subscription_held", 0) == held_before + 1
    )

    # 다른 지문으로는 안 된다.
    wrong = client.post(
        f"/api/datasources/core-subscriptions/{made['id']}/held/apply",
        json={"mark": "nope"},
        headers=admin.headers,
    )
    assert wrong.status_code == 409

    started = client.post(
        f"/api/datasources/core-subscriptions/{made['id']}/held/apply",
        json={"mark": shown["held"]["mark"]},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done", done
    applied = _property(twin["part"], "color")
    assert applied is not None and applied.enum_options == ["빨강"]
    after = _one(client, admin, made["id"])
    assert after["held"] is None and after["last_status"] == "ok"
    assert maintenance_counts(client, admin).get("core_subscription_held", 0) == held_before


def test_공개를_해제한_타입은_수신만_멈추고_받은_자료는_남는다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, _ = world
    n, twin = _names(tag, "hub"), _names(tag, "twin")
    made = _subscribed(client, admin, twin["group"])

    closed = client.patch(
        f"/api/ontology/types/{n['part']}", json={"core": False}, headers=admin.headers
    )
    assert closed.status_code == 200, closed.text
    got = _check(client, admin, made["id"])
    assert got["paused"] == [f"{twin['part']}_core"], got
    part = _sources(made["id"])[twin["part"]]
    assert part.is_active is False
    assert _count(twin["part"]) == 3  # 받은 자료는 남는다
    shown = _one(client, admin, made["id"])
    assert next(one for one in shown["sources"] if one["type_slug"] == twin["part"])["paused"]

    reopened = client.patch(
        f"/api/ontology/types/{n['part']}", json={"core": True}, headers=admin.headers
    )
    assert reopened.status_code == 200, reopened.text
    again = _check(client, admin, made["id"])
    assert again["resumed"] == [f"{twin['part']}_core"], again
    assert _sources(made["id"])[twin["part"]].is_active is True

    # 사람이 비활성화한 소스는 다시 공개돼도 그대로 둔다.
    patched = client.patch(
        f"/api/datasources/{twin['material']}_core",
        json={"is_active": False},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    _check(client, admin, made["id"])
    assert _sources(made["id"])[twin["material"]].is_active is False


def test_이_설치가_만든_같은_이름의_타입은_건드리지_않는다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, _ = world
    twin = _names(tag, "twin")
    mine = client.post(
        "/api/ontology/types",
        json={"slug": twin["material"], "label": "우리 재질"},
        headers=admin.headers,
    )
    assert mine.status_code == 201, mine.text

    plan = _create(client, admin, twin["group"], apply=False).json()
    actions = {one["type_slug"]: one for one in plan["types"]}
    assert actions[twin["material"]]["action"] == "skip"
    assert "같은 이름" in actions[twin["material"]]["reason"]
    assert any("참조 칸 material" in one for one in plan["notes"]), plan["notes"]

    made = _subscribed(client, admin, twin["group"])
    with SessionLocal() as db:
        own = db.scalar(select(ObjectType).where(ObjectType.slug == twin["material"]))
        assert own is not None and own.managed_by == "" and own.label == "우리 재질"
    assert twin["material"] not in _sources(made["id"])
    assert _count(twin["material"]) == 0
    assert _count(twin["part"]) == 3


def test_같은_허브의_형제_코어_소스는_구독이_맡고_토큰을_한_곳에서_바꾼다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, _ = world
    n = _names(tag, "hub")
    twin = _twin(client, admin, n, tag)
    old = client.post(
        "/api/datasources/subscriptions",
        json={
            "base_url": _base(tag),
            "auth_kind": "bearer",
            "auth_secret": "sibling-token",
            "groups": [twin["group"]],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert old.status_code == 200, old.text
    for one in old.json()["items"]:
        if one["source_slug"]:
            done = finish_job(
                client,
                admin,
                client.post(
                    f"/api/datasources/{one['source_slug']}/sync",
                    params={"apply": "true"},
                    headers=admin.headers,
                ).json(),
            )
            assert done["status"] == "done", done
    # 옛 소스의 칸 대응에서 「재질」 이 빠진 채로 받아 둔 것 — 구독이 그 칸을 이으면 이미 받은
    # 행에도 값이 있으니 처음부터 다시 받아야 한다.
    with SessionLocal() as db:
        part = db.scalar(select(DataSource).where(DataSource.slug == f"{twin['part']}_core"))
        assert part is not None
        part.mapping = {
            **part.mapping,
            "columns": [one for one in part.mapping["columns"] if one["source"] != "material"],
        }
        db.commit()

    plan = _create(client, admin, twin["group"], apply=False, secret="new-token").json()
    assert {one["action"] for one in plan["types"]} == {"adopt"}

    made = client.post(
        "/api/datasources/core-subscriptions",
        json={
            "base_url": _base(tag),
            "auth_secret": "new-token",
            "groups": [twin["group"]],
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    done = finish_job(client, admin, {"id": made["job_id"]})
    assert done["status"] == "done", done
    result = done["result"]
    assert result["created"] == []
    assert sorted(result["adopted"]) == sorted(
        [f"{twin['material']}_core", f"{twin['part']}_core"]
    )
    assert result["restarted"] == [f"{twin['part']}_core"]
    assert _value(twin["part"], "A", "material") is not None
    sources = _sources(made["subscription"]["id"])
    assert {one.auth.get("secret") for one in sources.values()} == {"new-token"}

    rotated = client.patch(
        f"/api/datasources/core-subscriptions/{made['subscription']['id']}",
        json={"auth_secret": "rotated"},
        headers=admin.headers,
    )
    assert rotated.status_code == 200, rotated.text
    assert {
        one.auth.get("secret") for one in _sources(made["subscription"]["id"]).values()
    } == {"rotated"}

    # 구독을 삭제해도 소스와 자료는 남는다.
    removed = client.delete(
        f"/api/datasources/core-subscriptions/{made['subscription']['id']}",
        headers=admin.headers,
    )
    assert removed.status_code == 204, removed.text
    with SessionLocal() as db:
        left = db.scalar(select(DataSource).where(DataSource.slug == f"{twin['part']}_core"))
        assert left is not None and left.subscription_id is None
    assert _count(twin["part"]) == 3


def test_정의_창구가_없는_허브는_이_설치에_정의가_있는_타입만_받는다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    tag, hub = world
    twin = _twin(client, admin, _names(tag, "hub"), tag)
    hub.no_definitions = True

    plan = _create(client, admin, twin["group"], apply=False).json()
    assert plan["definitions"] is False
    assert any("정의 창구가 없습니다" in one for one in plan["notes"])
    assert {one["type_slug"]: one["action"] for one in plan["types"]} == {
        twin["material"]: "create",
        twin["part"]: "create",
    }
    made = _subscribed(client, admin, twin["group"])
    assert sorted(made["result"]["created"]) == sorted(
        [f"{twin['material']}_core", f"{twin['part']}_core"]
    )
    assert _count(twin["part"]) == 3


def test_구독의_소스는_구독과_같은_차례에_돈다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    """따로 돌면 허브에 새 칸이 생긴 직후 소스가 먼저 받고, 나중에 구독이 그 칸을 이을 때 이미
    받은 행을 처음부터 다시 받게 된다 — 그래서 타이머는 구독을 확인한 뒤 그 소스를 함께
    돌린다."""
    from app.modules.datasources import subscriptions
    from app.modules.jobs import services as job_services

    tag, _ = world
    twin = _names(tag, "twin")
    made = _subscribed(client, admin, twin["group"])
    mine = {f"{twin['material']}_core", f"{twin['part']}_core"}
    with SessionLocal() as db:
        row = db.get(CoreSubscription, uuid.UUID(made["id"]))
        assert row is not None
        # 한 시간이 지났다 — 소스도 구독도 차례가 됐다.
        row.checked_at = (
            row.checked_at.replace(year=row.checked_at.year - 1) if row.checked_at else None
        )
        for one in db.scalars(select(DataSource).where(DataSource.slug.in_(mine))):
            one.last_run_at = row.checked_at
        db.commit()
        assert not mine & {one.slug for one in services.due(db)}
        assert row.id in {one.id for one in subscriptions.due(db)}
        job = job_services.enqueue(
            db,
            kind="datasource_sync_round",
            params={"slugs": [], "apply": True, "subscriptions": [made["id"]]},
            user=None,
            workspace_id=None,
        )
        db.commit()
        job_id = str(job.id)
    done = finish_job(client, admin, {"id": job_id})
    assert done["status"] == "done", done
    result = done["result"]
    assert [one["status"] for one in result["subscriptions"]] == ["ok"]
    assert mine <= {one["slug"] for one in result["sources"]}
    # 구독 → 가리키는 쪽(재질)부터.
    assert result["order"].index(f"{twin['material']}_core") < result["order"].index(
        f"{twin['part']}_core"
    )


def test_다른_주소의_형제_코어_소스가_받는_타입은_건너뛰고_주소를_맞추면_맡는다(
    client: TestClient, admin: Signed, world: tuple[str, Hub]
) -> None:
    """같은 허브를 다른 표기(IP · 호스트 이름)로 적은 소스는 넘겨받지 못한다 — 그대로 두면
    구독이 같은 타입에 소스를 하나 더 만들어 둘이 번갈아 받는다. 그래서 그 타입은 건너뛰고
    까닭을
    말한다."""
    tag, _ = world
    twin = _twin(client, admin, _names(tag, "hub"), tag)
    alias = f"http://hub-{tag}-alias.local/api"
    old = client.post(
        "/api/datasources/subscriptions",
        json={
            "base_url": alias,
            "auth_kind": "bearer",
            "auth_secret": "old-token",
            "groups": [twin["group"]],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert old.status_code == 200, old.text
    made_old = {one["type_slug"]: one["source_slug"] for one in old.json()["items"]}
    other = made_old[twin["part"]]
    # 재질은 옛 소스를 지웠다 — 구독이 새로 만든다.
    removed = client.delete(
        f"/api/datasources/{made_old[twin['material']]}", headers=admin.headers
    )
    assert removed.status_code == 204, removed.text

    plan = _create(client, admin, twin["group"], apply=False).json()
    actions = {one["type_slug"]: one for one in plan["types"]}
    assert actions[twin["material"]]["action"] == "create"
    assert actions[twin["part"]]["action"] == "skip"
    assert "다른 주소" in actions[twin["part"]]["reason"]
    assert actions[twin["part"]]["source_slug"] == other

    made = _subscribed(client, admin, twin["group"])
    assert made["result"]["created"] == [f"{twin['material']}_core"]
    assert any("다른 주소" in one for one in made["result"]["notes"])
    assert twin["part"] not in _sources(made["id"])
    with SessionLocal() as db:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == twin["part"]))
        assert (
            len(
                list(
                    db.scalars(
                        select(DataSource).where(
                            DataSource.kind == "sp_core", DataSource.type_id == type_id
                        )
                    )
                )
            )
            == 1
        )

    # 그 소스의 주소를 구독 주소와 같게 수정하면 다음 확인이 맡는다(새로 만들지 않는다).
    fixed = client.patch(
        f"/api/datasources/{other}", json={"base_url": _base(tag)}, headers=admin.headers
    )
    assert fixed.status_code == 200, fixed.text
    again = _check(client, admin, made["id"])
    assert again["adopted"] == [other], again
    assert again["created"] == []
    assert _sources(made["id"])[twin["part"]].auth.get("secret") == "hub-token"
