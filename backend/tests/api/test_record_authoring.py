"""기록은 구성원도 넣는다(ADR 0036) — 넣은 사람이 고치고, 축은 지금처럼 관리자가.

관리자만 넣게 두면 보고 넣는 사람이 관리자에게 부탁해야 한다. 사용자 안내서는 「멤버: 보고
넣음」 이라고 적었는데 코드는 관리자만 받았다(2026-10-23).
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modules.workspaces.models import Workspace, WorkspaceMember
from tests.api.conftest import Signed, _login, _make_user
from tests.api.test_ontology import _link, _make_object, _make_relation, _make_type


@pytest.fixture
def kinds(client: TestClient, admin: Signed) -> dict[str, str]:
    return {
        "log": _make_type(client, admin, label="변경점", usage="log"),
        "axis": _make_type(client, admin, label="모델"),
    }


def _peer(client: TestClient, db: Session, workspace_slug: str) -> Signed:
    """같은 부서의 다른 멤버."""
    workspace = db.query(Workspace).filter(Workspace.slug == workspace_slug).one()
    user = _make_user(db, workspace, label="peer", is_system_admin=False, role="member")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)


def _profile(client: TestClient, who: Signed, slug: str, object_id: str) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_멤버가_자기_부서에_기록을_넣고_고친다(
    client: TestClient, member: Signed, kinds: dict[str, str]
) -> None:
    made = _make_object(client, member, kinds["log"], label="커넥터 변경")
    assert _profile(client, member, kinds["log"], made["id"])["can_edit"] is True
    patched = client.patch(
        f"/api/objects/{kinds['log']}/{made['id']}",
        json={"label": "커넥터 사양 변경"},
        headers=member.headers,
    )
    assert patched.status_code == 200, patched.text


def test_축은_지금처럼_관리자만_넣는다(
    client: TestClient, member: Signed, kinds: dict[str, str]
) -> None:
    got = client.post(
        f"/api/objects/{kinds['axis']}",
        json={"label": "모델 X", "workspace_slug": member.workspace},
        headers=member.headers,
    )
    assert got.status_code == 403


def test_구성원이_아닌_부서와_전역에는_못_넣는다(
    client: TestClient, db: Session, member: Signed, kinds: dict[str, str]
) -> None:
    other = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="남의 팀")
    db.add(other)
    db.commit()
    elsewhere = client.post(
        f"/api/objects/{kinds['log']}",
        json={"label": "남의 기록", "workspace_slug": other.slug},
        headers=member.headers,
    )
    assert elsewhere.status_code == 403
    assert "남의 팀" in elsewhere.json()["error"]["message"]
    nowhere = client.post(
        f"/api/objects/{kinds['log']}", json={"label": "전역 기록"}, headers=member.headers
    )
    assert nowhere.status_code == 403


def test_남이_넣은_기록은_넣은_사람과_관리자만_고친다(
    client: TestClient,
    db: Session,
    member: Signed,
    manager: Signed,
    kinds: dict[str, str],
) -> None:
    made = _make_object(client, member, kinds["log"], label="하네스 변경")
    peer = _peer(client, db, member.workspace)
    path = f"/api/objects/{kinds['log']}/{made['id']}"
    assert _profile(client, peer, kinds["log"], made["id"])["can_edit"] is False
    denied = client.patch(path, json={"label": "몰래"}, headers=peer.headers)
    assert denied.status_code == 403
    assert "넣은 사람과 부서 관리자" in denied.json()["error"]["message"]
    assert client.delete(path, headers=peer.headers).status_code == 403
    assert client.patch(path, json={"label": "관리자가"}, headers=manager.headers).is_success


def test_부서를_떠나면_넣은_기록도_못_고친다(
    client: TestClient, db: Session, member: Signed, kinds: dict[str, str]
) -> None:
    made = _make_object(client, member, kinds["log"], label="떠나기 전")
    db.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id
            == db.query(Workspace.id)
            .filter(Workspace.slug == member.workspace)
            .scalar_subquery()
        )
    )
    db.commit()
    got = client.patch(
        f"/api/objects/{kinds['log']}/{made['id']}",
        json={"label": "떠난 뒤"},
        headers=member.headers,
    )
    assert got.status_code == 403


def test_내_기록을_지우되_참조를_끊는_삭제는_관리자의_일이다(
    client: TestClient, member: Signed, kinds: dict[str, str]
) -> None:
    gone = _make_object(client, member, kinds["log"], label="지울 것")
    path = f"/api/objects/{kinds['log']}/{gone['id']}"
    assert client.delete(path, headers=member.headers).status_code == 204
    kept = _make_object(client, member, kinds["log"], label="끊고 지울 것")
    detach = client.delete(
        f"/api/objects/{kinds['log']}/{kept['id']}",
        params={"mode": "detach"},
        headers=member.headers,
    )
    assert detach.status_code == 403


def test_내_기록에서_관계를_잇고_첨부를_붙인다(
    client: TestClient, admin: Signed, member: Signed, kinds: dict[str, str]
) -> None:
    """설명회 기록이 변경점을 잇는 모양 — 출발점(내 기록)만 고칠 수 있으면 된다."""
    meeting = _make_type(client, admin, label="설명회", usage="log")
    covers = _make_relation(
        client, admin, base="covers", src_type_slugs=[meeting], dst_type_slugs=[kinds["log"]]
    )
    mine = _make_object(client, member, meeting, label="10월 설명회")
    change = _make_object(client, member, kinds["log"], label="커넥터 변경")
    linked = _link(client, member, meeting, mine["id"], covers, change["id"])
    assert linked.status_code == 201, linked.text

    attached = client.post(
        "/api/attachments",
        data={"owner_table": "objects", "owner_id": mine["id"]},
        files={"file": ("회의록.txt", io.BytesIO(b"notes"), "text/plain")},
        headers=member.headers,
    )
    assert attached.status_code == 201, attached.text
