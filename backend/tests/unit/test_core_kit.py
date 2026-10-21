"""연동 키트(`sp_core_pull.py`) — **받는 쪽이 지켜야 할 규칙을 그 파일이 지키나.**

키트는 상대 회사에서 도는 한 파일이다. 여기서 시험하지 않으면 「고쳤다」 를 확인할 자리가
우리 쪽에 하나도 없고, 틀린 것은 남의 서버에서 조용히 틀린다.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from app.shared.errors import code

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


def test_reset_이_오면_처음부터_받고_끝까지_받은_뒤_안_온_선을_끊는다(tmp_path: Path) -> None:
    """**빈 쪽을 「변경 없음」 으로 읽으면 이미 끊긴 선을 영영 들고 있는다.**

    오래 안 받아 가면 공개 측은 끊긴 선을 알려 줄 수 없다고 말한다(무덤은 한동안만 보관).
    키트는 시각 없이 처음부터 다시 받고, **마지막 쪽까지 받은 뒤** 이번에 안 온 선을 끊는다.
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
            "reset_reason": "90일보다 오래된 시점부터는 끊긴 선을 알려 줄 수 없습니다",
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
    assert counts["reset"] == 1 and counts["rows"] == 1 and counts["gone"] == 1
    # 옛 선은 끊겼고 새로 받은 것만 남았다.
    assert _rows(tmp_path / "sp_core.sqlite", "part__relations") == [("P-1", "ACME")]
    # 끝까지 받았으니 시각을 옮긴다.
    assert state["part#relations"] == "2026-09-30T00:00:00.000000Z"


def test_reset_뒤_중간에_멈추면_아무것도_지우지_않는다(tmp_path: Path) -> None:
    """먼저 비우던 때는 다시 받다가 끊기면 **아직 안 받은 것까지 사라졌다** — 비우지 않고
    끝까지 받은 뒤에만 정리한다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    kit.save_relations(
        cfg, "part", [{"src": "P-0", "relation": "supplies", "dst": "ACME", "updated_at": "1"}]
    )
    pages: list[Any] = [
        {"items": [], "reset": True, "reset_reason": "x"},
        {
            "as_of": None,
            "next": "c1",
            "items": [{"src": "P-1", "relation": "supplies", "dst": "ACME"}],
        },
    ]

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        if not pages:
            raise SystemExit("연결하지 못했습니다")
        return pages.pop(0)

    kit.request_json = fake
    state = {"part#relations": "2020-01-01T00:00:00.000000Z"}
    with pytest.raises(SystemExit):
        kit.pull_relations(cfg, "part", state)
    assert _rows(tmp_path / "sp_core.sqlite", "part__relations") == [
        ("P-0", "ACME"),
        ("P-1", "ACME"),
    ]
    assert state["part#relations"] == "2020-01-01T00:00:00.000000Z"


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


def _all(path: Path, sql: str) -> list[tuple[Any, ...]]:
    db = sqlite3.connect(path)
    try:
        return sorted(db.execute(sql))
    finally:
        db.close()


def _obj(key: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": key, "status": "active", "updated_at": "1", **extra}


def test_식별자가_바뀌면_가진_행과_선을_새_key_로_옮긴다(tmp_path: Path) -> None:
    """`renamed_from` 을 안 보면 새 식별자가 처음 보는 것이 되어 **같은 것이 둘이 된다.**
    오래 안 받았으면 가진 것이 마지막 옛 식별자가 아니다(A→B→C 에서 A) — `previous_keys` 로
    찾는다. 옛 → 새 짝은 참조 칸을 풀 수 있게 표로 남긴다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    kit.save_rows(cfg, "vendor", [_obj("A", properties={"grade": "A"})])
    kit.save_rows(cfg, "part", [_obj("P-1")])
    kit.save_relations(
        cfg, "vendor", [{"src": "A", "relation": "owns", "dst": "P-1", "dst_type": "part"}]
    )
    kit.save_relations(
        cfg,
        "part",
        [{"src": "P-1", "relation": "supplied_by", "dst": "A", "dst_type": "vendor"}],
    )

    done = kit.save_rows(
        cfg, "vendor", [_obj("C", renamed_from="B", previous_keys=["A", "B"], updated_at="2")]
    )

    assert done["renamed"] == 1
    assert _all(path, 'SELECT key, updated_at FROM "vendor"') == [("C", "2")]
    assert _all(path, 'SELECT src, dst FROM "vendor__relations"') == [("C", "P-1")]
    assert _all(path, 'SELECT src, dst FROM "part__relations"') == [("P-1", "C")]
    assert _all(path, 'SELECT old_key, key FROM "vendor__previous_keys"') == [
        ("A", "C"),
        ("B", "C"),
    ]


def test_사라진_객체를_끝으로_하는_선을_끊는다(tmp_path: Path) -> None:
    """공개 측은 객체를 지우면(합치기 · 부서 이동 포함) 그 선을 **안 보낼 뿐 끊긴 선으로
    알리지 않는다.** 받는 쪽이 끊지 않으면 없는 객체에 매달린 선이 남는다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    kit.save_rows(cfg, "vendor", [_obj("V-1"), _obj("V-2")])
    kit.save_rows(cfg, "part", [_obj("P-1"), _obj("P-2"), _obj("P-3")])
    kit.save_relations(
        cfg,
        "part",
        [
            {"src": "P-1", "relation": "supplied_by", "dst": "V-1", "dst_type": "vendor"},
            {"src": "P-2", "relation": "supplied_by", "dst": "V-2", "dst_type": "vendor"},
            # 같은 key 가 다른 타입에도 있을 수 있다 — 그 줄은 건드리지 않는다.
            {"src": "P-2", "relation": "uses", "dst": "V-1", "dst_type": "tester"},
        ],
    )
    kit.save_relations(
        cfg, "vendor", [{"src": "V-2", "relation": "owns", "dst": "P-3", "dst_type": "part"}]
    )

    kit.save_rows(cfg, "vendor", [_obj("V-1", deleted=True, merged_into="V-2")])
    kit.save_rows(
        cfg, "part", [{"key": "P-3", "label": "P-3", "deleted": True, "hidden": True}]
    )

    assert _all(path, 'SELECT src, dst, dst_type FROM "part__relations"') == [
        ("P-2", "V-1", "tester"),
        ("P-2", "V-2", "vendor"),
    ]
    assert _all(path, 'SELECT src, dst FROM "vendor__relations"') == []

    # 도착이 이쪽에서 비활성인 선은 다시 와도 넣지 않는다(공개 측은 출발만 거른다).
    kit.save_relations(
        cfg,
        "part",
        [{"src": "P-1", "relation": "supplied_by", "dst": "V-1", "dst_type": "vendor"}],
    )
    assert _all(path, 'SELECT src, dst, dst_type FROM "part__relations"') == [
        ("P-2", "V-1", "tester"),
        ("P-2", "V-2", "vendor"),
    ]


def _paged(*pages: dict[str, Any]) -> Any:
    queue = list(pages)

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        return queue.pop(0)

    return fake


def test_처음부터_받으면_안_온_것을_비활성으로_둔다(tmp_path: Path) -> None:
    """`since` 없이 받으면 지워진 것은 오지 않는다 — 부재로만 안다. 정리하면 그 선도 끊는다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    kit.save_rows(cfg, "part", [_obj("P-1"), _obj("P-2"), _obj("P-3")])
    kit.save_relations(
        cfg, "part", [{"src": "P-3", "relation": "uses", "dst": "P-1", "dst_type": "part"}]
    )

    kit.request_json = _paged(
        {"as_of": "T1", "next": None, "items": [_obj("P-1"), _obj("P-2")]},
    )
    state: dict[str, str] = {}
    counts = kit.pull_type(cfg, "part", state)

    assert counts["full"] == 1 and counts["gone"] == 1
    assert _all(path, 'SELECT key, deleted FROM "part"') == [
        ("P-1", 0),
        ("P-2", 0),
        ("P-3", 1),
    ]
    assert _all(path, 'SELECT src FROM "part__relations"') == []
    assert state["part"] == "T1"


def test_증분이면_안_온_것을_건드리지_않는다(tmp_path: Path) -> None:
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    kit.save_rows(cfg, "part", [_obj("P-1"), _obj("P-2")])
    kit.request_json = _paged({"as_of": "T2", "next": None, "items": [_obj("P-1")]})
    counts = kit.pull_type(cfg, "part", {"part": "T1"})
    assert counts["full"] == 0 and counts["gone"] == 0
    assert _all(tmp_path / "sp_core.sqlite", 'SELECT key, deleted FROM "part"') == [
        ("P-1", 0),
        ("P-2", 0),
    ]


def test_절반_넘게_안_오면_정리하지_않고_멈춘다(tmp_path: Path) -> None:
    """토큰의 부서 · 공개 범위가 바뀐 날 전부를 비활성으로 만들지 않는다 — 사람이 보고
    `--allow-shrink` 로 한 번 더 받는다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    kit.save_rows(cfg, "part", [_obj(f"P-{n}") for n in range(30)])

    kit.request_json = _paged({"as_of": "T1", "next": None, "items": [_obj("P-0")]})
    state: dict[str, str] = {}
    counts = kit.pull_type(cfg, "part", state)
    assert counts["held_back"] == 29 and counts["gone"] == 0
    assert _all(path, 'SELECT count(*) FROM "part" WHERE deleted=1') == [(0,)]
    assert state["part"] == "T1"  # 받은 것은 반영했고 시각도 옮겼다 — 까닭은 「확인 필요」 로

    kit.request_json = _paged({"as_of": "T2", "next": None, "items": [_obj("P-0")]})
    counts = kit.pull_type(cfg, "part", {}, allow_shrink=True)
    assert counts["gone"] == 29
    assert _all(path, 'SELECT count(*) FROM "part" WHERE deleted=1') == [(29,)]


def test_커서를_못_읽으면_커서_없이_같은_시각부터_다시_받는다(tmp_path: Path) -> None:
    """404 는 두 가지다 — 커서 문제(CORE 2번) 하나로 타입 수신을 멈추지 않는다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    asked: list[dict[str, Any]] = []
    answers: list[Any] = [
        {"as_of": None, "next": "c1", "items": [_obj("P-1")]},
        kit.CursorLost("커서를 읽을 수 없습니다"),
        {"as_of": "T2", "next": None, "items": [_obj("P-1"), _obj("P-2")]},
    ]

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        asked.append(dict(params or {}))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    kit.request_json = fake
    state = {"part": "T1"}
    counts = kit.pull_type(cfg, "part", state)
    assert [one.get("cursor") for one in asked] == [None, "c1", None]
    assert [one.get("since") for one in asked] == ["T1", "T1", "T1"]
    assert counts["restarted"] == 1 and state["part"] == "T2"


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


def test_404_를_오류_코드로_가른다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kit = _kit()
    cfg = {"base_url": "https://sp.test/api", "token": "t", "timeout": 1, "retries": 1}

    def answer(code: str) -> None:
        body = {"error": {"code": code, "message": "m"}}
        monkeypatch.setattr(
            kit.requests, "get", lambda *a, **k: _Response(404, body), raising=False
        )

    answer(code("CORE", 1))
    with pytest.raises(kit.NotPublished):
        kit.request_json(cfg, "/core/part")
    answer(code("CORE", 2))
    with pytest.raises(kit.CursorLost):
        kit.request_json(cfg, "/core/part")
    answer("")
    with pytest.raises(SystemExit, match="base_url"):
        kit.request_json(cfg, "/core/part")


def _config(tmp_path: Path, types: str = "") -> str:
    path = tmp_path / "config.ini"
    path.write_text(
        f"[sp]\nbase_url = https://sp.test/api\ntoken = t\ntypes = {types}\n"
        f"out_dir = {tmp_path / 'out'}\n",
        encoding="utf-8",
    )
    return str(path)


def _catalog(
    revision: str, *, props: tuple[str, ...] = ("grade",), relations: bool = True
) -> dict[str, Any]:
    return {
        "system": "sp",
        "revision": revision,
        "types": [
            {
                "slug": "part",
                "relations": ["uses"] if relations else [],
                "properties": [{"key": one, "data_type": "text"} for one in props],
            },
            {"slug": "vendor", "relations": [], "properties": []},
        ],
    }


def test_공개_안_된_타입은_건너뛰고_나머지를_받은_뒤_알린다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kit = _kit()
    catalog = _catalog("r1", relations=False)

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/core":
            return catalog
        if path == "/core/part":
            raise kit.NotPublished("열려 있는 코어 타입이 아닙니다: part")
        return {"as_of": "T1", "next": None, "items": [_obj("V-1")]}

    kit.request_json = fake
    code = kit.main(["--config", _config(tmp_path, "part, vendor, ghost")])
    err = capsys.readouterr().err
    assert code == 2
    assert "ghost" in err and "part" in err
    state = kit.load_state({"out_dir": tmp_path / "out"})
    assert state == {"vendor": "T1"}


def test_정의_판이_바뀌면_차이를_말하고_전부_처음부터_받는다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """새로 열린 칸 · 관계의 기존 값은 바뀐 것이 아니라 증분으로 오지 않는다."""
    kit = _kit()
    asked: list[tuple[str, dict[str, Any]]] = []
    catalog = {"now": _catalog("r1")}

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/core":
            return catalog["now"]
        asked.append((path, dict(params or {})))
        return {"as_of": "T9", "next": None, "items": []}

    kit.request_json = fake
    config = _config(tmp_path)
    assert kit.main(["--config", config]) == 0
    asked.clear()
    assert kit.main(["--config", config]) == 0
    assert all(one[1].get("since") == "T9" for one in asked), asked

    catalog["now"] = _catalog("r2", props=("grade", "density"))
    asked.clear()
    assert kit.main(["--config", config]) == 0
    out = capsys.readouterr().out
    assert "r1 → r2" in out and "+ part.density" in out
    assert asked and all("since" not in one[1] for one in asked), asked
    assert (tmp_path / "out" / "catalog.prev.json").exists()


def test_되살아난_객체가_있으면_선을_처음부터_다시_받는다(tmp_path: Path) -> None:
    """지웠다 되살린 객체의 선은 바뀐 것이 아니라서 증분으로 다시 오지 않는다."""
    kit = _kit()
    out = tmp_path / "out"
    cfg = {"out_dir": out, "page_size": 500}
    kit.save_rows(cfg, "part", [_obj("P-1", deleted=True)])
    kit.save_state(cfg, {"part": "T1", "part#relations": "T1"})
    (out / "catalog.json").write_text(json.dumps(_catalog("r1")), encoding="utf-8")
    asked: dict[str, dict[str, Any]] = {}

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/core":
            return _catalog("r1")
        asked[path] = dict(params or {})
        if path == "/core/part":
            return {"as_of": "T2", "next": None, "items": [_obj("P-1")]}
        return {"as_of": "T2", "next": None, "items": []}

    kit.request_json = fake
    assert kit.main(["--config", _config(tmp_path, "part")]) == 0
    assert asked["/core/part"].get("since") == "T1"
    assert "since" not in asked["/core/part/relations"]


def test_키트_판이_CHANGES_맨_위에_적혀_있다() -> None:
    """판을 올리고 바뀐 것을 안 적으면, 옛 키트를 가진 사람은 무엇을 해야 하는지 모른다."""
    kit = _kit()
    changes = (KIT.parent / "CHANGES.md").read_text(encoding="utf-8")
    first = next(line for line in changes.splitlines() if line.startswith("## 20"))
    assert kit.KIT_VERSION in first


def test_지운_행은_가진_칸을_두고_비활성만_한다(tmp_path: Path) -> None:
    """지운 행은 칸이 빈 채로 온다 — 그것으로 덮으면 이미 쓴 기록이 보일 값이 사라진다.
    살아 있는 행은 칸을 통째로 바꾼다(SP 에서 비운 칸이 남지 않게)."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    kit.save_rows(cfg, "vendor", [_obj("V-1", properties={"grade": "A", "country": "KR"})])
    kit.save_rows(cfg, "vendor", [_obj("V-1", properties={"grade": "B"})])
    assert _all(path, 'SELECT properties FROM "vendor"') == [('{"grade": "B"}',)]

    kit.save_rows(cfg, "vendor", [_obj("V-1", deleted=True, merged_into="V-9", properties={})])
    assert _all(path, 'SELECT deleted, merged_into, properties FROM "vendor"') == [
        (1, "V-9", '{"grade": "B"}')
    ]
