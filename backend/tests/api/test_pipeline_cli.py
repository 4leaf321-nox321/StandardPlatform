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

from tests.api.conftest import Signed, bundle_import, patched_pipeline

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
    # 6자리면 한 번의 전체 시험에서 만드는 수천 개끼리 부딪친다(2026-10-05, 409) — 12자리.
    return f"{base}_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    """정제 도구가 TestClient 앱에 말한다 — 작업을 물을 때마다 워커가 한 바퀴 돈다."""
    with patched_pipeline(pipeline, client):
        yield


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
    made = bundle_import(
        client,
        admin,
        {
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
    )
    assert made["applied"] is True, made
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


def test_아무도_집어_가지_않으면_멈추고_말한다(
    client: TestClient, admin: Signed, tmp_path: Path
) -> None:
    """**워커가 꺼져 있으면 작업은 영영 대기다.** 말없이 계속 기다리면 사람은 제 묶음이
    잘못된 줄 알고 몇 번을 다시 만든다 — 원인은 서버에 있는데."""
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    _fill(run, "cae")
    token = _token(client, admin)

    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        # 워커를 **안 돌린다** — 넣은 작업이 계속 `queued` 다.
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        got = client.request(method, path, content=body, headers=headers)
        return got.status_code, got.json()

    before_send, before_wait = pipeline.SEND, pipeline.WAIT
    pipeline.SEND, pipeline.WAIT = send, lambda _seconds: None
    try:
        with pytest.raises(pipeline.Stop, match="작업 워커가 꺼져 있는 것 같습니다"):
            pipeline.cmd_preview(run, server=SERVER, token=token)
    finally:
        pipeline.SEND, pipeline.WAIT = before_send, before_wait


def test_검증이_끝점을_플랫폼에_미리_묻는다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    """**끝점이 풀리는지는 미리 보기에서야 알았다.**

    미리 보기는 계획을 세우느라 몇 분이 걸리고, 그 몇 분 뒤에 「가리키는 것이 없다」 를
    듣는다. 물어서 아는 것은 먼저 묻는다 — 주소와 토큰이 있으면 검증이 판정을 받아 온다.
    판정은 **플랫폼의 것**을 쓴다(식별자 → 별칭 → 이름 순서와 겹침 규칙이 거기 있다).
    """
    token = _token(client, admin)
    run = tmp_path / "run"
    pipeline.cmd_init(run)
    names = _fill(run, admin.workspace)
    made = client.post(
        "/api/ontology/import",
        json={
            "types": [{"slug": names["company"], "label": "기업", "key_policy": "required"}]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text

    # 이 실행이 기업도 함께 만든다 — 아직 플랫폼에 없어도 끝점은 풀린 것으로 본다.
    ok, text = pipeline.cmd_validate(run, server=SERVER, token=token)
    assert ok is True, text
    assert "끝점 확인함" in text

    # 같은 실행에서 기업을 빼면 — 가리키는 것이 플랫폼에도 없다.
    (run / "objects" / "b_company.json").unlink()
    manifest = json.loads((run / "bundle.json").read_text(encoding="utf-8"))
    manifest["objects_order"] = [names["tool"]]
    _write(run / "bundle.json", manifest)
    ok, text = pipeline.cmd_validate(run, server=SERVER, token=token)
    assert ok is False, text
    assert "가리키는 것이 플랫폼에 없습니다" in text and "C-1" in text

    # 주소가 없으면 모양만 본다 — 예전과 같다(오프라인에서도 돌아야 한다).
    ok, text = pipeline.cmd_validate(run)
    assert ok is True, text
    assert "끝점 확인함" not in text

    # 이름이 여럿과 맞으면 막는다 — 플랫폼은 그때 고르지 않는다.
    for label in ("한화정밀", "한화중공업"):
        got = client.post(
            f"/api/objects/{names['company']}/import-rows",
            json={"rows": [{"key": label, "label": label}], "apply": True},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
    _write(
        run / "objects" / "a_tool.json",
        {
            "type_slug": names["tool"],
            "workspace_slug": admin.workspace,
            "rows": [{"key": "T-1", "label": "툴1", "vendor": "한화"}],
        },
    )
    ok, text = pipeline.cmd_validate(run, server=SERVER, token=token)
    assert ok is False, text
    assert "이름이 여럿과 맞습니다" in text


def test_받을_때_허브가_적은_것을_떨어뜨리지_않는다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    """**허브가 적어 보낸 것을 도구가 버리면 두 설치가 갈린다.**

    별칭·관계의 「맞춤」(`aliases_mode` · `mode`)과 **사라진 것**(`tombstones`)이 그것이다.
    떨어뜨리면 허브에서 뺀 별칭 · 끊은 선 · 지운 객체가 받는 쪽에 그대로 남는다.
    묶음도 여럿 받는다 — 코어를 축별로 나눈 허브에서 하나씩 받으면 축끼리 가리키는 참조
    때문에 어느 쪽도 못 받는다.
    """
    tag = uuid.uuid4().hex[:6]
    group, other, kind = f"hg{tag}", f"hg2{tag}", f"hk{tag}"
    made = bundle_import(
        client,
        admin,
        {
            "ontology": {
                "groups": [
                    {"slug": group, "label": "허브 묶음"},
                    {"slug": other, "label": "다른 묶음"},
                ],
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
            "objects": [
                {
                    "type_slug": kind,
                    "rows": [
                        {"key": "K-1", "label": "하나", "aliases": ["첫째"]},
                        {"key": "K-2", "label": "둘", "aliases": ["둘째"]},
                    ],
                }
            ],
            "apply": True,
        },
    )
    assert made["applied"] is True, made
    token = _token(client, admin)

    # 허브에서 하나를 지운다 — 그것이 무덤으로 간다.
    rows = client.get(f"/api/objects/{kind}", headers=admin.headers).json()["items"]
    gone = next(one for one in rows if one["key"] == "K-2")
    dropped = client.delete(f"/api/objects/{kind}/{gone['id']}", headers=admin.headers)
    assert dropped.status_code in (200, 204), dropped.text

    run = tmp_path / "pulled"
    text = pipeline.cmd_pull(run, hub=SERVER, hub_token=token, group=[group, other])
    assert "사라진 것 1" in text, text

    # 파일로 남아 있고, 보낼 때 **그대로** 실린다.
    graves = json.loads((run / "tombstones.json").read_text(encoding="utf-8"))
    assert [one["key"] for one in graves["objects"]] == ["K-2"]
    body = pipeline.payload(pipeline.load(run))
    assert body["tombstones"] == graves
    assert body["objects"][0]["aliases_mode"] == "replace"

    ok, report = pipeline.cmd_validate(run)
    assert ok is True, report


def test_백필_칸을_실행_폴더에서_켠다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    """**도구로 넣는 백필이 기본값으로 돌았다.**

    네 칸(`preview` · `events` · `audit` · `missing_refs`)을 보내는 쪽이 API 뿐이라, 정제
    도구로 수만 줄을 넣으면 미리 보기가 적용을 두 번 돌고 웹훅이 수만 번 나갔다. 실행
    폴더(`bundle.json`)가 그것을 정하고, `--backfill` 이 넷을 한꺼번에 켠다.
    """
    run = tmp_path / "run"
    pipeline.cmd_init(run, backfill=True)
    names = _fill(run, admin.workspace)
    body = pipeline.payload(pipeline.load(run))
    assert body["preview"] == "plan"
    assert body["events"] == "summary"
    assert body["audit"] == "summary"
    assert body["missing_refs"] == "blank"

    # 켜지 않으면 안 실린다 — 평소 적재는 기본값이 더 엄하다.
    plain = tmp_path / "plain"
    pipeline.cmd_init(plain)
    _fill(plain, admin.workspace)
    assert "preview" not in pipeline.payload(pipeline.load(plain))

    # 한 칸만 켜도 된다. 값이 틀리면 검증이 잡는다.
    manifest = json.loads((plain / "bundle.json").read_text(encoding="utf-8"))
    manifest["audit"] = "summary"
    _write(plain / "bundle.json", manifest)
    assert pipeline.payload(pipeline.load(plain))["audit"] == "summary"
    manifest["audit"] = "없는값"
    _write(plain / "bundle.json", manifest)
    ok, report = pipeline.cmd_validate(plain)
    assert ok is False and "audit 은" in report, report

    # 그대로 보내면 플랫폼이 받는다 — 가벼운 미리 보기로 돈다.
    token = _token(client, admin)
    ok, summary = pipeline.cmd_preview(run, server=SERVER, token=token)
    assert ok is True, summary
    ok, summary = pipeline.cmd_apply(run, server=SERVER, token=token)
    assert ok is True, summary
    tools = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()
    assert [one["key"] for one in tools["items"]] == ["T-1"]


def test_넣은_판을_도구로_되돌린다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    """**백필을 되돌릴 길이 도구에 없었다.**

    수만 줄을 넣은 뒤 원천을 잘못 맞춘 것을 알아도 화면에서 하나씩 고치는 길밖에 없었다.
    `runs` 가 판 번호를 보여 주고, `undo` 가 그 판만 되돌린다 — 기본은 계획이다.
    """
    run = tmp_path / "run"
    pipeline.cmd_init(run, backfill=True)
    names = _fill(run, admin.workspace)
    token = _token(client, admin)
    assert pipeline.cmd_preview(run, server=SERVER, token=token)[0] is True
    ok, summary = pipeline.cmd_apply(run, server=SERVER, token=token)
    assert ok is True, summary

    ok, listed = pipeline.cmd_runs(server=SERVER, token=token)
    assert ok is True and names["tool"] in listed, listed
    run_id = next(
        line.split()[0]
        for line in listed.splitlines()[1:]
        if names["tool"] in line or names["company"] in line
    )

    # 계획 — 아직 그대로다.
    ok, text = pipeline.cmd_undo(run_id, server=SERVER, token=token)
    assert ok is True and "계획입니다" in text, text
    tools = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()
    assert [one["key"] for one in tools["items"]] == ["T-1"]

    # 되돌린다.
    ok, text = pipeline.cmd_undo(run_id, server=SERVER, token=token, apply=True)
    assert ok is True and "되돌렸습니다" in text, text
    tools = client.get(f"/api/objects/{names['tool']}", headers=admin.headers).json()
    assert tools["items"] == [], tools
