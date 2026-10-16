"""게시판(ADR 0033) — 누구나 읽고 쓰고, 수정 · 삭제는 작성자와 시스템 관리자, 고정은 시스템
관리자, 첨부는 글의 작성자가 붙이고 뗀다, 내 글의 댓글은 알림으로 온다.

부서 관리자(`manager`)로도 본다 — 게시판은 부서의 것이 아니라서 부서 관리자도 남의 글을 못
고친다. 관리자만으로 돌리는 시험은 그 문턱이 있는지 보지 못한다.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.files.models import Attachment
from app.modules.notifications import services as notifications
from app.shared.errors import code
from tests.api.conftest import Signed, notifications_of


def _post(client: TestClient, who: Signed, **body: Any) -> dict[str, Any]:
    made = client.post(
        "/api/board/posts",
        json={"title": body.get("title", "서버 점검 일정"), "body": body.get("body", "본문")},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def _attach(client: TestClient, who: Signed, post_id: str, name: str = "회의록.txt") -> Any:
    return client.post(
        "/api/attachments",
        data={"owner_table": "board_posts", "owner_id": post_id},
        files={"file": (name, io.BytesIO("내용".encode()), "text/plain")},
        headers=who.headers,
    )


def _listed(client: TestClient, who: Signed, **params: Any) -> dict[str, Any]:
    got = client.get("/api/board/posts", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_누구나_작성하고_수정_삭제는_작성자와_시스템_관리자(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    post = _post(client, member, title="견본 위치 문의", body="3층 창고에 있나요?")
    assert post["author_name"] == "member" and post["can_edit"] is True
    assert post["comments"] == [] and post["edited_at"] is None

    # 남(부서 관리자라도)은 수정 · 삭제 못 한다 — 단추도 안 보인다.
    seen = client.get(f"/api/board/posts/{post['id']}", headers=manager.headers).json()
    assert seen["can_edit"] is False
    refused = client.patch(
        f"/api/board/posts/{post['id']}", json={"title": "바꿈"}, headers=manager.headers
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == code("BOARD", 4)
    assert (
        client.delete(f"/api/board/posts/{post['id']}", headers=manager.headers).status_code
        == 403
    )

    # 작성자는 부분 수정 — 안 보낸 본문은 그대로.
    edited = client.patch(
        f"/api/board/posts/{post['id']}",
        json={"title": "견본 위치 문의(해결)"},
        headers=member.headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "견본 위치 문의(해결)"
    assert edited.json()["body"] == "3층 창고에 있나요?"
    assert edited.json()["edited_at"] is not None

    # 시스템 관리자는 남의 글을 삭제한다 — 감사에 남는다.
    removed = client.delete(f"/api/board/posts/{post['id']}", headers=admin.headers)
    assert removed.status_code == 204
    gone = client.get(f"/api/board/posts/{post['id']}", headers=member.headers)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == code("BOARD", 1)
    assert post["id"] not in {one["id"] for one in _listed(client, member)["items"]}


def test_삭제는_감사에_남고_첨부도_함께_내려간다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    post = _post(client, member)
    attached = _attach(client, member, post["id"])
    assert attached.status_code == 201, attached.text
    attachment_id = attached.json()["id"]
    assert (
        client.get(
            f"/api/attachments/{attachment_id}/content", headers=admin.headers
        ).status_code
        == 200
    )

    assert (
        client.delete(f"/api/board/posts/{post['id']}", headers=admin.headers).status_code
        == 204
    )
    # 글이 없어졌는데 파일이 id 로 계속 내려받아지면 지운 사람의 뜻과 다르다.
    assert (
        client.get(
            f"/api/attachments/{attachment_id}/content", headers=member.headers
        ).status_code
        == 404
    )
    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "board.post.delete",
            AuditEntry.target_id == uuid.UUID(post["id"]),
        )
    )
    assert entry is not None and entry.changes == {"author": "member", "attachments": 1}


def test_첨부는_글의_작성자가_업로드하고_삭제한다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed, db: Session
) -> None:
    """첨부는 전역(모두가 본다)이지만 붙이는 사람은 작성자 — 첨부의 예전 규칙(전역은 시스템
    관리자만)이면 작성자가 자기 글에 파일을 못 붙였다."""
    post = _post(client, member)
    made = _attach(client, member, post["id"], name="절차서.pdf")
    assert made.status_code == 201, made.text
    row = db.get(Attachment, uuid.UUID(made.json()["id"]))
    assert row is not None and row.workspace_id is None  # 전역 — 모두가 본다

    # 남은 못 붙이고 못 뗀다.
    refused = _attach(client, manager, post["id"])
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == code("BOARD", 3)
    assert (
        client.delete(
            f"/api/attachments/{made.json()['id']}", headers=manager.headers
        ).status_code
        == 403
    )
    # 모두가 본다 — 목록 · 글의 첨부 수 · 내려받기.
    listed = client.get(
        "/api/attachments",
        params={"owner_table": "board_posts", "owner_id": post["id"]},
        headers=manager.headers,
    )
    assert [one["original_name"] for one in listed.json()] == ["절차서.pdf"]
    row = next(one for one in _listed(client, manager)["items"] if one["id"] == post["id"])
    assert row["attachment_count"] == 1
    assert (
        client.get(
            f"/api/attachments/{made.json()['id']}/content", headers=manager.headers
        ).status_code
        == 200
    )
    # 작성자 · 시스템 관리자는 뗀다.
    assert (
        client.delete(
            f"/api/attachments/{made.json()['id']}", headers=member.headers
        ).status_code
        == 204
    )
    again = _attach(client, admin, post["id"])
    assert again.status_code == 201
    assert (
        client.delete(
            f"/api/attachments/{again.json()['id']}", headers=admin.headers
        ).status_code
        == 204
    )
    # 없는 글 · 지운 글에는 못 붙인다.
    assert _attach(client, member, str(uuid.uuid4())).status_code == 404


def test_댓글은_작성자에게_알리고_내_댓글은_안_알린다(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    post = _post(client, member, title="빌드 서버 느림")
    before = len(notifications_of(client, member, notifications.BOARD_COMMENT))
    mine = client.post(
        f"/api/board/posts/{post['id']}/comments",
        json={"body": "제가 먼저 봅니다"},
        headers=member.headers,
    )
    assert mine.status_code == 201, mine.text
    assert len(notifications_of(client, member, notifications.BOARD_COMMENT)) == before

    other = client.post(
        f"/api/board/posts/{post['id']}/comments",
        json={"body": "어제부터 그렇습니다"},
        headers=manager.headers,
    )
    assert other.status_code == 201, other.text
    got = notifications_of(client, member, notifications.BOARD_COMMENT)
    assert len(got) == before + 1
    assert got[0]["link"] == f"/board/{post['id']}" and "빌드 서버 느림" in got[0]["title"]

    seen = client.get(f"/api/board/posts/{post['id']}", headers=member.headers).json()
    assert [one["body"] for one in seen["comments"]] == [
        "제가 먼저 봅니다",
        "어제부터 그렇습니다",
    ]
    assert [one["can_edit"] for one in seen["comments"]] == [True, False]
    assert seen["comment_count"] == 2

    # 남의 댓글은 못 고친다. 시스템 관리자가 지우면 감사에 남는다(목록에서는 빠진다).
    comment_id = other.json()["id"]
    assert (
        client.patch(
            f"/api/board/comments/{comment_id}", json={"body": "x"}, headers=member.headers
        ).status_code
        == 403
    )
    edited = client.patch(
        f"/api/board/comments/{comment_id}",
        json={"body": "어제 오후부터"},
        headers=manager.headers,
    )
    assert edited.status_code == 200 and edited.json()["edited_at"] is not None
    assert (
        client.delete(f"/api/board/comments/{comment_id}", headers=admin.headers).status_code
        == 204
    )
    seen = client.get(f"/api/board/posts/{post['id']}", headers=member.headers).json()
    assert [one["body"] for one in seen["comments"]] == ["제가 먼저 봅니다"]
    assert seen["comment_count"] == 1


def test_고정은_시스템_관리자만_하고_목록_맨_위에_선다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    tag = uuid.uuid4().hex[:8]
    first = _post(client, member, title=f"{tag} 먼저 쓴 글")
    second = _post(client, member, title=f"{tag} 나중 쓴 글")
    order = [one["id"] for one in _listed(client, member, q=tag)["items"]]
    assert order == [second["id"], first["id"]]  # 새 글부터

    refused = client.put(
        f"/api/board/posts/{first['id']}/pinned", json={"pinned": True}, headers=member.headers
    )
    assert refused.status_code == 403
    pinned = client.put(
        f"/api/board/posts/{first['id']}/pinned", json={"pinned": True}, headers=admin.headers
    )
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    order = [one["id"] for one in _listed(client, member, q=tag)["items"]]
    assert order == [first["id"], second["id"]]
    assert db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "board.post.pin",
            AuditEntry.target_id == uuid.UUID(first["id"]),
        )
    )


def test_찾기와_내_글과_쪽_넘기기(client: TestClient, admin: Signed, member: Signed) -> None:
    tag = uuid.uuid4().hex[:8]
    _post(client, member, title=f"{tag} 제목에", body="본문")
    _post(client, admin, title="다른 제목", body=f"본문에 {tag}")
    _post(client, member, title="상관없는 글", body="본문")
    found = _listed(client, member, q=tag)
    assert found["total"] == 2
    mine = _listed(client, member, q=tag, mine=True)
    assert [one["title"] for one in mine["items"]] == [f"{tag} 제목에"]
    # 「%」 · 「_」 는 글자 그대로 찾는다.
    assert _listed(client, member, q=f"{tag}%")["total"] == 0
    page = _listed(client, member, q=tag, limit=1, offset=1)
    assert page["total"] == 2 and len(page["items"]) == 1 and page["limit"] == 1
    assert "body" not in page["items"][0]  # 목록에는 본문을 싣지 않는다


def test_빈_제목과_긴_본문은_받지_않고_토큰으로는_읽기만(
    client: TestClient, admin: Signed
) -> None:
    assert (
        client.post(
            "/api/board/posts", json={"title": "", "body": "x"}, headers=admin.headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/board/posts",
            json={"title": "t", "body": "x" * 20_001},
            headers=admin.headers,
        ).status_code
        == 422
    )
    made = client.post(
        "/api/auth/tokens",
        json={"name": "읽기", "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    token = {"Authorization": f"Bearer {made.json()['token']}"}
    assert client.get("/api/board/posts", headers=token).status_code == 200
    refused = client.post("/api/board/posts", json={"title": "t", "body": "b"}, headers=token)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == code("AUTH", 105)
