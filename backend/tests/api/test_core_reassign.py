"""부서 통폐합으로 옮긴 객체 — **받는 쪽에 「더 이상 안 보임」 으로 알린다.**

코어 API 는 보이던 부서에서 옮겨 간 객체를 무덤(`hidden`)으로 알린다. 그 판정은 객체마다의 소유
부서 기록을 보는데, 통폐합(`workspaces.reassign`)은 통폐합 한 줄만 남겨 여기 안 걸렸다 — 원본
부서만 보던 토큰의 수신 측은 옮겨 간 객체를 영영 살아 있는 것으로 들었다(2026-10-08).

토큰 주인의 부서로 가른다: 옮겨 가서 이제 안 보이는 자격(무덤) · 옮겨 왔지만 원래도 보이던
자격(그냥 바뀐 행) · 옮겨 와서 새로 보이는 자격(새 행) · 어느 쪽도 못 보는 자격(아무것도 —
식별자도 안 샌다).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.workspaces.models import Workspace, WorkspaceMember
from tests.api.conftest import Signed, _login, _make_user
from tests.api.test_core_api import _open
from tests.api.test_ontology import _make_object, _make_type


def _workspace(client: TestClient, admin: Signed, db: Session, name: str) -> Workspace:
    made = client.post(
        "/api/workspaces",
        json={"slug": f"ws-{uuid.uuid4().hex[:8]}", "name": name},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return db.scalars(select(Workspace).where(Workspace.slug == made.json()["slug"])).one()


def _reader(
    client: TestClient, db: Session, home: Workspace, *also: Workspace
) -> dict[str, str]:
    """그 부서들에 든 사람 하나의 자격. 무엇이 보이나는 **그 주인의 소속**이 정한다 — 바깥에 준
    토큰도 그 주인의 것이다(`visible_owner_clause`)."""
    user = _make_user(db, home, label="reader", is_system_admin=False, role="member")
    for one in also:
        db.add(WorkspaceMember(workspace_id=one.id, user_id=user.id, role="member"))
    db.commit()
    return {"Authorization": f"Bearer {_login(client, user.email)}"}


def _pull(client: TestClient, headers: dict[str, str], slug: str, **params: Any) -> Any:
    got = client.get(f"/api/core/{slug}", params=params, headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def test_통폐합으로_옮겨_간_객체는_토큰의_부서에_따라_무덤_또는_바뀐_행으로_간다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    _open(client, admin, vendor)
    source = _workspace(client, admin, db, "없어지는 팀")
    target = _workspace(client, admin, db, "합쳐지는 팀")
    elsewhere = _workspace(client, admin, db, "남의 팀")

    moving = _make_object(
        client, admin, vendor, label="옮길 공급사", key="MOV-1", workspace_slug=source.slug
    )
    _make_object(
        client, admin, vendor, label="원래 거기", key="THERE-1", workspace_slug=target.slug
    )

    only_source = _reader(client, db, source)
    both = _reader(client, db, source, target)
    only_target = _reader(client, db, target)
    neither = _reader(client, db, elsewhere)
    marks = {
        name: _pull(client, headers, vendor)["as_of"]
        for name, headers in {
            "source": only_source,
            "both": both,
            "target": only_target,
            "neither": neither,
        }.items()
    }

    moved = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": target.slug, "kinds": ["objects"]},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == {"objects": 1}

    # 원본 부서만 보던 자격 — **이제 안 보인다**: 이름 · 칸 없이 식별자만 실은 무덤.
    gone = _pull(client, only_source, vendor, since=marks["source"])["items"]
    grave = next(one for one in gone if one["key"] == "MOV-1")
    assert grave["deleted"] is True and grave["hidden"] is True
    assert grave["label"] == "MOV-1" and grave["properties"] == {}
    assert all(one["key"] != "THERE-1" for one in gone)  # 대상 부서 것은 새지 않는다

    # 두 부서를 다 보던 자격 — 옮겨 왔지만 **원래도 보였다**: 그냥 바뀐 행이다.
    seen = next(
        one
        for one in _pull(client, both, vendor, since=marks["both"])["items"]
        if one["key"] == "MOV-1"
    )
    assert seen["deleted"] is False and seen["hidden"] is False
    assert seen["label"] == "옮길 공급사"

    # 대상 부서만 보던 자격 — 옮겨 와서 **새로 보인다**: 새 행으로 온다(무덤이 아니다).
    fresh = next(
        one
        for one in _pull(client, only_target, vendor, since=marks["target"])["items"]
        if one["key"] == "MOV-1"
    )
    assert fresh["deleted"] is False and fresh["hidden"] is False

    # 어느 쪽도 못 보던 자격에게는 아무것도 안 간다 — 무덤으로도 식별자가 새지 않는다.
    assert all(
        one["key"] != "MOV-1"
        for one in _pull(client, neither, vendor, since=marks["neither"])["items"]
    )

    # 객체의 이력에도 선다 — 누가 · 어느 부서에서 어디로(통폐합 한 줄로는 안 보였다).
    history = client.get(
        f"/api/objects/{vendor}/{moving['id']}/history", headers=admin.headers
    ).json()
    entry = next(one for one in history if "owner_workspace_id" in one["changes"])
    assert entry["changes"]["owner_workspace_id"] == {
        "before": "없어지는 팀",
        "after": "합쳐지는 팀",
    }
    assert "부서 통폐합" in (entry["reason"] or "") and entry["actor_label"] == "admin"


def test_통폐합의_객체_기록은_한_문장으로_옮긴_수만큼_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """수십만 개를 옮기는 통폐합에서 줄마다 파이썬으로 기록을 만들면 그 목록만으로 수백 MB 다
    — DB 안의 한 문장(`INSERT … SELECT`)으로 남긴다. 줄마다 id 가 다르고(파이썬 기본값을 쓰면
    한 문장에서 한 번만 불려 모두 같은 id 가 된다), 지운 객체도 함께 옮긴 만큼 남는다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    source = _workspace(client, admin, db, "원본")
    target = _workspace(client, admin, db, "대상")
    made = [
        _make_object(
            client,
            admin,
            vendor,
            label=f"공급사 {n}",
            key=f"B-{n}",
            workspace_slug=source.slug,
        )
        for n in range(5)
    ]
    gone = client.delete(f"/api/objects/{vendor}/{made[0]['id']}", headers=admin.headers)
    assert gone.status_code in (200, 204), gone.text

    moved = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": target.slug, "kinds": ["objects"]},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == {"objects": 5}

    ids = [uuid.UUID(one["id"]) for one in made]
    rows = list(
        db.execute(
            select(AuditEntry.id, AuditEntry.target_id, AuditEntry.workspace_id).where(
                AuditEntry.target_table == "objects",
                AuditEntry.target_id.in_(ids),
                AuditEntry.changes.has_key("owner_workspace_id"),
            )
        )
    )
    assert sorted(one.target_id for one in rows) == sorted(ids)
    assert len({one.id for one in rows}) == 5
    assert {one.workspace_id for one in rows} == {target.id}
    # 바깥(웹훅 · 지켜보기)에는 통폐합 한 줄만 — 줄마다 알리지 않는다.
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(
                AuditEntry.action == "workspace.reassigned",
                AuditEntry.target_id == source.id,
            )
        )
        == 1
    )
