"""코어 온톨로지 초안이 **그대로 들어가고, 규약과 같은 말을 하나.**

AI 는 모델링 규약(`get_guide(topic="modeling")`)을 읽고 코어 초안
(`pipeline/core/core-ontology.json`)부터 쓴다. 둘이 어긋나거나 초안이 플랫폼에 안 들어가면,
그 사실은 **파일럿 그룹의 첫 실행**에서야 드러난다 — 그때는 이미 원천을 정제해 놓은 뒤다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "pipeline" / "core" / "core-ontology.json"
GUIDE = REPO / "mcp_server" / "guide" / "GUIDE.md"


def _core() -> dict[str, Any]:
    return dict(json.loads(CORE.read_text(encoding="utf-8")))


def test_코어_온톨로지_초안이_오류_없이_들어간다(client: TestClient, admin: Signed) -> None:
    """미리 보기로만 본다 — 시험 DB 에 코어 slug 를 남기지 않는다."""
    got = client.post("/api/bundles/import", json={"ontology": _core()}, headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    problems = {"ontology": (body["ontology"] or {}).get("errors"), "bundle": body["errors"]}
    assert body["ok"] is True, problems
    # 코어의 뼈대 — 문서에서 뽑은 사실을 문서와 잇는 관계가 들어간다.
    assert any(change["slug"] == "evidence" for change in body["ontology"]["changes"])


def test_규약이_말하는_코어와_초안이_같다() -> None:
    """규약 문서에 없는 타입이 초안에 있거나 그 반대면, AI 는 둘 중 하나를 틀리게 따른다."""
    core = _core()
    modeling = GUIDE.read_text(encoding="utf-8").split("<!--@ modeling -->", 1)[1]
    slugs = [one["slug"] for one in core["types"]] + [
        one["slug"] for one in core["relation_types"]
    ]
    missing = [slug for slug in slugs if f"`{slug}`" not in modeling]
    assert not missing, f"규약(GUIDE.md modeling)에 없는 코어 slug: {missing}"


def test_코어의_참조와_관계는_코어_안을_가리킨다() -> None:
    """코어가 코어 밖(그룹 전용 타입)을 가리키면 복사본마다 따로 깨진다."""
    core = _core()
    types = {one["slug"] for one in core["types"]}
    for one in core["types"]:
        for prop in one.get("properties", []):
            if prop.get("data_type") == "object_ref":
                assert prop["ref_type_slug"] in types, (one["slug"], prop["key"])
    for relation in core["relation_types"]:
        for side in ("src_type_slugs", "dst_type_slugs"):
            assert set(relation.get(side) or []) <= types, (relation["slug"], side)
