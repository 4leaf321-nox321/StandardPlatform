"""표 → 실행 폴더(`pipeline/sp_table.py`) — **규칙으로 옮기고, 짐작하지 않는다.**

값 · 코드는 전부 지어낸 것이다. 모양(한 행에 프로젝트 · 과제 · 모델, 이름에 박힌 조각)만
실제 원천을 따른다 — 사내 데이터는 저장소에 두지 않는다.
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

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "pipeline"
SERVER = "http://platform.test"


def _load() -> Any:
    # sp_table 은 옆 파일 sp_pipeline 을 import 한다 — 스크립트로 돌 때처럼 폴더를 길에 둔다.
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    spec = importlib.util.spec_from_file_location(
        "sp_table_under_test", PIPELINE_DIR / "sp_table.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


table = _load()

HEADER = (
    "프로젝트명,과제코드,과제명,과제상태,SRA실적일,개발모델유형,개발모델명,"
    "CS인증대상과제,시험Plan,Test진행상태,DESCRIPTION"
)
ROWS = [
    # 한 과제에 Basic + RC — CS 인증 대상이 행마다 다르다(모델 칸이라 갈려도 된다).
    "P-알파,tk-001,알파,정상완료,2026-01-05,Basic,SM-X100A_KOR_SKT,임원협의,t100,완료,",
    "P-알파,TK-001,알파,정상완료,2026-01-05,RC,SM-X100A_D1_KOR_KTF,비대상,T100,진행,설명",
    "P-알파,TK-002,베타_12,정상완료,Skip,파생,SM-X100A_EUR_12_MEA,비대상,T100,완료,",
    "",
    "P-베타,TK-003,감마_C2,정상진행,-,파생,SM-X200B_NA_C2_ATT,비대상,T100,진행,",
    # Skip 인데 리비전이 없다 — 검증 위반.
    "P-베타,TK-004,델타,정상완료,Skip,파생,SM-X200B_NA_ATT,비대상,T100,완료,",
    # 과제명 끝(_RR)과 리비전 조각(QQ)이 다르다 — 조각이 밀렸다.
    "P-베타,TK-005,엡실론_RR,정상완료,2026.02.03,파생,SM-X200B_CHN_QQ_CMC,비대상,T100,완료,",
    # 사전에 없는 지역 · 날짜가 아닌 날짜.
    "P-베타,TK-006,제타,지연완료,2026-13-01,MU,OL-X300_ZZZ_SP2,비대상,T100,완료,",
]


def _mapping(slugs: dict[str, str], *, ontology: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "format": "sp-table/1",
        "title": "시험 표",
        "workspace_slug": "cae",
        "dictionaries": {"region": ["KOR", "NA", "EUR", "CHN", "MEA", "ALL"]},
        "parsers": {
            "model_name": {
                "column": "개발모델명",
                "separator": "_",
                "rare_below": 2,
                "slots": [
                    {"name": "base", "kinds": {"SM": "^SM-", "OL": "^OL-"}},
                    {"name": "marker", "optional": True, "values": ["D1", "D2"]},
                    {"name": "region", "dictionary": "region"},
                    {
                        "name": "revision",
                        "optional": True,
                        "kinds": {
                            "OS 버전": "^[0-9]{2}$",
                            "차수": "^[A-Z][0-9]$",
                            "기호": "^[A-Z]{2}$",
                        },
                    },
                    {"name": "carrier"},
                ],
                "checks": [
                    {"slot": "revision", "suffix_of": "과제명", "joiner": "_"},
                    {"require": "revision", "when_column": "SRA실적일", "in": ["Skip"]},
                ],
            }
        },
        "types": [
            {
                "type_slug": slugs["project"],
                "key": {"column": "프로젝트명"},
                "label": {"column": "프로젝트명"},
            },
            {
                "type_slug": slugs["site"],
                "key": {"column": "시험Plan", "upper": True},
            },
            {
                "type_slug": slugs["task"],
                "key": {"column": "과제코드", "upper": True},
                "label": {"column": "과제명"},
                "fields": {
                    "project": {"key_of": slugs["project"]},
                    "task_status": {"column": "과제상태"},
                    "sra_on": {"column": "SRA실적일", "date": True, "blank": ["Skip", "-"]},
                    "sra_kind": {
                        "column": "SRA실적일",
                        "map": {"<date>": "실적", "Skip": "면제", "-": "미도래"},
                    },
                    "test_site": {"key_of": slugs["site"]},
                },
            },
            {
                "type_slug": slugs["model"],
                "key": {"column": "개발모델명"},
                "fields": {
                    "task": {"key_of": slugs["task"]},
                    "model_kind": {"column": "개발모델유형"},
                    "cs_target": {"column": "CS인증대상과제"},
                    "test_progress": {"column": "Test진행상태"},
                    "description": {"column": "DESCRIPTION"},
                    "series": {"parser": "model_name", "slot": "base", "part": "kind"},
                    "base_code": {"parser": "model_name", "slot": "base"},
                    "rc_marker": {"parser": "model_name", "slot": "marker"},
                    "region": {"parser": "model_name", "slot": "region"},
                    "model_revision": {"parser": "model_name", "slot": "revision"},
                    "model_revision_kind": {
                        "parser": "model_name",
                        "slot": "revision",
                        "part": "kind",
                    },
                    "carrier": {"parser": "model_name", "slot": "carrier"},
                },
            },
        ],
    }
    if ontology:
        body["ontology"] = ontology
    return body


SLUGS = {"project": "t_project", "site": "t_site", "task": "t_task", "model": "t_model"}


def _files(
    tmp_path: Path,
    mapping: dict[str, Any],
    rows: list[str] | None = None,
    *,
    encoding: str = "utf-8-sig",
) -> tuple[Path, Path]:
    source = tmp_path / "표.csv"
    source.write_bytes("\n".join([HEADER, *(ROWS if rows is None else rows)]).encode(encoding))
    mapping_path = tmp_path / "표.table.json"
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping_path, source


def _objects(run: Path, slug: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for file in sorted((run / "objects").glob(f"{slug}*.json")):
        for row in json.loads(file.read_text(encoding="utf-8"))["rows"]:
            rows[row["key"]] = row
    return rows


def test_한_행을_타입마다_나누고_이름의_조각을_칸에_넣는다(tmp_path: Path) -> None:
    mapping_path, source = _files(tmp_path, _mapping(SLUGS))
    run = tmp_path / "run"
    ok, report = table.convert(mapping_path, source, run)
    assert ok is True, report

    manifest = json.loads((run / "bundle.json").read_text(encoding="utf-8"))
    # 참조되는 것이 먼저 — 대응 파일에 적은 차례가 넣는 차례다.
    assert manifest["objects_order"] == list(SLUGS.values())
    assert manifest["sources"][0]["rows"] == 7
    assert "task ← t_task 의 식별자" in manifest["notes"]

    assert set(_objects(run, "t_project")) == {"P-알파", "P-베타"}
    # 대문자로 맞춘 식별자 — 원천의 tk-001 · TK-001 · t100 · T100 이 하나씩이 된다.
    assert set(_objects(run, "t_site")) == {"T100"}
    tasks = _objects(run, "t_task")
    assert len(tasks) == 6
    assert tasks["TK-001"]["project"] == "P-알파"
    assert tasks["TK-001"]["_note"] == "2행에서 모음"
    assert tasks["TK-001"]["_source"] == {"file": "표.csv", "row": 2}
    assert tasks["TK-002"]["sra_on"] is None and tasks["TK-002"]["sra_kind"] == "면제"
    assert tasks["TK-003"]["sra_kind"] == "미도래"
    assert tasks["TK-005"]["sra_on"] == "2026-02-03" and tasks["TK-005"]["sra_kind"] == "실적"

    models = _objects(run, "t_model")
    assert len(models) == 7
    basic = models["SM-X100A_KOR_SKT"]
    assert basic["label"] == "SM-X100A_KOR_SKT" and basic["task"] == "TK-001"
    assert (basic["region"], basic["carrier"], basic["rc_marker"]) == ("KOR", "SKT", None)
    assert basic["description"] is None
    rc = models["SM-X100A_D1_KOR_KTF"]
    # 마커가 지역을 한 칸 민다 — 위치로 자르면 D1 이 지역이 된다.
    assert (rc["rc_marker"], rc["region"], rc["carrier"]) == ("D1", "KOR", "KTF")
    assert rc["cs_target"] == "비대상" and basic["cs_target"] == "임원협의"
    os_version = models["SM-X100A_EUR_12_MEA"]
    # 마지막 자리의 MEA 는 지역 사전에 있어도 사업자다.
    assert os_version["region"] == "EUR" and os_version["carrier"] == "MEA"
    assert (os_version["model_revision"], os_version["model_revision_kind"]) == (
        "12",
        "OS 버전",
    )
    assert models["SM-X200B_NA_C2_ATT"]["model_revision_kind"] == "차수"
    assert models["SM-X100A_KOR_SKT"]["series"] == "SM"
    assert models["SM-X100A_KOR_SKT"]["base_code"] == "SM-X100A"

    # 못 읽은 모델도 들어간다 — 조각 칸만 비운다.
    for broken in ("SM-X200B_NA_ATT", "SM-X200B_CHN_QQ_CMC", "OL-X300_ZZZ_SP2"):
        assert models[broken]["region"] is None and models[broken]["carrier"] is None
        assert models[broken]["task"]
    assert json.loads((run / "unresolved.json").read_text(encoding="utf-8")) == []


def test_보고서는_값을_가린_패턴으로만_적는다(tmp_path: Path) -> None:
    mapping_path, source = _files(tmp_path, _mapping(SLUGS))
    run = tmp_path / "run"
    _, report = table.convert(mapping_path, source, run)
    assert (run / "table-report.txt").read_text(encoding="utf-8").strip() == report.strip()

    assert "원천: 7행(빈 행 1)" in report
    assert "[해석 model_name] 개발모델명 7행 · 읽음 4 · 못 읽음 3" in report
    assert "못 나눔(조각 3개) 1: AA-A999_AAA_AA9 1" in report
    assert "검증: revision 이(가) 과제명 의 끝과 다름 1: AA-A999A_AAA_AA_AAA 1" in report
    assert "검증: SRA실적일 이(가) Skip 인데 revision 없음 1: AA-A999A_AA_AAA 1" in report
    assert "OS 버전 1" in report and "차수 1" in report
    assert "칸 sra_on — 날짜가 아닌 값 1: 9999-99-99 1" in report
    assert "칸 sra_kind — 대응 없는 값 1: 9999-99-99 1" in report
    # 원천의 값은 한 글자도 없다.
    for secret in ("SM-X", "OL-X", "TK-00", "알파", "P-베타", "T100", "SKT", "2026"):
        assert secret not in report


def test_같은_식별자인데_값이_갈리면_짐작하지_않고_미해결로(tmp_path: Path) -> None:
    rows = [
        *ROWS,
        "P-베타,TK-001,알파,지연완료,2026-01-05,파생,SM-X100A_KOR_LGT,비대상,T100,완료,",
    ]
    mapping_path, source = _files(tmp_path, _mapping(SLUGS), rows)
    run = tmp_path / "run"
    ok, report = table.convert(mapping_path, source, run)
    assert ok is False
    assert "값이 갈린 칸 2 (project 1 · task_status 1)" in report

    task = _objects(run, "t_task")["TK-001"]
    # 갈린 칸은 안 보낸다 — 플랫폼 값을 건드리지 않는다.
    assert "task_status" not in task and "project" not in task
    unresolved = json.loads((run / "unresolved.json").read_text(encoding="utf-8"))
    status = next(one for one in unresolved if one["what"].endswith("task_status"))
    assert status["options"] == ["정상완료", "지연완료"]
    assert status["_source"]["rows"] == [[2, 3], [10]]

    ok, text = table.pipeline.cmd_validate(run)
    assert ok is False and "미해결 2건" in text


def test_CP949_로_저장한_표도_읽는다(tmp_path: Path) -> None:
    mapping_path, source = _files(tmp_path, _mapping(SLUGS), encoding="cp949")
    ok, report = table.convert(mapping_path, source, tmp_path / "run")
    assert ok is True
    assert "cp949" in report


def test_한_파일의_행_수를_넘으면_나눠_쓴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(table.pipeline, "MAX_ROWS", 3)
    mapping_path, source = _files(tmp_path, _mapping(SLUGS))
    run = tmp_path / "run"
    table.convert(mapping_path, source, run)
    names = sorted(one.name for one in (run / "objects").glob("t_model*.json"))
    assert names == ["t_model-001.json", "t_model-002.json", "t_model-003.json"]
    assert len(_objects(run, "t_model")) == 7
    assert (run / "objects" / "t_project.json").exists()


def test_대응_파일이_틀리면_무엇이_틀렸는지_말하고_멈춘다(tmp_path: Path) -> None:
    broken = _mapping(SLUGS)
    broken["types"][2]["fields"]["task_status"] = {"column": "과제 상태"}
    mapping_path, source = _files(tmp_path, broken)
    with pytest.raises(table.Stop, match="열 '과제 상태' 가 원천에 없습니다"):
        table.convert(mapping_path, source, tmp_path / "run")
    assert not (tmp_path / "run").exists()

    broken = _mapping(SLUGS)
    broken["types"][3]["fields"]["region"] = {"parser": "model_name", "slot": "area"}
    mapping_path, source = _files(tmp_path, broken)
    with pytest.raises(table.Stop, match="조각 'area' 가 해석기에 없습니다"):
        table.convert(mapping_path, source, tmp_path / "run")


def test_두_갈래로_읽히는_이름은_고르지_않는다() -> None:
    parser = table.Parser(
        "code",
        {
            "column": "코드",
            "slots": [
                {"name": "head"},
                {"name": "a", "optional": True, "pattern": "^X"},
                {"name": "b", "optional": True, "pattern": "^X"},
                {"name": "tail"},
            ],
        },
        {},
        ["코드"],
    )
    assert parser.parse(2, {"코드": "H_X1_T"}).failure == "두 갈래로 읽힘"
    assert parser.parse(3, {"코드": "H_X1_X2_T"}).slots["b"] == ("X2", None)
    assert parser.parse(4, {"코드": "H"}).failure == "못 나눔(조각 1개)"


# --------------------------------------------------------------------------
# 만든 실행 폴더가 플랫폼에 그대로 들어가나
# --------------------------------------------------------------------------


@pytest.fixture
def platform(client: TestClient) -> Iterator[None]:
    def send(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        got = client.request(method, path, content=body, headers=headers)
        return got.status_code, got.json()

    before = table.pipeline.SEND
    table.pipeline.SEND = send
    try:
        yield
    finally:
        table.pipeline.SEND = before


def _ontology(slugs: dict[str, str]) -> dict[str, Any]:
    return {
        "groups": [],
        "types": [
            {"slug": slugs["project"], "label": "프로젝트", "key_policy": "required"},
            {"slug": slugs["site"], "label": "시험 주체", "key_policy": "required"},
            {
                "slug": slugs["task"],
                "label": "과제",
                "key_policy": "required",
                "properties": [
                    {
                        "key": "project",
                        "label": "프로젝트",
                        "data_type": "object_ref",
                        "ref_type_slug": slugs["project"],
                    },
                    {
                        "key": "task_status",
                        "label": "과제상태",
                        "data_type": "enum",
                        "enum_options": ["정상진행", "지연진행", "정상완료", "지연완료"],
                    },
                    {"key": "sra_on", "label": "SRA 실적일", "data_type": "date"},
                    {
                        "key": "sra_kind",
                        "label": "SRA 구분",
                        "data_type": "enum",
                        "enum_options": ["실적", "면제", "미도래"],
                    },
                    {
                        "key": "test_site",
                        "label": "시험 주체",
                        "data_type": "object_ref",
                        "ref_type_slug": slugs["site"],
                    },
                ],
            },
            {
                "slug": slugs["model"],
                "label": "개발모델",
                "key_policy": "required",
                "properties": [
                    {
                        "key": "task",
                        "label": "과제",
                        "data_type": "object_ref",
                        "ref_type_slug": slugs["task"],
                    },
                    {
                        "key": "model_kind",
                        "label": "유형",
                        "data_type": "enum",
                        "enum_options": ["Basic", "RC", "MU", "파생"],
                    },
                    {"key": "cs_target", "label": "CS", "data_type": "text"},
                    {"key": "test_progress", "label": "진행", "data_type": "text"},
                    {
                        "key": "series",
                        "label": "계열",
                        "data_type": "enum",
                        "enum_options": ["SM", "OL"],
                    },
                    {"key": "base_code", "label": "기본", "data_type": "text"},
                    {"key": "rc_marker", "label": "마커", "data_type": "text"},
                    {"key": "region", "label": "지역", "data_type": "text"},
                    {"key": "model_revision", "label": "리비전", "data_type": "text"},
                    {
                        "key": "model_revision_kind",
                        "label": "리비전 종류",
                        "data_type": "text",
                    },
                    {"key": "carrier", "label": "사업자", "data_type": "text"},
                ],
            },
        ],
        "relation_types": [],
    }


def test_만든_실행_폴더가_미리_보기와_적용을_통과한다(
    client: TestClient, admin: Signed, platform: None, tmp_path: Path
) -> None:
    tag = uuid.uuid4().hex[:6]
    slugs = {name: f"{slug}_{tag}" for name, slug in SLUGS.items()}
    (tmp_path / "ontology.json").write_text(
        json.dumps(_ontology(slugs), ensure_ascii=False), encoding="utf-8"
    )
    mapping = _mapping(slugs, ontology="ontology.json")
    mapping["workspace_slug"] = admin.workspace
    mapping_path, source = _files(tmp_path, mapping)
    run = tmp_path / "run"
    ok, report = table.convert(mapping_path, source, run)
    assert ok is True, report

    made = client.post(
        "/api/auth/tokens",
        json={"name": f"table-{tag}", "scopes": ["read", "objects:write", "ontology:write"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    token = made.json()["token"]

    ok, text = table.pipeline.cmd_preview(run, server=SERVER, token=token)
    assert ok is True, text
    ok, text = table.pipeline.cmd_apply(run, server=SERVER, token=token)
    assert ok is True, text

    listed = client.get(
        f"/api/objects/{slugs['model']}", params={"limit": 50}, headers=admin.headers
    )
    assert listed.status_code == 200, listed.text
    models = {one["key"]: one for one in listed.json()["items"]}
    assert len(models) == 7
    rc = models["SM-X100A_D1_KOR_KTF"]
    assert rc["description"] == "설명"
    assert rc["properties"]["rc_marker"] == "D1" and rc["properties"]["region"] == "KOR"
    # 참조 칸은 식별자로 적었고 플랫폼이 객체로 풀었다.
    assert rc["properties"]["task"]
