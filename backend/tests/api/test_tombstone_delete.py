"""원천이 보낸 것을 원천이 지운다(ADR 0090) — 무덤의 `action: "delete"` 와 원천 잠금 풀기.

datarelay 의 「보낸 것 걷어내기」 는 무덤만 담은 묶음을 올린다. 예전에는 받는 쪽이 사용
중지까지만 해서 시험으로 보낸 것이 목록에 남았고, 잠긴 타입이라 화면에서도 못
지웠다(OBJECTS-87).

- `delete` 는 그 타입을 잠근 원천의 묶음만 — 남이 잠근 타입 · 사람이 만든 타입은 줄 오류.
- 가리키는 것이 있으면 막는다. 같은 묶음이 끊는 선 · 함께 지우는 객체는 빼고 센다.
- 계획에서 「지움」 은 「사용 중지」 와 따로 센다.
- 잠금 풀기는 시스템 관리자만. 원천이 같은 정의를 다시 보내면 다시 잠근다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from tests.api.conftest import Signed, bundle_import
from tests.api.test_bundles import _bundle
from tests.api.test_ontology import _make_object, _make_type

SOURCE = "relay-x"


def _world(client: TestClient, admin: Signed) -> dict[str, str]:
    """원천(relay-x)이 넣은 기업 C-1 ← 툴 T-1 · T-2(개발사 칸), T-1 -경쟁-> T-2."""
    bundle, names = _bundle(admin.workspace)
    done = bundle_import(client, admin, {**bundle, "source": SOURCE, "apply": True})
    assert done["applied"] is True, done
    return names


def _graves(
    client: TestClient,
    admin: Signed,
    objects: list[dict[str, Any]],
    relations: list[dict[str, Any]] | None = None,
    *,
    source: str = SOURCE,
    apply: bool = False,
) -> dict[str, Any]:
    return bundle_import(
        client,
        admin,
        {
            "tombstones": {"objects": objects, "relations": relations or []},
            "source": source,
            "apply": apply,
        },
    )


def _keys(client: TestClient, admin: Signed, slug: str) -> set[str]:
    items = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"]
    return {one["key"] for one in items}


def test_원천이_잠근_타입은_원천의_무덤으로_지우고_사용_중지와_따로_센다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    tool, company = w["tool"], w["company"]
    objects = [
        {"type_slug": tool, "key": "T-1", "action": "delete"},
        {"type_slug": tool, "key": "T-2", "action": "delete"},
        # 툴 둘이 가리키지만 함께 지운다 — 가리키는 것으로 안 센다.
        {"type_slug": company, "key": "C-1", "action": "delete"},
    ]
    edge = [{"type_slug": tool, "src": "T-1", "relation": w["competes"], "dst": "T-2"}]
    planned = _graves(client, admin, objects, edge)
    assert planned["ok"] is True, planned
    rows = planned["tombstones"]["rows"]
    # 줄 차례는 보낸 차례 그대로 — 지우는 줄은 선 무덤 뒤에 계획하지만 표는 섞이지 않는다.
    assert [one["action"] for one in rows] == ["delete", "delete", "delete", "unlink"], rows
    assert "사용 중지가 아니라 삭제" in rows[0]["message"]
    assert planned["tombstones"]["counts"]["delete"] == 3
    assert planned["counts"]["tombstones_delete"] == 3
    assert planned["counts"]["tombstones_unlink"] == 1
    assert "tombstones_deprecate" not in planned["counts"]
    # 미리 보기는 아무것도 안 바꾼다.
    assert _keys(client, admin, tool) == {"T-1", "T-2"}

    done = _graves(client, admin, objects, edge, apply=True)
    assert done["applied"] is True, done
    assert _keys(client, admin, tool) == set() and _keys(client, admin, company) == set()
    # 이력 · 감사 기록은 남는다 — 누가 왜 지웠나.
    reasons = list(
        db.scalars(
            select(AuditEntry.reason).where(
                AuditEntry.action == "object.delete",
                AuditEntry.target_label.in_([f"{tool}:툴1", f"{company}:미국사"]),
            )
        )
    )
    assert len(reasons) == 2 and all(
        f"원천({SOURCE})의 요청으로 삭제" in (one or "") for one in reasons
    )


def test_가리키는_것이_있으면_막고_함께_지울_것이_막히면_다시_센다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    tool, company = w["tool"], w["company"]
    # 기업만 — 툴 둘의 개발사 칸이 가리킨다.
    alone = _graves(client, admin, [{"type_slug": company, "key": "C-1", "action": "delete"}])
    assert alone["ok"] is False
    (row,) = alone["tombstones"]["rows"]
    assert row["action"] == "error" and "다른 객체의 칸 2건" in row["message"], row

    # 셋을 함께 지우되 툴 사이의 선은 안 끊는다 — 툴 둘은 그 선에 막히고, 그러면 기업도
    # 그 툴들이 가리켜 막힌다(툴이 남는데 기업만 지워지지 않게).
    together = _graves(
        client,
        admin,
        [
            {"type_slug": tool, "key": "T-1", "action": "delete"},
            {"type_slug": tool, "key": "T-2", "action": "delete"},
            {"type_slug": company, "key": "C-1", "action": "delete"},
        ],
    )
    rows = together["tombstones"]["rows"]
    assert [one["action"] for one in rows] == ["error", "error", "error"], rows
    assert "관계 1건" in rows[0]["message"] and "다른 객체의 칸 2건" in rows[2]["message"]
    assert _keys(client, admin, company) == {"C-1"}


def test_남이_잠근_타입_사람이_만든_타입은_원천이_못_지우고_사용_중지된_것은_지운다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    tool = w["tool"]
    other = _graves(
        client, admin, [{"type_slug": tool, "key": "T-2", "action": "delete"}], source="other"
    )
    assert other["tombstones"]["rows"][0]["action"] == "error"
    assert SOURCE in other["tombstones"]["rows"][0]["message"]

    hand = _make_type(client, admin, label="손으로 만든 타입", key_policy="required")
    _make_object(client, admin, hand, label="손", key="H-1")
    refused = _graves(client, admin, [{"type_slug": hand, "key": "H-1", "action": "delete"}])
    (row,) = refused["tombstones"]["rows"]
    assert row["action"] == "error" and "관리하는 타입이 아니라" in row["message"], row
    # 사용 중지는 지금처럼 된다.
    kept = _graves(client, admin, [{"type_slug": hand, "key": "H-1"}])
    assert kept["tombstones"]["rows"][0]["action"] == "deprecate"

    # 합치기와 지우기를 함께 적으면 그 줄을 오류로.
    both = _graves(
        client,
        admin,
        [{"type_slug": tool, "key": "T-2", "merged_into": "T-1", "action": "delete"}],
    )
    assert "함께 지정했습니다" in both["tombstones"]["rows"][0]["message"]

    # 앞서 걷어낸(사용 중지된) 것도 나중에 지운다.
    edge = [{"type_slug": tool, "src": "T-1", "relation": w["competes"], "dst": "T-2"}]
    first = _graves(client, admin, [{"type_slug": tool, "key": "T-2"}], edge, apply=True)
    assert first["tombstones"]["rows"][0]["action"] == "deprecate"
    later = _graves(
        client, admin, [{"type_slug": tool, "key": "T-2", "action": "delete"}], apply=True
    )
    (row,) = later["tombstones"]["rows"]
    assert row["action"] == "delete" and "사용 중지 상태인" in row["message"], row
    assert _keys(client, admin, tool) == {"T-1"}


def test_잠금을_풀면_사람이_지우고_원천이_다시_보내면_다시_잠근다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    bundle, w = _bundle(admin.workspace)
    done = bundle_import(client, admin, {**bundle, "source": SOURCE, "apply": True})
    assert done["applied"] is True, done
    tool = w["tool"]
    t2 = next(
        one
        for one in client.get(f"/api/objects/{tool}", headers=admin.headers).json()["items"]
        if one["key"] == "T-2"
    )
    # 잠긴 동안은 화면에서 못 지운다(OBJECTS-87).
    locked = client.delete(f"/api/objects/{tool}/{t2['id']}", headers=admin.headers)
    assert locked.status_code == 409 and "OBJECTS-0087" in locked.text, locked.text

    unlock = f"/api/ontology/types/{tool}/unlock"
    assert client.post(unlock, headers=member.headers).status_code == 403
    got = client.post(unlock, headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.json() == {"slug": tool, "was": SOURCE}
    audit = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "ontology.type.unlock", AuditEntry.target_label == tool
        )
    ).one()
    assert audit.changes == {"managed_by": {"before": SOURCE, "after": ""}}
    # 이미 풀렸으면 아무것도 안 바꾼다.
    again = client.post(unlock, headers=admin.headers)
    assert again.json() == {"slug": tool, "was": ""}

    # 풀린 뒤로는 사람이 평소처럼 — 선이 걸려 있으면 화면의 막기 그대로.
    blocked = client.delete(f"/api/objects/{tool}/{t2['id']}", headers=admin.headers)
    assert blocked.status_code == 409 and "OBJECTS-0050" in blocked.text, blocked.text
    relation = client.post(
        f"/api/ontology/relation-types/{w['competes']}/unlock", headers=admin.headers
    )
    assert relation.json() == {"slug": w["competes"], "was": SOURCE}
    removed = client.delete(
        f"/api/objects/{tool}/{t2['id']}", params={"mode": "detach"}, headers=admin.headers
    )
    assert removed.status_code == 204, removed.text

    # 원천이 같은 정의를 다시 보내면 다시 잠근다.
    bundle_import(client, admin, {**bundle, "source": SOURCE, "apply": True})
    types = client.get("/api/ontology/types", headers=admin.headers).json()
    assert next(one for one in types if one["slug"] == tool)["managed_by"] == SOURCE


def _reason(db: Session, action: str, label: str) -> str | None:
    return db.scalars(
        select(AuditEntry.reason).where(
            AuditEntry.action == action, AuditEntry.target_label == label
        )
    ).one()


def test_정의_삭제는_잠근_원천의_이름으로_부르면_잠금을_넘고_다른_이름은_막는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """보강 — 정의 삭제 · 미리 보기가 `source` 를 받는다. 다른 막는 것은 그대로다."""
    w = _world(client, admin)
    tool, company, competes = w["tool"], w["company"], w["competes"]
    plan = "/api/ontology/delete-plan"

    # 이름 없이 · 다른 이름으로는 지금처럼 막는다 — 미리 보기가 잠근 원천을 싣는다.
    for named in ({}, {"source": "other"}):
        got = client.get(
            plan, params={"kind": "type", "slug": tool, **named}, headers=admin.headers
        )
        body = got.json()
        assert body["managed_by"] == SOURCE and body["allowed"] is False, body
        assert body["blocking"][0]["code"].endswith("ONTOLOGY-0082")
        assert "잠금 해제" in body["blocking"][0]["message"]
    refused = client.delete(
        f"/api/ontology/types/{company}/properties/country",
        params={"source": "other"},
        headers=admin.headers,
    )
    assert refused.status_code == 409 and "ONTOLOGY-0082" in refused.text, refused.text

    # 원천의 이름이면 잠금은 넘지만 객체가 남은 타입은 그대로 막는다.
    named = {"source": SOURCE}
    body = client.get(
        plan, params={"kind": "type", "slug": tool, **named}, headers=admin.headers
    ).json()
    assert [one["code"][-4:] for one in body["blocking"]] == ["0038"], body

    # 속성은 주인 타입의 잠금을 본다.
    dropped = client.delete(
        f"/api/ontology/types/{company}/properties/country",
        params=named,
        headers=admin.headers,
    )
    assert dropped.status_code == 204, dropped.text
    assert _reason(db, "ontology.property.delete", f"{company}.country") == (
        f"원천({SOURCE})의 이름으로 삭제"
    )

    # 원천이 객체를 삭제 표식으로 먼저 지운다 — 그 뒤 지운 객체만 남으면 영구 삭제 확인과 함께.
    graves = [
        {"type_slug": tool, "key": "T-1", "action": "delete"},
        {"type_slug": tool, "key": "T-2", "action": "delete"},
        {"type_slug": company, "key": "C-1", "action": "delete"},
    ]
    edge = [{"type_slug": tool, "src": "T-1", "relation": competes, "dst": "T-2"}]
    assert _graves(client, admin, graves, edge, apply=True)["applied"] is True
    body = client.get(
        plan, params={"kind": "type", "slug": tool, **named}, headers=admin.headers
    ).json()
    assert body["allowed"] is True and body["purge_deleted"] == 2, body
    gone = client.delete(
        f"/api/ontology/types/{tool}",
        params={**named, "purge_deleted": "true"},
        headers=admin.headers,
    )
    assert gone.status_code == 204, gone.text
    assert _reason(db, "ontology.type.delete", tool) == (
        f"원천({SOURCE})의 이름으로 삭제 · 지운 객체 2개 영구 삭제"
    )

    # 관계 종류도 — 맺힌 선은 툴과 함께 영구 삭제됐다.
    blocked = client.delete(f"/api/ontology/relation-types/{competes}", headers=admin.headers)
    assert blocked.status_code == 409 and "ONTOLOGY-0082" in blocked.text, blocked.text
    removed = client.delete(
        f"/api/ontology/relation-types/{competes}", params=named, headers=admin.headers
    )
    assert removed.status_code == 204, removed.text
    assert _reason(db, "ontology.relation_type.delete", competes) == (
        f"원천({SOURCE})의 이름으로 삭제"
    )
    slugs = {
        one["slug"] for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert tool not in slugs and company in slugs


def test_잠금이_없는_정의는_원천_이름을_적어도_평소의_삭제와_같다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    hand = _make_type(client, admin, label="손으로 만든 타입")
    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "type", "slug": hand, "source": SOURCE},
        headers=admin.headers,
    ).json()
    assert plan["allowed"] is True and plan["managed_by"] == "", plan
    gone = client.delete(
        f"/api/ontology/types/{hand}", params={"source": SOURCE}, headers=admin.headers
    )
    assert gone.status_code == 204, gone.text
    assert _reason(db, "ontology.type.delete", hand) is None


def test_같은_객체를_지우는_줄이_둘이면_뒤의_줄을_알린다(
    client: TestClient, admin: Signed
) -> None:
    """전에는 뒤의 줄이 계획에서 말없이 빠졌다 — 보낸 줄과 계획의 줄 수가 달랐다."""
    w = _world(client, admin)
    objects = [
        {"type_slug": w["company"], "key": "C-1", "action": "delete"},
        {"type_slug": w["company"], "key": "C-1", "action": "delete"},
        {"type_slug": w["tool"], "key": "T-1", "action": "delete"},
        {"type_slug": w["tool"], "key": "T-2", "action": "delete"},
    ]
    planned = _graves(client, admin, objects)
    rows = planned["tombstones"]["rows"]
    assert len(rows) == 4, rows
    second = next(one for one in rows if one["row"] == 2)
    assert second["action"] == "unchanged" and "1번 줄" in second["message"], rows
