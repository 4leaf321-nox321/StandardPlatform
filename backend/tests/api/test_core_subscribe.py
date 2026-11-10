"""큰 기준정보를 코어 API 로(ADR 0055) — **정의는 「정의만」 묶음, 행은 묶음 구독이 쪽씩.**

허브의 진짜 코어 API 와 쌍둥이의 진짜 받는 쪽을 잇는다. 한 시험 DB 에 둘을 함께 둘 수 없어서
(`test_hub.py` 와 같다) 허브의 타입은 `hub<tag>`, 쌍둥이의 타입은 `twin<tag>` 이고, 그 사이의
httpx 가 slug 만 바꿔 앱에 그대로 넘긴다(`Forward`).

지키는 것:

- 지난 · 앞으로의 BOM 선이 둘 다 간다 — 기본 모양은 오늘 유효한 선만 준다.
- 선에 붙은 참조 칸은 상대의 식별자로 간다(예전에는 uuid 로 나갔다).
- 설명 · 별칭의 출처와 확인 표시가 간다(묶음과 같다).
- 허브에서 기간을 고치면 쌍둥이의 그 선을 **그 자리에서** 고치고(선 id 가 이어진다), 끊으면
  끊고, 새 기간은 새로 잇는다. 품번을 바꾸면 같은 것이 둘이 되지 않는다.
- 5만 줄에서 끊긴 받기는 다음 간격을 기다리지 않고 한 작업 안에서 끝까지 잇는다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.modules.datasources import odata, services
from app.modules.objects.aliases import HUMAN
from app.modules.objects.models import ObjectAlias, ObjectInstance, ObjectRelation
from app.modules.ontology.models import ObjectType
from app.shared.errors import code
from tests.api.conftest import Signed, bundle_import, clock_settled, finish_job

BASE = "http://sibling.local/api"


def _names(tag: str, side: str) -> dict[str, str]:
    return {
        "group": f"g{side}{tag}",
        "material": f"mat{side}{tag}",
        "part": f"part{side}{tag}",
        "memo": f"memo{side}{tag}",
        "bom": f"bom{side}{tag}",
    }


def _ontology(n: dict[str, str]) -> dict[str, Any]:
    return {
        "groups": [{"slug": n["group"], "label": "구독 시험"}],
        "types": [
            {
                "slug": n["material"],
                "label": "재질",
                "nav_group_slug": n["group"],
                "key_policy": "required",
            },
            {
                "slug": n["part"],
                "label": "부품",
                "nav_group_slug": n["group"],
                "key_policy": "required",
                "properties": [
                    {
                        "key": "material",
                        "label": "재질",
                        "data_type": "object_ref",
                        "ref_type_slug": n["material"],
                    }
                ],
            },
            # 허브가 코어로 공개하지 않는 타입 — 구독은 까닭을 적고 건너뛴다.
            {
                "slug": n["memo"],
                "label": "메모",
                "nav_group_slug": n["group"],
                "key_policy": "required",
            },
        ],
        "relation_types": [
            {
                "slug": n["bom"],
                "label": "BOM",
                "src_type_slugs": [n["part"]],
                "dst_type_slugs": [n["part"]],
                "cardinality": "many_to_many",
                "effectivity": True,
                "properties": [
                    {"key": "qty", "label": "수량", "data_type": "number"},
                    {
                        "key": "sub",
                        "label": "대체 재질",
                        "data_type": "object_ref",
                        "ref_type_slug": n["material"],
                    },
                ],
            }
        ],
    }


def _hub(client: TestClient, admin: Signed, tag: str) -> dict[str, str]:
    """허브 — 재질 둘, 부품 셋, A 의 BOM 은 3월 1일에 B 에서 C 로 바뀐다."""
    n = _names(tag, "hub")
    got = bundle_import(
        client,
        admin,
        {
            "ontology": _ontology(n),
            "objects": [
                {
                    "type_slug": n["material"],
                    "rows": [{"key": "AL", "label": "알루미늄"}, {"key": "ST", "label": "강"}],
                },
                {
                    "type_slug": n["part"],
                    "rows": [
                        {
                            "key": "A",
                            "label": "부품 A",
                            "material": "AL",
                            "description": "본체",
                            "aliases": [
                                {"value": "본체A", "source": "PLM", "verified": True},
                                {"value": "몸통"},
                            ],
                        },
                        {"key": "B", "label": "부품 B", "material": "ST"},
                        {"key": "C", "label": "부품 C"},
                    ],
                },
            ],
            "relations": [
                {
                    "type_slug": n["part"],
                    "rows": [
                        {
                            "src": "A",
                            "relation": n["bom"],
                            "dst": "B",
                            "valid_from": "2026-01-01",
                            "valid_to": "2026-03-01",
                            "properties": {"qty": 2, "sub": "ST"},
                        },
                        {
                            "src": "A",
                            "relation": n["bom"],
                            "dst": "C",
                            "valid_from": "2026-03-01",
                            "properties": {"qty": 1, "sub": "AL"},
                        },
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert got["applied"] is True, got
    for slug in (n["material"], n["part"]):
        opened = client.patch(
            f"/api/ontology/types/{slug}", json={"core": True}, headers=admin.headers
        )
        assert opened.status_code == 200, opened.text
    return n


def _twin(client: TestClient, admin: Signed, hub: dict[str, str], tag: str) -> dict[str, str]:
    """쌍둥이 — 허브의 묶음을 **정의만** 받는다(허브 관리가 선다)."""
    started = client.post(
        "/api/bundles/export",
        json={"group": hub["group"], "definitions_only": True},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done", done
    exported = client.get(f"/api/jobs/{done['id']}/download", headers=admin.headers).json()
    body = json.loads(
        json.dumps(exported, ensure_ascii=False).replace(f"hub{tag}", f"twin{tag}")
    )
    got = bundle_import(
        client, admin, {"ontology": body["ontology"], "source": "hub", "apply": True}
    )
    assert got["applied"] is True, got
    return _names(tag, "twin")


class Forward:
    """쌍둥이의 httpx → 이 앱의 코어 API — slug 만 바꾼다(`twin<tag>` ↔ `hub<tag>`)."""

    def __init__(self, client: TestClient, admin: Signed, tag: str) -> None:
        self.client = client
        self.admin = admin
        self.tag = tag
        self.old_hub = False
        """참이면 0.4.74 처럼 카탈로그에 `shapes` 가 없다."""
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path.replace(f"twin{self.tag}", f"hub{self.tag}")
        got = self.client.get(
            path, params=list(request.url.params.multi_items()), headers=self.admin.headers
        )
        body = got.json()
        if self.old_hub and isinstance(body, dict):
            body.pop("shapes", None)
        text = json.dumps(body, ensure_ascii=False).replace(
            f"hub{self.tag}", f"twin{self.tag}"
        )
        return httpx.Response(
            got.status_code,
            content=text.encode(),
            headers={"content-type": "application/json"},
        )


@pytest.fixture
def world(client: TestClient, admin: Signed) -> Iterator[tuple[dict[str, str], Forward]]:
    tag = uuid.uuid4().hex[:6]
    hub = _hub(client, admin, tag)
    twin = _twin(client, admin, hub, tag)
    forward = Forward(client, admin, tag)
    services.transport = httpx.MockTransport(forward)
    try:
        yield twin, forward
    finally:
        services.transport = None
    # hub 이름은 시험마다 다르므로 남겨 둔다(시험 DB 는 함께 쓴다 — 서로 안 겹친다).
    assert hub


def _subscribe(client: TestClient, admin: Signed, group: str, *, apply: bool) -> Any:
    return client.post(
        "/api/datasources/subscriptions",
        json={
            "base_url": BASE,
            "auth_kind": "bearer",
            "auth_secret": "sibling-token",
            "groups": [group],
            "apply": apply,
        },
        headers=admin.headers,
    )


def _sync(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    started = client.post(
        f"/api/datasources/{slug}/sync", params={"apply": "true"}, headers=admin.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _bom(slug: str, bom: str) -> dict[tuple[str, str], tuple[uuid.UUID, Any]]:
    """쌍둥이의 BOM 선 — (도착 식별자, 시작일) → (선 id, 붙은 값 · 끝날)."""
    with SessionLocal() as db:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == slug))
        keys = {
            one.id: one.key
            for one in db.scalars(
                select(ObjectInstance).where(ObjectInstance.type_id == type_id)
            )
        }
        out: dict[tuple[str, str], tuple[uuid.UUID, Any]] = {}
        for edge in db.scalars(select(ObjectRelation).where(ObjectRelation.relation == bom)):
            out[(str(keys[edge.dst_object_id]), str(edge.valid_from))] = (
                edge.id,
                (dict(edge.properties or {}), edge.valid_to),
            )
        return out


def test_구독은_공개된_타입마다_SP_모양의_소스를_만들고_기준정보를_온전히_받는다(
    client: TestClient,
    admin: Signed,
    world: tuple[dict[str, str], Forward],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    twin, forward = world

    # 미리 보기 — 만들지 않고 무엇을 만들지만. 공개 안 한 타입은 까닭과 함께 건너뛴다.
    planned = _subscribe(client, admin, twin["group"], apply=False)
    assert planned.status_code == 200, planned.text
    actions = {one["type_slug"]: one for one in planned.json()["items"]}
    assert actions[twin["material"]]["action"] == "create"
    assert actions[twin["part"]]["action"] == "create"
    assert actions[twin["part"]]["relations"] is True
    assert actions[twin["part"]]["source_name"] == "hub"
    assert actions[twin["memo"]]["action"] == "skip"
    assert "코어 공개" in actions[twin["memo"]]["reason"]
    listed = client.get("/api/datasources", headers=admin.headers).json()
    assert not [one for one in listed if one["entity_set"] in (twin["material"], twin["part"])]

    made = _subscribe(client, admin, twin["group"], apply=True)
    assert made.status_code == 200, made.text
    assert made.json()["created"] == 2
    slugs = {one["type_slug"]: one["source_slug"] for one in made.json()["items"]}
    source = client.get(
        f"/api/datasources/{slugs[twin['part']]}", headers=admin.headers
    ).json()
    assert source["options"]["shape"] == "sp" and source["options"]["relations"] is True
    # 두 번 구독하면 두 번 만들지 않는다 — 같은 객체를 두 소스가 번갈아 고친다.
    again = _subscribe(client, admin, twin["group"], apply=True).json()
    assert again["created"] == 0
    assert {one["action"] for one in again["items"]} == {"exists", "skip"}

    # **처음 받기는 끊겨도 한 작업 안에서 끝까지 잇는다** — 한 차례에 한 줄씩으로 줄여 본다.
    monkeypatch.setattr(odata, "MAX_ROWS", 1)
    for slug in (slugs[twin["material"]], slugs[twin["part"]]):
        patched = client.patch(
            f"/api/datasources/{slug}", json={"page_size": 1}, headers=admin.headers
        )
        assert patched.status_code == 200, patched.text
    assert _sync(client, admin, slugs[twin["material"]]).get("rounds") == 2
    first = _sync(client, admin, slugs[twin["part"]])
    assert first.get("rounds", 1) >= 3, first
    monkeypatch.setattr(odata, "MAX_ROWS", 50_000)

    parts = client.get(f"/api/objects/{twin['part']}?limit=50", headers=admin.headers).json()
    by_key = {one["key"]: one for one in parts["items"]}
    assert set(by_key) == {"A", "B", "C"}
    a = client.get(f"/api/objects/{twin['part']}/{by_key['A']['id']}", headers=admin.headers)
    assert a.json()["object"]["description"] == "본체"
    # 별칭은 출처 · 확인 표시까지 — 이름만 이으면 허브에서 확인한 것이 영영 검수 대기다.
    with SessionLocal() as db:
        found = {
            one.value: one
            for one in db.scalars(
                select(ObjectAlias).where(
                    ObjectAlias.object_id == uuid.UUID(by_key["A"]["id"]),
                    ObjectAlias.kind == HUMAN,
                )
            )
        }
    assert found["본체A"].verified_at is not None and found["본체A"].source == "PLM"
    assert found["몸통"].verified_at is None

    # **지난 · 앞으로의 BOM 이 둘 다** — 기본 모양이면 오늘 유효한 하나만 왔다.
    bom = _bom(twin["part"], twin["bom"])
    assert set(bom) == {("B", "2026-01-01"), ("C", "2026-03-01")}
    b_edge, (b_values, b_to) = bom[("B", "2026-01-01")]
    assert b_to == date(2026, 3, 1)
    # 선에 붙은 참조 칸은 쌍둥이의 재질로 풀린다(허브가 식별자로 보냈다).
    materials = client.get(f"/api/objects/{twin['material']}", headers=admin.headers).json()
    steel = next(one["id"] for one in materials["items"] if one["key"] == "ST")
    assert b_values == {"qty": 2, "sub": steel}
    assert any("shape=sp" in str(one.url) for one in forward.seen)

    # --- 허브에서 바꾼다: B 의 끝날을 당기고, C 를 끊고, D 를 새 기간으로 — B 는 B-2 로.
    clock_settled()
    hub = _names(twin["group"][len("gtwin") :], "hub")
    changed = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": hub["part"],
                    "rows": [
                        {
                            "key": "B-2",
                            "label": "부품 B",
                            "renamed_from": "B",
                            "material": "ST",
                        },
                        {"key": "D", "label": "부품 D"},
                    ],
                }
            ],
            "relations": [
                {
                    "type_slug": hub["part"],
                    "mode": "replace",
                    "rows": [
                        {
                            "src": "A",
                            "relation": hub["bom"],
                            "dst": "B-2",
                            "valid_from": "2026-01-01",
                            "valid_to": "2026-02-01",
                            "properties": {"qty": 2, "sub": "ST"},
                        },
                        {
                            "src": "A",
                            "relation": hub["bom"],
                            "dst": "D",
                            "valid_from": "2026-02-01",
                            "valid_to": "2026-03-01",
                            "properties": {"qty": 4},
                        },
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert changed["applied"] is True, changed
    _sync(client, admin, slugs[twin["part"]])

    parts = client.get(f"/api/objects/{twin['part']}?limit=50", headers=admin.headers).json()
    # 품번이 바뀌어도 같은 것이 둘이 되지 않는다.
    assert sorted(one["key"] for one in parts["items"]) == ["A", "B-2", "C", "D"]
    bom = _bom(twin["part"], twin["bom"])
    assert set(bom) == {("B-2", "2026-01-01"), ("D", "2026-02-01")}
    # **기간만 바뀐 선은 그 자리에서 고친다** — 끊고 다시 이으면 선 id · 이력이 끊긴다.
    assert bom[("B-2", "2026-01-01")][0] == b_edge
    assert bom[("B-2", "2026-01-01")][1][1] == date(2026, 2, 1)
    assert bom[("D", "2026-02-01")][1][0] == {"qty": 4}


def test_기본_모양은_그대로_오늘의_선이고_SP_모양을_모르는_상대는_구독을_거절한다(
    client: TestClient, admin: Signed, world: tuple[dict[str, str], Forward]
) -> None:
    """바깥 시스템(RA · 연동 키트)의 봉투는 안 바뀐다 — `extra` · `periods` 는 비어 있다.
    0.4.74 까지의 허브(카탈로그에 `shapes` 가 없다)를 구독하면 기간이 다른 선이 빠지므로
    거절한다."""
    twin, forward = world
    hub = _names(twin["group"][len("gtwin") :], "hub")
    plain = client.get(f"/api/core/{hub['part']}/relations", headers=admin.headers).json()
    assert [one["periods"] for one in plain["items"]] == [None] * len(plain["items"])
    # 오늘(2026-11) 유효한 것만 — 그리고 선의 참조 칸은 식별자다(예전에는 uuid 가 나갔다).
    assert [(one["dst"], one["properties"]) for one in plain["items"]] == [
        ("C", {"qty": 1, "sub": "AL"})
    ]
    rows = client.get(f"/api/core/{hub['part']}", headers=admin.headers).json()["items"]
    assert {one["extra"] is None for one in rows} == {True}
    full = client.get(
        f"/api/core/{hub['part']}", params={"shape": "sp"}, headers=admin.headers
    ).json()["items"]
    assert next(one for one in full if one["key"] == "A")["extra"]["description"] == "본체"

    forward.old_hub = True
    refused = _subscribe(client, admin, twin["group"], apply=False)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == code("DATASOURCES", 60)
