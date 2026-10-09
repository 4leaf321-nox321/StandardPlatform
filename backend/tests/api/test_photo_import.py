"""이미지 첨부의 남은 셋(ADR 0012, 2026-10-08) — 목록의 미리보기 · 생성 직후 업로드 · 사진 일괄
업로드(zip).

- 목록 화면의 열에 파일 속성이 있으면 **한 번에** 칸(첫 장 · 수)이 온다. 바이트는 없고,
  미리보기 주소는 원본과 같은 규칙으로 판정한다(못 보는 객체의 미리보기는 404).
- 생성 화면은 객체를 만든 뒤 같은 업로드 API 로 올린다 — 거절된 업로드의 파일은 아무도 안
  가리키고, 고아 정리가 하루 뒤에 치운다.
- 사진 일괄 업로드는 작업이다. 파일 이름으로 객체를 찾아 계획을 세우고(못 찾음 · 여럿에 맞음 ·
  이미지 아님 · 너무 큼 · 권한 없음), 사람이 보고 적용한다. zip 은 믿지 않는다(폭탄 · 경로 ·
  숨김 파일 · CP949 이름).
"""

from __future__ import annotations

import io
import os
import struct
import time
import unicodedata
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.files import gc
from app.modules.objects import photos
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, finish_job, maintenance_counts
from tests.api.test_attachment_images import _attach, _png
from tests.api.test_ontology import _make_object, _make_property, _make_type

DAY_AGO = time.time() - 2 * 24 * 3600


class _Cp949Info(zipfile.ZipInfo):
    """Windows 탐색기(한국어)의 「압축 폴더」 가 만드는 항목 — 이름이 CP949 바이트이고 UTF-8
    표시(0x800)가 없다. 파이썬은 이름에 ASCII 밖의 글자가 있으면 늘 UTF-8 로 적으므로 덮어
    쓴다(zipfile 의 이름 그대로)."""

    def _encodeFilenameFlags(self) -> tuple[bytes, int]:
        return self.filename.encode("cp949"), self.flag_bits & ~0x800


def _zip(files: dict[str, bytes], *, cp949: frozenset[str] = frozenset()) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            if name in cp949:
                archive.writestr(_Cp949Info(name), body)
            else:
                archive.writestr(name, body)
    return out.getvalue()


def _photo_type(client: TestClient, admin: Signed, *, columns: bool = False) -> str:
    kind = _make_type(client, admin, label="시험 결과", key_policy="optional")
    _make_property(
        client, admin, kind, key="photo", label="사진", data_type="file", accept="image"
    )
    _make_property(client, admin, kind, key="doc", label="성적서", data_type="file")
    if columns:
        view = {"columns": ["label", "properties.photo", "properties.doc"]}
        got = client.patch(
            f"/api/ontology/types/{kind}", json={"list_view": view}, headers=admin.headers
        )
        assert got.status_code == 200, got.text
    return kind


def _other(db: Session) -> Workspace:
    row = Workspace(slug=f"o-{uuid.uuid4().hex[:8]}", name="다른 부서")
    db.add(row)
    db.commit()
    return row


def _send(
    client: TestClient,
    who: Signed,
    kind: str,
    body: bytes,
    *,
    field: str = "photo",
    existing: str = "skip",
    name: str = "사진.zip",
) -> Any:
    return client.post(
        f"/api/objects/{kind}/photos/import",
        files={"file": (name, io.BytesIO(body), "application/zip")},
        data={"field": field, "existing": existing},
        headers=who.headers,
    )


def _plan(
    client: TestClient, who: Signed, kind: str, body: bytes, **kw: Any
) -> dict[str, Any]:
    sent = _send(client, who, kind, body, **kw)
    assert sent.status_code == 202, sent.text
    assert sent.json()["kind"] == "objects_photos"
    done = finish_job(client, who, sent.json())
    assert done["status"] == "done", done
    return dict(done)


def _apply(client: TestClient, who: Signed, plan: dict[str, Any]) -> dict[str, Any]:
    started = client.post(f"/api/jobs/{plan['id']}/apply", headers=who.headers)
    assert started.status_code == 202, started.text
    return dict(finish_job(client, who, started.json()))


def _rows(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {one["name"]: one for one in job["result"]["files"]}


def _attached(client: TestClient, who: Signed, object_id: str, field: str) -> list[Any]:
    got = client.get(
        "/api/attachments",
        params={"owner_table": "objects", "owner_id": object_id, "owner_field": field},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return list(got.json())


# --- 목록의 파일 칸 ------------------------------------------------------------------


def test_목록의_파일_칸은_첫_사진과_수를_한_번에_싣는다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    kind = _photo_type(client, admin, columns=True)
    full = _make_object(client, admin, kind, label="결과 A")
    papers = _make_object(client, admin, kind, label="결과 B")
    empty = _make_object(client, admin, kind, label="결과 C")
    for owner, content, name, field in (
        (full["id"], _png(), "1.png", "photo"),
        (full["id"], _png(330), "2.png", "photo"),
        # 성적서(PDF)가 먼저 올라왔어도 칸의 대표는 사진이다 — 아이콘이 서면 사진이 없다고
        # 읽힌다.
        (full["id"], b"%PDF-1.7 first", "a.pdf", "doc"),
        (full["id"], _png(331), "scan.png", "doc"),
        (papers["id"], b"%PDF-1.7 only", "b.pdf", "doc"),
    ):
        made = _attach(client, admin, owner, content=content, name=name, field=field)
        assert made.status_code == 201, made.text
    secret = _make_object(client, admin, kind, label="결과 S", workspace_slug=_other(db).slug)
    hidden = _attach(client, admin, secret["id"], content=_png(340), name="s.png")
    assert hidden.status_code == 201

    page = client.get(f"/api/objects/{kind}", headers=member.headers)
    assert page.status_code == 200, page.text
    files = page.json()["files"]
    assert empty["id"] not in files and secret["id"] not in files
    cell = files[full["id"]]
    assert cell["photo"]["count"] == 2
    assert cell["photo"]["first"]["is_image"] is True
    assert cell["photo"]["first"]["original_name"] == "1.png"
    assert cell["doc"]["count"] == 2 and cell["doc"]["first"]["original_name"] == "scan.png"
    assert files[papers["id"]] == {
        "doc": {"count": 1, "first": files[papers["id"]]["doc"]["first"]}
    }
    assert files[papers["id"]]["doc"]["first"]["is_image"] is False

    # 미리보기는 원본과 같은 규칙 — 보이는 객체의 것은 받고, 못 보는 객체의 것은 없는 것이다.
    first = cell["photo"]["first"]["id"]
    thumb = client.get(f"/api/attachments/{first}/thumbnail", headers=member.headers)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    assert thumb.headers["cache-control"].startswith("private")
    stolen = client.get(
        f"/api/attachments/{hidden.json()['id']}/thumbnail", headers=member.headers
    )
    assert stolen.status_code == 404


def test_파일_칸이_열에_없으면_묻지_않는다(client: TestClient, admin: Signed) -> None:
    kind = _photo_type(client, admin)
    made = _make_object(client, admin, kind, label="결과")
    assert _attach(client, admin, made["id"], content=_png()).status_code == 201
    page = client.get(f"/api/objects/{kind}", headers=admin.headers).json()
    assert page["files"] == {} and page["total"] == 1


# --- 생성 화면 — 만든 뒤 업로드, 끊긴 업로드는 고아 정리가 치운다 ------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """**제 저장소에서만** — 개발 저장소의 파일을 지우지 않게."""
    root = tmp_path / "filestore"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "filestore_dir", root)
    return root


def test_생성_직후_업로드가_거절되면_객체는_남고_파일은_고아_정리가_치운다(
    client: TestClient, admin: Signed, store: Path, db: Session
) -> None:
    """생성 화면은 객체를 만든 뒤 같은 업로드 API 로 올린다. 거절된(이미지만 받는 칸에 PDF)
    업로드는 파일을 먼저 쓰고 행을 안 넣었다 — 그 파일은 아무도 안 가리키고, 하루 뒤 정리가
    지운다. 객체는 그대로 있다(화면은 상세로 안내한다)."""
    kind = _photo_type(client, admin)
    made = _make_object(client, admin, kind, label="방금 만든 것")
    refused = _attach(client, admin, made["id"], content=b"%PDF-1.7 refused", name="x.pdf")
    assert refused.status_code == 415, refused.text
    kept = _attach(client, admin, made["id"], content=_png(321, 200), name="ok.png")
    assert kept.status_code == 201, kept.text
    assert (
        client.get(f"/api/objects/{kind}/{made['id']}", headers=admin.headers).status_code
        == 200
    )

    for path in store.rglob("*"):
        if path.is_file():
            os.utime(path, (DAY_AGO, DAY_AGO))
    found = gc.scan(db)
    # 거절된 PDF 하나만 — 붙은 사진의 원본 · 미리보기는 쓰인다.
    assert len(found.orphans) == 1, found.orphans
    gc.clean(db, found)
    got = client.get(f"/api/attachments/{kept.json()['id']}/content", headers=admin.headers)
    assert got.status_code == 200


# --- 사진 일괄 업로드 ------------------------------------------------------------------


def test_파일_이름으로_찾아_계획하고_적용한다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    kind = _photo_type(client, admin)
    bolt = _make_object(client, manager, kind, label="볼트", key="P-100")
    nut = _make_object(client, manager, kind, label="너트")
    # 「와셔」 가 아니다 — 볼트 · 너트 · 와셔가 한 타입에 모두 있으면 표 추론 시험(같은 DB)이
    # 그 열을 이 타입의 참조로 읽는다.
    washer = _make_object(client, manager, kind, label="평와셔", key="P-200")
    assert _attach(client, manager, washer["id"], content=_png(300)).status_code == 201
    _make_object(client, manager, kind, label="같은 이름")
    _make_object(client, manager, kind, label="같은 이름")
    shared = _make_object(client, admin, kind, label="전역 시료", workspace_slug=None)

    body = _zip(
        {
            "P-100.jpg": _png(400),
            "P-100_2.png": _png(401),
            "사진/너트.png": _png(402),
            "P-200.png": _png(403),
            "같은 이름.png": _png(404),
            "없는것.png": _png(405),
            "전역 시료.png": _png(406),
            "README.txt": b"photos of parts",
            "../evil.png": _png(407),
            "__MACOSX/._P-100.jpg": b"\x00\x05\x16\x07",
            ".DS_Store": b"\x00\x00\x00\x01Bud1",
        },
        cp949=frozenset({"사진/너트.png", "같은 이름.png"}),
    )
    waiting = maintenance_counts(client, manager).get("job_awaiting_apply", 0)
    plan = _plan(client, manager, kind, body)
    result = plan["result"]
    rows = _rows(plan)
    # 홈의 「읽고 적용하기를 기다리는 계획」 에 선다 — 못 붙는 줄이 있어도 붙는 것이 있으면.
    assert maintenance_counts(client, manager).get("job_awaiting_apply", 0) == waiting + 1
    assert result["applied"] is False and result["ok"] is True
    assert result["hidden"] == 2 and "__MACOSX/._P-100.jpg" not in rows
    assert rows["P-100.jpg"]["status"] == "attach"
    assert rows["P-100.jpg"]["object_id"] == bolt["id"]
    assert rows["P-100.jpg"]["matched_by"] == "key"
    # 한 객체의 둘째 사진 — 뒤 번호를 떼고 찾았다고 줄에 적는다.
    assert rows["P-100_2.png"]["object_id"] == bolt["id"]
    assert rows["P-100_2.png"]["matched_by"] == "suffix"
    # CP949 로 적힌 한글 이름도 깨지지 않고 이름으로 찾는다.
    assert rows["사진/너트.png"]["status"] == "attach"
    assert rows["사진/너트.png"]["object_id"] == nut["id"]
    assert rows["P-200.png"]["status"] == "skip"  # 이미 사진이 있다(기본은 건너뜀)
    assert rows["같은 이름.png"]["status"] == "ambiguous"
    assert rows["없는것.png"]["status"] == "not_found"
    assert rows["전역 시료.png"]["status"] == "forbidden"  # 보이지만 시스템 관리자만 고친다
    assert rows["전역 시료.png"]["object_id"] == shared["id"]
    assert rows["README.txt"]["status"] == "not_image"
    assert rows["../evil.png"]["status"] == "bad_entry"
    assert result["tally"]["attach"] == 3
    # 계획은 아무것도 안 바꿨다.
    assert _attached(client, manager, bolt["id"], "photo") == []

    applied = _apply(client, manager, plan)
    assert applied["status"] == "done", applied
    assert applied["result"]["applied"] is True
    done = _rows(applied)
    assert done["P-100.jpg"]["attachment_id"] and done["사진/너트.png"]["attachment_id"]
    names = sorted(
        one["original_name"] for one in _attached(client, manager, bolt["id"], "photo")
    )
    assert names == ["P-100.jpg", "P-100_2.png"]
    assert all(one["is_image"] for one in _attached(client, manager, bolt["id"], "photo"))
    assert len(_attached(client, manager, nut["id"], "photo")) == 1
    assert len(_attached(client, manager, washer["id"], "photo")) == 1  # 건너뛰었다
    # 붙인 일은 객체의 이력에 남는다(지켜보기 · 웹훅이 같은 기록에서 나간다).
    history = client.get(f"/api/objects/{kind}/{bolt['id']}/history", headers=manager.headers)
    assert sum(one["action"] == "object.attachment.add" for one in history.json()) == 2
    # 계획은 「적용됐다」 를 안다 — 다시 적용하지 않는다(홈의 할 일에서도 빠진다).
    again = client.get(f"/api/jobs/{plan['id']}", headers=manager.headers).json()
    assert again["applied_by"] == applied["id"]
    assert maintenance_counts(client, manager).get("job_awaiting_apply", 0) == waiting


def test_교체는_있던_사진을_떼고_추가는_곁에_붙인다(client: TestClient, admin: Signed) -> None:
    kind = _photo_type(client, admin)
    one = _make_object(client, admin, kind, label="하나", key="K-1")
    two = _make_object(client, admin, kind, label="둘", key="K-2")
    old_one = _attach(client, admin, one["id"], content=_png(500), name="옛것.png").json()
    _attach(client, admin, two["id"], content=_png(501), name="옛것.png")

    replaced = _plan(client, admin, kind, _zip({"K-1.png": _png(502)}), existing="replace")
    row = _rows(replaced)["K-1.png"]
    assert row["status"] == "replace" and row["replaces"] == 1
    assert _apply(client, admin, replaced)["status"] == "done"
    now = _attached(client, admin, one["id"], "photo")
    assert [item["original_name"] for item in now] == ["K-1.png"]
    assert old_one["id"] not in {item["id"] for item in now}

    added = _plan(client, admin, kind, _zip({"K-2.png": _png(503)}), existing="add")
    assert _rows(added)["K-2.png"]["status"] == "attach"
    assert _apply(client, admin, added)["status"] == "done"
    assert len(_attached(client, admin, two["id"], "photo")) == 2


def test_미리_본_뒤_누가_사진을_붙이면_적용하지_않는다(
    client: TestClient, admin: Signed
) -> None:
    kind = _photo_type(client, admin)
    made = _make_object(client, admin, kind, label="시료", key="S-1")
    plan = _plan(client, admin, kind, _zip({"S-1.png": _png(600)}))
    assert _rows(plan)["S-1.png"]["status"] == "attach"
    # 그 사이에 누가 상세에서 사진을 붙였다 — 계획은 이제 「건너뜀」 이다.
    assert _attach(client, admin, made["id"], content=_png(601)).status_code == 201
    failed = _apply(client, admin, plan)
    assert failed["status"] == "failed" and "JOBS-0020" in failed["error"]
    assert len(_attached(client, admin, made["id"], "photo")) == 1


def test_넣는_순간에_타입_칸_파일을_본다(client: TestClient, admin: Signed) -> None:
    kind = _photo_type(client, admin)
    _make_property(client, admin, kind, key="memo", label="메모", data_type="text")
    body = _zip({"x.png": _png()})
    not_zip = _send(client, admin, kind, body, name="사진.rar")
    assert not_zip.status_code == 409 and "OBJECTS-0125" in not_zip.text
    not_file = _send(client, admin, kind, body, field="memo")
    assert not_file.status_code == 409 and "OBJECTS-0121" in not_file.text
    assert "photo(사진)" in not_file.json()["error"]["message"]
    bad_mode = _send(client, admin, kind, body, existing="merge")
    assert bad_mode.status_code == 422
    # 일반 작업 API 로는 안 된다 — 전용 경로가 타입 · 칸을 넣는 순간에 본다.
    general = client.post(
        "/api/jobs",
        data={"kind": "objects_photos", "params": '{"type_slug": "x", "field": "photo"}'},
        files={"file": ("p.zip", io.BytesIO(body), "application/zip")},
        headers=admin.headers,
    )
    assert general.status_code == 403 and "JOBS-0025" in general.text


def test_zip_을_믿지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _photo_type(client, admin)
    broken = _send(client, admin, kind, b"PK\x03\x04 not really a zip")
    failed = finish_job(client, admin, broken.json())
    assert failed["status"] == "failed" and "OBJECTS-0123" in failed["error"]

    # 압축 폭탄 — 풀린 크기의 합과 항목 수를 **풀기 전에** 본다(워커가 이 프로세스에서 돈다).
    bomb = _zip({"zeros.png": b"\x00" * 200_000})
    assert len(bomb) < 2_000
    monkeypatch.setattr(photos, "MAX_TOTAL_BYTES", 100_000)
    failed = finish_job(client, admin, _send(client, admin, kind, bomb).json())
    assert failed["status"] == "failed" and "OBJECTS-0124" in failed["error"]
    monkeypatch.setattr(photos, "MAX_ENTRIES", 2)
    many = _zip({f"{index}.png": b"x" for index in range(3)})
    failed = finish_job(client, admin, _send(client, admin, kind, many).json())
    assert failed["status"] == "failed" and "OBJECTS-0124" in failed["error"]
    monkeypatch.undo()

    # 한 파일의 상한을 넘으면 읽지 않고 「너무 큼」.
    from app.modules.files import services as files_services

    _make_object(client, admin, kind, label="큰 것", key="BIG")
    monkeypatch.setattr(files_services, "MAX_BYTES", 1_000)
    plan = _plan(client, admin, kind, _zip({"BIG.png": _png(800)}))
    assert _rows(plan)["BIG.png"]["status"] == "too_large"
    assert plan["result"]["ok"] is False


# --- 이름 풀기(단위) -------------------------------------------------------------------


def _infos(body: bytes) -> list[zipfile.ZipInfo]:
    return zipfile.ZipFile(io.BytesIO(body)).infolist()


def test_한글_이름은_깨지지_않는다() -> None:
    cp949 = _zip({"시료/볼트 앞면.jpg": b"x"}, cp949=frozenset({"시료/볼트 앞면.jpg"}))
    (info,) = _infos(cp949)
    assert (
        not info.flag_bits & 0x800 and info.filename != "시료/볼트 앞면.jpg"
    )  # 파이썬은 깨뜨린다
    assert photos.entry_name(info) == "시료/볼트 앞면.jpg"

    # macOS — UTF-8 표시는 있지만 한글을 풀어 쓴 꼴(NFD)이다.
    decomposed = unicodedata.normalize("NFD", "사진.png")
    (info,) = _infos(_zip({decomposed: b"x"}))
    assert photos.entry_name(info) == "사진.png"

    # 7-Zip 의 유니코드 경로(0x7075) — 옛 이름의 CRC 가 맞을 때만 쓴다.
    raw = b"photo.png"
    unicode_path = struct.pack("<BI", 1, zlib.crc32(raw)) + "사진.png".encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        member = zipfile.ZipInfo("photo.png")
        member.extra = struct.pack("<HH", 0x7075, len(unicode_path)) + unicode_path
        archive.writestr(member, b"x")
    (info,) = _infos(out.getvalue())
    assert photos.entry_name(info) == "사진.png"
