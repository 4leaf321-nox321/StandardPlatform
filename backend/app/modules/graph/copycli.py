"""그래프 사본 싣기의 앱 쪽 — `deploy/graphcopy.sh` 가 이미지 안에서 부른다(4단계).

    python -m app.modules.graph.copycli export /data/graphdb/import
    python -m app.modules.graph.copycli indexes /data/graphdb/import

`export` 는 한 트랜잭션(REPEATABLE READ — 노드와 선이 같은 때의 것)에서 CSV · `manifest.json` ·
`import.args` 를 쓴다. `indexes` 는 바꿔 끼운 사본이 뜬 뒤 색인을 세운다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.database import SessionLocal
from app.modules.graph import copyexport


def _export(root: Path) -> int:
    with SessionLocal() as db:
        db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        manifest = copyexport.export(db, root)
        copyexport.write_args(manifest, root, root)
        db.rollback()
    print(
        f"내보냄: 노드 {manifest.nodes:,} · 선 {manifest.relationships:,} · "
        f"{manifest.seconds}초 → {root}"
    )
    return 0


def _indexes(root: Path) -> int:
    from app.modules.graph import cypher

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    labels = sorted(
        part["name"].split(":", 1)[1]
        for part in manifest["parts"]
        if part["kind"] == "nodes" and part["rows"] and ":" in part["name"]
    )
    made = cypher.build_indexes(labels)
    print(f"색인 {made}개(라벨 {len(labels)}개)")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("export", "indexes"):
        print(
            "쓰는 법: python -m app.modules.graph.copycli export|indexes <폴더>",
            file=sys.stderr,
        )
        return 2
    root = Path(argv[1])
    return _export(root) if argv[0] == "export" else _indexes(root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
