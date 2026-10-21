"""시험장(가짜 SP)과 참고 구현이 **서로를 믿을 만한가.**

받는 쪽 AI 는 시험장을 통과하면 다 됐다고 본다. 그러니 (1) 참고 구현은 시험장 전 단계를
진짜 HTTP 로 통과해야 하고, (2) 규칙 하나를 빼면 그 규칙을 보는 단계에서 **떨어져야** 한다 —
아무거나 통과시키는 시험장은 「다 됐다」 를 거짓으로 만든다.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import types
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_core_kit import KIT, _kit

TESTBED = KIT.parent / "testbed"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"testbed_{name}", TESTBED / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code = status
        self.text = body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


def _get(
    url: str, headers: dict[str, str], params: dict[str, Any] | None = None, timeout: int = 10
) -> _Response:
    """키트가 쓰는 `requests.get` 만큼 — 우리 쪽에는 requests 가 없다."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=timeout
        ) as got:
            return _Response(got.status, got.read())
    except urllib.error.HTTPError as failed:
        return _Response(failed.code, failed.read())


@pytest.fixture
def server() -> Iterator[str]:
    fake = _load("fake_sp").serve(0, quiet=True)
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{fake.server_address[1]}"
    finally:
        fake.shutdown()
        fake.server_close()


def _post(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10) as got:
        return dict(json.loads(got.read()))


def _walk(
    server: str, tmp_path: Path, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[dict[str, Any], int, list[str]]]:
    """단계마다: 넘기고 → 한 번 받고 → 채점. (단계 설명, 끝나는 값, 다른 곳)."""
    monkeypatch.setattr(
        kit,
        "requests",
        types.SimpleNamespace(get=_get, RequestException=OSError, Response=_Response),
    )
    compare, dump = _load("compare"), _load("dump_reference")
    out = tmp_path / "out"
    config = tmp_path / "config.ini"
    config.write_text(
        f"[sp]\nbase_url = {server}/api\ntoken = test-token\ntypes =\nout_dir = {out}\n",
        encoding="utf-8",
    )
    while True:
        info = _post(f"{server}/_step")
        args = ["--config", str(config)] + (["--full"] if info["run"] == "처음부터" else [])
        code = kit.main(args)
        expected = compare.fetch_expected(server)
        yield info, code, compare.diff(expected, dump.dump(out))
        if info["step"] == info["last_step"]:
            return


def test_참고_구현은_시험장_전_단계를_통과한다(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = _kit()
    seen = 0
    for info, code, problems in _walk(server, tmp_path, kit, monkeypatch):
        seen += 1
        assert problems == [], (info["step"], info["title"], problems)
        assert code == (0 if info["expect_status"] == "정상" else 2), info
    assert seen == 8


@pytest.mark.parametrize(
    ("what", "breaks", "fails_at"),
    [
        ("식별자를 안 옮긴다", lambda kit: setattr(kit, "old_keys", lambda row: []), 3),
        (
            "지운 객체의 선을 안 끊는다",
            lambda kit: setattr(kit, "cut_touching", lambda *a: 0),
            2,
        ),
        (
            "처음부터 받아도 정리를 안 한다",
            lambda kit: setattr(kit, "prune_relations", lambda *a, **k: (0, 0)),
            5,
        ),
        (
            "정의 판이 바뀌어도 이어서 받는다",
            lambda kit: setattr(kit, "remember_catalog", lambda *a: []),
            6,
        ),
        ("절반 넘게 안 와도 정리한다", lambda kit: setattr(kit, "GONE_LIMIT", 10.0), 8),
    ],
)
def test_규칙_하나를_빼면_그_규칙을_보는_단계에서_떨어진다(
    server: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    what: str,
    breaks: Any,
    fails_at: int,
) -> None:
    kit = _kit()
    breaks(kit)
    for info, _code, problems in _walk(server, tmp_path, kit, monkeypatch):
        if problems:
            assert info["step"] == fails_at, (what, info["step"], problems)
            return
    raise AssertionError(f"{what} — 시험장이 끝까지 통과시켰다")
