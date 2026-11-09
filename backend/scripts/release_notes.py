"""판 기록 — 운영자가 갱신 **전에** 알 것: 무엇이 바뀌나 · DB 가 바뀌나 · 데이터를 지우나.

릴리스 본문은 설치 절차뿐이었다(PR 을 안 써서 자동 노트가 빈다). 판마다의 주의는 판 올림 커밋
메시지와 ADR 곳곳에만 있어, 운영자는 「이번 판은 DB 가 바뀌나 · 되돌릴 수 있나」 를 알 자리가
없었다. 운영 안내서는 「파괴적 마이그레이션이면 DB 는 백업에서 복구 · 이중화는 두 대를 잇달아」
라고 말하지만, 이번 판이 그런 판인지는 말하지 않았다.

두 가지로 쓴다(표준 라이브러리만 — 릴리스 러너에서도, 이미지 안에서도 돈다):

    notes --since <앞 릴리스 태그>
        저장소(git)에서 판 기록 마크다운을 만든다 — 번들의 `CHANGES.md` 와 릴리스 본문이
        같은 글이다(`deploy/build_bundle.sh`).
    pending --from <DB 리비전>
        새 이미지 안에서 **지금 DB 부터** 끝까지의 마이그레이션을 센다(`deploy.sh update`).
        서버는 판을 건너뛰어 올 수 있어, 앞 릴리스와 견준 기록만으로는 모자란다.

「데이터를 지운다」 는 `upgrade()` 안의 표 · 칸 지우기(`drop_table` · `drop_column`)와
원문 SQL 의 `DROP TABLE` · `DROP COLUMN` · `TRUNCATE` · `DELETE FROM` 이다. 색인 · 제약
지우기는 데이터를 안 잃어 세지 않는다. `downgrade()` 는 안 본다 — 되돌릴 때의 일이다.
이름 바꾸기(`rename_table` · `alter_column(new_column_name=)`)는 따로 적는다(데이터는
그대로지만 앞 판 코드가 못 읽는다).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = "backend/migrations/versions"
ENV_EXAMPLE = "deploy/.env.production.example"

#: 원문 SQL 에서 데이터를 지우는 말.
_SQL_DROPS = re.compile(
    r"\b(DROP\s+TABLE|DROP\s+COLUMN|TRUNCATE|DELETE\s+FROM)\b[^;'\"]{0,60}", re.IGNORECASE
)
#: 판 올림 커밋 — 「바뀐 것」 에 안 넣는다(그 판의 요약이라 겹친다).
_VERSION_BUMP = re.compile(r"^버전 \d+\.\d+\.\d+")
#: .env 보기 파일의 키 줄 — 주석으로 둔 키(`# KEY=`)도 그 판이 아는 키다.
_ENV_KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=", re.MULTILINE)


@dataclass
class Migration:
    revision: str
    down: list[str]
    title: str
    drops: list[str] = field(default_factory=list)
    renames: list[str] = field(default_factory=list)


def _texts(node: ast.AST) -> list[str]:
    """호출 안의 글자 상수 전부 — `op.execute("…")` · `sa.text("…")` · f-문자열 조각."""
    return [
        one.value
        for one in ast.walk(node)
        if isinstance(one, ast.Constant) and isinstance(one.value, str)
    ]


def _literal(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "?"


def parse_migration(source: str) -> Migration | None:
    """마이그레이션 파일 하나 — 리비전 · 앞 리비전 · 설명 첫 줄 · 지우기 · 이름 바꾸기."""
    tree = ast.parse(source)
    found: dict[str, ast.AST] = {}
    for top in tree.body:
        if isinstance(top, ast.AnnAssign) and isinstance(top.target, ast.Name) and top.value:
            found[top.target.id] = top.value
        elif isinstance(top, ast.Assign):
            for target in top.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = top.value
    revision = found.get("revision")
    if not (isinstance(revision, ast.Constant) and isinstance(revision.value, str)):
        return None
    raw_down = found.get("down_revision")
    down: list[str] = []
    if isinstance(raw_down, ast.Constant) and isinstance(raw_down.value, str):
        down = [raw_down.value]
    elif isinstance(raw_down, ast.Tuple | ast.List):
        down = [_literal(one) for one in raw_down.elts]
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    one = Migration(revision=revision.value, down=down, title=doc[0] if doc else "")
    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None
    )
    for node in ast.walk(upgrade) if upgrade else ():
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name, args = node.func.attr, node.args
        if name == "drop_table":
            one.drops.append(f"표 지우기 {_literal(args[0] if args else None)}")
        elif name == "drop_column":
            # `op.drop_column(표, 칸)` · 묶음 안의 `batch.drop_column(칸)`.
            where = ".".join(_literal(arg) for arg in args[:2])
            one.drops.append(f"칸 지우기 {where}")
        elif name == "execute":
            for text in _texts(node):
                for hit in _SQL_DROPS.finditer(text):
                    one.drops.append(f"SQL: {' '.join(hit.group(0).split())}")
        elif name == "rename_table":
            one.renames.append(f"표 이름 {' → '.join(_literal(arg) for arg in args[:2])}")
        elif name == "alter_column":
            new = next((k.value for k in node.keywords if k.arg == "new_column_name"), None)
            if new is not None:
                where = ".".join(_literal(arg) for arg in args[:2])
                one.renames.append(f"칸 이름 {where} → {_literal(new)}")
    return one


def scan(directory: Path) -> dict[str, Migration]:
    out: dict[str, Migration] = {}
    for path in sorted(directory.glob("*.py")):
        one = parse_migration(path.read_text(encoding="utf-8"))
        if one is not None:
            out[one.revision] = one
    return out


def pending(migrations: dict[str, Migration], current: str | None) -> list[Migration]:
    """`current` 다음부터 끝(head)까지 — 적용할 차례대로. `current` 가 없으면(빈 DB) 처음부터.

    모르는 리비전이면 빈 목록이다 — DB 가 이 이미지보다 앞선 것이고, update 가 따로 막는다."""
    children: dict[str | None, list[str]] = {}
    for one in migrations.values():
        parents: list[str | None] = [*one.down] or [None]
        for parent in parents:
            children.setdefault(parent, []).append(one.revision)
    if current is not None and current not in migrations:
        return []
    out: list[Migration] = []
    frontier = sorted(children.get(current, []))
    seen: set[str] = set()
    while frontier:
        revision = frontier.pop(0)
        if revision in seen:
            continue
        seen.add(revision)
        out.append(migrations[revision])
        frontier.extend(sorted(children.get(revision, [])))
    return out


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def _headline(subject: str) -> str:
    """커밋 제목의 머리 — 「제목 — 설명」 의 앞. 머리가 「고침」 처럼 짧으면 뒤를 쓴다."""
    head, sep, rest = subject.partition(" — ")
    # 머리가 짧으면 뒤의 첫 문장만 — 「고침 — A 를 고친다. 까닭은 …」.
    text = head if not sep or len(head) > 4 else rest.split(". ", 1)[0]
    return text if len(text) <= 90 else text[:89] + "…"


def _env_keys(revision: str) -> set[str]:
    try:
        return set(_ENV_KEY.findall(_git("show", f"{revision}:{ENV_EXAMPLE}")))
    except subprocess.CalledProcessError:
        return set()


def _rollback(found: list[Migration]) -> list[str]:
    drops = [one for one in found if one.drops]
    if not found:
        return [
            "- **DB 구조가 안 바뀐다.** 갱신 뒤 문제가 있으면 앞 판 이미지"
            "(`app.sif.prev`)로 되돌리면 된다(README 「갱신」)."
        ]
    lines = [
        f"- **DB 구조가 바뀐다 — 마이그레이션 {len(found)}개.** 앞 판 이미지"
        "(`app.sif.prev`)로 손으로 되돌릴 수는 있지만 DB 는 새 구조 그대로다(마이그레이션은 "
        "취소되지 않는다). `deploy.sh update` 로 앞 판을 다시 까는 길은 멈춘다 — 앞 판은 "
        "새 DB 를 모른다.",
    ]
    if drops:
        names = " · ".join(one.revision.split("_", 1)[0] for one in drops)
        lines.append(
            f"- ⚠ **데이터를 지우는 마이그레이션이 있다**({names}). 되돌려도 지운 값은 "
            "돌아오지 않는다 — 갱신 **전에** 백업을 확인한다(update 가 최근 백업 시각을 "
            "보여 주고 묻는다). 이중화는 두 대를 빠르게 잇달아 올린다(README 8.2 — 옛 "
            "이미지가 도는 동안 오류가 날 수 있다)."
        )
    else:
        lines.append(
            "- 데이터를 지우는 마이그레이션은 없다(표 · 칸을 더하거나 바꾸기만 한다)."
        )
    return lines


def notes(since: str, revision: str, version: str) -> str:
    """앞 릴리스(`since`) 이후의 판 기록."""
    out = [f"# {version} 판 기록", ""]
    if not since:
        out += [
            "앞 릴리스를 몰라 이 판에서 바뀐 것을 적지 못했다. `deploy.sh update` 가 갱신할 "
            "때 **지금 DB 기준으로** 마이그레이션과 데이터를 지우는 것을 보여 준다.",
            "",
        ]
        return "\n".join(out)
    when = _git("log", "-1", "--format=%cs", since).strip()
    subjects = [
        line
        for line in _git(
            "log", "--no-merges", "--format=%s", f"{since}..{revision}"
        ).splitlines()
        if line and not _VERSION_BUMP.match(line)
    ]
    added = [
        path
        for path in _git(
            "diff", "--name-only", "--diff-filter=A", since, revision, "--", VERSIONS
        ).splitlines()
        if re.search(r"/\d{4}_[^/]*\.py$", path)
    ]
    found = [
        one
        for one in (
            parse_migration(_git("show", f"{revision}:{path}")) for path in sorted(added)
        )
        if one is not None
    ]
    before, now = _env_keys(since), _env_keys(revision)
    out += [f"앞 릴리스 {since}({when}) 이후 — 커밋 {len(subjects)}개.", ""]
    out += ["## 갱신 전에 볼 것", "", *_rollback(found)]
    new_keys, gone_keys = sorted(now - before), sorted(before - now)
    if new_keys:
        out.append(
            "- 새로 생긴 `.env` 값: "
            + " · ".join(f"`{key}`" for key in new_keys)
            + " — 뜻 · 기본값은 `운영-env-설정.md`."
        )
    if gone_keys:
        out.append("- 없어진 `.env` 값: " + " · ".join(f"`{key}`" for key in gone_keys))
    if not new_keys and not gone_keys:
        out.append("- `.env` 의 키는 그대로다.")
    if found:
        out += ["", "## DB 마이그레이션", "", "| 리비전 | 내용 | 지우기 · 이름 바꾸기 |"]
        out.append("| --- | --- | --- |")
        for one in found:
            marks = " · ".join([*(f"⚠ {d}" for d in one.drops), *one.renames]) or "—"
            title = one.title.replace("|", "\\|")
            out.append(f"| `{one.revision}` | {title} | {marks} |")
    out += ["", "## 바뀐 것", ""]
    out += [f"- {_headline(one)}" for one in reversed(subjects)] or ["- (없음)"]
    out.append("")
    return "\n".join(out)


def _pending_text(found: list[Migration]) -> str:
    """update 가 읽는 꼴 — 첫 줄은 셈(`pending=<n> drops=<m>`), 다음은 사람이 읽는 줄."""
    drops = [one for one in found if one.drops]
    lines = [f"pending={len(found)} drops={len(drops)}"]
    for one in found:
        lines.append(f"  {one.revision} — {one.title}")
        lines += [f"    ⚠ {d}" for d in one.drops]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="action", required=True)
    one = sub.add_parser("notes", help="앞 릴리스 이후의 판 기록(마크다운)")
    one.add_argument("--since", default="", help="앞 릴리스 태그(비우면 「모른다」)")
    one.add_argument("--rev", default="HEAD")
    one.add_argument("--version", default="")
    two = sub.add_parser("pending", help="지금 DB 리비전부터 끝까지의 마이그레이션")
    two.add_argument("--from", dest="current", default="", help="`alembic current` 의 리비전")
    two.add_argument("--versions", default=str(ROOT / VERSIONS))
    args = parser.parse_args(argv)
    if args.action == "notes":
        print(notes(args.since, args.rev, args.version or args.rev))
    else:
        found = pending(scan(Path(args.versions)), args.current or None)
        print(_pending_text(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
