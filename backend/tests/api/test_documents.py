"""문서 속성(ADR 0053) — 객체에 붙는 마크다운 문서와 그 판.

- 본문은 속성 칸이 아니라 문서 표에 — `properties` 로 보내면 거절한다.
- 고칠 때 보고 고친 판을 보낸다. 그사이 바뀌었으면 409 — 말없이 덮지 않는다(사람과 AI 가 같은
  문서를 고친다). 본문이 그대로면 판을 올리지 않는다.
- 판마다 누가 · 어느 토큰으로 고쳤는지 남는다.
- 본문의 링크 `/o/<타입>/<id>` 가 가리킨 객체의 상세에 「이 객체를 언급한 문서」 로 모인다.
- 값이 속성 칸에 없는 종류라 공통 속성 · 종류 변경 · 조건이 되지 않는다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _doc_type(client: TestClient, admin: Signed) -> str:
    slug = _make_type(client, admin, label="백서")
    _make_property(client, admin, slug, key="body", label="본문", data_type="document")
    return slug


def _url(slug: str, object_id: str, key: str = "body") -> str:
    return f"/api/objects/{slug}/{object_id}/documents/{key}"


def _put(client: TestClient, who: Signed, url: str, body: str, base: int, **kw: Any) -> Any:
    return client.put(
        url, json={"body": body, "base_revision": base, **kw}, headers=who.headers
    )


def test_판이_오르고_그사이_바뀌었으면_덮지_않는다(client: TestClient, admin: Signed) -> None:
    slug = _doc_type(client, admin)
    paper = _make_object(client, admin, slug, label="힌지 내구 백서")
    url = _url(slug, paper["id"])

    empty = client.get(url, headers=admin.headers).json()
    assert (empty["body"], empty["revision"], empty["can_edit"]) == ("", 0, True)

    first = _put(client, admin, url, "# 힌지\n\n내구 시험 조건", 0, note="처음")
    assert first.status_code == 200, first.text
    assert (first.json()["revision"], first.json()["chars"]) == (1, 14)
    assert first.json()["updated_via"] == ""  # 화면(세션)에서 고쳤다

    # 같은 본문이면 판을 올리지 않는다.
    same = _put(client, admin, url, "# 힌지\r\n\r\n내구 시험 조건", 1)
    assert same.json()["revision"] == 1

    # 판 0 을 보고 고친 쪽은 늦었다 — 지금 판을 알려 주고 덮지 않는다.
    late = _put(client, admin, url, "다른 내용", 0)
    assert late.status_code == 409
    assert late.json()["error"]["code"] == code("OBJECTS", 149)
    assert late.json()["error"]["details"]["revision"] == 1

    second = _put(client, admin, url, "# 힌지\n\n내구 시험 조건 — 20만 회", 1, note="수치")
    assert second.json()["revision"] == 2

    revisions = client.get(f"{url}/revisions", headers=admin.headers).json()
    assert [(one["revision"], one["note"]) for one in revisions] == [(2, "수치"), (1, "처음")]
    old = client.get(f"{url}/revisions/1", headers=admin.headers).json()
    assert old["body"] == "# 힌지\n\n내구 시험 조건"


def test_본문은_속성_칸으로_못_넣는다(client: TestClient, admin: Signed) -> None:
    slug = _doc_type(client, admin)
    refused = client.post(
        f"/api/objects/{slug}",
        json={
            "label": "백서",
            "properties": {"body": "# 본문"},
            "workspace_slug": admin.workspace,
        },
        headers=admin.headers,
    )
    assert refused.status_code == 422
    assert "문서 API" in refused.text


def test_문서_속성이_아니면_없다고_한다(client: TestClient, admin: Signed) -> None:
    slug = _doc_type(client, admin)
    _make_property(client, admin, slug, key="memo", label="메모", data_type="text")
    paper = _make_object(client, admin, slug, label="백서")
    for key in ("memo", "nothing"):
        got = client.get(_url(slug, paper["id"], key), headers=admin.headers)
        assert got.status_code == 404
        assert got.json()["error"]["code"] == code("OBJECTS", 148)


def test_읽기는_누구나_쓰기는_고칠_수_있는_사람만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    slug = _doc_type(client, admin)
    paper = _make_object(client, admin, slug, label="백서", workspace_slug=admin.workspace)
    url = _url(slug, paper["id"])
    _put(client, admin, url, "본문", 0)

    seen = client.get(url, headers=member.headers).json()
    assert (seen["body"], seen["can_edit"]) == ("본문", False)
    assert _put(client, member, url, "바꿈", 1).status_code == 403


def test_토큰으로_고치면_그_이름이_판에_남는다(client: TestClient, admin: Signed) -> None:
    """AI(MCP)가 쓴 판을 사람이 검토한다 — 어느 판이 기계의 것인지 알아야 한다."""
    slug = _doc_type(client, admin)
    paper = _make_object(client, admin, slug, label="백서")
    made = client.post(
        "/api/auth/tokens",
        json={"name": "백서 백필 봇", "scopes": ["read", "objects:write"]},
        headers=admin.headers,
    )
    token = {"Authorization": f"Bearer {made.json()['token']}"}
    url = _url(slug, paper["id"])
    written = client.put(
        url, json={"body": "AI 가 쓴 요약", "base_revision": 0}, headers=token
    )
    assert written.status_code == 200, written.text
    assert written.json()["updated_via"] == "백서 백필 봇"
    assert (
        client.get(f"{url}/revisions", headers=admin.headers).json()[0]["via"]
        == "백서 백필 봇"
    )


def test_본문의_링크가_가리킨_객체에_언급으로_모인다(
    client: TestClient, admin: Signed
) -> None:
    slug = _doc_type(client, admin)
    part = _make_type(client, admin, label="부품")
    hinge = _make_object(client, admin, part, label="힌지")
    cover = _make_object(client, admin, part, label="커버")
    paper = _make_object(client, admin, slug, label="힌지 내구 백서")
    url = _url(slug, paper["id"])
    body = (
        f"[힌지](/o/{part}/{hinge['id']}) 와 http://x/o/{part}/{cover['id']} 를 본다. "
        f"자기 자신 [백서](/o/{slug}/{paper['id']}) · "
        f"없는 것 /o/{part}/00000000-0000-0000-0000-000000000000"
    )
    _put(client, admin, url, body, 0)

    found = client.get(f"/api/objects/{part}/{hinge['id']}/mentions", headers=admin.headers)
    assert found.status_code == 200, found.text
    assert [(one["object_id"], one["key"], one["property_label"]) for one in found.json()] == [
        (paper["id"], "body", "본문")
    ]
    assert (
        len(
            client.get(
                f"/api/objects/{part}/{cover['id']}/mentions", headers=admin.headers
            ).json()
        )
        == 1
    )
    assert (
        client.get(f"/api/objects/{slug}/{paper['id']}/mentions", headers=admin.headers).json()
        == []
    )

    # 링크를 지우면 언급도 사라진다 — 본문이 정본이다.
    _put(client, admin, url, f"[힌지](/o/{part}/{hinge['id']})", 1)
    assert (
        client.get(f"/api/objects/{part}/{cover['id']}/mentions", headers=admin.headers).json()
        == []
    )
    # 문서를 가진 객체를 지우면 언급에서 빠진다.
    client.delete(f"/api/objects/{slug}/{paper['id']}", headers=admin.headers)
    assert (
        client.get(f"/api/objects/{part}/{hinge['id']}/mentions", headers=admin.headers).json()
        == []
    )


def test_문서는_공통_속성_종류_변경_조건이_되지_않는다(
    client: TestClient, admin: Signed
) -> None:
    slug = _doc_type(client, admin)
    retype = client.post(
        f"/api/ontology/types/{slug}/properties/body/retype",
        json={"data_type": "text_long"},
        headers=admin.headers,
    )
    assert retype.status_code == 409
    assert "문서 속성은" in retype.text

    listed = client.get(
        f"/api/objects/{slug}",
        params={"f.body.contains": "x"},
        headers=admin.headers,
    )
    assert listed.status_code == 422, listed.text
    assert "문서 칸은 못 겁니다" in listed.text


def test_이력에는_판으로_서고_그_시점의_값이_없다(client: TestClient, admin: Signed) -> None:
    """문서 수정은 값이 아니라 판이다 — 이력에 「어느 문서 · 몇 판 · 토큰 · 사유」 로 서고, 그
    기록으로 값을 되돌리지 않는다(옛 판은 문서의 판 이력에서 복원)."""
    slug = _doc_type(client, admin)
    paper = _make_object(client, admin, slug, label="백서")
    _put(client, admin, _url(slug, paper["id"]), "본문", 0, note="처음")
    entries = client.get(f"/api/objects/{slug}/{paper['id']}/history", headers=admin.headers)
    assert entries.status_code == 200, entries.text
    document = next(one for one in entries.json() if one["action"] == "object.document")
    assert document["document"] == {
        "key": "body",
        "revision": 1,
        "chars": 2,
        "via": "",
        "note": "처음",
    }
    assert document["snapshot"] is None and document["changes"] == {}
    refused = client.post(
        f"/api/objects/{slug}/{paper['id']}/restore",
        json={"entry_id": document["id"]},
        headers=admin.headers,
    )
    assert refused.status_code == 409
    assert "판 이력" in refused.text


def test_문서의_그림은_그_문서_자리에_이미지만_붙고_조각에는_캡션만_남는다(
    client: TestClient, admin: Signed
) -> None:
    """문서의 그림(ADR 0056) — 문서 속성의 키가 첨부의 자리다. 본문 사이에 그리므로 서버가
    이미지로 읽은 것만 받는다(성적서 PDF 는 파일 속성 · 「그 밖의 첨부」 로). 뜻으로 찾기의
    조각에는 첨부 id 대신 캡션이 남는다 — id 36자는 뜻이 없다."""
    import io

    from PIL import Image

    from app.modules.files import photomarks
    from app.modules.semantic.services import document_pieces

    slug = _doc_type(client, admin)
    paper = _make_object(client, admin, slug, label="힌지 내구 백서")

    def attach(content: bytes, name: str, field: str | None = "body") -> Any:
        data = {"owner_table": "objects", "owner_id": paper["id"]}
        if field is not None:
            data["owner_field"] = field
        return client.post(
            "/api/attachments",
            data=data,
            files={"file": (name, io.BytesIO(content), "application/octet-stream")},
            headers=admin.headers,
        )

    out = io.BytesIO()
    Image.new("RGB", (400, 300), (10, 120, 200)).save(out, format="PNG")
    placed = attach(out.getvalue(), "단면.png")
    assert placed.status_code == 201, placed.text
    assert (placed.json()["owner_field"], placed.json()["is_image"]) == ("body", True)
    refused = attach(b"%PDF-1.4 not an image", "성적서.pdf")
    assert refused.status_code == 415, refused.text
    assert refused.json()["error"]["code"] == code("FILES", 7)
    # 문서 속성도 파일 속성도 아닌 칸은 예전처럼 거절한다.
    assert attach(out.getvalue(), "a.png", field="nothing").status_code == 409

    photo = placed.json()["id"]
    figure = photomarks.marker(photo, 60, "하우징 단면")
    body = f"# 결과\n\n{figure}\n\n찍힘 없음\n\n## 그림만\n\n{photomarks.marker(photo, 40)}"
    saved = _put(client, admin, _url(slug, paper["id"]), body, 0)
    assert saved.status_code == 200, saved.text
    # 그림 표시는 언급이 아니다(객체 링크가 아니다).
    listed = client.get(
        "/api/attachments",
        params={"owner_table": "objects", "owner_id": paper["id"], "owner_field": "body"},
        headers=admin.headers,
    ).json()
    assert [one["id"] for one in listed] == [photo]

    class Row:
        label = "힌지 내구 백서"

    pieces = document_pieces(Row(), "body", body)  # type: ignore[arg-type]
    assert [one.text for one in pieces] == [
        "힌지 내구 백서 › 결과\n그림: 하우징 단면\n\n찍힘 없음"
    ]
    assert photomarks.readable(f"글 ![](photo:{photo}) 글") == "글  글"
