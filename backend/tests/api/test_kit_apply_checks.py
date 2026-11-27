"""정제 키트의 적용(ADR 0100) — 0.4.107 점검의 결함을 **진짜 앱에 붙여** 본다.

가짜 플랫폼으로 보는 것(대기열 · 번호 · zip 항목 수 · 그림 자리 …)은 `pipeline/tests/
test_kit_fixes.py` 에 있다. 여기서는 플랫폼의 지문 · 작업이 실제로 어떻게 답하는가:

- 미리 본 뒤 참조 대상이 지워지면 — 플랫폼이 적용 때 다시 판정해 넣지 않고(지문), 키트는
  실행을 잠그지 않는다.
- 화면에서 누른 적용이 아직 대기열에 있으면 키트가 「적용함」 으로 적지 않고, 그 적용이
  들어가지 못하면 「적용 거절됨」 으로 옮긴다.
- 문서 올리기의 작업이 여럿이면 함께 본 뒤 차례로 넣고, 그 사이 누가 고쳐 뒤 작업이 거절되면
  어느 것이 들어갔는지 말한다.
"""

from __future__ import annotations

import importlib
import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed, finish_job, patched_pipeline, pipeline_sender

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
sp_pipeline: Any = importlib.import_module("sp_pipeline")
sp_work: Any = importlib.import_module("sp_work")
sp_docpush: Any = importlib.import_module("sp_docpush")
SERVER = "http://platform.test"


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    with patched_pipeline(sp_pipeline, client):
        yield


@pytest.fixture
def token(client: TestClient, admin: Signed) -> str:
    made = client.post(
        "/api/auth/tokens",
        json={"name": _uniq("kit"), "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return str(made.json()["token"])


def _vendor_world(client: TestClient, admin: Signed) -> tuple[str, str, str]:
    """기업 ← 툴(참조). 기업 C-1 이 플랫폼에 있다. (기업 타입, 툴 타입, C-1 의 id)."""
    company, tool = _uniq("company"), _uniq("tool")
    made = client.post(
        "/api/ontology/import",
        params={"dry_run": "false"},
        json={
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
            ]
        },
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    vendor = client.post(
        f"/api/objects/{company}",
        json={"key": "C-1", "label": "기업1", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert vendor.status_code == 201, vendor.text
    return company, tool, str(vendor.json()["id"])


def _tool_run(run: Path, tool: str, workspace: str) -> None:
    """정의 없이 — 있는 타입에 툴 하나(개발사 C-1 을 가리킨다)."""
    sp_pipeline.cmd_init(run)
    sp_pipeline._write(
        run / sp_pipeline.OBJECTS_DIR / "tool.json",
        {
            "type_slug": tool,
            "workspace_slug": workspace,
            "rows": [{"key": "T-1", "label": "툴1", "vendor": "C-1", "_source": {"row": 1}}],
        },
    )


def test_미리_본_뒤_참조_대상이_지워지면_플랫폼이_넣지_않고_실행은_잠기지_않는다(
    client: TestClient, admin: Signed, platform: None, token: str, tmp_path: Path
) -> None:
    """**적용 때 다시 판정한다** — 미리 보기(전체)의 지문은 줄의 판정까지 담는다. 그 사이 C-1
    이 지워지면 적용 때의 계획이 달라져(가리키는 것이 없음) 플랫폼이 넣지 않는다. 키트가 따로
    다시 볼 것이 없다 — 대신 그것을 「적용함」 으로 잠그지 않고 까닭을 남기는지 본다."""
    company, tool, vendor = _vendor_world(client, admin)
    run = tmp_path / "run"
    _tool_run(run, tool, admin.workspace)
    ok, summary = sp_pipeline.cmd_preview(run, server=SERVER, token=token)
    assert ok, summary

    gone = client.delete(f"/api/objects/{company}/{vendor}", headers=admin.headers)
    assert gone.status_code == 204, gone.text

    out = sp_pipeline.cmd_apply_plan(run, server=SERVER, token=token)
    assert out["state"] == "failed" and out["applied"] is False, out
    assert (run / sp_pipeline.APPLY_FAILED).exists()
    assert not (run / sp_pipeline.APPLIED).exists(), "들어가지 않은 적용으로 잠그지 않는다"
    listed = client.get(f"/api/objects/{tool}", headers=admin.headers).json()
    assert listed["total"] == 0, listed


def test_화면의_적용이_대기열에_있으면_적용함으로_적지_않고_못_들어가면_거절로_옮긴다(
    client: TestClient,
    admin: Signed,
    token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company, tool, vendor = _vendor_world(client, admin)
    monkeypatch.setenv("SP_SETTINGS", str(tmp_path / "sp-settings.json"))
    sp_pipeline.save_settings(
        {"work_root": str(tmp_path), "platforms": {"main": {"server": SERVER, "token": token}}}
    )
    folder = tmp_path / "작업"
    sp_work.init(folder, title="툴", platform="main")
    sp_work.record(folder, topic="정의", decision="새 정의 없음", confirms_ontology=True)
    run = sp_work.new_run(folder, "툴")
    _tool_run(run, tool, admin.workspace)

    with_worker = pipeline_sender(client)

    def without_worker(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        # 워커가 밤 계산으로 바쁘다 — 넣은 작업을 아무도 안 집는다.
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        got = client.request(method, path, content=body, headers=headers)
        return got.status_code, got.json()

    monkeypatch.setattr(sp_pipeline, "WAIT", lambda _seconds: None)
    monkeypatch.setattr(sp_pipeline, "SEND", with_worker)
    ok, summary = sp_pipeline.cmd_preview(run, server=SERVER, token=token, platform="main")
    assert ok, summary
    plan = json.loads((run / sp_pipeline.PREVIEW).read_text())["result"]["job_id"]

    # 사람이 화면에서 「적용」 — 적용 작업은 대기열에 선다.
    monkeypatch.setattr(sp_pipeline, "SEND", without_worker)
    pressed = client.post(f"/api/jobs/{plan}/apply", headers=admin.headers)
    assert pressed.status_code == 202, pressed.text
    assert client.get(f"/api/jobs/{plan}", headers=admin.headers).json()["applied_by"], (
        "플랫폼은 대기 중인 적용도 「적용한 것」 으로 센다"
    )
    shown = sp_work.render(sp_work.status(folder))
    assert "적용 대기 중" in shown, shown
    assert not (run / sp_pipeline.APPLIED).exists(), (
        "대기 중인 적용을 「적용함」 으로 적지 않는다"
    )
    assert sp_pipeline.cmd_apply_plan(run, server=SERVER, token=token)["state"] == "waiting"

    # 그 사이 C-1 이 지워져 그 적용은 들어가지 못한다 — 실행은 잠기지 않는다.
    gone = client.delete(f"/api/objects/{company}/{vendor}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    finished = finish_job(client, admin, pressed.json())
    assert finished["status"] in ("done", "failed"), finished
    shown = sp_work.render(sp_work.status(folder))
    assert "적용 거절됨" in shown and "적용 대기 중" not in shown, shown
    assert not (run / sp_pipeline.APPLIED).exists()


# --- 문서 올리기 -----------------------------------------------------------------------


def _paper_world(client: TestClient, admin: Signed, count: int) -> tuple[str, list[str]]:
    slug = _uniq("paper_owner")
    made = client.post(
        "/api/ontology/import",
        params={"dry_run": "false"},
        json={
            "types": [
                {
                    "slug": slug,
                    "label": "보고 대상",
                    "properties": [{"key": "paper", "label": "백서", "data_type": "document"}],
                }
            ]
        },
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    ids = []
    for index in range(count):
        obj = client.post(
            f"/api/objects/{slug}",
            json={"label": f"힌지{index}", "workspace_slug": admin.workspace},
            headers=admin.headers,
        )
        assert obj.status_code == 201, obj.text
        ids.append(str(obj.json()["id"]))
    return slug, ids


def _drafts(folder: Path, slug: str, ids: list[str], **extra: Any) -> list[Any]:
    items = []
    for index, oid in enumerate(ids):
        where = folder / "docs" / f"문서{index}"
        where.mkdir(parents=True, exist_ok=True)
        (where / "draft.md").write_text(f"# 문서 {index}\n\n본문 {index}.\n", encoding="utf-8")
        items.append(
            sp_docpush.Item(
                slug, oid, "paper", f"docs/문서{index}/draft.md", extra.get(str(index))
            )
        )
    return items


def _revision(client: TestClient, admin: Signed, slug: str, oid: str) -> int:
    got = client.get(
        f"/api/objects/{slug}/{oid}/documents/paper",
        params={"body": "false"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return int(got.json()["revision"])


def test_문서_올리기_작업_하나에_오류가_있으면_다른_작업도_넣지_않는다(
    client: TestClient,
    admin: Signed,
    platform: None,
    token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sp_docpush, "DOCUMENTS_PER_JOB", 1)
    slug, ids = _paper_world(client, admin, 2)
    # 둘째 문서는 없는 판을 기준으로 — 그 작업의 계획에 오류 줄이 생긴다.
    planned = sp_docpush.push(
        tmp_path, _drafts(tmp_path, slug, ids, **{"1": 5}), server=SERVER, token=token
    )
    assert len(planned["jobs"]) == 2 and planned["ok"] is False, planned
    out = sp_docpush.apply(tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token)
    assert [one["state"] for one in out["applied"]] == ["not_applied", "refused"], out
    assert _revision(client, admin, slug, ids[0]) == 0, "깨끗한 앞 작업도 넣지 않는다"


def test_문서_올리기_뒤_작업이_그_사이_고쳐져_거절되면_어느_것이_들어갔는지_말한다(
    client: TestClient,
    admin: Signed,
    platform: None,
    token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sp_docpush, "DOCUMENTS_PER_JOB", 1)
    slug, ids = _paper_world(client, admin, 3)
    planned = sp_docpush.push(
        tmp_path, _drafts(tmp_path, slug, ids), server=SERVER, token=token
    )
    assert planned["ok"] is True and len(planned["jobs"]) == 3, planned

    # 계획을 본 뒤 사람이 화면에서 둘째 문서를 고쳤다.
    edited = client.put(
        f"/api/objects/{slug}/{ids[1]}/documents/paper",
        json={"body": "# 사람이 쓴 것\n", "base_revision": 0},
        headers=admin.headers,
    )
    assert edited.status_code == 200, edited.text

    out = sp_docpush.apply(tmp_path, sp_docpush.latest(tmp_path), server=SERVER, token=token)
    assert [one["state"] for one in out["applied"]] == ["applied", "failed", "not_applied"], (
        out
    )
    first = planned["jobs"][0]["job_id"]
    assert first in out["message"] and "거절돼" in out["message"], out["message"]
    assert _revision(client, admin, slug, ids[0]) == 1
    assert _revision(client, admin, slug, ids[1]) == 1, "사람이 쓴 판 그대로"
    assert _revision(client, admin, slug, ids[2]) == 0, "거절된 뒤의 작업은 넣지 않았다"
