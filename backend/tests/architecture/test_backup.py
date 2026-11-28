"""`deploy/backup.sh` — **첨부 미러는 바로 지우지 않는다**(2026-11-28 점검).

미러가 한 벌뿐인데 `rsync --delete` 로 맞췄다 — 첨부 정리 · 실수로 지움 · 빈 폴더로 다시
마운트가 그날 밤 그대로 미러에서도 지워져, 원본과 같은 날 백업까지 잃었다. 작업
파일(`_jobs/`) · 받다 만 업로드(`_incoming/`)도 담았다. 가짜 `pg_dump` 를 PATH 앞에 두고
진짜 `rsync` 로 스크립트를 그대로 돌린다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKUP = REPO / "deploy" / "backup.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("rsync") is None or os.name == "nt",
    reason="bash · rsync 가 있는 리눅스에서만",
)

FAKE_PG_DUMP = """#!/bin/sh
for one in "$@"; do
  case "$one" in --file=*) printf 'PGDMP fake' > "${one#--file=}" ;; esac
done
"""


class Box:
    """설치 하나 — .env · 첨부 폴더 · 백업 폴더 · 가짜 pg_dump."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = root / "filestore"
        self.out = root / "backup"
        self.bin = root / "bin"
        self.bin.mkdir()
        (self.bin / "pg_dump").write_text(FAKE_PG_DUMP, encoding="utf-8")
        (self.bin / "pg_dump").chmod(0o755)
        self.env = root / ".env"
        self.env.write_text(
            "DATABASE_URL=postgresql+psycopg://app:secret@localhost:5432/demo\n",
            encoding="utf-8",
        )
        self.store.mkdir()

    def put(self, rel: str, body: str = "x") -> None:
        path = self.store / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                shutil.which("bash") or "bash",
                str(BACKUP),
                "-e",
                str(self.env),
                "-f",
                str(self.store),
                "-o",
                str(self.out),
            ],
            env={"PATH": f"{self.bin}:{os.environ['PATH']}", **env},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def mirrored(self) -> set[str]:
        base = self.out / "filestore"
        return {str(one.relative_to(base)) for one in base.rglob("*") if one.is_file()}

    def deleted_today(self) -> Path:
        return self.out / "filestore-deleted" / datetime.now().strftime("%Y%m%d")

    def last(self) -> str:
        return (self.out / "LAST_BACKUP.txt").read_text(encoding="utf-8")


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_원본에서_사라진_첨부는_미러에서_지우지_않고_그날의_칸으로_옮긴다(box: Box) -> None:
    box.put("ab/cd/keep")
    box.put("ab/cd/gone", "소중한 파일")
    first = box.run()
    assert first.returncode == 0, first.stderr
    assert box.mirrored() == {"ab/cd/keep", "ab/cd/gone"}

    (box.store / "ab" / "cd" / "gone").unlink()  # 첨부 정리 · 실수로 지움
    second = box.run()
    assert second.returncode == 0, second.stderr
    assert box.mirrored() == {"ab/cd/keep"}
    kept = box.deleted_today() / "ab" / "cd" / "gone"
    assert kept.read_text(encoding="utf-8") == "소중한 파일"  # 같은 경로로 — rsync 로 되살린다
    assert "1개를" in box.last() and "filestore-deleted" in box.last()


def test_한꺼번에_절반_넘게_지우게_되면_이번에는_안_지우고_경고한다(box: Box) -> None:
    """원본이 빈 폴더로 마운트되면 미러 전부가 「지울 것」 이 된다 — 그날 밤 백업까지 비우지
    않는다. 새 파일은 그래도 복사한다."""
    for n in range(150):
        box.put(f"aa/{n:03d}")
    assert box.run().returncode == 0
    shutil.rmtree(box.store / "aa")
    box.put("bb/new")

    done = box.run()

    assert done.returncode == 0, done.stderr
    assert len(box.mirrored()) == 151 and "bb/new" in box.mirrored()
    assert not box.deleted_today().exists()
    assert "첨부 미러의 삭제를 건너뜀" in done.stderr
    warned = [line for line in box.last().splitlines() if line.startswith("경고")]
    assert warned and "150개" in warned[0] and "ALLOW_MASS_DELETE=1" in warned[0]

    # 정말 지운 것이면 한 번만 허락한다 — 지운 것은 그날의 칸으로(14일).
    allowed = box.run(ALLOW_MASS_DELETE="1")
    assert allowed.returncode == 0, allowed.stderr
    assert box.mirrored() == {"bb/new"}
    assert len(list((box.deleted_today() / "aa").iterdir())) == 150
    assert not any(line.startswith("경고") for line in box.last().splitlines())


def test_작업_파일과_받다_만_업로드는_미러에_안_담는다(box: Box) -> None:
    box.put("ab/cd/real")
    box.put("_jobs/big.zip", "zip")
    box.put("_incoming/half.part", "part")
    # 앞 판의 미러가 담아 둔 것 — 빠진 파일 칸으로 옮긴다(바로 지우지 않는다).
    old = box.out / "filestore" / "_jobs"
    old.mkdir(parents=True)
    (old / "old.zip").write_text("old", encoding="utf-8")

    done = box.run()

    assert done.returncode == 0, done.stderr
    assert box.mirrored() == {"ab/cd/real"}
    assert (box.deleted_today() / "_jobs" / "old.zip").exists()


def test_빠진_첨부의_칸은_14일이_지나면_지운다(box: Box) -> None:
    deleted = box.out / "filestore-deleted"
    stale = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    fresh = (datetime.now() - timedelta(days=13)).strftime("%Y%m%d")
    for day in (stale, fresh):
        (deleted / day).mkdir(parents=True)
        (deleted / day / "f").write_text("x", encoding="utf-8")
    (deleted / "notes").mkdir()  # 날짜가 아닌 것은 건드리지 않는다

    done = box.run()

    assert done.returncode == 0, done.stderr
    assert not (deleted / stale).exists()
    assert (deleted / fresh / "f").exists() and (deleted / "notes").exists()
