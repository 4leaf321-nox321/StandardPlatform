"""일괄 넣기·빼기.

**계획이 거짓말을 안 하나, 전부 아니면 무인가, 두 번 올려도 두 벌이 안 되나.**
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.database import engine
from app.modules.accounts.models import User
from app.modules.objects import bulk
from app.modules.ontology.models import ObjectType
from tests.api.conftest import Signed, bundle_import, export_file, finish_job, import_file
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
    """일괄 입력은 **작업**이다 — 올리면 202, 워커가 계획을 세우고, 적용은
    `/jobs/{id}/apply`. 시험은 그 워커를 이 프로세스에서 돌린다(`conftest.import_file`)."""
    return import_file(client, who, type_slug, csv_text, apply=apply, path=path, name=name)


def _id_of(client: TestClient, who: Signed, type_slug: str, key: str) -> str:
    """식별자로 객체 id — 관계를 읽는 주소가 id 다."""
    rows = client.get(f"/api/objects/{type_slug}", headers=who.headers).json()["items"]
    return str(next(one for one in rows if one["key"] == key)["id"])


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
    assert plan["counts"] == {
        "create": 1,
        "update": 1,
        "unchanged": 0,
        "unlink": 0,
        "error": 1,
    }
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
    assert first["counts"] == {
        "create": 2,
        "update": 0,
        "unchanged": 0,
        "unlink": 0,
        "error": 0,
    }
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["total"] == 2
    bolt = next(one for one in listed["items"] if one["key"] == "P-1")
    assert bolt["properties"] == {"weight": 1, "material": "스틸", "tags": ["a", "b"]}

    # 같은 파일 한 번 더 — 전부 「그대로」.
    again = _upload(client, admin, part, csv_text, apply=True)
    assert again["counts"] == {
        "create": 0,
        "update": 0,
        "unchanged": 2,
        "unlink": 0,
        "error": 0,
    }
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

    exported = export_file(client, admin, f"{part}/export")
    text = exported.text.lstrip("﻿")
    assert "V-1" in text and acme["id"] not in text
    assert "예" in text

    # 그대로 다시 올리면 전부 「그대로」.
    plan = _upload(client, admin, part, text, apply=True)
    assert plan["counts"] == {
        "create": 0,
        "update": 0,
        "unchanged": 1,
        "unlink": 0,
        "error": 0,
    }

    as_json = export_file(client, admin, f"{part}/export", {"format": "json"}).json()
    assert as_json["rows"][0]["vendor"] == "V-1"


def test_내보내기는_목록과_같은_거르기를_쓴다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    _make_object(client, admin, part, key="P-1", label="볼트", properties={"material": "스틸"})
    _make_object(
        client, admin, part, key="P-2", label="고무링", properties={"material": "고무"}
    )
    exported = export_file(client, admin, f"{part}/export", {"p.material": "고무"})
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
        data={"workspace_slug": member.workspace},
        headers=member.headers,
    )
    # 넣는 순간에 거절한다 — 워커가 돌 때 거절하면 사람은 몇 분 뒤에야 본다.
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
    assert plan["counts"] == {
        "create": 1,
        "update": 0,
        "unchanged": 0,
        "unlink": 0,
        "error": 0,
    }
    assert plan["rows"][0]["label"] == "볼트 -공급받음-> ACME"

    done = _upload(client, admin, part, csv_text, path="relations/import", apply=True)
    assert done["applied"] is True
    again = _upload(client, admin, part, csv_text, path="relations/import", apply=True)
    assert again["counts"]["unchanged"] == 1

    exported = export_file(client, admin, f"{part}/relations/export")
    lines = exported.text.lstrip("﻿").splitlines()
    assert lines == ["src,relation,dst,evidence_note", f"P-1,{kind},V-1,카탈로그 12쪽"]


def test_별칭은_목록으로_오면_쪼개지_않는다(client: TestClient, admin: Signed) -> None:
    """**JSON 으로 온 목록을 글자로 바꾸면 안 된다.**

    예전에는 `str(raw).split(";")` 이라 `["박리", "코팅 박리"]` 가 `"['박리', '코팅 박리']"`
    **한 덩어리**로 저장됐다. 그리고 별칭 **안에** `;` 가 든 자료가 있다 — 그런 것은 목록으로
    보내야 살아남는다(고장 모드 80건 · 메커니즘 482건).
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    got = client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [
                {"key": "F-1", "label": "박리", "aliases": ["코팅 박리", "벗겨짐"]},
                # **별칭 안에 `;` 가 든 것** — 목록으로 보내면 그대로 하나로 산다.
                {"key": "F-2", "label": "균열", "aliases": ["갈라짐; 크랙"]},
                # 글 하나로 오면 `;` 로 가른다(CSV 한 칸에 여럿을 적는 길).
                {"key": "F-3", "label": "부식", "aliases": "녹; 산화"},
            ],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text

    rows = client.get(f"/api/objects/{mode}", headers=admin.headers).json()["items"]
    first = next(one for one in rows if one["key"] == "F-1")
    detail = client.get(f"/api/objects/{mode}/{first['id']}", headers=admin.headers).json()
    # 목록은 그대로 둘이 된다 — 한 덩어리가 아니다.
    assert sorted(detail["object"]["aliases"]) == ["벗겨짐", "코팅 박리"]

    second = next(one for one in rows if one["key"] == "F-2")
    other = client.get(f"/api/objects/{mode}/{second['id']}", headers=admin.headers).json()
    # **`;` 가 든 이름은 쪼개지지 않는다** — 이것이 80건 · 482건을 살리는 길이다.
    assert other["object"]["aliases"] == ["갈라짐; 크랙"]

    third = next(one for one in rows if one["key"] == "F-3")
    text_one = client.get(f"/api/objects/{mode}/{third['id']}", headers=admin.headers).json()
    assert sorted(text_one["object"]["aliases"]) == ["녹", "산화"]


def test_별칭_하나가_겹쳐도_나머지는_들어간다(client: TestClient, admin: Signed) -> None:
    """**한 줄 때문에 수백 줄이 막히면 안 된다.**

    별칭은 이름을 거드는 값이지 정체성이 아니다. 겹치는 것만 빼고 넣되, 무엇이 빠졌는지
    계획에 적는다 — 조용히 버리면 나중에 「그 이름으로 검색이 안 된다」 로 만난다.
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [{"key": "F-1", "label": "박리", "aliases": ["코팅 도장 손상"]}],
            "apply": True,
        },
        headers=admin.headers,
    )

    plan = client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [
                {
                    "key": "F-2",
                    "label": "코딩 박리",
                    "aliases": ["코팅 도장 손상", "코팅 벗김"],
                },
                {"key": "F-3", "label": "부식", "aliases": ["녹"]},
            ]
        },
        headers=admin.headers,
    ).json()
    # **행이 오류가 아니다** — 겹치는 별칭만 빠지고, 그 사실이 줄에 적힌다.
    assert [one["action"] for one in plan["rows"]] == ["create", "create"], plan
    assert "코팅 도장 손상" in plan["rows"][0]["message"]

    client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [
                {
                    "key": "F-2",
                    "label": "코딩 박리",
                    "aliases": ["코팅 도장 손상", "코팅 벗김"],
                },
                {"key": "F-3", "label": "부식", "aliases": ["녹"]},
            ],
            "apply": True,
        },
        headers=admin.headers,
    )
    rows = client.get(f"/api/objects/{mode}", headers=admin.headers).json()["items"]
    made = next(one for one in rows if one["key"] == "F-2")
    detail = client.get(f"/api/objects/{mode}/{made['id']}", headers=admin.headers).json()
    # 겹친 것만 빠지고 나머지는 붙었다 — 그리고 원래 주인의 별칭은 그대로다.
    assert detail["object"]["aliases"] == ["코팅 벗김"]


def test_너무_긴_별칭은_자르지_않고_뺀다(client: TestClient, admin: Signed) -> None:
    """**조용히 자르면 다른 이름이 된다.**

    별칭 칸은 200자다. 예전에는 `value[:200]` 으로 잘라 넣었다 — 잘린 것으로는 원래 이름으로
    검색이 안 되고, 앞 200자가 같은 둘은 서로 충돌한다. 넣은 사람은 둘 다 모른다.
    파일에서는 그 별칭만 빼고 줄에 적고, 손으로 넣는 자리에서는 거절한다.
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    long_one = "가" * 201
    plan = client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [{"key": "F-1", "label": "박리", "aliases": [long_one, "코팅 벗김"]}]},
        headers=admin.headers,
    ).json()
    assert plan["rows"][0]["action"] == "create", plan
    assert "201자" in plan["rows"][0]["message"], plan["rows"][0]

    client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [{"key": "F-1", "label": "박리", "aliases": [long_one, "코팅 벗김"]}],
            "apply": True,
        },
        headers=admin.headers,
    )
    made = _id_of(client, admin, mode, "F-1")
    detail = client.get(f"/api/objects/{mode}/{made}", headers=admin.headers).json()
    assert detail["object"]["aliases"] == ["코팅 벗김"]

    # 화면에서 손으로 넣을 때는 **거절한다** — 사람이 바로 고칠 수 있는 자리다.
    denied = client.put(
        f"/api/objects/{mode}/{made}/aliases",
        json={"aliases": [long_one]},
        headers=admin.headers,
    )
    assert denied.status_code == 422, denied.text
    assert "200자" in denied.json()["error"]["message"], denied.text


def test_관계를_파일대로_맞추면_없는_선은_끊는다(client: TestClient, admin: Signed) -> None:
    """**사라진 관계를 정리할 길이 없었다** — 가져오기는 더하기만 했다.

    범위는 파일에 **나온** (출발 객체 · 관계 종류)다. 파일에 아예 없는 객체의 선은 안 건드린다
    — 한 타입의 일부만 담은 파일이 나머지 전부를 지우는 것은 되돌릴 수 없는 사고다.
    끊는 것은 계획에 「끊음」 으로 올라오고, 사람이 보고 적용한다.
    """
    cause = _make_type(client, admin, label="원인", key_policy="required")
    effect = _make_type(client, admin, label="결과", key_policy="required")
    kind = f"causes_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "일으킴",
                    "src_type_slugs": [cause],
                    "dst_type_slugs": [effect],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    client.post(
        f"/api/objects/{cause}/import-rows",
        json={
            "rows": [{"key": "C-1", "label": "진동"}, {"key": "C-2", "label": "과열"}],
            "apply": True,
        },
        headers=admin.headers,
    )
    client.post(
        f"/api/objects/{effect}/import-rows",
        json={
            "rows": [{"key": "E-1", "label": "균열"}, {"key": "E-2", "label": "변색"}],
            "apply": True,
        },
        headers=admin.headers,
    )
    both = [
        {"src": "C-1", "relation": kind, "dst": "E-1"},
        {"src": "C-1", "relation": kind, "dst": "E-2"},
        {"src": "C-2", "relation": kind, "dst": "E-1"},
    ]
    client.post(
        f"/api/objects/{cause}/relations/import-rows",
        json={"rows": both, "apply": True},
        headers=admin.headers,
    )

    # C-1 의 선 하나만 담은 파일 — 맞춤이면 C-1 의 나머지는 끊고, **C-2 는 안 건드린다.**
    fewer = [{"src": "C-1", "relation": kind, "dst": "E-1"}]
    plan = client.post(
        f"/api/objects/{cause}/relations/import-rows",
        json={"rows": fewer, "relations_mode": "replace"},
        headers=admin.headers,
    ).json()
    assert plan["counts"]["unlink"] == 1, plan
    cut = next(one for one in plan["rows"] if one["action"] == "unlink")
    assert "변색" in cut["label"] and "파일에 없어" in cut["message"]

    # 더하기(기본)면 끊지 않는다.
    keep = client.post(
        f"/api/objects/{cause}/relations/import-rows",
        json={"rows": fewer},
        headers=admin.headers,
    ).json()
    assert keep["counts"]["unlink"] == 0, keep

    client.post(
        f"/api/objects/{cause}/relations/import-rows",
        json={"rows": fewer, "relations_mode": "replace", "apply": True},
        headers=admin.headers,
    )
    left = client.get(
        f"/api/objects/{cause}/{_id_of(client, admin, cause, 'C-1')}", headers=admin.headers
    ).json()["related"]
    assert [one["object_label"] for one in left] == ["균열"]
    # C-2 의 선은 그대로다.
    other = client.get(
        f"/api/objects/{cause}/{_id_of(client, admin, cause, 'C-2')}", headers=admin.headers
    ).json()["related"]
    assert [one["object_label"] for one in other] == ["균열"]


def test_다시_넣어도_사람이_붙인_별칭은_남는다(client: TestClient, admin: Signed) -> None:
    """**적재는 사람이 화면에서 한 일을 지우지 않는다.**

    파이프라인은 같은 파일을 여러 번 넣는다. 예전에는 `aliases` 칸이 통째로 바뀌어, 그 사이에
    사람이 붙여 둔 별칭이 조용히 사라졌다 — 어디에도 안 적히고, 몇 달 뒤 「그 이름으로 검색이
    안 된다」 로 만난다. 기본은 **더하기**고, 파일을 정본으로 보는 자리만 `replace` 다.
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [{"key": "F-1", "label": "박리", "aliases": ["코팅 벗김"]}],
            "apply": True,
        },
        headers=admin.headers,
    )
    made = _id_of(client, admin, mode, "F-1")
    # 사람이 화면에서 하나 더 붙인다.
    put = client.put(
        f"/api/objects/{mode}/{made}/aliases",
        json={"aliases": ["코팅 벗김", "사람이 붙인 별칭"]},
        headers=admin.headers,
    )
    assert put.status_code == 200, put.text

    # 같은 파일을 다시 — 사람이 붙인 것은 그대로 있어야 한다.
    again = client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [{"key": "F-1", "label": "박리", "aliases": ["코팅 벗김"]}],
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    assert [one["action"] for one in again["rows"]] == ["unchanged"], again
    detail = client.get(f"/api/objects/{mode}/{made}", headers=admin.headers).json()
    assert detail["object"]["aliases"] == ["코팅 벗김", "사람이 붙인 별칭"]

    # 파일을 정본으로 볼 때는 맞춘다 — 허브가 쌍둥이에 보낼 때 이 길로 간다.
    replaced = client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [{"key": "F-1", "label": "박리", "aliases": ["코팅 벗김"]}],
            "apply": True,
            "aliases_mode": "replace",
        },
        headers=admin.headers,
    ).json()
    assert [one["action"] for one in replaced["rows"]] == ["update"], replaced
    detail = client.get(f"/api/objects/{mode}/{made}", headers=admin.headers).json()
    assert detail["object"]["aliases"] == ["코팅 벗김"]


def test_옛_식별자로_찾아_키를_바꾼다(client: TestClient, admin: Signed) -> None:
    """**키 체계가 바뀌는 일은 실제로 일어난다**(`F-1` → `FM-BRK-1`).

    옛 식별자로 찾을 길이 없으면 같은 것이 새 객체로 하나 더 생기고, 관계 · 참조는 옛 쪽에
    남는다. `renamed_from` 으로 찾아 키를 바꾸고, **옛 식별자는 별칭으로 남긴다** — 그 번호로
    적힌 문서와 사람의 기억이 있다.
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [{"key": "F-1", "label": "박리"}], "apply": True},
        headers=admin.headers,
    )
    made = _id_of(client, admin, mode, "F-1")

    row = {"renamed_from": "F-1", "key": "FM-BRK-1", "label": "박리"}
    plan = client.post(
        f"/api/objects/{mode}/import-rows", json={"rows": [row]}, headers=admin.headers
    ).json()
    assert plan["rows"][0]["action"] == "update", plan
    assert "key" in plan["rows"][0]["changes"]
    assert "별칭으로 남깁니다" in plan["rows"][0]["message"], plan["rows"][0]

    client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [row], "apply": True},
        headers=admin.headers,
    )
    detail = client.get(f"/api/objects/{mode}/{made}", headers=admin.headers).json()
    # 같은 객체다 — 새로 생기지 않았다.
    assert detail["object"]["key"] == "FM-BRK-1"
    assert detail["object"]["aliases"] == ["F-1"]
    assert len(client.get(f"/api/objects/{mode}", headers=admin.headers).json()["items"]) == 1

    # 옛 것과 새 것이 **서로 다른 객체**면 거절한다 — 합치기가 할 일이다.
    client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [{"key": "F-2", "label": "부식"}], "apply": True},
        headers=admin.headers,
    )
    clash = client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [{"renamed_from": "F-2", "key": "FM-BRK-1", "label": "부식"}]},
        headers=admin.headers,
    ).json()
    assert clash["rows"][0]["action"] == "error", clash
    assert "합치기" in clash["rows"][0]["message"]


def test_관계에_붙는_값도_파일로_넣는다(client: TestClient, admin: Signed) -> None:
    """**선 자체에 딸린 값이 있다** — 인과 관계의 근거 건수 · 근거 종류처럼.

    예전에는 관계 파일이 넷(src · relation · dst · evidence_note)만 받아, 그 값을 넣을 길이
    없었다. 이미 이어진 선도 값이 다르면 고친다 — 안 그러면 나중에 채울 수가 없다.
    """
    cause = _make_type(client, admin, label="원인", key_policy="required")
    effect = _make_type(client, admin, label="결과", key_policy="required")
    kind = f"causes_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "일으킴",
                    "src_type_slugs": [cause],
                    "dst_type_slugs": [effect],
                    # **관계 종류에도 속성을 정의한다.**
                    "properties": [
                        {"key": "n", "label": "근거 건수", "data_type": "number"},
                        {
                            "key": "basis",
                            "label": "근거 종류",
                            "data_type": "enum",
                            "enum_options": ["시험", "시장", "문헌"],
                        },
                    ],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text

    _make_object(client, admin, cause, key="C-1", label="과열")
    _make_object(client, admin, effect, key="E-1", label="변색")

    csv_text = f"src,relation,dst,evidence_note,n,basis\nC-1,{kind},E-1,보고서 3건,3,시험\n"
    plan = _upload(client, admin, cause, csv_text, path="relations/import")
    assert plan["counts"]["create"] == 1, plan
    _upload(client, admin, cause, csv_text, path="relations/import", apply=True)

    # 내보내기가 정본 확인 자리다 — 관계에 붙은 값이 그대로 실린다.
    first = export_file(client, admin, f"{cause}/relations/export").text.lstrip("\ufeff")
    head, row = first.splitlines()[0].split(","), first.splitlines()[1].split(",")
    got = dict(zip(head, row, strict=True))
    assert got["n"] == "3" and got["basis"] == "시험" and got["evidence_note"] == "보고서 3건"

    # **이미 이어진 선이라도 값이 다르면 고친다.**
    later = f"src,relation,dst,evidence_note,n,basis\nC-1,{kind},E-1,보고서 5건,5,시장\n"
    again = _upload(client, admin, cause, later, path="relations/import")
    assert again["counts"]["update"] == 1, again
    _upload(client, admin, cause, later, path="relations/import", apply=True)
    later_text = export_file(client, admin, f"{cause}/relations/export").text.lstrip("\ufeff")
    head, row = later_text.splitlines()[0].split(","), later_text.splitlines()[1].split(",")
    after = dict(zip(head, row, strict=True))
    assert after["n"] == "5" and after["basis"] == "시장"

    # 정의에 없는 열은 거절한다 — 조용히 버리면 넣은 사람은 들어간 줄 안다.
    bad = _upload(
        client,
        admin,
        cause,
        f"src,relation,dst,없는칸\nC-1,{kind},E-1,x\n",
        path="relations/import",
    )
    assert bad["errors"], bad


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

    part = _part_type(client, admin)
    text = "key\tlabel\tweight\nP-1\t볼트\t1.5\nP-2\t너트\t0.5\n"
    planned = import_file(client, admin, part, text, name="pasted.csv")
    assert [r["action"] for r in planned["rows"]] == ["create", "create"]
    assert planned["rows"][0]["label"] == "볼트"


def test_한국어_엑셀이_그냥_저장한_CSV_도_읽는다(client: TestClient, admin: Signed) -> None:
    """한국어 Windows 엑셀의 「CSV (쉼표로 분리)」 는 CP949 다 — 사람이 가장 흔히 만드는
    CSV 가 그것이다. UTF-8 만 받았더니 작업이 `UnicodeDecodeError` 한 줄로 실패했다."""
    part = _part_type(client, admin)
    text = "key,label,재질\nP-1,볼트,스틸\n"
    applied = import_file(client, admin, part, text, encoding="cp949", apply=True)
    assert applied["applied"] is True
    item = client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"][0]
    assert item["label"] == "볼트" and item["properties"]["material"] == "스틸"


def test_읽을_수_없는_글자면_무엇을_할지_말한다(client: TestClient, admin: Signed) -> None:
    part = _part_type(client, admin)
    response = client.post(
        f"/api/objects/{part}/import",
        files={"file": ("rows.csv", io.BytesIO(b"key,label\nP-1,\x80\n"), "text/csv")},
        data={"workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert response.status_code == 202, response.text
    failed = finish_job(client, admin, response.json())
    assert failed["status"] == "failed"
    assert "CSV UTF-8" in failed["error"] and "UnicodeDecodeError" not in failed["error"]


def test_계획은_줄마다_묻지_않는다(client: TestClient, admin: Signed, db: Session) -> None:
    """**질의 수가 줄 수를 따라가면 안 된다.**

    예전에는 줄마다 대여섯 번 물었다 — 2만 줄에서 계획 29초 · 다시 적재 193초였고(실측),
    표가 커질수록 더 느려졌다. 재적재는 흔한 일이라 그 수는 그대로 사람이 기다리는 시간이
    된다. 지금은 파일에 나온 것을 **한 번에 미리 읽는다.**

    시간을 재지 않고 **질의 수**를 센다 — 시간은 기계에 따라 달라지지만, 「줄마다 묻나」 는
    달라지지 않는다.
    """
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client, admin, part, key="serial", label="일련", data_type="text", unique=True
    )
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == part))
    user = db.scalar(select(User).where(User.email == admin.email))
    assert object_type is not None and user is not None

    def rows_of(count: int) -> list[dict[str, Any]]:
        tag = uuid.uuid4().hex[:6]
        return [
            {
                "key": f"P-{tag}-{n}",
                "label": f"부품 {n}",
                "serial": f"S-{tag}-{n}",
                "aliases": [f"별칭{tag}{n}"],
            }
            for n in range(count)
        ]

    def queries(count: int) -> int:
        seen = 0

        def tick(*_args: Any, **_kw: Any) -> None:
            nonlocal seen
            seen += 1

        event.listen(engine, "before_cursor_execute", tick)
        try:
            plan = bulk.plan_objects(
                db, user, object_type, rows_of(count), owner_workspace_id=None
            )
        finally:
            event.remove(engine, "before_cursor_execute", tick)
        assert plan.ok, plan.errors
        return seen

    small, big = queries(20), queries(200)
    # 열 배 긴 파일이 질의를 열 배 쓰지 않는다 — 몇 번 더 쓰는 것은 상한 없는 `IN` 이 아니라
    # 미리 읽는 질의가 값이 늘어서다.
    assert big <= small + 2, (small, big)
    assert big < 20, big


def test_관계도_줄마다_묻지_않는다(client: TestClient, admin: Signed, db: Session) -> None:
    """**관계 계획도 줄마다 대여섯 번 물었다.**

    끝점을 찾고(식별자 → 별칭 → 이름), 관계 종류의 속성 정의를 다시 읽고, 이미 이어진 선을
    또 물었다. 한 출발점이 수십 줄에 되풀이되는 파일(한 과제에 모델 여럿)에서는 그 대부분이
    같은 물음이다. 객체 쪽과 같이 **시간이 아니라 질의 수**를 센다.
    """
    cause = _make_type(client, admin, label="원인", key_policy="required")
    effect = _make_type(client, admin, label="결과", key_policy="required")
    kind = f"causes_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "일으킴",
                    "src_type_slugs": [cause],
                    "dst_type_slugs": [effect],
                    "properties": [{"key": "n", "label": "근거 건수", "data_type": "number"}],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text

    tag = uuid.uuid4().hex[:6]
    client.post(
        f"/api/objects/{cause}/import-rows",
        json={"rows": [{"key": f"C-{tag}", "label": "진동"}], "apply": True},
        headers=admin.headers,
    )
    client.post(
        f"/api/objects/{effect}/import-rows",
        json={
            "rows": [{"key": f"E-{tag}-{n}", "label": f"결과 {n}"} for n in range(60)],
            "apply": True,
        },
        headers=admin.headers,
    )
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == cause))
    user = db.scalar(select(User).where(User.email == admin.email))
    assert object_type is not None and user is not None

    def queries(count: int, *, apply: bool = False) -> int:
        seen = 0

        def tick(*_args: Any, **_kw: Any) -> None:
            nonlocal seen
            seen += 1

        rows = [
            {"src": f"C-{tag}", "relation": kind, "dst": f"E-{tag}-{n}", "n": n}
            for n in range(count)
        ]
        event.listen(engine, "before_cursor_execute", tick)
        try:
            plan = (
                bulk.apply_relations(db, user, object_type, rows)
                if apply
                else bulk.plan_relations(db, user, object_type, rows)
            )
        finally:
            event.remove(engine, "before_cursor_execute", tick)
        assert plan.ok, [one.message for one in plan.rows if one.action == "error"]
        return seen

    small, big = queries(5), queries(50)
    # **줄 수와 무관해야 한다.** 고치기 전에는 줄당 넷이었다(끝점 찾기 둘 · 속성 정의 ·
    # 이미 이어진 선), 그러고도 새로 잇는 선마다 하나가 더 붙었다.
    assert big - small <= 3, (small, big)

    # **적용도 같은 색인을 쓴다** — 계획이 방금 읽은 것을 다시 묻지 않고, 줄마다 flush 하지
    # 않는다(넣기는 한 문장으로 묶인다).
    applied = queries(50, apply=True)
    assert applied <= 20, applied
    left = client.get(
        f"/api/objects/{cause}/{_id_of(client, admin, cause, f'C-{tag}')}",
        headers=admin.headers,
    ).json()["related"]
    assert len(left) == 50, len(left)


def test_키를_두_번_바꿔도_받는_쪽이_따라온다(client: TestClient, admin: Signed) -> None:
    """**동기화 사이에 키가 두 번 바뀌면**(A→B→C) 마지막 값만으로는 따라오지 못한다.

    받는 쪽이 가진 것은 A 인데 「B 였던 것」 이라고만 말하면 찾을 수가 없다. 그래서 이력을
    목록으로 들고, 받는 쪽은 그 목록을 거꾸로 훑어 제 것을 찾는다. **화면에서 고쳐도**
    같이 남는다 — 파일로 바꿀 때만 남기던 때는 화면에서 고친 키가 밖에서 새 객체가 됐다.
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [{"key": "A-1", "label": "박리"}], "apply": True},
        headers=admin.headers,
    )
    made = _id_of(client, admin, mode, "A-1")

    # ① 화면에서 한 번 바꾼다.
    patched = client.patch(
        f"/api/objects/{mode}/{made}", json={"key": "B-1"}, headers=admin.headers
    )
    assert patched.status_code == 200, patched.text
    # ② 파일로 또 바꾼다.
    client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [{"renamed_from": "B-1", "key": "C-1", "label": "박리"}],
            "apply": True,
        },
        headers=admin.headers,
    )
    detail = client.get(f"/api/objects/{mode}/{made}", headers=admin.headers).json()
    assert detail["object"]["key"] == "C-1"

    # **A 를 들고 있는 쪽도 따라온다** — 이력을 거꾸로 훑는다.
    plan = client.post(
        f"/api/objects/{mode}/import-rows",
        json={"rows": [{"previous_keys": ["A-1", "B-1"], "key": "C-1", "label": "박리"}]},
        headers=admin.headers,
    ).json()
    assert plan["rows"][0]["action"] == "unchanged", plan
    assert len(client.get(f"/api/objects/{mode}", headers=admin.headers).json()["items"]) == 1


def test_못_찾은_참조를_비우고_넣는_길이_있다(client: TestClient, admin: Signed) -> None:
    """**한 줄이 묶음 전체를 막는다** — PLM 에 없는 모델을 가리키는 행 하나 때문에 수만 줄이
    안 들어갔다. 별칭이 겹칠 때와 같은 규칙으로, 그 칸만 비우고 줄에 적는 길을 둔다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="required")
    tool = _make_type(client, admin, label="툴", key_policy="required")
    _make_property(
        client,
        admin,
        tool,
        key="vendor",
        label="개발사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    body = {
        "objects": [
            {
                "type_slug": tool,
                "workspace_slug": admin.workspace,
                "rows": [{"key": "T-1", "label": "툴1", "vendor": "없는공급사"}],
            }
        ],
    }
    # 기본은 오류다 — 전부 아니면 무.
    strict = bundle_import(client, admin, {**body, "apply": True})
    assert strict["ok"] is False, strict

    # 비우고 넣으면 들어가고, 그 사실이 줄에 적힌다.
    loose = bundle_import(client, admin, {**body, "apply": True, "missing_refs": "blank"})
    assert loose["ok"] is True, [
        one for batch in loose["objects"] for one in (batch["plan"] or {}).get("rows", [])
    ]
    row = loose["objects"][0]["plan"]["rows"][0]
    assert row["action"] == "create" and "찾지 못해 비웁니다" in row["message"]
    made = _id_of(client, admin, tool, "T-1")
    detail = client.get(f"/api/objects/{tool}/{made}", headers=admin.headers).json()
    assert detail["object"]["properties"].get("vendor") in (None, "")


def test_한_파일_안에서도_개수_제약이_선다(client: TestClient, admin: Signed) -> None:
    """**앞 줄이 만든 선이 검사에 보여야 한다.**

    개수 제약과 순환 금지는 표를 본다. 줄마다 쓰던 것을 없애면서(빠르게 하려고) 아직 안 쓴
    선을 검사가 못 보게 됐다 — 「하나만」 인 관계가 **한 파일 안에서** 둘이 될 수 있었다.
    계획은 표에 아무것도 없으니 `seen` 으로 보고, 적용은 제약이 걸린 종류에서만 먼저 쓴다.
    """
    model = _make_type(client, admin, label="모델", key_policy="required")
    kind = f"derived_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "원 모델",
                    "src_type_slugs": [model],
                    "dst_type_slugs": [model],
                    # **하나만** 맺는다 — 계층이다.
                    "cardinality": "many_to_one",
                    "acyclic": True,
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    tag = uuid.uuid4().hex[:6]
    client.post(
        f"/api/objects/{model}/import-rows",
        json={
            "rows": [{"key": f"M-{tag}-{n}", "label": f"모델 {n}"} for n in range(3)],
            "apply": True,
        },
        headers=admin.headers,
    )

    # 한 파일에서 같은 출발점이 둘을 가리킨다 — **계획에서** 걸려야 한다.
    doubled = [
        {"src": f"M-{tag}-0", "relation": kind, "dst": f"M-{tag}-1"},
        {"src": f"M-{tag}-0", "relation": kind, "dst": f"M-{tag}-2"},
    ]
    plan = client.post(
        f"/api/objects/{model}/relations/import-rows",
        json={"rows": doubled},
        headers=admin.headers,
    ).json()
    assert [one["action"] for one in plan["rows"]] == ["create", "error"], [
        (one["action"], one["message"]) for one in plan["rows"]
    ]
    assert "앞 줄이 이미 맺었습니다" in plan["rows"][1]["message"]

    # 적용도 막는다 — 계획이 막으므로 아무것도 안 들어간다.
    applied = client.post(
        f"/api/objects/{model}/relations/import-rows",
        json={"rows": doubled, "apply": True},
        headers=admin.headers,
    ).json()
    assert applied["applied"] is False, applied
    detail = client.get(
        f"/api/objects/{model}/{_id_of(client, admin, model, f'M-{tag}-0')}",
        headers=admin.headers,
    ).json()
    assert detail["related"] == []

    # 한 줄씩이면 들어간다(제약을 안 어긴다).
    ok = client.post(
        f"/api/objects/{model}/relations/import-rows",
        json={"rows": doubled[:1], "apply": True},
        headers=admin.headers,
    ).json()
    assert ok["applied"] is True, ok


def test_한_파일_안의_순환도_계획에서_걸린다(client: TestClient, admin: Signed) -> None:
    """**계획은 표를 보는데, 파일 안의 선은 표에 없다.**

    개수 제약은 이미 파일 안을 봤지만 순환은 안 봤다 — 세 줄로 고리를 만들면 계획이 「셋 다
    새로 잇습니다」 라고 말하고, 적용이 마지막 줄에서 터져 **묶음 전체가 롤백**됐다. 사람은
    통과한 계획을 보고 적용을 눌렀는데 아무것도 안 들어간다.
    """
    model = _make_type(client, admin, label="모델", key_policy="required")
    kind = f"parent_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "상위",
                    "src_type_slugs": [model],
                    "dst_type_slugs": [model],
                    "acyclic": True,
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    tag = uuid.uuid4().hex[:6]
    client.post(
        f"/api/objects/{model}/import-rows",
        json={
            "rows": [{"key": f"P-{tag}-{n}", "label": f"모델 {n}"} for n in range(3)],
            "apply": True,
        },
        headers=admin.headers,
    )

    looped = [
        {"src": f"P-{tag}-0", "relation": kind, "dst": f"P-{tag}-1"},
        {"src": f"P-{tag}-1", "relation": kind, "dst": f"P-{tag}-2"},
        {"src": f"P-{tag}-2", "relation": kind, "dst": f"P-{tag}-0"},
    ]
    plan = client.post(
        f"/api/objects/{model}/relations/import-rows",
        json={"rows": looped},
        headers=admin.headers,
    ).json()
    assert [one["action"] for one in plan["rows"]] == ["create", "create", "error"], [
        (one["action"], one["message"]) for one in plan["rows"]
    ]
    assert "순환" in plan["rows"][2]["message"]

    # 고리를 안 만드는 두 줄은 그대로 들어간다.
    ok = client.post(
        f"/api/objects/{model}/relations/import-rows",
        json={"rows": looped[:2], "apply": True},
        headers=admin.headers,
    ).json()
    assert ok["applied"] is True, ok


def test_날짜는_여러_표기와_연월을_받아_ISO_로_넣는다(
    client: TestClient, admin: Signed
) -> None:
    """월 집계 표(판매 · 생산)는 날이 없다 — 「2026-09」 를 **그 달 1일**로 받는다.
    「2026.11.5」 · 「2026년 12월」 같은 표기도 ISO 로 바꿔 넣는다(종류 변경과 한 벌).
    날짜 칸에 붙은 시각은 **조용히 버리지 않는다** — 그 행이 오류다."""
    kind = _make_type(client, admin, label="판매", key_policy="required")
    _make_property(client, admin, kind, key="month", label="판매월", data_type="date")
    _make_property(client, admin, kind, key="at", label="집계 시각", data_type="datetime")
    rows = [
        {"key": "S-1", "label": "가", "month": "2026-09"},
        {"key": "S-2", "label": "나", "month": "202610"},
        {"key": "S-3", "label": "다", "month": "2026.11.5"},
        {"key": "S-4", "label": "라", "month": "2026년 12월"},
        {"key": "S-5", "label": "마", "month": "2026-12-02 00:00:00"},
        # ISO 기본형은 늘 받던 것이라 그대로 둔다.
        {"key": "S-6", "label": "바", "month": "20261203", "at": "2026.12.3 9:05"},
    ]
    got = client.post(
        f"/api/objects/{kind}/import-rows",
        json={"rows": rows, "apply": True},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["applied"] is True, got.json()
    items = client.get(f"/api/objects/{kind}", headers=admin.headers).json()["items"]
    assert {one["key"]: one["properties"].get("month") for one in items} == {
        "S-1": "2026-09-01",
        "S-2": "2026-10-01",
        "S-3": "2026-11-05",
        "S-4": "2026-12-01",
        "S-5": "2026-12-02",
        "S-6": "20261203",
    }
    assert next(one for one in items if one["key"] == "S-6")["properties"]["at"] == (
        "2026-12-03T09:05"
    )

    bad = client.post(
        f"/api/objects/{kind}/import-rows",
        json={
            "rows": [
                {"key": "S-7", "label": "사", "month": "2026-12-02 13:05"},
                {"key": "S-8", "label": "아", "month": "2026-13"},
                # 연월일 여섯 자리를 2403년 5월로 읽지 않는다.
                {"key": "S-9", "label": "자", "month": "240305"},
            ]
        },
        headers=admin.headers,
    )
    assert bad.status_code == 200, bad.text
    # 오류 행은 행 번호로 찾는다(헤더 다음이 1).
    messages = {one["row"]: one["message"] for one in bad.json()["rows"]}
    assert "시각이 붙어" in messages[1], messages
    assert "날짜(YYYY-MM-DD)" in messages[2], messages
    assert "날짜(YYYY-MM-DD)" in messages[3], messages
