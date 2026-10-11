"""큰 작업 파일 — **DB 가 아니라 공용 저장소에**, 받을 때도 읽을 때도 통째로 메모리에 안 올린다
(2026-10-10).

작업 파일은 `job_files.data`(DB)에 들었고 상한이 100MB 였다 — 사진 zip 은 휴대폰 사진 스무 장
남짓에서 나눠 올려야 했다. 이제 상한은 종류마다(표 파일 100MB · 사진 zip 2GB)이고, 문턱
(`job_file_inline_max_bytes`)을 넘는 파일은 공용 저장소의 작업
폴더(`FILESTORE_DIR/_jobs/<id>`)에 놓이며 행에는 경로만 남는다. 만료가 그 파일을 지우고, 첨부의
고아 정리는 그 폴더를 안 본다.

여기서는 문턱을 64KB 로 낮춰 본다 — 같은 길을 작은 파일로.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import tracemalloc
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.files import gc
from app.modules.files.models import Attachment
from app.modules.jobs import files, services
from app.modules.jobs.models import Job, JobFile
from app.modules.objects import photos
from app.modules.workspaces.models import Workspace
from app.shared.errors import AppError
from tests.api.conftest import Signed, export_file, finish_job
from tests.api.test_attachment_images import _png
from tests.api.test_ontology import _make_object, _make_type
from tests.api.test_photo_import import _apply, _photo_type, _plan, _rows, _send, _zip

DAY_AGO = time.time() - 2 * 24 * 3600
INLINE = 64 * 1024


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """**제 저장소에서만**, 문턱은 64KB — 개발 저장소에 파일을 남기지 않게."""
    root = tmp_path / "filestore"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "filestore_dir", root)
    monkeypatch.setattr(get_settings(), "job_file_inline_max_bytes", INLINE)
    return root


def _input_file(db: Session, job: dict[str, Any]) -> JobFile:
    db.expire_all()
    return db.scalars(
        select(JobFile)
        .join(Job, Job.input_file_id == JobFile.id)
        .where(Job.id == uuid.UUID(job["id"]))
    ).one()


def _noise(size: int) -> bytes:
    """안 줄어드는 것 — zip 이 문턱을 넘게."""
    return os.urandom(size)


def _noise_png(side: int = 80) -> bytes:
    out = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(out, format="PNG")
    return out.getvalue()


def test_큰_사진_zip_은_공용_저장소에_놓이고_워커가_파일로_읽는다(
    client: TestClient, admin: Signed, manager: Signed, db: Session, store: Path
) -> None:
    kind = _photo_type(client, admin)
    bolt = _make_object(client, manager, kind, label="볼트", key="BIG-1")
    body = _zip({"BIG-1.png": _png(500), "filler.bin": _noise(200 * 1024)})
    assert len(body) > INLINE

    plan = _plan(client, manager, kind, body)
    row = _input_file(db, plan)
    # **DB 에는 경로만** — 내용은 작업 폴더에, 받은 그대로.
    assert row.data is None and row.path == f"_jobs/{row.id}"
    assert (store / row.path).read_bytes() == body
    assert row.size_bytes == len(body) and row.sha256 == hashlib.sha256(body).hexdigest()
    assert not list((store / "_jobs").glob("*.part"))
    assert _rows(plan)["BIG-1.png"]["status"] == "attach"
    assert _rows(plan)["filler.bin"]["status"] == "not_image"

    applied = _apply(client, manager, plan)
    assert applied["status"] == "done", applied
    # 붙은 첨부는 **객체의 부서**를 따른다 — 첨부 자리(`register_attachment_owner`)가 워커에도
    # 걸려 있어야 한다(`worker._assemble`).
    attached = db.scalars(
        select(Attachment).where(Attachment.owner_id == uuid.UUID(bolt["id"]))
    ).one()
    home = db.scalars(select(Workspace).where(Workspace.slug == manager.workspace)).one()
    assert attached.workspace_id == home.id


def test_작은_파일은_그대로_DB_에_든다(
    client: TestClient, admin: Signed, db: Session, store: Path
) -> None:
    """작은 것은 DB 가 낫다 — 공용 폴더는 다른 사업장이라 느리고, DB 는 복제로 따라간다."""
    kind = _photo_type(client, admin)
    sent = _send(client, admin, kind, _zip({"tiny.png": _png(10)}))
    assert sent.status_code == 202, sent.text
    row = _input_file(db, sent.json())
    assert row.path is None and row.data is not None and len(row.data) == row.size_bytes
    finish_job(client, admin, sent.json())


def test_만료되면_저장소의_파일도_지우고_행_없는_파일은_하루_뒤에_치운다(
    client: TestClient, admin: Signed, db: Session, store: Path
) -> None:
    kind = _photo_type(client, admin)
    plan = _plan(client, admin, kind, _zip({"x.bin": _noise(100 * 1024)}))
    row = _input_file(db, plan)
    kept = store / str(row.path)
    assert kept.is_file()

    jobs = store / "_jobs"
    # 행이 안 가리키는 것 — 받다 끊긴 것(`.part`) · 받은 뒤 넣다 롤백된 것. 하루가 안 지난 것은
    # 아직 받는 중이거나 커밋을 기다리는 것이라 남긴다.
    stray = jobs / str(uuid.uuid4())
    broken = jobs / f"{uuid.uuid4()}.part"
    fresh = jobs / str(uuid.uuid4())
    for path in (stray, broken, fresh):
        path.write_bytes(b"x")
    for path in (stray, broken):
        os.utime(path, (DAY_AGO, DAY_AGO))
    # 행이 가리키는 것은 오래돼도 남긴다(만료 전).
    os.utime(kept, (DAY_AGO, DAY_AGO))
    assert services.purge_expired_files(db) >= 2
    assert kept.exists() and fresh.exists()
    assert not stray.exists() and not broken.exists()

    db.execute(
        update(JobFile)
        .where(JobFile.id == row.id)
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    db.commit()
    assert services.purge_expired_files(db) >= 1
    assert not kept.exists()
    db.expire_all()
    assert db.get(JobFile, row.id) is None
    # 파일이 없으면 적용도 없다 — 다시 올리라고 말한다.
    refused = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert refused.status_code == 409 and "JOBS-0013" in refused.text


def test_고아_첨부_정리는_작업_폴더를_안_본다(
    client: TestClient, admin: Signed, db: Session, store: Path
) -> None:
    """작업 파일은 첨부가 아니다 — 「아무 첨부도 안 가리킨다」 로 지우면 계획을 본 뒤 적용을
    기다리는 사진 zip 이 사라진다. 수명은 작업 파일의 만료가 정한다."""
    kind = _photo_type(client, admin)
    plan = _plan(client, admin, kind, _zip({"x.bin": _noise(100 * 1024)}))
    waiting = store / str(_input_file(db, plan).path)
    # 첨부(내용 주소) 모양으로 놓인 것도 그 폴더 안이면 안 본다.
    digest = hashlib.sha256(b"job").hexdigest()
    shaped = store / "_jobs" / digest[:2] / digest[2:4] / digest
    shaped.parent.mkdir(parents=True)
    shaped.write_bytes(b"job")
    for path in (waiting, shaped):
        os.utime(path, (DAY_AGO, DAY_AGO))

    found = gc.scan(db)
    assert not [one for one, _ in found.orphans if one.startswith("_jobs")]
    gc.clean(db, found)
    assert waiting.exists() and shaped.exists()


def test_종류마다_상한이_다르다(
    client: TestClient, admin: Signed, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "job_file_max_bytes", 2_000)
    monkeypatch.setattr(settings, "job_photos_max_bytes", 300 * 1024)
    kinds = {
        one["name"]: one for one in client.get("/api/jobs/kinds", headers=admin.headers).json()
    }
    assert kinds["objects_import"]["max_file_bytes"] == 2_000
    assert kinds["objects_photos"]["max_file_bytes"] == 300 * 1024
    assert kinds["objects_export"]["max_file_bytes"] is None  # 파일을 받지 않는다

    part = _make_type(client, admin, label="부품", key_policy="required")
    table = client.post(
        f"/api/objects/{part}/import",
        files={
            "file": ("rows.csv", io.BytesIO(b"key,label\n" + b"P-1,x\n" * 500), "text/csv")
        },
        data={"workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert table.status_code == 413 and "JOBS-0001" in table.text

    # 같은 크기의 사진 zip 은 받는다 — 상한이 종류마다다.
    kind = _photo_type(client, admin)
    accepted = _send(client, admin, kind, _zip({"a.bin": _noise(3_000)}))
    assert accepted.status_code == 202, accepted.text
    refused = _send(client, admin, kind, _zip({"a.bin": _noise(400 * 1024)}))
    assert refused.status_code == 413, refused.text
    error = refused.json()["error"]
    assert (
        "사진 일괄 업로드" in error["message"] and error["details"]["max_bytes"] == 300 * 1024
    )
    # 거절한 것은 작업 폴더에 남기지 않는다.
    assert not list((store / "_jobs").glob("*.part"))
    finish_job(client, admin, accepted.json())


class _Zeros:
    """읽을 때마다 만들어 주는 스트림 — 내용 전체를 한 번도 들고 있지 않다(올린 파일이
    Starlette 의 임시 파일에 있는 것과 같다)."""

    def __init__(self, size: int) -> None:
        self.left = size

    def read(self, size: int = -1) -> bytes:
        take = self.left if size < 0 else min(size, self.left)
        self.left -= take
        return bytes(take)


def test_흘려_받는다_통째로_메모리에_올리지_않는다(db: Session, store: Path) -> None:
    size = 40 * 1024 * 1024
    tracemalloc.start()
    try:
        row = files.store_stream(
            db,
            name="big.zip",
            content_type="application/zip",
            stream=_Zeros(size),  # type: ignore[arg-type]
            cap=size,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # 1MB 씩 — 40MB 를 받는 동안 몇 MB 를 넘게 들지 않는다(예전 `read()` 는 40MB 를 통째로).
    assert peak < 8 * 1024 * 1024, peak
    assert row.data is None and (store / str(row.path)).stat().st_size == size
    assert row.sha256 == hashlib.sha256(bytes(size)).hexdigest()
    db.rollback()

    # 상한은 **받는 도중에** 본다 — 넘는 순간 멈추고 쓰던 파일을 지운다(크기를 미리 몰라도).
    with pytest.raises(AppError) as caught:
        files.store_stream(
            db,
            name="too.zip",
            content_type="application/zip",
            stream=_Zeros(3 * 1024 * 1024),  # type: ignore[arg-type]
            cap=2 * 1024 * 1024,
        )
    assert caught.value.status == 413
    assert not list((store / "_jobs").glob("*.part"))


def test_큰_결과_파일도_저장소에_놓이고_내려받기는_파일에서_흘려_보낸다(
    client: TestClient,
    admin: Signed,
    db: Session,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "job_file_inline_max_bytes", 10)
    part = _make_type(client, admin, label="부품", key_policy="required")
    for n in range(3):
        _make_object(client, admin, part, label=f"부품 {n}", key=f"OUT-{n}")
    got = export_file(client, admin, f"{part}/export")
    assert "OUT-0" in got.text and "OUT-2" in got.text
    made = db.scalars(
        select(JobFile).where(JobFile.name.like(f"{part}-%"), JobFile.path.is_not(None))
    ).one()
    assert (store / str(made.path)).read_bytes() == got.content


def test_풀린_크기의_상한은_zip_크기에_비례한다(
    client: TestClient, admin: Signed, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """고정 1GB 이던 때는 zip 상한(100MB)보다 넉넉했다 — 2GB 로 올리면서 1GB 를 넘는 정상 zip
    이 폭탄으로 거절될 뻔했다. 사진은 거의 안 줄어드니 정상 zip 은 풀어도 제 크기 남짓이다."""
    kind = _photo_type(client, admin)
    _make_object(client, admin, kind, label="잡음 사진", key="NOISE-1")
    picture = _noise_png()
    monkeypatch.setattr(photos, "MAX_TOTAL_BYTES", len(picture) // 2)
    plan = _plan(client, admin, kind, _zip({"NOISE-1.png": picture}))
    assert _rows(plan)["NOISE-1.png"]["status"] == "attach"
    # 폭탄은 여전히 — 풀면 zip 의 두 배를 한참 넘는다.
    bomb = _send(client, admin, kind, _zip({"zeros.png": bytes(200_000)}))
    failed = finish_job(client, admin, bomb.json())
    assert failed["status"] == "failed" and "OBJECTS-0124" in failed["error"]
