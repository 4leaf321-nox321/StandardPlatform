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

from tests.api.conftest import Signed, patched_pipeline
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
    # 이 PC 의 정제 도구 설정도 안 본다 — 키트를 설치한 PC 면 그것을 읽어 흔들린다.
    monkeypatch.setenv("SP_SETTINGS", str(tmp_path / "sp-settings.json"))
    return base


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    """정제 도구가 TestClient 앱에 말한다 — 작업을 물을 때마다 워커가 한 바퀴 돈다."""
    with patched_pipeline(server.pipeline, client):
        yield


def test_적용_도구는_없고_안내는_정본을_내려준다() -> None:
    assert set(REGISTERED) == {
        "pipeline_guide",
        "work_list",
        "work_init",
        "work_platform",
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
        "runs_list",
        "run_undo",
    }
    # **사람이 확인한 뒤에만 넣는다** — 도구의 모양으로. 되돌리기도 같다: 계획을 보여 주는
    # 도구만 있고, 되돌리는 것은 사람이 `undo_command` 로 한다.
    assert not any("apply" in name for name in REGISTERED)
    assert "계획만" in (server.run_undo.__doc__ or "")

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

    # 작업마다 **넣을 곳**도 보인다 — 플랫폼이 없을 때 만든 것은 빈 값.
    assert server.work_list()["works"] == [
        {"work": "cae/대장", "title": "대장", "platform": ""}
    ]
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

    # 넣을 곳이 하나도 없으면 — 무엇을 해야 하는지(설치 명령) 말한다.
    with pytest.raises(server.Stop, match="등록된 플랫폼이 없습니다"):
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

    # --- 한 PC 가 플랫폼 여럿을 겨눈다 ---------------------------------------------------
    # 시험 장치는 주소의 호스트를 무시하고 이 앱으로 보낸다 — 그래서 **토큰**으로 갈 곳을
    # 가른다.
    # 진짜 토큰을 든 `main` 과 가짜 토큰을 든 `other` 를 등록하고, 작업 폴더가 고른 쪽으로만
    # 가는지 본다.
    monkeypatch.delenv("SP_SERVER")
    monkeypatch.delenv("SP_TOKEN")
    server.pipeline.save_settings(
        {
            "work_root": str(root),
            "platforms": {
                "main": {"server": SERVER, "token": made.json()["token"]},
                "other": {"server": "http://딴곳:3040/other", "token": "spt_fake"},
            },
        }
    )
    listed = server.work_list()
    assert listed["platforms"] == {"main": SERVER, "other": "http://딴곳:3040/other"}
    assert "spt_fake" not in json.dumps(listed, ensure_ascii=False)  # 토큰은 안 보인다
    # 이 작업은 플랫폼이 하나도 없을 때 만들었다 — 여럿이 된 지금은 **짐작하지 않는다.**
    with pytest.raises(server.Stop, match="여럿"):
        server.run_preview(work, again["run"])
    with pytest.raises(server.Stop, match="어느 플랫폼"):
        server.work_init("plm/새것", "새 작업")

    assert "main 에 넣습니다" in server.work_platform(work, "main")
    went = server.run_preview(work, again["run"])
    assert went["ok"] is True and went["platform"].startswith("main ")
    seen = json.loads((root / work / again["run"] / "preview.json").read_text())
    assert seen["platform"] == "main" and seen["server"] == SERVER

    # 다른 곳으로 바꾸면 그쪽으로 간다 — 가짜 토큰이라 그 플랫폼이 거절한다.
    server.work_platform(work, "other")
    with pytest.raises(server.Stop):
        server.run_preview(work, again["run"])
