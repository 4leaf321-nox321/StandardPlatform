"""별칭 후보 — **못 찾은 말을 모아, 사람이 별칭으로 붙이면 다음부터 찾힌다.**

이름으로 찾다가 못 찾은 쪽은(사람이든 AI 든) 없는 줄 알고 새로 만든다 — 그러면 같은 것이
둘이 된다. 그 말을 모아 두면 사람이 그것을 어떤 객체의 다른 이름으로 붙일 수 있다(ADR 0025).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects import misses
from app.modules.objects.models import SearchMiss
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, _login, _make_user, maintenance_counts
from tests.api.test_ontology import _make_object, _make_type


def _candidates(client: TestClient, who: Signed, **params: Any) -> list[dict[str, Any]]:
    got = client.get("/api/objects/alias-candidates", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return list(got.json()["items"])


def _resolve(client: TestClient, who: Signed, slug: str, name: str) -> str:
    got = client.get(
        f"/api/objects/{slug}/resolve", params={"name": name}, headers=who.headers
    )
    assert got.status_code == 200, got.text
    return str(got.json()["match"])


def test_못_찾은_말이_남고_별칭으로_붙이면_다음부터_찾힌다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    tool = _make_type(client, admin, label="툴")
    fluent = _make_object(client, admin, tool, label="Ansys Fluent")

    # 사람(관리자)과 다른 사람(부서 관리자)이 같은 오타로 못 찾는다 — 한 줄, 두 사람.
    assert _resolve(client, admin, tool, "Ansys Fluet") == "none"
    assert _resolve(client, manager, tool, "ansys  fluet") == "none"
    # 통합 검색에서도 못 찾았다 — 범위가 다르니 다른 줄이다.
    found = client.get("/api/search", params={"q": "Ansys Fluet"}, headers=admin.headers)
    assert found.json()["total"] == 0

    (row,) = _candidates(client, admin, type=tool)
    assert row["text"] == "Ansys Fluet" and row["hits"] == 2 and row["people"] == 2
    assert row["vias"] == ["resolve"] and row["status"] == "pending"
    assert row["scope_label"] == "툴" and row["scope_kind"] == "type"
    # 「이것 아닐까」 — 이름이 비슷한 것. 짐작이므로 사람이 고른다.
    assert row["suggestions"][0]["id"] == fluent["id"]
    assert row["suggestions"][0]["matched"] == "label"
    globally = [
        one for one in _candidates(client, admin, type="") if one["text"] == "Ansys Fluet"
    ]
    assert len(globally) == 1 and globally[0]["scope_label"] == "통합 검색"

    # 미리 보기 — 아무것도 안 바뀐다.
    path = f"/api/objects/alias-candidates/{row['id']}/attach"
    plan = client.post(path, json={"object_id": fluent["id"]}, headers=admin.headers)
    assert plan.status_code == 200, plan.text
    assert plan.json()["applied"] is False
    assert plan.json()["aliases_before"] == [] and plan.json()["aliases_after"] == [
        "Ansys Fluet"
    ]
    assert _resolve(client, admin, tool, "Ansys Fluet") == "none"

    done = client.post(
        path, json={"object_id": fluent["id"], "apply": True}, headers=admin.headers
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["applied"] is True and body["candidate"]["status"] == "attached"
    assert body["candidate"]["object_label"] == "Ansys Fluent"
    # 같은 말을 통합 검색에서 못 찾은 줄도 함께 닫힌다 — 이제 거기서도 찾힌다.
    assert body["closed"] == 1
    assert _resolve(client, admin, tool, "Ansys Fluet") == "exact"
    assert (
        client.get("/api/search", params={"q": "Ansys Fluet"}, headers=admin.headers).json()[
            "total"
        ]
        == 1
    )
    assert not [
        one for one in _candidates(client, admin, type="") if one["text"] == "Ansys Fluet"
    ]

    # 객체 화면의 길과 같다 — 감사 기록이 남고, 사람이 붙인 것이라 검수 대기가 아니다.
    history = client.get(
        f"/api/objects/{tool}/{fluent['id']}/history", headers=admin.headers
    ).json()
    assert history[0]["changes"]["aliases"]["after"] == ["Ansys Fluet"]
    assert "별칭 후보" in (history[0]["reason"] or "")
    pending = client.get(f"/api/objects/{tool}/aliases/pending", headers=admin.headers).json()
    assert pending["total"] == 0


def test_거르는_말과_있는데_안_보인_말은_남지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool = _make_type(client, admin, label="툴")
    _make_object(client, admin, tool, label="Abaqus")
    for word in (
        "z",
        "12345",
        "2026-10-08",
        str(uuid.uuid4()),
        "kim@example.com",
        "Passw0rd!x",
    ):
        assert _resolve(client, admin, tool, word) == "none"
    assert _candidates(client, admin, type=tool) == []

    # 남의 부서에만 있는 객체의 이름 — 못 찾은 까닭은 이름이 아니라 권한이다.
    other = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(other)
    db.commit()
    stranger = _make_user(db, other, label="far", is_system_admin=False, role="member")
    who = Signed(
        email=stranger.email, token=_login(client, stranger.email), workspace=other.slug
    )
    assert _resolve(client, who, tool, "abaqus") == "none"
    assert _candidates(client, admin, type=tool) == []


@pytest.mark.parametrize(
    ("word", "reason"),
    [
        ("가", "짧음"),
        ("x" * 101, "김"),
        ("010-1234-5678", "숫자뿐"),
        ("Passw0rd!x", "비밀번호 같음"),
        ("sp_pat_abc", "비밀번호 같음"),
        ("BT-2041", None),
        ("iPhone15Pro", None),
        ("앤시스", None),
    ],
)
def test_거르는_기준(word: str, reason: str | None) -> None:
    """부품번호(`BT-2041`)와 제품 이름(`iPhone15Pro`)은 남는다 — 소문자 · 대문자 · 숫자 ·
    기호를 **모두** 쓴 띄어쓰기 없는 말만 비밀번호로 본다."""
    assert misses.skip_reason(word) == reason


def test_목록은_검색어만_걸렸을_때_남기고_치는_중의_글자는_지운다(
    client: TestClient, admin: Signed
) -> None:
    tool = _make_type(client, admin, label="툴")
    _make_object(client, admin, tool, label="Nastran")
    listed = client.get(
        f"/api/objects/{tool}",
        params={"q": "Zzyzx", "status": "active"},
        headers=admin.headers,
    )
    assert listed.json()["total"] == 0
    assert _candidates(client, admin, type=tool) == []  # 다른 조건이 함께 걸렸다

    for typed in ("Zzy", "Zzyz", "Zzyzx"):
        client.get(f"/api/objects/{tool}", params={"q": typed}, headers=admin.headers)
    rows = _candidates(client, admin, type=tool)
    assert [one["text"] for one in rows] == ["Zzyzx"] and rows[0]["vias"] == ["list"]


def test_무시하면_다시_안_뜨고_되돌릴_수_있다(client: TestClient, admin: Signed) -> None:
    tool = _make_type(client, admin, label="툴")
    assert _resolve(client, admin, tool, "엉뚱한말") == "none"
    (row,) = _candidates(client, admin, type=tool)
    decided = client.post(
        "/api/objects/alias-candidates/decide",
        json={"ids": [row["id"], str(uuid.uuid4())], "action": "ignore"},
        headers=admin.headers,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["done"] == 1 and len(decided.json()["refused"]) == 1

    # 또 찾아도 무시한 채로 횟수만 오른다.
    assert _resolve(client, admin, tool, "엉뚱한말") == "none"
    assert _candidates(client, admin, type=tool) == []
    (ignored,) = _candidates(client, admin, type=tool, status="ignored")
    assert ignored["hits"] == 2 and ignored["decided_at"]

    client.post(
        "/api/objects/alias-candidates/decide",
        json={"ids": [row["id"]], "action": "restore"},
        headers=admin.headers,
    )
    assert [one["id"] for one in _candidates(client, admin, type=tool)] == [row["id"]]


def test_다른_객체가_쓰는_별칭이면_막고_범위_밖_객체에는_안_붙인다(
    client: TestClient, admin: Signed
) -> None:
    tool = _make_type(client, admin, label="툴")
    vendor = _make_type(client, admin, label="공급사")
    one = _make_object(client, admin, tool, label="LS-DYNA")
    two = _make_object(client, admin, tool, label="Radioss")
    elsewhere = _make_object(client, admin, vendor, label="Altair")
    assert _resolve(client, admin, tool, "다이나") == "none"
    (row,) = _candidates(client, admin, type=tool)
    # 그 사이에 누군가 다른 객체에 같은 별칭을 붙였다.
    taken = client.put(
        f"/api/objects/{tool}/{two['id']}/aliases",
        json={"aliases": ["다이나"]},
        headers=admin.headers,
    )
    assert taken.status_code == 200, taken.text

    path = f"/api/objects/alias-candidates/{row['id']}/attach"
    plan = client.post(path, json={"object_id": one["id"]}, headers=admin.headers).json()
    assert plan["blocking"] and "Radioss의 별칭" in plan["blocking"][0]
    refused = client.post(
        path, json={"object_id": one["id"], "apply": True}, headers=admin.headers
    )
    assert refused.status_code == 409
    assert "OBJECTS-0114" in refused.json()["error"]["code"]

    wrong = client.post(path, json={"object_id": elsewhere["id"]}, headers=admin.headers)
    assert wrong.status_code == 422 and "OBJECTS-0113" in wrong.json()["error"]["code"]


def test_부서_관리자_이상만_보고_자기_부서_것에만_붙인다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    denied = client.get("/api/objects/alias-candidates", headers=member.headers)
    assert denied.status_code == 403

    tool = _make_type(client, admin, label="툴")
    other = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(other)
    db.commit()
    boss = _make_user(db, other, label="far-boss", is_system_admin=False, role="manager")
    far = Signed(email=boss.email, token=_login(client, boss.email), workspace=other.slug)
    mine = _make_object(client, admin, tool, label="Marc")
    assert _resolve(client, admin, tool, "마크") == "none"
    (row,) = _candidates(client, far, type=tool)
    # 먼 부서의 관리자는 우리 부서 객체를 못 본다 — 없는 것과 같은 말로 답한다.
    got = client.post(
        f"/api/objects/alias-candidates/{row['id']}/attach",
        json={"object_id": mine["id"]},
        headers=far.headers,
    )
    assert got.status_code == 404


def test_여러_번_찾은_말이_남은_일에_뜬다(client: TestClient, admin: Signed) -> None:
    tool = _make_type(client, admin, label="툴")
    before = maintenance_counts(client, admin).get("alias_candidates", 0)
    for _ in range(misses.REPEAT_MIN):
        assert _resolve(client, admin, tool, "자주찾는말") == "none"
    after = maintenance_counts(client, admin)
    assert after.get("alias_candidates", 0) == before + 1


def test_오래된_것과_지운_타입의_줄은_정리한다(db: Session) -> None:
    old = datetime.now(UTC) - timedelta(days=misses.KEEP_DECIDED_DAYS + 1)
    rows = [
        SearchMiss(scope="", norm=f"old-{uuid.uuid4().hex}", text="옛 무시", status="ignored"),
        SearchMiss(scope="", norm=f"rare-{uuid.uuid4().hex}", text="드문 말", hits=1),
        SearchMiss(scope="gone_type_x", norm=f"gone-{uuid.uuid4().hex}", text="지운 타입"),
        SearchMiss(scope="", norm=f"keep-{uuid.uuid4().hex}", text="최근 대기", hits=1),
    ]
    db.add_all(rows)
    db.flush()
    rows[0].last_at = old
    rows[1].last_at = datetime.now(UTC) - timedelta(days=misses.KEEP_RARE_DAYS + 1)
    db.commit()
    assert misses.purge(db) >= 3
    db.commit()
    left = set(
        db.scalars(select(SearchMiss.text).where(SearchMiss.id.in_([r.id for r in rows])))
    )
    assert left == {"최근 대기"}
