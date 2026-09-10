"""메타모델과 객체 — **진짜 앱을 부른다.**

여기서 보려는 것은 「검증이 되나」 가 아니라(그건 단위 시험이 본다) **라우터·
권한·소유 판정이 실제로 걸리나** 다. 실제로 깨지는 자리는 대개 거기다.

**시험 DB 는 스위트 하나를 통째로 함께 쓴다.** 「하나뿐이니 막힐 것」 을 기대하는
시험은 앞선 시험이 만든 행 때문에 우연히만 통과한다 — 그래서 각 시험이 자기 slug 를
직접 만들고, 그 값을 돌려받아 쓴다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:6]}"


def _make_type(client: TestClient, admin: Signed, base: str = "part", **kw: Any) -> str:
    """타입 하나를 만들고 **그 slug 를 돌려준다.**"""
    slug = kw.pop("slug", None) or _uniq(base)
    body = {"slug": slug, "label": kw.pop("label", base), **kw}
    response = client.post("/api/ontology/types", json=body, headers=admin.headers)
    assert response.status_code == 201, response.text
    return str(response.json()["slug"])


def _make_property(
    client: TestClient, admin: Signed, type_slug: str, **kw: Any
) -> dict[str, Any]:
    response = client.post(
        f"/api/ontology/types/{type_slug}/properties", json=kw, headers=admin.headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _make_object(client: TestClient, who: Signed, type_slug: str, **kw: Any) -> dict[str, Any]:
    body = {"workspace_slug": who.workspace, **kw}
    response = client.post(f"/api/objects/{type_slug}", json=body, headers=who.headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- 메타모델은 시스템 관리자만 ---------------------------------------------


def test_타입_만들기는_시스템_관리자만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**타입은 전역이다.** 부서 관리자가 만들 수 있으면 같은 개념이 부서마다
    갈리고, 연결하려고 만든 것이 칸막이가 된다."""
    slug = _uniq("part")
    denied = client.post(
        "/api/ontology/types", json={"slug": slug, "label": "부품"}, headers=member.headers
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/api/ontology/types", json={"slug": slug, "label": "부품"}, headers=admin.headers
    )
    assert allowed.status_code == 201


def test_타입_목록은_누구나_본다(client: TestClient, admin: Signed, member: Signed) -> None:
    """**화면이 폼을 그리려면 정의를 읽어야 한다.** 관리자만 읽을 수 있으면
    평범한 사람에게는 빈 화면이 된다."""
    slug = _make_type(client, admin, "vendor", label="공급사")
    response = client.get("/api/ontology/types", headers=member.headers)
    assert response.status_code == 200
    assert any(row["slug"] == slug for row in response.json())


def test_쓸_수_없는_slug_는_거절한다(client: TestClient, admin: Signed) -> None:
    """**바꿀 수 없는 값이므로 들어올 때 막는다.** URL·관계·MCP 도구 이름이 물린다."""
    response = client.post(
        "/api/ontology/types", json={"slug": "Part-No", "label": "부품"}, headers=admin.headers
    )
    assert response.status_code == 422


def test_같은_slug_는_두_번_안_만들어진다(client: TestClient, admin: Signed) -> None:
    slug = _make_type(client, admin, "task", label="과제")
    again = client.post(
        "/api/ontology/types", json={"slug": slug, "label": "과제2"}, headers=admin.headers
    )
    assert again.status_code == 409


def test_속성_종류는_바꿀_수_없다(client: TestClient, admin: Signed) -> None:
    """이미 저장된 값이 새 종류에 안 맞아도 **화면은 아무 말도 안 한다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")

    response = client.patch(
        f"/api/ontology/types/{part}/properties/qty",
        json={"key": "qty", "label": "수량", "data_type": "text"},
        headers=admin.headers,
    )
    assert response.status_code == 409
    assert "바꿀 수 없습니다" in response.json()["error"]["message"]


def test_선택_속성은_고를_것이_있어야_한다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    response = client.post(
        f"/api/ontology/types/{part}/properties",
        json={"key": "grade", "label": "등급", "data_type": "enum"},
        headers=admin.headers,
    )
    assert response.status_code == 409


# --- 객체 ---------------------------------------------------------------


def test_객체를_만들고_속성이_검증된다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")

    created = _make_object(client, admin, part, label="볼트", properties={"qty": 3})
    assert created["properties"] == {"qty": 3}

    bad = client.post(
        f"/api/objects/{part}",
        json={"label": "너트", "properties": {"qty": "셋"}, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert bad.status_code == 422


def test_모르는_속성은_거절한다(client: TestClient, admin: Signed) -> None:
    """조용히 저장하면 **어느 화면에도 안 나오는 데이터**가 쌓이고, 그것이
    있다는 사실은 아무도 모른다."""
    part = _make_type(client, admin, label="부품")
    response = client.post(
        f"/api/objects/{part}",
        json={"label": "볼트", "properties": {"ghost": 1}, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert response.status_code == 422


def test_부분_수정은_안_보낸_속성을_지우지_않는다(client: TestClient, admin: Signed) -> None:
    """**통째로 덮으면 이름 하나 바꿀 때마다 다른 속성이 함께 날아가고, 그 손실은
    저장한 사람 눈에 안 보인다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")
    _make_property(client, admin, part, key="memo", label="메모", data_type="text")

    created = _make_object(
        client, admin, part, label="볼트", properties={"qty": 3, "memo": "재고 확인"}
    )
    patched = client.patch(
        f"/api/objects/{part}/{created['id']}",
        json={"properties": {"qty": 5}},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["properties"] == {"qty": 5, "memo": "재고 확인"}


def test_null_을_보내면_그_속성만_지운다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="memo", label="메모", data_type="text")
    created = _make_object(client, admin, part, label="볼트", properties={"memo": "x"})

    patched = client.patch(
        f"/api/objects/{part}/{created['id']}",
        json={"properties": {"memo": None}},
        headers=admin.headers,
    )
    assert patched.json()["properties"] == {}


def test_식별자_정책을_지킨다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")

    missing = client.post(
        f"/api/objects/{part}",
        json={"label": "볼트", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert missing.status_code == 422

    _make_object(client, admin, part, key="B-1", label="볼트")

    duplicate = client.post(
        f"/api/objects/{part}",
        json={"key": "B-1", "label": "다른 볼트", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert duplicate.status_code == 409


def test_식별자를_안_쓰는_타입에_식별자를_주면_거절한다(
    client: TestClient, admin: Signed
) -> None:
    grade = _make_type(client, admin, "grade", label="등급")  # key_policy 기본 none
    response = client.post(
        f"/api/objects/{grade}",
        json={"key": "A", "label": "A등급", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert response.status_code == 422


def test_없는_객체를_가리키는_참조는_거절한다(client: TestClient, admin: Signed) -> None:
    """**빈 칸으로 나오면 「값이 없음」 과 「가리키던 것이 사라짐」 을 구별할 수 없다.**"""
    vendor = _make_type(client, admin, "vendor", label="공급사")
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )

    response = client.post(
        f"/api/objects/{part}",
        json={
            "label": "볼트",
            "properties": {"vendor": "00000000-0000-0000-0000-000000000001"},
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert response.status_code == 422


def test_있는_객체를_가리키는_참조는_받는다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, "vendor", label="공급사")
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    supplier = _make_object(client, admin, vendor, label="한빛정밀")
    created = _make_object(
        client, admin, part, label="볼트", properties={"vendor": supplier["id"]}
    )
    assert created["properties"]["vendor"] == supplier["id"]


def test_파일_속성은_properties_로_안_받는다(client: TestClient, admin: Signed) -> None:
    """첨부는 `attachments.owner_field` 로 붙는다. **조용히 버리지 않고 말해 준다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="drawing", label="도면", data_type="file")
    response = client.post(
        f"/api/objects/{part}",
        json={
            "label": "볼트",
            "properties": {"drawing": "a.pdf"},
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert response.status_code == 422
    assert "첨부" in response.json()["error"]["message"]


def test_system_타입에는_객체를_안_만든다(client: TestClient, admin: Signed) -> None:
    """system 축은 **원 표를 투영한다.** 여기 행을 만들면 두 벌이 되고 갈린다."""
    dept = _make_type(client, admin, "dept", label="부서", kind_class="system")
    response = client.post(
        f"/api/objects/{dept}",
        json={"label": "본사", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert response.status_code == 403


# --- 권한 -------------------------------------------------------------------


def test_전역_객체는_시스템_관리자만_만든다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """전역은 **여러 부서가 함께 쓴다.** 한 부서가 고치면 다른 부서의 데이터가
    다르게 읽힌다."""
    part = _make_type(client, admin, label="부품")
    denied = client.post(
        f"/api/objects/{part}", json={"label": "볼트"}, headers=member.headers
    )
    assert denied.status_code == 403

    allowed = client.post(
        f"/api/objects/{part}", json={"label": "볼트"}, headers=admin.headers
    )
    assert allowed.status_code == 201
    assert allowed.json()["owner_workspace_slug"] is None


def test_남의_부서_객체는_안_보인다(client: TestClient, admin: Signed, member: Signed) -> None:
    """**없는 것과 안 보이는 것을 같은 말로 답한다** — 구별해 주면 남의 부서에
    무엇이 있는지 id 를 바꿔 가며 알아낼 수 있다."""
    part = _make_type(client, admin, label="부품")
    # 부서 slug 는 하이픈 규칙이다(타입 slug 는 밑줄) — 규칙이 다른 것을 여기서 만난다.
    other_slug = f"other-{uuid.uuid4().hex[:6]}"
    other = client.post(
        "/api/workspaces", json={"slug": other_slug, "name": "다른팀"}, headers=admin.headers
    )
    assert other.status_code == 201, other.text

    created = client.post(
        f"/api/objects/{part}",
        json={"label": "남의 볼트", "workspace_slug": other_slug},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    hidden = created.json()["id"]

    listed = client.get(f"/api/objects/{part}", headers=member.headers).json()
    assert all(row["id"] != hidden for row in listed["items"])

    profile = client.get(f"/api/objects/{part}/{hidden}", headers=member.headers)
    assert profile.status_code == 404


def test_지우면_목록에서_빠진다(client: TestClient, admin: Signed) -> None:
    """**행은 남는다.** 이 객체를 가리키는 관계와 첨부가 밖에 있다."""
    part = _make_type(client, admin, label="부품")
    created = _make_object(client, admin, part, label="볼트")

    removed = client.delete(f"/api/objects/{part}/{created['id']}", headers=admin.headers)
    assert removed.status_code == 204

    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["total"] == 0


# --- 목록 -------------------------------------------------------------------


def test_목록은_상한과_total_을_준다(client: TestClient, admin: Signed) -> None:
    """**서버가 상한을 강제한다.** 없으면 화면이 「전부 보기」 를 구현하면서
    큰 수를 넣고, 그 한 번에 서버가 죽는다."""
    part = _make_type(client, admin, label="부품")
    for index in range(3):
        _make_object(client, admin, part, label=f"볼트{index}")

    response = client.get(f"/api/objects/{part}?limit=1000000", headers=admin.headers).json()
    assert response["total"] == 3
    assert response["limit"] <= 200


def test_검색과_속성_거르기(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")
    for label, grade in (("볼트", "A"), ("너트", "B")):
        _make_object(client, admin, part, label=label, properties={"grade": grade})

    found = client.get(f"/api/objects/{part}?q=볼트", headers=admin.headers).json()
    assert [row["label"] for row in found["items"]] == ["볼트"]

    filtered = client.get(f"/api/objects/{part}?p.grade=B", headers=admin.headers).json()
    assert [row["label"] for row in filtered["items"]] == ["너트"]


def test_프로필이_속성_정의를_함께_준다(client: TestClient, admin: Signed) -> None:
    """따로 받게 하면 두 번 왕복하고, 그 사이에 정의가 바뀔 수 있다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")
    created = _make_object(client, admin, part, label="볼트", properties={"qty": 1})

    profile = client.get(f"/api/objects/{part}/{created['id']}", headers=admin.headers).json()
    assert profile["object"]["label"] == "볼트"
    assert [p["key"] for p in profile["properties_schema"]] == ["qty"]
    assert profile["can_edit"] is True


def test_고칠_수_없는_사람에게는_can_edit_이_거짓이다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**서버가 판정한 것을 화면에 알려 준다.** 화면이 스스로 정하면 어떤 화면은
    단추를 보이고 어떤 화면은 안 보이는 상태가 된다."""
    part = _make_type(client, admin, label="부품")
    created = client.post(
        f"/api/objects/{part}", json={"label": "전역 볼트"}, headers=admin.headers
    )
    assert created.status_code == 201

    profile = client.get(
        f"/api/objects/{part}/{created.json()['id']}", headers=member.headers
    ).json()
    assert profile["object"]["owner_workspace_slug"] is None
    assert profile["can_edit"] is False


# --- 스키마·사이드바·참조 ---------------------------------------------------


def test_스키마_하나로_전부_읽힌다(client: TestClient, admin: Signed, member: Signed) -> None:
    """**MCP 의 입력.** 이 하나를 읽으면 도구를 동적으로 만들 수 있다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")

    schema = client.get("/api/ontology/schema", headers=member.headers).json()
    found = next(row for row in schema["types"] if row["slug"] == part)
    assert [p["key"] for p in found["properties"]] == ["qty"]
    assert "object_ref" in schema["data_types"]
    assert "file" in schema["data_types"]


def test_사이드바는_그룹에_걸린_타입만_낸다(client: TestClient, admin: Signed) -> None:
    """**빈 묶음은 안 내보낸다** — 제목은 「여기 여럿이 있다」 는 신호라서,
    하나도 없는데 달면 거짓말이 된다."""
    filled = _uniq("domain")
    empty = _uniq("empty")
    for slug, label in ((filled, "도메인"), (empty, "빈 묶음")):
        created = client.post(
            "/api/ontology/groups", json={"slug": slug, "label": label}, headers=admin.headers
        )
        assert created.status_code == 201, created.text

    part = _make_type(client, admin, label="부품", nav_group_slug=filled)
    _make_type(client, admin, "grade", label="등급")  # 그룹 없음 — 사이드바에 안 선다

    nav = client.get("/api/ontology/nav", headers=admin.headers).json()
    slugs = [group["slug"] for group in nav]
    assert filled in slugs
    assert empty not in slugs

    group = next(g for g in nav if g["slug"] == filled)
    assert [item["to"] for item in group["items"]] == [f"/o/{part}"]


def test_부서_삭제_확인에_객체가_뜬다(client: TestClient, admin: Signed) -> None:
    """**안 걸면 부서를 지울 때 이 표가 목록에 안 나타나고**, 사람은 아무것도
    안 걸린 줄 안다 — 그리고 FK 가 RESTRICT 라 서버가 500 을 낸다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")

    references = client.get(
        f"/api/workspaces/{admin.workspace}/references", headers=admin.headers
    ).json()
    row = next(item for item in references if item["table"] == "objects")
    assert row["count"] >= 1
    assert row["blocks_delete"] is True


def test_속성을_지우기_전에_값이_몇_개인지_말한다(client: TestClient, admin: Signed) -> None:
    """「정말 삭제하시겠습니까」 만 묻는 창은 아무도 안 읽고 예를 누른다 —
    **읽을 것이 없어서다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")
    for index in range(2):
        _make_object(client, admin, part, label=f"볼트{index}", properties={"qty": index})

    usage = client.get(
        f"/api/ontology/types/{part}/properties/qty/usage", headers=admin.headers
    ).json()
    assert usage["objects_with_value"] == 2


# --- 지우기 -----------------------------------------------------------------


def test_객체가_있는_타입은_못_지운다(client: TestClient, admin: Signed) -> None:
    """**행이 있는데 지우면 그 데이터가 통째로 고아가 된다.** 몇 개가 걸렸는지
    말하며 막고, 그만 쓰려는 것이면 비활성으로 두라고 알려 준다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")

    denied = client.delete(f"/api/ontology/types/{part}", headers=admin.headers)
    assert denied.status_code == 409
    assert "1개" in denied.json()["error"]["message"]


def test_빈_타입은_속성_정의까지_함께_지운다(client: TestClient, admin: Signed) -> None:
    """안 지우면 같은 slug 로 다시 만들 때 **옛 속성이 되살아난다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")

    removed = client.delete(f"/api/ontology/types/{part}", headers=admin.headers)
    assert removed.status_code == 204

    again = _make_type(client, admin, slug=part, label="부품 다시")
    assert (
        client.get(f"/api/ontology/types/{again}/properties", headers=admin.headers).json()
        == []
    )


def test_타입이_걸린_묶음은_못_지운다(client: TestClient, admin: Signed) -> None:
    """FK 가 SET NULL 이라 DB 는 지우게 두지만, 그러면 그 타입들이 **조용히
    사이드바에서 사라진다.**"""
    group = _uniq("domain")
    client.post(
        "/api/ontology/groups", json={"slug": group, "label": "도메인"}, headers=admin.headers
    )
    _make_type(client, admin, label="부품", nav_group_slug=group)

    denied = client.delete(f"/api/ontology/groups/{group}", headers=admin.headers)
    assert denied.status_code == 409
    assert "부품" in denied.json()["error"]["message"]


def test_빈_묶음은_지워진다(client: TestClient, admin: Signed) -> None:
    group = _uniq("empty")
    client.post(
        "/api/ontology/groups", json={"slug": group, "label": "빈 묶음"}, headers=admin.headers
    )
    assert (
        client.delete(f"/api/ontology/groups/{group}", headers=admin.headers).status_code
        == 204
    )
    remaining = client.get("/api/ontology/groups", headers=admin.headers).json()
    assert all(row["slug"] != group for row in remaining)


def test_지우기도_시스템_관리자만(client: TestClient, admin: Signed, member: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    assert (
        client.delete(f"/api/ontology/types/{part}", headers=member.headers).status_code == 403
    )


def test_타입의_묶음을_바꿀_수_있다(client: TestClient, admin: Signed) -> None:
    """**행을 눌러 고치는 자리가 있어야 한다.** 없으면 잘못 넣은 타입을 옮길
    방법이 없어, 사람은 새로 만들고 옛것을 버려 둔다."""
    first, second = _uniq("g1"), _uniq("g2")
    for slug in (first, second):
        client.post(
            "/api/ontology/groups", json={"slug": slug, "label": slug}, headers=admin.headers
        )
    part = _make_type(client, admin, label="부품", nav_group_slug=first)

    moved = client.patch(
        f"/api/ontology/types/{part}",
        json={"slug": part, "label": "부품", "nav_group_slug": second},
        headers=admin.headers,
    )
    assert moved.status_code == 200
    assert moved.json()["nav_group_slug"] == second


def test_타입_수정은_안_보낸_것을_안_건드린다(client: TestClient, admin: Signed) -> None:
    """**전체 교체면 화면이 `list_view` 를 안 실어 보낸 날 그 설정이 통째로
    날아가고, 그 손실은 저장한 사람 눈에 안 보인다.**"""
    part = _make_type(
        client, admin, label="부품", list_view={"columns": ["label"], "search": ["label"]}
    )
    patched = client.patch(
        f"/api/ontology/types/{part}", json={"label": "부품(수정)"}, headers=admin.headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["label"] == "부품(수정)"
    assert patched.json()["list_view"] == {"columns": ["label"], "search": ["label"]}


def test_묶음에서_빼려면_null_을_명시한다(client: TestClient, admin: Signed) -> None:
    """**안 보낸 것과 비운 것을 구별한다.** 안 가르면 다른 칸 하나 고칠 때마다
    그 타입이 메뉴에서 사라진다."""
    group = _uniq("domain")
    client.post(
        "/api/ontology/groups", json={"slug": group, "label": "도메인"}, headers=admin.headers
    )
    part = _make_type(client, admin, label="부품", nav_group_slug=group)

    kept = client.patch(
        f"/api/ontology/types/{part}", json={"label": "부품2"}, headers=admin.headers
    ).json()
    assert kept["nav_group_slug"] == group

    cleared = client.patch(
        f"/api/ontology/types/{part}", json={"nav_group_slug": None}, headers=admin.headers
    ).json()
    assert cleared["nav_group_slug"] is None


def test_속성의_모든_칸을_고칠_수_있다(client: TestClient, admin: Signed) -> None:
    """**고칠 자리가 없으면 지웠다 다시 만들게 되고, 그 순간 그 속성의 값이 전부
    화면에서 사라진다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="width", label="폭", data_type="number")

    patched = client.patch(
        f"/api/ontology/types/{part}/properties/width",
        json={
            "key": "width",
            "label": "너비",
            "data_type": "number",
            "unit": "mm",
            "help": "바깥 지름 기준",
            "required": True,
            "multi": True,
            "sort_order": 5,
        },
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    got = patched.json()
    assert got["label"] == "너비"
    assert got["unit"] == "mm"
    assert got["help"] == "바깥 지름 기준"
    assert got["required"] is True
    assert got["multi"] is True
    assert got["sort_order"] == 5


def test_고른_값_목록을_고칠_수_있다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B"],
    )
    patched = client.patch(
        f"/api/ontology/types/{part}/properties/grade",
        json={
            "key": "grade",
            "label": "등급",
            "data_type": "enum",
            "enum_options": ["A", "B", "C"],
        },
        headers=admin.headers,
    )
    assert patched.json()["enum_options"] == ["A", "B", "C"]


def test_필수로_바꿔도_이미_있는_객체는_안_건드린다(client: TestClient, admin: Signed) -> None:
    """**정의를 고치는 일이 이미 쌓인 자료를 건드리면 안 된다.** 대신 다음에
    그것을 고칠 때 걸린다 — 그때는 사람이 화면 앞에 있어 값을 넣을 수 있다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="memo", label="메모", data_type="text")
    created = _make_object(client, admin, part, label="볼트")

    client.patch(
        f"/api/ontology/types/{part}/properties/memo",
        json={"key": "memo", "label": "메모", "data_type": "text", "required": True},
        headers=admin.headers,
    )

    # 그대로 읽힌다.
    assert (
        client.get(f"/api/objects/{part}/{created['id']}", headers=admin.headers).status_code
        == 200
    )
    # 속성과 상관없는 수정도 그대로 된다. **관계없는 고침을 막지 않는다** —
    # 이름 오타 하나를 고치려다 남의 속성을 채우게 하면, 사람은 고치기를 그만둔다.
    assert (
        client.patch(
            f"/api/objects/{part}/{created['id']}",
            json={"label": "볼트2"},
            headers=admin.headers,
        ).status_code
        == 200
    )
    # **속성을 건드리는 순간 걸린다.** 그때는 사람이 화면 앞에 있어 값을 넣을 수 있다.
    denied = client.patch(
        f"/api/objects/{part}/{created['id']}",
        json={"properties": {}},
        headers=admin.headers,
    )
    assert denied.status_code == 422


def test_참조는_id_가_아니라_이름으로_읽힌다(client: TestClient, admin: Signed) -> None:
    """**값에는 id 만 있다.** 그대로 그리면 목록에 UUID 가 뜨고, 그 열은 아무것도
    말해 주지 못한다 — 그러면 「참조를 열로 보이기」 자체가 쓸모없어진다."""
    vendor = _make_type(client, admin, "vendor", label="공급사")
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    supplier = _make_object(client, admin, vendor, label="한빛정밀")
    _make_object(client, admin, part, label="볼트", properties={"vendor": supplier["id"]})

    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    row = listed["items"][0]
    assert row["properties"]["vendor"] == supplier["id"]
    assert row["ref_labels"][supplier["id"]] == "한빛정밀"


def test_참조가_없으면_이름도_안_싣는다(client: TestClient, admin: Signed) -> None:
    """**한 번에 모아 읽는다.** 참조 속성이 없으면 질의 자체를 안 한다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert listed["items"][0]["ref_labels"] == {}


def test_목록_화면_설정을_저장한다(client: TestClient, admin: Signed) -> None:
    """**정의가 곧 화면이다.** 이것을 못 정하면 속성을 아무리 만들어도 목록은
    기본형으로 떨어지고, 「정의가 곧 화면」 이라는 말이 반만 참이 된다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")

    view = {
        "columns": ["label", "properties.grade"],
        "sort": {"field": "properties.grade", "dir": "desc"},
        "search": ["label", "properties.grade"],
        "filters": ["properties.grade"],
    }
    saved = client.patch(
        f"/api/ontology/types/{part}", json={"list_view": view}, headers=admin.headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["list_view"] == view

    # 그 설정대로 목록이 돈다.
    for label, grade in (("볼트", "A"), ("너트", "B")):
        _make_object(client, admin, part, label=label, properties={"grade": grade})
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert [row["label"] for row in listed["items"]] == ["너트", "볼트"]


# --- 관계 종류 (2-a) ---------------------------------------------------------


def _make_relation(client: TestClient, admin: Signed, base: str = "part_of", **kw: Any) -> str:
    slug = kw.pop("slug", None) or _uniq(base)
    body = {"slug": slug, "label": kw.pop("label", base), **kw}
    response = client.post("/api/ontology/relation-types", json=body, headers=admin.headers)
    assert response.status_code == 201, response.text
    return str(response.json()["slug"])


def test_관계_종류는_시스템_관리자만_만든다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    slug = _uniq("supplied_by")
    denied = client.post(
        "/api/ontology/relation-types",
        json={"slug": slug, "label": "공급"},
        headers=member.headers,
    )
    assert denied.status_code == 403
    assert _make_relation(client, admin, slug=slug, label="공급") == slug


def test_이행적인데_순환을_허용하면_거절한다(client: TestClient, admin: Signed) -> None:
    """**재귀 펼침이 자기 자신으로 돌아오면 트리가 무한히 돈다** — 그 상태는
    화면이 멈추는 것으로만 드러난다."""
    response = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("bad"), "label": "나쁨", "transitive": True, "acyclic": False},
        headers=admin.headers,
    )
    assert response.status_code == 409
    assert "무한히" in response.json()["error"]["message"]


def test_개수_제약을_고른다(client: TestClient, admin: Signed) -> None:
    """**없으면 「한 부품의 공급사는 하나」 를 표현할 방법이 없다.**"""
    slug = _make_relation(
        client, admin, "supplied_by", label="공급", cardinality="many_to_one"
    )
    found = next(
        row
        for row in client.get("/api/ontology/relation-types", headers=admin.headers).json()
        if row["slug"] == slug
    )
    assert found["cardinality"] == "many_to_one"

    bad = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("x"), "label": "x", "cardinality": "one_to_three"},
        headers=admin.headers,
    )
    assert bad.status_code == 422


def test_허용_타입은_실재해야_한다(client: TestClient, admin: Signed) -> None:
    """없는 slug 를 넣어 두면 그 관계는 아무것도 못 맺는데, 화면은 「고를 것이
    없습니다」 라고만 말한다 — **오타인지 데이터가 없는 것인지 구별되지 않는다.**"""
    part = _make_type(client, admin, label="부품")
    ok = _make_relation(client, admin, "made_of", label="재질", src_type_slugs=[part])
    assert ok

    bad = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("y"), "label": "y", "dst_type_slugs": ["없는타입"]},
        headers=admin.headers,
    )
    assert bad.status_code == 404


def test_허용_타입은_null_로_풀고_안_보내면_그대로다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    slug = _make_relation(client, admin, "used_in", label="쓰임", src_type_slugs=[part])

    kept = client.patch(
        f"/api/ontology/relation-types/{slug}", json={"label": "쓰임2"}, headers=admin.headers
    ).json()
    assert kept["src_type_slugs"] == [part]

    cleared = client.patch(
        f"/api/ontology/relation-types/{slug}",
        json={"src_type_slugs": None},
        headers=admin.headers,
    ).json()
    assert cleared["src_type_slugs"] is None


def test_스키마가_관계_종류도_돌려준다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**MCP 가 이 하나를 읽는다.** 관계가 빠지면 기계는 엮는 법을 모른다."""
    slug = _make_relation(client, admin, "caused_by", label="원인", inverse_label="일으킴")
    schema = client.get("/api/ontology/schema", headers=member.headers).json()
    found = next(row for row in schema["relation_types"] if row["slug"] == slug)
    assert found["inverse_label"] == "일으킴"


def test_관계_종류를_지운다(client: TestClient, admin: Signed) -> None:
    slug = _make_relation(client, admin, "tested", label="시험")
    assert (
        client.delete(
            f"/api/ontology/relation-types/{slug}", headers=admin.headers
        ).status_code
        == 204
    )
    remaining = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    assert all(row["slug"] != slug for row in remaining)


def test_속성_묶음과_이름_틀_칸이_저장된다(client: TestClient, admin: Signed) -> None:
    """**동작은 3단계지만 칸은 지금 있다.** 나중에 넣으면 이미 정의된 속성 전부를
    다시 분류해야 한다."""
    part = _make_type(client, admin, label="부품")
    prop = _make_property(
        client, admin, part, key="width", label="폭", data_type="number", section="치수"
    )
    assert prop["section"] == "치수"

    patched = client.patch(
        f"/api/ontology/types/{part}",
        json={"title_template": "{model} {size}"},
        headers=admin.headers,
    ).json()
    assert patched["title_template"] == "{model} {size}"
    # 다른 칸을 고쳐도 안 날아간다.
    again = client.patch(
        f"/api/ontology/types/{part}", json={"label": "부품2"}, headers=admin.headers
    ).json()
    assert again["title_template"] == "{model} {size}"


# --- 관계 맺기 (2-b) ---------------------------------------------------------


def _link(
    client: TestClient,
    who: Signed,
    type_slug: str,
    src: str,
    relation: str,
    dst: str,
    **kw: Any,
) -> Any:
    return client.post(
        f"/api/objects/{type_slug}/{src}/relations",
        json={"relation": relation, "dst_object_id": dst, **kw},
        headers=who.headers,
    )


def test_관계를_맺으면_양쪽_모두에서_보인다(client: TestClient, admin: Signed) -> None:
    """**「이것이 가리키는 것」 만 주면 「이것을 가리키는 것」 을 물을 자리가
    없어진다** — 부품에서 그 부품을 쓰는 어셈블리를 못 보면 지워도 되는지 모른다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client,
        admin,
        "part_of",
        label="속함",
        inverse_label="포함",
        transitive=True,
        acyclic=True,
    )
    parent = _make_object(client, admin, part, label="구동부")
    child = _make_object(client, admin, part, label="모터")

    made = _link(
        client, admin, part, child["id"], kind, parent["id"], evidence_note="BOM 기준"
    )
    assert made.status_code == 201, made.text
    assert made.json()["label"] == "속함"
    assert made.json()["outgoing"] is True

    # 부모 쪽에서는 역방향 말로 읽힌다.
    up = client.get(f"/api/objects/{part}/{parent['id']}", headers=admin.headers).json()
    seen = up["related"][0]
    assert seen["label"] == "포함"
    assert seen["outgoing"] is False
    assert seen["object_label"] == "모터"
    assert seen["evidence_note"] == "BOM 기준"


def test_역방향_이름이_없으면_그렇다고_말한다(client: TestClient, admin: Signed) -> None:
    """빈 칸으로 두면 그 줄이 무슨 관계인지 알 방법이 없다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "tested", label="시험함")
    one = _make_object(client, admin, part, label="A")
    two = _make_object(client, admin, part, label="B")
    _link(client, admin, part, one["id"], kind, two["id"])

    down = client.get(f"/api/objects/{part}/{two['id']}", headers=admin.headers).json()
    assert down["related"][0]["label"] == "시험함의 반대"


def test_허용_타입이_아니면_거절한다(client: TestClient, admin: Signed) -> None:
    """**말이 안 되는 관계가 남으면 그 데이터로는 아무것도 못 믿는다.**"""
    part = _make_type(client, admin, "part", label="부품")
    vendor = _make_type(client, admin, "vendor", label="공급사")
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    one = _make_object(client, admin, part, label="볼트")
    two = _make_object(client, admin, part, label="너트")

    denied = _link(client, admin, part, one["id"], kind, two["id"])
    assert denied.status_code == 409
    assert "공급사" in denied.json()["error"]["message"]


def test_개수_제약을_지킨다(client: TestClient, admin: Signed) -> None:
    """**없으면 「한 부품의 공급사는 하나」 가 조용히 여럿이 된다.**"""
    part = _make_type(client, admin, "part", label="부품")
    vendor = _make_type(client, admin, "vendor", label="공급사")
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        cardinality="many_to_one",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    bolt = _make_object(client, admin, part, label="볼트")
    first = _make_object(client, admin, vendor, label="한빛")
    second = _make_object(client, admin, vendor, label="대성")

    assert _link(client, admin, part, bolt["id"], kind, first["id"]).status_code == 201
    twice = _link(client, admin, part, bolt["id"], kind, second["id"])
    assert twice.status_code == 409
    assert "하나만" in twice.json()["error"]["message"]


def test_순환을_막는다(client: TestClient, admin: Signed) -> None:
    """**자기 조상을 자식으로 넣는 순간 트리가 무한히 돈다** — 그 상태는 화면이
    멈추는 것으로만 드러난다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client, admin, "part_of", label="속함", transitive=True, acyclic=True
    )
    a = _make_object(client, admin, part, label="A")
    b = _make_object(client, admin, part, label="B")
    c = _make_object(client, admin, part, label="C")

    assert _link(client, admin, part, a["id"], kind, b["id"]).status_code == 201
    assert _link(client, admin, part, b["id"], kind, c["id"]).status_code == 201

    # C -> A 를 이으면 A -> B -> C -> A 로 돈다.
    looped = _link(client, admin, part, c["id"], kind, a["id"])
    assert looped.status_code == 409
    assert "순환" in looped.json()["error"]["message"]

    myself = _link(client, admin, part, a["id"], kind, a["id"])
    assert myself.status_code == 409


def test_같은_관계를_두_번_안_맺는다(client: TestClient, admin: Signed) -> None:
    """막지 않으면 「관련 객체」 에 같은 줄이 둘 서고, 사람은 그것을 데이터가
    이상한 것으로 읽는다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    one = _make_object(client, admin, part, label="A")
    two = _make_object(client, admin, part, label="B")

    assert _link(client, admin, part, one["id"], kind, two["id"]).status_code == 201
    assert _link(client, admin, part, one["id"], kind, two["id"]).status_code == 409


def test_출발점을_고칠_수_있어야_맺는다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    """도착점은 **볼 수만 있으면** 된다.

    양쪽 다 고칠 수 있어야 한다고 하면 부서를 가로지르는 연결을 아무도 못 만들고,
    그러면 **연결하려고 만든 것이 칸막이가 된다.** 대신 맺은 사람과 근거가 남는다.
    """
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    mine = _make_object(client, manager, part, label="내 것")
    global_one = client.post(
        f"/api/objects/{part}", json={"label": "전역"}, headers=admin.headers
    ).json()

    # 전역 객체는 시스템 관리자만 고친다 — 출발점으로는 못 쓴다.
    denied = _link(client, manager, part, global_one["id"], kind, mine["id"])
    assert denied.status_code == 403

    # 내 부서 것을 출발점으로 하면 전역을 가리킬 수 있다.
    allowed = _link(client, manager, part, mine["id"], kind, global_one["id"])
    assert allowed.status_code == 201, allowed.text


def test_관계_속성과_근거를_고치고_끊는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    one = _make_object(client, admin, part, label="A")
    two = _make_object(client, admin, part, label="B")
    made = _link(client, admin, part, one["id"], kind, two["id"]).json()

    patched = client.patch(
        f"/api/objects/{part}/{one['id']}/relations/{made['relation_id']}",
        json={"evidence_note": "도면 확인"},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["evidence_note"] == "도면 확인"

    cut = client.delete(
        f"/api/objects/{part}/{one['id']}/relations/{made['relation_id']}",
        headers=admin.headers,
    )
    assert cut.status_code == 204
    profile = client.get(f"/api/objects/{part}/{one['id']}", headers=admin.headers).json()
    assert profile["related"] == []


def test_남의_관계는_내_화면에서_못_끊는다(client: TestClient, admin: Signed) -> None:
    """아니면 남의 관계를 남의 화면에서 끊을 수 있게 된다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    one = _make_object(client, admin, part, label="A")
    two = _make_object(client, admin, part, label="B")
    outsider = _make_object(client, admin, part, label="상관없는 것")
    made = _link(client, admin, part, one["id"], kind, two["id"]).json()

    denied = client.delete(
        f"/api/objects/{part}/{outsider['id']}/relations/{made['relation_id']}",
        headers=admin.headers,
    )
    assert denied.status_code == 404


def test_안_쓰는_관계_종류로는_못_맺는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "old", label="옛것", is_active=False)
    one = _make_object(client, admin, part, label="A")
    two = _make_object(client, admin, part, label="B")

    denied = _link(client, admin, part, one["id"], kind, two["id"])
    assert denied.status_code == 409


def test_맺힌_관계가_있는_종류는_못_지운다(client: TestClient, admin: Signed) -> None:
    """엣지는 slug 를 문자열로 들고 있다(FK 가 없다). 종류를 지우면 그 관계들은
    **이름 없는 엣지**로 남고, 화면은 slug 를 그대로 보여 줄 수밖에 없다."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "near", label="가까움")
    one = _make_object(client, admin, part, label="A")
    two = _make_object(client, admin, part, label="B")
    _link(client, admin, part, one["id"], kind, two["id"])

    denied = client.delete(f"/api/ontology/relation-types/{kind}", headers=admin.headers)
    assert denied.status_code == 409
    assert "1개" in denied.json()["error"]["message"]


# --- 트리 (2-c · 2-d) --------------------------------------------------------


def _tree(client: TestClient, who: Signed, type_slug: str, **params: Any) -> Any:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(
        f"/api/objects/{type_slug}/tree{'?' + query if query else ''}", headers=who.headers
    ).json()


def _tree_type(client: TestClient, admin: Signed) -> tuple[str, str]:
    """트리가 그려지는 타입 하나와 그 관계."""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client,
        admin,
        "part_of",
        label="속함",
        inverse_label="포함",
        transitive=True,
        acyclic=True,
    )
    client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"tree": {"relation": kind, "parent": "dst"}}},
        headers=admin.headers,
    )
    return part, kind


def test_트리를_안_정하면_안_그린다(client: TestClient, admin: Signed) -> None:
    """**`transitive` 인 관계가 둘 이상일 수 있다** — 아무거나 골라 그리면 그
    트리는 무엇을 보여 주는지 말할 수 없다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")
    assert _tree(client, admin, part) == {"nodes": [], "orphan_count": 0}


def test_뿌리와_어디에도_안_걸린_것을_가른다(client: TestClient, admin: Signed) -> None:
    """트리를 아직 안 만든 타입에서는 거의 모두가 부모가 없다 — **안 가르면 뿌리
    목록이 곧 전체 목록이 된다.** 그리고 안 걸린 것을 아예 안 보여 주면 눈에서
    사라진 채 남는다."""
    part, kind = _tree_type(client, admin)
    top = _make_object(client, admin, part, label="구동부")
    child = _make_object(client, admin, part, label="모터")
    _make_object(client, admin, part, label="떠도는 것")
    _link(client, admin, part, child["id"], kind, top["id"])

    roots = _tree(client, admin, part)
    assert [one["label"] for one in roots["nodes"]] == ["구동부"]
    assert roots["nodes"][0]["child_count"] == 1
    assert roots["orphan_count"] == 1

    orphans = _tree(client, admin, part, orphans="true")
    assert [one["label"] for one in orphans["nodes"]] == ["떠도는 것"]


def test_한_단계씩_펼친다(client: TestClient, admin: Signed) -> None:
    """**통째로 안 불러온다** — 부품 5천 개짜리 트리에서 첫 화면이 안 뜬다."""
    part, kind = _tree_type(client, admin)
    top = _make_object(client, admin, part, label="A")
    mid = _make_object(client, admin, part, label="B")
    leaf = _make_object(client, admin, part, label="C")
    _link(client, admin, part, mid["id"], kind, top["id"])
    _link(client, admin, part, leaf["id"], kind, mid["id"])

    # 뿌리에는 A 만. B 는 A 를 펼쳐야 나온다.
    assert [one["label"] for one in _tree(client, admin, part)["nodes"]] == ["A"]
    level = _tree(client, admin, part, parent=top["id"])
    assert [one["label"] for one in level["nodes"]] == ["B"]
    assert level["nodes"][0]["child_count"] == 1


def test_아래_것까지_포함이_기본이다(client: TestClient, admin: Signed) -> None:
    """**안 그러면 상위 노드를 눌렀을 때 목록이 비고, 그 빈 목록은 「없다」 로
    읽힌다.**"""
    part, kind = _tree_type(client, admin)
    top = _make_object(client, admin, part, label="A")
    mid = _make_object(client, admin, part, label="B")
    leaf = _make_object(client, admin, part, label="C")
    _link(client, admin, part, mid["id"], kind, top["id"])
    _link(client, admin, part, leaf["id"], kind, mid["id"])

    deep = client.get(f"/api/objects/{part}?under={top['id']}", headers=admin.headers).json()
    assert sorted(one["label"] for one in deep["items"]) == ["A", "B", "C"]

    shallow = client.get(
        f"/api/objects/{part}?under={top['id']}&deep=false", headers=admin.headers
    ).json()
    assert [one["label"] for one in shallow["items"]] == ["A"]


def test_트리가_없는_타입에_under_를_주면_말해_준다(client: TestClient, admin: Signed) -> None:
    """조용히 무시하면 화면은 좁혀진 줄 아는데 목록은 전부를 보여 준다."""
    part = _make_type(client, admin, label="부품")
    one = _make_object(client, admin, part, label="A")
    denied = client.get(f"/api/objects/{part}?under={one['id']}", headers=admin.headers)
    assert denied.status_code == 409
    assert "트리" in denied.json()["error"]["message"]


def test_부모가_반대쪽인_관계도_그린다(client: TestClient, admin: Signed) -> None:
    """관계는 사람이 정의하므로 방향이 둘 다 가능하다 — **하나로 정하면 반대로
    정의한 사람의 트리가 뒤집힌 채 그려지고 그것을 말해 주는 자리가 없다.**"""
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client,
        admin,
        "contains",
        label="포함",
        inverse_label="속함",
        transitive=True,
        acyclic=True,
    )
    client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"tree": {"relation": kind, "parent": "src"}}},
        headers=admin.headers,
    )
    top = _make_object(client, admin, part, label="구동부")
    child = _make_object(client, admin, part, label="모터")
    # 부모 -> 자식 방향으로 잇는다.
    _link(client, admin, part, top["id"], kind, child["id"])

    roots = _tree(client, admin, part)
    assert [one["label"] for one in roots["nodes"]] == ["구동부"]
    assert [one["label"] for one in _tree(client, admin, part, parent=top["id"])["nodes"]] == [
        "모터"
    ]


# --- 시간 차원 (2-e) ---------------------------------------------------------


def test_evergreen_은_연도_필터를_무시한다(client: TestClient, admin: Signed) -> None:
    """기본값이다. **연도와 상관없는 축**(시험 종류·개발 단계)이 대부분이다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")
    listed = client.get(f"/api/objects/{part}?year=2020", headers=admin.headers).json()
    assert listed["total"] == 1


def test_lifecycle_은_유효_구간으로_거른다(client: TestClient, admin: Signed) -> None:
    """도입·폐지가 있는 축. **NULL 끝은 열려 있다** — 아직 안 끝난 것이다."""
    part = _make_type(client, admin, label="부품", temporal_kind="lifecycle")
    _make_object(client, admin, part, label="옛것", valid_from_year=2010, valid_to_year=2015)
    _make_object(client, admin, part, label="지금것", valid_from_year=2020)

    old = client.get(f"/api/objects/{part}?year=2012", headers=admin.headers).json()
    assert [one["label"] for one in old["items"]] == ["옛것"]

    now = client.get(f"/api/objects/{part}?year=2026", headers=admin.headers).json()
    assert [one["label"] for one in now["items"]] == ["지금것"]


def test_yearly_는_배정한_해에만_나온다(client: TestClient, admin: Signed) -> None:
    """**불연속이 가능하다** — 2024·2026 에는 쓰고 2025 에는 안 쓰는 일이 있다.
    구간으로는 그것을 표현할 수 없다."""
    model = _make_type(client, admin, "model", label="모델", temporal_kind="yearly")
    one = _make_object(client, admin, model, label="A모델")

    saved = client.put(
        f"/api/objects/{model}/{one['id']}/years", json=[2024, 2026], headers=admin.headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == [2024, 2026]

    for year, expected in ((2024, 1), (2025, 0), (2026, 1)):
        listed = client.get(f"/api/objects/{model}?year={year}", headers=admin.headers).json()
        assert listed["total"] == expected, year


def test_연도_배정은_통째로_바꾼다(client: TestClient, admin: Signed) -> None:
    """**화면이 보여 준 것과 저장되는 것이 같아야 한다.**"""
    model = _make_type(client, admin, "model", label="모델", temporal_kind="yearly")
    one = _make_object(client, admin, model, label="A모델")
    client.put(f"/api/objects/{model}/{one['id']}/years", json=[2024], headers=admin.headers)
    client.put(f"/api/objects/{model}/{one['id']}/years", json=[2026], headers=admin.headers)
    got = client.get(f"/api/objects/{model}/{one['id']}/years", headers=admin.headers).json()
    assert got == [2026]


def test_연도_축이_아니면_배정을_거절한다(client: TestClient, admin: Signed) -> None:
    """조용히 저장하면 아무 데도 안 쓰이는 데이터가 쌓인다."""
    part = _make_type(client, admin, label="부품")  # evergreen
    one = _make_object(client, admin, part, label="볼트")
    denied = client.put(
        f"/api/objects/{part}/{one['id']}/years", json=[2024], headers=admin.headers
    )
    assert denied.status_code == 409


def test_derived_는_등록이_없으면_안_거른다(client: TestClient, admin: Signed) -> None:
    """**빈 목록을 주지 않는다.** 빈 목록은 「데이터가 없다」 로 읽히고, 그러면
    사람은 없는 것을 새로 만든다 — 필터가 작동 안 하는 것과는 다른 일이다."""
    part = _make_type(client, admin, label="부품", temporal_kind="derived")
    _make_object(client, admin, part, label="볼트")
    listed = client.get(f"/api/objects/{part}?year=2020", headers=admin.headers).json()
    assert listed["total"] == 1


# --- 속성 표현력 (3-a) -------------------------------------------------------


def test_숫자의_아래위_끝을_지킨다(client: TestClient, admin: Signed) -> None:
    """**없으면 두께가 -5mm 여도 통과한다** — 그 값은 나중에 집계에 섞여 들어가
    어디서 온 것인지 아무도 못 찾는다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="width",
        label="폭",
        data_type="number",
        unit="mm",
        min_value=0,
        max_value=500,
    )
    assert _make_object(client, admin, part, label="A", properties={"width": 10})

    for bad in (-5, 900):
        denied = client.post(
            f"/api/objects/{part}",
            json={
                "label": "B",
                "properties": {"width": bad},
                "workspace_slug": admin.workspace,
            },
            headers=admin.headers,
        )
        assert denied.status_code == 422, bad
        assert "mm" in denied.json()["error"]["message"]


def test_소수_자릿수는_반올림하지_않고_거절한다(client: TestClient, admin: Signed) -> None:
    """**조용히 바꾸면 사람이 넣은 값과 저장된 값이 달라지고, 그 차이는 아무
    데도 안 뜬다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="w", label="폭", data_type="number", decimals=1)
    assert _make_object(client, admin, part, label="A", properties={"w": 1.5})
    denied = client.post(
        f"/api/objects/{part}",
        json={"label": "B", "properties": {"w": 1.55}, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "소수점" in denied.json()["error"]["message"]


def test_모양_규칙을_지킨다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="dwg",
        label="도번",
        data_type="text",
        pattern=r"^D-\d{4}$",
    )
    assert _make_object(client, admin, part, label="A", properties={"dwg": "D-1234"})
    denied = client.post(
        f"/api/objects/{part}",
        json={"label": "B", "properties": {"dwg": "1234"}, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "모양" in denied.json()["error"]["message"]


def test_깨진_규칙은_정의가_잘못됐다고_말한다(client: TestClient, admin: Signed) -> None:
    """**「식이 잘못됐다」 와 「값이 안 맞는다」 는 고칠 곳이 다르다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="x", label="X", data_type="text", pattern="[unclosed"
    )
    denied = client.post(
        f"/api/objects/{part}",
        json={
            "label": "A",
            "properties": {"x": "아무거나"},
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "정의" in denied.json()["error"]["message"]


def test_뒤집힌_범위는_만들_때_막는다(client: TestClient, admin: Signed) -> None:
    """뒤집힌 범위는 아무 값도 안 받는데, 화면에는 「값이 틀렸다」 로만 뜬다."""
    part = _make_type(client, admin, label="부품")
    denied = client.post(
        f"/api/ontology/types/{part}/properties",
        json={
            "key": "w",
            "label": "폭",
            "data_type": "number",
            "min_value": 100,
            "max_value": 1,
        },
        headers=admin.headers,
    )
    assert denied.status_code == 409


def test_기본값은_안_넣었을_때만_들어간다(client: TestClient, admin: Signed) -> None:
    """**넣은 것을 덮으면 사람이 지운 값이 되살아나고**, 그 되살아남은 저장한
    사람 눈에 안 보인다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B"],
        default_value="A",
    )
    filled = _make_object(client, admin, part, label="A")
    assert filled["properties"]["grade"] == "A"

    given = _make_object(client, admin, part, label="B", properties={"grade": "B"})
    assert given["properties"]["grade"] == "B"

    # 명시적으로 지우면 기본값이 안 되살아난다.
    cleared = client.patch(
        f"/api/objects/{part}/{given['id']}",
        json={"properties": {"grade": None}},
        headers=admin.headers,
    ).json()
    assert "grade" not in cleared["properties"]


def test_유일해야_하는_속성은_두_번_안_들어간다(client: TestClient, admin: Signed) -> None:
    """**같은 것이 둘이 되면 둘 다 못 믿게 된다** — 어느 쪽이 맞는지 알 방법이 없다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="serial", label="시리얼", data_type="text", unique=True
    )
    _make_object(client, admin, part, label="A", properties={"serial": "S-1"})
    denied = client.post(
        f"/api/objects/{part}",
        json={
            "label": "B",
            "properties": {"serial": "S-1"},
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert denied.status_code == 409
    assert "둘 다 못 믿게" in denied.json()["error"]["message"]


def test_새_종류_셋을_받는다(client: TestClient, admin: Signed) -> None:
    """`datetime`(시각) · `url`(링크) · `text_long`(여러 줄)."""
    part = _make_type(client, admin, label="부품")
    for key, kind in (("at", "datetime"), ("link", "url"), ("memo", "text_long")):
        _make_property(client, admin, part, key=key, label=key, data_type=kind)

    ok = _make_object(
        client,
        admin,
        part,
        label="A",
        properties={
            "at": "2026-09-11T13:05",
            "link": "https://example.local/a",
            "memo": "여러\n줄",
        },
    )
    assert ok["properties"]["link"] == "https://example.local/a"

    for key, bad in (("at", "2026-09-11 오후"), ("link", "example.local")):
        denied = client.post(
            f"/api/objects/{part}",
            json={"label": "B", "properties": {key: bad}, "workspace_slug": admin.workspace},
            headers=admin.headers,
        )
        assert denied.status_code == 422, key


# --- 뷰 스펙의 경계 (3-b) ----------------------------------------------------


def test_모르는_키는_거절한다(client: TestClient, admin: Signed) -> None:
    """**조용히 무시하면 적어 둔 사람은 적용된 줄 안다** — 「스펙에는 있는데
    안 그려지는 필드」 가 쌓인다(ADR 0005 「뷰 스펙의 경계」)."""
    part = _make_type(client, admin, label="부품")
    denied = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"columns": ["label"], "group_by": "properties.x"}},
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "group_by" in denied.json()["error"]["message"]


def test_없는_속성을_가리키면_거절한다(client: TestClient, admin: Signed) -> None:
    """**가리키는 속성이 없으면 빈 열이 서고, 빈 열은 「값이 없다」 로 읽힌다.**"""
    part = _make_type(client, admin, label="부품")
    denied = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"columns": ["properties.없는것"]}},
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "없는것" in denied.json()["error"]["message"]


def test_속성을_지우면_뷰에서도_걷어낸다(client: TestClient, admin: Signed) -> None:
    """**안 걷어내면 그 뒤로 타입을 고칠 때마다 「없는 속성」 이라고 거절당하고**,
    사람은 자기가 방금 고친 것과 상관없는 그 오류를 이해할 수 없다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")
    client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "columns": ["label", "properties.grade"],
                "sort": {"field": "properties.grade"},
                "filters": ["properties.grade"],
            }
        },
        headers=admin.headers,
    )
    client.delete(f"/api/ontology/types/{part}/properties/grade", headers=admin.headers)

    got = next(
        row
        for row in client.get("/api/ontology/types", headers=admin.headers).json()
        if row["slug"] == part
    )
    assert got["list_view"]["columns"] == ["label"]
    assert "sort" not in got["list_view"]
    assert got["list_view"]["filters"] == []

    # 그다음 타입을 고쳐도 안 걸린다.
    again = client.patch(
        f"/api/ontology/types/{part}", json={"label": "부품2"}, headers=admin.headers
    )
    assert again.status_code == 200


def test_폼_묶음은_속성이_들고_있다(client: TestClient, admin: Signed) -> None:
    """뷰가 소속까지 정하면 두 벌이 되고, **갈린 두 벌은 한쪽만 고쳐진다.**"""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="w", label="폭", data_type="number", section="치수"
    )

    ok = client.patch(
        f"/api/ontology/types/{part}",
        json={"form_view": {"sections": [{"name": "치수", "columns": 2}]}},
        headers=admin.headers,
    )
    assert ok.status_code == 200, ok.text

    # 속한 속성이 없는 묶음은 **빈 칸으로 선다** — 비어 있는 제목은 「뭔가 안
    # 나온다」 로 읽힌다.
    denied = client.patch(
        f"/api/ontology/types/{part}",
        json={"form_view": {"sections": [{"name": "없는묶음"}]}},
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "속한 속성이 없습니다" in denied.json()["error"]["message"]


def test_묶음의_열_수와_접힘만_정한다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="w", label="폭", data_type="number", section="치수"
    )
    for bad, why in (
        ({"name": "치수", "properties": ["w"]}, "소속은 뷰가 안 정한다"),
        ({"name": "치수", "columns": 7}, "열 수는 1·2·3"),
    ):
        denied = client.patch(
            f"/api/ontology/types/{part}",
            json={"form_view": {"sections": [bad]}},
            headers=admin.headers,
        )
        assert denied.status_code == 422, why


# --- 가져오기·되돌리기 (3-c) -------------------------------------------------


def _import(
    client: TestClient, admin: Signed, body: dict[str, Any], *, dry_run: bool = True
) -> Any:
    return client.post(
        f"/api/ontology/import?dry_run={'true' if dry_run else 'false'}",
        json=body,
        headers=admin.headers,
    )


def test_기본은_미리보기다(client: TestClient, admin: Signed) -> None:
    """**기계가 부르는 자리**라 실수가 기계 속도로 반영된다. 적용은 의도를 적어야
    일어난다."""
    slug = _uniq("mach")
    plan = _import(client, admin, {"types": [{"slug": slug, "label": "기계"}]}).json()
    assert plan["applied"] is False
    assert [(c["kind"], c["action"]) for c in plan["changes"]] == [("type", "create")]

    # 안 만들어졌다.
    listed = client.get("/api/ontology/types", headers=admin.headers).json()
    assert all(row["slug"] != slug for row in listed)


def test_한_번에_통째로_적용한다(client: TestClient, admin: Signed) -> None:
    """한 칸씩이면 200번 왕복하고, **중간에 실패하면 반쯤 만들어진 온톨로지가 남는다.**"""
    group, part, vendor, rel = _uniq("g"), _uniq("part"), _uniq("vendor"), _uniq("made")
    body = {
        "groups": [{"slug": group, "label": "묶음"}],
        "types": [
            {"slug": vendor, "label": "공급사", "nav_group_slug": group},
            {
                "slug": part,
                "label": "부품",
                "nav_group_slug": group,
                "key_policy": "required",
                "properties": [
                    {
                        "key": "w",
                        "label": "폭",
                        "data_type": "number",
                        "min_value": 0,
                        "max_value": 100,
                        "section": "치수",
                    },
                    {
                        "key": "v",
                        "label": "공급사",
                        "data_type": "object_ref",
                        "ref_type_slug": vendor,
                    },
                ],
                "list_view": {"columns": ["key", "label", "properties.w"]},
                "form_view": {"sections": [{"name": "치수", "columns": 2}]},
            },
        ],
        "relation_types": [
            {
                "slug": rel,
                "label": "만듦",
                "inverse_label": "만들어짐",
                "src_type_slugs": [part],
                "dst_type_slugs": [vendor],
            },
        ],
    }
    applied = _import(client, admin, body, dry_run=False)
    assert applied.status_code == 200, applied.text
    got = applied.json()
    assert got["applied"] is True
    assert got["errors"] == []
    assert got["snapshot_id"]

    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    made = next(t for t in schema["types"] if t["slug"] == part)
    assert [p["key"] for p in made["properties"]] == ["w", "v"], "적은 차례대로"
    assert made["form_view"]["sections"][0]["name"] == "치수"
    assert any(r["slug"] == rel for r in schema["relation_types"])


def test_오류가_하나라도_있으면_아무것도_안_바꾼다(client: TestClient, admin: Signed) -> None:
    """**반쯤 만들어진 온톨로지가 남지 않는다** — 그것이 한 트랜잭션인 이유다."""
    good, bad = _uniq("good"), _uniq("bad")
    body = {
        "types": [
            {"slug": good, "label": "정상"},
            {"slug": bad, "label": "틀림", "kind_class": "없는분류"},
        ]
    }
    got = _import(client, admin, body, dry_run=False).json()
    assert got["applied"] is False
    assert got["errors"]

    listed = client.get("/api/ontology/types", headers=admin.headers).json()
    assert all(row["slug"] != good for row in listed), "앞의 것도 안 만들어져야 한다"


def test_모르는_항목은_거절한다(client: TestClient, admin: Signed) -> None:
    denied = _import(client, admin, {"타입들": []})
    assert denied.status_code == 409
    assert "모르는 항목" in denied.json()["error"]["message"]


def test_위험한_것을_미리_말한다(client: TestClient, admin: Signed) -> None:
    """**적용은 되지만 조용히 무언가를 잃는 것.** 사람이 읽고 판단할 자리다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B", "C"],
    )
    _make_object(client, admin, part, label="볼트", properties={"grade": "C"})

    plan = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": part,
                    "label": "부품",
                    "properties": [
                        {
                            "key": "grade",
                            "label": "등급",
                            "data_type": "enum",
                            "enum_options": ["A", "B"],
                            "required": True,
                        }
                    ],
                }
            ]
        },
    ).json()
    joined = " ".join(plan["warnings"])
    assert "C" in joined and "거절" in joined, joined
    assert "필수로 바꿉니다" in joined


def test_종류_변경은_계획에서_막는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="w", label="폭", data_type="number")
    plan = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": part,
                    "label": "부품",
                    "properties": [{"key": "w", "label": "폭", "data_type": "text"}],
                }
            ]
        },
    ).json()
    assert any("종류는 바꿀 수 없습니다" in one for one in plan["errors"])


def test_안_바뀌는_것은_unchanged_로_적는다(client: TestClient, admin: Signed) -> None:
    """**안 바뀌는 것을 바뀐다고 적으면 사람은 그 목록을 안 읽게 되고, 그때 진짜
    하나가 묻힌다.**"""
    part = _make_type(client, admin, label="부품", sort_order=7)
    plan = _import(
        client, admin, {"types": [{"slug": part, "label": "부품", "sort_order": 7}]}
    ).json()
    assert [c["action"] for c in plan["changes"]] == ["unchanged"]


def test_되돌리면_그때의_정의로_돌아온다(client: TestClient, admin: Signed) -> None:
    """**감사 로그는 누가 뭘 했는지는 알려 주지만 되돌려 주지 않는다.**"""
    part = _uniq("part")
    _import(client, admin, {"types": [{"slug": part, "label": "처음"}]}, dry_run=False)
    second = _import(
        client, admin, {"types": [{"slug": part, "label": "고친 뒤"}]}, dry_run=False
    ).json()
    snapshot = second["snapshot_id"]

    now = next(
        t
        for t in client.get("/api/ontology/types", headers=admin.headers).json()
        if t["slug"] == part
    )
    assert now["label"] == "고친 뒤"

    restored = client.post(
        f"/api/ontology/snapshots/{snapshot}/restore", headers=admin.headers
    )
    assert restored.status_code == 200, restored.text
    back = next(
        t
        for t in client.get("/api/ontology/types", headers=admin.headers).json()
        if t["slug"] == part
    )
    assert back["label"] == "처음"


def test_되돌려도_그_뒤에_만든_것은_안_지운다(client: TestClient, admin: Signed) -> None:
    """**지우면 그 사이에 쌓인 객체가 통째로 갈 곳을 잃는다** — 되돌리기가
    그것까지 하면 되돌리기 자체가 위험해진다."""
    first, later = _uniq("first"), _uniq("later")
    made = _import(
        client, admin, {"types": [{"slug": first, "label": "처음"}]}, dry_run=False
    ).json()
    _import(client, admin, {"types": [{"slug": later, "label": "나중"}]}, dry_run=False)

    client.post(
        f"/api/ontology/snapshots/{made['snapshot_id']}/restore", headers=admin.headers
    )
    slugs = {
        t["slug"] for t in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert later in slugs


def test_스냅샷_목록이_남는다(client: TestClient, admin: Signed) -> None:
    _import(client, admin, {"types": [{"slug": _uniq("x"), "label": "x"}]}, dry_run=False)
    rows = client.get("/api/ontology/snapshots", headers=admin.headers).json()
    assert rows
    assert rows[0]["reason"] == "가져오기"
    assert rows[0]["actor_label"]


def test_가져오기는_시스템_관리자만(client: TestClient, admin: Signed, member: Signed) -> None:
    denied = client.post("/api/ontology/import", json={"types": []}, headers=member.headers)
    assert denied.status_code == 403


def test_적은_차례가_그대로_순서가_된다(client: TestClient, admin: Signed) -> None:
    """**안 그러면 전부 0 이 되어 이름순으로 서고**, 스키마에 적어 둔 순서(대개
    사람이 읽는 차례)가 조용히 뒤집힌다."""
    part = _uniq("part")
    _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": part,
                    "label": "부품",
                    "properties": [
                        {"key": "zulu", "label": "마지막에 읽을 것", "data_type": "text"},
                        {"key": "alpha", "label": "먼저 읽을 것", "data_type": "text"},
                    ],
                }
            ]
        },
        dry_run=False,
    )

    schema = client.get("/api/ontology/schema", headers=admin.headers).json()
    made = next(t for t in schema["types"] if t["slug"] == part)
    assert [p["key"] for p in made["properties"]] == ["zulu", "alpha"]


def test_안_바뀐_묶음을_바뀐다고_안_적는다(client: TestClient, admin: Signed) -> None:
    """행에는 `nav_group_id` 가 있고 스키마에는 slug 가 온다 — 그것을 안 맞추면
    **늘 바뀐다고 나오고**, 그러면 사람은 계획을 안 읽게 된다."""
    group = _uniq("g")
    part = _uniq("part")
    client.post(
        "/api/ontology/groups", json={"slug": group, "label": "묶음"}, headers=admin.headers
    )
    _import(
        client,
        admin,
        {"types": [{"slug": part, "label": "부품", "nav_group_slug": group}]},
        dry_run=False,
    )
    plan = _import(
        client, admin, {"types": [{"slug": part, "label": "부품", "nav_group_slug": group}]}
    ).json()
    assert [c["action"] for c in plan["changes"]] == ["unchanged"], plan["changes"]


# --- 기계 자격 (3-d) ---------------------------------------------------------


def test_기계_자격은_범위가_있어야_고친다(client: TestClient, admin: Signed) -> None:
    """**안 열면 PAT 로는 못 고친다** — 기본이 「막힘」 이고 그것이 맞는 기본값이다.

    그리고 정의와 데이터를 가른다: 한 범위로 묶으면 「객체만 넣게」 하려던 토큰이
    **타입까지 지울 수 있다.**
    """
    part = _make_type(client, admin, label="부품")

    def pat(*scopes: str) -> dict[str, str]:
        made = client.post(
            "/api/auth/tokens",
            json={"name": _uniq("bot"), "scopes": list(scopes)},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text
        return {"Authorization": f"Bearer {made.json()['token']}"}

    read_only = pat("read")
    # 읽기는 된다.
    assert client.get("/api/ontology/schema", headers=read_only).status_code == 200
    # 쓰기는 막힌다 — 정의도 데이터도.
    assert (
        client.post(
            f"/api/objects/{part}", json={"label": "볼트"}, headers=read_only
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/ontology/types", json={"slug": _uniq("x"), "label": "x"}, headers=read_only
        ).status_code
        == 403
    )

    data_only = pat("read", "objects:write")
    assert (
        client.post(
            f"/api/objects/{part}", json={"label": "볼트"}, headers=data_only
        ).status_code
        == 201
    )
    # **데이터 범위로는 정의를 못 고친다.**
    assert (
        client.post(
            "/api/ontology/types", json={"slug": _uniq("x"), "label": "x"}, headers=data_only
        ).status_code
        == 403
    )

    full = pat("read", "ontology:write")
    assert (
        client.post(
            "/api/ontology/types", json={"slug": _uniq("ok"), "label": "됨"}, headers=full
        ).status_code
        == 201
    )
