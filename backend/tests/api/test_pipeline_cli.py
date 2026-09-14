"""로컬 정제 도구(`pipeline/sp_pipeline.py`) — **미리 본 것만 넣는다.**

도구는 저장소 루트의 한 파일이라 패키지로 import 하지 않고 경로로 집어 온다(MCP 서버와 같다).
HTTP 는 이 프로세스 안의 앱(TestClient)으로 돌린다 — 라우터 · 권한 · 검증이 통째로 돈다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed

PIPELINE = Path(__file__).resolve().parents[3] / "pipeline" / "sp_pipeline.py"
SERVER = "http://platform.test"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("sp_pipeline_under_test", PIPELINE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass 가 자기 모듈을 sys.modules 에서 찾는다 — 먼저 올려 둔다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pipeline = _load()


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:6]}"


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        got = client.request(method, path, content=body, headers=headers)
        return got.status_code, got.json()

    before = pipeline.SEND
    pipeline.SEND = send
    try:
        yield
    finally:
        pipeline.SEND = before


def _token(client: TestClient, admin: Signed) -> str:
    made = client.post(
        "/api/auth/tokens",
        json={
            "name": _uniq("pipeline"),
            "scopes": ["read", "objects:write", "ontology:write"],
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return str(made.json()["token"])


def _write(path: Path, body: Any) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _fill(run: Path, workspace: str) -> dict[str, str]:
    """기업 ← 툴(참조). **파일 이름 순으로는 툴이 먼저다** — `objects_order` 가
    차례를 정한다."""
    company, tool = _uniq("company"), _uniq("tool")
    _write(
        run / "ontology.json",
        {
            "groups": [],
            "types": [
                {"slug": company, "label": "기업", "key_policy": "required", "properties": []},
                {
                    "slug": tool,
                    "label": "툴",
                    "key_policy": "required",
                    "properties": [
                        {
                            "key": "vendor",
                            "label": "개발사",
                            "data_type": "object_ref",
                            "ref_type_slug": company,
                        }
                    ],
                },
            ],
            "relation_types": [],
        },
    )
    manifest = json.loads((run / "bundle.json").read_text(encoding="utf-8"))
    manifest["objects_order"] = [company, tool]
    _write(run / "bundle.json", manifest)
    source = {"file": "툴목록.xlsx", "row": 3}
    _write(
        run / "objects" / "a_tool.json",
        {
            "type_slug": tool,
            "workspace_slug": workspace,
            "rows": [
                {
                    "key": "T-1",
                    "label": "툴1",
                    "vendor": "C-1",
                    "_source": source,
                    "_note": "시트 A",
                }
            ],
        },
    )
    _write(
        run / "objects" / "b_company.json",
        {
            "type_slug": company,
            "workspace_slug": workspace,
            "rows": [{"key": "C-1", "label": "기업1", "_source": source, "_confidence": 0.9}],
        },
    )
    return {"company": company, "tool": tool}


def test_빈_실행_폴더는_검증에서_막힌다(tmp_path: Path) -> None:
    run = tmp_path / "run"
    pipeline.cmd_init(run, title="빈 것")
    ok, text = pipeline.cmd_validate(run)
    assert ok is False and "비어" in text
    # 새 실행은 새 폴더로 — 지난 실행의 기록을 덮지 않는다.
    with pytest.raises(pipeline.Stop, match="이미 실행 폴더"):
        pipeline.cmd_init(run)


def test_검증이_보내기_전에_잡는다(tmp_path: Path) -> None:
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    names = _fill(run, "cae")
    _write(
        run / "objects" / "c_more.json",
        {
            "type_slug": names["tool"],
            "workspace_slug": "cae",
            "rows": [{"key": "T-1", "label": "겹친 툴"}],
        },
    )
    _write(
        run / "relations" / "tool.json",
        {
            "type_slug": names["tool"],
            "rows": [{"src": "T-1", "relation": "uses", "weight": 3}],
        },
    )
    _write(run / "unresolved.json", [{"what": "PowerFLOW", "question": "어느 회사 제품인가"}])

    ok, text = pipeline.cmd_validate(run)
    assert ok is False
    assert "겹칩니다" in text  # 같은 타입의 식별자
    assert "dst" in text and "weight" in text  # 관계 행의 빠진 칸 · 없는 칸
    assert "미해결 1건" in text
    assert "출처(_source)가 없는 행" in text  # 경고
    assert "근거(evidence_note)" in text  # 경고


def test_미리_보고_나서만_넣고_출처는_플랫폼에_안_간다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    names = _fill(run, admin.workspace)
    token = _token(client, admin)

    with pytest.raises(pipeline.Stop, match="먼저 preview"):
        pipeline.cmd_apply(run, server=SERVER, token=token)

    # `_source` · `_note` 가 플랫폼에 가면 「모르는 열」 로 거절된다 —
    # 통과하면 떼어 보낸 것이다.
    # 툴이 기업을 참조하므로, 차례(objects_order)가 틀리면 여기서 오류가 난다.
    ok, text = pipeline.cmd_preview(run, server=SERVER, token=token)
    assert ok is True, text
    assert (run / "preview.json").exists()
    assert (
        client.get(f"/api/objects/{names['tool']}", headers=admin.headers).status_code == 404
    )

    ok, text = pipeline.cmd_apply(run, server=SERVER, token=token)
    assert ok is True, text
    applied = json.loads((run / "applied.json").read_text(encoding="utf-8"))
    assert applied["result"]["applied"] is True
    tools = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()
    assert [one["key"] for one in tools["items"]] == ["T-1"]


def test_미리_본_뒤_바뀌면_넣지_않는다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    """미리 본 뒤 파일을 고쳤으면 **사람이 본 적 없는 것**이 들어간다."""
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    names = _fill(run, admin.workspace)
    token = _token(client, admin)
    ok, _ = pipeline.cmd_preview(run, server=SERVER, token=token)
    assert ok is True

    changed = run / "objects" / "b_company.json"
    body = json.loads(changed.read_text(encoding="utf-8"))
    body["rows"][0]["label"] = "몰래 바꾼 이름"
    _write(changed, body)

    with pytest.raises(pipeline.Stop, match="바뀌었습니다"):
        pipeline.cmd_apply(run, server=SERVER, token=token)
    assert (
        client.get(f"/api/objects/{names['company']}", headers=admin.headers).status_code
        == 404
    )


def test_플랫폼이_거절하면_이유를_말하고_멈춘다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    _fill(run, admin.workspace)
    with pytest.raises(pipeline.Stop, match="거절"):
        pipeline.cmd_preview(run, server=SERVER, token="spt_not-a-real-token")


def test_플랫폼에_닿지_않으면_트레이스백이_아니라_할_일을_말한다(tmp_path: Path) -> None:
    """서버가 꺼졌거나 주소가 틀렸을 때 — 거절당한 것과 **닿지 않은 것**을 가른다."""
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    _fill(run, "cae")

    def refused(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        raise pipeline.urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    before = pipeline.SEND
    pipeline.SEND = refused
    try:
        with pytest.raises(pipeline.Stop, match="닿지 않습니다"):
            pipeline.cmd_preview(run, server="http://127.0.0.1:9", token="spt_x")
    finally:
        pipeline.SEND = before


def test_규약대로_확신도가_낮은_행은_막고_인용_없는_문서_행은_경고한다(tmp_path: Path) -> None:
    """모델링 규약 5장 — 0.7 미만은 넣지 않고, 문서에서 뽑은 행은 원문 인용을 붙인다."""
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    names = _fill(run, "cae")
    _write(
        run / "objects" / "c_docs.json",
        {
            "type_slug": names["company"],
            "workspace_slug": "cae",
            "rows": [
                {
                    "key": "C-9",
                    "label": "문맥으로 짐작한 기업",
                    "_source": {"file": "보고서.pdf", "page": 4, "quote": "협력사로 참여"},
                    "_confidence": 0.5,
                },
                {
                    "key": "C-8",
                    "label": "표에서 읽은 기업",
                    "_source": {"file": "발표.pptx", "slide": 7},
                },
            ],
        },
    )
    ok, text = pipeline.cmd_validate(run)
    assert ok is False
    assert "확신도 0.7 미만인 행 1개(1행)" in text
    assert "원문 인용(_source.quote)이 없는 행 1개(2행)" in text


def test_허브에서_받은_실행은_source_를_싣고_받은_타입은_허브_관리가_된다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    """쌍둥이 쪽 한 바퀴 — 받기(pull) → 검증 → 미리 보기 → 적용. 시험 DB 하나가 허브도 된다."""
    tag = uuid.uuid4().hex[:6]
    group, kind = f"hg{tag}", f"hk{tag}"
    made = client.post(
        "/api/bundles/import",
        json={
            "ontology": {
                "groups": [{"slug": group, "label": "허브 묶음"}],
                "types": [
                    {
                        "slug": kind,
                        "label": "기준",
                        "nav_group_slug": group,
                        "key_policy": "required",
                    }
                ],
                "relation_types": [],
            },
            "objects": [{"type_slug": kind, "rows": [{"key": "K-1", "label": "하나"}]}],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert made.json()["applied"] is True, made.text
    token = _token(client, admin)

    run = tmp_path / "pulled"
    text = pipeline.cmd_pull(run, hub=SERVER, hub_token=token, group=group)
    assert "객체 1" in text
    manifest = json.loads((run / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "hub" and manifest["objects_order"] == [kind]

    ok, report = pipeline.cmd_validate(run)
    assert ok is True, report
    # 받은 행은 원천이 허브다 — 행마다 출처를 요구하지 않는다.
    assert "출처(_source)" not in report and "전역" not in report
    assert pipeline.payload(pipeline.load(run))["source"] == "hub"

    ok, summary = pipeline.cmd_preview(run, server=SERVER, token=token)
    assert ok is True, summary
    ok, summary = pipeline.cmd_apply(run, server=SERVER, token=token)
    assert ok is True, summary
    types = {
        one["slug"]: one
        for one in client.get("/api/ontology/types", headers=admin.headers).json()
    }
    assert types[kind]["managed_by"] == "hub"

    with pytest.raises(pipeline.Stop, match="거절"):
        pipeline.cmd_pull(tmp_path / "없음", hub=SERVER, hub_token=token, group="없는묶음")
