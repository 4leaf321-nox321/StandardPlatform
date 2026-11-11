"""SPARQL 사본 싣기(ADR 0070 · 0071) — 배포의 밤 타이머(`<slug>-rdfcopy`)가 이미지 안에서
부른다.

    python -m app.modules.rdf.copycli load /data/rdfstore           # 밤마다 — 되면 바뀐 것만
    python -m app.modules.rdf.copycli load /data/rdfstore --full    # 통째로

**통째로(흘려 싣기)** — 내보내는 프로세스(자식 — 한 트랜잭션, REPEATABLE READ)가 정의 · 데이터
N-Triples 를 파이프로 흘리고, 이 프로세스가 받는 대로 쉬는 쪽 폴더에 싣는다(`bulk_load`). 임시
파일이 없다(2,000만 객체에서 90GB 였다). 자식은 다 흘린 뒤 **같은 트랜잭션에서** 지문
(`copystate`)을 짓는다 — 그 사이 이쪽은 다진다(`optimize`). 자식이 실패하면 실은 폴더를 버린다:
파이프가 줄 끝에서 끊기면 싣기는 「다 받았다」 로 끝날 수 있어, **자식의 종료 코드로만** 판다.

**바뀐 것만** — 지난 판의 지문과 지금 DB 의 지문을 견주고(`copystate.diff`), 지금 판을
체크포인트로 떠서(하드 링크) 바뀐 줄만 지우고 다시 넣은 뒤(`copyapply`), DB 가 말하는 개수와
견준다.

그날 통째로 하는 때: `--full` · 지난 판이나 지문이 없음 · 지문 판 · SP 판 · 정의가 다름 ·
주 1회(`RDF_COPY_FULL_WEEKDAY`) · 통째로 한 지 7일 · 바뀐 것이 10% 넘음 · 고친 뒤 개수가
어긋남 · 고치다 오류. 끄기: `RDF_COPY_CHANGES=false`(늘 통째로).

어느 쪽이든 쉬는 쪽 폴더에 짓고 `active` 를 이름 바꾸기 한 번으로 바꿔 끼운다. 실패하면
`active` 는 그대로다 — 앞 판이 계속 답한다. 앞 판은 다음 밤까지 둔다(그 사이 도는 질의가
읽는다).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.objects import edges as edge_rule
from app.modules.rdf import copyexport, copystate
from app.modules.rdf.copyexport import Shape
from app.modules.rdf.store import ACTIVE, MANIFEST

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: 통째로 한 지 이만큼 지나면 그날은 통째로(요일을 비웠거나 그 요일의 싣기가 실패했을 때).
FULL_EVERY_DAYS = 7
#: 바뀐 것이 지난 판의 이만큼을 넘으면 통째로 — 그쯤이면 통째로가 빠르고 깨끗하다.
CHANGED_RATIO = 0.1
CHANGED_FLOOR = 10_000
#: 이 표식이 사본 폴더에 있으면 다음 싣기는 통째로(`deploy.sh rdf-copy --full` 이 둔다) — 다
#: 실으면 지운다.
FULL_NEXT = "full-next"
#: 내보내기 → 싣기 파이프의 크기(리눅스 기본 64KB — 문맥 전환이 잦다).
PIPE_SIZE = 1 << 20
_F_SETPIPE_SZ = 1031
_BACKEND = Path(__file__).resolve().parents[3]
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_ALT = "http://www.w3.org/2004/02/skos/core#altLabel"


def _log(message: str) -> None:
    print(message, flush=True)


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _slots(root: Path) -> tuple[str | None, str]:
    active = root / ACTIVE
    current = os.readlink(active) if active.is_symlink() else None
    return current, "slot-b" if current == "slot-a" else "slot-a"


def _switch(root: Path, slot: str) -> None:
    fresh = root / f"{ACTIVE}.new"
    if fresh.is_symlink() or fresh.exists():
        fresh.unlink()
    fresh.symlink_to(slot)
    os.replace(fresh, root / ACTIVE)


def _size(path: Path) -> float:
    return sum(one.stat().st_size for one in path.rglob("*") if one.is_file()) / 2**30


def _repeatable(db: Session) -> None:
    db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")


# ── 통째로(흘려 싣기) ──


def export(staging: Path, fd: int) -> int:
    """자식 — 정의 · 데이터를 `fd` 에 흘리고 닫은 뒤, 같은 트랜잭션에서 지문 · 판 기록을
    `staging` 에 쓴다."""
    with SessionLocal() as db:
        _repeatable(db)
        shape = Shape.load(db)
        today = edge_rule.today()
        with os.fdopen(fd, "w", encoding="utf-8", buffering=PIPE_SIZE) as out:
            schema, schema_triples = copyexport.schema_text(db)
            out.write(schema)
            written = copyexport.write(db, out, shape, today=today)
        written.triples += schema_triples
        _log(
            f"내보냄: 객체 {written.objects:,} · 트리플 {written.triples:,} · "
            f"{written.seconds}초"
        )
        mark = time.monotonic()
        meta = {
            "format": copystate.FORMAT,
            "definitions": copystate.definitions(db),
            "full_on": today.isoformat(),
            "built_on": today.isoformat(),
            "copied_at": written.copied_at,
        }
        built = copystate.build(db, shape, staging / copystate.STATE, today=today, meta=meta)
        _log(
            f"지문: 객체 {built.objects:,} · 선 {built.relations:,} · "
            f"{time.monotonic() - mark:.1f}초"
        )
        (staging / MANIFEST).write_text(
            json.dumps({**asdict(written), "meta": meta}, ensure_ascii=False), encoding="utf-8"
        )
        db.rollback()
    return 0


def _exporter(staging: Path, fd: int) -> list[str]:
    """내보내는 자식의 명령 — 같은 파이썬 · 같은 앱(시험이 바꿔 끼운다)."""
    return [sys.executable, "-m", "app.modules.rdf.copycli", "export", str(staging), str(fd)]


def _full(root: Path, slot: str, reason: str, started: float) -> int:
    from pyoxigraph import RdfFormat, Store

    staging, target = root / "import", root / slot
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    shutil.rmtree(target, ignore_errors=True)
    read_fd, write_fd = os.pipe()
    try:
        import fcntl

        fcntl.fcntl(write_fd, _F_SETPIPE_SZ, PIPE_SIZE)
    except OSError:
        pass  # 서버의 파이프 상한이 더 작다 — 기본 크기로 간다
    child = subprocess.Popen(_exporter(staging, write_fd), pass_fds=(write_fd,), cwd=_BACKEND)
    os.close(write_fd)
    mark = time.monotonic()
    received = 0.0
    try:
        store = Store(str(target))
        with os.fdopen(read_fd, "rb", buffering=PIPE_SIZE) as stream:
            store.bulk_load(input=stream, format=RdfFormat.N_TRIPLES)
        received = round(time.monotonic() - mark, 2)
        store.flush()
        store.optimize()
        del store
    except Exception:
        traceback.print_exc()
        child.kill()
        child.wait()
        shutil.rmtree(target, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        _log("싣기가 실패했습니다 — 앞 판이 그대로 답합니다")
        return 1
    code = child.wait()
    if code != 0:
        shutil.rmtree(target, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        _log(f"내보내기가 실패했습니다(종료 코드 {code}) — 앞 판이 그대로 답합니다")
        return 1
    done = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
    meta = done.pop("meta")
    shutil.move(staging / copystate.STATE, target / copystate.STATE)
    manifest = {
        **done,
        "mode": "full",
        "reason": reason,
        "full_on": meta["full_on"],
        "received_seconds": received,
        "seconds": round(time.monotonic() - started, 1),
    }
    (target / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _switch(root, slot)
    shutil.rmtree(staging, ignore_errors=True)
    (root / FULL_NEXT).unlink(missing_ok=True)
    _log(
        f"실음(통째로 — {reason}): {slot} · 받으며 싣기 {received}초 · 디스크 "
        f"{_size(target):.2f}GB · 모두 {time.monotonic() - started:.1f}초"
    )
    return 0


# ── 바뀐 것만 ──


class _Full(Exception):
    """이번 밤은 통째로 — 까닭을 든다."""


def _when_full(meta: dict[str, str] | None, today: date) -> str | None:
    settings = get_settings()
    if not settings.rdf_copy_changes:
        return "바뀐 것만 고치기 꺼짐(RDF_COPY_CHANGES=false)"
    if meta is None:
        return "지난 판에 지문이 없음"
    if meta.get("format") != copystate.FORMAT:
        return "지문 판이 다름"
    try:
        full_on = date.fromisoformat(meta["full_on"])
    except (KeyError, ValueError):
        return "지난 통째로 싣기의 날짜를 모름"
    if (today - full_on).days >= FULL_EVERY_DAYS:
        return f"통째로 한 지 {(today - full_on).days}일"
    weekday = settings.rdf_copy_full_weekday.strip().lower()[:3]
    if weekday in WEEKDAYS and today.weekday() == WEEKDAYS.index(weekday) and full_on != today:
        return f"주 1회 통째로({weekday})"
    return None


def _verify(writer: Any, want: copystate.Expected, shape: Shape) -> str | None:
    """고친 사본의 개수를 DB 가 말하는 것과 견준다 — 다르면 그 첫 몇 개. 타입 · 인터페이스마다
    객체, 관계 종류마다 (출발, 도착) 짝, 참조 칸마다 줄, 구체화한 진술, 별칭."""
    wrong: list[str] = []
    got = writer.class_counts()
    for iri in sorted({head[1:-1] for heads in shape.heads.values() for head in heads}):
        if got.get(iri, 0) != want.classes.get(iri, 0):
            wrong.append(f"{iri} {got.get(iri, 0)} ≠ {want.classes.get(iri, 0)}")
    expected = {one.value: 0 for one in writer.relations}
    expected.update({iri[1:-1]: 0 for iri in shape.ref_pred.values()})
    expected.update(want.relations)
    expected.update(want.refs)
    for iri, count in sorted(expected.items()):
        have = writer.count(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <{iri}> ?o }}")
        if have != count:
            wrong.append(f"{iri} {have} ≠ {count}")
    said = writer.count(
        f"SELECT (COUNT(*) AS ?n) WHERE {{ ?b <{_RDF}type> <{_RDF}Statement> }}"
    )
    if said != want.statements:
        wrong.append(f"구체화한 진술 {said} ≠ {want.statements}")
    prefix = f"{shape.iris.names.base}o/"
    names = writer.count(
        f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <{_ALT}> ?o "
        f'FILTER(STRSTARTS(STR(?s), "{prefix}")) }}'
    )
    if names != want.aliases:
        wrong.append(f"별칭 {names} ≠ {want.aliases}")
    return "; ".join(wrong[:5]) or None


def _changes(root: Path, current: str | None, slot: str, started: float) -> None:
    """바뀐 것만 고쳐 바꿔 끼운다. 통째로 해야 하면 `_Full`."""
    if current is None:
        raise _Full("지난 판 없음")
    if (root / FULL_NEXT).exists():
        raise _Full(f"운영자가 통째로를 고름({FULL_NEXT})")
    active, target = root / current, root / slot
    state = active / copystate.STATE
    meta = copystate.read_meta(state)
    today = edge_rule.today()
    reason = _when_full(meta, today)
    if reason is not None:
        raise _Full(reason)
    assert meta is not None
    from app.modules.rdf import copyapply

    with SessionLocal() as db:
        _repeatable(db)
        copied_at = _stamp()
        prints = copystate.definitions(db)
        if prints != meta.get("definitions"):
            raise _Full("정의(타입 · 칸 · 인터페이스 · 관계 종류)나 SP 판이 바뀜")
        shape = Shape.load(db)
        total = int(meta.get("objects", "0")) + int(meta.get("relations", "0"))
        limit = max(CHANGED_FLOOR, int(total * CHANGED_RATIO))
        mark = time.monotonic()
        try:
            plan = copystate.diff(db, shape, state, today=today, limit=limit)
        except copystate.TooMany as why:
            raise _Full(f"바뀐 것이 많음({why})") from why
        except copystate.OutOfOrder as why:
            raise _Full(f"지문의 차례가 어긋남({why})") from why
        _log(
            f"바뀐 것: 객체 {len(plan.objects):,} · 선 {len(plan.relations):,} "
            f"(지문 견주기 {time.monotonic() - mark:.1f}초)"
        )
        mark = time.monotonic()
        copyapply.checkpoint(active, target)
        shutil.copyfile(state, target / copystate.STATE)
        relation_iris = {shape.iris.relation(slug)[1:-1] for slug in plan.relation_slugs}
        relation_iris |= {
            shape.iris.relation(slug)[1:-1]
            for (slug,) in db.execute(text("SELECT slug FROM relation_types"))
        }
        writer = copyapply.Writer(target, relation_iris)
        wrong: str | None = None
        try:
            for change in plan.objects:
                if change.old is not None:
                    writer.drop_object(change.old, moved=change.moved)
            keys = plan.keys()
            old = copystate.old_iris(
                state, {one[0] for one in keys} | {one[2] for one in keys}
            )
            writer.drop_keys(
                (old[src], shape.iris.relation(slug)[1:-1], old[dst])
                for src, slug, dst in keys
                if src in old and dst in old
            )
            for chunk in copystate.emit(db, shape, plan, today=today):
                writer.add(chunk)
            applied = time.monotonic() - mark
            mark = time.monotonic()
            if not plan.empty:
                want = copystate.expected(db, shape, today=today)
                wrong = _verify(writer, want, shape)
        finally:
            writer.close()
        db.rollback()
    if wrong is not None:
        shutil.rmtree(target, ignore_errors=True)
        raise _Full(f"고친 뒤 개수가 어긋남 — {wrong}")
    previous = json.loads((active / MANIFEST).read_text(encoding="utf-8"))
    counted = writer.counted
    next_meta = {
        **meta,
        "built_on": today.isoformat(),
        "copied_at": copied_at,
        "objects": str(plan.object_total),
        "relations": str(plan.relation_total),
    }
    copystate.advance(target / copystate.STATE, plan, next_meta)
    manifest = {
        **previous,
        "copied_at": copied_at,
        "objects": plan.object_total,
        "triples": int(previous.get("triples") or 0) - counted.removed + counted.added,
        "mode": "changes",
        "reason": "",
        "changed_objects": len(plan.objects),
        "changed_relations": len(plan.relations),
        "removed_triples": counted.removed,
        "added_triples": counted.added,
        "seconds": round(time.monotonic() - started, 1),
    }
    (target / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _switch(root, slot)
    _log(
        f"고침(바뀐 것만): {slot} · 지운 줄 {counted.removed:,} · 넣은 줄 {counted.added:,} · "
        f"고치기 {applied:.1f}초 · 견주기 {time.monotonic() - mark:.1f}초 · "
        f"모두 {time.monotonic() - started:.1f}초"
    )


def load(root: Path, *, full: bool = False) -> int:
    started = time.monotonic()
    root.mkdir(parents=True, exist_ok=True)
    current, slot = _slots(root)
    reason = "요청(--full)" if full else ""
    if not full:
        try:
            _changes(root, current, slot, started)
            return 0
        except _Full as why:
            reason = str(why)
        except Exception:  # 고치다 막히면 통째로 — 사본이 낡는 것보다 낫다
            traceback.print_exc()
            reason = "바뀐 것만 고치다 오류"
            shutil.rmtree(root / slot, ignore_errors=True)
    _log(f"통째로 싣는다 — {reason}")
    return _full(root, slot, reason, started)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "export":
        return export(Path(argv[1]), int(argv[2]))
    if len(argv) in (2, 3) and argv[0] == "load" and argv[2:] in ([], ["--full"]):
        return load(Path(argv[1]), full=argv[2:] == ["--full"])
    print("쓰는 법: python -m app.modules.rdf.copycli load <폴더> [--full]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
