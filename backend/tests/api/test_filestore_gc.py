"""첨부 저장소의 **고아 파일 정리** — 아무 첨부도 안 가리키는 파일을 지운다(ADR 0012).

첨부를 지우면 행만 지우고 파일은 남긴다(같은 내용을 다른 첨부가 가리킬 수 있다). 그 사이 뗀
첨부 · 거절된 업로드의 파일이 쌓이기만 했다. 정리는 **살아 있는 첨부가 하나도 안 가리키고
하루가 지난** 내용 주소 파일과, 끊긴 업로드의 임시 파일만 지운다 — 그 밖의 것(데이터 소스의
파일 폴더를 이 아래 두는 설치가 있다)은 손대지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.files import gc
from tests.api.conftest import Signed, finish_job
from tests.api.test_attachment_images import _attach, _png, _world

DAY_AGO = time.time() - 2 * 24 * 3600


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """**제 저장소에서만** — 개발 저장소의 파일을 지우지 않게."""
    root = tmp_path / "filestore"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "filestore_dir", root)
    return root


def _content_file(root: Path, body: bytes, *, old: bool = True) -> Path:
    digest = hashlib.sha256(body).hexdigest()
    path = root / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if old:
        os.utime(path, (DAY_AGO, DAY_AGO))
    return path


def _age_all(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (DAY_AGO, DAY_AGO))


def test_고아만_지우고_쓰이는_것_새것_다른_것은_남긴다(
    client: TestClient, admin: Signed, store: Path
) -> None:
    w = _world(client, admin)
    kept = _attach(client, admin, w["id"], content=_png(320, 200))
    assert kept.status_code == 201, kept.text
    gone = _attach(client, admin, w["id"], content=_png(330, 200))
    assert gone.status_code == 201, gone.text
    removed = client.delete(f"/api/attachments/{gone.json()['id']}", headers=admin.headers)
    assert removed.status_code == 204, removed.text
    _age_all(store)
    stray = _content_file(store, b"rejected upload")  # 거절된 업로드 — 행이 없다
    fresh = _content_file(store, b"just uploaded", old=False)  # 행이 아직 안 생겼을 수 있다
    other = store / "incoming" / "suppliers.csv"  # 데이터 소스의 파일 폴더
    other.parent.mkdir()
    other.write_text("VendorNo,Name\n")
    os.utime(other, (DAY_AGO, DAY_AGO))
    temp = store / "_incoming" / "tmpabc123"
    temp.parent.mkdir(exist_ok=True)
    temp.write_bytes(b"half")
    os.utime(temp, (DAY_AGO, DAY_AGO))

    planned = client.post(
        "/api/jobs", data={"kind": "filestore_gc", "params": "{}"}, headers=admin.headers
    )
    assert planned.status_code == 202, planned.text
    plan = finish_job(client, admin, planned.json())
    assert plan["status"] == "done", plan
    result = plan["result"]
    # 뗀 첨부의 원본 · 미리보기와 거절된 업로드 — 셋. 아직 아무것도 안 지웠다.
    assert result["orphans"] == 3 and result["temp"] == 1 and result["recent"] == 1, result
    assert result["ok"] is True and stray.exists()

    applied = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done", done
    assert done["result"]["removed"] == 3 and done["result"]["temp"] == 1, done["result"]

    assert not stray.exists() and not temp.exists()
    assert fresh.exists() and other.exists()
    # 쓰이는 첨부는 그대로 내려받힌다.
    got = client.get(f"/api/attachments/{kept.json()['id']}/content", headers=admin.headers)
    assert got.status_code == 200


def test_훑은_뒤_같은_내용이_다시_올라오면_지우지_않는다(
    client: TestClient, admin: Signed, store: Path, db: Session
) -> None:
    """같은 내용은 한 파일이다 — 고아로 골라 둔 파일을 새 첨부가 가리키게 되면 남긴다."""
    w = _world(client, admin)
    body = _png(300, 300)
    first = _attach(client, admin, w["id"], content=body)
    client.delete(f"/api/attachments/{first.json()['id']}", headers=admin.headers)
    _age_all(store)
    found = gc.scan(db)
    assert len(found.orphans) == 2  # 원본 · 미리보기

    again = _attach(client, admin, w["id"], content=body)
    assert again.status_code == 201, again.text
    done = gc.clean(db, found)
    assert done.files == 0 and done.kept == 2
    got = client.get(f"/api/attachments/{again.json()['id']}/content", headers=admin.headers)
    assert got.status_code == 200


def test_시스템_관리자만_정리한다(client: TestClient, member: Signed, store: Path) -> None:
    planned = client.post(
        "/api/jobs", data={"kind": "filestore_gc", "params": "{}"}, headers=member.headers
    )
    # 넣는 순간에 거절한다 — 몇 분 뒤 「실패」 로 알게 하지 않는다.
    assert planned.status_code == 403, planned.text
    assert "시스템 관리자" in planned.json()["error"]["message"]
