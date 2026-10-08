"""남의 부서 객체가 **새지 않는다** — 목록 · 상세가 숨기는 것을 다른 자리도 숨기나.

이 틀의 규칙은 「없는 것과 안 보이는 것을 같은 말로」 다. 목록 · 상세 · 관련 객체는 지키는데,
그 옆 자리(이력 · 묶음 막대 · 트리 수 · 파일 가져오기 · 알림 · 변경 이력)가 저장된 이름이나
가시성 없는 수를 그대로 내보내고 있었다(2026-10-08 점검). 자리마다 하나씩 붙잡는다.

    내 부서(workspace) — admin · manager · member 가 함께 있다
    다른 부서(other)    — 시스템 관리자(admin)만 그 안의 객체를 본다
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, notifications_of
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
    _tree,
    _tree_type,
)
from tests.api.test_watches import _watch

HIDDEN = "(볼 수 없는 객체)"


def _other(db: Session) -> Workspace:
    """내가 속하지 않은 부서 — 그 안의 객체는 시스템 관리자만 본다."""
    row = Workspace(slug=f"o-{uuid.uuid4().hex[:8]}", name="다른 부서")
    db.add(row)
    db.commit()
    return row


def _hidden(
    client: TestClient, admin: Signed, slug: str, other: Workspace, **kw: Any
) -> dict[str, Any]:
    return _make_object(client, admin, slug, workspace_slug=other.slug, **kw)


def _labels(client: TestClient, who: Signed, slug: str, **params: Any) -> list[str]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return sorted(one["label"] for one in got.json()["items"])


def _buckets(client: TestClient, who: Signed, slug: str, group_by: str) -> list[str]:
    got = client.get(
        f"/api/objects/{slug}/summary", params={"group_by": group_by}, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return [one["label"] for one in got.json()["buckets"]]


# --- 이력 ---------------------------------------------------------------------


def test_이력의_관계_기록은_못_보는_끝의_이름을_가린다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """기록은 맺을 때의 양 끝 이름을 박아 둔다 — 「관련 객체」 는 숨기는 줄인데 이력은 그
    이름을 그대로 보였다."""
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    mine = _make_object(client, admin, part, label="내 부품")
    secret = _hidden(client, admin, part, other, label="비밀 부품")
    assert _link(client, admin, part, mine["id"], kind, secret["id"]).status_code == 201

    def others(who: Signed) -> list[str]:
        got = client.get(f"/api/objects/{part}/{mine['id']}/history", headers=who.headers)
        assert got.status_code == 200, got.text
        return [
            one["relation"]["other_label"] for one in got.json() if one["kind"] == "relation"
        ]

    assert others(admin) == ["비밀 부품"]
    assert others(member) == [HIDDEN]


# --- 이어진 것 너머 — 조건 · 묶음 ---------------------------------------------------


def test_관계_걸음으로_묶거나_걸러도_못_보는_객체는_안_나온다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """`group_by=in.<관계>` 로 묶으면 남의 부서 객체 이름이 막대로 섰다 — 들어오는 참조
    걸음에만 가시성이 걸려 있었다."""
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(
        client,
        admin,
        "supplies",
        label="공급",
        inverse_label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[part],
    )
    mine = _make_object(client, admin, part, label="내 부품")
    secret = _hidden(client, admin, part, other, label="비밀 공급처")
    assert _link(client, admin, part, secret["id"], kind, mine["id"]).status_code == 201

    assert "비밀 공급처" in _buckets(client, admin, part, f"in.{kind}")
    assert "비밀 공급처" not in _buckets(client, member, part, f"in.{kind}")
    # 이어진 것이 못 보는 것뿐이면 「이어져 있음」 에도 안 걸린다 — 있다는 사실도 샌다.
    assert _labels(client, admin, part, **{f"f.in.{kind}.notempty": ""}) == ["내 부품"]
    assert _labels(client, member, part, **{f"f.in.{kind}.notempty": ""}) == []
    assert _labels(client, member, part, **{f"f.in.{kind}.eq": secret["id"]}) == []
    assert _labels(client, member, part, **{f"f.in.{kind}.label.eq": "비밀 공급처"}) == []


def test_참조_칸이_못_보는_객체를_가리키면_이름을_가린다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """가리킨 뒤에 상대가 다른 부서로 갔을 수 있다 — 칸의 이름표 · 묶음 막대 · 참조 너머의
    조건이 그 이름과 값을 보였다."""
    other = _other(db)
    company = _make_type(client, admin, label="기업")
    _make_property(client, admin, company, key="country", label="국가", data_type="text")
    tool = _make_type(client, admin, label="툴")
    _make_property(
        client,
        admin,
        tool,
        key="maker",
        label="만든 곳",
        data_type="object_ref",
        ref_type_slug=company,
    )
    secret = _hidden(
        client, admin, company, other, label="비밀사", properties={"country": "US"}
    )
    made = _make_object(client, admin, tool, label="툴1", properties={"maker": secret["id"]})

    profile = client.get(f"/api/objects/{tool}/{made['id']}", headers=member.headers).json()
    assert profile["object"]["ref_labels"][secret["id"]] == HIDDEN
    seen = client.get(f"/api/objects/{tool}/{made['id']}", headers=admin.headers).json()
    assert seen["object"]["ref_labels"][secret["id"]] == "비밀사"

    assert "비밀사" not in _buckets(client, member, tool, "properties.maker")
    assert HIDDEN in _buckets(client, member, tool, "properties.maker")
    assert "비밀사" not in _buckets(client, member, tool, "ref.maker.label")
    assert "비밀사" in _buckets(client, admin, tool, "ref.maker.label")
    # 못 보는 객체의 값을 조건으로 떠볼 수 없다.
    assert _labels(client, admin, tool, **{"f.ref.maker.country.eq": "US"}) == ["툴1"]
    assert _labels(client, member, tool, **{"f.ref.maker.country.eq": "US"}) == []


# --- 트리 ---------------------------------------------------------------------


def test_트리의_수는_보이는_것만_센다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """「자식 2」 를 눌러 하나만 열리고, 「어디에도 안 걸린 것 2」 를 눌러 하나만 나오면 — 그
    차이가 남의 부서에 무엇이 있는지 말한다."""
    other = _other(db)
    part, kind = _tree_type(client, admin)
    top = _make_object(client, admin, part, label="구동부")
    motor = _make_object(client, admin, part, label="모터")
    secret = _hidden(client, admin, part, other, label="비밀 모터")
    _link(client, admin, part, motor["id"], kind, top["id"])
    _link(client, admin, part, secret["id"], kind, top["id"])
    _make_object(client, admin, part, label="내 외톨이")
    _hidden(client, admin, part, other, label="비밀 외톨이")

    mine = _tree(client, member, part)
    assert [one["label"] for one in mine["nodes"]] == ["구동부"]
    assert mine["nodes"][0]["child_count"] == 1
    assert mine["orphan_count"] == 1
    assert _tree(client, member, part, parent=top["id"])["nodes"][0]["label"] == "모터"

    everything = _tree(client, admin, part)
    assert everything["nodes"][0]["child_count"] == 2
    assert everything["orphan_count"] == 2


# --- 파일 가져오기의 참조 풀이 · 저장의 참조 검사 ---------------------------------------


def _vendor_world(client: TestClient, admin: Signed) -> tuple[str, str]:
    vendor = _make_type(client, admin, label="공급사")
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
    return vendor, part


def test_파일의_참조는_못_보는_객체의_별칭에_가려지지_않는다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    """별칭이 이름보다 먼저 맞는다 — 남의 부서 객체의 별칭 「ACME」 가 보이는 「ACME」 를
    가리고, 그 뒤 「찾을 수 없습니다: <id>」 로 못 보는 객체의 id 가 샜다."""
    other = _other(db)
    vendor, part = _vendor_world(client, admin)
    visible = _make_object(client, admin, vendor, label="ACME")
    secret = _hidden(client, admin, vendor, other, label="비밀사")
    named = client.put(
        f"/api/objects/{vendor}/{secret['id']}/aliases",
        json={"aliases": ["ACME"]},
        headers=admin.headers,
    )
    assert named.status_code == 200, named.text

    body = {"rows": [{"label": "볼트", "vendor": "ACME"}], "workspace_slug": manager.workspace}
    plan = client.post(f"/api/objects/{part}/import-rows", json=body, headers=manager.headers)
    assert plan.status_code == 200, plan.text
    assert plan.json()["counts"]["error"] == 0, plan.json()
    assert secret["id"] not in json.dumps(plan.json())

    applied = client.post(
        f"/api/objects/{part}/import-rows",
        json={**body, "apply": True},
        headers=manager.headers,
    ).json()
    assert applied["applied"] is True, applied
    made = applied["rows"][0]["object_id"]
    got = client.get(f"/api/objects/{part}/{made}", headers=manager.headers).json()
    assert got["object"]["properties"]["vendor"] == visible["id"]


def test_id_만_알아도_못_보는_객체를_칸에_넣지_못한다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    """파일 가져오기 · 관계 잇기는 보이는 것만 찾는데, 만들기 · 고치기는 id 가 있기만 하면
    받았다 — 그리고 상세의 이름표가 그 이름을 보였다."""
    other = _other(db)
    vendor, part = _vendor_world(client, admin)
    secret = _hidden(client, admin, vendor, other, label="비밀사")

    denied = client.post(
        f"/api/objects/{part}",
        json={
            "label": "볼트",
            "workspace_slug": manager.workspace,
            "properties": {"vendor": secret["id"]},
        },
        headers=manager.headers,
    )
    assert denied.status_code == 422, denied.text
    assert "찾을 수 없습니다" in denied.json()["error"]["message"]

    mine = _make_object(client, manager, part, label="너트")
    patched = client.patch(
        f"/api/objects/{part}/{mine['id']}",
        json={"properties": {"vendor": secret["id"]}},
        headers=manager.headers,
    )
    assert patched.status_code == 422, patched.text

    # 이미 들어 있던 값(시스템 관리자가 넣었다)은 **다른 칸을 고치는 저장**을 막지 않는다.
    held = _make_object(client, admin, part, label="와셔", properties={"vendor": secret["id"]})
    renamed = client.patch(
        f"/api/objects/{part}/{held['id']}", json={"label": "평와셔"}, headers=manager.headers
    )
    assert renamed.status_code == 200, renamed.text


# --- 지켜보기 알림 ---------------------------------------------------------------


def test_못_보게_된_사람에게는_알림이_안_간다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    """`/watching` 목록은 거르는데 알림은 객체 이름과 바뀐 칸을 그대로 실어 보냈다."""
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    bolt = _make_object(client, admin, part, label="볼트")
    assert _watch(client, manager, part, bolt["id"], True).status_code == 200
    before = len(notifications_of(client, manager, "object.changed"))

    moved = client.post(
        f"/api/objects/{part}/bulk-edit",
        json={"ids": [bolt["id"]], "field": "workspace", "value": other.slug, "apply": True},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["applied"] is True, moved.json()
    edited = client.patch(
        f"/api/objects/{part}/{bolt['id']}", json={"label": "비밀 볼트"}, headers=admin.headers
    )
    assert edited.status_code == 200, edited.text

    after = notifications_of(client, manager, "object.changed")
    assert len(after) == before
    assert not any("비밀 볼트" in one["title"] for one in after)


# --- 변경 이력(감사) ---------------------------------------------------------------


def test_부서_관리자는_자기_부서의_변경_이력만_본다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    """어느 부서든 관리자이기만 하면 **모든 부서**의 기록 — 남의 부서 객체 이름 · 바뀐 값 —
    이 다 보였다."""
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    mine = _make_object(client, admin, part, label="내 부품")
    secret = _hidden(client, admin, part, other, label="비밀 부품")

    def created(who: Signed) -> set[str]:
        got = client.get(
            "/api/audit/entries",
            params={"action": "object.create", "limit": 200},
            headers=who.headers,
        )
        assert got.status_code == 200, got.text
        return {one["target_id"] for one in got.json()["items"]}

    assert {mine["id"], secret["id"]} <= created(admin)
    seen = created(manager)
    assert mine["id"] in seen
    assert secret["id"] not in seen
    # 부서가 없는 기록(타입 정의)은 시스템 관리자의 일이다.
    defs = client.get(
        "/api/audit/entries",
        params={"target_table": "object_types", "limit": 200},
        headers=manager.headers,
    )
    assert defs.status_code == 200, defs.text
    assert defs.json()["items"] == []


# --- 합쳐진 것 · 끊을 선 · 깨진 참조 -----------------------------------------------------


def test_합쳐진_남의_객체를_열어도_그_이름이_안_나온다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    winner = _make_object(client, admin, part, label="남은 것")
    loser = _hidden(client, admin, part, other, label="비밀 이름")
    merged = client.post(
        f"/api/objects/{part}/{loser['id']}/merge",
        json={"into": winner["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text

    opened = client.get(f"/api/objects/{part}/{loser['id']}", headers=member.headers)
    assert opened.status_code == 404
    assert "비밀 이름" not in opened.text
    # 볼 수 있던 사람에게는 어디로 갔는지 말한다(옛 링크가 새 것으로 간다).
    told = client.get(f"/api/objects/{part}/{loser['id']}", headers=admin.headers)
    assert told.json()["error"]["details"]["merged_into"] == winner["id"]


def test_파일대로_맞출_때_끊을_선의_못_보는_끝은_이름을_가린다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    other = _other(db)
    part = _make_type(client, admin, label="부품")
    kind = _make_relation(client, admin, "uses", label="씀")
    mine = _make_object(client, admin, part, label="내 부품")
    friend = _make_object(client, admin, part, label="이웃 부품")
    secret = _hidden(client, admin, part, other, label="비밀 부품")
    assert _link(client, admin, part, mine["id"], kind, secret["id"]).status_code == 201

    plan = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={
            "rows": [{"src": mine["id"], "relation": kind, "dst": friend["id"]}],
            "relations_mode": "replace",
        },
        headers=manager.headers,
    )
    assert plan.status_code == 200, plan.text
    unlinks = [one["label"] for one in plan.json()["rows"] if one["action"] == "unlink"]
    assert unlinks == [f"내 부품 -{kind}-> {HIDDEN}"]


def test_깨진_참조의_지워진_남의_객체는_이름을_가린다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    other = _other(db)
    vendor, part = _vendor_world(client, admin)
    secret = _hidden(client, admin, vendor, other, label="비밀사")
    mine = _make_object(client, admin, part, label="볼트", properties={"vendor": secret["id"]})
    # 참조를 비우지 않고 지워진 상태 — 옛 데이터 · 원 SQL 이 남기는 모양이다.
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == uuid.UUID(secret["id"]))
        .values(deleted_at=func.now())
    )
    db.commit()

    def detail(who: Signed) -> str:
        got = client.get(
            "/api/objects/quality/report", params={"kind": "broken_ref"}, headers=who.headers
        )
        assert got.status_code == 200, got.text
        found = next(one for one in got.json()["findings"] if one["type_slug"] == part)
        return str(next(hit for hit in found["hits"] if hit["id"] == mine["id"])["detail"])

    assert "비밀사" in detail(admin)
    shown = detail(manager)
    assert "비밀사" not in shown
    assert HIDDEN in shown


def test_내보내기는_못_보는_객체를_가리키는_칸을_비운다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """화면에서 가린 이름이 파일로는 나갔다 — 참조 칸에 상대의 식별자 · 이름(없으면 id)을
    그대로 썼다(2026-10-08). 비운 칸은 다시 넣을 때 「안 건드림」 이라 값을 지우지 않는다."""
    from tests.api.conftest import finish_job

    other = _other(db)
    company = _make_type(client, admin, label="기업")
    tool = _make_type(client, admin, label="툴")
    _make_property(
        client,
        admin,
        tool,
        key="maker",
        label="만든 곳",
        data_type="object_ref",
        ref_type_slug=company,
    )
    secret = _hidden(client, admin, company, other, label="비밀사")
    _make_object(client, admin, tool, label="툴1", properties={"maker": secret["id"]})

    def exported(who: Signed) -> str:
        started = client.post(f"/api/objects/{tool}/export", headers=who.headers)
        assert started.status_code == 202, started.text
        done = finish_job(client, who, started.json())
        assert done["status"] == "done", done
        got = client.get(f"/api/jobs/{done['id']}/download", headers=who.headers)
        assert got.status_code == 200, got.text
        return str(got.text)

    mine = exported(member)
    assert "툴1" in mine
    assert "비밀사" not in mine and secret["id"] not in mine
    assert "비밀사" in exported(admin)
