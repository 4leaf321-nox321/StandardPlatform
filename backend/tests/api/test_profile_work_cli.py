"""원천 조사(`sp_profile.py`) · 작업 폴더(`sp_work.py`) — **세는 것은 코드가, 진행은 폴더가.**

값은 전부 지어낸 것이다.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

# 모듈 이름으로 집어 온다 — sp_profile 이 쓰는 sp_pipeline 과 **같은 것**을 갈아 끼워야 한다.
sp_pipeline: Any = importlib.import_module("sp_pipeline")
sp_profile: Any = importlib.import_module("sp_profile")
sp_work: Any = importlib.import_module("sp_work")

HEADER = "프로젝트명,과제코드,과제명,CS,개발모델명,SRA실적일"


def _rows() -> list[str]:
    """과제 12개. 짝수 과제는 모델이 둘(Basic + RC)이고 CS 가 행마다 갈린다."""
    rows = []
    for number in range(1, 13):
        project = f"P{number % 3}"
        task = f"TK-{number:03d}"
        sra = "2026-01-05" if number % 3 else ("Skip" if number % 2 else "-")
        rows.append(f"{project},{task},과제{number},비대상,SM-X{number:03d}A_KOR_SKT,{sra}")
        if number % 2 == 0:
            rows.append(
                f"{project},{task},과제{number},임원협의,SM-X{number:03d}A_D1_KOR_KTF,{sra}"
            )
    return rows


def _csv(path: Path, rows: list[str], header: str = HEADER) -> Path:
    path.write_text("\n".join([header, *rows]), encoding="utf-8-sig")
    return path


def _profile(tmp_path: Path, **options: Any) -> tuple[dict[str, Any], str]:
    source = _csv(tmp_path / "표.csv", _rows())
    table = sp_profile.sp_table.read_table(source)
    result = sp_profile.profile(table, enum_limit=5, **options)
    return result, sp_profile.render(result)


def test_행_단위와_몇_대_몇과_갈리는_열을_센다(tmp_path: Path) -> None:
    result, _ = _profile(tmp_path)
    assert result["source"]["rows"] == 18
    assert result["unique"]["columns"] == ["개발모델명"]

    pairs = {(one["a"], one["b"]): one for one in result["cardinality"]}
    assert pairs[("과제코드", "과제명")]["kind"] == "1:1"
    task_model = pairs[("과제코드", "개발모델명")]
    assert task_model["kind"] == "1:N"
    assert task_model["a_to_b"]["max"] == 2 and task_model["b_to_a"]["max"] == 1

    by_task = next(one for one in result["varying"] if one["key"] == "과제코드")
    # CS 는 과제가 아니라 모델의 칸이다 — 짝수 과제 6개 · 12행에서 갈린다.
    assert by_task["varying"] == [
        {"column": "CS", "keys": 6, "rows": 12},
        {"column": "개발모델명", "keys": 6, "rows": 12},
    ]
    assert "과제명" in by_task["steady"] and "SRA실적일" in by_task["steady"]


def test_이름의_조각을_자리마다_세고_모양이_어느_자리에_나오나_보인다(tmp_path: Path) -> None:
    result, _ = _profile(tmp_path)
    split = next(one for one in result["splits"] if one["column"] == "개발모델명")
    assert split["pieces"] == {"3": 12, "4": 6}
    # D1 은 2번 자리에만, 지역 · 사업자 모양(AAA)은 마커가 미는 만큼 여러 자리에.
    assert split["shapes"]["A9"] == {"2": 6}
    assert split["shapes"]["AAA"] == {"2": 12, "3": 18, "4": 6, "마지막": 18}


def test_값은_기본으로_가리고_요청하면_보인다(tmp_path: Path) -> None:
    hidden, text = _profile(tmp_path)
    for secret in ("TK-0", "SM-X", "KOR", "과제1", "Skip", "2026"):
        assert secret not in text
    dates = next(one for one in hidden["dates"] if one["column"] == "SRA실적일")
    assert dates["not_dates"] == [["-", 4], ["AAAA", 2]]
    assert next(one for one in hidden["enums"] if one["column"] == "CS")["values"] == [
        ["가가가", 12],
        ["가가가가", 6],
    ]

    shown, text = _profile(tmp_path, show_values=True)
    assert next(one for one in shown["enums"] if one["column"] == "CS")["values"] == [
        ["비대상", 12],
        ["임원협의", 6],
    ]
    assert "Skip" in text


def test_코어_대조는_그대로_대소문자_줄여씀_더붙음_없음을_가른다(tmp_path: Path) -> None:
    source = _csv(
        tmp_path / "그룹.csv",
        ["SM-A1_KOR_SKT", "sm-a1_kor_skt", "SM-A1", "SM-A1_KOR_SKT_X9", "OL-Z", "OL-Z"],
        header="모델",
    )
    (tmp_path / "keys.txt").write_text("SM-A1_KOR_SKT\nSM-B2_NA_ATT\n", encoding="utf-8")
    result, text = sp_profile.run(
        source, matches=[f"모델=@{tmp_path / 'keys.txt'}"], out_dir=tmp_path / "out"
    )
    core = result["core"][0]
    assert (core["exact"], core["case"], core["shorter"], core["longer"], core["none"]) == (
        1,
        1,
        1,
        1,
        2,
    )
    assert core["none_top"] == [["AA-A", 2]]
    assert "그대로 1(16.7%)" in text
    assert (tmp_path / "out" / "그룹.profile.txt").exists()
    assert json.loads((tmp_path / "out" / "그룹.profile.json").read_text(encoding="utf-8"))


def test_코어_식별자는_플랫폼에서_쪽마다_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [f"K-{number}" for number in range(5)]
    asked: list[str] = []

    def send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
        asked.append(url)
        offset = int(url.rsplit("offset=", 1)[1])
        page = [{"key": key} for key in keys[offset : offset + 2]]
        return 200, {"items": page, "total": len(keys)}

    monkeypatch.setattr(sp_pipeline, "SEND", send)
    assert sp_profile.fetch_keys("http://p", "spt_x", "plm_model") == set(keys)
    assert len(asked) == 3 and asked[0].startswith("http://p/api/objects/plm_model?")


# --------------------------------------------------------------------------
# 작업 폴더
# --------------------------------------------------------------------------


def _ontology(folder: Path, marker: str = "") -> None:
    body = {
        "groups": [],
        "types": [{"slug": "t_task", "label": f"과제{marker}", "key_policy": "required"}],
        "relation_types": [],
    }
    (folder / sp_work.ONTOLOGY).write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8"
    )


def test_작업_폴더가_다음_할_일을_말한다(tmp_path: Path) -> None:
    folder = tmp_path / "해석팀"
    sp_work.init(folder, title="해석팀 대장", group="cae")
    with pytest.raises(sp_work.Stop, match="이미 작업 폴더"):
        sp_work.init(folder, title="다시")

    def nexts() -> str:
        return "\n".join(sp_work.status(folder)["next"])

    assert "원천 파일을 00-원천/" in nexts()
    _csv(folder / sp_work.SOURCES / "대장.csv", _rows())
    (folder / sp_work.SOURCES / "보고서.pdf").write_bytes(b"%PDF")
    assert "대장.csv: 조사" in nexts() and "정의 초안" in nexts()

    sp_profile.run(folder / sp_work.SOURCES / "대장.csv", out_dir=folder / sp_work.SURVEY)
    _ontology(folder)
    state = sp_work.status(folder)
    assert state["sources"][0] == {
        "name": "대장.csv",
        "kind": "표",
        "profiled": True,
        "mapping": None,
    }
    assert "정의 확정 대기" in nexts()

    with pytest.raises(sp_work.Stop, match="topic"):
        sp_work.record(folder, topic=" ", decision="확정")
    sp_work.record(
        folder,
        topic="정의 확정",
        decision="과제 타입",
        decided_by="온톨로지 담당",
        confirms_ontology=True,
    )
    assert sp_work.status(folder)["ontology"]["confirmed"] is True
    assert "대장.csv: 대응 파일" in nexts() and "보고서.pdf: 추출" in nexts()
    log = (folder / sp_work.DECISIONS).read_text(encoding="utf-8")
    assert "- 결정: 과제 타입" in log and "- 정한 사람: 온톨로지 담당" in log

    # 확정 뒤 정의가 바뀌면 다시 확정받아야 한다.
    _ontology(folder, marker="!")
    assert sp_work.status(folder)["ontology"]["changed_after_confirm"] is True
    assert "확정 뒤 바뀌었다" in nexts()


def test_실행_폴더의_상태와_지난_실행을_덮지_않는_이름(tmp_path: Path) -> None:
    folder = tmp_path / "w"
    sp_work.init(folder, title="w")
    first = sp_work.new_run(folder, "대장 / 1차")
    assert first.name.endswith("-대장-1차")
    sp_pipeline.cmd_init(first)
    second = sp_work.new_run(folder, "대장 / 1차")
    assert second.name == f"{first.name}-2"

    rows = {
        "type_slug": "t_task",
        "workspace_slug": "cae",
        "rows": [{"key": "A", "label": "a"}],
    }
    (first / "objects" / "t_task.json").write_text(json.dumps(rows), encoding="utf-8")
    (first / "unresolved.json").write_text('[{"what": "?"}]', encoding="utf-8")
    run = sp_work.status(folder)["runs"][0]
    assert (run["objects"], run["unresolved"], run["preview"], run["applied"]) == (
        1,
        1,
        "없음",
        False,
    )
    assert "미해결 1건" in "\n".join(sp_work.status(folder)["next"])

    with pytest.raises(sp_work.Stop, match="밖의 경로"):
        sp_work.inside(folder, "../다른")
    assert "작업: w" in sp_work.render(sp_work.status(folder))
