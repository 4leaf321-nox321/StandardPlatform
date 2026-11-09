"""판 기록(`scripts/release_notes.py`) — 갱신 전에 볼 「DB 가 바뀌나 · 데이터를 지우나」.

update 는 새 이미지 안에서 이것으로 **지금 DB 부터** 마이그레이션을 센다. 그래서 파일 하나라도
못 읽으면 사슬이 끊겨 적게 세고, 지우는 것을 놓치면 백업 확인 없이 지나간다 — 실제 마이그레이션
전부로 본다.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "migrations" / "versions"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_notes", BACKEND / "scripts" / "release_notes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass 가 자기 모듈을 `sys.modules` 에서 찾는다 — 안 넣으면 정의하다 깨진다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


notes = _load()

MIGRATION = '''"""{title}

Revision ID: {rev}
"""

import sqlalchemy as sa
from alembic import op

revision: str = "{rev}"
down_revision = {down!r}


def upgrade() -> None:
{body}


def downgrade() -> None:
    op.drop_column("parts", "late")  # 되돌릴 때의 지우기는 안 센다
'''


def _migration(rev: str, down: str | None, body: str, title: str = "제목") -> str:
    return MIGRATION.format(title=title, rev=rev, down=down, body=body)


def test_지우기와_이름_바꾸기를_upgrade_에서만_센다() -> None:
    body = "\n".join(
        "    " + line
        for line in (
            'op.add_column("parts", sa.Column("x", sa.Integer()))',
            'op.drop_index("ix_parts_x", table_name="parts")  # 색인은 데이터가 아니다',
            'op.drop_column("parts", "restricted")',
            "op.execute(\"DELETE FROM audit_logs WHERE kind = 'x'\")",
            'op.execute(sa.text("UPDATE parts SET x = 1"))  # 고치기는 지우기가 아니다',
            'op.alter_column("parts", "name", new_column_name="label")',
            'with op.batch_alter_table("vendors") as batch:',
            '    batch.drop_column("note")',
        )
    )
    source = _migration("0002_x", "0001_a", body, title="부품 칸 정리 — 길다")
    one = notes.parse_migration(source)
    assert one.revision == "0002_x" and one.down == ["0001_a"]
    assert one.title == "부품 칸 정리 — 길다"
    assert sorted(one.drops) == [
        "SQL: DELETE FROM audit_logs WHERE kind =",
        "칸 지우기 note",
        "칸 지우기 parts.restricted",
    ]
    assert one.renames == ["칸 이름 parts.name → label"]


def test_실제_마이그레이션을_모두_읽고_한_줄로_잇는다() -> None:
    """하나라도 못 읽으면 update 가 「지금 DB 부터」 를 적게 센다."""
    files = [path for path in VERSIONS.glob("*.py") if re.match(r"\d{4}_", path.name)]
    found = notes.scan(VERSIONS)
    assert len(found) == len(files)
    chain = notes.pending(found, None)
    assert [one.revision for one in chain] == sorted(found)
    # 지금까지 데이터를 지운 것(칸 지우기) — 빠지면 update 가 백업을 묻지 않고 지나간다.
    dropping = {one.revision[:4] for one in found.values() if one.drops}
    assert {"0031", "0041", "0045", "0082"} <= dropping


def test_지금_DB_부터_끝까지_세고_모르는_리비전이면_세지_않는다() -> None:
    found = notes.scan(VERSIONS)
    revisions = sorted(found)
    middle = revisions[-3]
    assert [one.revision for one in notes.pending(found, middle)] == revisions[-2:]
    assert notes.pending(found, revisions[-1]) == []
    # DB 가 이 이미지보다 앞섰다 — update 가 따로 막는다(여기서 세면 엉뚱하다).
    assert notes.pending(found, "9999_future") == []


def test_update_가_읽는_첫_줄은_셈이다() -> None:
    found = notes.scan(VERSIONS)
    revisions = sorted(found)
    text = notes._pending_text(notes.pending(found, revisions[-3]))
    first, *rest = text.splitlines()
    assert first == "pending=2 drops=0"
    assert rest[0].startswith(f"  {revisions[-2]} — ")


def _git(repo: Path, *args: str) -> None:
    who = ["-c", "user.name=t", "-c", "user.email=t@example.local"]
    subprocess.run(["git", "-C", str(repo), *who, *args], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git 이 없다")
def test_판_기록은_앞_릴리스_이후의_변경_마이그레이션_새_env_값을_적는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    versions = repo / "backend" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (repo / "deploy").mkdir()
    (versions / "0001_base.py").write_text(_migration("0001_base", None, "    pass"), "utf-8")
    example = repo / "deploy" / ".env.production.example"
    example.write_text("APP_NAME=1\n# OLD_KEY=\n", "utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "버전 0.1.0 — 처음")
    _git(repo, "tag", "v0.1.0")
    monkeypatch.setattr(notes, "ROOT", repo)
    same = notes.notes("v0.1.0", "HEAD", "v0.1.0")
    assert "DB 구조가 안 바뀐다" in same and "`.env` 의 키는 그대로다" in same

    body = '    op.drop_column("parts", "note")'
    (versions / "0002_drop_note.py").write_text(
        _migration("0002_drop_note", "0001_base", body, title="메모 칸을 지운다"), "utf-8"
    )
    example.write_text("APP_NAME=1\n# NEW_KEY=\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "고침 — 메모 칸을 지운다. 까닭은 길게 적었다")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "검색 순위 — 꼭 맞는 것을 맨 위로")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "버전 0.1.1 — 요약")

    text = notes.notes("v0.1.0", "HEAD", "v0.1.1")
    assert text.startswith("# v0.1.1 판 기록")
    assert "커밋 2개" in text  # 판 올림 커밋은 안 센다
    assert "마이그레이션 1개" in text
    assert "⚠ **데이터를 지우는 마이그레이션이 있다**(0002)" in text
    assert "| `0002_drop_note` | 메모 칸을 지운다 | ⚠ 칸 지우기 parts.note |" in text
    assert "새로 생긴 `.env` 값: `NEW_KEY`" in text and "없어진 `.env` 값: `OLD_KEY`" in text
    # 머리가 짧은 커밋(「고침 — …」)은 뒤의 첫 문장, 차례는 오래된 것부터.
    assert "- 메모 칸을 지운다\n- 검색 순위\n" in text
    assert "버전 0.1.1" not in text.split("## 바뀐 것")[1]


def test_앞_릴리스를_모르면_모른다고_적는다() -> None:
    text = notes.notes("", "HEAD", "ci-abc")
    assert "앞 릴리스를 몰라" in text and "지금 DB 기준으로" in text
