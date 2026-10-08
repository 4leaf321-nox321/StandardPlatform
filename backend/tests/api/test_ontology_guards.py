"""온톨로지 정의의 **문턱** — 한 길로는 막히는데 다른 길로는 지나가던 것들(2026-10-08 점검).

지키는 것: 정의 가져오기의 미리 보기가 적용과 같은 것을 보나(칸의 길이 · 종류, 화면 모양,
재귀 관계의 순환), 허브가 관리하는 정의가 옆길(관계 속성 · 코드표 승격 · 고를 값 이름 바꾸기)로
안 바뀌나, 속성을 지우거나 묶음을 옮긴 뒤 타입 수정이 안 막히나, 지우기가 원 표의 선을 세나,
화면이 보낸 틀린 모양이 500 이 아니라 422 인가, 객체 API 의 고정 경로와 겹치는 slug 를 막나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.objects import routes as objects_routes
from app.modules.objects.models import ObjectLink
from app.modules.ontology import inference
from app.modules.ontology.models import ObjectType, RelationType
from app.modules.ontology.services import RESERVED_SLUGS
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type, _uniq


def _import(
    client: TestClient, admin: Signed, schema: dict[str, Any], *, dry_run: bool = True
) -> Any:
    return client.post(
        "/api/ontology/import",
        params={"dry_run": dry_run},
        json=schema,
        headers=admin.headers,
    )


def _type_of(client: TestClient, who: Signed, slug: str) -> dict[str, Any]:
    rows = client.get("/api/ontology/types", headers=who.headers).json()
    return dict(next(one for one in rows if one["slug"] == slug))


def _hub(db: Session, model: type[Any], slug: str) -> None:
    """허브가 관리하는 정의로 — 받기(`source`)를 흉내 낸다."""
    db.execute(update(model).where(model.slug == slug).values(managed_by="hub"))
    db.commit()


# --- 정의 가져오기: 미리 보기 = 적용 ----------------------------------------------------


def test_허브_관계의_속성은_파일로_못_고친다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """**관계 종류의 속성은 타입에서 찾아서** 늘 「주인 없음」 이었다 — source 없는 가져오기 ·
    묶음 · 스냅샷 복원이 허브 관계에 속성을 만들고 고쳤다."""
    relation = _uniq("cause")
    made = client.post(
        "/api/ontology/relation-types",
        json={"slug": relation, "label": "원인"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    _hub(db, RelationType, relation)

    planned = _import(
        client,
        admin,
        {
            "relation_types": [
                {
                    "slug": relation,
                    "properties": [
                        {"key": "weight", "label": "가중치", "data_type": "number"}
                    ],
                }
            ]
        },
    )
    assert planned.status_code == 200, planned.text
    errors = planned.json()["errors"]
    assert any("관리하는 정의" in one for one in errors), errors


def test_재귀_관계는_파일로도_순환을_막는다(client: TestClient, admin: Signed) -> None:
    """계획은 acyclic 을 transitive 로 쳐 주고 적용은 모델 기본값(거짓)을 썼다 — API 가 막는
    상태(ONTOLOGY-42)가 파일로 들어갔다."""
    fresh = _uniq("part_of")
    done = _import(
        client,
        admin,
        {"relation_types": [{"slug": fresh, "label": "부분", "transitive": True}]},
        dry_run=False,
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    kinds = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    made = next(one for one in kinds if one["slug"] == fresh)
    assert made["transitive"] is True and made["acyclic"] is True, made

    # 있는 관계에 transitive 만 켜면 — 지금 acyclic 이 거짓이니 막는다.
    loose = _uniq("near")
    assert (
        client.post(
            "/api/ontology/relation-types",
            json={"slug": loose, "label": "가까움"},
            headers=admin.headers,
        ).status_code
        == 201
    )
    planned = _import(
        client, admin, {"relation_types": [{"slug": loose, "transitive": True}]}
    ).json()
    assert any("순환을 막아야" in one for one in planned["errors"]), planned


def test_미리_보기가_화면_모양을_적용과_같이_본다(client: TestClient, admin: Signed) -> None:
    """**미리 보기는 통과, 적용은 실패** — 계획은 화면 모양을 안 봤다. 모양이 틀린 스펙은
    적용에서 500 이었다."""
    slug = _uniq("part")
    broken = _import(
        client,
        admin,
        {"types": [{"slug": slug, "label": "부품", "list_view": {"sort": 1}}]},
    )
    assert broken.status_code == 200, broken.text
    assert broken.json()["errors"], broken.json()

    applied = _import(
        client,
        admin,
        {"types": [{"slug": slug, "label": "부품", "list_view": {"sort": 1}}]},
        dry_run=False,
    )
    assert applied.status_code == 200 and applied.json()["applied"] is False, applied.text

    # 없는 묶음을 가리키는 폼 — 예전에는 미리 보기 오류 0, 적용 422.
    lonely = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": _uniq("part"),
                    "label": "부품",
                    "form_view": {"sections": [{"name": "없는 묶음"}]},
                }
            ]
        },
    ).json()
    assert any("없는 묶음" in one for one in lonely["errors"]), lonely


def test_미리_보기가_칸의_길이를_본다(client: TestClient, admin: Signed) -> None:
    """64자를 넘는 이름은 미리 보기를 지나 적용에서 `varchar(64)` 로 500 이었다."""
    planned = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": _uniq("part"),
                    "label": "부품",
                    "properties": [{"key": "long", "label": "가" * 70, "data_type": "text"}],
                }
            ]
        },
    )
    assert planned.status_code == 200, planned.text
    assert any("64자" in one for one in planned.json()["errors"]), planned.json()


def test_모양이_틀린_정의는_422(client: TestClient, admin: Signed) -> None:
    """`{"types": ["x"]}` · 숫자 slug · 목록 자리의 글자는 계획을 세울 수 없다 — 500 이 아니라
    무엇이 틀렸는지 말하는 422."""
    for schema in (
        {"types": ["x"]},
        {"types": [{"slug": 5, "label": "숫자"}]},
        {"types": [{"slug": _uniq("part"), "label": "부품", "interface_slugs": "abc"}]},
        {"types": [{"slug": _uniq("part"), "label": 3}]},
        {"relation_types": [{"slug": _uniq("rel"), "properties": "x"}]},
    ):
        got = _import(client, admin, schema)
        assert got.status_code == 422, (schema, got.text)


def test_표에서_추론한_긴_머리글은_이름_칸에_맞게_자른다() -> None:
    header = "아주 긴 머리글 " * 10
    inferred = inference.infer([{header: "값", "이름": "가"}])
    column = next(one for one in inferred.columns if one.header == header)
    assert len(column.label) <= 64 and column.label, column


# --- 코드표 승격 ---------------------------------------------------------------------


def _enum_part(client: TestClient, admin: Signed, **prop: Any) -> str:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["상", "하"],
        **prop,
    )
    return part


def test_허브가_관리하는_코드표에는_승격으로_객체를_못_만든다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """그 코드표에 객체를 만들면 **잠금을 돌아간다** — 이 설치에서 고치지도 지우지도 못한다."""
    part = _enum_part(client, admin)
    book = _make_type(client, admin, label="등급표", kind_class="reference")
    _make_object(client, admin, book, label="상")
    _hub(db, ObjectType, book)

    plan = client.post(
        f"/api/ontology/types/{part}/properties/grade/promote",
        json={"target_type_slug": book, "apply": True},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is False, plan
    assert any("관리하는 코드표" in one and "하" in one for one in plan["errors"]), plan


def test_승격의_새_slug_도_인터페이스_고정_경로와_안_겹친다(
    client: TestClient, admin: Signed
) -> None:
    part = _enum_part(client, admin)
    iface = _uniq("grade_if")
    assert (
        client.post(
            "/api/ontology/interfaces",
            json={"slug": iface, "label": "등급류"},
            headers=admin.headers,
        ).status_code
        == 201
    )
    for slug in (iface, "home"):
        got = client.post(
            f"/api/ontology/types/{part}/properties/grade/promote",
            json={"new_slug": slug, "new_label": "등급", "apply": False},
            headers=admin.headers,
        )
        assert got.status_code == 409, (slug, got.text)


def test_여러_값_칸의_기본값도_승격된다(client: TestClient, admin: Signed) -> None:
    """기본값(목록)을 안 옮겨 옛 이름이 참조 칸의 기본값으로 남았다 — 그 칸을 비운 객체
    만들기가 늘 422 였다."""
    part = _enum_part(client, admin, multi=True, default_value=["상"])
    done = client.post(
        f"/api/ontology/types/{part}/properties/grade/promote",
        json={"new_slug": _uniq("grade"), "new_label": "등급", "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    ids = {one["value"]: one["object_id"] for one in done["options"]}

    made = _make_object(client, admin, part, label="기본값으로")
    assert made["properties"]["grade"] == [ids["상"]], made


# --- 속성 삭제 · 묶음 옮기기 뒤의 타입 수정 ---------------------------------------------


def _sectioned(client: TestClient, admin: Signed) -> str:
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="mass", label="무게", data_type="number", section="기본"
    )
    _make_property(
        client, admin, part, key="note", label="메모", data_type="text", section="기타"
    )
    views = {"sections": [{"name": "기본"}, {"name": "기타"}]}
    set_up = client.patch(
        f"/api/ontology/types/{part}",
        json={"form_view": views, "detail_view": views},
        headers=admin.headers,
    )
    assert set_up.status_code == 200, set_up.text
    return part


def _rename(client: TestClient, admin: Signed, part: str) -> Any:
    """화면의 타입 수정 창처럼 — 이름만 고쳐도 폼 · 상세를 함께 보낸다."""
    row = _type_of(client, admin, part)
    return client.patch(
        f"/api/ontology/types/{part}",
        json={
            "label": "부품 고침",
            "form_view": row["form_view"],
            "detail_view": row["detail_view"],
        },
        headers=admin.headers,
    )


def test_속성을_지우면_빈_묶음도_걷는다(client: TestClient, admin: Signed) -> None:
    """마지막 속성을 지우면 묶음 이름이 뷰에 남아 **이름만 고쳐도** ONTOLOGY-60 이었다."""
    part = _sectioned(client, admin)
    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "property", "slug": part, "key": "note"},
        headers=admin.headers,
    ).json()
    assert any("기타" in one for one in plan["removes"]), plan

    gone = client.delete(f"/api/ontology/types/{part}/properties/note", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    row = _type_of(client, admin, part)
    assert row["form_view"] == {"sections": [{"name": "기본"}]}, row
    renamed = _rename(client, admin, part)
    assert renamed.status_code == 200, renamed.text


def test_속성의_묶음을_옮기면_빈_묶음을_걷는다(client: TestClient, admin: Signed) -> None:
    part = _sectioned(client, admin)
    moved = client.patch(
        f"/api/ontology/types/{part}/properties/note",
        json={"key": "note", "label": "메모", "data_type": "text", "section": "기본"},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    row = _type_of(client, admin, part)
    assert row["detail_view"] == {"sections": [{"name": "기본"}]}, row
    assert _rename(client, admin, part).status_code == 200


def test_저장된_빈_묶음이_있어도_이름은_고친다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """이 고침 전에 남은 빈 묶음 — 서버가 들고 있던 그대로 돌아오면 걷고 받는다."""
    part = _sectioned(client, admin)
    stale = {"sections": [{"name": "기본"}, {"name": "옛 묶음"}]}
    db.execute(
        update(ObjectType)
        .where(ObjectType.slug == part)
        .values(form_view=stale, detail_view=stale)
    )
    db.commit()
    assert _rename(client, admin, part).status_code == 200
    # 사람이 새로 적은 빈 묶음은 여전히 거절한다.
    wrong = client.patch(
        f"/api/ontology/types/{part}",
        json={"form_view": {"sections": [{"name": "새 묶음"}]}},
        headers=admin.headers,
    )
    assert wrong.status_code == 422, wrong.text


# --- 지우기가 원 표의 선을 센다 -------------------------------------------------------


def test_원_표와_잇는_선이_있는_관계_종류는_못_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """계정 → 과제 같은 관계는 선이 전부 `object_links` 에 있다 — 세지 않아 지워졌고, 그 선은
    이름 없이 남았다."""
    task = _make_type(client, admin, label="과제")
    relation = _uniq("joins")
    assert (
        client.post(
            "/api/ontology/relation-types",
            json={"slug": relation, "label": "참여", "dst_type_slugs": [task]},
            headers=admin.headers,
        ).status_code
        == 201
    )
    made = _make_object(client, admin, task, label="과제 1")
    db.add(
        ObjectLink(
            src_type="user",
            src_id=uuid.uuid4(),
            dst_type=task,
            dst_id=uuid.UUID(made["id"]),
            relation=relation,
        )
    )
    db.commit()

    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "relation_type", "slug": relation},
        headers=admin.headers,
    ).json()
    assert plan["allowed"] is False, plan
    denied = client.delete(f"/api/ontology/relation-types/{relation}", headers=admin.headers)
    assert denied.status_code == 409, denied.text


def test_선이_걸린_원_표_타입은_못_지운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    people = _make_type(client, admin, label="사람", kind_class="system", system_source="user")
    db.add(
        ObjectLink(
            src_type=people,
            src_id=uuid.uuid4(),
            dst_type=_uniq("task"),
            dst_id=uuid.uuid4(),
            relation=_uniq("joins"),
        )
    )
    db.commit()
    denied = client.delete(f"/api/ontology/types/{people}", headers=admin.headers)
    assert denied.status_code == 409, denied.text
    assert "ONTOLOGY-0093" in denied.json()["error"]["code"], denied.text


# --- 연동 토큰 이름은 시스템 관리자에게만 ------------------------------------------------


def test_속성_사용처는_일반_사용자에게_토큰_이름을_안_보인다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """`core-status` 가 시스템 관리자 전용인 바로 그 까닭 — 열린 타입이면 모든 사용자가 남의
    read · core 토큰 이름과 마지막 사용일을 읽었다."""
    part = _make_type(client, admin, label="부품", core=True)
    _make_property(client, admin, part, key="mass", label="무게", data_type="number")
    name = _uniq("partner-sync")
    token = client.post(
        "/api/auth/tokens", json={"name": name, "scopes": ["read"]}, headers=admin.headers
    )
    assert token.status_code == 201, token.text

    seen = client.get(
        f"/api/ontology/types/{part}/properties/mass/usage", headers=member.headers
    ).json()
    assert seen["core_open"] is True and seen["core_consumers"] == [], seen
    full = client.get(
        f"/api/ontology/types/{part}/properties/mass/usage", headers=admin.headers
    ).json()
    assert any(name in one for one in full["core_consumers"]), full


# --- 화면이 보낸 틀린 모양은 422 -----------------------------------------------------


def test_틀린_화면_모양은_422(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    for body in (
        {"list_view": {"sort": 1}},
        {"list_view": {"sort": [{"field": "label"}]}},
        {"list_view": {"tree": True}},
        {"list_view": {"columns": "label"}},
        {"form_view": {"sections": 3}},
        {"detail_view": {"sections": "기본"}},
    ):
        got = client.patch(f"/api/ontology/types/{part}", json=body, headers=admin.headers)
        assert got.status_code == 422, (body, got.text)
    iface = _uniq("iface")
    assert (
        client.post(
            "/api/ontology/interfaces",
            json={"slug": iface, "label": "공통"},
            headers=admin.headers,
        ).status_code
        == 201
    )
    got = client.patch(
        f"/api/ontology/interfaces/{iface}",
        json={"list_view": {"sort": 1}},
        headers=admin.headers,
    )
    assert got.status_code == 422, got.text


# --- 인터페이스의 고를 값 이름 바꾸기 ----------------------------------------------------


def test_허브_구현_타입이_있으면_고를_값_이름을_안_바꾼다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    iface = _uniq("graded")
    assert (
        client.post(
            "/api/ontology/interfaces",
            json={"slug": iface, "label": "등급"},
            headers=admin.headers,
        ).status_code
        == 201
    )
    added = client.post(
        f"/api/ontology/interfaces/{iface}/properties",
        json={
            "key": "grade",
            "label": "등급",
            "data_type": "enum",
            "enum_options": ["상", "하"],
        },
        headers=admin.headers,
    )
    assert added.status_code == 201, added.text
    part = _make_type(client, admin, label="부품", interface_slugs=[iface])
    made = _make_object(client, admin, part, label="볼트", properties={"grade": "상"})
    _hub(db, ObjectType, part)

    done = client.post(
        f"/api/ontology/interfaces/{iface}/properties/grade/rename-option",
        json={"from_value": "상", "to_value": "높음", "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is False, done
    assert any("관리하는 타입" in one for one in done["errors"]), done
    profile = client.get(f"/api/objects/{part}/{made['id']}", headers=admin.headers).json()
    assert profile["object"]["properties"]["grade"] == "상"


# --- 예약 slug ---------------------------------------------------------------------


def test_객체_API_의_고정_경로와_겹치는_slug_는_못_만든다(
    client: TestClient, admin: Signed
) -> None:
    """`/api/objects/home` 이 `/{type_slug}` 보다 앞에 있어 slug 가 home 인 타입은 목록이
    안 열렸다."""
    for slug in sorted(RESERVED_SLUGS):
        typed = client.post(
            "/api/ontology/types", json={"slug": slug, "label": "겹침"}, headers=admin.headers
        )
        assert typed.status_code == 409, typed.text
        assert "ONTOLOGY-0091" in typed.json()["error"]["code"]
        iface = client.post(
            "/api/ontology/interfaces",
            json={"slug": slug, "label": "겹침"},
            headers=admin.headers,
        )
        assert iface.status_code == 409, iface.text
    planned = _import(client, admin, {"types": [{"slug": "watching", "label": "겹침"}]}).json()
    assert any("고정 주소" in one for one in planned["errors"]), planned


def test_예약_slug_는_객체_라우터의_고정_첫_마디와_같다() -> None:
    """객체 라우터에 고정 경로를 더하고 여기를 잊으면 그 이름의 타입이 다시 안 열린다."""
    fixed: set[str] = set()
    for route in objects_routes.router.routes:
        path = str(getattr(route, "path", ""))
        first = path.removeprefix("/objects").strip("/").split("/", 1)[0]
        if first and not first.startswith("{"):
            fixed.add(first)
    assert fixed <= RESERVED_SLUGS, sorted(fixed - RESERVED_SLUGS)


# --- 그 밖의 문턱 ------------------------------------------------------------------


def test_원_표_타입은_만들_때도_공개하지_않는다(client: TestClient, admin: Signed) -> None:
    """고치기(ONTOLOGY-45)는 막는데 만들기 · 가져오기는 `core` + `system` 을 받았다."""
    made = client.post(
        "/api/ontology/types",
        json={
            "slug": _uniq("people"),
            "label": "사람",
            "kind_class": "system",
            "system_source": "user",
            "core": True,
        },
        headers=admin.headers,
    )
    assert made.status_code == 409, made.text
    planned = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": _uniq("people"),
                    "label": "사람",
                    "kind_class": "system",
                    "system_source": "user",
                    "core": True,
                }
            ]
        },
    ).json()
    assert any("공개" in one for one in planned["errors"]), planned


def test_틀린_기본값은_속성을_저장할_때_막는다(client: TestClient, admin: Signed) -> None:
    """틀린 기본값은 만들 때마다 채워져, 그 칸을 비운 객체 만들기가 **전부 422** 였다 — 고칠
    자리(속성 정의)는 그 오류에 안 나온다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="grade", label="등급", data_type="enum", enum_options=["상"]
    )
    wrong = client.patch(
        f"/api/ontology/types/{part}/properties/grade",
        json={
            "key": "grade",
            "label": "등급",
            "data_type": "enum",
            "enum_options": ["상"],
            "default_value": "없는 값",
        },
        headers=admin.headers,
    )
    assert wrong.status_code == 422, wrong.text
    assert "ONTOLOGY-0090" in wrong.json()["error"]["code"]

    # 여러 값 칸에 글자 하나를 보내면(화면의 기본값 칸) 그 하나를 담은 목록이다.
    tags = _make_property(
        client,
        admin,
        part,
        key="tags",
        label="꼬리표",
        data_type="enum",
        enum_options=["가", "나"],
        multi=True,
        default_value="가",
    )
    assert tags["default_value"] == ["가"], tags
    assert _make_object(client, admin, part, label="볼트")["properties"]["tags"] == ["가"]


def test_속성을_지우기_전에_딸린_뷰와_지표를_말한다(client: TestClient, admin: Signed) -> None:
    """삭제 계획이 저장된 뷰 · 지표를 말하지 않아, 지운 뒤 지표의 밤 계산이 실패하고서야
    알았다. 종류 변경의 계획도 지표를 안 봤다."""
    part = _make_type(client, admin, label="판매")
    _make_property(client, admin, part, key="units", label="수량", data_type="number")
    metric = client.post(
        "/api/metrics",
        params={"recompute": False},
        json={
            "slug": _uniq("m"),
            "label": "판매 수량",
            "source_type_slug": part,
            "spec": {"measure": "sum", "measure_field": "properties.units"},
        },
        headers=admin.headers,
    )
    assert metric.status_code == 201, metric.text
    view = client.post(
        f"/api/objects/{part}/views",
        json={
            "name": "많이 판 것",
            "query": {"conditions": [{"field": "units", "op": "gte", "value": "10"}]},
        },
        headers=admin.headers,
    )
    assert view.status_code == 201, view.text

    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "property", "slug": part, "key": "units"},
        headers=admin.headers,
    ).json()
    assert any("판매 수량" in one for one in plan["warnings"]), plan["warnings"]
    assert any("많이 판 것" in one for one in plan["warnings"]), plan["warnings"]

    retyped = client.post(
        f"/api/ontology/types/{part}/properties/units/retype",
        json={"data_type": "text", "apply": False},
        headers=admin.headers,
    ).json()
    assert any("판매 수량" in one for one in retyped["warnings"]), retyped["warnings"]
