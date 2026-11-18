"""타입의 앞머리와 보여 줄 이름(ADR 0088) — 절 번호를 이름에 넣지 않고 어디서나 보인다.

- 타입의 `label_prefix`(칸 하나 — 속성 · 기본 칸, 파일 · 문서 칸과 이름 자신은 안 된다)로
  정한 칸의 값을 이름 앞에 붙인 `display_label` 이 목록 · 상세 · 참조 링크 · 관계 목록 · 검색 ·
  검토함 · 트리에 실린다. 이름(`label`)은 그대로다.
- 옛 자리(`list_view.tree.prefix`, 0.4.95)로 보낸 앞머리는 타입의 앞머리로 옮겨 받는다.
- 다른 설치로 보내는 정의는 비어 있는 앞머리를 싣지 않는다(옛 받는 쪽이 모르는 칸을 거절한다).
- 속성을 지우면 앞머리에서 걷는다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.modules.bundles import export
from app.modules.ontology.models import ObjectType
from tests.api.conftest import Signed, bundle_import
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
    _uniq,
)


def _type_of(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    listed = client.get("/api/ontology/types", headers=admin.headers)
    assert listed.status_code == 200, listed.text
    return dict(next(one for one in listed.json() if one["slug"] == slug))


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """문서 · 절(절 번호 · 문서 참조) · 「하위 절」 관계, 앞머리 = 절 번호."""
    doc = _make_type(client, admin, label="문서", key_policy="optional")
    sec = _make_type(client, admin, label="절", key_policy="optional")
    _make_property(client, admin, sec, key="section_no", label="절 번호", data_type="text")
    _make_property(client, admin, sec, key="pic", label="그림", data_type="file")
    _make_property(
        client,
        admin,
        doc,
        key="main",
        label="대표 절",
        data_type="object_ref",
        ref_type_slug=sec,
    )
    kind = _make_relation(
        client,
        admin,
        "sub_of",
        label="하위 절",
        transitive=True,
        acyclic=True,
        src_type_slugs=[sec],
        dst_type_slugs=[sec],
    )
    got = client.patch(
        f"/api/ontology/types/{sec}",
        json={
            "label_prefix": "properties.section_no",
            "list_view": {"tree": {"relation": kind, "parent": "dst"}},
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["label_prefix"] == "properties.section_no"
    return {"doc": doc, "sec": sec, "kind": kind}


def test_앞머리는_있는_칸만_받는다(client: TestClient, admin: Signed) -> None:
    world = _world(client, admin)

    def patch(value: str) -> int:
        got = client.patch(
            f"/api/ontology/types/{world['sec']}",
            json={"label_prefix": value},
            headers=admin.headers,
        )
        return int(got.status_code)

    assert patch("properties.nope") == 422
    assert patch("properties.pic") == 422  # 파일 칸은 값이 따로다
    assert patch("label") == 422  # 「측정 방법 측정 방법」 이 된다
    assert patch("key") == 200
    assert patch("") == 200
    assert patch("properties.section_no") == 200


def test_목록_상세_트리_검색이_보여_줄_이름을_싣고_이름은_그대로다(
    client: TestClient, admin: Signed
) -> None:
    world = _world(client, admin)
    sec, kind = world["sec"], world["kind"]
    word = _uniq("측정")
    parent = _make_object(client, admin, sec, label="방법", properties={"section_no": "3"})
    child = _make_object(
        client, admin, sec, label=f"{word} 방법", properties={"section_no": "3.2"}
    )
    plain = _make_object(client, admin, sec, label=f"{word} 메모")  # 번호 없음
    assert _link(client, admin, sec, child["id"], kind, parent["id"]).status_code == 201

    # 만든 응답부터 — 수정 창이 받는 것도 같은 줄이다.
    assert child["display_label"] == f"3.2 {word} 방법"
    assert child["label"] == f"{word} 방법"

    listed = client.get(f"/api/objects/{sec}", headers=admin.headers).json()["items"]
    shown = {one["id"]: one["display_label"] for one in listed}
    assert shown[child["id"]] == f"3.2 {word} 방법"
    assert shown[plain["id"]] == f"{word} 메모"
    assert {one["id"]: one["label"] for one in listed}[child["id"]] == f"{word} 방법"

    detail = client.get(f"/api/objects/{sec}/{child['id']}", headers=admin.headers).json()
    assert detail["object"]["display_label"] == f"3.2 {word} 방법"
    # 관계 목록 — 상대(부모 절)도 앞머리를 붙인다.
    related = {one["object_id"]: one for one in detail["related"]}
    assert related[parent["id"]]["object_display_label"] == "3 방법"
    assert related[parent["id"]]["object_label"] == "방법"

    tree = client.get(
        f"/api/objects/{sec}/tree", params={"parent": parent["id"]}, headers=admin.headers
    ).json()["nodes"]
    assert [(one["prefix"], one["display_label"]) for one in tree] == [
        ("3.2", f"3.2 {word} 방법")
    ]

    # 검색 — 찾기는 이름으로, 보이는 줄은 앞머리를 붙인다.
    found = client.get("/api/search", params={"q": word}, headers=admin.headers).json()
    hits = {one["id"]: one for one in found["items"]}
    assert hits[child["id"]]["display_label"] == f"3.2 {word} 방법"
    assert hits[child["id"]]["label"] == f"{word} 방법"
    assert hits[plain["id"]]["display_label"] == f"{word} 메모"


def test_참조_링크와_검토함도_보여_줄_이름이다(client: TestClient, admin: Signed) -> None:
    world = _world(client, admin)
    sec, doc = world["sec"], world["doc"]
    first = _make_object(client, admin, sec, label="개요", properties={"section_no": "1.1"})
    second = _make_object(client, admin, sec, label="개요", properties={"section_no": "2.1"})
    paper = _make_object(
        client, admin, doc, label="시험 절차서", properties={"main": first["id"]}
    )
    listed = client.get(f"/api/objects/{doc}", headers=admin.headers).json()["items"]
    assert {one["id"]: one["ref_labels"] for one in listed}[paper["id"]] == {
        first["id"]: "1.1 개요"
    }

    # 병합 후보 — 이름이 같아 모인 두 절을 앞머리가 가른다.
    page = client.get(
        "/api/review/items", params={"kind": "duplicate", "type": sec}, headers=admin.headers
    ).json()
    (item,) = page["items"]
    assert {one["value"]: one["label"] for one in item["choices"]} == {
        first["id"]: "1.1 개요",
        second["id"]: "2.1 개요",
    }
    assert item["question"].startswith("「1.1 개요」") or item["question"].startswith(
        "「2.1 개요」"
    )


def test_옛_자리의_앞머리는_옮겨_받고_보내는_정의는_빈_앞머리를_싣지_않는다(
    client: TestClient, admin: Signed
) -> None:
    sec = _make_type(client, admin, label="절", key_policy="optional")
    _make_property(client, admin, sec, key="section_no", label="절 번호", data_type="text")
    kind = _make_relation(
        client,
        admin,
        "sub_of",
        src_type_slugs=[sec],
        dst_type_slugs=[sec],
        transitive=True,
        acyclic=True,
    )
    # 0.4.95 가 내보낸 모양 — 트리 안의 앞머리.
    got = bundle_import(
        client,
        admin,
        {
            "ontology": {
                "types": [
                    {
                        "slug": sec,
                        "list_view": {
                            "tree": {
                                "relation": kind,
                                "parent": "dst",
                                "prefix": "properties.section_no",
                            }
                        },
                    }
                ]
            },
            "apply": True,
        },
    )
    assert got["applied"] is True, got
    stored = _type_of(client, admin, sec)
    assert stored["label_prefix"] == "properties.section_no"
    assert "prefix" not in stored["list_view"]["tree"]
    made = _make_object(client, admin, sec, label="범위", properties={"section_no": "1"})
    assert made["display_label"] == "1 범위"

    # 보내는 정의 — 쓰는 타입은 싣고, 비어 있는 타입은 그 칸을 뺀다.
    blank = _make_type(client, admin, label="빈 앞머리")
    with SessionLocal() as db:
        types = [one for one in db.query(ObjectType).filter(ObjectType.slug.in_([sec, blank]))]
        shared = export.definitions_of(db, types, set()).ontology["types"]
    by_slug = {one["slug"]: one for one in shared}
    assert by_slug[sec]["label_prefix"] == "properties.section_no"
    assert "label_prefix" not in by_slug[blank]

    # 속성을 지우면 앞머리에서 걷는다.
    removed = client.delete(
        f"/api/ontology/types/{sec}/properties/section_no", headers=admin.headers
    )
    assert removed.status_code in (200, 204), removed.text
    stored = _type_of(client, admin, sec)
    assert stored["label_prefix"] == ""
