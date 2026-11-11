"""SPARQL 사본 싣기(ADR 0070) — 배포의 밤 타이머(`<slug>-rdfcopy`)가 이미지 안에서 부른다.

    python -m app.modules.rdf.copycli load /data/rdfstore

1. 내보내기 — 한 트랜잭션(REPEATABLE READ)에서 데이터 N-Triples · 정의(OWL)를 `import/` 에.
2. 쉬는 쪽 폴더(`slot-a` / `slot-b`)를 비우고 Oxigraph 로 통째로 싣는다(`bulk_load`), 다지기.
3. `manifest.json` 을 쓰고 `active` 를 바꿔 끼운다(이름 바꾸기 한 번 — 웹은 다음 질의부터 새
판).
4. 내보낸 파일을 지운다. 앞 판은 다음 밤까지 둔다(그 사이 도는 질의가 읽는다).

실패하면 `active` 는 그대로다 — 앞 판이 계속 답한다.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from app.database import SessionLocal
from app.modules.rdf import copyexport
from app.modules.rdf.store import ACTIVE, MANIFEST


def load(root: Path) -> int:
    from pyoxigraph import RdfFormat, Store

    started = time.monotonic()
    root.mkdir(parents=True, exist_ok=True)
    active = root / ACTIVE
    current = os.readlink(active) if active.is_symlink() else None
    slot = "slot-b" if current == "slot-a" else "slot-a"
    staging = root / "import"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    data, schema = staging / "data.nt", staging / "schema.nt"
    with SessionLocal() as db:
        db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with data.open("w", encoding="utf-8", buffering=1024 * 1024) as out:
            written = copyexport.write(db, out)
        written.triples += copyexport.schema(db, schema)
        db.rollback()
    print(
        f"내보냄: 객체 {written.objects:,} · 트리플 {written.triples:,} · {written.seconds}초",
        flush=True,
    )
    target = root / slot
    shutil.rmtree(target, ignore_errors=True)
    mark = time.monotonic()
    store = Store(str(target))
    store.bulk_load(path=str(data), format=RdfFormat.N_TRIPLES)
    store.bulk_load(path=str(schema), format=RdfFormat.N_TRIPLES)
    store.flush()
    store.optimize()
    del store
    loaded = round(time.monotonic() - mark, 2)
    copyexport.manifest(written, target / MANIFEST)
    fresh = root / f"{ACTIVE}.new"
    if fresh.is_symlink() or fresh.exists():
        fresh.unlink()
    fresh.symlink_to(slot)
    os.replace(fresh, active)
    shutil.rmtree(staging, ignore_errors=True)
    size = sum(one.stat().st_size for one in target.rglob("*") if one.is_file())
    print(
        f"실음: {slot} · 싣기 {loaded}초 · 디스크 {size / 2**30:.2f}GB · "
        f"모두 {time.monotonic() - started:.1f}초",
        flush=True,
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "load":
        print("쓰는 법: python -m app.modules.rdf.copycli load <폴더>", file=sys.stderr)
        return 2
    return load(Path(argv[1]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
