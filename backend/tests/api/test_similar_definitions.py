"""비슷한 정의(ADR 0091) — 정의 가져오기 · 묶음 가져오기의 미리 보기가 새 정의를 이미 있는 것과
견준다.

- 이름이 같은 타입 · 관계 종류는 뜻 계산 없이도 말한다.
- 뜻이 가까운 것은 서버 안의 모델로(시험은 가짜 Ollama) — 같은 파일이 함께 만드는 것끼리도.
- 같은 키 · 같은 이름인데 종류 · 단위가 다른 속성.
- 못 계산하면 이름으로만 비교했다고 말한다. 적용(`dry_run=false`)에서는 세지 않는다.
"""

from __future__ import annotations

import json
import math
import uuid
import zlib
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.modules.ontology import similar
from app.shared import embedding, vectors
from tests.api.conftest import Signed, bundle_import
from tests.api.test_ontology import _make_property, _make_type

#: 가짜 모델의 뜻 — 같은 묶음의 낱말이 든 글끼리 가깝다(약 0.92), 아니면 멀다(0 근처).
CONCEPTS = (("냉각탑", "쿨링타워", "cooling"), ("송풍기", "블로워", "blower"))


def _vector(text: str) -> list[float]:
    at = [0.0] * vectors.DIM
    for dim, words in enumerate(CONCEPTS):
        if any(word in text for word in words):
            at[dim] = 1.0
    at[len(CONCEPTS) + zlib.crc32(text.encode()) % (vectors.DIM - len(CONCEPTS))] = 0.3
    size = math.sqrt(sum(one * one for one in at))
    return [one / size for one in at]


@pytest.fixture
def ollama() -> Iterator[list[list[str]]]:
    asked: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        texts = list(json.loads(request.content or b"{}").get("input") or [])
        asked.append(texts)
        return httpx.Response(200, json={"embeddings": [_vector(one) for one in texts]})

    similar.forget()
    embedding.transport = httpx.MockTransport(handle)
    yield asked
    embedding.transport = None
    similar.forget()


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    similar.forget()
    yield
    similar.forget()


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _preview(client: TestClient, admin: Signed, schema: dict[str, Any]) -> dict[str, Any]:
    got = client.post("/api/ontology/import", json=schema, headers=admin.headers)
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    assert body["applied"] is False and not body["errors"], body
    return body


def _near(body: dict[str, Any], slug: str) -> list[dict[str, Any]]:
    found = [one for one in body["similar"] if one["slug"] == slug]
    assert len(found) == 1, body["similar"]
    return list(found[0]["near"])


def test_이름이_같은_타입과_관계_종류는_뜻_계산_없이도_말한다(
    client: TestClient, admin: Signed
) -> None:
    label = f"냉각수 펌프 {_hex()}"
    had = _make_type(client, admin, label=label)
    relation = f"순환 {_hex()}"
    made = client.post(
        "/api/ontology/relation-types",
        json={"slug": f"rel_{_hex()}", "label": relation},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    new_type, new_relation = f"pump_{_hex()}", f"circ_{_hex()}"
    body = _preview(
        client,
        admin,
        {
            # 띄어쓰기만 다른 이름도 같은 이름이다.
            "types": [{"slug": new_type, "label": label.replace(" ", "")}],
            "relation_types": [{"slug": new_relation, "label": relation}],
        },
    )
    # 시험은 진짜 Ollama 에 닿지 않는다 — 이름으로만 견주고 그렇다고 말한다.
    assert body["similar_meaning"] == "unavailable"
    assert any("이름으로만 비교했습니다" in one for one in body["warnings"])
    [near] = _near(body, new_type)
    assert near["slug"] == had and near["why"] == "이름이 같음" and near["score"] is None
    assert _near(body, new_relation)[0]["why"] == "이름이 같음"
    line = next(one for one in body["warnings"] if new_type in one)
    assert line.startswith("비슷한 정의 — 새 타입") and had in line
    # 미리 보기는 아무것도 안 만든다 — 적용에서는 세지 않는다(늦다).
    applied = client.post(
        "/api/ontology/import",
        params={"dry_run": "false"},
        json={"types": [{"slug": new_type, "label": label}]},
        headers=admin.headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True and applied.json()["similar"] == []


def test_뜻이_가까운_타입은_같은_파일의_새_것끼리도_말한다(
    client: TestClient, admin: Signed, ollama: list[list[str]]
) -> None:
    had = _make_type(client, admin, label=f"쿨링타워 {_hex()}")
    first, second = f"tower_{_hex()}", f"tower_{_hex()}"
    body = _preview(
        client,
        admin,
        {
            "types": [
                {"slug": first, "label": f"냉각탑 {_hex()}"},
                {"slug": second, "label": f"냉각탑 설비 {_hex()}"},
            ]
        },
    )
    assert body["similar_meaning"] == "ready"
    near = _near(body, first)
    assert near[0]["slug"] == had and near[0]["why"] == "뜻이 가까움"
    assert near[0]["score"] >= similar.TYPE_MIN
    # 뒤의 것은 앞의 새 것도 가깝다고 말한다(한 번만 — 앞의 것에는 안 적는다).
    later = {one["slug"]: one for one in _near(body, second)}
    assert later[first]["pending"] is True and had in later
    assert all(one["slug"] != second for one in near)
    assert any("뜻이 가까움" in one and first in one for one in body["warnings"])
    # 뜻이 먼 것(송풍기)은 말하지 않는다.
    other = f"fan_{_hex()}"
    far = _preview(client, admin, {"types": [{"slug": other, "label": f"송풍기 {_hex()}"}]})
    assert all(one["slug"] != other for one in far["similar"])

    # 한 번 계산한 있는 정의는 다시 묻지 않는다(프로세스 안에 둔다).
    asked = sum(len(one) for one in ollama)
    _preview(client, admin, {"types": [{"slug": f"x_{_hex()}", "label": "블로워"}]})
    assert sum(len(one) for one in ollama) - asked == 1


def test_같은_칸인데_종류_단위가_다른_새_속성을_말한다(
    client: TestClient, admin: Signed
) -> None:
    key = f"weight_{_hex()}"
    part = _make_type(client, admin, label=f"부품 {_hex()}")
    _make_property(client, admin, part, key=key, label="무게", data_type="number", unit="kg")
    same_shape = _make_type(client, admin, label=f"반제품 {_hex()}")
    _make_property(
        client, admin, same_shape, key=key, label="무게", data_type="number", unit="kg"
    )
    # 이름만 같은 칸(키가 다르다) — 같은 키인 것이 먼저 실린다(둘까지).
    named = _make_type(client, admin, label=f"가공품 {_hex()}")
    _make_property(
        client, admin, named, key=f"w_{_hex()}", label="무게", data_type="number", unit="kg"
    )
    new_type, memo = f"cover_{_hex()}", f"memo_{_hex()}"
    body = _preview(
        client,
        admin,
        {
            "types": [
                {
                    "slug": new_type,
                    "label": f"커버 {_hex()}",
                    "properties": [
                        {"key": key, "label": "무게", "data_type": "text"},
                        # 모양이 같으면 말하지 않는다(그것은 정의 점검의 인터페이스 후보다).
                        {"key": memo, "label": f"메모 {_hex()}", "data_type": "text"},
                    ],
                }
            ]
        },
    )
    props = [one for one in body["similar"] if one["kind"] == "property"]
    assert f"{new_type}.{memo}" not in {one["slug"] for one in props}
    [one] = [p for p in props if p["slug"] == f"{new_type}.{key}"]
    assert one["shape"] == "글 (한 줄)"
    assert {near["slug"] for near in one["near"]} == {f"{part}.{key}", f"{same_shape}.{key}"}
    assert all(near["why"] == "숫자 · kg" for near in one["near"])
    assert any(line.startswith("같은 칸 다른 모양") for line in body["warnings"])

    # 단위만 달라도 말한다 — 숫자끼리.
    grams = _preview(
        client,
        admin,
        {
            "types": [
                {
                    "slug": f"lid_{_hex()}",
                    "label": f"덮개 {_hex()}",
                    "properties": [
                        {"key": key, "label": "무게", "data_type": "number", "unit": "g"}
                    ],
                }
            ]
        },
    )
    assert any(one["kind"] == "property" for one in grams["similar"])


def test_묶음_미리_보기도_비슷한_정의를_경고에_싣는다(
    client: TestClient, admin: Signed
) -> None:
    label = f"집진기 {_hex()}"
    had = _make_type(client, admin, label=label)
    new_type = f"dust_{_hex()}"
    planned = bundle_import(
        client,
        admin,
        {"ontology": {"types": [{"slug": new_type, "label": label}]}, "apply": False},
    )
    warnings = planned["ontology"]["warnings"]
    assert any(new_type in one and had in one and "이름이 같음" in one for one in warnings)
    # 미리 보기라 아무것도 안 남는다.
    types = client.get("/api/ontology/types", headers=admin.headers).json()
    assert new_type not in {one["slug"] for one in types}
