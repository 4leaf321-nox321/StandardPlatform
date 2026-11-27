"""문서 올리기(ADR 0099) — 로컬 마크다운 · 그림을 **파일 그대로** 문서 칸에 넣는다.

플랫폼 MCP `document_write` 는 본문이 도구 인자라 Word 569건 · 270만 자를 모델이 옮겨 적어야
했다. 키트(`pipeline/sp_docpush.py`)가 zip 으로 묶어 작업(`documents_push`)으로 올리고, 사람이
「작업」 화면에서 적용하며, 대조는 본문 없이 sha256 으로 한다.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.shared.errors import code
from tests.api.conftest import Signed, finish_job, patched_pipeline

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
sp_pipeline: Any = importlib.import_module("sp_pipeline")
sp_docpush: Any = importlib.import_module("sp_docpush")
SERVER = "http://platform.test"


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:12]}"


def _png(color: str = "red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    with patched_pipeline(sp_pipeline, client):
        yield


@pytest.fixture
def token(client: TestClient, admin: Signed) -> str:
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("docpush"), "scopes": ["read", "objects:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return str(made.json()["token"])


def _world(client: TestClient, admin: Signed) -> tuple[str, str]:
    slug = _uniq("paper_owner")
    made = client.post(
        "/api/ontology/import",
        params={"dry_run": "false"},
        json={
            "types": [
                {
                    "slug": slug,
                    "label": "보고 대상",
                    "properties": [{"key": "paper", "label": "백서", "data_type": "document"}],
                }
            ]
        },
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    obj = client.post(
        f"/api/objects/{slug}",
        json={"label": "힌지", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert obj.status_code == 201, obj.text
    return slug, str(obj.json()["id"])


def _draft(folder: Path, body: str, *, image: bytes | None = None) -> str:
    where = folder / "docs" / "백서"
    (where / "media").mkdir(parents=True, exist_ok=True)
    if image is not None:
        (where / "media" / "image1.png").write_bytes(image)
    (where / "draft.md").write_text(body, encoding="utf-8")
    return "docs/백서/draft.md"


BODY = "# 힌지 캠 마모\n\n시험 결과 요약.\n\n![마모 사진](media/image1.png?w=60)\n\n끝.\n"


def _apply(client: TestClient, admin: Signed, job_id: str) -> dict[str, Any]:
    made = client.post(f"/api/jobs/{job_id}/apply", headers=admin.headers)
    assert made.status_code == 202, made.text
    done = finish_job(client, admin, made.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def test_로컬_마크다운과_그림을_올리고_화면에서_적용하면_sha256_으로_대조한다(
    client: TestClient, admin: Signed, platform: None, token: str, tmp_path: Path
) -> None:
    slug, oid = _world(client, admin)
    path = _draft(tmp_path, BODY, image=_png())
    items = [sp_docpush.Item(slug, oid, "paper", path)]

    planned = sp_docpush.push(tmp_path, items, server=SERVER, token=token)
    assert planned["ok"] is True and planned["counts"]["create"] == 1, planned
    row = planned["rows"][0]
    assert row["action"] == "create" and row["images"] == 1, row
    assert row["sha256"] is None, "새 그림이 있으면 첨부 id 가 정해져야 sha256 을 안다"
    # 미리 보기는 아무것도 안 썼다.
    before = client.get(
        f"/api/objects/{slug}/{oid}/documents/paper",
        params={"body": "false"},
        headers=admin.headers,
    ).json()
    assert before["revision"] == 0 and before["body_included"] is False, before

    # 사람이 대화에서 「좋다」 — 키트가 적용하고 곧바로 대조한다.
    applied = sp_docpush.apply(
        tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token
    )
    assert applied["all_applied"] is True and applied["applied"][0]["state"] == "applied", (
        applied
    )
    assert applied["check"]["all_same"] is True, applied["check"]
    job = client.get(
        f"/api/jobs/{applied['applied'][0]['apply_job_id']}", headers=admin.headers
    ).json()
    written = job["result"]["rows"][0]
    assert written["revision"] == 1 and written["sha256"], written
    attachment = written["images"][0]["attachment_id"]
    # 두 번 부르면 이미 적용한 작업은 건너뛴다.
    twice = sp_docpush.apply(tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token)
    assert twice["applied"][0]["state"] == "already", twice
    meta = client.get(
        f"/api/objects/{slug}/{oid}/documents/paper",
        params={"body": "false"},
        headers=admin.headers,
    ).json()
    assert meta["body"] == "" and meta["sha256"] == written["sha256"], meta
    full = client.get(
        f"/api/objects/{slug}/{oid}/documents/paper", headers=admin.headers
    ).json()
    assert f"![마모 사진](photo:{attachment}?w=60)" in full["body"], full["body"]
    assert full["updated_via"] == "문서 올리기"

    checked = sp_docpush.check(
        tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token
    )
    assert checked["all_same"] is True, checked

    # 다시 올리면 그대로 — 같은 그림은 붙은 것을 쓴다(첨부가 늘지 않는다).
    again = sp_docpush.push(tmp_path, items, server=SERVER, token=token)
    assert again["counts"]["unchanged"] == 1, again
    pictures = client.get(
        "/api/attachments",
        params={"owner_table": "objects", "owner_id": oid, "owner_field": "paper"},
        headers=admin.headers,
    ).json()
    assert len(pictures) == 1, pictures

    # 그 뒤 누가 고치면 대조가 「다름」.
    changed = client.put(
        f"/api/objects/{slug}/{oid}/documents/paper",
        json={"body": full["body"] + "\n덧붙임\n", "base_revision": 1},
        headers=admin.headers,
    )
    assert changed.status_code == 200 and changed.json()["sha256"] != meta["sha256"]
    later = sp_docpush.check(tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token)
    assert later["counts"]["different"] == 1 and later["all_same"] is False, later


def test_판이_다르거나_올리기_전에_고칠_것이_있으면_쓰지_않는다(
    client: TestClient, admin: Signed, platform: None, token: str, tmp_path: Path
) -> None:
    slug, oid = _world(client, admin)
    first = client.put(
        f"/api/objects/{slug}/{oid}/documents/paper",
        json={"body": "# 사람이 쓴 것\n", "base_revision": 0},
        headers=admin.headers,
    )
    assert first.status_code == 200, first.text
    path = _draft(tmp_path, "# 새 판\n")
    stale = sp_docpush.push(
        tmp_path, [sp_docpush.Item(slug, oid, "paper", path, 0)], server=SERVER, token=token
    )
    assert stale["ok"] is False and stale["counts"]["error"] == 1, stale
    assert "판이 올랐습니다" in stale["rows"][0]["message"], stale
    refused = client.post(
        f"/api/jobs/{stale['jobs'][0]['job_id']}/apply", headers=admin.headers
    )
    assert refused.status_code == 409, "오류가 있는 계획은 적용하지 않는다"
    kit = sp_docpush.apply(tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token)
    assert kit["applied"][0]["state"] == "refused" and kit["all_applied"] is False, kit

    # 키트가 막는 것 — 플랫폼에 아무것도 보내지 않는다.
    _draft(tmp_path, "연락처 010-1234-5678\n\n![없는 그림](media/없음.png)\n")
    with pytest.raises(sp_pipeline.Stop) as stopped:
        sp_docpush.push(
            tmp_path, [sp_docpush.Item(slug, oid, "paper", path)], server=SERVER, token=token
        )
    assert "개인 정보 꼴" in str(stopped.value) and "그림 파일이 없습니다" in str(
        stopped.value
    )


def test_플랫폼은_목록을_믿지_않는다(client: TestClient, admin: Signed) -> None:
    """키트를 안 거친 zip — 없는 그림 · 문서 칸 아님 · 같은 칸 둘 · 목록 없음."""
    slug, oid = _world(client, admin)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "sp-documents/1",
                    "documents": [
                        {"type_slug": slug, "object_id": oid, "key": "paper", "path": "a.md"},
                        {"type_slug": slug, "object_id": oid, "key": "paper", "path": "a.md"},
                        {"type_slug": slug, "object_id": oid, "key": "nope", "path": "a.md"},
                        {"type_slug": slug, "object_id": oid, "key": "paper", "path": "b.md"},
                    ],
                }
            ),
        )
        archive.writestr("a.md", "# 가\n\n![그림](img/없음.png)\n")
    made = client.post(
        "/api/jobs",
        data={"kind": "documents_push", "params": "{}"},
        files={"file": ("docs.zip", buffer.getvalue(), "application/zip")},
        headers=admin.headers,
    )
    assert made.status_code == 202, made.text
    done = finish_job(client, admin, made.json())
    rows = done["result"]["rows"]
    messages = [one["message"] for one in rows]
    assert "zip 에 그 그림이 없습니다" in messages[0], messages
    assert "1번째 줄이 씁니다" in messages[1]
    assert "문서 칸" in messages[2]
    assert "같은 객체 · 문서 칸" in messages[3] or "마크다운이 없습니다" in messages[3]

    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("a.md", "# 가")
    bare = client.post(
        "/api/jobs",
        data={"kind": "documents_push", "params": "{}"},
        files={"file": ("docs.zip", empty.getvalue(), "application/zip")},
        headers=admin.headers,
    )
    failed = finish_job(client, admin, bare.json())
    assert failed["status"] == "failed" and code("OBJECTS", 161) in (failed["error"] or ""), (
        failed
    )


def test_화면에서_먼저_적용했으면_키트는_건너뛰고_대조만_한다(
    client: TestClient, admin: Signed, platform: None, token: str, tmp_path: Path
) -> None:
    slug, oid = _world(client, admin)
    path = _draft(tmp_path, "# 화면에서 적용\n")
    planned = sp_docpush.push(
        tmp_path, [sp_docpush.Item(slug, oid, "paper", path)], server=SERVER, token=token
    )
    _apply(client, admin, planned["jobs"][0]["job_id"])
    kit = sp_docpush.apply(tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token)
    assert kit["applied"][0]["state"] == "already" and kit["check"]["all_same"] is True, kit


# --- 2026-11-27 점검 — 상한 · 그림 꼴 · 같은 그림 · 빈 본문 · NUL · 줄 자르기 ------------


def _push(
    client: TestClient, admin: Signed, documents: list[Any], files: dict[str, bytes | str]
) -> dict[str, Any]:
    """키트를 안 거친 zip — 목록 · 마크다운 · 그림을 그대로 싣는다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json", json.dumps({"format": "sp-documents/1", "documents": documents})
        )
        for name, data in files.items():
            archive.writestr(name, data)
    made = client.post(
        "/api/jobs",
        data={"kind": "documents_push", "params": "{}"},
        files={"file": ("docs.zip", buffer.getvalue(), "application/zip")},
        headers=admin.headers,
    )
    assert made.status_code == 202, made.text
    return finish_job(client, admin, made.json())


def _doc(slug: str, oid: str, path: str) -> dict[str, Any]:
    return {"type_slug": slug, "object_id": oid, "key": "paper", "path": path}


def _another(client: TestClient, admin: Signed, slug: str, label: str) -> str:
    made = client.post(
        f"/api/objects/{slug}",
        json={"label": label, "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def test_목록과_마크다운은_항목마다_상한이_있어_풀기_전에_거절한다(
    client: TestClient, admin: Signed
) -> None:
    """작은 zip 에 든 큰 목록 · 마크다운 — 전에는 통째로 풀어 읽었다(1MB zip 의 1GB 목록이
    워커의 메모리를 다 썼다). 이제 머리에 적힌 크기로 먼저 거절한다."""
    from app.modules.objects import docpush

    slug, oid = _world(client, admin)
    padded = io.BytesIO()
    with zipfile.ZipFile(padded, "w", zipfile.ZIP_DEFLATED) as archive:
        listed = json.dumps(
            {"format": "sp-documents/1", "documents": [_doc(slug, oid, "a.md")]}
        )
        archive.writestr("manifest.json", listed + " " * (docpush.MANIFEST_MAX_BYTES + 1))
        archive.writestr("a.md", "# 가\n")
    assert len(padded.getvalue()) < 100_000, "눌러 담으면 작다"
    made = client.post(
        "/api/jobs",
        data={"kind": "documents_push", "params": "{}"},
        files={"file": ("docs.zip", padded.getvalue(), "application/zip")},
        headers=admin.headers,
    )
    failed = finish_job(client, admin, made.json())
    assert failed["status"] == "failed", failed
    assert (
        code("OBJECTS", 161) in failed["error"] and "목록은 4.0MB 까지" in failed["error"]
    ), failed["error"]

    huge = "a" * (docpush.BODY_MAX_BYTES + 1)
    done = _push(client, admin, [_doc(slug, oid, "a.md")], {"a.md": huge})
    assert done["status"] == "done", done
    row = done["result"]["rows"][0]
    assert row["action"] == "error" and row["message"].startswith("마크다운이 "), row


def test_그림_꼴로_못_읽은_자리와_빈_본문은_경고이고_줄을_자를_때_앞세운다(
    client: TestClient, admin: Signed
) -> None:
    from app.modules.jobs import services as job_services

    slug, oid = _world(client, admin)
    written = client.put(
        f"/api/objects/{slug}/{oid}/documents/paper",
        json={"body": "# 사람이 쓴 것\n", "base_revision": 0},
        headers=admin.headers,
    )
    assert written.status_code == 200, written.text
    plain = _another(client, admin, slug, "평범")
    odd = _another(client, admin, slug, "그림 꼴")
    body = "\n".join(
        [
            "# 그림 꼴",
            '![제목](img/a.png "제목")',
            "![빈칸](my pic.png)",
            "![폭](img/a.png?w=1000)",
            "![대괄]호](img/a.png)",
            "![절대](/img/a.png)",
            "![맞음](img/a.png?w=60)",
            "![바깥](https://example.com/a.png)",
            "[문서](notes.pdf)",
            "",
        ]
    )
    done = _push(
        client,
        admin,
        [
            _doc(slug, plain, "plain.md"),
            _doc(slug, odd, "odd.md"),
            _doc(slug, oid, "empty.md"),
        ],
        {"plain.md": "# 평범\n", "odd.md": body, "empty.md": "  \n", "img/a.png": _png()},
    )
    assert done["status"] == "done", done
    result = done["result"]
    rows = result["rows"]
    assert [one["action"] for one in rows] == ["create", "create", "update"], rows
    assert rows[0]["warnings"] == []
    (pictures,) = rows[1]["warnings"]
    assert pictures.startswith("그림 꼴로 읽지 않은 자리 5곳"), pictures
    assert (
        '「![제목](img/a.png "제목")」' in pictures and "「![빈칸](my pic.png)」" in pictures
    )
    assert len(rows[1]["images"]) == 1, "맞는 꼴 하나만 첨부한다"
    (emptied,) = rows[2]["warnings"]
    assert emptied.startswith(f"지금 본문 {written.json()['chars']}자가 빈 본문이 됩니다"), (
        emptied
    )
    assert result["warned"] == 2 and "확인할 것 2" in result["summary"], result
    # 「작업」 화면의 계획 표가 일괄 입력과 같은 모양으로 읽는다.
    assert result["errors"] == []
    # 경고는 막지 않는다.
    assert result["ok"] is True

    trimmed = job_services.trim_rows(result, 2)
    assert [one["row"] for one in trimmed["rows"]] == [2, 3], "경고 줄을 앞세운다"
    assert trimmed["rows_omitted"] == 1


def test_같은_그림을_여러_자리가_가리켜도_한_번만_붙인다(
    client: TestClient, admin: Signed
) -> None:
    """`./img/a.png` · `img/a.png` · 이름만 다른 같은 파일 — 전에는 자리마다 첨부가 늘었다."""
    slug, oid = _world(client, admin)
    picture = _png("blue")
    body = "![가](./img/a.png)\n\n![나](img/a.png?w=50)\n\n![다](img/copy.png)\n"
    planned = _push(
        client,
        admin,
        [_doc(slug, oid, "docs/a.md")],
        {"docs/a.md": body, "docs/img/a.png": picture, "docs/img/copy.png": picture},
    )
    row = planned["result"]["rows"][0]
    assert row["action"] == "create" and len(row["images"]) == 1, row
    applied = _apply(client, admin, planned["id"])
    attachment = applied["rows"][0]["images"][0]["attachment_id"]
    pictures = client.get(
        "/api/attachments",
        params={"owner_table": "objects", "owner_id": oid, "owner_field": "paper"},
        headers=admin.headers,
    ).json()
    assert [one["id"] for one in pictures] == [attachment], pictures
    full = client.get(f"/api/objects/{slug}/{oid}/documents/paper", headers=admin.headers)
    assert full.json()["body"].count(f"photo:{attachment}") == 3, full.json()["body"]


def test_본문의_NUL_문자는_계획에서_오류다(client: TestClient, admin: Signed) -> None:
    """PostgreSQL 의 글자 칸은 NUL 을 못 담는다 — 전에는 계획을 지나 적용이 DB 오류로
    죽었다."""
    slug, oid = _world(client, admin)
    other = _another(client, admin, slug, "메모")
    noted = {**_doc(slug, other, "b.md"), "note": "메모\x00"}
    done = _push(
        client,
        admin,
        [_doc(slug, oid, "a.md"), noted],
        {"a.md": "# 가\n\n나\x00다\n", "b.md": "# 나\n"},
    )
    assert done["status"] == "done", done
    row, note = done["result"]["rows"]
    assert row["action"] == "error" and "NUL 문자" in row["message"], row
    assert "3째 줄" in row["message"], row
    assert note["action"] == "error" and "목록의 줄에 NUL 문자" in note["message"], note
