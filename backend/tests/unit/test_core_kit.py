"""연동 키트(`sp_core_pull.py`) — **받는 쪽이 지켜야 할 규칙을 그 파일이 지키나.**

키트는 상대 회사에서 도는 한 파일이다. 여기서 시험하지 않으면 「고쳤다」 를 확인할 자리가
우리 쪽에 하나도 없고, 틀린 것은 남의 서버에서 조용히 틀린다.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

KIT = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "coreapi"
    / "kit"
    / "sp_core_pull.py"
)


def _kit() -> Any:
    """키트를 불러온다 — `requests` 는 우리 쪽에 없다(상대가 깔 것이다)."""
    if "requests" not in sys.modules:
        fake = types.ModuleType("requests")
        fake.RequestException = type("RequestException", (Exception,), {})  # type: ignore[attr-defined]
        fake.Response = object  # type: ignore[attr-defined]
        sys.modules["requests"] = fake
    spec = importlib.util.spec_from_file_location("sp_core_pull", KIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(path: Path, table: str) -> list[tuple[Any, ...]]:
    db = sqlite3.connect(path)
    try:
        return list(db.execute(f'SELECT src, dst FROM "{table}" ORDER BY src'))
    finally:
        db.close()


def test_reset_이_오면_비우고_처음부터_받는다(tmp_path: Path) -> None:
    """**빈 쪽을 「변경 없음」 으로 읽으면 이미 끊긴 선을 영영 들고 있는다.**

    오래 안 받아 가면 공개 측은 끊긴 선을 알려 줄 수 없다고 말한다(무덤은 한동안만 보관).
    키트는 그 말을 듣지 못해 `since` 를 그대로 들고 다시 물었고, 그러면 사라진 선이 자기
    쪽에 영구히 남았다.
    """
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    # 그 사이에 끊긴 선 하나가 이미 저장돼 있다.
    kit.save_relations(
        cfg,
        "part",
        [{"src": "P-사라짐", "relation": "supplies", "dst": "ACME", "updated_at": "1"}],
    )
    assert _rows(tmp_path / "sp_core.sqlite", "part__relations") == [("P-사라짐", "ACME")]

    pages = [
        {
            "as_of": None,
            "items": [],
            "reset": True,
            "reset_reason": "30일보다 오래된 시점부터는 끊긴 선을 알려 줄 수 없습니다",
        },
        {
            "as_of": "2026-09-30T00:00:00.000000Z",
            "next": None,
            "items": [
                {"src": "P-1", "relation": "supplies", "dst": "ACME", "updated_at": "2"}
            ],
        },
    ]
    asked: list[dict[str, Any]] = []

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        asked.append(dict(params or {}))
        return pages.pop(0)

    kit.request_json = fake
    state = {"part#relations": "2020-01-01T00:00:00.000000Z"}
    counts = kit.pull_relations(cfg, "part", state)

    # 첫 물음은 저장한 시각으로, **다시 묻는 물음은 시각 없이.**
    assert asked[0].get("since") == "2020-01-01T00:00:00.000000Z", asked
    assert "since" not in asked[1], asked
    assert counts["reset"] == 1 and counts["rows"] == 1
    # 옛 선은 비웠고 새로 받은 것만 남았다.
    assert _rows(tmp_path / "sp_core.sqlite", "part__relations") == [("P-1", "ACME")]
    # 끝까지 받았으니 시각을 옮긴다.
    assert state["part#relations"] == "2026-09-30T00:00:00.000000Z"


def test_reset_이_두_번_오면_멈추고_말한다(tmp_path: Path) -> None:
    """`since` 를 비웠는데도 다시 오면 무한히 돌 자리다 — 멈추고 사람에게 말한다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    kit.request_json = lambda *_a, **_k: {"items": [], "reset": True, "reset_reason": "x"}
    try:
        kit.pull_relations(cfg, "part", {"part#relations": "2020-01-01T00:00:00.000000Z"})
    except SystemExit as stopped:
        assert "reset" in str(stopped)
    else:  # pragma: no cover - 돌면 안 되는 자리
        raise AssertionError("두 번째 reset 에서 멈춰야 한다")


def test_더_이상_안_보이는_행은_이름과_칸을_두고_비활성만_한다(tmp_path: Path) -> None:
    """`hidden` — 지운 것이 아니라 그 토큰으로 더 이상 못 보게 된 것(소유 부서 이동)이다.
    공개 측은 옮긴 뒤의 이름 · 칸을 싣지 않으므로(식별자만), 그대로 덮으면 가진 이름이 식별자로
    바뀌고 칸이 비었다(2026-10-08)."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    kit.save_rows(
        cfg,
        "vendor",
        [
            {
                "key": "MOV-1",
                "label": "옮길 공급사",
                "status": "active",
                "updated_at": "1",
                "deleted": False,
                "properties": {"grade": "A"},
            }
        ],
    )
    kit.save_rows(
        cfg,
        "vendor",
        [
            {
                "key": "MOV-1",
                "label": "MOV-1",
                "status": "active",
                "updated_at": "2",
                "deleted": True,
                "hidden": True,
                "properties": {},
            },
            # 받은 적 없는 것의 무덤은 새로 만들지 않는다.
            {
                "key": "NEW-1",
                "label": "NEW-1",
                "updated_at": "2",
                "deleted": True,
                "hidden": True,
            },
        ],
    )
    db = sqlite3.connect(tmp_path / "sp_core.sqlite")
    try:
        rows = list(db.execute('SELECT key, label, deleted, properties FROM "vendor"'))
    finally:
        db.close()
    assert rows == [("MOV-1", "옮길 공급사", 1, '{"grade": "A"}')]


def test_끝이_인터페이스인_선은_도착_타입까지_가려_저장한다(tmp_path: Path) -> None:
    """`key` 는 타입 안에서만 하나다 — 끝이 인터페이스인 관계는 같은 출발 · 관계 · 도착
    `key` 가 두 구현 타입에 걸칠 수 있다. 세 끝만 키로 두면 둘이 한 줄로 겹쳐 하나가
    사라졌다. 옛 키트가 만든 표(세 끝이 키)도 내용을 그대로 옮겨 키를 바꾼다(2026-10-08)."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    # 옛 키트가 만든 표 — 세 끝이 키다.
    old = sqlite3.connect(path)
    old.execute(
        'CREATE TABLE "part__relations" (src TEXT, relation TEXT, dst TEXT, dst_type TEXT, '
        "evidence_note TEXT, updated_at TEXT, properties TEXT, "
        "PRIMARY KEY (src, relation, dst))"
    )
    old.execute(
        'INSERT INTO "part__relations" VALUES '
        "('P-0', 'uses', 'EQ-0', 'tester', '', '1', '{}')"
    )
    old.commit()
    old.close()

    kit.save_relations(
        cfg,
        "part",
        [
            {"src": "P-1", "relation": "uses", "dst": "EQ-1", "dst_type": "tester"},
            {"src": "P-1", "relation": "uses", "dst": "EQ-1", "dst_type": "plant"},
        ],
    )
    db = sqlite3.connect(path)
    try:
        rows = sorted(db.execute('SELECT src, dst, dst_type FROM "part__relations"'))
    finally:
        db.close()
    assert rows == [
        ("P-0", "EQ-0", "tester"),  # 옛 표의 줄도 그대로 옮겨졌다
        ("P-1", "EQ-1", "plant"),
        ("P-1", "EQ-1", "tester"),
    ]

    # 끊긴 선은 **그 도착 타입의 것만** 지운다.
    kit.save_relations(
        cfg,
        "part",
        [
            {
                "src": "P-1",
                "relation": "uses",
                "dst": "EQ-1",
                "dst_type": "plant",
                "deleted": True,
            }
        ],
    )
    db = sqlite3.connect(path)
    try:
        rows = sorted(db.execute('SELECT src, dst, dst_type FROM "part__relations"'))
    finally:
        db.close()
    assert rows == [("P-0", "EQ-0", "tester"), ("P-1", "EQ-1", "tester")]
