"""첨부 — 올리고, 받고, 떼고, **못 볼 것은 못 본다.**"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.modules.files import services
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed


def _upload(
    client: TestClient,
    who: Signed,
    *,
    content: bytes = b"hello",
    name: str = "보고서.txt",
    workspace: str | None = None,
) -> Response:
    data = {"owner_table": "parts", "owner_id": "11111111-1111-1111-1111-111111111111"}
    if workspace is not None:
        data["workspace_slug"] = workspace
    response: Response = client.post(
        "/api/attachments",
        data=data,
        files={"file": (name, io.BytesIO(content), "text/plain")},
        headers=who.headers,
    )
    return response


def test_올리고_받으면_같은_내용이다(client: TestClient, admin: Signed) -> None:
    """**우리가 실제로 쓴 바이트를 센다** — 브라우저가 보낸 값을 믿지 않는다."""
    created = _upload(client, admin, content=b"hello world", workspace=admin.workspace)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["size_bytes"] == 11
    assert body["original_name"] == "보고서.txt"

    got = client.get(f"/api/attachments/{body['id']}/content", headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.content == b"hello world"
    # 한글 이름이 내려받기에 살아 있어야 한다.
    assert "filename*=UTF-8''" in got.headers["content-disposition"]


def test_같은_내용은_한_번만_저장된다(client: TestClient, admin: Signed) -> None:
    """저장 이름이 내용의 해시다. **행은 둘이고 파일은 하나다.**"""
    first = _upload(client, admin, content=b"same bytes", workspace=admin.workspace)
    second = _upload(
        client, admin, content=b"same bytes", name="다른이름.txt", workspace=admin.workspace
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["sha256"] == second.json()["sha256"]
    assert first.json()["id"] != second.json()["id"]


def test_빈_파일은_거절한다(client: TestClient, admin: Signed) -> None:
    response = _upload(client, admin, content=b"", workspace=admin.workspace)
    assert response.status_code == 400


def test_상한을_서버가_강제한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**화면의 검사는 우회할 수 있다.** 우회되면 디스크가 차고, 디스크가 차면
    앱만이 아니라 DB 도 함께 멈춘다."""
    monkeypatch.setattr(services, "MAX_BYTES", 8)
    response = _upload(client, admin, content=b"much too long", workspace=admin.workspace)
    assert response.status_code == 413
    assert response.json()["error"]["details"]["max_bytes"] == 8


def test_전역_첨부는_시스템_관리자만(client: TestClient, member: Signed) -> None:
    """전역은 여러 부서가 함께 본다 — 한 부서가 붙이면 다른 부서의 화면이 바뀐다."""
    response = _upload(client, member, workspace=None)
    assert response.status_code == 403


def test_남의_부서_첨부는_없는_것과_같다(
    client: TestClient, db: Session, admin: Signed, member: Signed
) -> None:
    """**403 으로 가르면 그 id 가 존재한다는 사실이 샌다.**

    시스템 관리자가 다른 부서에 붙인 첨부를, 그 부서가 아닌 멤버는 못 본다.
    """
    other = Workspace(slug="other-team", name="다른팀")
    db.add(other)
    db.commit()

    created = _upload(client, admin, workspace=other.slug)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]

    # member 는 other-team 소속이 아니다.
    assert (
        client.get(
            f"/api/attachments/{attachment_id}/content", headers=member.headers
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/attachments/{attachment_id}", headers=member.headers).status_code
        == 404
    )


def test_목록은_붙은_자료별로_나온다(client: TestClient, admin: Signed) -> None:
    _upload(client, admin, name="하나.txt", content=b"one", workspace=admin.workspace)
    rows = client.get(
        "/api/attachments",
        params={"owner_table": "parts", "owner_id": "11111111-1111-1111-1111-111111111111"},
        headers=admin.headers,
    )
    assert rows.status_code == 200, rows.text
    assert len(rows.json()) >= 1

    # 다른 자료에는 안 붙어 있다.
    empty = client.get(
        "/api/attachments",
        params={"owner_table": "parts", "owner_id": "22222222-2222-2222-2222-222222222222"},
        headers=admin.headers,
    )
    assert empty.json() == []


def test_떼면_목록에서_빠진다(client: TestClient, admin: Signed) -> None:
    """**행만 지운다 — 파일은 안 지운다.** 같은 내용을 다른 행이 가리킬 수 있다."""
    created = _upload(client, admin, content=b"to be removed", workspace=admin.workspace)
    attachment_id = created.json()["id"]

    assert (
        client.delete(f"/api/attachments/{attachment_id}", headers=admin.headers).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/attachments/{attachment_id}/content", headers=admin.headers
        ).status_code
        == 404
    )


def test_부서_삭제_확인에_첨부가_뜬다(client: TestClient, admin: Signed) -> None:
    """**등록하지 않으면 안 뜬다.** 안 뜨면 사람은 아무것도 안 걸린 줄 알고
    지우려 하는데, FK 가 RESTRICT 라 서버가 500 을 낸다."""
    _upload(client, admin, content=b"blocks delete", workspace=admin.workspace)

    references = client.get(
        f"/api/workspaces/{admin.workspace}/references", headers=admin.headers
    )
    assert references.status_code == 200, references.text
    found = next(one for one in references.json() if one["table"] == "attachments")
    assert found["count"] >= 1
    assert found["blocks_delete"] is True


def test_서버_화면이_첨부_수를_센다(client: TestClient, admin: Signed) -> None:
    _upload(client, admin, content=b"counted", workspace=admin.workspace)
    counts = client.get("/api/server/status", headers=admin.headers).json()["counts"]
    assert any(one["label"] == "첨부" and one["count"] >= 1 for one in counts)
