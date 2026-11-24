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
    assert by_task["varying"] == [{"column": "CS", "keys": 6, "rows": 12}]
    # 과제 아래 모델은 더 잘게 나뉘는 식별자 — 갈리는 게 당연해 따로 적는다.
    assert by_task["finer"] == ["개발모델명"]
    # 날짜 모양이 섞인 열은 식별자 후보가 아니다.
    assert all(one["key"] != "SRA실적일" for one in result["varying"])
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


def test_조사의_말로_전할_요약(tmp_path: Path) -> None:
    result, _ = _profile(tmp_path)
    short = sp_profile.brief(result)
    lines = short.splitlines()
    assert lines[0] == "행 18 · 열 6" and lines[1] == "유일한 열: 개발모델명"
    assert "과제코드 ↔ 개발모델명: 1:N" in lines
    assert "과제코드 기준 갈림: CS(6)" in lines
    for secret in ("TK-0", "SM-X", "과제1"):
        assert secret not in short


PARTS = "품번,품명,중량(kg),재질,등록일,두께,비고"


def _parts(path: Path) -> Path:
    rows = [
        f"P-{n:03d},부품{n},{n * 1.5:.1f},{'알루미늄' if n % 2 else '강철'},"
        f"2026-01-{n % 28 + 1:02d},{n}mm,메모 {n}"
        for n in range(1, 21)
    ]
    return _csv(path, rows, header=PARTS)


def test_조사가_정의_초안을_낸다_식별자_이름_숫자와_단위_날짜_고를_값(tmp_path: Path) -> None:
    """ADR 0095(온톨로지 넣기 · 관리 2번) — 셀 수 있는 것은 키트가 정하고 근거를 단다."""
    source = _parts(tmp_path / "부품표.csv")
    result, text = sp_profile.run(source, out_dir=tmp_path / "01-조사", enum_limit=5)
    found = json.loads(
        (tmp_path / "01-조사" / "부품표.draft.json").read_text(encoding="utf-8")
    )
    assert found["_key_column"] == "품번" and found["_label_column"] == "품명"
    kind = found["types"][0]
    assert kind["key_policy"] == "required" and kind["slug"] == "type_draft"
    props = {one["_column"]: one for one in kind["properties"]}
    assert props["중량(kg)"]["data_type"] == "number" and props["중량(kg)"]["unit"] == "kg"
    assert props["중량(kg)"]["label"] == "중량"
    assert props["두께"]["data_type"] == "number" and props["두께"]["unit"] == "mm"
    assert props["재질"]["data_type"] == "enum" and props["재질"]["enum_options"] is None
    assert "--values" in props["재질"]["_why"]
    assert props["등록일"]["data_type"] == "date"
    assert props["비고"]["data_type"] == "text"
    assert all(one["key"].startswith("col_") for one in kind["properties"])
    assert "## 정의 초안" in text and "중량(kg) → col_3: number · kg" in text

    # 값을 보이면 고를 값이 채워진다.
    shown = sp_profile.draft(
        sp_profile.sp_table.read_table(source),
        result,
        slug="part",
        show_values=True,
        enum_limit=5,
    )
    material = next(one for one in shown["types"][0]["properties"] if one["_column"] == "재질")
    assert material["enum_options"] == ["강철", "알루미늄"]


def test_두_열이_함께_유일하고_N대M_이면_잇는_표_후보다(tmp_path: Path) -> None:
    rows = [
        f"M-{model},P-{part},{(model + part) % 4 + 1}"
        for model in range(1, 6)
        for part in range(1, 6)
        if (model + part) % 3
    ]
    source = _csv(tmp_path / "bom.csv", rows, header="model,part,qty")
    result, _ = sp_profile.run(source, enum_limit=3)
    found = sp_profile.draft(
        sp_profile.sp_table.read_table(source), result, slug="bom", enum_limit=3
    )
    assert found["_bridge"] == {"src": "model", "dst": "part", "edge_fields": ["qty"]}
    assert found["types"][0]["key_policy"] == "optional"
    assert any("잇는 표 후보" in one for one in found["_notes"])


# --------------------------------------------------------------------------
# 엑셀(.xlsx) 그대로 · 정의 초안과 다른 칸(ADR 0095)
# --------------------------------------------------------------------------

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _xlsx(path: Path, sheets: list[tuple[str, str, list[list[Any]]]]) -> Path:
    """표준 라이브러리로 만든 작은 통합 문서. 셀은 (종류, 값, 서식 번호) — 종류 s(공유 글) ·
    inline · n · b, 서식 1 은 날짜(14), 2 는 날짜와 시각(사용자 서식). None 은 빈 칸."""
    import zipfile

    shared: list[str] = []
    members: dict[str, str] = {}
    for index, (_name, _state, rows) in enumerate(sheets, start=1):
        lines = []
        for number, row in enumerate(rows, start=1):
            cells = []
            for column, cell in enumerate(row):
                if cell is None:
                    continue
                kind, value, style = cell if len(cell) == 3 else (*cell, 0)
                ref = f"{chr(65 + column)}{number}"
                if kind == "s":
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
                elif kind == "inline":
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
                elif kind == "b":
                    cells.append(f'<c r="{ref}" t="b"><v>{value}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            lines.append(f'<row r="{number}">{"".join(cells)}</row>')
        members[f"xl/worksheets/sheet{index}.xml"] = (
            f"<worksheet {_NS}><sheetData>{''.join(lines)}</sheetData></worksheet>"
        )
    members["xl/workbook.xml"] = (
        f"<workbook {_NS} {_RNS}><sheets>"
        + "".join(
            f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"'
            + (f' state="{state}"' if state else "")
            + "/>"
            for i, (name, state, _rows) in enumerate(sheets, start=1)
        )
        + "</sheets></workbook>"
    )
    members["xl/_rels/workbook.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" Type="x"/>'
            for i in range(1, len(sheets) + 1)
        )
        + "</Relationships>"
    )
    members["xl/styles.xml"] = (
        f"<styleSheet {_NS}>"
        '<numFmts count="1">'
        '<numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd hh:mm"/></numFmts>'
        '<cellXfs count="3">'
        '<xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs>'
        "</styleSheet>"
    )
    members["xl/sharedStrings.xml"] = (
        f"<sst {_NS}>" + "".join(f"<si><t>{one}</t></si>" for one in shared) + "</sst>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


def _part_rows() -> list[list[Any]]:
    """1행은 비고(제목 줄 자리) 2행이 머리글. 45658 = 2025-01-01."""
    rows: list[list[Any]] = [
        [],
        [("s", "품번"), ("s", "품명"), ("s", "중량(kg)"), ("s", "등록일"), ("s", "사용")],
    ]
    for n in range(1, 21):
        rows.append(
            [
                ("s", f"P-{n:03d}"),
                ("inline", f"부품{n}"),
                ("n", "0.30000000000000004" if n == 1 else str(n * 1.5)),
                ("n", str(45658 + n), 1),
                ("b", str(n % 2)),
            ]
        )
    rows.insert(5, [None, None])
    rows.append([("n", "45658.5", 2)])
    return rows


def test_엑셀을_그대로_읽는다_날짜_숫자_글_시트(tmp_path: Path) -> None:
    path = _xlsx(
        tmp_path / "부품표.xlsx",
        [
            ("숨김", "hidden", [[("s", "x")]]),
            ("부품", "", _part_rows()),
            ("기타", "", [[("s", "코드")], [("s", "A")]]),
        ],
    )
    table = sp_profile.sp_table.read_table(path)
    assert table.sheet == "부품", "첫 **보이는** 시트"
    assert table.header == ["품번", "품명", "중량(kg)", "등록일", "사용"]
    number, first = table.rows[0]
    assert number == 3, "행 번호는 시트의 행 번호"
    assert first == {
        "품번": "P-001",
        "품명": "부품1",
        "중량(kg)": "0.3",
        "등록일": "2025-01-02",
        "사용": "TRUE",
    }
    assert table.rows[1][1]["중량(kg)"] == "3" and table.rows[1][1]["사용"] == "FALSE"
    assert table.blank_rows == 1
    assert table.rows[-1][1]["품번"] == "2025-01-01 12:00:00", "사용자 서식의 날짜와 시각"
    assert sp_profile.sp_table.read_table(path, sheet="기타").rows[0][1] == {"코드": "A"}
    with pytest.raises(sp_profile.sp_table.Stop, match="있는 시트: 숨김, 부품, 기타"):
        sp_profile.sp_table.read_table(path, sheet="없음")
    old = tmp_path / "옛.xls"
    old.write_bytes(b"x")
    with pytest.raises(sp_profile.sp_table.Stop, match=r"옛 엑셀\(.xls\)"):
        sp_profile.sp_table.read_table(old)


def test_엑셀_시트를_조사하면_결과_파일에_시트가_붙는다(tmp_path: Path) -> None:
    rows = _part_rows()[:-1]
    path = _xlsx(tmp_path / "부품표.xlsx", [("부품", "", rows)])
    result, text = sp_profile.run(
        path, out_dir=tmp_path / "01-조사", enum_limit=3, sheet="부품"
    )
    assert result["source"]["sheet"] == "부품" and "부품표.xlsx 「부품」" in text
    found = json.loads(
        (tmp_path / "01-조사" / "부품표-부품.draft.json").read_text(encoding="utf-8")
    )
    kind = found["types"][0]
    assert kind["label"] == "부품" and kind["key_policy"] == "required"
    props = {one["_column"]: one for one in kind["properties"]}
    assert props["중량(kg)"]["data_type"] == "number" and props["중량(kg)"]["unit"] == "kg"
    assert props["등록일"]["data_type"] == "date"
    assert props["사용"]["data_type"] == "bool"


def test_정의가_초안과_다른_칸은_옮기기_보고서가_알린다(tmp_path: Path) -> None:
    """초안이 센 종류 · 단위를 정의가 다르게 적으면 — 막지 않고 보고서에."""
    work = tmp_path / "work"
    (work / "00-원천").mkdir(parents=True)
    source = _parts(work / "00-원천" / "부품표.csv")
    sp_profile.run(source, out_dir=work / "01-조사", enum_limit=5)
    (work / "02-정의").mkdir()
    (work / "02-정의" / "ontology.json").write_text(
        json.dumps(
            {
                "types": [
                    {
                        "slug": "part",
                        "label": "부품",
                        "key_policy": "required",
                        "properties": [
                            {"key": "weight", "label": "중량", "data_type": "text"},
                            {
                                "key": "thick",
                                "label": "두께",
                                "data_type": "number",
                                "unit": "cm",
                            },
                            {"key": "registered", "label": "등록일", "data_type": "date"},
                            {"key": "material", "label": "재질", "data_type": "text"},
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work / "03-대응").mkdir()
    mapping = work / "03-대응" / "부품표.table.json"
    mapping.write_text(
        json.dumps(
            {
                "format": "sp-table/1",
                "title": "부품",
                "ontology": "../02-정의/ontology.json",
                "types": [
                    {
                        "type_slug": "part",
                        "key": {"column": "품번"},
                        "label": {"column": "품명"},
                        "fields": {
                            "weight": {"column": "중량(kg)"},
                            "thick": {"column": "두께"},
                            "registered": {"column": "등록일", "date": True},
                            "material": {"column": "재질"},
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _ok, report = sp_profile.sp_table.convert(mapping, source, work / "runs" / "first")
    assert "정의 초안과 다른 칸 2개" in report, report
    assert "「중량(kg)」 → part.weight: 초안은 숫자(" in report and "정의는 글" in report
    assert "「두께」 → part.thick: 초안의 단위 mm · 정의의 단위 cm" in report
    assert "재질" not in report.split("정의 초안과 다른 칸")[1], "고를 값 · 글은 사람이 정한다"
