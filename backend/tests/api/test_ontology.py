"""메타모델과 인스턴스 — **진짜 앱을 부른다.**

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


# --- 인스턴스 ---------------------------------------------------------------


def test_인스턴스를_만들고_속성이_검증된다(client: TestClient, admin: Signed) -> None:
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


def test_system_타입에는_인스턴스를_안_만든다(client: TestClient, admin: Signed) -> None:
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


def test_인스턴스가_있는_타입은_못_지운다(client: TestClient, admin: Signed) -> None:
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
