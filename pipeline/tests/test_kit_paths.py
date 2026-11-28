"""키트가 **작업 폴더 밖에 쓰거나 · 다른 주소로 가거나 · 토큰을 남에게 주는** 길 — 0.4.109 뒤
보고된 넷.

1. 대응 파일의 `type_slug` · `relation` 이 실행 폴더의 파일 이름이 되어 밖의 JSON 을 덮었다.
2. `source_profile(sheet=…)` 의 시트가 결과 파일 이름이 되어(CSV 는 시트를 안 봐 검사도 없었다)
   Windows 에서 `01-조사/` 밖에 썼다.
3. 플랫폼 주소를 값 그대로 이어(`documents/{key}` · `objects/{slug}`) `..` · `#` · `?` 가 다른
   길이 됐다.
4. urllib 가 다른 호스트로 넘기는 답을 따라가며 `Authorization` 을 그대로 실었다.
5. (같은 점검에서) 같은 관계 종류를 출발 타입만 달리 두 번 적으면 두 절이 같은 파일
   `relations/<관계>.json` 에 써서 나중 것이 앞의 선을 말없이 덮었다.
"""

from __future__ import annotations

import http.server
import json
import threading
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import pytest
import sp_docpush
import sp_pipeline as pipeline
import sp_profile
import sp_table
import sp_work

SERVER = "http://platform.test"
TOKEN = "spt_test"


class Recorder:
    """보낸 요청을 적기만 하는 가짜 플랫폼 — 모르는 길은 404."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        #: 경로(질의까지) → 답. 없으면 404.
        self.answers: dict[str, tuple[int, Any]] = {}

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        path = url.removeprefix(SERVER)
        self.calls.append((method, path))
        return self.answers.get(
            path, (404, {"error": {"code": "TEST", "message": "모르는 길"}})
        )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(pipeline, "SEND", recorder)
    monkeypatch.setattr(pipeline, "WAIT", lambda _seconds: None)
    return recorder


# --- 1 대응 파일의 slug ------------------------------------------------------------


def _work(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(작업 폴더, 원천, 대응 파일 자리). 작업 폴더는 `tmp_path/작업`."""
    folder = tmp_path / "작업"
    sp_work.init(folder, title="시험")
    source = folder / sp_work.SOURCES / "표.csv"
    source.write_text("코드,이름\nA-1,가\nA-2,나\n", encoding="utf-8")
    return folder, source, folder / sp_work.MAPPING / "표.table.json"


def _victim(tmp_path: Path, name: str) -> Path:
    """작업 폴더 **밖**의 설정 파일(Claude Desktop 설정 · 키트 설정 자리)."""
    victim = tmp_path / "밖" / f"{name}.json"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    return victim


def test_대응_파일의_type_slug_로_작업_폴더_밖을_덮지_못한다(tmp_path: Path) -> None:
    folder, source, mapping = _work(tmp_path)
    victim = _victim(tmp_path, "claude_desktop_config")
    # runs/<실행>/objects/ 에서 네 번 올라가면 작업 폴더의 부모 — 그 옆의 밖/.
    evil = "../../../../밖/claude_desktop_config"
    mapping.write_text(
        json.dumps(
            {
                "format": "sp-table/1",
                "types": [{"type_slug": evil, "key": {"column": "코드"}}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(pipeline.Stop, match="slug 꼴이 아닙니다"):
        sp_table.convert(mapping, source, folder / "runs" / "r1", root=folder)
    assert victim.read_text(encoding="utf-8") == '{"mcpServers": {}}\n'
    # 끝에 줄바꿈 하나 — `re.match("…$")` 는 지나친다(fullmatch 로 본다).
    mapping.write_text(
        json.dumps(
            {
                "format": "sp-table/1",
                "types": [{"type_slug": "part\n", "key": {"column": "코드"}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(pipeline.Stop, match="slug 꼴이 아닙니다"):
        sp_table.convert(mapping, source, folder / "runs" / "r2", root=folder)


def test_대응_파일의_relation_으로_작업_폴더_밖을_덮지_못한다(tmp_path: Path) -> None:
    folder, source, mapping = _work(tmp_path)
    victim = _victim(tmp_path, "settings")
    # 선만 담은 대응 파일 — types 가 없으니 type_slug 도 types 와 견주지 못한다.
    for field, evil in (
        ("relation", "../../../../밖/settings"),
        ("type_slug", "../../x"),
    ):
        one = {
            "relation": "tested_at",
            "type_slug": "part",
            "src": {"column": "코드"},
            "dst": {"column": "이름"},
        }
        one[field] = evil
        mapping.write_text(
            json.dumps({"format": "sp-table/1", "relations": [one]}, ensure_ascii=False),
            encoding="utf-8",
        )
        with pytest.raises(pipeline.Stop, match=f"{field} .* slug 꼴이 아닙니다"):
            sp_table.convert(mapping, source, folder / "runs" / f"r-{field}", root=folder)
    assert victim.read_text(encoding="utf-8") == '{"mcpServers": {}}\n'


def test_slug_검사를_지나쳐도_쓰는_자리는_실행_폴더_안만(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 겹이다 — slug 꼴(첫 겹)을 빼도 쓰는 자리를 `inside` 가 본다."""
    folder, source, mapping = _work(tmp_path)
    victim = _victim(tmp_path, "claude_desktop_config")
    monkeypatch.setattr(pipeline, "require_slug", lambda value, _what: value)
    mapping.write_text(
        json.dumps(
            {
                "format": "sp-table/1",
                "types": [
                    {
                        "type_slug": "../../../../밖/claude_desktop_config",
                        "key": {"column": "코드"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(pipeline.Stop, match="작업 폴더 밖"):
        sp_table.convert(mapping, source, folder / "runs" / "r1", root=folder)
    assert victim.read_text(encoding="utf-8") == '{"mcpServers": {}}\n'


def test_허브가_보낸_slug_도_파일_이름이_되기_전에_본다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = {
        "format": pipeline.FORMAT,
        "objects": [{"type_slug": "../../../밖/x", "rows": []}],
        "relations": [],
    }
    monkeypatch.setattr(pipeline, "_export_bundle", lambda *_a: bundle)
    run = tmp_path / "runs" / "허브"
    with pytest.raises(pipeline.Stop, match="slug 꼴이 아닙니다"):
        pipeline.cmd_pull(run, hub=SERVER, hub_token=TOKEN, group="plm")
    assert not run.exists(), "반쯤 쓴 실행 폴더를 남기지 않는다"
    bundle["objects"] = [{"type_slug": "part", "rows": []}]
    pipeline.cmd_pull(run, hub=SERVER, hub_token=TOKEN, group="plm")
    assert (run / "objects" / "001-part.json").is_file()


# --- 5 같은 관계 종류의 절 둘 ------------------------------------------------------------


def test_같은_관계_종류를_출발_타입만_달리_두_번_적어도_선이_사라지지_않는다(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "작업"
    sp_work.init(folder, title="시험")
    source = folder / sp_work.SOURCES / "표.csv"
    source.write_text(
        "과제,모델,시험장\nTK-1,M-1,T100\nTK-1,M-2,T100\nTK-2,M-3,T200\n", encoding="utf-8"
    )
    mapping = folder / sp_work.MAPPING / "표.table.json"

    def edge(slug: str, dst: dict[str, Any], relation: str = "tested_at") -> dict[str, Any]:
        return {"relation": relation, "type_slug": slug, "src": {"key_of": slug}, "dst": dst}

    mapping.write_text(
        json.dumps(
            {
                "format": "sp-table/1",
                "types": [
                    {"type_slug": "task", "key": {"column": "과제"}},
                    {"type_slug": "model", "key": {"column": "모델"}},
                ],
                "relations": [
                    edge("task", {"column": "시험장"}),
                    edge("model", {"column": "시험장"}),
                    # 출발 타입까지 같은 절이 또 — 그것도 덮지 않는다.
                    edge("task", {"value": "T900"}),
                    # 하나뿐인 관계 종류는 예전 이름 그대로.
                    edge("model", {"value": "C1"}, relation="made_by"),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run = folder / "runs" / "r1"
    sp_table.convert(mapping, source, run, root=folder)
    files = {
        one.name: json.loads(one.read_text(encoding="utf-8"))
        for one in sorted((run / "relations").glob("*.json"))
    }
    assert sorted(files) == [
        "made_by.json",
        "tested_at--model.json",
        "tested_at--task-2.json",
        "tested_at--task.json",
    ], sorted(files)
    assert files["tested_at--task.json"]["type_slug"] == "task"
    assert {one["src"] for one in files["tested_at--task.json"]["rows"]} == {"TK-1", "TK-2"}
    assert files["tested_at--model.json"]["type_slug"] == "model"
    assert len(files["tested_at--model.json"]["rows"]) == 3
    assert {one["dst"] for one in files["tested_at--task-2.json"]["rows"]} == {"T900"}
    # 실행으로 읽어도 선이 다 있다 — 검사 · 미리 보기 · 적용이 보내는 묶음이 이것이다.
    loaded = pipeline.load(run)
    assert sorted((one.type_slug, len(one.rows)) for one in loaded.relations) == [
        ("model", 3),
        ("model", 3),
        ("task", 2),
        ("task", 2),
    ]


# --- 2 시트 이름 -----------------------------------------------------------------


def _xlsx(path: Path, sheet: str) -> Path:
    """시트 하나짜리 작은 통합 문서 — 엑셀 화면은 `/` · `\\` 를 시트 이름에 못 쓰게 하지만
    파일(XML)에는 무엇이든 적힌다."""
    rows = [["코드", "이름"], ["A-1", "가"], ["A-2", "나"]]
    data = "".join(
        f'<row r="{number}">'
        + "".join(
            f'<c r="{chr(65 + column)}{number}" t="inlineStr">'
            f"<is><t>{escape(value)}</t></is></c>"
            for column, value in enumerate(row)
        )
        + "</row>"
        for number, row in enumerate(rows, start=1)
    )
    main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f"<workbook {main} {rel}><sheets>"
            f'<sheet name={quoteattr(sheet)} sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet {main}><sheetData>{data}</sheetData></worksheet>",
        )
    return path


EVIL_SHEETS = (
    "/../../../밖",
    "..\\..\\..\\밖",
    "C:\\Users\\x\\밖",
    "C:밖",
    "\\\\host\\share\\밖",
    "a:stream",
    "x\x00y",
    "..",
    " . ",
)


def _flat(name: str) -> bool:
    """한 폴더 안의 이름 하나인가 — POSIX 로도 Windows 로도."""
    return (
        PureWindowsPath(name).name == name
        and PurePosixPath(name).name == name
        and ":" not in name
        and name not in (".", "..")
    )


@pytest.mark.parametrize("sheet", ["/../../../밖", "..\\..\\..\\밖", "C:밖"])
def test_엑셀_시트_이름은_조사_결과를_01_조사_밖에_못_쓴다(tmp_path: Path, sheet: str) -> None:
    out = tmp_path / "작업" / sp_work.SURVEY
    source = _xlsx(tmp_path / "표.xlsx", sheet)
    result, _text = sp_profile.run(source, out_dir=out, sheet=sheet)
    assert result["source"]["sheet"] == sheet, "읽기는 그 시트 그대로"
    written = sorted(one.name for one in out.iterdir())
    assert len(written) == 3 and all(_flat(one) for one in written), written
    assert sorted(one.name for one in tmp_path.iterdir()) == ["작업", "표.xlsx"], (
        "밖에 안 썼다"
    )
    # 변환이 정의 초안을 같은 이름으로 다시 찾는다.
    mapping = tmp_path / "작업" / sp_work.MAPPING / "표.table.json"
    assert sp_table._draft_for(mapping, source, sheet) is not None


def test_시트_조각을_지나쳐도_조사_결과는_01_조사_안만(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 겹이다 — 이름 다듬기(첫 겹)를 빼도 쓰는 자리를 `inside` 가 본다."""
    out = tmp_path / "작업" / sp_work.SURVEY
    sheet = "/../../../밖"
    source = _xlsx(tmp_path / "표.xlsx", sheet)
    monkeypatch.setattr(sp_table, "survey_stem", lambda one, part: f"{one.stem}-{part}")
    with pytest.raises(pipeline.Stop, match="작업 폴더 밖"):
        sp_profile.run(source, out_dir=out, sheet=sheet)
    assert sorted(one.name for one in tmp_path.iterdir()) == ["작업", "표.xlsx"]


def test_CSV_는_시트를_안_보므로_이름에도_싣지_않는다(tmp_path: Path) -> None:
    out = tmp_path / "작업" / sp_work.SURVEY
    source = tmp_path / "표.csv"
    source.write_text("코드,이름\nA-1,가\n", encoding="utf-8")
    sp_profile.run(source, out_dir=out, sheet="/../../../밖")
    assert sorted(one.name for one in out.iterdir()) == [
        "표.draft.json",
        "표.profile.json",
        "표.profile.txt",
    ]


@pytest.mark.parametrize("sheet", EVIL_SHEETS)
def test_시트_조각은_Windows_경로로도_한_폴더_안의_이름이다(sheet: str) -> None:
    stem = sp_table.survey_stem(Path("표.xlsx"), sheet)
    base = PureWindowsPath("C:/작업/01-조사")
    for suffix in (".profile.json", ".draft.json", ".profile.txt"):
        assert _flat(stem + suffix), stem
        assert (base / f"{stem}{suffix}").parent == base
    # 빈칸 · 한글은 그대로 — 예전 이름으로 만든 초안을 다시 찾는다.
    assert sp_table.survey_stem(Path("표.xlsx"), "판매 실적") == "표-판매 실적"


# --- 3 주소 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/objects/../jobs/x/apply",
        "/api/objects/./x",
        "/api/objects/%2e%2e/jobs",
        "/api/objects/%2E%2E/jobs",
        "/api/objects/a%2fb",
        "/api/objects/a%5Cb",
        "/api/objects/a\\..\\b",
        "/api/jobs/x#/apply",
        "/api/jobs/x\n/apply",
        "/api/jobs/x y",
    ],
)
def test_다른_자리를_가리키는_주소는_보내지_않는다(sent: Recorder, path: str) -> None:
    # `#` 은 주소를 잇는 자리(`api_url`)가 먼저 막는다 — 조각도 질의처럼 경로에 못 잇는다.
    with pytest.raises(pipeline.Stop, match=r"보내지 않았습니다|잇지 않습니다"):
        pipeline._get_json(SERVER, TOKEN, path, who="플랫폼")
    assert sent.calls == [], "가짜로 갈아 끼운 SEND 도 이 검사를 지난다"
    with pytest.raises(pipeline.Stop, match="보내지 않았습니다"):
        pipeline._urllib_send("GET", SERVER + path, {}, None)


def test_경로에_질의를_잇지_않고_params_로(sent: Recorder) -> None:
    with pytest.raises(pipeline.Stop, match="params"):
        pipeline._get_json(SERVER, TOKEN, "/api/attachments?owner_id=x", who="플랫폼")
    assert sent.calls == []
    with pytest.raises(pipeline.Stop):
        pipeline._get_json(
            SERVER, TOKEN, "/api/x", params={"owner_field": "a&apply=true"}, who="플랫폼"
        )
    assert sent.calls == [("GET", "/api/x?owner_field=a%26apply%3Dtrue")]


def test_코어_대조의_타입은_slug_꼴만_주소에_넣는다(sent: Recorder) -> None:
    evil = f"x/../../jobs/{uuid.uuid4()}/apply#"
    with pytest.raises(pipeline.Stop, match="slug 꼴이 아닙니다"):
        pipeline.fetch_keys(SERVER, TOKEN, evil)
    assert pipeline.resolve_names(SERVER, TOKEN, evil, ["A-1"]) is None
    assert pipeline.resolve_names(SERVER, TOKEN, "..", ["A-1"]) is None
    assert sent.calls == [], "꼴이 아니면 묻지도 않는다"
    assert pipeline.resolve_names(SERVER, TOKEN, "part", ["A-1"]) is None, "404 는 None"
    assert sent.calls == [("POST", "/api/objects/part/resolve-many")]


def _record(folder: Path, documents: list[dict[str, Any]]) -> Path:
    where = folder / sp_docpush.PUSH_DIR / "20261128-120000"
    where.mkdir(parents=True)
    path = where / sp_docpush.RECORD
    pipeline._write(path, {"at": "20261128-120000", "server": SERVER, "documents": documents})
    return path


def test_문서_대조는_기록의_slug_키를_꼴을_보고_감싸서_잇는다(
    sent: Recorder, tmp_path: Path
) -> None:
    folder = tmp_path / "작업"
    sp_work.init(folder, title="시험")
    object_id = str(uuid.uuid4())
    record = _record(
        folder,
        [
            {"type_slug": "..", "object_id": object_id, "key": "body", "path": "x.md"},
            {"type_slug": "paper", "object_id": object_id, "key": "..", "path": "x.md"},
            {"type_slug": "paper", "object_id": object_id, "key": "b?x=1", "path": "x.md"},
            {"type_slug": "paper", "object_id": "../jobs", "key": "body", "path": "x.md"},
            {"type_slug": "paper", "object_id": object_id, "key": "body", "path": "x.md"},
        ],
    )
    meta = f"/api/objects/paper/{object_id}/documents/body?body=false"
    attached = f"/api/attachments?owner_table=objects&owner_id={object_id}&owner_field=body"
    sent.answers = {meta: (200, {"revision": 0}), attached: (200, [])}
    out = sp_docpush.check(folder, record, server=SERVER, token=TOKEN)
    assert out["counts"]["error"] == 4 and out["counts"]["local_changed"] == 1, out
    # 앞의 넷은 묻지도 않았다. 마지막(꼴이 맞음)만 — 감싼 경로와 params 의 질의로.
    assert sent.calls == [("GET", meta), ("GET", attached)], sent.calls


def test_키트는_SEND_를_한_곳에서만_부른다() -> None:
    """가짜로 갈아 끼운 SEND 를 직접 부르는 자리가 생기면 그 요청은 주소 검사를 건너뛴다."""
    here = Path(pipeline.__file__).resolve().parent
    calls = {
        one.name: one.read_text(encoding="utf-8").count("SEND(")
        for one in sorted(here.glob("sp_*.py"))
    }
    assert calls.pop("sp_pipeline.py") == 1, "_transmit 하나"
    assert not any(calls.values()), calls


# --- 4 다른 주소로 넘기는 답 -------------------------------------------------------------


class _Seen:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None]] = []


def _serve(handle: Any) -> tuple[http.server.ThreadingHTTPServer, int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            handle(self)

        def log_message(self, *_args: Any) -> None:
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _answer(handler: Any, status: int, body: Any, **headers: str) -> None:
    raw = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(raw)


@pytest.fixture
def two_hosts(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, int, _Seen, _Seen]]:
    """(플랫폼 포트, 로그인 쪽 포트, 플랫폼이 받은 것, 로그인 쪽이 받은 것)."""
    monkeypatch.setenv("NO_PROXY", "*")
    platform_seen, login_seen = _Seen(), _Seen()

    def login(handler: Any) -> None:
        login_seen.requests.append((handler.path, handler.headers.get("Authorization")))
        _answer(handler, 200, {"login": True})

    login_server, login_port = _serve(login)

    def platform(handler: Any) -> None:
        platform_seen.requests.append((handler.path, handler.headers.get("Authorization")))
        if handler.path.startswith("/api/old"):
            _answer(handler, 301, {}, Location="/api/new")
        elif handler.path.startswith("/api/new"):
            _answer(handler, 200, {"ok": True})
        else:
            _answer(
                handler, 302, {}, Location=f"http://127.0.0.1:{login_port}/sso/login?state=1"
            )

    platform_server, platform_port = _serve(platform)
    try:
        yield platform_port, login_port, platform_seen, login_seen
    finally:
        for one in (platform_server, login_server):
            one.shutdown()
            one.server_close()


def test_다른_곳으로_넘기는_답은_따라가지_않고_토큰을_주지_않는다(
    two_hosts: tuple[int, int, _Seen, _Seen],
) -> None:
    port, login_port, platform_seen, login_seen = two_hosts
    headers = {"Authorization": f"Bearer {TOKEN}"}
    url = f"http://127.0.0.1:{port}/api/jobs/{uuid.uuid4()}"
    with pytest.raises(pipeline.Stop) as stopped:
        pipeline._urllib_send("GET", url, headers, None)
    said = str(stopped.value)
    assert "다른 주소로 넘겨져 멈췄습니다" in said and "포털 로그인" in said, said
    assert f"127.0.0.1:{login_port}" in said and "state=1" not in said, said
    assert login_seen.requests == [], "넘겨진 곳에는 아무것도(토큰도) 안 갔다"
    assert platform_seen.requests[0][1] == f"Bearer {TOKEN}"

    # 같은 곳 안에서 넘기는 것은 따른다 — 토큰도 그대로.
    status, body = pipeline._urllib_send(
        "GET", f"http://127.0.0.1:{port}/api/old", headers, None
    )
    assert (status, body) == (200, {"ok": True})
    assert platform_seen.requests[-1] == ("/api/new", f"Bearer {TOKEN}")


def test_작업을_기다리다_넘겨지면_받아들여진_작업으로_멈춘다(
    two_hosts: tuple[int, int, _Seen, _Seen], monkeypatch: pytest.MonkeyPatch
) -> None:
    """포털 세션이 기다리는 사이 끝나면 — 거절이 아니라 「끝을 못 봤다」 이고, 까닭을
    싣는다."""
    port, _login_port, _platform_seen, login_seen = two_hosts
    monkeypatch.setattr(pipeline, "SEND", pipeline._urllib_send)

    def stuck(_seconds: float) -> None:
        raise AssertionError("로그인 화면을 작업의 답으로 읽고 기다리기만 했다")

    monkeypatch.setattr(pipeline, "WAIT", stuck)
    job = {"id": str(uuid.uuid4())}
    with pytest.raises(pipeline.Pending, match="다른 주소로 넘겨져"):
        pipeline._wait_job(f"http://127.0.0.1:{port}", TOKEN, job)
    assert login_seen.requests == []
    # 묻기만 하는 자리는 「모른다」.
    assert pipeline.plan_apply_job(f"http://127.0.0.1:{port}", TOKEN, job["id"]) is None
    assert login_seen.requests == []


@pytest.mark.parametrize(
    ("there", "same"),
    [
        ("http://platform.test/x", True),
        ("http://PLATFORM.test:80/x", True),
        ("https://platform.test/x", False),
        ("http://platform.test:8080/x", False),
        ("http://sso.platform.test/x", False),
        ("http://platform.test.evil/x", False),
    ],
)
def test_같은_곳은_scheme_호스트_포트가_같은_것(there: str, same: bool) -> None:
    assert (pipeline._origin(there) == pipeline._origin("http://platform.test/api")) is same
