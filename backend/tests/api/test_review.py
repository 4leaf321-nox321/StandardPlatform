"""검토함 — **사람이 판단할 일을 한 곳에서, 한꺼번에**(ADR 0027).

종류마다 원래 자리에서 읽어 모으고(복사하지 않는다), 처리는 원래 기능으로 간다. 그래서 여기서
보는 것은 넷이다: 원래 기능과 같은 결과가 나는가 · 「추천대로 모두」 가 문턱과 막힘을 지키고
미리 본 그대로 적용하는가 · 권한이 원래 화면의 것인가 · 결정이 기록되고 복원되는가.

시험 DB 를 스위트가 함께 쓰므로 늘 **이 시험이 만든 타입**으로 거른다 — 남이 남긴 줄이 섞인다.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.main import app as fastapi_app
from app.modules.accounts.models import User
from app.modules.objects import bulkedit, lifecycle, misses
from app.modules.objects import review as objreview
from app.modules.objects.models import ObjectAlias, ObjectInstance, ObjectRef, SearchMiss
from app.modules.ontology.models import ObjectType
from app.modules.review import registry
from app.modules.review import services as review_services
from app.modules.review.models import ReviewDecision
from app.modules.review.registry import Applied, Filters, Item
from app.modules.workspaces.models import Workspace
from app.shared import events
from app.shared.batches import chunks
from app.shared.text import compare_key
from tests.api.conftest import Signed, _login, _make_user, finish_job
from tests.api.test_bulk_writes import _last_seq, _since, _Statements
from tests.api.test_mcp_tools import Bot, server
from tests.api.test_ontology import _make_object, _make_property, _make_type, _uniq

# --- 손 ---------------------------------------------------------------------------


def _items(client: TestClient, who: Signed, **params: Any) -> dict[str, Any]:
    got = client.get("/api/review/items", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _plan(client: TestClient, who: Signed, **body: Any) -> dict[str, Any]:
    got = client.post("/api/review/plan", json=body, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _apply(
    client: TestClient,
    who: Signed,
    plan: dict[str, Any],
    *,
    mode: str = "selected",
    threshold: float | None = None,
) -> httpx.Response:
    got: httpx.Response = client.post(
        "/api/review/apply",
        json={
            "decisions": plan["decisions"],
            "fingerprint": plan["fingerprint"],
            "mode": mode,
            "threshold": threshold,
        },
        headers=who.headers,
    )
    return got


def _undo(client: TestClient, who: Signed, batch_id: str, apply: bool) -> dict[str, Any]:
    got = client.post(
        f"/api/review/batches/{batch_id}/undo", json={"apply": apply}, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _miss(db: Session, scope: str, text: str, times: int = 3) -> SearchMiss:
    """못 찾은 말 — 여러 사람이 `times` 번(홈 · 검토함의 문턱이 세 번이다)."""
    for one in range(times):
        assert misses.remember(db, scope, text, "resolve", f"asker{one}")
    db.commit()
    row = db.scalar(
        select(SearchMiss).where(
            SearchMiss.scope == scope, SearchMiss.norm == compare_key(text)
        )
    )
    assert row is not None
    return row


def _machine_alias(
    db: Session, type_slug: str, object_id: str, value: str, source: str = "적재 v3"
) -> str:
    """기계(적재)가 붙인 별칭 — 사람이 아직 안 봤다(`verified_at` 없음)."""
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == type_slug))
    row = ObjectAlias(
        object_id=uuid.UUID(object_id),
        type_id=type_id,
        kind="alias",
        value=value,
        norm=compare_key(value),
        source=source,
    )
    db.add(row)
    db.commit()
    return str(row.id)


def _delete_quietly(db: Session, object_id: str) -> None:
    """가리키는 것이 있는 객체를 지운다 — 화면 · API 는 막으므로(지우기 전 확인) 옛
    데이터처럼 표에서 바로. 「지워진 것을 가리키는 칸」 은 그렇게 생긴다(적재 · 동기화가 옛 id
    를 들고 온다)."""
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == uuid.UUID(object_id))
        .values(deleted_at=datetime.now(UTC))
    )
    db.commit()


def _vendor_part(client: TestClient, admin: Signed) -> tuple[str, str]:
    vendor = _make_type(client, admin, "vendor", label="공급사", key_policy="optional")
    part = _make_type(client, admin, "part", label="부품", key_policy="optional")
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


# --- 별칭 후보 ---------------------------------------------------------------------


def test_별칭_후보는_한_줄로_서고_추천대로_하면_원래_기능과_같다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool = _make_type(client, admin, "tool", label="툴")
    fluent = _make_object(client, admin, tool, label="Ansys Fluent")
    _make_object(client, admin, tool, label="Abaqus")
    miss = _miss(db, tool, "Ansys Fluet")

    page = _items(client, admin, kind="alias_candidate", type=tool)
    (item,) = page["items"]
    assert item["question"] == "「Ansys Fluet」 을(를) 무엇의 별칭으로 추가할까요?"
    assert item["evidence"][0] == "툴에서 3번 · 3명이 찾았으나 못 찾음"
    assert item["recommended"] == fluent["id"] and item["recommended_label"] == "Ansys Fluent"
    assert 0 < item["confidence"] <= 1 and "이름이 비슷함" in item["reason"]
    assert page["kinds"][0]["kind"] == "alias_candidate" and page["kinds"][0]["matched"] == 1

    plan = _plan(
        client,
        admin,
        decisions=[{"kind": "alias_candidate", "item_id": item["id"], "action": "accept"}],
    )
    (outcome,) = plan["outcomes"]
    assert outcome["status"] == "apply" and outcome["undoable"] is True
    assert outcome["effect"] == "「Ansys Fluet」 → Ansys Fluent의 별칭"
    # 미리 보기는 아무것도 안 바꾼다.
    db.expire_all()
    assert db.get(SearchMiss, miss.id).status == "pending"  # type: ignore[union-attr]

    done = _apply(client, admin, plan, mode="one")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["applied"] is True and body["counts"] == {"done": 1, "refused": 0}
    # 원래 기능(`misses.attach`)과 같은 결과 — 별칭 · 감사 기록 · 후보 닫힘.
    history = client.get(
        f"/api/objects/{tool}/{fluent['id']}/history", headers=admin.headers
    ).json()
    assert history[0]["changes"]["aliases"]["after"] == ["Ansys Fluet"]
    assert "별칭 후보" in (history[0]["reason"] or "")
    db.expire_all()
    assert db.get(SearchMiss, miss.id).status == "attached"  # type: ignore[union-attr]
    assert _items(client, admin, kind="alias_candidate", type=tool)["items"] == []

    # 결정 기록 — 추천을 따랐다.
    (decision,) = db.scalars(
        select(ReviewDecision).where(ReviewDecision.batch_id == uuid.UUID(body["batch_id"]))
    )
    assert decision.followed is True and decision.mode == "one"
    assert decision.recommended == fluent["id"] and decision.undoable is True

    # 복원 — 미리 보기 뒤 적용: 별칭이 빠지고 후보가 대기로.
    preview = _undo(client, admin, body["batch_id"], apply=False)
    assert preview["lines"][0]["status"] == "undo"
    undone = _undo(client, admin, body["batch_id"], apply=True)
    assert undone["counts"] == {"done": 1}
    got = client.get(f"/api/objects/{tool}/{fluent['id']}", headers=admin.headers).json()
    assert got["object"]["aliases"] == []
    db.expire_all()
    assert db.get(SearchMiss, miss.id).status == "pending"  # type: ignore[union-attr]
    # 두 번 복원하지 않는다.
    again = _undo(client, admin, body["batch_id"], apply=False)
    assert again["lines"][0]["status"] == "blocked"


def test_무시하고_다른_것으로_고르면_추천을_안_따른_것으로_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool = _make_type(client, admin, "tool", label="툴")
    fluent = _make_object(client, admin, tool, label="Ansys Fluent")
    mech = _make_object(client, admin, tool, label="Mechanical")
    first = _miss(db, tool, "Ansys Fluet")
    second = _miss(db, tool, "앤시스 메카")

    plan = _plan(
        client,
        admin,
        decisions=[
            {"kind": "alias_candidate", "item_id": str(first.id), "action": "ignore"},
            {
                "kind": "alias_candidate",
                "item_id": str(second.id),
                "action": "choose",
                "value": mech["id"],
            },
        ],
    )
    assert plan["counts"] == {"apply": 2, "blocked": 0}
    done = _apply(client, admin, plan).json()
    assert done["counts"]["done"] == 2
    db.expire_all()
    assert db.get(SearchMiss, first.id).status == "ignored"  # type: ignore[union-attr]
    attached = db.get(SearchMiss, second.id)
    assert attached is not None and str(attached.object_id) == mech["id"]
    rows = {
        one.item_id: one
        for one in db.scalars(
            select(ReviewDecision).where(
                ReviewDecision.batch_id == uuid.UUID(done["batch_id"])
            )
        )
    }
    # 무시는 추천(Ansys Fluent)을 안 따랐다. 추천이 없던 것(「앤시스 메카」 는 비슷한 이름이
    # 없다)은 셈하지 않는다.
    assert rows[str(first.id)].followed is False
    assert rows[str(first.id)].recommended == fluent["id"]
    assert rows[str(second.id)].recommended is None and rows[str(second.id)].followed is None

    accuracy = client.get("/api/review/decisions/accuracy", headers=admin.headers).json()
    line = next(one for one in accuracy["kinds"] if one["kind"] == "alias_candidate")
    assert line["recommended"] >= 1 and line["rate"] is not None
    assert [one["label"] for one in accuracy["bands"]] == [
        "0.9 이상",
        "0.7 ~ 0.9",
        "0.5 ~ 0.7",
        "0.5 미만",
    ]
    logged = client.get(
        "/api/review/decisions", params={"batch": done["batch_id"]}, headers=admin.headers
    ).json()
    assert logged["total"] == 2 and {one["action"] for one in logged["items"]} == {
        "ignore",
        "choose",
    }


# --- 「추천대로 모두」 ---------------------------------------------------------------


def test_추천대로_모두는_문턱_이상만_막히는_것은_빼고_미리_본_그대로_적용한다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    vendor = _make_type(client, admin, "vendor", label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    _make_object(client, admin, vendor, label="Altair")
    # 전역 객체 — 부서 관리자는 못 고친다(시스템 관리자만).
    shared = _make_object(client, admin, vendor, label="Siemens", workspace_slug=None)
    same = _machine_alias(db, vendor, ansys["id"], "ANSYS")  # 표기만 다름 → 확인 0.95
    clash = _machine_alias(db, vendor, ansys["id"], "altair")  # 남의 이름 → 삭제 0.9
    far = _machine_alias(db, vendor, ansys["id"], "앤시스")  # 음역 → 확인 0.5(문턱 아래)
    blocked = _machine_alias(db, vendor, shared["id"], "SIEMENS")  # 0.95 지만 막힘

    page = _items(client, manager, kind="alias_review", type=vendor)
    by_id = {one["id"]: one for one in page["items"]}
    assert by_id[same]["recommended"] == "approve" and by_id[same]["confidence"] == 0.95
    assert by_id[clash]["recommended"] == "remove" and by_id[clash]["confidence"] == 0.9
    assert by_id[far]["confidence"] < 0.9
    assert by_id[blocked]["blocked"] and "시스템 관리자" in by_id[blocked]["blocked"]
    # 처리할 수 있는 것만 — 막힌 것이 빠진다.
    mine = _items(client, manager, kind="alias_review", type=vendor, actionable=True)
    assert blocked not in {one["id"] for one in mine["items"]}
    # 확신도로 거르기.
    sure = _items(client, manager, kind="alias_review", type=vendor, min_confidence=0.9)
    assert {one["id"] for one in sure["items"]} == {same, clash, blocked}

    plan = _plan(
        client,
        manager,
        mode="recommended",
        threshold=0.9,
        filters={"kind": "alias_review", "type": vendor},
    )
    assert plan["counts"] == {"apply": 2, "blocked": 1} and plan["skipped"] == 1
    assert plan["outcomes"][0]["status"] == "blocked"  # 막힌 것 먼저
    assert {one["item_id"] for one in plan["decisions"]} == {same, clash, blocked}
    (line,) = plan["by_kind"]
    assert line["apply"] == 2 and line["blocked"] == 1 and line["undoable"] is True

    done = _apply(client, manager, plan, mode="recommended", threshold=0.9).json()
    assert done["counts"] == {"done": 2, "refused": 0} and done["undoable"] == 2
    db.expire_all()
    assert db.get(ObjectAlias, uuid.UUID(same)).verified_at is not None  # type: ignore[union-attr]
    assert db.get(ObjectAlias, uuid.UUID(clash)) is None
    assert db.get(ObjectAlias, uuid.UUID(far)).verified_at is None  # type: ignore[union-attr]
    assert db.get(ObjectAlias, uuid.UUID(blocked)).verified_at is None  # type: ignore[union-attr]
    rows = list(
        db.scalars(
            select(ReviewDecision).where(
                ReviewDecision.batch_id == uuid.UUID(done["batch_id"])
            )
        )
    )
    assert len(rows) == 2 and all(one.followed and one.threshold == 0.9 for one in rows)
    assert all(one.mode == "recommended" for one in rows)

    # 복원 — 확인은 검수 대기로, 삭제는 되살린다(원래 출처 그대로).
    undone = _undo(client, manager, done["batch_id"], apply=True)
    assert undone["counts"] == {"done": 2}
    db.expire_all()
    assert db.get(ObjectAlias, uuid.UUID(same)).verified_at is None  # type: ignore[union-attr]
    back = db.scalar(
        select(ObjectAlias).where(
            ObjectAlias.object_id == uuid.UUID(ansys["id"]), ObjectAlias.norm == "altair"
        )
    )
    assert back is not None and back.verified_at is None and back.source == "적재 v3"


def test_미리_본_뒤_바뀌면_아무것도_안_한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    vendor = _make_type(client, admin, "vendor", label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    one = _machine_alias(db, vendor, ansys["id"], "ANSYS")
    two = _machine_alias(db, vendor, ansys["id"], "ANSYS Inc")
    plan = _plan(
        client,
        admin,
        mode="all",
        action="accept",
        filters={"kind": "alias_review", "type": vendor},
    )
    assert plan["counts"]["apply"] == 2
    # 그 사이 원래 화면에서 하나를 확인했다.
    client.post(
        f"/api/objects/{vendor}/aliases/review",
        json={"ids": [one], "action": "approve"},
        headers=admin.headers,
    )
    late = _apply(client, admin, plan)
    assert late.status_code == 409, late.text
    db.expire_all()
    assert db.get(ObjectAlias, uuid.UUID(two)).verified_at is None  # type: ignore[union-attr]


# --- 같은 것끼리 묶기 · 지워진 것을 가리키는 칸 ---------------------------------------------


def test_같은_지워진_것을_가리키는_줄은_한_결정으로_바뀌고_묶음으로_복원된다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    vendor, part = _vendor_part(client, admin)
    old = _make_object(client, admin, vendor, label="ACME")
    parts = [
        _make_object(client, admin, part, label=f"볼트 {n}", properties={"vendor": old["id"]})
        for n in range(5)
    ]
    _delete_quietly(db, old["id"])
    fresh = _make_object(client, admin, vendor, label="ACME")  # 같은 이름의 새것

    page = _items(client, admin, kind="broken_ref", type=part)
    (item,) = page["items"]
    assert item["rows"] == 5
    assert item["question"].startswith("「공급사」 칸 5건이 지워진 「ACME」")
    assert item["recommended"] == fresh["id"] and item["confidence"] == 0.9
    assert item["choices"][-1]["value"] == "clear"

    plan = _plan(
        client,
        admin,
        decisions=[{"kind": "broken_ref", "item_id": item["id"], "action": "accept"}],
    )
    (outcome,) = plan["outcomes"]
    assert outcome["status"] == "apply" and outcome["rows"] == 5
    done = _apply(client, admin, plan).json()
    assert done["counts"]["done"] == 1 and done["outcomes"][0]["rows"] == 5
    for one in parts:
        got = client.get(f"/api/objects/{part}/{one['id']}", headers=admin.headers).json()
        assert got["object"]["properties"]["vendor"] == fresh["id"]
    assert _items(client, admin, kind="broken_ref", type=part)["items"] == []
    # 원래 기능(일괄 수정)의 기록 — 줄마다 같은 묶음 번호.
    history = client.get(
        f"/api/objects/{part}/{parts[0]['id']}/history", headers=admin.headers
    ).json()
    assert "검토함" in (history[0]["reason"] or "")

    # 묶음 복원 = 일괄 수정의 묶음 되돌리기 — 다시 지워진 것을 가리킨다.
    undone = _undo(client, admin, done["batch_id"], apply=True)
    assert undone["counts"] == {"done": 1}
    got = client.get(f"/api/objects/{part}/{parts[0]['id']}", headers=admin.headers).json()
    assert got["object"]["properties"]["vendor"] == old["id"]
    assert _items(client, admin, kind="broken_ref", type=part)["items"][0]["rows"] == 5


def test_같은_것끼리_묶으면_여러_줄이_한_줄로_접힌다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    vendor, part = _vendor_part(client, admin)
    assembly = _make_type(client, admin, "assy", label="조립품", key_policy="optional")
    _make_property(
        client,
        admin,
        assembly,
        key="maker",
        label="제조사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    old = _make_object(client, admin, vendor, label="ACME")
    for n in range(3):
        _make_object(client, admin, part, label=f"볼트 {n}", properties={"vendor": old["id"]})
    for n in range(2):
        _make_object(
            client, admin, assembly, label=f"모듈 {n}", properties={"maker": old["id"]}
        )
    _delete_quietly(db, old["id"])
    fresh = _make_object(client, admin, vendor, label="ACME Korea")

    flat = [
        one
        for one in _items(client, admin, kind="broken_ref")["items"]
        if one["group"] == old["id"]
    ]
    assert sorted(one["rows"] for one in flat) == [2, 3]
    folded = [
        one
        for one in _items(client, admin, kind="broken_ref", group=True, limit=200)["items"]
        if one["group"] == old["id"]
    ]
    (group,) = folded
    assert group["rows"] == 5 and len(group["members"]) == 2
    assert group["type_label"] == "여러 타입"
    # 접은 줄의 후보는 둘 다에게 있는 것 — 결정은 펼친 줄마다 간다.
    assert fresh["id"] in {one["value"] for one in group["choices"]}
    plan = _plan(
        client,
        admin,
        decisions=[
            {"kind": "broken_ref", "item_id": member, "action": "choose", "value": fresh["id"]}
            for member in group["members"]
        ],
    )
    assert plan["counts"] == {"apply": 2, "blocked": 0} and plan["rows"] == 5
    done = _apply(client, admin, plan).json()
    assert done["counts"]["done"] == 2
    assert not [
        one
        for one in _items(client, admin, kind="broken_ref")["items"]
        if one["group"] == old["id"]
    ]


def test_같은_말을_여러_자리에서_못_찾았으면_묶이고_한_번에_닫힌다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool = _make_type(client, admin, "tool", label="툴")
    target = _make_object(client, admin, tool, label=f"Hyperworks {uuid.uuid4().hex[:6]}")
    word = f"하이퍼웍스{uuid.uuid4().hex[:4]}"
    typed = _miss(db, tool, word)
    globally = _miss(db, "", word)

    folded = [
        one
        for one in _items(client, admin, kind="alias_candidate", group=True, limit=200)[
            "items"
        ]
        if one["group"] == compare_key(word)
    ]
    (group,) = folded
    assert set(group["members"]) == {str(typed.id), str(globally.id)}
    plan = _plan(
        client,
        admin,
        decisions=[
            {
                "kind": "alias_candidate",
                "item_id": member,
                "action": "choose",
                "value": target["id"],
            }
            for member in group["members"]
        ],
    )
    assert plan["counts"]["apply"] == 2
    done = _apply(client, admin, plan).json()
    # 앞의 결정이 붙이면서 같은 말의 다른 줄도 닫는다 — 뒤의 것은 「함께 닫힘」 으로 끝난다.
    assert done["counts"] == {"done": 2, "refused": 0}
    db.expire_all()
    assert {db.get(SearchMiss, typed.id).status, db.get(SearchMiss, globally.id).status} == {  # type: ignore[union-attr]
        "attached"
    }


# --- 지워진 것을 가리키는 칸 — 빠른 길은 예전 길과 같은 것을 남긴다 ------------------------
#
# 2026-10-10 에 쓰는 길을 바꿨다 — 일괄 수정의 계획 · 적용 · flush(줄마다 저장 검사 전체 ·
# 줄마다 UPDATE · ORM 감사)에서, **바뀌는 칸만** 줄마다 검사하고 일괄 입력의 덩어리 쓰기
# (`bulk._Writes`)로. 같은 결정을 두 길로 **같은 세계에서**(한 트랜잭션 안에서 적용 · 복원하고
# 롤백) 돌려 남는 것을 견준다 — 객체 · 사람이 고친 칸 · 감사(내용 · 차례) · 바깥 알림 ·
# 되돌릴 기록(묶음 표식) · 참조 색인 · 결정 기록 · 복원.


def _old_apply_broken_ref(
    db: Session, user: User, item: Item, value: str, _in_job: bool
) -> Applied:
    """**예전 길(2026-10-10 전)** — 일괄 수정의 계획(줄마다 저장 검사 전체) · 적용(속성
    전체를 다시 검증) · flush(줄마다 UPDATE) · 기록(`audit.record`, ORM). 견주려고 그대로
    옮겨 둔다."""
    if value == "clear":
        new, label = None, "비움"
    else:
        target = objreview._ref_target(db, user, item, value)
        assert target is not None
        new, label = str(target.id), target.label
    rows = objreview._ref_rows(db, user, item)
    object_type = db.get(ObjectType, uuid.UUID(str(item.payload["type_id"])))
    assert object_type is not None
    key = str(item.payload["key"])
    field_name = f"properties.{key}"
    by_value: dict[str, tuple[Any, list[ObjectInstance]]] = {}
    for row in rows:
        after = lifecycle.replace_ref(
            {key: (row.properties or {}).get(key)}, key, str(item.payload["dst_id"]), new
        ).get(key)
        by_value.setdefault(json.dumps(after, sort_keys=True), (after, []))[1].append(row)
    plans = [
        (
            value_for_rows,
            batch,
            bulkedit.plan(
                db,
                user,
                object_type,
                ids=[one.id for one in batch],
                field_name=field_name,
                value=value_for_rows,
                rows=batch,
            ),
        )
        for value_for_rows, group in by_value.values()
        for batch in chunks(group, bulkedit.MAX_ROWS)
    ]
    batch_id = uuid.uuid4()
    changed_total = 0
    for value_for_rows, batch, planned in plans:
        before_state = {one.id: bulkedit.state_of(one) for one in batch}
        before_raw = {one.id: bulkedit.raw_value(one, field_name) for one in batch}
        bulkedit.apply_to(
            db,
            user,
            object_type,
            field_name=field_name,
            value=value_for_rows,
            rows=batch,
            planned=planned,
        )
        db.flush()
        changed = {one.id for one in planned.rows if one.action == "change"}
        bulkedit.record(
            db,
            user,
            object_type,
            rows=batch,
            before_state=before_state,
            before_raw=before_raw,
            changed=changed,
            field_name=field_name,
            field_label=planned.field_label,
            batch_id=batch_id,
            reason=f"검토함 — 지워진 「{item.payload.get('dead_label')}」 대신 「{label}」",
        )
        changed_total += len(changed)
    return Applied(
        rows=changed_total,
        undo={"op": "bulk_edit", "batch_id": str(batch_id), "type_slug": object_type.slug},
    )


def _broken_world(client: TestClient, admin: Signed, db: Session) -> dict[str, Any]:
    """값 하나 칸(바꾸기 · 비우기) · 여러 값 칸(자리만 바꾸기 · 겹치면 하나로) · 참조 아닌 칸이
    함께 있는 줄 · 사람이 이름을 고친 줄 · 안 가리키는 줄. 한 줄이 지워진 것을 한 칸에서만
    가리킨다 — 두 칸이면 예전 길은 그 줄을 늘 뺐다(아래 시험)."""
    vendor, part = _vendor_part(client, admin)
    for key, label, more in (
        (
            "vendors",
            "공급사들",
            {"data_type": "object_ref", "ref_type_slug": vendor, "multi": True},
        ),
        ("maker", "제조사", {"data_type": "object_ref", "ref_type_slug": vendor}),
        ("cost", "비용", {"data_type": "number"}),
    ):
        _make_property(client, admin, part, key=key, label=label, **more)
    old = _make_object(client, admin, vendor, label="ACME")["id"]
    other = _make_object(client, admin, vendor, label="Beta")["id"]
    fresh = _make_object(client, admin, vendor, label="ACME")["id"]
    made = {
        label: _make_object(client, admin, part, label=label, properties=props)["id"]
        for label, props in (
            ("볼트 1", {"vendor": old, "cost": 1}),
            ("볼트 2", {"vendors": [old, other]}),
            ("볼트 3", {"vendors": [old]}),
            ("볼트 4", {"vendors": [other, old], "cost": 4}),
            ("볼트 5", {"vendor": old, "maker": other}),
            ("볼트 6", {"maker": old, "cost": 6}),
            ("볼트 7", {"vendors": [old, fresh]}),
            ("볼트 8", {"cost": 8}),
        )
    }
    edited = client.patch(
        f"/api/objects/{part}/{made['볼트 5']}",
        json={"label": "볼트 5(사람)"},
        headers=admin.headers,
    )
    assert edited.status_code == 200, edited.text
    _delete_quietly(db, old)
    workspace = db.scalar(select(Workspace.id).where(Workspace.slug == admin.workspace))
    return {
        "vendor": vendor,
        "part": part,
        "names": {
            old: "old",
            other: "other",
            fresh: "fresh",
            **{v: k for k, v in made.items()},
        },
        "ids": {"old": old, "other": other, "fresh": fresh, **made},
        "item": lambda key: f"{part}:{key}:{old}:{workspace}",
    }


class _Plain:
    """한 세계의 값을 **id · slug · 묶음 번호 없이** — 이름으로, 모르는 id 는 처음 본
    차례로."""

    def __init__(self, world: dict[str, Any]) -> None:
        self.names: dict[str, str] = dict(world["names"])
        self.slugs = {world["part"]: "<part>", world["vendor"]: "<vendor>"}

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self(one) for key, one in value.items()}
        if isinstance(value, list | tuple):
            return [self(one) for one in value]
        if isinstance(value, uuid.UUID):
            value = str(value)
        if not isinstance(value, str):
            return value
        if value in self.names:
            return self.names[value]
        try:
            uuid.UUID(value)
        except ValueError:
            for slug, name in self.slugs.items():
                value = value.replace(slug, name)
            return ":".join(self.names.get(part, part) for part in value.split(":"))
        self.names[value] = f"<id{len(self.names)}>"
        return self.names[value]


def _sizeless(changes: dict[str, Any]) -> dict[str, Any]:
    """묶음 표식의 크기만 뺀다 — 예전 길은 값마다 나눈 덩어리의 수를 적었다(새 길은 그 결정의
    바뀐 줄 수). 크기는 따로 본다."""
    if "_batch" not in changes:
        return changes
    return {**changes, "_batch": {**changes["_batch"], "size": None}}


def _apply_and_undo(
    db: Session, user: User, world: dict[str, Any], decisions: list[review_services.Decision]
) -> dict[str, Any]:
    """결정들을 적용하고 그 묶음을 복원한다 — 커밋 대신 flush(부르는 쪽이 롤백한다). 적용 뒤 ·
    복원 뒤의 모양을 이름으로.

    한 트랜잭션이라 결정 기록의 「정한 때」 가 모두 같다 — 복원은 결정을 그 차례(같으면 id)로
    돌므로, 복원 쪽은 차례 없이 견준다."""
    plain = _Plain(world)
    ids = [uuid.UUID(one) for one in world["ids"].values()]
    stamped: dict[uuid.UUID, datetime] = {
        object_id: at
        for object_id, at in db.execute(
            select(ObjectInstance.id, ObjectInstance.updated_at).where(
                ObjectInstance.id.in_(ids)
            )
        )
    }

    def state(since: int, ordered: bool) -> dict[str, Any]:
        db.expire_all()
        rows = list(db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(ids))))
        logged = [
            (
                one.action,
                plain(one.target_id),
                plain(one.target_label),
                plain(_sizeless(one.changes)),
                one.reason,
            )
            for one in _since(db, since)
        ]
        return {
            "objects": sorted(
                (
                    plain(one.id),
                    plain(one.label),
                    plain(one.properties),
                    sorted(one.human_edits),
                    one.updated_at != stamped[one.id],
                )
                for one in rows
            ),
            "audit": logged if ordered else sorted(logged, key=repr),
            "refs": sorted(
                plain([src, key, dst])
                for src, key, dst in db.execute(
                    select(ObjectRef.src_id, ObjectRef.key, ObjectRef.dst_id).where(
                        ObjectRef.src_id.in_(ids)
                    )
                )
            ),
        }

    since = _last_seq(db)
    with _Statements(db) as sent:
        planned = review_services.plan(db, user, decisions)
        result = review_services.apply(
            db,
            user,
            decisions,
            review_services.fingerprint_of(planned),
            mode="selected",
            threshold=None,
            planned=planned,
        )
    assert result.batch_id is not None
    heard = [
        (one.action, plain(one.target_id), plain(_sizeless(one.changes)))
        for one in db.info.get(events._KEY, [])
    ]
    applied = state(since, ordered=True)
    sizes = [
        one.changes["_batch"]["size"] for one in _since(db, since) if "_batch" in one.changes
    ]
    decided = sorted(
        (
            one.kind,
            plain(one.item_id),
            one.action,
            plain(one.choice),
            one.status,
            one.rows,
            one.followed,
            one.undoable,
            plain(one.undo),
            one.message,
        )
        for one in db.scalars(
            select(ReviewDecision).where(ReviewDecision.batch_id == result.batch_id)
        )
    )
    since = _last_seq(db)
    lines = review_services.undo_batch(db, user, result.batch_id, apply=True)
    return {
        "outcomes": [(one.status, one.rows, one.message) for one in result.outcomes],
        "applied": applied,
        "heard": heard,
        "decided": decided,
        "undo": sorted((line.status, line.message) for line in lines),
        "restored": state(since, ordered=False),
        "sizes": sizes,
        "statements": [one for one in sent.seen if one[0] == "objects"],
    }


def test_지워진_것을_가리키는_칸의_빠른_길은_예전_길과_같은_것을_남긴다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _broken_world(client, admin, db)
    user = _user(db, admin)
    fresh = world["ids"]["fresh"]
    decisions = [
        review_services.Decision("broken_ref", world["item"]("vendor"), "accept"),
        review_services.Decision("broken_ref", world["item"]("vendors"), "choose", fresh),
        review_services.Decision("broken_ref", world["item"]("maker"), "choose", "clear"),
    ]
    old_provider = dataclasses.replace(objreview.BROKEN_REF, apply=_old_apply_broken_ref)
    runs: dict[str, dict[str, Any]] = {}
    for path in ("old", "new"):
        with monkeypatch.context() as patch:
            if path == "old":
                patch.setitem(registry._registry, "broken_ref", old_provider)
            patch.setattr(db, "commit", db.flush)
            runs[path] = _apply_and_undo(db, user, world, decisions)
        db.rollback()
    old, new = runs["old"], runs["new"]
    for part in ("outcomes", "applied", "heard", "decided", "undo", "restored"):
        assert new[part] == old[part], part

    # 무엇이 남았나 — 두 길이 같으니 새 길만 본다.
    assert new["outcomes"] == [("done", 2, ""), ("done", 4, ""), ("done", 1, "")]
    objects = {one[0]: one for one in new["applied"]["objects"]}
    assert objects["볼트 1"][2] == {"vendor": "fresh", "cost": 1}
    assert objects["볼트 2"][2] == {"vendors": ["other", "fresh"]}
    assert objects["볼트 3"][2] == {"vendors": ["fresh"]}
    assert objects["볼트 4"][2] == {"vendors": ["other", "fresh"], "cost": 4}
    assert objects["볼트 6"][2] == {"cost": 6}  # 비움 — 칸이 빠진다
    assert objects["볼트 7"][2] == {"vendors": ["fresh"]}  # 겹치면 하나로
    # 사람이 고친 칸 — 바꾼 칸이 더해지고, 먼저 고친 이름 표시는 그대로.
    assert objects["볼트 5"][3] == ["label", "properties.vendor"]
    assert objects["볼트 3"][3] == ["properties.vendors"]
    assert objects["볼트 8"][3] == [] and objects["볼트 8"][4] is False  # 안 가리키는 줄
    assert ["볼트 1", "vendor", "fresh"] in new["applied"]["refs"]
    assert not [one for one in new["applied"]["refs"] if one[2] == "old"]
    # 감사 — 바뀐 줄마다 하나, 바깥 알림도 줄마다. 묶음 크기는 그 결정의 바뀐 줄 수(예전 길은
    # 값마다 나눈 덩어리의 수였다 — 여러 값 칸에서 2 · 2).
    assert [one[0] for one in new["applied"]["audit"]] == ["object.update"] * 7
    assert [one[0] for one in new["heard"]] == ["object.update"] * 7
    assert new["sizes"] == [2, 2, 4, 4, 4, 4, 1]
    assert old["sizes"] == [2, 2, 2, 2, 2, 2, 1]
    assert [one[4] for one in new["decided"]] == ["done"] * 3
    vendor_line = next(one for one in new["decided"] if one[1].split(":")[1] == "vendor")
    assert vendor_line[6] is True  # 추천(같은 이름의 새것)을 따랐다
    # 복원 — 지워진 것을 다시 가리킨다(그때 값).
    assert new["undo"] == [
        ("done", "1건을 그때 값으로"),
        ("done", "2건을 그때 값으로"),
        ("done", "4건을 그때 값으로"),
    ]
    restored = {one[0]: one for one in new["restored"]["objects"]}
    assert restored["볼트 4"][2] == {"vendors": ["other", "old"], "cost": 4}
    assert restored["볼트 6"][2] == {"maker": "old", "cost": 6}
    assert ["볼트 1", "vendor", "old"] in new["restored"]["refs"]

    # 쓰는 방식만 다르다 — 예전 길은 바뀐 줄마다 UPDATE **두** 문장(값은 적용 뒤 flush, 사람이
    # 고친 칸 표시는 기록 뒤 다음 flush — 참조 색인 트리거도 그만큼), 새 길은 결정마다 덩어리
    # UPDATE 한 문장(값과 표시를 함께).
    assert new["statements"] == [("objects", 1, True)] * 3
    assert not [one for one in old["statements"] if one[2]]
    assert sum(one[1] for one in old["statements"]) == 14


@pytest.mark.usefixtures("department_scope")
def test_지워진_것을_가리키는_칸은_바뀌는_칸만_검사하고_막을_것은_막는다(
    client: TestClient, admin: Signed, manager: Signed, db: Session
) -> None:
    """빠른 길은 **바뀌는 칸만** 줄마다 본다 — 그래도 막을 것(못 보는 · 타입이 안 맞는 · 지워진
    대상, 여러 값 칸에 남는 다른 지워진 것, 필수 칸 비우기, 유일 칸 겹침)은 막는다. 다른 칸이
    지워진 것을 가리키는 줄은 이제 그 칸과 상관없이 바뀐다(예전에는 늘 빠졌다)."""
    vendor, part = _vendor_part(client, admin)
    for key, label, more in (
        ("vendors", "공급사들", {"multi": True}),
        ("maker", "제조사", {"required": True}),
        ("sole", "전속 공급사", {"unique": True}),
    ):
        _make_property(
            client,
            admin,
            part,
            key=key,
            label=label,
            data_type="object_ref",
            ref_type_slug=vendor,
            **more,
        )
    tool = _make_type(client, admin, "tool", label="툴")
    wrong = _make_object(client, admin, tool, label="ACME")["id"]  # 이름은 같아도 툴
    old = _make_object(client, admin, vendor, label="ACME")["id"]
    gone = _make_object(client, admin, vendor, label="Gone")["id"]
    other = _make_object(client, admin, vendor, label="Beta")["id"]
    fresh = _make_object(client, admin, vendor, label="ACME")["id"]
    far_place = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(far_place)
    db.commit()
    stranger = _make_user(db, far_place, label="far", is_system_admin=False, role="manager")
    far = Signed(
        email=stranger.email, token=_login(client, stranger.email), workspace=far_place.slug
    )
    hidden = _make_object(client, far, vendor, label="남의 ACME")["id"]
    rows = {
        label: _make_object(client, admin, part, label=label, properties=props)["id"]
        for label, props in (
            ("가", {"vendor": old, "maker": old, "vendors": [old, gone]}),
            ("나", {"vendor": old, "maker": old, "vendors": [old]}),
            ("다", {"vendor": gone, "maker": old}),
            ("라", {"maker": other, "sole": old}),
            ("마", {"maker": other}),
        )
    }
    # 유일한 칸에 같은 값이 둘 — 화면 · API 는 막으므로 옛 데이터처럼 표에서 바로.
    twin = db.get(ObjectInstance, uuid.UUID(rows["마"]))
    assert twin is not None
    twin.properties = {**twin.properties, "sole": old}
    db.commit()
    _delete_quietly(db, old)
    _delete_quietly(db, gone)
    workspace = db.scalar(select(Workspace.id).where(Workspace.slug == admin.workspace))

    def item(key: str) -> str:
        return f"{part}:{key}:{old}:{workspace}"

    def outcome(who: Signed, key: str, value: str) -> dict[str, Any]:
        plan = _plan(
            client,
            who,
            decisions=[
                {
                    "kind": "broken_ref",
                    "item_id": item(key),
                    "action": "choose",
                    "value": value,
                }
            ],
        )
        (got,) = plan["outcomes"]
        return dict(got)

    # 대신할 것 — 타입이 안 맞는 것 · 지워진 것 · 못 보는 부서의 것은 막는다.
    for value in (wrong, gone):
        blocked = outcome(admin, "vendor", value)
        assert blocked["status"] == "blocked"
        assert "그 칸에 지정할 수 없는 객체입니다" in blocked["message"]
    assert outcome(manager, "vendor", hidden)["status"] == "blocked"
    # 다른 칸(제조사 · 공급사들)이 지워진 것을 가리켜도 바꾸는 칸만 본다 — 둘 다 바뀐다.
    fine = outcome(manager, "vendor", fresh)
    assert (fine["status"], fine["rows"], fine["message"]) == ("apply", 2, "")
    # 여러 값 칸에 다른 지워진 것이 남는 줄은 저장할 수 없다 — 그 줄만 빠진다.
    partly = outcome(admin, "vendors", fresh)
    assert partly["status"] == "apply" and partly["rows"] == 1
    assert partly["message"] == (
        f"1건은 저장할 수 없어 제외합니다 — 가리키는 객체를 찾을 수 없습니다: {gone}"
    )
    # 필수 칸을 비우면 — 모든 줄이 막힌다.
    required = outcome(admin, "maker", "clear")
    assert required["status"] == "blocked" and "제조사: 값이 필요합니다" in required["message"]
    # 유일한 칸 — 이 묶음의 두 줄이 같은 값이 되면 둘 다 막힌다. 비우는 것은 된다.
    same = outcome(admin, "sole", fresh)
    assert (
        same["status"] == "blocked" and "이 묶음의 2건이 같은 값이 됩니다" in same["message"]
    )
    assert outcome(admin, "sole", "clear")["rows"] == 2

    # 줄마다의 검사도 대상을 본다 — 묶음 앞에서 거르는 것(`_ref_target`)을 건너뛰어도.
    who = _user(db, manager)
    (vendor_item,) = objreview.collect_broken_refs(
        db, who, Filters(item_ids=frozenset([item("vendor")]))
    ).items
    _, _, changes = objreview._ref_changes(
        db, who, vendor_item, wrong, objreview._ref_values(db, who, vendor_item)
    )
    assert {one.action for one in changes} == {"error"}
    assert "참조 칸의 대상이 아닌 것을 가리킵니다" in changes[0].message
    _, _, changes = objreview._ref_changes(
        db, who, vendor_item, hidden, objreview._ref_values(db, who, vendor_item)
    )
    assert {one.message for one in changes} == {f"가리키는 객체를 찾을 수 없습니다: {hidden}"}

    plan = _plan(
        client,
        manager,
        decisions=[
            {
                "kind": "broken_ref",
                "item_id": item("vendor"),
                "action": "choose",
                "value": fresh,
            },
            {
                "kind": "broken_ref",
                "item_id": item("vendors"),
                "action": "choose",
                "value": fresh,
            },
        ],
    )
    done = _apply(client, manager, plan).json()
    assert [(one["status"], one["rows"]) for one in done["outcomes"]] == [
        ("done", 2),
        ("done", 1),
    ]
    assert done["outcomes"][1]["message"].startswith("1건은 저장하지 못했습니다 — ")
    db.expire_all()
    got = {label: db.get(ObjectInstance, uuid.UUID(one)) for label, one in rows.items()}
    assert got["가"] is not None and got["나"] is not None and got["다"] is not None
    assert got["가"].properties["vendor"] == fresh and got["나"].properties["vendor"] == fresh
    assert got["가"].properties["vendors"] == [old, gone]  # 빠진 줄은 그대로
    assert got["나"].properties["vendors"] == [fresh]
    assert got["다"].properties["vendor"] == gone  # 다른 지워진 것 — 다른 항목이다


def test_지워진_것을_가리키는_칸이_많으면_작업으로_바꾸고_복원한다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendor, part = _vendor_part(client, admin)
    old = _make_object(client, admin, vendor, label="ACME")["id"]
    parts = [
        _make_object(client, admin, part, label=f"볼트 {n}", properties={"vendor": old})["id"]
        for n in range(5)
    ]
    _delete_quietly(db, old)
    fresh = _make_object(client, admin, vendor, label="ACME")["id"]
    monkeypatch.setattr(review_services, "INLINE_ROWS", 4)
    (item,) = _items(client, admin, kind="broken_ref", type=part)["items"]
    plan = _plan(
        client,
        admin,
        decisions=[{"kind": "broken_ref", "item_id": item["id"], "action": "accept"}],
    )
    assert plan["as_job"] is True and plan["rows"] == 5
    queued = _apply(client, admin, plan)
    assert queued.status_code == 202, queued.text
    job = finish_job(client, admin, {"id": queued.json()["job_id"]})
    assert job["status"] == "done", job
    assert job["result"]["counts"] == {"done": 1, "refused": 0}
    db.expire_all()
    for one in parts:
        row = db.get(ObjectInstance, uuid.UUID(one))
        assert row is not None and row.properties["vendor"] == fresh
        # 작업(워커)도 사람의 결정이다 — 다음 동기화가 덮지 못하게 표시한다.
        assert "properties.vendor" in row.human_edits
    undone = _undo(client, admin, job["result"]["batch_id"], apply=True)
    assert undone["counts"] == {"done": 1}
    db.expire_all()
    assert all(
        db.get(ObjectInstance, uuid.UUID(one)).properties["vendor"] == old  # type: ignore[union-attr]
        for one in parts
    )


# --- 병합 후보 ---------------------------------------------------------------------


def test_병합_후보는_원래_병합으로_가고_복원은_없다고_말한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    vendor, part = _vendor_part(client, admin)
    keep = _make_object(client, admin, vendor, label="볼트상사")
    lose = _make_object(client, admin, vendor, label="볼트상사 ")
    _make_object(client, admin, part, label="볼트", properties={"vendor": keep["id"]})
    other = _make_object(client, admin, vendor, label="너트상사")
    other_twin = _make_object(client, admin, vendor, label="너트상사")

    page = _items(client, admin, kind="duplicate", type=vendor)
    by_label = {one["question"]: one for one in page["items"]}
    item = by_label["「볼트상사」 2개를 하나로 병합할까요?"]
    # 남길 것 = 가리키는 것이 많은 것.
    assert item["recommended"] == keep["id"] and item["confidence"] == 0.95
    plan = _plan(
        client,
        admin,
        decisions=[{"kind": "duplicate", "item_id": item["id"], "action": "accept"}],
    )
    (outcome,) = plan["outcomes"]
    assert outcome["undoable"] is False and "복원할 수 없습니다" in outcome["message"]
    assert "복원할 수 없습니다" in plan["by_kind"][0]["undo_note"]
    done = _apply(client, admin, plan).json()
    assert done["counts"]["done"] == 1
    db.expire_all()
    loser = db.get(ObjectInstance, uuid.UUID(lose["id"]))
    assert loser is not None and loser.deleted_at is not None
    assert str(loser.merged_into_id) == keep["id"]
    undo = _undo(client, admin, done["batch_id"], apply=False)
    assert undo["lines"][0]["status"] == "blocked"
    assert "병합은 복원할 수 없습니다" in undo["lines"][0]["message"]

    # 무시는 결정 기록이 기억한다 — 다시 안 뜨고, 복원하면 다시 뜬다.
    nut = next(one for one in page["items"] if one["question"].startswith("「너트상사」"))
    ignored = _apply(
        client,
        admin,
        _plan(
            client,
            admin,
            decisions=[{"kind": "duplicate", "item_id": nut["id"], "action": "ignore"}],
        ),
    ).json()
    assert ignored["counts"]["done"] == 1
    assert _items(client, admin, kind="duplicate", type=vendor)["items"] == []
    _undo(client, admin, ignored["batch_id"], apply=True)
    assert [
        one["id"] for one in _items(client, admin, kind="duplicate", type=vendor)["items"]
    ] == [nut["id"]]
    assert other and other_twin


# --- 권한 ----------------------------------------------------------------------------


@pytest.mark.usefixtures("department_scope")
def test_못_보는_부서의_항목은_안_보이고_멤버는_못_연다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed, db: Session
) -> None:
    vendor = _make_type(client, admin, "vendor", label="공급사", key_policy="optional")
    far_place = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(far_place)
    db.commit()
    stranger = _make_user(db, far_place, label="far", is_system_admin=False, role="manager")
    far = Signed(
        email=stranger.email, token=_login(client, stranger.email), workspace=far_place.slug
    )
    theirs = _make_object(client, far, vendor, label="남의 공급사")
    mine = _make_object(client, manager, vendor, label="우리 공급사")
    hidden = _machine_alias(db, vendor, theirs["id"], "THEIRS")
    shown = _machine_alias(db, vendor, mine["id"], "OURS")

    ids = {
        one["id"] for one in _items(client, manager, kind="alias_review", type=vendor)["items"]
    }
    assert shown in ids and hidden not in ids
    # 부서로 거르기 — 그 부서 것만.
    only = _items(client, admin, kind="alias_review", type=vendor, workspace=far_place.slug)
    assert {one["id"] for one in only["items"]} == {hidden}
    assert only["items"][0]["workspace_name"] == "먼 부서"
    # 안 보이는 항목을 id 로 집어 와도 처리되지 않는다 — 없는 것과 같은 말로.
    plan = _plan(
        client,
        manager,
        decisions=[{"kind": "alias_review", "item_id": hidden, "action": "accept"}],
    )
    assert plan["counts"] == {"apply": 0, "blocked": 1}
    assert "볼 수 없는" in plan["outcomes"][0]["message"]

    denied = client.get("/api/review/items", headers=member.headers)
    assert denied.status_code == 403
    assert client.post("/api/review/plan", json={}, headers=member.headers).status_code == 403


def test_홈_남은_일이_검토함의_그_종류로_간다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    tool = _make_type(client, admin, "tool", label="툴")
    _miss(db, tool, f"없는말{uuid.uuid4().hex[:4]}")
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    line = next(one for one in home if one["key"] == "alias_candidates")
    assert line["link"] == "/review?kind=alias_candidate"
    # 검토함을 못 보는 사람의 홈은 원래 화면으로 — 멤버에게는 그 줄 자체가 없다.
    assert review_services.maintenance_links(db, _user(db, member)) == {}


def _user(db: Session, who: Signed) -> Any:
    from app.modules.accounts.models import User

    return db.scalar(select(User).where(User.email == who.email))


# --- 큰 묶음은 작업으로 -------------------------------------------------------------


def test_큰_묶음은_작업으로_간다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendor = _make_type(client, admin, "vendor", label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    ids = [_machine_alias(db, vendor, ansys["id"], f"ANSYS-{n}") for n in range(3)]
    monkeypatch.setattr(review_services, "INLINE_DECISIONS", 1)
    plan = _plan(
        client,
        admin,
        mode="all",
        action="accept",
        filters={"kind": "alias_review", "type": vendor},
    )
    assert plan["as_job"] is True
    queued = _apply(client, admin, plan, mode="all")
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["applied"] is False and body["job_id"]
    job = finish_job(client, admin, {"id": body["job_id"]})
    assert job["status"] == "done", job
    assert job["result"]["applied"] is True and job["result"]["counts"]["done"] == 3
    # 작업 목록은 줄 목록을 요약만 싣는다(`sp_job_brief` · `rows_omitted`).
    listed = client.get("/api/jobs", params={"kind": "review_apply"}, headers=admin.headers)
    brief = next(one for one in listed.json()["items"] if one["id"] == body["job_id"])
    assert brief["result"]["rows"] == [] and brief["result"]["rows_omitted"] == 3
    db.expire_all()
    assert all(
        db.get(ObjectAlias, uuid.UUID(one)).verified_at is not None  # type: ignore[union-attr]
        for one in ids
    )
    batches = client.get("/api/review/batches", headers=admin.headers).json()
    assert any(one["batch_id"] == job["result"]["batch_id"] for one in batches)


# --- MCP 두 도구 ----------------------------------------------------------------------


@pytest.fixture
def review_bot(client: TestClient, admin: Signed) -> Iterator[Bot]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("bot"), "scopes": ["read", "objects:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    server._TRANSPORT = httpx.ASGITransport(app=fastapi_app)
    from app.modules.jobs import services as job_services

    original_get = server._get

    async def get_with_worker(ctx: Any, path: str, params: Any = None) -> Any:
        if path.startswith("/api/jobs/"):
            job_services.process_one("test-worker")
        return await original_get(ctx, path, params)

    server._get = get_with_worker
    try:
        yield Bot(made.json()["token"])
    finally:
        server._get = original_get
        server._TRANSPORT = None


def test_MCP_빈_목록은_까닭을_말한다(review_bot: Bot) -> None:
    """0건을 그냥 0건으로 주면 읽는 쪽은 「없다」 로 읽는다 — 거르기 때문인지 말한다."""
    listed = review_bot.call(server.review_list, kind="alias_review", type_slug="no_such_type")
    assert listed["items"] == [] and listed["notes"]


def test_MCP_review_decide_흐름(
    client: TestClient, admin: Signed, review_bot: Bot, db: Session
) -> None:
    vendor = _make_type(client, admin, "vendor", label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    first = _machine_alias(db, vendor, ansys["id"], "ANSYS")
    second = _machine_alias(db, vendor, ansys["id"], "앤시스")

    listed = review_bot.call(server.review_list, kind="alias_review", type_slug=vendor)
    assert {one["id"] for one in listed["items"]} == {first, second}
    assert listed["kinds"][0]["matched"] == 2

    # 무엇을 정할지 없으면 거절 — 스스로 고르지 않는다.
    with pytest.raises(RuntimeError):
        review_bot.call(server.review_decide)
    preview = review_bot.call(
        server.review_decide, recommended=True, kind="alias_review", type_slug=vendor
    )
    assert preview["applied"] is False and preview["counts"] == {"apply": 1, "blocked": 0}
    assert preview["skipped"] == 1 and "apply=True" in preview["next"]
    db.expire_all()
    assert db.get(ObjectAlias, uuid.UUID(first)).verified_at is None  # type: ignore[union-attr]
    # 지문 없이 적용하지 않는다.
    with pytest.raises(RuntimeError):
        review_bot.call(
            server.review_decide,
            recommended=True,
            kind="alias_review",
            type_slug=vendor,
            apply=True,
        )
    done = review_bot.call(
        server.review_decide,
        recommended=True,
        kind="alias_review",
        type_slug=vendor,
        apply=True,
        fingerprint=preview["fingerprint"],
    )
    assert done["applied"] is True and done["counts"] == {"done": 1, "refused": 0}
    db.expire_all()
    assert db.get(ObjectAlias, uuid.UUID(first)).verified_at is not None  # type: ignore[union-attr]
    assert db.get(ObjectAlias, uuid.UUID(second)).verified_at is None  # type: ignore[union-attr]

    # 고른 것 — 다른 것으로(삭제).
    picked = [
        {"kind": "alias_review", "item_id": second, "action": "choose", "value": "remove"}
    ]
    plan = review_bot.call(server.review_decide, decisions=picked)
    gone = review_bot.call(
        server.review_decide, decisions=picked, apply=True, fingerprint=plan["fingerprint"]
    )
    assert gone["counts"]["done"] == 1
    db.expire_all()
    assert db.get(ObjectAlias, uuid.UUID(second)) is None
