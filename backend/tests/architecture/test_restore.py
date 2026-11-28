"""`deploy/restore.sh` — **운영을 비운 채 끝나지 않고, 확인용이 운영 첨부를 덮지 않는다.**

가짜 `psql` · `pg_restore` · `rsync` · `sudo` · `id` 를 PATH 앞에 두고 스크립트를 그대로
돌린다. 가짜는 받은 명령을 **누가**(`psql(app)` · `psql(postgres)`) 보냈는지와 함께 차례대로
적고, DB 목록을 파일 하나(`state.json`)로 들고 있어 만들기 · 지우기 · 이름 바꾸기를 흉내 낸다.
보는 것은 **차례**다 — 덤프를 읽어 보기 전에, 임시 DB 의 검사를 통과하기 전에 운영 DB 를
건드리는 명령이 하나라도 있으면 안 된다.

DB 를 만들고 이름을 바꾸는 계정 — root 로 DB 서버에서 돌리면 그 서버의 postgres(앱이 쓰는
클러스터인지 견준 뒤), 아니면 앱 계정. 앱 계정에 CREATEDB 가 없으면 아무것도 안 바꾸고 멈춘다
(앱 계정에 그 권한을 주지 않기로 했다).

전에는 덤프를 보기 전에 운영 DB 를 `DROP` 했고, pg_restore 의 실패는 `|| true` 로 넘기고 「표가
하나라도 있나」 만 봤다. 그리고 확인용 DB 이름에 `-i` 를 붙이면 `-f` 없이 운영 첨부 폴더로
`rsync --delete` 를 했다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
RESTORE = REPO / "deploy" / "restore.sh"
SOURCE = "demo"
TEMP = f"{SOURCE}__restoring"

LISTING = """\
;
; Archive created at 2026-11-28 03:00:00 KST
;
215; 1259 16386 TABLE public alembic_version app
216; 1259 16390 TABLE public attachments app
3401; 0 16386 TABLE DATA public alembic_version app
3402; 0 16390 TABLE DATA public attachments app
"""

#: 가짜 psql — SQL 을 보고 답한다. DB 목록은 `state.json` 이 든다.
FAKE_PSQL = r"""
import json, os, re, sys

state_path = os.environ["FAKE_STATE"]
with open(state_path, encoding="utf-8") as fh:
    state = json.load(fh)
args = sys.argv[1:]
sql = ""
for i, one in enumerate(args):
    if one in ("-c", "-tAc"):
        sql = args[i + 1]
database = args[-1]
who = os.environ.get("FAKE_AS", "app")
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as fh:
    fh.write("psql(" + who + ")[" + database + "] " + " ".join(sql.split()) + "\n")
if who == "postgres" and state.get("local_down"):
    sys.stderr.write("psql: error: connection to server on socket failed\n")
    sys.exit(2)


def save():
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def fail(message):
    sys.stderr.write("ERROR:  " + message + "\n")
    sys.exit(1)


dbs = state["dbs"]
DEFAULT_LOCALE = "en_US.UTF-8|en_US.UTF-8|c||UTF8"
locales = state.setdefault("locales", {})
if "pg_stat_activity" in sql:
    # 접속 하나 = 이름(앱 계정의 접속) 또는 {"app", "user"} — 계정이 없으면 autovacuum
    # 작업자다.
    name = re.search(r"datname='([^']+)'", sql).group(1)
    for one in state.get("connections", {}).get(name, []):
        one = {"app": one, "user": "app"} if isinstance(one, str) else one
        if one["user"] is None and "usename IS NOT NULL" in sql:
            continue
        print(one["app"] or one["user"] or "")
elif "datcollate" in sql:
    name = re.search(r"datname='([^']+)'", sql).group(1)
    if name in dbs:
        print(locales.get(name, state.get("default_locale", DEFAULT_LOCALE)))
elif "pg_is_in_recovery" in sql:
    print("t" if state.get("standby") else "f")
elif "pg_control_system" in sql:
    print(state.get("cluster", {}).get(who, "2026-11-26 07:54:39+09 7639844039051170998"))
elif "rolcreatedb" in sql:
    print("t" if state.get("createdb", True) else "f")
elif "rolsuper" in sql:
    print("f")
elif "datdba" in sql:
    name = re.search(r"datname='([^']+)'", sql).group(1)
    print(state.get("owners", {}).get(name, "app"))
elif "FROM pg_database" in sql:
    name = re.search(r"datname='([^']+)'", sql).group(1)
    print("1" if name in dbs else "")
elif sql.startswith("CREATE DATABASE"):
    name = re.search(r'"([^"]+)"', sql).group(1)
    if state.get("create_fails") or name in dbs:
        fail("permission denied to create database")
    dbs.append(name)
    # 정렬 — 적은 대로(template0), 안 적으면 서버의 기본(template1).
    keys = r"(LC_COLLATE|LC_CTYPE|ICU_LOCALE|LOCALE_PROVIDER) '?([^' ]+)'?"
    given = dict(re.findall(keys, sql))
    if "LC_COLLATE" in given and not state.get("create_ignores_locale"):
        provider = {"icu": "i", "builtin": "b"}.get(given.get("LOCALE_PROVIDER", ""), "c")
        icu = given.get("ICU_LOCALE", "")
        parts = [given["LC_COLLATE"], given["LC_CTYPE"], provider, icu, "UTF8"]
        locales[name] = "|".join(parts)
    save()
elif sql.startswith("DROP DATABASE"):
    name = re.search(r'"([^"]+)"', sql).group(1)
    if name in dbs:
        dbs.remove(name)
    save()
elif sql.startswith("ALTER DATABASE"):
    # 여러 문장은 한 트랜잭션 — 하나라도 안 되면 아무것도 안 바뀐다.
    after = list(dbs)
    for old, new in re.findall(r'ALTER DATABASE "([^"]+)" RENAME TO "([^"]+)"', sql):
        if old not in after or new in after:
            fail("cannot rename " + old)
        after[after.index(old)] = new
    renames = dict(re.findall(r'ALTER DATABASE "([^"]+)" RENAME TO "([^"]+)"', sql))
    state["dbs"] = after
    state["locales"] = {renames.get(key, key): value for key, value in locales.items()}
    save()
elif "information_schema.tables" in sql:
    print(state.get("tables", 5))
elif "to_regclass" in sql:
    print("t" if state.get("alembic", "0107") is not None else "f")
elif "alembic_version LIMIT 1" in sql:
    print(state.get("alembic", "0107") or "")
elif "information_schema.columns" in sql:
    print("")
"""

#: 가짜 pg_restore — `-l` 은 목록을, 아니면 받은 DB 를 적고 정해 둔 오류로 끝난다.
FAKE_PG_RESTORE = r"""
import json, os, sys

with open(os.environ["FAKE_STATE"], encoding="utf-8") as fh:
    state = json.load(fh)
args = sys.argv[1:]
calls = open(os.environ["FAKE_CALLS"], "a", encoding="utf-8")
if "-l" in args:
    calls.write("pg_restore -l\n")
    if state.get("listing") is None:
        sys.stderr.write("pg_restore: error: input file is not a valid archive\n")
        sys.exit(1)
    sys.stdout.write(state["listing"])
    sys.exit(0)
target = args[args.index("--dbname") + 1]
user = args[args.index("--username") + 1]
calls.write("pg_restore --dbname " + target + " --username " + user + "\n")
sys.stderr.write(state.get("restore_stderr", ""))
sys.exit(state.get("restore_rc", 0))
"""

#: 가짜 sudo — `sudo -u <계정> <명령>` 을 그 계정인 척(`FAKE_AS`) 돌린다.
FAKE_SUDO = r"""
import os, sys

args = sys.argv[1:]
assert args[0] == "-u", args
os.environ["FAKE_AS"] = args[1]
os.execvp(args[2], args[2:])
"""

#: 가짜 id — `id -u` 만 정해 둔 값으로(root 인 척). 시험을 root 로 돌려도 기본은 root 가
#: 아니다.
FAKE_ID = r"""
import json, os, sys

if sys.argv[1:] == ["-u"]:
    with open(os.environ["FAKE_STATE"], encoding="utf-8") as fh:
        print(json.load(fh).get("uid", 1000))
else:
    os.execv("/usr/bin/id", ["id", *sys.argv[1:]])
"""

FAKE_RSYNC = r"""
import os, sys

with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as fh:
    fh.write("rsync " + " ".join(sys.argv[1:]) + "\n")
"""


class Box:
    """가짜 도구와 백업 폴더 한 벌."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.backup = root / "backup"
        (self.backup / "env").mkdir(parents=True)
        (self.backup / "db").mkdir()
        (self.backup / "filestore" / "ab" / "cd").mkdir(parents=True)
        (self.backup / "filestore" / "ab" / "cd" / "f1").write_text("x", encoding="utf-8")
        self.host("localhost")
        (self.backup / "db" / "db-20261128T030000.dump").write_bytes(b"PGDMP fake")
        self.bin = root / "bin"
        self.bin.mkdir()
        for name, body in (
            ("psql", FAKE_PSQL),
            ("pg_restore", FAKE_PG_RESTORE),
            ("rsync", FAKE_RSYNC),
            ("sudo", FAKE_SUDO),
            ("id", FAKE_ID),
        ):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            path.chmod(0o755)
        self.calls = root / "calls.log"
        self.calls.write_text("", encoding="utf-8")
        self.state_path = root / "state.json"
        self.state({"dbs": [SOURCE], "listing": LISTING})

    def host(self, name: str) -> None:
        """백업이 받아 둔 .env 의 DB 주소 — 이 서버(`localhost`)인가, 다른 서버인가."""
        (self.backup / "env" / ".env").write_text(
            f"APP_SLUG=demo\nDATABASE_URL=postgresql+psycopg://app:pw@{name}:5432/{SOURCE}\n",
            encoding="utf-8",
        )

    def state(self, value: dict[str, Any] | None = None) -> dict[str, Any]:
        if value is not None:
            self.state_path.write_text(json.dumps(value), encoding="utf-8")
        return dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def tune(self, **values: Any) -> None:
        self.state({**self.state(), **values})

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "FAKE_STATE": str(self.state_path),
            "FAKE_CALLS": str(self.calls),
            "TMPDIR": str(self.root),
            "LANG": "C.UTF-8",
        }
        return subprocess.run(
            [shutil.which("bash") or "bash", str(RESTORE), "-b", str(self.backup), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )

    def lines(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines()


def _index(lines: list[str], text: str) -> int:
    return next(i for i, one in enumerate(lines) if text in one)


def _touches(lines: list[str], name: str) -> list[str]:
    """그 DB 를 지우거나 이름을 바꾸는 명령."""
    return [
        one
        for one in lines
        if (one.split("] ", 1)[-1].startswith(("DROP DATABASE", "ALTER DATABASE")))
        and f'"{name}"' in one
    ]


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_실제_복구는_임시_DB_에_풀고_검사한_뒤에야_이름을_바꾸고_옛_DB_를_남긴다(
    box: Box,
) -> None:
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    lines = box.lines()
    listed = _index(lines, "pg_restore -l")
    created = _index(lines, f'CREATE DATABASE "{TEMP}"')
    restored = _index(lines, f"pg_restore --dbname {TEMP}")
    checked = _index(lines, "alembic_version LIMIT 1")
    renamed = _index(lines, f'ALTER DATABASE "{SOURCE}" RENAME TO "{SOURCE}__before_restore_')
    assert listed < created < restored < checked < renamed
    # 운영 DB 는 지우지 않는다 — 이름만 비켜 둔다(임시 DB 의 이름 바꾸기와 한 문장에).
    assert not any("DROP DATABASE" in one and f'"{SOURCE}"' in one for one in lines)
    assert f'ALTER DATABASE "{TEMP}" RENAME TO "{SOURCE}"' in lines[renamed]
    # root 가 아니면 앱 계정이 만들고(주인도 앱 계정) 바꾼다 — 그 계정에 CREATEDB 가 있을 때.
    assert lines[created].startswith("psql(app)") and 'OWNER "app"' in lines[created]
    assert lines[renamed].startswith("psql(app)")
    dbs = box.state()["dbs"]
    kept = [one for one in dbs if one.startswith(f"{SOURCE}__before_restore_")]
    assert SOURCE in dbs and len(kept) == 1 and TEMP not in dbs
    # 지우는 명령은 사람에게 적어 준다.
    assert f"dropdb --host=localhost --port=5432 --username=app {kept[0]}" in done.stdout


@pytest.mark.parametrize(
    "state",
    [
        pytest.param({"listing": None}, id="덤프를_못_읽음"),
        pytest.param(
            {"listing": "; 목록만\n215; 1259 16386 TABLE public t app\n"}, id="행이_없음"
        ),
    ],
)
def test_덤프를_읽어_보기_전에는_어느_DB_도_건드리지_않는다(
    box: Box, state: dict[str, Any]
) -> None:
    box.tune(**state)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "아무것도 바꾸지 않았습니다" in done.stderr
    lines = box.lines()
    assert not [one for one in lines if "CREATE DATABASE" in one or _touches([one], SOURCE)]
    assert not any(one.startswith("pg_restore --dbname") for one in lines)
    assert box.state()["dbs"] == [SOURCE]


@pytest.mark.parametrize(
    ("state", "said"),
    [
        pytest.param(
            {
                "restore_rc": 1,
                "restore_stderr": "pg_restore: error: could not read from input file\n",
            },
            "중간에 멈췄습니다",
            id="중간에_멈춤",
        ),
        pytest.param(
            {
                "restore_rc": 1,
                "restore_stderr": (
                    "pg_restore: error: could not execute query: ERROR:  type does not exist\n"
                    "Command was: CREATE TABLE public.object_embeddings (\n"
                    'pg_restore: error: COPY failed for table "notes": ERROR:  bad\n'
                    "pg_restore: warning: errors ignored on restore: 2\n"
                ),
            },
            "넣지 못한 것이 2 건",
            id="표와_행을_못_넣음",
        ),
        pytest.param({"tables": 0}, "표가 하나도", id="표가_없음"),
        pytest.param({"alembic": None}, "alembic_version 표가 없습니다", id="판_표가_없음"),
        pytest.param({"alembic": ""}, "alembic_version 이 비어", id="판이_비었음"),
    ],
)
def test_임시_DB_의_검사에_걸리면_임시_DB_만_지우고_운영은_그대로다(
    box: Box, state: dict[str, Any], said: str
) -> None:
    box.tune(**state)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert said in done.stderr, done.stderr
    lines = box.lines()
    assert not _touches(lines, SOURCE), lines
    assert any(f'DROP DATABASE IF EXISTS "{TEMP}"' in one for one in lines)
    assert box.state()["dbs"] == [SOURCE]


def test_무해한_오류만이면_복구한다(box: Box) -> None:
    """앞에서 만든 `extensions` 스키마 · postgres 가 가진 확장의 주석 — 늘 나는 오류다."""
    box.tune(
        restore_rc=1,
        restore_stderr=(
            "pg_restore: error: could not execute query: ERROR:  already exists\n"
            "Command was: CREATE SCHEMA extensions;\n"
            "pg_restore: error: could not execute query: ERROR:  must be owner\n"
            "Command was: COMMENT ON EXTENSION vector IS 'vector data type';\n"
            "pg_restore: warning: errors ignored on restore: 2\n"
        ),
    )
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    assert "무해한 오류 2 건" in done.stderr


def test_접속이_남아_있으면_풀기_전에_멈추고_누가_붙었는지_적는다(box: Box) -> None:
    box.tune(connections={SOURCE: ["demo-worker", "demo"]})
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "접속 2 개" in done.stderr and "demo-worker" in done.stderr
    assert "sudo systemctl stop demo demo-worker demo-mcp" in done.stderr
    lines = box.lines()
    assert not any("CREATE DATABASE" in one for one in lines)
    assert box.state()["dbs"] == [SOURCE]


def test_임시_DB_를_못_만들면_운영은_그대로다(box: Box) -> None:
    """예전에는 운영을 지운 다음에 만들었다 — 만들기가 실패하면 운영이 없는 채 끝났다."""
    box.tune(create_fails=True)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "만들지 못했습니다" in done.stderr
    assert not _touches(box.lines(), SOURCE)
    assert box.state()["dbs"] == [SOURCE]


def _changes(lines: list[str]) -> list[str]:
    return [
        one
        for one in lines
        if one.split("] ", 1)[-1].startswith(
            ("CREATE DATABASE", "DROP DATABASE", "ALTER DATABASE")
        )
        or one.startswith(("pg_restore --dbname", "rsync "))
    ]


def test_root_로_DB_서버에서_돌리면_postgres_계정이_만들고_바꾸고_풀기는_앱_계정이다(
    box: Box,
) -> None:
    """앱 계정에 CREATEDB 를 주지 않는다 — 그래도 root 로 DB 서버에서 돌리면 된다."""
    box.tune(uid=0, createdb=False, listing=LISTING + "3; 3079 16385 EXTENSION - vector\n")
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    assert "이 서버의 postgres" in done.stdout
    lines = box.lines()
    # 같은 클러스터인지 두 접속에서 견준 뒤에.
    assert any(
        one.startswith("psql(postgres)") and "pg_control_system" in one for one in lines
    )
    assert any(one.startswith("psql(app)") and "pg_control_system" in one for one in lines)
    created = lines[_index(lines, f'CREATE DATABASE "{TEMP}"')]
    assert created.startswith("psql(postgres)[postgres]") and 'OWNER "app"' in created
    vector = lines[_index(lines, "CREATE EXTENSION IF NOT EXISTS vector")]
    assert vector.startswith(f"psql(postgres)[{TEMP}]")
    assert f"pg_restore --dbname {TEMP} --username app" in lines
    renamed = lines[_index(lines, f'ALTER DATABASE "{SOURCE}" RENAME TO')]
    assert renamed.startswith("psql(postgres)")
    # 대기 · 주인 · 접속은 앱이 보는 접속으로 본다.
    assert lines[_index(lines, "pg_is_in_recovery")].startswith("psql(app)")
    assert lines[_index(lines, "pg_stat_activity")].startswith("psql(app)")


@pytest.mark.parametrize(
    ("root", "host"),
    [
        pytest.param(False, "localhost", id="root_가_아님"),
        pytest.param(True, "db.example", id="DB_가_다른_서버"),
    ],
)
def test_앱_계정에_CREATEDB_가_없으면_아무것도_안_바꾸고_할_일을_적는다(
    box: Box, root: bool, host: str
) -> None:
    box.host(host)
    # 다른 서버면 이 서버의 postgres 는 다른 클러스터다(또는 없다).
    box.tune(uid=0 if root else 1000, createdb=False, cluster={"postgres": "다른 클러스터"})
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "CREATEDB" in done.stderr and "아무것도 바꾸지 않았습니다" in done.stderr
    assert 'ALTER ROLE "app" CREATEDB' in done.stderr
    assert f"sudo ./restore.sh -b {box.backup} -d {SOURCE} -f" in done.stderr
    lines = box.lines()
    assert not _changes(lines), lines
    assert not any(one.startswith("psql(postgres)") and "DATABASE" in one for one in lines)
    assert box.state()["dbs"] == [SOURCE]


def test_DB_주소가_이름이어도_같은_클러스터면_postgres_계정으로(box: Box) -> None:
    """이중화의 주 서버 — 앱은 DB VIP 로 붙지만 그 VIP 가 이 서버에 있다."""
    box.host("db.example")
    box.tune(uid=0, createdb=False)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    lines = box.lines()
    assert lines[_index(lines, f'CREATE DATABASE "{TEMP}"')].startswith("psql(postgres)")


def test_root_인데_이_서버의_postgres_가_안_받으면_앱_계정으로(box: Box) -> None:
    box.tune(uid=0, local_down=True)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    lines = box.lines()
    assert lines[_index(lines, f'CREATE DATABASE "{TEMP}"')].startswith("psql(app)")


def test_이_서버의_postgres_가_앱이_쓰는_클러스터가_아니면_멈춘다(box: Box) -> None:
    """같은 포트에 다른 클러스터 — 거기서 만들고 이름을 바꾸면 엉뚱한 DB 를 건드린다."""
    box.tune(
        uid=0,
        cluster={"app": "2026-11-26 07:54:39+09 1", "postgres": "2026-11-20 01:00:00+09 2"},
    )
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "다릅니다" in done.stderr and "아무것도 바꾸지 않았습니다" in done.stderr
    assert not _changes(box.lines())
    assert box.state()["dbs"] == [SOURCE]


@pytest.mark.parametrize("root", [False, True], ids=["앱_계정", "postgres"])
def test_대기_서버면_아무것도_안_바꾼다(box: Box, root: bool) -> None:
    box.tune(uid=0 if root else 1000, standby=True)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "대기(복제본)" in done.stderr
    assert not _changes(box.lines())
    assert box.state()["dbs"] == [SOURCE]


def test_pgvector_덤프를_root_없이_돌리면_풀기_전에_멈춘다(box: Box) -> None:
    box.tune(listing=LISTING + "3; 3079 16385 EXTENSION - vector\n")
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "pgvector" in done.stderr and "sudo ./restore.sh" in done.stderr
    assert not _changes(box.lines())


@pytest.mark.parametrize("root", [False, True], ids=["앱_계정", "postgres"])
def test_주인이_앱_계정이_아닌_DB_는_비켜_두지_않는다(box: Box, root: bool) -> None:
    box.tune(uid=0 if root else 1000, owners={SOURCE: "someone_else"})
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "someone_else" in done.stderr
    assert not _changes(box.lines())


def test_비어_있지_않은_첨부_폴더는_DB_이름이_무엇이든_f_가_있어야_한다(box: Box) -> None:
    """확인용 이름에 `-F` 를 붙였다고 운영 첨부를 덮지 않는다."""
    store = box.root / "live-filestore"
    (store / "zz").mkdir(parents=True)
    (store / "zz" / "after-backup").write_text("y", encoding="utf-8")
    check = f"{SOURCE}_restore_check"

    refused = box.run("-d", check, "-F", str(store))
    assert refused.returncode != 0
    assert "비어 있지 않습니다" in refused.stderr
    assert box.lines() == [], "DB 도 첨부도 건드리기 전에 멈춘다"

    done = box.run("-d", check, "-F", str(store), "-f")
    assert done.returncode == 0, done.stderr
    synced = [one for one in box.lines() if one.startswith("rsync ")]
    # 백업 뒤에 올린 파일은 남긴다 — 비켜 둔 옛 DB 로 돌아갈 때 그 파일이 있어야 한다.
    assert len(synced) == 1 and "--delete" not in synced[0]


def test_빈_첨부_폴더에는_f_없이_되돌린다(box: Box) -> None:
    store = box.root / "new-filestore"
    store.mkdir()
    done = box.run("-d", f"{SOURCE}_restore_check", "-F", str(store))
    assert done.returncode == 0, done.stderr
    assert any(one.startswith("rsync ") for one in box.lines())


def test_있는_DB_는_f_없이_비켜_두지_않는다(box: Box) -> None:
    check = f"{SOURCE}_restore_check"
    box.tune(dbs=[SOURCE, check])
    done = box.run("-d", check)
    assert done.returncode != 0
    assert "이미 있습니다" in done.stderr
    assert not any("CREATE DATABASE" in one for one in box.lines())


def test_autovacuum_작업자는_접속으로_세지_않는다(box: Box) -> None:
    """서비스를 다 멈춘 직후에도 autovacuum 은 그 DB 이름을 달고 돈다 — 이름 바꾸기가 내보낸다.

    세던 때는 「접속 1 개: ?」 로 정상 복구를 거절했다. 계정이 없는 것(작업자)만 뺀다 — 앱
    계정에게는 남의 접속의 backend_type 이 가려 보인다.
    """
    autovacuum = {"app": "", "user": None}
    box.tune(connections={SOURCE: [autovacuum]})
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    query = box.lines()[_index(box.lines(), "pg_stat_activity")]
    assert "usename IS NOT NULL" in query

    # 계정이 있는 접속(남의 계정이어도)은 그대로 막는다.
    box.state({"dbs": [SOURCE], "listing": LISTING})
    box.tune(connections={SOURCE: [autovacuum, {"app": "", "user": "report_bot"}]})
    refused = box.run("-d", SOURCE, "-f")
    assert refused.returncode != 0
    assert "접속 1 개" in refused.stderr and "report_bot" in refused.stderr


@pytest.mark.parametrize(
    ("existing", "options", "shown"),
    [
        pytest.param(
            "C.UTF-8|C.UTF-8|c||UTF8",
            "TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8'",
            "C.UTF-8 / C.UTF-8 (libc)",
            id="libc",
        ),
        pytest.param(
            "C.UTF-8|C.UTF-8|i|ko-KR|UTF8",
            "LOCALE_PROVIDER icu ICU_LOCALE 'ko-KR'",
            "C.UTF-8 / C.UTF-8 (ICU ko-KR)",
            id="ICU",
        ),
    ],
)
def test_임시_DB_는_지금_DB_의_정렬로_만든다(
    box: Box, existing: str, options: str, shown: str
) -> None:
    """C.UTF-8 로 다시 만든 운영 DB 가 복구 한 번에 서버의 기본(en_US)으로 돌아가지 않게."""
    box.tune(locales={SOURCE: existing})
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode == 0, done.stderr
    lines = box.lines()
    assert options in lines[_index(lines, f'CREATE DATABASE "{TEMP}"')]
    assert f"정렬: {shown} — 지금 '{SOURCE}' 과 같게" in done.stdout
    after = box.state()["locales"]
    assert after[SOURCE] == existing
    assert any(key.startswith(f"{SOURCE}__before_restore_") for key in after)


def test_맞출_DB_가_없으면_서버의_기본으로_만들고_무엇이_됐는지_적는다(box: Box) -> None:
    check = f"{SOURCE}_restore_check"
    done = box.run("-d", check)
    assert done.returncode == 0, done.stderr
    lines = box.lines()
    created = lines[_index(lines, f'CREATE DATABASE "{TEMP.replace(SOURCE, check)}"')]
    assert "template0" not in created and "LC_COLLATE" not in created
    assert "정렬: en_US.UTF-8 / en_US.UTF-8 (libc) — 맞출" in done.stdout
    assert "template1" in done.stdout


def test_정렬이_다르게_만들어지면_운영은_그대로다(box: Box) -> None:
    box.tune(locales={SOURCE: "C.UTF-8|C.UTF-8|c||UTF8"}, create_ignores_locale=True)
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "정렬" in done.stderr and "다르게" in done.stderr
    assert not _touches(box.lines(), SOURCE)
    assert box.state()["dbs"] == [SOURCE]


def test_정렬_값을_못_읽으면_아무것도_안_바꾼다(box: Box) -> None:
    """값은 CREATE DATABASE 에 글자로 들어간다 — 따옴표가 섞이면 엉뚱한 문장이 된다."""
    box.tune(locales={SOURCE: "C.UTF-8' x|C.UTF-8|c||UTF8"})
    done = box.run("-d", SOURCE, "-f")
    assert done.returncode != 0
    assert "정렬을 읽지 못했습니다" in done.stderr
    assert not any("CREATE DATABASE" in one for one in box.lines())
