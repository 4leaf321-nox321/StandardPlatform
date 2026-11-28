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


def test_작은_표도_전부_안_오면_정리하지_않고_멈춘다(tmp_path: Path) -> None:
    """바닥(10건) 아래의 표는 비율을 안 봐, 공개 측이 한 번 빈 응답을 주면 전부가 비활성 ·
    끊김이 됐다 — 공개 측(ADR 0098)과 같게 **전부** 안 오면 멈춘다. 몇 건 빠진 것은 그대로
    정리한다."""
    kit = _kit()
    assert kit.too_many(3, 3) is True
    assert kit.too_many(1, 3) is False
    assert kit.too_many(0, 0) is False
    assert kit.too_many(11, 20) is True and kit.too_many(10, 15) is False
    cfg = {"out_dir": tmp_path, "page_size": 500}
    path = tmp_path / "sp_core.sqlite"
    kit.save_rows(cfg, "part", [_obj("P-1"), _obj("P-2"), _obj("P-3")])
    kit.save_relations(
        cfg, "part", [{"src": "P-3", "relation": "uses", "dst": "P-1", "dst_type": "part"}]
    )

    kit.request_json = _paged({"as_of": "T1", "next": None, "items": []})
    counts = kit.pull_type(cfg, "part", {})
    assert counts["held_back"] == 3 and counts["gone"] == 0, counts
    assert _all(path, 'SELECT count(*) FROM "part" WHERE deleted=1') == [(0,)]
    kit.request_json = _paged({"as_of": "T1", "next": None, "items": []})
    counts = kit.pull_relations(cfg, "part", {})
    assert counts["held_back"] == 1 and counts["gone"] == 0, counts
    assert _all(path, 'SELECT src FROM "part__relations"') == [("P-3",)]

    kit.request_json = _paged({"as_of": "T2", "next": None, "items": []})
    counts = kit.pull_type(cfg, "part", {}, allow_shrink=True)
    assert counts["gone"] == 3
    assert _all(path, 'SELECT count(*) FROM "part" WHERE deleted=1') == [(3,)]


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


#: 공개 측이 협력사(`vendor_old`)의 객체를 공급사(`vendor`)로 옮긴 때(ADR 0094).
MOVED_AT = "2026-11-28T01:00:00.000000Z"
BEFORE = "2026-11-27T09:00:00.000000Z"
AFTER = "2026-11-28T02:00:00.000000Z"


def _moved_catalog(
    revision: str, *, moved: bool, moved_to: str | None = "vendor"
) -> dict[str, Any]:
    old: dict[str, Any] = {"slug": "vendor_old", "relations": ["uses"], "properties": []}
    if moved:
        old.update(moved_at=MOVED_AT, moved_to=moved_to)
    return {
        "system": "sp",
        "revision": revision,
        "types": [
            old,
            {"slug": "vendor", "relations": ["uses"], "properties": []},
            {"slug": "part", "relations": ["supplied_by"], "properties": []},
        ],
    }


def _before_move(out: Path) -> dict[str, Any]:
    """옮기기 전에 받아 둔 것 — 협력사 V-1 · V-2(그 선 하나), 부품이 V-2 를 가리키는 선."""
    cfg = {"out_dir": out, "page_size": 500}
    kit = _kit()
    kit.save_rows(cfg, "vendor_old", [_obj(key, updated_at=BEFORE) for key in ("V-1", "V-2")])
    kit.save_rows(cfg, "vendor", [_obj("V-9", updated_at=BEFORE)])
    kit.save_rows(cfg, "part", [_obj("P-1", updated_at=BEFORE)])
    kit.save_relations(
        cfg,
        "vendor_old",
        [{"src": "V-1", "relation": "uses", "dst": "P-1", "dst_type": "part"}],
    )
    kit.save_relations(
        cfg,
        "part",
        [{"src": "P-1", "relation": "supplied_by", "dst": "V-2", "dst_type": "vendor_old"}],
    )
    kit.save_state(
        cfg,
        {
            one: BEFORE
            for one in (
                "vendor_old",
                "vendor",
                "part",
                "vendor_old#relations",
                "part#relations",
            )
        },
    )
    (out / "catalog.json").write_text(
        json.dumps(_moved_catalog("r1", moved=False)), encoding="utf-8"
    )
    return cfg


def _moved_server(kit: Any, *, moved_to: str | None = "vendor", old_rows: Any = ()) -> Any:
    """옮긴 뒤의 공개 측 — 협력사는 비어(옮긴 뒤 새로 생긴 것만) 떠난 때 · 간 곳을 싣고,
    공급사가 옮겨 온 V-1 · V-2 를 「바뀐 것」 으로 준다. 선은 끝 타입만 바뀌어 다시 오지
    않는다(처음부터 받으면 지금 모양으로 온다)."""
    catalog = _moved_catalog("r2", moved=True, moved_to=moved_to)
    landed = (
        [] if moved_to is None else [_obj(key, updated_at=AFTER) for key in ("V-1", "V-2")]
    )
    pages: dict[str, list[dict[str, Any]]] = {
        "/core/vendor_old": list(old_rows),
        "/core/vendor": [*landed, _obj("V-9", updated_at=BEFORE)],
        "/core/part": [_obj("P-1", updated_at=BEFORE)],
        "/core/vendor_old/relations": [],
        "/core/vendor/relations": (
            [{"src": "V-1", "relation": "uses", "dst": "P-1", "dst_type": "part"}]
            if moved_to
            else []
        ),
        "/core/part/relations": (
            [{"src": "P-1", "relation": "supplied_by", "dst": "V-2", "dst_type": moved_to}]
            if moved_to
            else []
        ),
    }

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/core":
            return catalog
        return {"as_of": "2026-11-28T03:00:00.000000Z", "next": None, "items": pages[path]}

    kit.request_json = fake
    return fake


def test_다른_타입으로_옮긴_타입은_멈추지_않고_행과_선을_간_곳으로_옮겨_놓는다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**옮긴 타입은 비어 온다** — 옛 키트는 처음부터 다시 받아 0건을 보고 「전부 안 옴」
    으로 멈췄다(가진 행은 영영 살아 있었다). 카탈로그의 moved_to 를 보면 간 곳을 먼저 받고,
    그때 앞의 행으로 간 곳에 같은 key 가 온 것을 비활성으로 두고(moved_to 칸), 선을 간 곳으로
    옮긴다."""
    kit = _kit()
    out = tmp_path / "out"
    _before_move(out)
    _moved_server(kit)
    assert kit.main(["--config", _config(tmp_path)]) == 0
    printed = capsys.readouterr()
    assert "> 타입 vendor_old 의 객체를 vendor(으)로 옮김" in printed.out
    assert "다른 타입으로 옮겨 놓음 2" in printed.out and "확인 필요" not in printed.err

    path = out / "sp_core.sqlite"
    assert _all(path, 'SELECT key, deleted, moved_to FROM "vendor_old"') == [
        ("V-1", 1, "vendor"),
        ("V-2", 1, "vendor"),
    ]
    assert _all(path, 'SELECT key, deleted FROM "vendor"') == [
        ("V-1", 0),
        ("V-2", 0),
        ("V-9", 0),
    ]
    # 선 — 나가던 것은 간 곳의 표로, 들어오던 것은 도착 타입이 간 곳으로(끊지 않는다).
    assert _all(path, 'SELECT src, dst FROM "vendor_old__relations"') == []
    assert _all(path, 'SELECT src, dst, dst_type FROM "vendor__relations"') == [
        ("V-1", "P-1", "part")
    ]
    assert _all(path, 'SELECT src, dst, dst_type FROM "part__relations"') == [
        ("P-1", "V-2", "vendor")
    ]

    # 다음 실행 — 같은 옮기기를 다시 봐도 아무것도 안 한다. 옮긴 뒤 새로 생긴 행은 그대로
    # 받는다.
    _moved_server(kit, old_rows=[_obj("V-5", updated_at=AFTER)])
    assert kit.main(["--config", _config(tmp_path)]) == 0
    assert _all(path, 'SELECT key, deleted, moved_to FROM "vendor_old"') == [
        ("V-1", 1, "vendor"),
        ("V-2", 1, "vendor"),
        ("V-5", 0, None),
    ]


def test_옮겼는데_간_곳을_안_받으면_지우지_않고_알린다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kit = _kit()
    out = tmp_path / "out"
    _before_move(out)
    _moved_server(kit)
    assert kit.main(["--config", _config(tmp_path, "vendor_old, part")]) == 2
    err = capsys.readouterr().err
    assert "vendor(으)로 옮겼는데" in err and "types 에 vendor 를 더하세요" in err, err
    assert "정리하지 않았습니다" not in err, "옮겨 간 것은 「안 온 것」 이 아니다"
    path = out / "sp_core.sqlite"
    assert _all(path, 'SELECT key, deleted FROM "vendor_old"') == [("V-1", 0), ("V-2", 0)]
    assert _all(path, 'SELECT src, dst FROM "vendor_old__relations"') == [("V-1", "P-1")]


def test_공개하지_않은_타입으로_옮기면_그때_앞의_행을_비활성으로_둔다(tmp_path: Path) -> None:
    """간 곳을 밝히지 않으면(공개하지 않은 타입) 그 행들은 이 창구로 더 못 받는다 — hidden 처럼
    이름 · 칸을 두고 비활성, 그 선은 끊는다. 옮긴 뒤 새로 생긴 행은 그대로."""
    kit = _kit()
    out = tmp_path / "out"
    _before_move(out)
    _moved_server(kit, moved_to=None, old_rows=[_obj("V-5", updated_at=AFTER)])
    assert kit.main(["--config", _config(tmp_path)]) == 0
    path = out / "sp_core.sqlite"
    assert _all(path, 'SELECT key, deleted, moved_to FROM "vendor_old"') == [
        ("V-1", 1, ""),
        ("V-2", 1, ""),
        ("V-5", 0, None),
    ]
    assert _all(path, 'SELECT src, dst FROM "vendor_old__relations"') == []
    assert _all(path, 'SELECT src, dst FROM "part__relations"') == []


def test_moved_to_를_모르는_키트는_지우지_않고_멈춘다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**옛 키트의 안전** — moved_to 를 모르는 키트(2026-11-27 판까지)는 옮긴 일로 바뀐 판을
    보고 처음부터 다시 받아, 빈 원 타입을 「전부 안 옴」 으로 멈춘다. 아무것도 비활성으로
    만들지 않는다 — 공개 측이 옮긴 일을 판에 싣는 까닭이다(조용히 두 타입에 같은 행이 사는
    것보다 「확인 필요」 가 낫다)."""
    kit = _kit()
    out = tmp_path / "out"
    _before_move(out)
    _moved_server(kit)
    monkeypatch.setattr(kit, "moved_types", lambda catalog: {})
    assert kit.main(["--config", _config(tmp_path)]) == 2
    assert "정리하지 않았습니다" in capsys.readouterr().err
    path = out / "sp_core.sqlite"
    assert _all(path, 'SELECT key, deleted FROM "vendor_old"') == [("V-1", 0), ("V-2", 0)]


def test_한_번_받기_안에서는_같은_key_의_산_행이_이긴다(tmp_path: Path) -> None:
    """한 흐름에 같은 key 가 둘 오면 서로 다른 객체다 — 다른 타입에서 옮겨 와 같은 key 의
    객체로 병합된 진 쪽의 무덤(`merged_into` 가 제 key)이 산 행 **뒤에**(다음 쪽에) 와도 산
    행을 비활성으로 만들지 않는다. 같은 쪽 안에서도 같다."""
    kit = _kit()
    cfg = {"out_dir": tmp_path, "page_size": 500}
    pages = [
        {"as_of": None, "next": "c1", "items": [_obj("V-1", label="ACME")]},
        {
            "as_of": "T2",
            "next": None,
            "items": [
                _obj("V-1", deleted=True, merged_into="V-1", properties={}),
                _obj("V-2", label="한화"),
                _obj("V-2", deleted=True, merged_into="V-9", properties={}),
            ],
        },
    ]

    def fake(cfg_: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        return pages.pop(0)

    kit.request_json = fake
    kit.pull_type(cfg, "vendor", {"vendor": "T1"})
    assert _all(tmp_path / "sp_core.sqlite", 'SELECT key, deleted, label FROM "vendor"') == [
        ("V-1", 0, "ACME"),
        ("V-2", 0, "한화"),
    ]
