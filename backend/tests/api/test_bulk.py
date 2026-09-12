"""일괄 넣기·빼기.

**계획이 거짓말을 안 하나, 전부 아니면 무인가, 두 번 올려도 두 벌이 안 되나.**
"""

from __future__ import annotations

import io
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import (
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _upload(
    client: TestClient,
    who: Signed,
    type_slug: str,
    csv_text: str,
    *,
    apply: bool = False,
    path: str = "import",
    name: str = "rows.csv",
) -> Any:
    response = client.post(
        f"/api/objects/{type_slug}/{path}",
        files={"file": (name, io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        data={"workspace_slug": who.workspace, "apply": "true" if apply else "false"},
        headers=who.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _part_type(client: TestClient, admin: Signed) -> str:
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client, admin, part, key="weight", label="무게", data_type="number", unit="kg"
    )
    _make_property(
        client,
        admin,
        part,
        key="material",
        label="재질",
        data_type="enum",
        enum_options=["스틸", "고무"],
    )
    _make_property(
        client, admin, part, key="tags", label="꼬리표", data_type="text", multi=True
    )
    return part


def test_템플릿은_속성이_헤더로_적힌_빈_CSV(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    response = client.get(f"/api/objects/{part}/template", headers=admin.headers)
    assert response.status_code == 200
    # 엑셀이 한글을 안 깨뜨리게 BOM 이 붙는다.
    assert response.content.startswith("﻿".encode())
    header = response.text.lstrip("\ufeff").splitlines()[0].split(",")
    assert header[:8] == [
        "id",
        "key",
        "label",
        "description",
        "aliases",
        "status",
        "valid_from_year",
        "valid_to_year",
    ]
    assert sorted(header[8:]) == ["material", "tags", "weight"]


def test_계획은_아무것도_안_바꾸고_행마다_말한다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    existing = _make_object(
        client, admin, part, key="P-1", label="볼트", properties={"weight": 1}
    )
    csv_text = (
        "key,label,weight,material\n"
        "P-1,볼트,2,스틸\n"  # 고침 (무게·재질)
        "P-2,너트,0.5,\n"  # 새로
        "P-3,와셔,가벼움,\n"  # 오류 — 숫자 아님
    )
    plan = _upload(client, admin, part, csv_text)
    assert plan["applied"] is False
    assert plan["counts"] == {"create": 1, "update": 1, "unchanged": 0, "error": 1}
    by_row = {one["row"]: one for one in plan["rows"]}
    assert by_row[1]["action"] == "update"
    assert by_row[1]["object_id"] == existing["id"]
    assert sorted(by_row[1]["changes"]) == ["material", "weight"]
    assert by_row[2]["action"] == "create"
    assert by_row[3]["action"] == "error"
    assert "숫자" in by_row[3]["message"]
    # 정말 아무것도 안 바뀌었다.
    profile = client.get(f"/api/objects/{part}/{existing['id']}", headers=admin.headers).json()
    assert profile["object"]["properties"]["weight"] == 1
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["total"] == 1


def test_한_행이라도_틀리면_아무것도_안_넣는다(client: TestClient, admin: Signed) -> None:
    """반쯤 들어간 파일은 어디까지 들어갔는지를 사람이 챙겨야 하고, 아무도 안 챙긴다."""
    part = _part_type(client, admin)
    csv_text = "key,label,weight\nP-1,볼트,1\nP-2,너트,무거움\n"
    plan = _upload(client, admin, part, csv_text, apply=True)
    assert plan["applied"] is False
    assert plan["counts"]["error"] == 1
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["total"] == 0


def test_적용하면_넣고_다시_올려도_두_벌이_안_된다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    csv_text = "key,label,weight,material,tags\nP-1,볼트,1,스틸,a;b\nP-2,너트,0.5,고무,\n"
    first = _upload(client, admin, part, csv_text, apply=True)
    assert first["applied"] is True
    assert first["counts"] == {"create": 2, "update": 0, "unchanged": 0, "error": 0}
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["total"] == 2
    bolt = next(one for one in listed["items"] if one["key"] == "P-1")
    assert bolt["properties"] == {"weight": 1, "material": "스틸", "tags": ["a", "b"]}

    # 같은 파일 한 번 더 — 전부 「그대로」.
    again = _upload(client, admin, part, csv_text, apply=True)
    assert again["counts"] == {"create": 0, "update": 0, "unchanged": 2, "error": 0}
    assert client.get(f"/api/objects/{part}", headers=admin.headers).json()["total"] == 2


def test_빈_칸은_안_보낸_것이고_null_표시가_비움이다(
    client: TestClient, admin: Signed
) -> None:
    """열 하나 빠진 파일로 300개 속성이 날아가면 안 된다."""
    part = _part_type(client, admin)
    made = _make_object(
        client,
        admin,
        part,
        key="P-1",
        label="볼트",
        properties={"weight": 1, "material": "스틸"},
    )
    # weight 열이 없고 material 은 빈 칸 — 둘 다 그대로여야 한다.
    plan = _upload(client, admin, part, "key,label,material\nP-1,볼트,\n", apply=True)
    assert plan["counts"]["unchanged"] == 1
    profile = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert profile["object"]["properties"] == {"weight": 1, "material": "스틸"}

    # \null 은 비움.
    plan = _upload(client, admin, part, "key,label,material\nP-1,볼트,\\null\n", apply=True)
    assert plan["rows"][0]["changes"] == ["material"]
    profile = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert profile["object"]["properties"] == {"weight": 1}


def test_참조는_식별자나_이름으로_적고_겹치면_거절한다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    acme = _make_object(client, admin, vendor, key="V-1", label="ACME")
    _make_object(client, admin, vendor, label="같은이름")
    _make_object(client, admin, vendor, label="같은이름")

    plan = _upload(
        client,
        admin,
        part,
        "key,label,vendor\nP-1,볼트,V-1\nP-2,너트,ACME\nP-3,와셔,같은이름\nP-4,핀,없는것\n",
    )
    by_row = {one["row"]: one for one in plan["rows"]}
    assert by_row[1]["action"] == "create"
    assert by_row[2]["action"] == "create"
    assert by_row[3]["action"] == "error" and "2개" in by_row[3]["message"]
    assert by_row[4]["action"] == "error" and "찾을 수 없" in by_row[4]["message"]

    ok = _upload(
        client, admin, part, "key,label,vendor\nP-1,볼트,V-1\nP-2,너트,ACME\n", apply=True
    )
    assert ok["applied"] is True
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert {one["properties"]["vendor"] for one in listed["items"]} == {acme["id"]}


def test_모르는_열은_통째로_거절한다(client: TestClient, admin: Signed) -> None:
    """조용히 버리면 올린 사람은 들어간 줄 안다."""
    part = _part_type(client, admin)
    plan = _upload(client, admin, part, "key,label,color\nP-1,볼트,빨강\n")
    assert plan["rows"] == []
    assert any("color" in one for one in plan["errors"])


def test_라벨로_적은_열도_받는다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    plan = _upload(client, admin, part, "key,label,무게\nP-1,볼트,3\n", apply=True)
    assert plan["applied"] is True
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["items"][0]["properties"]["weight"] == 3


def test_내보낸_것을_그대로_다시_넣을_수_있다(client: TestClient, admin: Signed) -> None:
    """**왕복.** 참조가 uuid 로 나가면 사람이 못 읽고, 이름으로 나가면 다시 못 넣는다 —
    식별자로 나간다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(client, admin, part, key="ok", label="합격", data_type="bool")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    acme = _make_object(client, admin, vendor, key="V-1", label="ACME")
    _make_object(
        client,
        admin,
        part,
        key="P-1",
        label="볼트",
        properties={"weight": 1.5, "ok": True, "vendor": acme["id"]},
    )

    exported = client.get(f"/api/objects/{part}/export", headers=admin.headers)
    assert exported.status_code == 200
    text = exported.text.lstrip("﻿")
    assert "V-1" in text and acme["id"] not in text
    assert "예" in text

    # 그대로 다시 올리면 전부 「그대로」.
    plan = _upload(client, admin, part, text, apply=True)
    assert plan["counts"] == {"create": 0, "update": 0, "unchanged": 1, "error": 0}

    as_json = client.get(
        f"/api/objects/{part}/export?format=json", headers=admin.headers
    ).json()
    assert as_json["rows"][0]["vendor"] == "V-1"


def test_내보내기는_목록과_같은_거르기를_쓴다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    _make_object(client, admin, part, key="P-1", label="볼트", properties={"material": "스틸"})
    _make_object(
        client, admin, part, key="P-2", label="고무링", properties={"material": "고무"}
    )
    exported = client.get(
        f"/api/objects/{part}/export", params={"p.material": "고무"}, headers=admin.headers
    )
    lines = exported.text.lstrip("﻿").splitlines()
    assert len(lines) == 2 and "고무링" in lines[1]


def test_부서를_고칠_수_없는_사람은_파일을_못_올린다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """만들기와 같은 문턱 — 부서 관리자거나 시스템 관리자."""
    part = _part_type(client, admin)
    response = client.post(
        f"/api/objects/{part}/import",
        files={"file": ("rows.csv", io.BytesIO(b"key,label\nP-1,x\n"), "text/csv")},
        data={"workspace_slug": member.workspace, "apply": "false"},
        headers=member.headers,
    )
    assert response.status_code == 403


def test_관계_파일은_두_번_올려도_선이_한_겹이다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    _make_object(client, admin, part, key="P-1", label="볼트")
    _make_object(client, admin, vendor, key="V-1", label="ACME")

    csv_text = f"src,relation,dst,evidence_note\nP-1,{kind},ACME,카탈로그 12쪽\n"
    plan = _upload(client, admin, part, csv_text, path="relations/import")
    assert plan["counts"] == {"create": 1, "update": 0, "unchanged": 0, "error": 0}
    assert plan["rows"][0]["label"] == "볼트 -공급받음-> ACME"

    done = _upload(client, admin, part, csv_text, path="relations/import", apply=True)
    assert done["applied"] is True
    again = _upload(client, admin, part, csv_text, path="relations/import", apply=True)
    assert again["counts"]["unchanged"] == 1

    exported = client.get(f"/api/objects/{part}/relations/export", headers=admin.headers)
    lines = exported.text.lstrip("﻿").splitlines()
    assert lines == ["src,relation,dst,evidence_note", f"P-1,{kind},V-1,카탈로그 12쪽"]


def test_관계_파일의_끝점이_없으면_행_오류(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    kind = _make_relation(client, admin, "near", label="가까움")
    _make_object(client, admin, part, key="P-1", label="볼트")
    plan = _upload(
        client,
        admin,
        part,
        f"src,relation,dst\nP-1,{kind},없는것\nP-1,없는관계,P-1\n",
        path="relations/import",
    )
    assert [one["action"] for one in plan["rows"]] == ["error", "error"]


def test_JSON_행으로도_같은_규칙(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    response = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [{"key": "P-1", "label": "볼트", "weight": 2, "tags": ["a"]}],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["create"] == 1
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["items"][0]["properties"] == {"weight": 2, "tags": ["a"]}


def test_상한을_넘는_파일은_거절한다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    response = client.post(
        f"/api/objects/{part}/import-rows",
        json={"rows": [{"key": f"P-{i}", "label": "x"} for i in range(5001)], "apply": False},
        headers=admin.headers,
    )
    body = response.json()
    assert body["rows"] == [] and any("5000" in one for one in body["errors"])


def test_파일_안_중복_식별자는_오류(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    plan = _upload(client, admin, part, "key,label\nP-1,볼트\nP-1,볼트2\n")
    assert plan["rows"][1]["action"] == "error"
    assert "행에도" in plan["rows"][1]["message"]


# --- 리뷰에서 잡힌 것 ------------------------------------------------------------


def test_id_로_고치면서_식별자도_바꾼다(client: TestClient, admin: Signed) -> None:
    """계획은 「key 바뀜」 이라 했는데 적용이 옛 키를 쓰던 구멍."""
    part = _part_type(client, admin)
    made = _make_object(client, admin, part, key="P-1", label="볼트")
    done = _upload(client, admin, part, f"id,key,label\n{made['id']},P-9,볼트\n", apply=True)
    assert done["applied"] is True and done["rows"][0]["changes"] == ["key"]
    profile = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert profile["object"]["key"] == "P-9"


def test_JSON_의_null_은_비움이고_없는_키는_안_보냄이다(
    client: TestClient, admin: Signed
) -> None:
    """MCP 가 그렇게 약속했다 — 파일과 달리 JSON 은 null 로 「비움」 을 말할 수 있다."""
    part = _part_type(client, admin)
    made = _make_object(
        client,
        admin,
        part,
        key="P-1",
        label="볼트",
        properties={"weight": 1, "material": "스틸"},
    )
    response = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [{"key": "P-1", "label": "볼트", "material": None}],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    )
    assert response.json()["rows"][0]["changes"] == ["material"]
    profile = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert profile["object"]["properties"] == {"weight": 1}


def test_uuid_모양이어도_없는_객체를_가리키면_거절한다(
    client: TestClient, admin: Signed
) -> None:
    """모양만 보고 받으면 없는 것을 가리키는 참조가 저장되고, 화면에는 빈 칸으로 뜬다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    ghost = "11111111-1111-4111-8111-111111111111"
    plan = _upload(client, admin, part, f"key,label,vendor\nP-1,볼트,{ghost}\n", apply=True)
    assert plan["applied"] is False
    assert plan["rows"][0]["action"] == "error" and "찾을 수 없" in plan["rows"][0]["message"]


def test_붙여_넣은_탭_구분_표도_받는다(client: TestClient, admin: Signed) -> None:
    """엑셀에서 복사하면 탭으로 온다 — 사내 DRM 이 저장을 잠그면 붙여 넣기가 유일한 길이다."""
    import io

    part = _part_type(client, admin)
    text = "key\tlabel\tweight\nP-1\t볼트\t1.5\nP-2\t너트\t0.5\n"
    planned = client.post(
        f"/api/objects/{part}/import",
        files={"file": ("pasted.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
        data={"workspace_slug": admin.workspace, "apply": "false"},
        headers=admin.headers,
    )
    assert planned.status_code == 200, planned.text
    assert [r["action"] for r in planned.json()["rows"]] == ["create", "create"]
    assert planned.json()["rows"][0]["label"] == "볼트"
