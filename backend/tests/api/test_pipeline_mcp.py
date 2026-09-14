"""로컬 정제 MCP(`pipeline/sp_mcp.py`)를 **진짜 앱에 붙여** 한 바퀴 돈다.

`mcp` 패키지는 백엔드 환경에 깔지 않는다(플랫폼 MCP 와 같은 이유) — 가짜 `FastMCP` 로 도구
함수만 꺼낸다. 진짜 `mcp` 로 서는지, stdio 로 불렀을 때 멈춤의 말이 그대로 가는지는
`pipeline/tests` 가 갈라진 환경에서 본다.

여기서 보는 것: 작업 폴더 밖을 못 건드리나, 원천 · 결정기록을 AI 가 못 쓰나, **적용 도구가
없나**, 그리고 조사 → 정의 확정 → 변환 → 검증 → 미리 보기가 이어지나.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_table_cli import HEADER, ROWS, SERVER, SLUGS, _mapping, _ontology

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "pipeline"
REGISTERED: list[str] = []


class _FakeFastMCP:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tool(self) -> Callable[[Any], Any]:
        def register(function: Any) -> Any:
            REGISTERED.append(function.__name__)
            return function

        return register


def _load() -> Any:
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    fake = types.ModuleType("mcp.server.fastmcp")
    fake.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    saved = {
        name: sys.modules.get(name) for name in ("mcp", "mcp.server", "mcp.server.fastmcp")
    }
    for name in ("mcp", "mcp.server"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["mcp.server.fastmcp"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "sp_mcp_under_test", PIPELINE_DIR / "sp_mcp.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        # 다른 시험(플랫폼 MCP)이 자기 가짜를 끼울 수 있게 되돌린다.
        for name, before in saved.items():
            if before is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = before
    return module


server = _load()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "작업들"
    base.mkdir()
    monkeypatch.setenv("SP_WORK_ROOT", str(base))
    monkeypatch.delenv("SP_SERVER", raising=False)
    monkeypatch.delenv("SP_TOKEN", raising=False)
    return base


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        got = client.request(method, path, content=body, headers=headers)
        return got.status_code, got.json()

    before = server.pipeline.SEND
    server.pipeline.SEND = send
    try:
        yield
    finally:
        server.pipeline.SEND = before


def test_적용_도구는_없고_안내는_정본을_내려준다() -> None:
    assert set(REGISTERED) == {
        "pipeline_guide",
        "work_list",
        "work_init",
        "work_status",
        "work_read",
        "work_write",
        "decision_record",
        "source_profile",
        "source_head",
        "table_convert",
        "run_init",
        "hub_pull",
        "run_validate",
        "run_preview",
    }
    # **사람이 확인한 뒤에만 넣는다** — 도구의 모양으로.
    assert not any("apply" in name for name in REGISTERED)

    overview = server.pipeline_guide()
    assert "work_status" in overview["content"]
    assert {"run", "table", "sources", "modeling"} <= set(overview["topics"])
    assert "판단 표" in server.pipeline_guide("modeling")["content"]
    assert "topics" in server.pipeline_guide("없는 주제")


def test_작업_폴더_밖과_원천과_도구가_쥔_파일은_못_건드린다(root: Path) -> None:
    assert "작업 폴더를 만들었습니다" in server.work_init("cae/대장", "대장", "cae")
    (root / "cae/대장/00-원천/표.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    with pytest.raises(server.Stop, match="밖의 경로"):
        server.work_init("../탈출", "x")
    for path in (
        "../../탈출.json",
        "00-원천/표.csv",
        "결정기록.md",
        "work.json",
        "runs/x/preview.json",
    ):
        with pytest.raises(server.Stop):
            server.work_write("cae/대장", path, "{}")
    with pytest.raises(server.Stop, match="JSON 이 아닙니다"):
        server.work_write("cae/대장", "02-정의/ontology.json", "{")
    with pytest.raises(server.Stop, match="먼저 run_init"):
        server.work_write("cae/대장", "runs/없음/bundle.json", "{}")
    for sneaky in ("00-원천/표.csv", "../대장/00-원천/표.csv"):
        with pytest.raises(server.Stop, match="통째로 읽지 않습니다"):
            server.work_read("cae/대장", sneaky)

    assert server.work_list()["works"] == [{"work": "cae/대장", "title": "대장"}]
    assert "행,a,b" in server.source_head("cae/대장", "표.csv")


def test_설정이_없으면_무엇을_적을지_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SP_WORK_ROOT", raising=False)
    with pytest.raises(server.Stop, match="SP_WORK_ROOT"):
        server.work_list()


def test_조사부터_미리_보기까지_한_바퀴(
    root: Path,
    client: TestClient,
    admin: Signed,
    platform: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = uuid.uuid4().hex[:6]
    slugs = {name: f"{slug}_{tag}" for name, slug in SLUGS.items()}
    work = "plm/models"
    server.work_init(work, "프로젝트-모델 표", "plm")
    (root / work / "00-원천" / "표.csv").write_text(
        "\n".join([HEADER, *ROWS]), encoding="utf-8"
    )

    assert "표.csv: 조사" in server.work_status(work)
    report = server.source_profile(work, "표.csv")
    assert "## 5. 이름에 박힌 조각" in report
    assert (root / work / "01-조사" / "표.profile.txt").exists()

    ontology = json.dumps(_ontology(slugs), ensure_ascii=False)
    assert "썼습니다" in server.work_write(work, "02-정의/ontology.json", ontology)
    server.work_write(work, "02-정의/판단표.md", "| 열 | 칸 |\n")
    assert "정의 확정 대기" in server.work_status(work)
    server.decision_record(
        work, "정의 확정", "4타입", decided_by="온톨로지 담당", confirms_ontology=True
    )

    mapping = _mapping(slugs, ontology="../02-정의/ontology.json")
    mapping["workspace_slug"] = admin.workspace
    server.work_write(work, "03-대응/표.table.json", json.dumps(mapping, ensure_ascii=False))
    assert "표.csv: 변환" in server.work_status(work)

    converted = server.table_convert(work, "03-대응/표.table.json", "표.csv")
    assert converted["unresolved_empty"] is True, converted["report"]
    run = converted["run"]
    assert run.startswith("runs/") and "못 읽음 3" in converted["report"]
    assert server.run_validate(work, run)["ok"] is True

    with pytest.raises(server.Stop, match="SP_SERVER"):
        server.run_preview(work, run)
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"mcp-{tag}", "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    monkeypatch.setenv("SP_SERVER", SERVER)
    monkeypatch.setenv("SP_TOKEN", made.json()["token"])
    preview = server.run_preview(work, run)
    assert preview["ok"] is True, preview["summary"]
    assert (
        "sp_pipeline.py" in preview["apply_command"] and " apply " in preview["apply_command"]
    )
    status = server.work_status(work)
    assert "미리 보기 괜찮음" in status and "**직접** 적용" in status

    # 넣는 것은 사람이 명령으로 — 도구가 아니다.
    assert (
        client.get(f"/api/objects/{slugs['model']}", headers=admin.headers).status_code == 404
    )
    ok, _ = server.pipeline.cmd_apply(
        root / work / run, server=SERVER, token=made.json()["token"]
    )
    assert ok is True
    assert "적용함" in server.work_status(work)

    # 같은 원천을 고쳐 다시 돌리면 **새** 실행 폴더.
    again = server.table_convert(work, "03-대응/표.table.json", "표.csv")
    assert again["run"] != run
