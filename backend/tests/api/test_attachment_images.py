"""이미지 첨부 — **서버가 열어 보고** 이미지로 읽은 것만 그림이다(ADR 0012).

- 올린 쪽이 붙인 `Content-Type` 은 판정에 안 쓴다 — HTML 을 `image/png` 라고 보낼 수 있다.
- 미리보기(WebP, 긴 변 320)는 서버가 만들고, 휴대폰 사진의 EXIF 회전을 반영해 세운다.
- 파일 칸의 「이미지만」 은 서버가 이미지로 못 읽은 것을 거절한다.
- 객체에 붙일 때 객체가 있나 · 고칠 수 있나 · 파일 칸인가를 객체 쪽이 답하고, 붙이고 뗀 일은
  객체의 이력에 남는다.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.files.models import Attachment
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _png(width: int = 640, height: int = 480) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


def _rotated_jpeg() -> bytes:
    """픽셀은 가로(400 x 200)로 누웠고 EXIF 가 「시계 방향 90° 돌려 보라」(6) — 휴대폰 사진."""
    image = Image.new("RGB", (400, 200), (10, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def _world(client: TestClient, admin: Signed, *, accept: str | None = None) -> dict[str, Any]:
    kind = _make_type(client, admin, label="시험 결과")
    photo: dict[str, Any] = {"key": "photo", "label": "사진", "data_type": "file"}
    if accept is not None:
        photo["accept"] = accept
    _make_property(client, admin, kind, **photo)
    _make_property(client, admin, kind, key="memo", label="메모", data_type="text")
    made = _make_object(client, admin, kind, label=f"결과 {uuid.uuid4().hex[:4]}")
    return {"type": kind, "id": made["id"]}


def _attach(
    client: TestClient,
    who: Signed | dict[str, str],
    owner_id: str,
    *,
    content: bytes,
    name: str = "사진.png",
    claimed: str = "application/octet-stream",
    field: str | None = "photo",
) -> Response:
    data = {"owner_table": "objects", "owner_id": owner_id}
    if field is not None:
        data["owner_field"] = field
    headers = who.headers if isinstance(who, Signed) else who
    response: Response = client.post(
        "/api/attachments",
        data=data,
        files={"file": (name, io.BytesIO(content), claimed)},
        headers=headers,
    )
    return response


def test_사진은_서버가_이미지로_읽고_미리보기를_낸다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    made = _attach(client, admin, w["id"], content=_png(), claimed="application/octet-stream")
    assert made.status_code == 201, made.text
    body = made.json()
    # 올린 쪽은 octet-stream 이라 했지만 서버가 열어 보고 PNG 로 정했다.
    assert body["is_image"] is True
    assert body["content_type"] == "image/png"
    assert (body["width"], body["height"]) == (640, 480)

    thumb = client.get(f"/api/attachments/{body['id']}/thumbnail", headers=admin.headers)
    assert thumb.status_code == 200, thumb.text
    assert thumb.headers["content-type"] == "image/webp"
    small = Image.open(io.BytesIO(thumb.content))
    assert small.format == "WEBP"
    assert max(small.size) == 320

    original = client.get(f"/api/attachments/{body['id']}/content", headers=admin.headers)
    assert original.headers["content-type"] == "image/png"
    assert original.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in original.headers["content-security-policy"]
    assert original.headers["cache-control"].startswith("private")

    # 상세 응답의 첨부 요약도 같은 판정을 싣는다 — 바이트는 없다.
    profile = client.get(f"/api/objects/{w['type']}/{w['id']}", headers=admin.headers).json()
    (brief,) = profile["attachments"]
    assert brief["is_image"] is True and brief["content_type"] == "image/png"


def test_이미지인_척하는_파일은_그림이_아니다(client: TestClient, admin: Signed) -> None:
    """HTML 을 `image/png` 라고 보내면 — 그 말을 믿고 그림으로 띄우면 스크립트가 앱의 주소에서
    돈다. SVG 도 글로 된 문서라 스크립트를 품는다 — 내려받기만 된다."""
    w = _world(client, admin)
    for content, name, claimed in (
        (b"<html><script>alert(1)</script></html>", "가짜.png", "image/png"),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "그림.svg",
            "image/svg+xml",
        ),
        (_png()[:60], "잘린.png", "image/png"),  # 머리만 PNG — 몸통이 없다
    ):
        made = _attach(client, admin, w["id"], content=content, name=name, claimed=claimed)
        assert made.status_code == 201, made.text
        assert made.json()["is_image"] is False, name
        thumb = client.get(
            f"/api/attachments/{made.json()['id']}/thumbnail", headers=admin.headers
        )
        assert thumb.status_code == 404
        assert thumb.json()["error"]["code"].endswith("FILES-0008")


def test_휴대폰_사진의_회전을_반영해_세운다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    made = _attach(client, admin, w["id"], content=_rotated_jpeg(), name="폰.jpg")
    body = made.json()
    assert body["is_image"] is True and body["content_type"] == "image/jpeg"
    # 눕힌 픽셀(400 x 200)이 아니라 세운 크기.
    assert (body["width"], body["height"]) == (200, 400)
    thumb = client.get(f"/api/attachments/{body['id']}/thumbnail", headers=admin.headers)
    assert Image.open(io.BytesIO(thumb.content)).size == (160, 320)


def test_이미지만_받는_칸은_서버가_못_읽은_것을_거절한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin, accept="image")
    refused = _attach(
        client, admin, w["id"], content=b"%PDF-1.7 ...", name="성적서.pdf", claimed="image/png"
    )
    assert refused.status_code == 415, refused.text
    assert refused.json()["error"]["code"].endswith("FILES-0007")
    assert _attach(client, admin, w["id"], content=_png()).status_code == 201
    # 그 밖의 첨부(자리 없음)는 칸의 규칙을 안 따른다.
    other = _attach(client, admin, w["id"], content=b"%PDF", name="성적서.pdf", field=None)
    assert other.status_code == 201, other.text


def test_받는_종류는_파일_칸에만(client: TestClient, admin: Signed) -> None:
    kind = _make_type(client, admin, label="부품")
    wrong = client.post(
        f"/api/ontology/types/{kind}/properties",
        json={"key": "memo", "label": "메모", "data_type": "text", "accept": "image"},
        headers=admin.headers,
    )
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["error"]["code"].endswith("ONTOLOGY-0087")
    unknown = client.post(
        f"/api/ontology/types/{kind}/properties",
        json={"key": "clip", "label": "영상", "data_type": "file", "accept": "video"},
        headers=admin.headers,
    )
    assert unknown.status_code >= 400
    made = _make_property(
        client, admin, kind, key="photo", label="사진", data_type="file", accept="image"
    )
    assert made["accept"] == "image"
    # 정의 파일로 내보내고 다시 읽어도 남는다.
    exported = client.get(
        "/api/ontology/export", params={"format": "json"}, headers=admin.headers
    )
    assert exported.status_code == 200, exported.text
    types = exported.json()["types"]
    (mine,) = [one for one in types if one["slug"] == kind]
    (photo,) = [one for one in mine["properties"] if one["key"] == "photo"]
    assert photo["accept"] == "image"


def test_붙일_자리는_객체가_답한다(client: TestClient, admin: Signed, member: Signed) -> None:
    """예전에는 부서 관리자면 아무 id · 아무 자리에 붙었다 — 없는 객체, 파일 칸이 아닌
    칸에도."""
    w = _world(client, admin)
    missing = _attach(client, admin, str(uuid.uuid4()), content=_png())
    assert missing.status_code == 404
    not_file = _attach(client, admin, w["id"], content=_png(), field="memo")
    assert not_file.status_code == 409
    assert not_file.json()["error"]["code"].endswith("OBJECTS-0097")
    no_field = _attach(client, admin, w["id"], content=_png(), field="nothing")
    assert no_field.status_code == 409
    # 부서원은 볼 수는 있어도 고칠 수 없다.
    assert _attach(client, member, w["id"], content=_png()).status_code == 403


def test_붙이고_뗀_일이_객체_이력에_남는다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    made = _attach(client, admin, w["id"], content=_png(), name="현장.png").json()
    removed = client.delete(f"/api/attachments/{made['id']}", headers=admin.headers)
    assert removed.status_code == 204, removed.text

    history = client.get(
        f"/api/objects/{w['type']}/{w['id']}/history", headers=admin.headers
    ).json()
    actions = [one["action"] for one in history]
    assert actions[:2] == ["object.attachment.remove", "object.attachment.add"]
    added = {"attachments.photo": {"before": None, "after": "현장.png"}}
    gone = {"attachments.photo": {"before": "현장.png", "after": None}}
    assert history[1]["changes"] == added
    assert history[0]["changes"] == gone


def test_칸이_없어진_뒤에도_붙은_것은_뗄_수_있다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    made = _attach(client, admin, w["id"], content=_png()).json()
    gone = client.delete(
        f"/api/ontology/types/{w['type']}/properties/photo", headers=admin.headers
    )
    assert gone.status_code in (200, 204), gone.text
    removed = client.delete(f"/api/attachments/{made['id']}", headers=admin.headers)
    assert removed.status_code == 204, removed.text


def test_개인_토큰은_객체_쓰기_범위로_붙인다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)

    def token(scopes: list[str]) -> dict[str, str]:
        made = client.post(
            "/api/auth/tokens",
            json={"name": f"attach_{uuid.uuid4().hex[:6]}", "scopes": scopes},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text
        return {"Authorization": f"Bearer {made.json()['token']}"}

    reader = token(["read"])
    refused = _attach(client, reader, w["id"], content=_png())
    assert refused.status_code == 403
    assert refused.json()["error"]["code"].endswith("AUTH-0106")

    writer = token(["read", "objects:write"])
    made = _attach(client, writer, w["id"], content=_png())
    assert made.status_code == 201, made.text
    removed = client.delete(f"/api/attachments/{made.json()['id']}", headers=writer)
    assert removed.status_code == 204, removed.text


def test_판별_전에_올라온_첨부는_처음_보일_때_본다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """마이그레이션은 파일을 안 읽는다 — 옛 첨부는 판별 칸이 비어 있다가 목록에 처음 나올 때
    채워진다."""
    w = _world(client, admin)
    made = _attach(client, admin, w["id"], content=_png(320, 200)).json()
    db.execute(
        update(Attachment)
        .where(Attachment.id == uuid.UUID(made["id"]))
        .values(media=None, width=None, height=None, thumb_path=None, content_type="image/x")
    )
    db.commit()

    listed = client.get(
        "/api/attachments",
        params={"owner_table": "objects", "owner_id": w["id"], "owner_field": "photo"},
        headers=admin.headers,
    ).json()
    (row,) = listed
    assert row["is_image"] is True and row["content_type"] == "image/png"
    assert (row["width"], row["height"]) == (320, 200)
    thumb = client.get(f"/api/attachments/{made['id']}/thumbnail", headers=admin.headers)
    assert thumb.status_code == 200


def test_합치면_사진도_이긴_쪽으로_간다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    winner = _make_object(client, admin, w["type"], label="이긴 쪽")["id"]
    made = _attach(client, admin, w["id"], content=_png(), name="옮길.png").json()
    merged = client.post(
        f"/api/objects/{w['type']}/{w['id']}/merge",
        json={"into": winner},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    profile = client.get(f"/api/objects/{w['type']}/{winner}", headers=admin.headers).json()
    assert [one["id"] for one in profile["attachments"]] == [made["id"]]
    history = client.get(
        f"/api/objects/{w['type']}/{winner}/history", headers=admin.headers
    ).json()
    assert history[0]["action"] == "object.merge"


def test_객체의_부서가_바뀌면_첨부도_따라간다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """부서가 바뀌는 길이 여럿이라 트리거가 맞춘다 — 안 맞추면 새 부서 사람에게 사진이
    404 다."""
    w = _world(client, admin)
    made = _attach(client, admin, w["id"], content=_png()).json()
    moved = client.post(
        f"/api/objects/{w['type']}/bulk-edit",
        json={"ids": [w["id"]], "field": "workspace", "value": "", "apply": True},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["applied"] is True
    row = db.get(Attachment, uuid.UUID(made["id"]))
    assert row is not None and row.workspace_id is None
