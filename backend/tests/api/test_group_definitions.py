"""그룹 전용 정의(`pipeline/groups/*.json`)가 **그대로 들어가나.**

공통 코어(`pipeline/core/`)와 다른 자리다: 코어는 어느 설치에나 있어야 하는 것이고, 이쪽은 한
그룹의 일을 담는 정의다. 둘 다 파일이 정본이라, 틀린 채로 올라가면 그 사실은 **그 그룹의 첫
적재**에서야 드러난다 — 그때는 이미 원천을 정제해 놓은 뒤다.

폴더를 통째로 돈다 — 그룹 정의가 하나 더 생겨도 이 시험을 고칠 일이 없다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed

REPO = Path(__file__).resolve().parents[3]
GROUPS = sorted((REPO / "pipeline" / "groups").glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", GROUPS, ids=[one.stem for one in GROUPS])
def test_그룹_정의가_오류_없이_들어간다(client: TestClient, admin: Signed, path: Path) -> None:
    """**미리 보기로 본다** — 시험 DB 에 그룹 slug 를 남기지 않는다."""
    plan = client.post(
        "/api/ontology/import",
        params={"dry_run": "true"},
        json=_load(path),
        headers=admin.headers,
    )
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["errors"] == [], body["errors"]
    assert body["changes"], "바뀌는 것이 없다 — 파일이 비었나"


@pytest.mark.parametrize("path", GROUPS, ids=[one.stem for one in GROUPS])
def test_그룹_정의는_정말_적용된다(client: TestClient, admin: Signed, path: Path) -> None:
    """**미리 보기는 화면 모양(뷰)을 안 본다** — 목록 열 · 폼 구획이 틀렸는지는 적용에서
    드러난다. 넣어 보고 되돌린다(시험 DB 에 남기지 않는다).
    """
    doc = _load(path)
    applied = client.post(
        "/api/ontology/import", params={"dry_run": "false"}, json=doc, headers=admin.headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["errors"] == [], applied.json()["errors"]
    try:
        schema = client.get("/api/ontology/schema", headers=admin.headers).json()
        slugs = {one["slug"] for one in schema["types"]}
        for one in doc.get("types") or []:
            assert one["slug"] in slugs, one["slug"]
    finally:
        # 넣은 것을 되돌린다 — 다음 시험이 남의 타입을 세지 않게.
        for one in reversed(doc.get("types") or []):
            client.delete(f"/api/ontology/types/{one['slug']}", headers=admin.headers)
        for one in reversed(doc.get("groups") or []):
            client.delete(f"/api/ontology/groups/{one['slug']}", headers=admin.headers)


def test_대응_파일이_정의의_칸을_다_덮는다() -> None:
    """원천 표(엑셀)를 옮기는 **대응 파일**과 정의가 갈리면, 적재는 조용히 몇 칸을 빼먹는다 —
    빠진 칸은 「원천에 없었다」 와 구별되지 않는다. 둘을 여기서 묶어 둔다.
    """
    doc = _load(REPO / "pipeline" / "groups" / "qings.json")
    table = _load(REPO / "pipeline" / "templates" / "qings-service-case.table.json")
    one = next(row for row in doc["types"] if row["slug"] == "qings_service_case")
    mapped = next(row for row in table["types"] if row["type_slug"] == "qings_service_case")

    defined = {prop["key"] for prop in one["properties"]}
    assert set(mapped["fields"]) == defined, set(mapped["fields"]) ^ defined
    # 날짜 칸은 대응에서도 날짜로 읽어야 한다 — 글자로 넣으면 그 칸으로 거를 수가 없다.
    for prop in one["properties"]:
        if prop["data_type"] == "date":
            assert mapped["fields"][prop["key"]].get("date") is True, prop["key"]
    # 식별자는 접수번호 열에서 온다.
    assert mapped["key"] == {"column": "접수번호"}


def test_시장_서비스_데이터는_접수번호를_식별자로_쓴다() -> None:
    """**접수번호는 속성이 아니라 객체의 식별자(key)다.** 둘 다 두면 같은 값이 두 칸에 들고,
    그때부터 어느 쪽이 맞는지 말할 수 없다(한쪽만 고쳐진다).
    """
    doc = _load(REPO / "pipeline" / "groups" / "qings.json")
    one = next(row for row in doc["types"] if row["slug"] == "qings_service_case")
    assert one["key_policy"] == "required"
    labels = {prop["label"] for prop in one["properties"]}
    assert "접수번호" not in labels
    # 목록의 첫 칸이 식별자다 — 서비스 건을 사람이 그 번호로 부른다.
    assert one["list_view"]["columns"][0] == "key"
    # 폼의 구획과 속성의 구획이 **같은 이름**이어야 한다 — 다르면 그 칸이 폼에서 사라진다.
    sections = {section["name"] for section in one["form_view"]["sections"]}
    used = {prop["section"] for prop in one["properties"]}
    assert used <= sections, used - sections
