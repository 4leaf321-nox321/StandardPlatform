"""0.4.107 점검에서 찾은 키트의 결함 — 가짜 플랫폼(SEND)으로 키트만 본다.

진짜 앱에 붙여 도는 것(정의 · 묶음 · 문서 올리기의 지문)은 `backend/tests/api/
test_kit_apply_checks.py` 가 본다. 여기서는 플랫폼이 **어떤 답을 하면** 키트가 무엇을 적고
무엇을 말하나 — 대기열 · 화면의 대기 중 적용 · 남의 작업 번호 · 주소에 이어 붙인 번호 · 폴더 밖
· zip 항목 수 · 빈 본문 · 그림 자리 · NUL · JSON 아닌 답.
"""

from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import sp_docpush
import sp_pipeline as pipeline
import sp_table
import sp_work

SERVER = "http://platform.test"
TOKEN = "spt_test"


class FakePlatform:
    """작업 API 만 흉내 낸다 — `GET /api/jobs/{id}` · `POST /api/jobs/{id}/apply`."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        #: 다음 적용 작업이 어떤 상태로 생기나 — `queued` 면 워커가 안 집는다.
        self.next_status = "done"
        self.next_error = ""
        self.down = False
        #: 이 토큰에 안 보이는 작업(남이 넣은 적용) — GET 이 404.
        self.hidden: set[str] = set()

    def plan(self, kind: str = "bundle_import", **result: Any) -> str:
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "done",
            "params": {},
            "result": {"ok": True, **result},
            "applied_by": None,
        }
        return job_id

    def finish(self, job_id: str, status: str, error: str = "") -> None:
        job = self.jobs[job_id]
        job["status"] = status
        job["error"] = error or None
        job["result"] = (
            {"applied": True, "counts": {"create": 1}} if status == "done" else None
        )
        parent = self.jobs.get(job.get("parent_id") or "")
        if parent is not None and status in ("failed", "cancelled"):
            # 플랫폼은 실패한 적용을 「적용한 것」 으로 세지 않는다(`counts_as_apply`).
            parent["applied_by"] = None

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        self.calls.append((method, path))
        if self.down and method == "GET":
            raise urllib.error.URLError("연결 끊김")
        found = re.fullmatch(r"/api/jobs/([0-9a-f-]{36})", path)
        if method == "GET" and found:
            job = self.jobs.get(found[1])
            if job is None or found[1] in self.hidden:
                return 404, {
                    "error": {"code": "JOBS-5", "message": "작업을 찾을 수 없습니다."}
                }
            child = self.jobs.get(job.get("applied_by") or "")
            # 플랫폼처럼 계획에 그 적용의 상태를 싣는다(`applied_status`).
            return 200, job | {"applied_status": child["status"] if child else None}
        found = re.fullmatch(r"/api/jobs/([0-9a-f-]{36})/apply", path)
        if method == "POST" and found:
            plan = self.jobs[found[1]]
            if plan.get("applied_by"):
                return 409, {
                    "error": {
                        "code": "JOBS-22",
                        "message": "이미 적용했습니다(또는 적용 중입니다)",
                        "details": {"applied_by": plan["applied_by"]},
                    }
                }
            apply_id = str(uuid.uuid4())
            self.jobs[apply_id] = {
                "id": apply_id,
                "kind": plan["kind"],
                "status": "queued",
                "params": {"apply": True},
                "result": None,
                "parent_id": found[1],
            }
            plan["applied_by"] = apply_id
            if self.next_status != "queued":
                self.finish(apply_id, self.next_status, self.next_error)
            return 202, self.jobs[apply_id]
        return 404, {"error": {"code": "TEST", "message": f"모르는 길: {method} {path}"}}

    def posts(self) -> list[str]:
        return [path for method, path in self.calls if method == "POST"]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakePlatform]:
    platform = FakePlatform()
    monkeypatch.setattr(pipeline, "SEND", platform)
    monkeypatch.setattr(pipeline, "WAIT", lambda _seconds: None)
    monkeypatch.setattr(pipeline, "QUEUED_LIMIT_SECONDS", 3.0)
    monkeypatch.setenv("SP_SETTINGS", str(tmp_path / "sp-settings.json"))
    pipeline.save_settings(
        {
            "work_root": str(tmp_path),
            "platforms": {
                "main": {"server": SERVER, "token": TOKEN},
                "other": {"server": "http://other.test", "token": "spt_other"},
            },
        }
    )
    yield platform


def _work(tmp_path: Path, name: str = "작업") -> Path:
    folder = tmp_path / name
    sp_work.init(folder, title="시험", platform="main")
    return folder


def _run(folder: Path, platform: FakePlatform, *, ontology: Any = None) -> Path:
    """미리 본 실행 하나 — 계획 작업은 가짜 플랫폼에 있다."""
    run = sp_work.new_run(folder, "부품")
    pipeline.cmd_init(run)
    if ontology is not None:
        pipeline._write(run / pipeline.ONTOLOGY, ontology)
    pipeline._write(
        run / pipeline.OBJECTS_DIR / "part.json",
        {
            "type_slug": "part",
            "rows": [{"key": "P-1", "label": "부품1", "_source": {"row": 1}}],
        },
    )
    job = platform.plan()
    pipeline._write(
        run / pipeline.PREVIEW,
        {
            "at": "지금",
            "server": SERVER,
            "platform": "main",
            "digest": pipeline.digest(pipeline.payload(pipeline.load(run))),
            "result": {"ok": True, "job_id": job},
        },
    )
    return run


# --- 1 · 2 대기열의 적용은 거절이 아니다 ---------------------------------------------


def test_대기열에_있는_적용은_거절로_적지_않고_다음에_다시_묻는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    run = _run(folder, fake)
    fake.next_status = "queued"  # 워커가 밤 계산으로 바쁘다 — 아무도 안 집는다.

    first = pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    assert first["state"] == "waiting" and first["applied"] is False, first
    assert (
        "거절된 것이 아닙니다" in first["summary"]
        and "다시 적용하지 않는다" in first["summary"]
    )
    assert (run / pipeline.APPLYING).exists()
    assert not (run / pipeline.APPLY_FAILED).exists(), "대기는 거절로 적지 않는다"
    assert not (run / pipeline.APPLIED).exists()
    apply_id = json.loads((run / pipeline.APPLYING).read_text())["apply_job_id"]

    # 작업 상태 — 「적용 대기 중」, 다시 적용하라고 조르지 않는다.
    shown = sp_work.render(sp_work.status(folder))
    assert "적용 대기 중" in shown and "아직 끝나지 않았습니다" in shown, shown
    assert "run_apply(work, run)" not in shown
    with pytest.raises(pipeline.Stop, match="대기열"):
        sp_work.close(folder, run, reason="안 씀")
    with pytest.raises(pipeline.Stop, match="대기열"):
        pipeline.cmd_preview(run, server=SERVER, token=TOKEN)

    # 다시 불러도 **또 넣지 않는다** — 그 적용에 묻는다.
    again = pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    assert again["state"] == "waiting"
    assert len(fake.posts()) == 1, fake.posts()

    # 워커가 집어 끝냈다 — 작업 상태가 그것을 「적용함」 으로 옮긴다.
    fake.finish(apply_id, "done")
    shown = sp_work.render(sp_work.status(folder))
    assert "적용함" in shown and "적용 대기 중" not in shown, shown
    applied = json.loads((run / pipeline.APPLIED).read_text())
    assert applied["apply_job_id"] == apply_id and applied["via"] == "kit"
    assert not (run / pipeline.APPLYING).exists()
    assert len(fake.posts()) == 1


def test_대기하던_적용이_실패하면_거절로_옮기고_잠그지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    run = _run(folder, fake)
    fake.next_status = "queued"
    pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    apply_id = json.loads((run / pipeline.APPLYING).read_text())["apply_job_id"]

    fake.finish(apply_id, "failed", "[JOBS-20] 미리 본 것과 달라졌습니다")
    settled = pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    assert settled["state"] == "failed" and "JOBS-20" in settled["summary"], settled
    assert (run / pipeline.APPLY_FAILED).exists() and not (run / pipeline.APPLYING).exists()
    assert "적용 거절됨" in sp_work.render(sp_work.status(folder))
    # 잠그지 않았다 — 다시 넣을 수 있다(플랫폼도 실패한 적용은 세지 않는다).
    fake.next_status = "done"
    retried = pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    assert retried["state"] == "applied" and retried["applied"] is True, retried
    assert (run / pipeline.APPLIED).exists() and not (run / pipeline.APPLY_FAILED).exists()


def test_기다리는_사이_못_닿아도_거절이_아니다(fake: FakePlatform, tmp_path: Path) -> None:
    run = _run(_work(tmp_path), fake)
    fake.next_status = "queued"
    real = fake.__call__

    def flaky(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
        answer = real(method, url, headers, body)
        if method == "POST":
            fake.down = True  # 202 를 받은 뒤 사내망이 끊겼다
        return answer

    pipeline.SEND = flaky
    out = pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    assert out["state"] == "waiting" and "묻지 못했습니다" in out["summary"], out
    assert (run / pipeline.APPLYING).exists() and not (run / pipeline.APPLY_FAILED).exists()


def test_화면의_적용이_대기_중이면_적용함으로_잠그지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    run = _run(folder, fake)
    plan = json.loads((run / pipeline.PREVIEW).read_text())["result"]["job_id"]
    # 사람이 화면에서 「적용」 을 눌렀다 — 적용 작업은 아직 대기열에.
    fake.next_status = "queued"
    status, made = fake("POST", f"{SERVER}/api/jobs/{plan}/apply", {}, None)
    assert status == 202
    fake.calls.clear()

    shown = sp_work.render(sp_work.status(folder))
    assert "적용 대기 중" in shown, shown
    assert not (run / pipeline.APPLIED).exists(), (
        "대기 중인 적용을 「적용함」 으로 적지 않는다"
    )
    # 키트로 적용해도 `already` 의 대기 — 또 넣지 않고 적용함으로 적지 않는다.
    out = pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    assert out["state"] == "waiting" and not (run / pipeline.APPLIED).exists(), out

    # 그 적용이 실패했다 — 실행은 잠기지 않는다(전에는 applied.json 이 남아 영영 「적용함」).
    fake.finish(made["id"], "failed", "[JOBS-20] 달라졌습니다")
    shown = sp_work.render(sp_work.status(folder))
    assert "적용 거절됨" in shown and not (run / pipeline.APPLIED).exists(), shown
    fake.next_status = "done"
    assert pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)["state"] == "applied"


def test_먼저_넣은_적용이_대기_중이면_already_가_아니라_waiting(fake: FakePlatform) -> None:
    plan = fake.plan()
    fake.next_status = "queued"
    fake("POST", f"{SERVER}/api/jobs/{plan}/apply", {}, None)
    out = pipeline.apply_plan(SERVER, TOKEN, plan)
    assert out["state"] == "waiting", out
    fake.finish(out["apply_job_id"], "done")
    assert pipeline.apply_plan(SERVER, TOKEN, plan)["state"] == "already"


def test_남이_넣어_안_보이는_적용도_대기면_적용함으로_잠그지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    """시스템 관리자가 남의 전역 계획을 화면에서 적용하면 그 적용 작업은 이 토큰에 안 보인다
    (404) — 전에는 곧장 「적용함」 으로 쳐, 대기 중이던 그 적용이 실패해도 영영 잠겼다."""
    folder = _work(tmp_path)
    run = _run(folder, fake)
    plan = json.loads((run / pipeline.PREVIEW).read_text())["result"]["job_id"]
    fake.next_status = "queued"
    _status, made = fake("POST", f"{SERVER}/api/jobs/{plan}/apply", {}, None)
    fake.hidden.add(made["id"])

    assert "적용 대기 중" in sp_work.render(sp_work.status(folder))
    assert not (run / pipeline.APPLIED).exists()
    fake.finish(made["id"], "failed", "달라졌습니다")
    assert "적용 거절됨" in sp_work.render(sp_work.status(folder))
    assert not (run / pipeline.APPLIED).exists(), "실패한 남의 적용으로 잠그지 않는다"


def test_등록한_주소가_바뀌어도_대기하던_적용을_같은_이름으로_다시_묻는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    """설치기를 다시 돌려 그 플랫폼의 주소가 바뀌면(포털 주소로 등) 옛 주소로만 물어 영영
    「적용 대기 중」 이었고, 다시 미리 보기 · 닫기도 막혔다(0.4.107 재점검)."""
    folder = _work(tmp_path)
    run = _run(folder, fake)
    fake.next_status = "queued"
    pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    apply_id = json.loads((run / pipeline.APPLYING).read_text())["apply_job_id"]
    settings = pipeline.load_settings()
    settings["platforms"]["main"]["server"] = "http://portal.test"
    pipeline.save_settings(settings)

    assert "적용 대기 중" in sp_work.render(sp_work.status(folder))
    fake.finish(apply_id, "done")
    assert "적용함" in sp_work.render(sp_work.status(folder))
    assert (run / pipeline.APPLIED).exists() and not (run / pipeline.APPLYING).exists()


def test_그_플랫폼이_등록에서_빠지면_대기하던_실행을_닫을_수_있다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    run = _run(folder, fake)
    fake.next_status = "queued"
    pipeline.cmd_apply_plan(run, server=SERVER, token=TOKEN)
    settings = pipeline.load_settings()
    del settings["platforms"]["main"]
    pipeline.save_settings(settings)
    assert "닫았습니다" in sp_work.close(folder, run, reason="그 플랫폼을 안 쓴다")


def test_같은_묶음이_다른_실행에서_대기_중이면_또_넣지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    """다른 실행의 `applied.json` 만 봐, 앞 실행이 대기 중인 사이 같은 원천을 다시 변환한
    실행이 또 들어갔다(백필의 가벼운 미리 보기는 지문이 없어 플랫폼도 못 막는다)."""
    folder = _work(tmp_path)
    first = _run(folder, fake)
    fake.next_status = "queued"
    pipeline.cmd_apply_plan(first, server=SERVER, token=TOKEN)
    second = _run(folder, fake)
    assert second != first

    with pytest.raises(pipeline.Stop, match="대기열"):
        sp_work.before_apply(folder, second)
    state = sp_work.status(folder)
    again = next(one for one in state["runs"] if one["run"].endswith(second.name))
    assert again["same_as"], state


# --- 3 · 9 정의 · 넣을 곳 ----------------------------------------------------------


V1 = {"types": [{"slug": "part", "label": "부품", "properties": []}]}
V2 = {
    "types": [{"slug": "part", "label": "부품", "properties": [{"key": "w", "label": "무게"}]}]
}


def test_확정한_정의와_다른_정의로_변환한_실행은_넣지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    definition = folder / sp_work.ONTOLOGY
    definition.write_text(json.dumps(V1, ensure_ascii=False), encoding="utf-8")
    sp_work.record(folder, topic="정의", decision="v1", confirms_ontology=True)
    # 변환은 정의를 실행 폴더에 다시 쓴다(들여쓰기가 달라도 같은 정의다).
    r1 = _run(folder, fake, ontology=V1)
    sp_work.before_apply(folder, r1)

    # v2 로 고쳐 다시 확정 — v1 으로 변환한 실행은 넣지 않는다.
    definition.write_text(json.dumps(V2, ensure_ascii=False), encoding="utf-8")
    sp_work.record(folder, topic="정의", decision="v2", confirms_ontology=True)
    with pytest.raises(pipeline.Stop, match="다시 변환하세요"):
        sp_work.before_apply(folder, r1)
    # 정의를 싣지 않는 실행(있는 타입에 넣기)은 견줄 것이 없다.
    bare = _run(folder, fake)
    sp_work.before_apply(folder, bare)

    # 확정한 정의 파일이 없어졌으면 조용히 지나가지 않는다.
    definition.unlink()
    with pytest.raises(pipeline.Stop, match="없어졌습니다"):
        sp_work.before_apply(folder, bare)

    # 「새 정의 없음」 으로 확정했는데 실행이 정의를 싣는다 — 넣지 않는다.
    sp_work.record(folder, topic="정의", decision="새 정의 없음", confirms_ontology=True)
    sp_work.before_apply(folder, bare)
    with pytest.raises(pipeline.Stop, match="확정한 정의가 없습니다"):
        sp_work.before_apply(folder, r1)

    # 허브에서 받은 실행의 정의는 허브의 것 — 02-정의 와 견주지 않는다.
    hub = _run(folder, fake, ontology=V2)
    manifest = json.loads((hub / pipeline.MANIFEST).read_text())
    pipeline._write(hub / pipeline.MANIFEST, manifest | {"source": "hub"})
    sp_work.before_apply(folder, hub)


def test_미리_본_뒤_넣을_곳을_바꾸면_다시_미리_본다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    run = _run(folder, fake)
    sp_work.record(folder, topic="정의", decision="새 정의 없음", confirms_ontology=True)
    sp_work.before_apply(folder, run)
    sp_work.set_platform(folder, "other")
    with pytest.raises(pipeline.Stop, match=r"미리 본 뒤 바뀌었습니다.*run_preview"):
        sp_work.before_apply(folder, run)


# --- 5 판 번호 ---------------------------------------------------------------------


def test_판_번호는_uuid_꼴만_주소에_넣는다(fake: FakePlatform) -> None:
    run_id = str(uuid.uuid4())
    with pytest.raises(pipeline.Stop, match="uuid 꼴"):
        pipeline.undo_job(f"{run_id}/undo?apply=true#", server=SERVER, token=TOKEN)
    assert fake.calls == [], "번호가 아니면 플랫폼에 묻지도 않는다"

    # 대문자로 적은 같은 번호는 같은 판이다.
    plan = fake.plan(kind="bundle_undo")
    fake.jobs[plan]["params"] = {"run_id": run_id}
    out = pipeline.cmd_undo_apply(run_id.upper(), plan.upper(), server=SERVER, token=TOKEN)
    assert out["state"] == "applied" and out["undone"] is True, out
    with pytest.raises(pipeline.Stop, match="계획 작업의 번호가 아닙니다"):
        pipeline.cmd_undo_apply(run_id, f"{plan}/apply", server=SERVER, token=TOKEN)


# --- 4 · 8 문서 올리기의 적용 -------------------------------------------------------


def _record(folder: Path, jobs: list[str]) -> Path:
    where = folder / sp_docpush.PUSH_DIR / "20261127-120000"
    where.mkdir(parents=True)
    path = where / sp_docpush.RECORD
    pipeline._write(
        path,
        {
            "at": "20261127-120000",
            "server": SERVER,
            "platform": "main",
            "jobs": [{"job_id": one, "ok": True, "counts": {"create": 1}} for one in jobs],
            "documents": [],
        },
    )
    return path


def _doc_plan(fake: FakePlatform, **counts: int) -> str:
    return fake.plan(
        kind="documents_push",
        counts={"create": 1, "update": 0, "unchanged": 0, "error": 0, **counts},
    )


def test_문서_적용은_올린_기록의_문서_올리기_계획만_넣는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    # 기록의 자리가 아니다 — 작업 폴더의 아무 JSON.
    stray = folder / "02-정의" / "x.json"
    pipeline._write(stray, {"server": SERVER, "jobs": [{"job_id": fake.plan(), "ok": True}]})
    with pytest.raises(pipeline.Stop, match="남긴 기록"):
        sp_docpush.record_path(folder, "02-정의/x.json")
    with pytest.raises(pipeline.Stop, match="남긴 기록"):
        sp_docpush.apply(folder, stray, server=SERVER, token=TOKEN)

    # 번호 모양이 아니면 아무것도 묻지 않는다.
    bad = _record(folder, ["../../auth/tokens"])
    with pytest.raises(pipeline.Stop, match="uuid 꼴"):
        sp_docpush.apply(folder, bad, server=SERVER, token=TOKEN)
    assert fake.posts() == []

    # 다른 종류의 계획(묶음 가져오기)을 이 길로 적용하지 않는다.
    bad.unlink()
    other = fake.plan(kind="bundle_import")
    pipeline._write(bad, {"server": SERVER, "jobs": [{"job_id": other, "ok": True}]})
    out = sp_docpush.apply(folder, bad, server=SERVER, token=TOKEN)
    assert out["applied"][0]["state"] == "refused", out
    assert "문서 올리기의 계획이 아닙니다" in out["applied"][0]["message"]
    assert fake.posts() == []


def test_여러_작업은_함께_보고_하나라도_틀리면_아무것도_넣지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    good, broken = _doc_plan(fake), _doc_plan(fake, error=1)
    out = sp_docpush.apply(folder, _record(folder, [good, broken]), server=SERVER, token=TOKEN)
    states = [one["state"] for one in out["applied"]]
    assert states == ["not_applied", "refused"], out
    assert out["all_applied"] is False and "아무것도 넣지" in out["message"]
    assert fake.posts() == [], "앞 작업도 넣지 않는다"


def test_뒤_작업이_거절되면_어느_것이_들어갔는지_말하고_그_뒤는_넣지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    jobs = [_doc_plan(fake), _doc_plan(fake), _doc_plan(fake)]
    record = _record(folder, jobs)
    real = fake.__call__

    def race(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
        # 두 번째 작업을 넣을 때는 그 사이 누가 문서를 고쳐 지문이 다르다.
        fake.next_status, fake.next_error = (
            ("failed", "[JOBS-20] 미리 본 것과 달라졌습니다")
            if url.endswith(f"{jobs[1]}/apply")
            else ("done", "")
        )
        return real(method, url, headers, body)

    pipeline.SEND = race
    out = sp_docpush.apply(folder, record, server=SERVER, token=TOKEN)
    states = [one["state"] for one in out["applied"]]
    assert states == ["applied", "failed", "not_applied"], out
    assert jobs[0] in out["message"] and "거절돼" in out["message"], out["message"]
    assert len(fake.posts()) == 2, "세 번째는 넣지 않는다"
    saved = json.loads(record.read_text())["jobs"]
    assert saved[0]["apply_state"] == "applied" and saved[1]["apply_state"] == "failed"


def test_문서의_대기_중_적용은_적용함으로_적어_두지_않는다(
    fake: FakePlatform, tmp_path: Path
) -> None:
    folder = _work(tmp_path)
    first, second = _doc_plan(fake), _doc_plan(fake)
    record = _record(folder, [first, second])
    fake.next_status = "queued"
    out = sp_docpush.apply(folder, record, server=SERVER, token=TOKEN)
    assert [one["state"] for one in out["applied"]] == ["waiting", "not_applied"], out
    assert out["waiting"] is True and out["all_applied"] is False
    saved = json.loads(record.read_text())["jobs"][0]
    assert saved["apply_state"] == "waiting", saved

    # 그 적용이 실패했다 — 「already」 로 남지 않는다.
    fake.finish(saved["apply_job_id"], "failed", "[JOBS-20] 달라졌습니다")
    later = sp_docpush.apply(folder, record, server=SERVER, token=TOKEN)
    assert later["applied"][0]["state"] == "failed", later
    assert "apply_job_id" not in json.loads(record.read_text())["jobs"][0]

    # 다시 부르면 다시 본 뒤 넣고, 끝났으면 다음 작업도 이어서.
    fake.next_status = "done"
    again = sp_docpush.apply(folder, record, server=SERVER, token=TOKEN)
    assert [one["state"] for one in again["applied"]] == ["applied", "applied"], again
    assert again["all_applied"] is True
    twice = sp_docpush.apply(folder, record, server=SERVER, token=TOKEN)
    assert [one["state"] for one in twice["applied"]] == ["already", "already"], twice


# --- 6 대응 파일 --------------------------------------------------------------------


def test_대응_파일은_작업_폴더_밖의_정의_식별자_파일을_못_읽는다(tmp_path: Path) -> None:
    folder = tmp_path / "작업"
    sp_work.init(folder, title="시험")
    secret = tmp_path / "밖.json"
    secret.write_text(json.dumps({"types": [{"slug": "x"}]}), encoding="utf-8")
    source = folder / sp_work.SOURCES / "표.csv"
    source.write_text("코드,이름\nA-1,가\n", encoding="utf-8")
    mapping = folder / sp_work.MAPPING / "표.table.json"

    def write(spec: dict[str, Any]) -> None:
        body = {
            "format": "sp-table/1",
            "types": [{"type_slug": "part", "key": {"column": "코드"}}],
            **spec,
        }
        mapping.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    write({"ontology": "../../밖.json"})
    with pytest.raises(pipeline.Stop, match="작업 폴더 밖"):
        sp_table.convert(mapping, source, folder / "runs" / "r1", root=folder)
    # 명령 창도 — 대응 파일이 든 작업 폴더를 스스로 찾는다.
    with pytest.raises(pipeline.Stop, match="작업 폴더 밖"):
        sp_table.convert(mapping, source, folder / "runs" / "r2")
    write(
        {
            "types": [
                {
                    "type_slug": "part",
                    "key": {"column": "코드"},
                    "fields": {
                        "model": {"column": "이름", "match": {"keys": "@../../밖.json"}}
                    },
                }
            ]
        }
    )
    with pytest.raises(pipeline.Stop, match="작업 폴더 밖"):
        sp_table.convert(mapping, source, folder / "runs" / "r3", root=folder)
    # 안의 정의는 읽는다.
    (folder / sp_work.ONTOLOGY).write_text(json.dumps(V1), encoding="utf-8")
    write({"ontology": "../02-정의/ontology.json"})
    ok, _report = sp_table.convert(mapping, source, folder / "runs" / "r4", root=folder)
    assert ok and json.loads((folder / "runs" / "r4" / "ontology.json").read_text()) == V1


# --- 7 · 10 · 11 · 12 문서 올리기의 준비 -------------------------------------------------


def _png(seed: int) -> bytes:
    # 내용만 다르면 된다 — 키트는 그림을 열어 보지 않는다(플랫폼이 본다).
    return b"\x89PNG\r\n\x1a\n" + seed.to_bytes(4, "big") * 8


def _doc(folder: Path, name: str, body: str, images: dict[str, bytes] | None = None) -> str:
    where = folder / "docs" / name
    for relative, data in (images or {}).items():
        (where / relative).parent.mkdir(parents=True, exist_ok=True)
        (where / relative).write_bytes(data)
    where.mkdir(parents=True, exist_ok=True)
    (where / "draft.md").write_text(body, encoding="utf-8")
    return f"docs/{name}/draft.md"


def _item(path: str, **extra: Any) -> sp_docpush.Item:
    return sp_docpush.Item("paper", str(uuid.uuid4()), "body", path, **extra)


def test_zip_항목_수가_플랫폼_상한을_넘지_않게_나눈다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 플랫폼은 항목 2000 개를 넘는 zip 을 통째로 거절한다 — 1000 편에 그림이 1000 장을 넘으면
    # 작업 전체가 막혔다. 문서 1000 편 · 편마다 다른 그림 둘.
    ready = []
    for index in range(1000):
        one = sp_docpush.Prepared(_item(f"docs/{index}.md"), body="가", size=10)
        one.image_sha = {
            f"a{index}.png": f"{index:064x}",
            f"b{index}.png": f"{index + 5000:064x}",
        }
        ready.append(one)
    chunks = sp_docpush._chunks(ready)
    assert len(chunks) > 1 and sum(len(one) for one in chunks) == 1000
    assert all(sp_docpush.entries(one) <= sp_docpush.ZIP_MAX_ENTRIES for one in chunks)
    assert sp_docpush.ZIP_MAX_ENTRIES < 2000

    # 한 편에 그림이 너무 많으면 올리기 전에 막는다.
    monkeypatch.setattr(sp_docpush, "ZIP_MAX_ENTRIES", 4)
    body = "\n".join(f"![{n}](media/{n}.png)" for n in range(3))
    path = _doc(tmp_path, "많음", body, {f"media/{n}.png": _png(n) for n in range(3)})
    problems = sp_docpush.prepare(tmp_path, [_item(path)])[0].problems
    assert any("서로 다른 그림이 3장" in one for one in problems), problems


def test_빈_본문은_사람이_비우라고_했을_때만_올린다(tmp_path: Path) -> None:
    path = _doc(tmp_path, "빈것", "  \n\n")
    refused = sp_docpush.prepare(tmp_path, [_item(path)])[0]
    assert any("본문이 비었습니다" in one for one in refused.problems), refused.problems
    allowed = sp_docpush.prepare(tmp_path, [_item(path, allow_empty=True)])[0]
    assert allowed.problems == [], allowed.problems
    listed = sp_docpush.items_of(
        [{"type_slug": "a", "object_id": "b", "key": "c", "path": path, "allow_empty": True}]
    )
    assert listed[0].allow_empty is True
    with pytest.raises(pipeline.Stop, match="allow_empty"):
        sp_docpush.items_of(
            [
                {
                    "type_slug": "a",
                    "object_id": "b",
                    "key": "c",
                    "path": path,
                    "allow_empty": "네",
                }
            ]
        )


def test_NUL_은_올리기_전에_막는다(tmp_path: Path) -> None:
    path = _doc(tmp_path, "널", "# 제목\n\n본문\x00끝\n")
    problems = sp_docpush.prepare(tmp_path, [_item(path)])[0].problems
    assert any("3번째 줄에 NUL" in one for one in problems), problems

    # 묶음의 문서 본문(`_documents`)도 같다.
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    (run / "documents").mkdir()
    (run / "documents" / "a.md").write_text("가\x00나", encoding="utf-8")
    pipeline._write(
        run / "objects" / "part.json",
        {
            "type_slug": "part",
            "rows": [
                {"key": "P-1", "label": "부품", "_documents": {"body": "documents/a.md"}}
            ],
        },
    )
    report = pipeline.validate(pipeline.load(run))
    assert any("NUL" in one for one in report.errors), report.errors
    assert "documents" not in pipeline.payload(pipeline.load(run))


def test_그림_꼴은_플랫폼과_같고_되풀이에_제곱으로_늘지_않는다(tmp_path: Path) -> None:
    assert sp_docpush.IMAGE.pattern == (
        r"(?P<head>!\[[^\[\]\n]*\]\()(?P<target>[^()\[\]\s?]+)(?P<query>\?w=\d{1,3})?\)"
    )
    hostile = "![](" * 50_000  # 20만 자
    started = time.perf_counter()
    assert list(sp_docpush.IMAGE.finditer(hostile)) == []
    sp_docpush.unconverted(hostile)
    sp_docpush.unconverted("![" * 100_000)
    path = _doc(tmp_path, "되풀이", hostile)
    sp_docpush.prepare(tmp_path, [_item(path)])
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "line",
    [
        '![제목](media/a.png "설명")',
        "![빈칸](media/my a.png)",
        "![폭](media/a.png?w=1000)",
        "![캡션]에 괄호](media/a.png)",
        "![절대](/home/me/a.png)",
        "![박음](data:image/png;base64,iVBORw0KGgo=)",
    ],
)
def test_그림으로_못_읽는_그림_자리는_올리기_전에_오류(tmp_path: Path, line: str) -> None:
    path = _doc(tmp_path, "꼴", f"# 제목\n\n{line}\n", {"media/a.png": _png(1)})
    problems = sp_docpush.prepare(tmp_path, [_item(path)])[0].problems
    assert any(
        "그림으로 못 읽는 그림 자리" in one and "2번째" not in one for one in problems
    ), problems
    assert any("3번째 줄" in one for one in problems), problems


def test_올라가는_그림_자리와_바깥_주소_코드는_그대로(tmp_path: Path) -> None:
    body = "\n".join(
        [
            "![사진](media/a.png?w=60)",
            "![붙은 것](photo:0b6f7c1e-0000-4000-8000-000000000000?w=40)",
            '![바깥](https://example.com/a b.png "t")',
            "`![코드](my file.png)` 는 그림 줄의 예다",
            "![로고][1] 는 [안내](guide.md) 를 본다",
            "```",
            "![블록](/abs.png)",
            "```",
        ]
    )
    path = _doc(tmp_path, "좋음", body, {"media/a.png": _png(1)})
    one = sp_docpush.prepare(tmp_path, [_item(path)])[0]
    assert one.problems == [], one.problems
    assert one.images == {"media/a.png": (tmp_path / "docs/좋음/media/a.png").resolve()}


def test_같은_그림은_한_번만_싣는다(tmp_path: Path) -> None:
    same = _png(7)
    body = "![하나](./media/a.png)\n\n![둘](media/a.png)\n\n![셋](media/copy.png)\n"
    path = _doc(tmp_path, "같음", body, {"media/a.png": same, "media/copy.png": same})
    one = sp_docpush.prepare(tmp_path, [_item(path)])[0]
    assert one.problems == [] and len(set(one.image_sha.values())) == 1
    assert one.size == len(body.encode("utf-8")) + len(same), "같은 내용은 한 번만 센다"
    with zipfile.ZipFile(io.BytesIO(sp_docpush._zip([one]))) as archive:
        names = [name for name in archive.namelist() if name.startswith("img/")]
        written = archive.read("docs/00001.md").decode("utf-8")
    assert len(names) == 1, names
    assert written.count(f"(../{names[0]})") == 3, written


# --- 13 · 14 플랫폼의 답 --------------------------------------------------------------


class _Page:
    status = 200
    headers: ClassVar[dict[str, str]] = {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self) -> _Page:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return b"<!DOCTYPE html><html><body>HWAX login</body></html>"


def test_JSON_이_아닌_답은_로그인이_끝났을_수_있다고_말한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", lambda *_a, **_k: _Page())
    with pytest.raises(pipeline.Stop) as stopped:
        pipeline._urllib_send("GET", f"{SERVER}/api/jobs/x?y=1", {}, None)
    said = str(stopped.value)
    assert "JSON 이 아닌 답" in said and "포털" in said and "로그인" in said, said
    assert "Expecting value" not in said and "y=1" not in said
    # 묻기만 하는 자리는 멈추지 않고 「모른다」.
    monkeypatch.setattr(pipeline, "SEND", pipeline._urllib_send)
    assert pipeline.plan_apply_job(SERVER, TOKEN, str(uuid.uuid4())) is None


def test_가벼운_미리_보기에는_지문이_없다고_요약이_말한다() -> None:
    said = pipeline.summarize({"ok": True, "preview": "plan", "objects": []})
    assert "지문이 없어" in said and "다시 판정" in said
    assert "지문" not in pipeline.summarize({"ok": True, "objects": []})
