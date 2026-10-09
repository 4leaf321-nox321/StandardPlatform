"""코어 API — **끝이 인터페이스인 관계 종류도 연다**(ADR 0006).

예전에는 끝에 인터페이스를 적은 종류를 아예 안 열어(「코어는 타입만 약속한다」), 허브 → 쌍둥이
동기화에서 그 선이 통째로 빠졌다(2026-10-08). 여기서 지키는 것: 그 종류가 열리나, **선마다
끝 객체의 타입이 열린 것만** 나가나(구현했어도 안 연 타입을 가리키는 선은 안 나간다),
카탈로그가 그 약속(인터페이스와 그 열린 구현 타입)을 말하나, 구현 타입이 열리면 판이 바뀌나,
그리고 같은 식별자가 두 구현 타입에 있어도 `dst_type` 으로 갈리나.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology.models import ObjectType
from tests.api.conftest import Signed
from tests.api.test_core_api import _open, _relations
from tests.api.test_datasource_core import (
    FakeCore,
    _edge,
    _source,
    _sync,
    _vendor_type,
    sibling,
)
from tests.api.test_interfaces import _make_interface
from tests.api.test_ontology import _make_object, _make_relation, _make_type, _uniq

__all__ = ["sibling"]  # 가짜 형제 설치(픽스처)를 이 파일에서도 쓴다


def _world(client: TestClient, admin: Signed) -> dict[str, str]:
    """부품(열림) -사용-> 설비(인터페이스). 시험장비 · 생산설비가 설비를 구현하고, 시험장비만
    연다."""
    iface = _make_interface(client, admin, label="설비")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    tester = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    plant = _make_type(
        client, admin, label="생산설비", key_policy="optional", interface_slugs=[iface]
    )
    kind = _make_relation(
        client, admin, "uses", label="사용", src_type_slugs=[part], dst_type_slugs=[iface]
    )
    _open(client, admin, part)
    _open(client, admin, tester)
    return {"iface": iface, "part": part, "tester": tester, "plant": plant, "kind": kind}


def _entry(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    catalog = client.get("/api/core", headers=admin.headers).json()
    return dict(next(one for one in catalog["types"] if one["slug"] == slug))


def _import(client: TestClient, admin: Signed, part: str, rows: list[dict[str, Any]]) -> Any:
    done = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={"rows": rows, "apply": True},
        headers=admin.headers,
    )
    assert done.status_code == 200, done.text
    return done.json()


def test_끝이_인터페이스인_종류는_열린_구현_타입을_가리키는_선만_나간다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    _make_object(client, admin, w["part"], label="볼트", key="P-1")
    _make_object(client, admin, w["tester"], label="인장 시험기", key="T-1")
    _make_object(client, admin, w["plant"], label="프레스", key="X-1")
    _import(
        client,
        admin,
        w["part"],
        [
            {"src": "P-1", "relation": w["kind"], "dst": "T-1"},
            {"src": "P-1", "relation": w["kind"], "dst": "X-1"},
        ],
    )

    # **열린다** — 그리고 약속을 말한다: 끝은 인터페이스이고, 거기 설 수 있는 열린 타입은
    # 시험장비뿐이다(생산설비는 구현했지만 안 열었다).
    entry = _entry(client, admin, w["part"])
    assert entry["relations"] == [w["kind"]]
    assert entry["relations_endpoint"].endswith(f"/core/{w['part']}/relations")
    kind = next(one for one in entry["relation_kinds"] if one["slug"] == w["kind"])
    assert kind["src"] == [{"slug": w["part"], "interface": False, "types": [w["part"]]}]
    assert kind["dst"] == [{"slug": w["iface"], "interface": True, "types": [w["tester"]]}]

    # **선마다 끝 객체의 타입을 본다** — 안 연 생산설비를 가리키는 선은 안 나간다.
    first = _relations(client, admin, w["part"])
    assert [(one["dst"], one["dst_type"]) for one in first["items"]] == [("T-1", w["tester"])]

    # 안 연 쪽의 선이 끊겨도 무덤을 안 보낸다 — 받은 적 없는 선을 끊으라고 할 이유가 없다.
    _cut = client.post(
        f"/api/objects/{w['part']}/relations/import-rows",
        json={
            "rows": [{"src": "P-1", "relation": w["kind"], "dst": "T-1"}],
            "relations_mode": "replace",
            "apply": True,
        },
        headers=admin.headers,
    )
    assert _cut.status_code == 200, _cut.text
    assert _relations(client, admin, w["part"], since=first["as_of"])["items"] == []

    # **구현 타입을 열면 판이 바뀌고** 그 타입이 도착에 선다 — 받는 쪽이 「도착에 올 수 있는
    # 것이 늘었다」 를 코드로 안다.
    before = client.get("/api/core", headers=admin.headers).json()["revision"]
    _open(client, admin, w["plant"])
    after = client.get("/api/core", headers=admin.headers).json()["revision"]
    assert before != after
    kind = next(
        one
        for one in _entry(client, admin, w["part"])["relation_kinds"]
        if one["slug"] == w["kind"]
    )
    assert kind["dst"][0]["types"] == sorted([w["tester"], w["plant"]])


def test_인터페이스를_구현한_타입이_하나도_안_열렸으면_종류도_안_열린다(
    client: TestClient, admin: Signed
) -> None:
    """나갈 선이 없는 약속은 헛말이다 — 카탈로그에 그 종류를 세우지 않는다."""
    iface = _make_interface(client, admin, label="계측")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_type(client, admin, label="게이지", key_policy="optional", interface_slugs=[iface])
    kind = _make_relation(
        client, admin, "measured", label="측정", src_type_slugs=[part], dst_type_slugs=[iface]
    )
    _open(client, admin, part)
    entry = _entry(client, admin, part)
    assert kind not in entry["relations"] and entry["relations_endpoint"] is None


def test_같은_식별자가_두_구현_타입에_있어도_도착_타입으로_갈린다(
    client: TestClient, admin: Signed
) -> None:
    """`key` 는 타입 안에서만 하나다 — 끝이 인터페이스면 「EQ-1」 이 두 구현 타입에 따로 있을
    수 있다. 선은 둘 다 나가고 `dst_type` 이 어느 것인지 말한다. 이쪽 관계 적재도 `dst_type`
    열로 도착을 좁힌다(형제 코어가 그 값을 그대로 싣는다)."""
    w = _world(client, admin)
    _open(client, admin, w["plant"])
    _make_object(client, admin, w["part"], label="볼트", key="P-1")
    _make_object(client, admin, w["tester"], label="시험기", key="EQ-1")
    _make_object(client, admin, w["plant"], label="프레스", key="EQ-1")

    # 도착 타입 없이 적으면 어느 것인지 정해지지 않는다 — 짐작으로 고르지 않는다.
    vague = client.post(
        f"/api/objects/{w['part']}/relations/import-rows",
        json={"rows": [{"src": "P-1", "relation": w["kind"], "dst": "EQ-1"}]},
        headers=admin.headers,
    )
    assert vague.status_code == 200, vague.text
    assert vague.json()["counts"].get("error") == 1, vague.json()

    _import(
        client,
        admin,
        w["part"],
        [
            {"src": "P-1", "relation": w["kind"], "dst": "EQ-1", "dst_type": w["tester"]},
            {"src": "P-1", "relation": w["kind"], "dst": "EQ-1", "dst_type": w["plant"]},
        ],
    )
    got = _relations(client, admin, w["part"])
    assert sorted((one["dst"], one["dst_type"]) for one in got["items"]) == sorted(
        [("EQ-1", w["tester"]), ("EQ-1", w["plant"])]
    )

    # 그 종류의 도착이 아닌 타입을 적으면 이유와 함께 거절한다.
    stranger = _make_type(client, admin, label=_uniq("외부"), key_policy="optional")
    _make_object(client, admin, stranger, label="남", key="EQ-1")
    refused = client.post(
        f"/api/objects/{w['part']}/relations/import-rows",
        json={
            "rows": [
                {"src": "P-1", "relation": w["kind"], "dst": "EQ-1", "dst_type": stranger}
            ]
        },
        headers=admin.headers,
    )
    assert refused.status_code == 200, refused.text
    row = next(one for one in refused.json()["rows"] if one["action"] == "error")
    assert stranger in row["message"] and "도착" in row["message"], row


def test_형제_코어_소스가_끝이_인터페이스인_선을_도착_타입대로_받고_끊는다(
    client: TestClient, admin: Signed, db: Session, sibling: FakeCore
) -> None:
    """받는 쪽(쌍둥이)도 그 선을 제대로 받아야 끝난다. 예전에는 선의 `dst_type` 을 떼고
    넣어, 같은 식별자가 두 구현 타입에 있으면 「여러 타입에 있습니다」 로 그 선이 영영
    기다렸고, 상대가 한쪽을 끊으면 무덤이 세 끝으로만 찾아 다른 타입의 선을 끊을 수 있었다."""
    iface = _make_interface(client, admin, label="설비")
    tester = _make_type(
        client, admin, label="시험장비", key_policy="optional", interface_slugs=[iface]
    )
    plant = _make_type(
        client, admin, label="생산설비", key_policy="optional", interface_slugs=[iface]
    )
    vendor = _vendor_type(client, admin)
    kind = _make_relation(
        client, admin, "uses", label="사용", src_type_slugs=[vendor], dst_type_slugs=[iface]
    )
    _make_object(client, admin, tester, label="시험기", key="EQ-1")
    _make_object(client, admin, plant, label="프레스", key="EQ-1")
    at = "2026-09-02T00:00:00.000000Z"
    sibling.edges = [
        {**_edge("V-001", kind, "EQ-1", at), "dst_type": tester},
        {**_edge("V-001", kind, "EQ-1", at), "dst_type": plant},
        {**_edge("V-002", kind, "EQ-1", at), "dst_type": plant},
    ]
    source = _source(client, admin, vendor, options={"relations": True})

    first = _sync(client, admin, source["slug"])
    assert first["run"]["status"] == "ok", first["run"]
    assert first["counts"]["relations_create"] == 3, first["counts"]
    assert _typed_edges(db, kind) == {
        ("V-001", "EQ-1", tester),
        ("V-001", "EQ-1", plant),
        ("V-002", "EQ-1", plant),
    }

    # 상대가 V-001 → 생산설비 EQ-1 만 끊었다 — 시험장비 쪽 선은 그대로다.
    sibling.edges[1] = {
        **_edge("V-001", kind, "EQ-1", "2026-09-03T00:00:00.000000Z", deleted=True),
        "dst_type": plant,
    }
    sibling.edges_as_of = "2026-09-03T12:00:00.000000Z"
    cut = _sync(client, admin, source["slug"])
    assert cut["counts"]["relations_unlink"] == 1, cut["counts"]
    assert _typed_edges(db, kind) == {("V-001", "EQ-1", tester), ("V-002", "EQ-1", plant)}


def _typed_edges(db: Session, kind: str) -> set[tuple[str, str, str]]:
    """그 관계 종류의 선 — (출발 식별자, 도착 식별자, 도착 타입)."""
    src = aliased(ObjectInstance)
    dst = aliased(ObjectInstance)
    rows = db.execute(
        select(src.key, dst.key, ObjectType.slug)
        .select_from(ObjectRelation)
        .join(src, src.id == ObjectRelation.src_object_id)
        .join(dst, dst.id == ObjectRelation.dst_object_id)
        .join(ObjectType, ObjectType.id == dst.type_id)
        .where(ObjectRelation.relation == kind)
    )
    return {(str(a), str(b), str(c)) for a, b, c in rows}
