"""트리의 차례와 앞머리(`list_view.tree.sort` · `prefix`) — 문서의 절 번호를 이름에 넣지 않고
보인다.

- 정의: `sort` 는 칸 이름의 목록, `prefix` 는 칸 하나 — 그 타입에 있는 속성 · 기본 칸만(목록의
  정렬과 같은 검사), 파일 · 문서 칸은 안 된다. 정의 가져오기가 그대로 저장하고 스키마 조회가
  돌려준다. 속성을 지우면 걷어낸다.
- 차례: 숫자로 저장된 값은 숫자로, 번호 모양의 글자(「2.10」)는 마디마다 숫자로, 아니면 글자로
  — 그다음 이름 · id. 참조 칸은 상대의 이름으로. `sort` 가 없으면 이름순. 뿌리와 하위가 같은
  차례다.
- 앞머리: 노드마다 그 칸의 값(없으면 null). 정하지 않은 타입은 null.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, bundle_import
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _section_world(client: TestClient, admin: Signed, tree: dict[str, Any]) -> dict[str, Any]:
    """문서 · 절 — 절은 「하위 절」 관계로 트리, 절 번호(글자) · 차례(숫자) · 문서(참조) 칸."""
    doc = _make_type(client, admin, label="문서", key_policy="optional")
    sec = _make_type(client, admin, label="절", key_policy="optional")
    _make_property(client, admin, sec, key="section_no", label="절 번호", data_type="text")
    _make_property(client, admin, sec, key="seq", label="차례", data_type="number")
    _make_property(
        client, admin, sec, key="doc", label="문서", data_type="object_ref", ref_type_slug=doc
    )
    _make_property(client, admin, sec, key="pic", label="그림", data_type="file")
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
        json={"list_view": {"tree": {"relation": kind, "parent": "dst", **tree}}},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    docs = {
        name: _make_object(client, admin, doc, label=name)["id"]
        for name in ("나 문서", "가 문서")
    }
    return {"doc": doc, "sec": sec, "kind": kind, "docs": docs}


def _children(
    client: TestClient, admin: Signed, world: dict[str, Any], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """부모 하나(「방법」) 아래에 `rows` 를 달고 그 아래 한 단계를 읽는다."""
    sec, kind = world["sec"], world["kind"]
    parent = _make_object(client, admin, sec, label="방법", properties={"section_no": "2"})
    for one in rows:
        made = _make_object(client, admin, sec, **one)
        assert _link(client, admin, sec, made["id"], kind, parent["id"]).status_code == 201
    got = client.get(
        f"/api/objects/{sec}/tree", params={"parent": parent["id"]}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    return list(got.json()["nodes"])


def test_절_번호로_차례를_세우고_이름_앞에_붙인다(client: TestClient, admin: Signed) -> None:
    world = _section_world(
        client, admin, {"sort": ["properties.section_no"], "prefix": "properties.section_no"}
    )
    nodes = _children(
        client,
        admin,
        world,
        [
            {"label": "끝", "properties": {"section_no": "2.10"}},
            {"label": "장비", "properties": {"section_no": "2.1"}},
            {"label": "번호 없음"},
            {"label": "부록", "properties": {"section_no": "부록 A"}},
            {"label": "절차", "properties": {"section_no": "2.2."}},
            {"label": "정리", "properties": {"section_no": "2.9"}},
        ],
    )
    # 글자로만 세우면 2.10 이 2.2 앞에 선다 — 번호 모양은 마디마다 숫자로. 번호가 아닌 글자는
    # 그 뒤, 값이 없는 것은 맨 뒤.
    assert [one["label"] for one in nodes] == [
        "장비",
        "절차",
        "정리",
        "끝",
        "부록",
        "번호 없음",
    ]
    assert [one["prefix"] for one in nodes] == ["2.1", "2.2.", "2.9", "2.10", "부록 A", None]


def test_숫자_칸은_숫자로_참조_칸은_상대의_이름으로_같으면_이름으로(
    client: TestClient, admin: Signed
) -> None:
    world = _section_world(client, admin, {"sort": ["properties.seq"]})
    nodes = _children(
        client,
        admin,
        world,
        [
            {"label": "다", "properties": {"seq": 10}},
            {"label": "나", "properties": {"seq": 2}},
            {"label": "가", "properties": {"seq": 2}},
        ],
    )
    assert [one["label"] for one in nodes] == ["가", "나", "다"]  # 2 · 2(이름) · 10
    assert all(one["prefix"] is None for one in nodes)  # 앞머리를 안 정한 타입

    by_doc = _section_world(
        client, admin, {"sort": ["properties.doc"], "prefix": "properties.doc"}
    )
    docs = by_doc["docs"]
    nodes = _children(
        client,
        admin,
        by_doc,
        [
            {"label": "B 절", "properties": {"doc": docs["나 문서"]}},
            {"label": "A 절", "properties": {"doc": docs["가 문서"]}},
        ],
    )
    assert [one["label"] for one in nodes] == ["A 절", "B 절"]  # 상대의 이름 차례
    assert [one["prefix"] for one in nodes] == ["가 문서", "나 문서"]


def test_정하지_않으면_이름순이고_뿌리도_같은_차례다(
    client: TestClient, admin: Signed
) -> None:
    plain = _section_world(client, admin, {})
    nodes = _children(
        client,
        admin,
        plain,
        [
            {"label": "다", "properties": {"section_no": "1"}},
            {"label": "가", "properties": {"section_no": "3"}},
        ],
    )
    assert [one["label"] for one in nodes] == ["가", "다"]
    assert all(one["prefix"] is None for one in nodes)

    world = _section_world(
        client, admin, {"sort": ["properties.section_no"], "prefix": "properties.section_no"}
    )
    sec, kind = world["sec"], world["kind"]
    for label, number in (("결과", "10"), ("개요", "1"), ("방법", "2")):
        root = _make_object(client, admin, sec, label=label, properties={"section_no": number})
        child = _make_object(client, admin, sec, label=f"{label}의 첫 절")
        assert _link(client, admin, sec, child["id"], kind, root["id"]).status_code == 201
    roots = client.get(f"/api/objects/{sec}/tree", headers=admin.headers).json()["nodes"]
    assert [one["label"] for one in roots] == ["개요", "방법", "결과"]
    assert [one["prefix"] for one in roots] == ["1", "2", "10"]


def test_정렬_앞머리는_있는_칸만_받고_가져오기_스키마가_그대로_싣고_칸을_지우면_걷는다(
    client: TestClient, admin: Signed
) -> None:
    world = _section_world(client, admin, {})
    sec, kind = world["sec"], world["kind"]

    def patch(tree: dict[str, Any]) -> Any:
        return client.patch(
            f"/api/ontology/types/{sec}",
            json={"list_view": {"tree": {"relation": kind, "parent": "dst", **tree}}},
            headers=admin.headers,
        )

    assert patch({"sort": ["properties.nope"]}).status_code == 422
    assert patch({"sort": ["nope"]}).status_code == 422
    assert patch({"sort": "properties.seq"}).status_code == 422  # 목록이어야 한다
    assert patch({"sort": ["properties.seq", "properties.seq"]}).status_code == 422
    assert patch({"prefix": "properties.nope"}).status_code == 422
    assert patch({"prefix": "properties.pic"}).status_code == 422  # 파일 칸은 값이 따로다
    assert patch({"sort": ["properties.seq", "label"], "prefix": "key"}).status_code == 200

    # 정의 가져오기가 그대로 저장하고, 스키마 조회가 돌려준다.
    spec = {
        "relation": kind,
        "parent": "dst",
        "sort": ["properties.section_no", "properties.seq"],
        "prefix": "properties.section_no",
    }
    got = bundle_import(
        client,
        admin,
        {"ontology": {"types": [{"slug": sec, "list_view": {"tree": spec}}]}, "apply": True},
    )
    assert got["applied"] is True, got
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    stored = next(one for one in schema["types"] if one["slug"] == sec)
    assert stored["list_view"]["tree"] == spec

    # 칸을 지우면 차례 · 앞머리에서 걷는다 — 안 걷으면 다음 타입 수정이 「없는 속성」 으로
    # 거절된다.
    removed = client.delete(
        f"/api/ontology/types/{sec}/properties/section_no", headers=admin.headers
    )
    assert removed.status_code in (200, 204), removed.text
    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    stored = next(one for one in schema["types"] if one["slug"] == sec)
    assert stored["list_view"]["tree"] == {
        "relation": kind,
        "parent": "dst",
        "sort": ["properties.seq"],
    }
    assert patch({"sort": ["properties.seq"]}).status_code == 200
