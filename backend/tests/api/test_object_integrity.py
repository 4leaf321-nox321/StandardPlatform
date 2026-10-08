"""객체의 정합 — **같은 것이 둘이 되지 않고, 틀린 입력은 500 이 아니라 이유로 돌아온다.**

유일성 검사는 줄마다 DB 만 봤다 — 한 묶음(여럿 고치기 · 파일 한 장 · 부서 옮기기) 안에서
겹치는 것은 아직 DB 에 없어서 못 봤다. 그리고 몇 가지 입력(긴 식별자 · 접두어 없는 칸 이름 ·
숫자 칸의 옛 글자)은 검사 없이 DB 까지 가서 500 이 됐다(2026-10-08 점검). 하나씩 붙잡는다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


def _other(db: Session, name: str = "다른 부서") -> Workspace:
    row = Workspace(slug=f"o-{uuid.uuid4().hex[:8]}", name=name)
    db.add(row)
    db.commit()
    return row


def _bulk_edit(client: TestClient, who: Signed, slug: str, **body: Any) -> dict[str, Any]:
    got = client.post(f"/api/objects/{slug}/bulk-edit", json=body, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _rows(
    client: TestClient, who: Signed, slug: str, rows: list[dict[str, Any]], **kw: Any
) -> dict[str, Any]:
    got = client.post(
        f"/api/objects/{slug}/import-rows",
        json={"rows": rows, "workspace_slug": who.workspace, **kw},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _labels(client: TestClient, who: Signed, slug: str, **params: Any) -> list[str]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return [one["label"] for one in got.json()["items"]]


# --- 유일성 — 한 묶음 안에서 ---------------------------------------------------------


def test_여럿_고치기는_유일_속성에_같은_값을_둘에_넣지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """줄마다 DB 만 봐서, 유일 속성 하나를 열 건에 같은 값으로 넣으면 열 건 다 바뀌었다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="serial", label="시리얼", data_type="text", unique=True
    )
    a = _make_object(client, admin, part, label="A")["id"]
    b = _make_object(client, admin, part, label="B")["id"]

    done = _bulk_edit(
        client, admin, part, ids=[a, b], field="properties.serial", value="S-1", apply=True
    )
    assert done["applied"] is False
    assert done["counts"]["error"] == 2
    assert all("이 묶음의 2건" in one["message"] for one in done["rows"])
    # 하나만 고르면 된다.
    one = _bulk_edit(
        client, admin, part, ids=[a], field="properties.serial", value="S-1", apply=True
    )
    assert one["applied"] is True


def test_범위가_부서면_다른_부서의_두_건은_같은_값이어도_된다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    other = _other(db)
    part = _make_type(client, admin, label="부품", key_scope="workspace")
    _make_property(
        client, admin, part, key="serial", label="시리얼", data_type="text", unique=True
    )
    a = _make_object(client, admin, part, label="A")["id"]
    b = _make_object(client, admin, part, label="B", workspace_slug=other.slug)["id"]
    done = _bulk_edit(
        client, admin, part, ids=[a, b], field="properties.serial", value="S-1", apply=True
    )
    assert done["applied"] is True, done


def test_부서를_옮기면_옮겨_갈_부서에서_유일성을_다시_본다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """범위가 부서인 타입(`key_scope="workspace"`) — 두 부서에서 각자 하나이던 「K-1」 이 한
    부서에 둘이 됐다."""
    first, second = _other(db), _other(db)
    part = _make_type(
        client, admin, label="부품", key_policy="optional", key_scope="workspace"
    )
    _make_property(
        client, admin, part, key="serial", label="시리얼", data_type="text", unique=True
    )
    _make_object(client, admin, part, label="여기 것", key="K-1", properties={"serial": "S-9"})
    same_key = _make_object(
        client, admin, part, label="저기 것", key="K-1", workspace_slug=first.slug
    )["id"]
    same_serial = _make_object(
        client,
        admin,
        part,
        label="저기 다른 것",
        key="K-2",
        workspace_slug=first.slug,
        properties={"serial": "S-9"},
    )["id"]
    # 서로 다른 두 부서에서 같은 식별자를 쓰던 둘을 **함께** 옮겨도 겹친다.
    pair = [
        _make_object(client, admin, part, label="갑", key="K-3", workspace_slug=first.slug)[
            "id"
        ],
        _make_object(client, admin, part, label="을", key="K-3", workspace_slug=second.slug)[
            "id"
        ],
    ]

    done = _bulk_edit(
        client,
        admin,
        part,
        ids=[same_key, same_serial, *pair],
        field="workspace",
        value=admin.workspace,
        apply=True,
    )
    assert done["applied"] is False, done
    by_id = {one["id"]: one for one in done["rows"]}
    assert "같은 식별자" in by_id[same_key]["message"]
    assert "시리얼" in by_id[same_serial]["message"]
    assert all("이 묶음의 2건" in by_id[one]["message"] for one in pair)


def test_부서_통폐합은_겹치는_식별자가_있으면_거절한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """원 SQL 로 한 번에 옮기므로 줄마다 볼 자리가 없다 — 겹치면 통째로 거절하고 아무것도 안
    옮긴다."""
    source = _other(db)
    part = _make_type(
        client, admin, label="부품", key_policy="optional", key_scope="workspace"
    )
    _make_object(client, admin, part, label="여기 것", key="K-1")
    moving = _make_object(
        client, admin, part, label="저기 것", key="K-1", workspace_slug=source.slug
    )["id"]

    denied = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": admin.workspace, "kinds": ["objects"]},
        headers=admin.headers,
    )
    assert denied.status_code == 409, denied.text
    assert "K-1" in denied.json()["error"]["message"]
    db.expire_all()
    stayed = db.get(ObjectInstance, uuid.UUID(moving))
    assert stayed is not None and stayed.owner_workspace_id == source.id


def test_파일_한_장_안에서_유일_값이_겹치면_뒷줄을_거절한다(
    client: TestClient, admin: Signed
) -> None:
    """DB 에는 아직 없는 값이라 미리 읽은 것에도 없었다 — 새로 만드는 두 줄이 둘 다
    만들어졌다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client, admin, part, key="serial", label="시리얼", data_type="text", unique=True
    )
    plan = _rows(
        client,
        admin,
        part,
        [
            {"label": "A", "serial": "S-1"},
            {"label": "B", "serial": "S-1"},
            {"label": "C", "serial": "S-2"},
        ],
    )
    assert [one["action"] for one in plan["rows"]] == ["create", "error", "create"]
    assert "1행" in plan["rows"][1]["message"]


def test_파일_한_장_안에서_같은_새_식별자를_두_줄이_쓰지_못한다(
    client: TestClient, admin: Signed
) -> None:
    """「id 로 찾아 키를 바꾸는 줄」 과 「그 키로 새로 만드는 줄」(또는 키를 바꾸는 두 줄)이
    함께 통과해 같은 식별자가 둘이 됐다."""
    part = _make_type(client, admin, label="부품", key_policy="optional")
    x = _make_object(client, admin, part, label="X", key="OLD-X")["id"]
    y = _make_object(client, admin, part, label="Y", key="OLD-Y")["id"]

    renamed_then_made = _rows(
        client, admin, part, [{"id": x, "key": "NEW"}, {"key": "NEW", "label": "새 것"}]
    )
    assert [one["action"] for one in renamed_then_made["rows"]] == ["update", "error"]

    made_then_renamed = _rows(
        client, admin, part, [{"key": "NEW", "label": "새 것"}, {"id": x, "key": "NEW"}]
    )
    assert [one["action"] for one in made_then_renamed["rows"]] == ["create", "error"]

    both_renamed = _rows(
        client, admin, part, [{"id": x, "key": "NEW"}, {"id": y, "key": "NEW"}]
    )
    assert [one["action"] for one in both_renamed["rows"]] == ["update", "error"]
    assert "1행" in both_renamed["rows"][1]["message"]


def test_여러_값과_예아니오_유일_속성도_겹침을_본다(client: TestClient, admin: Signed) -> None:
    """`str(['a'])` 는 `"['a']"` 인데 저장된 글자는 `'["a"]'`, `str(True)` 는 `"True"` 인데
    저장된 글자는 `"true"` — 그 칸들은 유일 검사가 아예 안 걸렸다."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="codes",
        label="코드",
        data_type="text",
        multi=True,
        unique=True,
    )
    _make_property(
        client, admin, part, key="master", label="대표", data_type="bool", unique=True
    )
    _make_object(
        client, admin, part, label="A", properties={"codes": ["가", "b"], "master": True}
    )

    def made(**properties: Any) -> int:
        got = client.post(
            f"/api/objects/{part}",
            json={"label": "B", "workspace_slug": admin.workspace, "properties": properties},
            headers=admin.headers,
        )
        return int(got.status_code)

    assert made(codes=["가", "b"]) == 409
    assert made(master=True) == 409
    assert made(codes=["가"], master=False) == 201

    plan = _rows(client, admin, part, [{"label": "C", "codes": "가;b"}])
    assert plan["counts"]["error"] == 1, plan


# --- 길이 --------------------------------------------------------------------------


def test_긴_식별자와_이름은_500_이_아니라_이유로_돌아온다(
    client: TestClient, admin: Signed
) -> None:
    """표의 칸은 식별자 120 · 이름 200 · 근거 500 자인데 검사가 없어, 화면은 500 이고 파일은
    계획을 통과한 뒤 적용에서 작업이 통째로 실패했다."""
    part = _make_type(client, admin, label="부품", key_policy="optional")
    made = client.post(
        f"/api/objects/{part}",
        json={"label": "A", "key": "K" * 121, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert made.status_code == 422, made.text
    assert "120자" in made.json()["error"]["message"]
    bolt = _make_object(client, admin, part, label="B", key="K-1")
    patched = client.patch(
        f"/api/objects/{part}/{bolt['id']}", json={"key": "K" * 121}, headers=admin.headers
    )
    assert patched.status_code == 422, patched.text

    plan = _rows(
        client,
        admin,
        part,
        [{"label": "C", "key": "K" * 121}, {"label": "가" * 201}, {"label": "D"}],
    )
    assert [one["action"] for one in plan["rows"]] == ["error", "error", "create"]

    kind = _make_relation(client, admin, "uses", label="씀")
    other = _make_object(client, admin, part, label="E")
    relations = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={
            "rows": [
                {
                    "src": bolt["id"],
                    "relation": kind,
                    "dst": other["id"],
                    "evidence_note": "근" * 501,
                }
            ]
        },
        headers=admin.headers,
    )
    assert relations.status_code == 200, relations.text
    assert relations.json()["counts"]["error"] == 1


def test_식별자를_안_쓰는_타입에_식별자_열이_오면_그_줄만_틀린다(
    client: TestClient, admin: Signed
) -> None:
    """예전에는 미리 읽기에서 터져 파일 전체가 422 였다 — 어느 줄인지 안 적혔다."""
    part = _make_type(client, admin, label="부품")  # key_policy 기본 none
    plan = _rows(client, admin, part, [{"label": "A", "key": "K-1"}, {"label": "B"}])
    assert [one["action"] for one in plan["rows"]] == ["error", "create"]


# --- 통계 · 목록 · 조건 -----------------------------------------------------------------


def test_접두어_없는_칸_이름은_422_다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    summed = client.get(
        f"/api/objects/{part}/summary",
        params={"metric": "sum", "metric_field": "weight"},
        headers=admin.headers,
    )
    assert summed.status_code == 422, summed.text
    assert "properties.weight" in summed.json()["error"]["message"]
    drawn = client.get(
        f"/api/objects/{part}/points", params={"x": "weight"}, headers=admin.headers
    )
    assert drawn.status_code == 422, drawn.text


def _sorted_by(
    client: TestClient, admin: Signed, slug: str, field: str, dir: str = "asc"
) -> None:
    got = client.patch(
        f"/api/ontology/types/{slug}",
        json={"list_view": {"sort": {"field": field, "dir": dir}}},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text


def test_숫자_칸은_숫자로_정렬한다(client: TestClient, admin: Signed) -> None:
    """글자로 세우면 10 · 100 · 9 — 못 읽는 값과 빈 값은 끝으로."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="n", label="수", data_type="number")
    for label, n in (("백", 100), ("구", 9), ("십", 10)):
        _make_object(client, admin, part, label=label, properties={"n": n})
    _make_object(client, admin, part, label="빈 것")
    _sorted_by(client, admin, part, "properties.n")
    assert _labels(client, admin, part) == ["구", "십", "백", "빈 것"]
    _sorted_by(client, admin, part, "properties.n", "desc")
    assert _labels(client, admin, part) == ["백", "십", "구", "빈 것"]


def test_뷰가_받는_정렬_칸은_모두_정렬된다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """상태 · 만든 때 · 소유 부서가 말없이 이름순으로 떨어졌다."""
    first, second = _other(db, "가 부서"), _other(db, "나 부서")
    part = _make_type(client, admin, label="부품")
    late = _make_object(client, admin, part, label="가", workspace_slug=second.slug)
    _make_object(client, admin, part, label="나", workspace_slug=first.slug)
    client.patch(
        f"/api/objects/{part}/{late['id']}",
        json={"status": "deprecated"},
        headers=admin.headers,
    )

    _sorted_by(client, admin, part, "status")
    assert _labels(client, admin, part) == ["나", "가"]
    _sorted_by(client, admin, part, "created_at", "desc")
    assert _labels(client, admin, part) == ["나", "가"]
    _sorted_by(client, admin, part, "owner_workspace")
    assert _labels(client, admin, part) == ["나", "가"]


def test_여러_값_글자_칸의_시작과_포함은_원소마다_본다(
    client: TestClient, admin: Signed
) -> None:
    """배열을 통째 글자로 견주면 「시작」 은 늘 `["` 로 시작해 0건이었다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="tags", label="태그", data_type="text", multi=True)
    _make_object(client, admin, part, label="A", properties={"tags": ["abc", "xyz"]})
    _make_object(client, admin, part, label="B", properties={"tags": ["qq"]})
    assert _labels(client, admin, part, **{"f.tags.starts": "ab"}) == ["A"]
    assert _labels(client, admin, part, **{"f.tags.starts": "xy"}) == ["A"]
    assert _labels(client, admin, part, **{"f.tags.contains": "BC"}) == ["A"]
    # 원소 사이(`", "`)에는 안 걸린다.
    assert _labels(client, admin, part, **{"f.tags.contains": 'c", "x'}) == []


def test_숫자_칸의_다름은_빈_것도_든다(client: TestClient, admin: Signed) -> None:
    """여러 값 숫자 · 참조 · 글자의 「다름」 은 빈 것을 넣는데 단일 숫자만 뺐다."""
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="n", label="수", data_type="number")
    _make_object(client, admin, part, label="오", properties={"n": 5})
    _make_object(client, admin, part, label="칠", properties={"n": 7})
    _make_object(client, admin, part, label="빈 것")
    assert sorted(_labels(client, admin, part, **{"f.n.ne": "5"})) == ["빈 것", "칠"]


def test_숫자_칸의_옛_글자는_조건을_500_으로_만들지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="n", label="수", data_type="number")
    _make_object(client, admin, part, label="숫자", properties={"n": 5})
    junk = _make_object(client, admin, part, label="옛 글자")
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == uuid.UUID(junk["id"]))
        .values(properties={"n": "약 5"})
    )
    db.commit()
    assert _labels(client, admin, part, **{"f.n.gt": "1"}) == ["숫자"]


def test_검색어의_와일드카드는_글자_그대로_찾는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품")
    for label in ("50%할인", "500개", "A_1", "AB1"):
        _make_object(client, admin, part, label=label)
    assert _labels(client, admin, part, q="50%") == ["50%할인"]
    assert _labels(client, admin, part, q="A_1") == ["A_1"]


def test_검색_자리가_못_훑는_칸뿐이면_이름으로_찾는다(
    client: TestClient, admin: Signed
) -> None:
    """예전에는 검색어를 말없이 버려 목록 전체가 「검색 결과」 로 나왔다."""
    part = _make_type(client, admin, label="부품")
    set_view = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"search": ["status"]}},
        headers=admin.headers,
    )
    assert set_view.status_code == 200, set_view.text
    _make_object(client, admin, part, label="볼트")
    _make_object(client, admin, part, label="너트")
    assert _labels(client, admin, part, q="볼") == ["볼트"]


# --- 관계 고치기 -------------------------------------------------------------------


def test_저쪽_끝을_못_보는_관계는_고치기_전에_404_다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    """고치기가 커밋된 뒤 돌려줄 줄을 못 찾아 500 이었다 — 저장은 됐는데 화면은 실패로
    읽는다."""
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    mine = _make_object(client, admin, part, label="내 부품")
    secret = _make_object(client, admin, part, label="비밀", workspace_slug=other.slug)
    linked = _link(client, admin, part, mine["id"], kind, secret["id"])
    assert linked.status_code == 201, linked.text
    relation_id = linked.json()["relation_id"]

    denied = client.patch(
        f"/api/objects/{part}/{mine['id']}/relations/{relation_id}",
        json={"evidence_note": "고침"},
        headers=manager.headers,
    )
    assert denied.status_code == 404, denied.text
    related = client.get(f"/api/objects/{part}/{mine['id']}", headers=admin.headers).json()
    assert [one["evidence_note"] for one in related["related"]] == [""]
