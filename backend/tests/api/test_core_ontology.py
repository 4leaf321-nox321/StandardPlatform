"""공통 코어가 **그대로 들어가고, 규약과 같은 말을 하나.**

코어는 둘이다 — PLM 기준정보(`pipeline/core/plm-core.json`, 허브가 내려준다)와 업무 공통
(`pipeline/core/work-core.json`, 그룹의 일이 PLM 과제에 붙는 자리). AI 는 모델링 규약
(`get_guide(topic="modeling")`)을 읽고 코어부터 쓴다. 둘이 어긋나거나 코어가 플랫폼에 안
들어가면, 그 사실은 **파일럿 그룹의 첫 실행**에서야 드러난다 — 그때는 이미 원천을 정제해
놓은 뒤다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, bundle_import

REPO = Path(__file__).resolve().parents[3]
PLM = REPO / "pipeline" / "core" / "plm-core.json"
WORK = REPO / "pipeline" / "core" / "work-core.json"
GUIDE = REPO / "mcp_server" / "guide" / "GUIDE.md"


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _both() -> dict[str, Any]:
    """허브에서 받은 PLM 코어 위에 업무 공통을 얹은 모양 — 쌍둥이가 갖게 되는 것."""
    plm, work = _load(PLM), _load(WORK)
    return {
        key: plm.get(key, []) + work.get(key, [])
        for key in ("groups", "types", "relation_types")
    }


def test_코어가_오류_없이_들어간다(client: TestClient, admin: Signed) -> None:
    """미리 보기로만 본다 — 시험 DB 에 코어 slug 를 남기지 않는다."""
    body = bundle_import(client, admin, {"ontology": _both()})
    problems = {"ontology": (body["ontology"] or {}).get("errors"), "bundle": body["errors"]}
    assert body["ok"] is True, problems
    changed = {change["slug"] for change in body["ontology"]["changes"]}
    # 뼈대 — PLM 모델, 그리고 문서에서 뽑은 사실을 문서와 잇는 관계.
    assert {"plm_model", "plm_task", "evidence", "participates"} <= changed


def test_PLM_코어만으로도_들어간다(client: TestClient, admin: Signed) -> None:
    """허브는 업무 공통 없이 PLM 코어만 갖는다."""
    got = bundle_import(client, admin, {"ontology": _load(PLM)})
    assert got["ok"] is True, got


def test_규약이_말하는_코어와_파일이_같다() -> None:
    """규약 문서에 없는 타입이 코어에 있거나 그 반대면, AI 는 둘 중 하나를 틀리게 따른다."""
    core = _both()
    modeling = GUIDE.read_text(encoding="utf-8").split("<!--@ modeling -->", 1)[1]
    slugs = [one["slug"] for one in core["types"]] + [
        one["slug"] for one in core["relation_types"]
    ]
    missing = [slug for slug in slugs if f"`{slug}`" not in modeling]
    assert not missing, f"규약(GUIDE.md modeling)에 없는 코어 slug: {missing}"
    assert "plm-core.json" in modeling and "work-core.json" in modeling


def test_코어의_참조와_관계는_코어_안을_가리키고_PLM_코어는_밖을_모른다() -> None:
    """코어가 코어 밖(그룹 전용 타입)을 가리키면 복사본마다 따로 깨진다. 허브는 업무 공통을
    갖지 않으므로 **PLM 코어는 업무 공통을 가리키지 않는다.**"""
    for core, label in ((_both(), "코어 전체"), (_load(PLM), "PLM 코어")):
        types = {one["slug"] for one in core["types"]}
        for one in core["types"]:
            for prop in one.get("properties", []):
                if prop.get("data_type") == "object_ref":
                    assert prop["ref_type_slug"] in types, (label, one["slug"], prop["key"])
        for relation in core["relation_types"]:
            for side in ("src_type_slugs", "dst_type_slugs"):
                assert set(relation.get(side) or []) <= types, (label, relation["slug"], side)


def test_PLM_타입은_PLM_코어에만_있다() -> None:
    """허브 소유 타입이 업무 공통에 섞이면 쌍둥이가 그것을 자기 것으로 알고 고친다."""
    plm, work = _load(PLM), _load(WORK)
    assert all(one["slug"].startswith("plm_") for one in plm["types"] + plm["relation_types"])
    assert not any(
        one["slug"].startswith("plm_") for one in work["types"] + work["relation_types"]
    )


def test_부서_밖의_코어_타입은_식별자를_필수로_받는다() -> None:
    """규약은 코어 타입마다 식별자(원천 번호 → 정규화한 이름)를 정한다. 그런데 업무 공통의
    업무 · 기관 · 사람 · 설비 · 소프트웨어 · 기술 분야에 `key_policy` 가 빠져 기본(`none` —
    식별자를 거절)이 됐고, 식별자를 넣으면 「식별자를 쓰지 않는 타입입니다」(OBJECTS-1)로
    막혔다(2026-11-24)."""
    for core in (_load(PLM), _load(WORK)):
        for one in core["types"]:
            if one.get("kind_class") == "system":
                continue  # 부서 — 원 표(조직도)를 비춘다.
            assert one.get("key_policy") == "required", one["slug"]


def test_코어_타입에_식별자를_넣어_미리_본다(client: TestClient, admin: Signed) -> None:
    """정의와 객체를 한 묶음으로 미리 본다(시험 DB 에 코어 slug 를 남기지 않는다) — 업무 공통의
    타입마다 규약이 말하는 식별자를 넣는다."""
    rows = {
        "work": [{"key": "설계 검토", "label": "설계 검토"}],
        "document": [{"key": "doc-0123456789ab", "label": "해석 보고서"}],
        "person": [{"key": "A12345", "label": "홍길동"}],
        "organization": [{"key": "acme", "label": "ACME Inc."}],
        "equipment": [{"key": "EQ-0001", "label": "진동 시험기"}],
        "software": [{"key": "ansys", "label": "ANSYS"}],
        "topic": [{"key": "구조 해석", "label": "구조 해석"}],
    }
    body = bundle_import(
        client,
        admin,
        {
            "ontology": _both(),
            "objects": [
                {"type_slug": slug, "workspace_slug": admin.workspace, "rows": items}
                for slug, items in rows.items()
            ],
        },
    )
    failed = {
        one["type_slug"]: one["error"]
        or [row for row in one["plan"]["rows"] if row["action"] == "error"]
        for one in body["objects"]
        if one["error"] or one["plan"]["counts"].get("error")
    }
    assert body["ok"] is True, failed or body["errors"]
    assert {one["type_slug"] for one in body["objects"]} == set(rows)
