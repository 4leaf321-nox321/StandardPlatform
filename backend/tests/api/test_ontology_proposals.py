"""정의 제안(ADR 0096) — 정의를 **적용 대신 쌓고**, 시스템 관리자가 검토함에서 적용 · 거절한다.

- 제안은 로그인한 누구나(토큰은 `objects:write` 만으로). 그 자리에서 센 계획을 함께 남긴다 —
  오류가 있거나 바뀌는 것이 없으면 받지 않고, 같은 사람의 같은 계획은 있는 것을 돌려준다.
- 적용은 지금 다시 센 계획의 지문이 제안 때와 같을 때만 — 그새 정의가 바뀌었으면 막고
  「다시 셈」. 적용은 정의 가져오기와 같은 길(스냅샷 · 감사 `ontology.import`).
- 거절은 되돌린다(다시 엶). 제안한 사람은 철회한다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.ontology.models import OntologyProposal
from tests.api.conftest import Signed, maintenance_counts

KIND = "definition_proposal"


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _schema(
    slug: str, *, label: str = "협력사", extra: list[dict[str, Any]] | None = None
) -> Any:
    return {
        "types": [
            {
                "slug": slug,
                "label": label,
                "key_policy": "optional",
                "properties": [
                    {"key": "country", "label": "국가", "data_type": "text"},
                    *(extra or []),
                ],
            }
        ]
    }


def _propose(client: TestClient, who: Signed | dict[str, str], body: dict[str, Any]) -> Any:
    headers = who.headers if isinstance(who, Signed) else who
    return client.post("/api/ontology/proposals", json=body, headers=headers)


def _token(client: TestClient, who: Signed, scopes: list[str]) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"proposal-{_hex()}", "scopes": scopes},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}", "X-SP-Tool": "ontology_propose"}


def _items(client: TestClient, who: Signed) -> dict[str, dict[str, Any]]:
    got = client.get("/api/review/items", params={"kind": KIND}, headers=who.headers)
    assert got.status_code == 200, got.text
    return {one["id"]: one for one in got.json()["items"] if one["kind"] == KIND}


def _plan(client: TestClient, who: Signed, item_id: str, value: str) -> dict[str, Any]:
    planned = client.post(
        "/api/review/plan",
        json={
            "mode": "decisions",
            "decisions": [
                {"kind": KIND, "item_id": item_id, "action": "choose", "value": value}
            ],
        },
        headers=who.headers,
    )
    assert planned.status_code == 200, planned.text
    return dict(planned.json())


def _decide(client: TestClient, who: Signed, item_id: str, value: str) -> dict[str, Any]:
    plan = _plan(client, who, item_id, value)
    applied = client.post(
        "/api/review/apply",
        json={"decisions": plan["decisions"], "fingerprint": plan["fingerprint"]},
        headers=who.headers,
    )
    assert applied.status_code == 200, applied.text
    return dict(applied.json())


def _row(db: Session, proposal_id: str) -> OntologyProposal:
    db.expire_all()
    found = db.get(OntologyProposal, uuid.UUID(proposal_id))
    assert found is not None
    return found


def _type_slugs(client: TestClient, admin: Signed) -> set[str]:
    got = client.get("/api/ontology/types", headers=admin.headers)
    assert got.status_code == 200, got.text
    return {one["slug"] for one in got.json()}


def test_구성원의_AI_가_제안하고_관리자가_검토함에서_적용한다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    slug = f"vendor_{_hex()}"
    headers = _token(client, member, ["read", "objects:write"])
    # 정의 가져오기는 막혀 있다(시스템 관리자 · ontology:write) — 그래서 제안이다.
    direct = client.post("/api/ontology/import", json=_schema(slug), headers=headers)
    assert direct.status_code == 403, direct.text

    before = maintenance_counts(client, admin).get("ontology_proposals", 0)
    made = _propose(
        client,
        headers,
        {
            "schema": _schema(slug),
            "note": "공급 계약서 3건에서 협력사 · 국가가 반복",
            "origin_ref": "계약서-2026.docx",
        },
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["status"] == "proposed" and body["created"] is True
    assert body["summary"] == "타입 새로 1 · 속성 새로 1"
    assert body["tool"] == "ontology_propose" and body["token_name"].startswith("proposal-")
    assert body["plan"]["errors"] == [] and body["plan"]["applied"] is False
    assert any("source 없이 새 정의" in one for one in body["plan"]["warnings"]), body
    assert slug not in _type_slugs(client, admin), "제안은 아무것도 안 바꾼다"
    assert maintenance_counts(client, admin).get("ontology_proposals", 0) == before + 1
    assert "ontology_proposals" not in maintenance_counts(client, member)

    # 같은 사람이 같은 계획을 또 내면 있는 것.
    again = _propose(client, headers, {"schema": _schema(slug)})
    assert again.status_code == 200 and again.json()["id"] == body["id"], again.text
    assert again.json()["created"] is False

    # 구성원은 자기 것만, 관리자는 전부.
    mine = client.get("/api/ontology/proposals", headers=member.headers).json()
    assert [one["id"] for one in mine if one["id"] == body["id"]] == [body["id"]]
    assert any(
        one["id"] == body["id"]
        for one in client.get("/api/ontology/proposals", headers=admin.headers).json()
    )

    # 검토함 — 구성원은 검토함을 못 연다(적용 · 거절은 시스템 관리자).
    denied = client.get("/api/review/items", params={"kind": KIND}, headers=member.headers)
    assert denied.status_code == 403, denied.text
    item = _items(client, admin)[body["id"]]
    assert [one["value"] for one in item["choices"]] == ["accept", "replan", "reject"]
    assert item["recommended"] is None and item["confidence"] is None
    assert any("계약서-2026.docx" in line for line in item["evidence"])
    assert any("AI 도구 ontology_propose" in line for line in item["evidence"])
    assert any(f"타입 새로: {slug}" in line for line in item["evidence"])

    done = _decide(client, admin, body["id"], "accept")
    assert done["counts"].get("done") == 1, done
    assert slug in _type_slugs(client, admin)
    row = _row(db, body["id"])
    assert row.status == "accepted" and row.snapshot_id is not None
    audit = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "ontology.import", AuditEntry.target_id == row.snapshot_id
        )
    ).one()
    assert audit.reason == f"정의 제안 {row.id}"
    assert body["id"] not in _items(client, admin), "처리하면 검토함에서 빠진다"
    # 처리된 것은 철회 · 다시 셈하지 못한다.
    late = client.post(
        f"/api/ontology/proposals/{body['id']}/withdraw", headers=member.headers
    )
    assert late.status_code == 409 and "ONTOLOGY-0109" in late.text, late.text


def test_그새_정의가_바뀌면_적용을_막고_다시_셈하면_새_계획으로(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    slug = f"vendor_{_hex()}"
    made = _propose(
        client,
        member,
        {
            "schema": _schema(
                slug, extra=[{"key": "grade", "label": "등급", "data_type": "text"}]
            )
        },
    )
    assert made.status_code == 201, made.text
    proposal = made.json()
    # 그 사이 관리자가 같은 타입을 국가만으로 먼저 만들었다 — 계획이 달라진다.
    direct = client.post(
        "/api/ontology/import",
        params={"dry_run": "false"},
        json=_schema(slug),
        headers=admin.headers,
    )
    assert direct.status_code == 200 and direct.json()["applied"] is True, direct.text

    plan = _plan(client, admin, proposal["id"], "accept")
    outcome = next(one for one in plan["outcomes"] if one["item_id"] == proposal["id"])
    assert outcome["status"] == "blocked" and "다시 셈" in outcome["message"], outcome

    replanned = _decide(client, admin, proposal["id"], "replan")
    assert replanned["counts"].get("done") == 1, replanned
    row = _row(db, proposal["id"])
    assert row.status == "proposed" and row.fingerprint != proposal["fingerprint"]
    assert row.summary == "속성 새로 1"
    item = _items(client, admin)[proposal["id"]]
    assert item["question"] == "정의 제안 「속성 새로 1」 을(를) 적용할까요?"

    done = _decide(client, admin, proposal["id"], "accept")
    assert done["counts"].get("done") == 1, done
    props = client.get(f"/api/ontology/types/{slug}/properties", headers=admin.headers).json()
    assert {one["key"] for one in props} >= {"country", "grade"}


def test_거절은_되돌리고_철회는_제안한_사람만(
    client: TestClient, admin: Signed, member: Signed, manager: Signed, db: Session
) -> None:
    first = _propose(client, member, {"schema": _schema(f"vendor_{_hex()}")}).json()
    rejected = _decide(client, admin, first["id"], "reject")
    assert rejected["counts"].get("done") == 1, rejected
    assert _row(db, first["id"]).status == "rejected"
    listed = client.get(
        "/api/ontology/proposals", params={"status": "rejected"}, headers=member.headers
    ).json()
    seen = next(one for one in listed if one["id"] == first["id"])
    assert seen["status_label"] == "거절" and seen["decided_by"]
    undone = client.post(
        f"/api/review/batches/{rejected['batch_id']}/undo",
        json={"apply": True},
        headers=admin.headers,
    )
    assert undone.status_code == 200, undone.text
    assert _row(db, first["id"]).status == "proposed"

    # 남의 제안은 못 보고 못 거둔다.
    other = client.post(
        f"/api/ontology/proposals/{first['id']}/withdraw", headers=manager.headers
    )
    assert other.status_code == 403, other.text
    hidden = client.get(f"/api/ontology/proposals/{first['id']}", headers=manager.headers)
    assert hidden.status_code == 403, hidden.text
    gone = client.post(
        f"/api/ontology/proposals/{first['id']}/withdraw", headers=member.headers
    )
    assert gone.status_code == 200 and gone.json()["status"] == "withdrawn", gone.text
    assert first["id"] not in _items(client, admin)


def test_오류가_있거나_바뀌는_것이_없으면_받지_않는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    broken = _propose(
        client,
        member,
        {
            "schema": _schema(
                f"vendor_{_hex()}",
                extra=[{"key": "w", "label": "무게", "data_type": "number_maybe"}],
            )
        },
    )
    assert broken.status_code == 409 and "ONTOLOGY-0107" in broken.text, broken.text
    assert broken.json()["error"]["details"]["errors"]

    slug = f"vendor_{_hex()}"
    made = client.post(
        "/api/ontology/import",
        params={"dry_run": "false"},
        json=_schema(slug),
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    same = _propose(client, member, {"schema": _schema(slug)})
    assert same.status_code == 409 and "바뀌는 것이 없습니다" in same.text, same.text

    unknown = _propose(client, member, {"schema": {"typez": []}})
    assert unknown.status_code == 409 and "ONTOLOGY-0070" in unknown.text, unknown.text
