"""표에서 타입 추론 — **CSV 하나로 타입과 데이터가 함께 생긴다.** 애매하면 글자다."""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from tests.api.conftest import Signed

CSV = "﻿" + "\n".join(
    [
        "코드,이름,무게(kg),출시일,단종,종류,홈페이지,태그,비고",
        *(
            f"P-{i:03d},부품 {i},{i * 0.5},2024-01-{(i % 28) + 1:02d},"
            f"{'예' if i % 3 == 0 else '아니오'},{['볼트', '너트', '와셔'][i % 3]},"
            f"https://example.com/p{i},a;b,{'긴 설명 ' * (60 if i == 1 else 1)}"
            for i in range(1, 31)
        ),
    ]
)


def _upload(client: TestClient, admin: Signed, text: str, name: str = "parts.csv") -> dict:
    return client.post(
        "/api/ontology/infer",
        files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/csv")},
        headers=admin.headers,
    ).json()


def test_열마다_역할과_종류를_제안한다(client: TestClient, admin: Signed) -> None:
    got = _upload(client, admin, CSV)
    assert got["rows"] == 30
    by_header = {one["header"]: one for one in got["columns"]}
    assert by_header["코드"]["role"] == "key"
    assert by_header["이름"]["role"] == "label"
    assert by_header["비고"]["role"] == "description"
    assert (
        by_header["무게(kg)"]["data_type"] == "number"
        and by_header["무게(kg)"]["decimals"] == 1
    )
    assert by_header["출시일"]["data_type"] == "date"
    assert by_header["단종"]["data_type"] == "bool"
    assert by_header["종류"]["data_type"] == "enum"
    assert by_header["종류"]["enum_options"] == ["너트", "볼트", "와셔"]
    assert by_header["홈페이지"]["data_type"] == "url"
    # 여러 값 칸 — 항목 하나하나로 종류를 맞힌다(a·b 둘뿐이니 고를 값).
    assert by_header["태그"]["multi"] is True and by_header["태그"]["data_type"] == "enum"
    assert by_header["태그"]["enum_options"] == ["a", "b"]
    # 한글 헤더는 키 규칙에 안 맞아 col_N — 라벨은 그대로.
    assert (
        by_header["무게(kg)"]["key"].startswith("col_")
        and by_header["무게(kg)"]["label"] == "무게(kg)"
    )
    assert "고를 값" in by_header["종류"]["note"]


def test_고친_제안으로_타입을_만들고_행을_넣는다(client: TestClient, admin: Signed) -> None:
    got = _upload(client, admin, CSV)
    columns = got["columns"]
    for one in columns:
        # 사람이 키를 읽을 수 있게 고치고, 「종류」 는 새 값이 올 수 있으니 글자로 되돌린다.
        if one["header"] == "무게(kg)":
            one["key"] = "weight"
        if one["header"] == "종류":
            one["data_type"] = "text"
            one["enum_options"] = []
        if one["header"] == "출시일":
            one["key"] = "released"
        if one["header"] == "홈페이지":
            one["role"] = "ignore"
    slug = f"part_{uuid.uuid4().hex[:6]}"
    built = client.post(
        "/api/ontology/infer/build",
        json={
            "slug": slug,
            "label": "부품",
            "key_policy": "required",
            "columns": columns,
            "raw_rows": got["raw_rows"],
        },
        headers=admin.headers,
    )
    assert built.status_code == 200, built.text
    schema = built.json()["schema"]
    keys = [p["key"] for p in schema["types"][0]["properties"]]
    assert (
        "weight" in keys
        and "released" in keys
        and not any(k.startswith("col_7") for k in keys)
    )
    assert schema["types"][0]["key_policy"] == "required"

    # 기존 길 둘 — 정의 적용, 행 넣기.
    applied = client.post(
        "/api/ontology/import?dry_run=false", json=schema, headers=admin.headers
    )
    assert applied.status_code == 200 and applied.json()["applied"] is True
    rows = built.json()["import_rows"]
    assert (
        rows[0]["key"] == "P-001" and rows[0]["label"] == "부품 1" and rows[0]["weight"] == 0.5
    )
    assert "홈페이지" not in rows[0] and rows[0]["description"].startswith("긴 설명")
    done = client.post(
        f"/api/objects/{slug}/import-rows",
        json={"rows": rows, "workspace_slug": admin.workspace, "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    assert done["counts"]["create"] == 30
    listed = client.get(
        f"/api/objects/{slug}", params={"f.weight.gte": "14"}, headers=admin.headers
    ).json()
    assert listed["total"] == 3


def test_이름_열이_없으면_말한다(client: TestClient, admin: Signed) -> None:
    got = _upload(client, admin, "a,b\n1,2\n3,4\n")
    assert all(one["role"] == "property" for one in got["columns"])
    denied = client.post(
        "/api/ontology/infer/build",
        json={
            "slug": "t_x",
            "label": "x",
            "columns": got["columns"],
            "raw_rows": got["raw_rows"],
        },
        headers=admin.headers,
    )
    assert denied.status_code == 409 and "이름" in denied.json()["error"]["message"]
