"""Word(.docx) → 그림 자리가 든 마크다운 초안 + 그림 파일(`sp_docx`) — **그림이 어디
있었는지를 잃지 않는가.**

시험 안에서 zipfile 로 최소 .docx 를 만든다(본문 · 관계 · 스타일 · 번호 · 그림). Word 가 만든
파일을 저장소에 두지 않는다 — 무엇을 보는지가 이 파일 안에 다 보이게.

EMF → PNG 는 Windows 의 PowerShell(GDI+)이 있을 때만 돈다(WSL 이면 `/mnt/c/…`). 없으면
건너뛴다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest
import sp_docx
import sp_mcp
import sp_pipeline
import sp_work

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office"'
)
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006"
BODY_EMU = (11906 - 1440 - 1440) * 635
"""A4 · 좌우 여백 1인치의 본문 폭."""


def _png(color: tuple[int, int, int], width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _size(png: bytes) -> tuple[int, int]:
    width, height = struct.unpack(">II", png[16:24])
    return width, height


RED, GREEN, BLUE, GRAY = (
    _png((255, 0, 0)),
    _png((0, 255, 0)),
    _png((0, 0, 255)),
    _png((9, 9, 9)),
)
FAKE_EMF = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 64


def _run(text: str, bold: bool = False) -> str:
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _p(*runs: str, style: str = "", extra: str = "") -> str:
    props = f'<w:pStyle w:val="{style}"/>' if style else ""
    return f"<w:p><w:pPr>{props}{extra}</w:pPr>{''.join(runs)}</w:p>"


def _picture(rid: str, cx: int, *, descr: str = "", svg: str = "", order: int = 1) -> str:
    ext = (
        '<a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
        f'<asvg:svgBlip r:embed="{svg}"/></a:ext></a:extLst>'
        if svg
        else ""
    )
    return (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="{cx}" cy="100000"/>'
        f'<wp:docPr id="{order}" name="그림 {order}" descr="{descr}"/>'
        f'<a:graphic><a:graphicData uri="{DRAWING}/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="x.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}">{ext}</a:blip></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:ext cx="{cx}" cy="100000"/></a:xfrm></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _chart(rid: str) -> str:
    return (
        '<w:r><w:drawing><wp:inline><wp:extent cx="3000000" cy="2000000"/>'
        '<wp:docPr id="50" name="차트 1"/>'
        f'<a:graphic><a:graphicData uri="{DRAWING}/chart">'
        f'<c:chart r:id="{rid}"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
    )


def _style(style_id: str, name: str, extra: str = "") -> str:
    based = '<w:basedOn w:val="a"/>' if style_id != "a" else ""
    default = ' w:default="1"' if style_id == "a" else ""
    return (
        f'<w:style w:type="paragraph"{default} w:styleId="{style_id}">'
        f'<w:name w:val="{name}"/>{based}{extra}</w:style>'
    )


def _level(ilvl: int, fmt: str, text: str) -> str:
    return (
        f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{text}"/></w:lvl>'
    )


STYLES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles {NS}>'
    + _style("a", "Normal")
    # 한국어 Word 는 제목 스타일의 id 가 「1」 · 「2」 다 — 이름과 outlineLvl 로 안다.
    + _style("1", "heading 1", '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>')
    + _style("MyH2", "제목 2")
    + _style("a3", "caption")
    + "</w:styles>"
)

NUMBERING = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering {NS}>'
    f'<w:abstractNum w:abstractNumId="0">{_level(0, "bullet", "•")}{_level(1, "bullet", "-")}'
    "</w:abstractNum>"
    f'<w:abstractNum w:abstractNumId="1">{_level(0, "decimal", "%1.")}</w:abstractNum>'
    f'<w:abstractNum w:abstractNumId="2">{_level(0, "ganada", "%1.")}</w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    '<w:num w:numId="3"><w:abstractNumId w:val="2"/></w:num>'
    "</w:numbering>"
)


def _item(text: str, num: int, level: int = 0) -> str:
    numbering = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num}"/></w:numPr>'
    return _p(_run(text), extra=numbering)


def _write_docx(
    path: Path,
    body: str,
    *,
    rels: dict[str, tuple[str, str]] | None = None,
    parts: dict[str, bytes | str] | None = None,
) -> Path:
    """최소 .docx — `rels` 는 rId → (종류, 대상). 종류가 `link` 면 바깥 주소."""
    rel_lines = [
        f'<Relationship Id="rIdS" Type="{REL}/styles" Target="styles.xml"/>',
        f'<Relationship Id="rIdN" Type="{REL}/numbering" Target="numbering.xml"/>',
    ]
    for rid, (kind, target) in (rels or {}).items():
        if kind == "link":
            rel_lines.append(
                f'<Relationship Id="{rid}" Type="{REL}/hyperlink" Target="{target}" '
                'TargetMode="External"/>'
            )
        else:
            rel_lines.append(
                f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"/>'
            )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {NS}><w:body>'
        f"{body}"
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdH"/>'
        '<w:footerReference w:type="default" r:id="rIdF"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    rel_lines += [
        f'<Relationship Id="rIdH" Type="{REL}/header" Target="header1.xml"/>',
        f'<Relationship Id="rIdF" Type="{REL}/footer" Target="footer1.xml"/>',
    ]
    files: dict[str, bytes | str] = {
        "[Content_Types].xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{PKG}/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{PKG}/relationships">'
            f"{''.join(rel_lines)}</Relationships>"
        ),
        "word/document.xml": document,
        "word/styles.xml": STYLES,
        "word/numbering.xml": NUMBERING,
        "word/header1.xml": f"<w:hdr {NS}>{_p(_run('머리글 글자'))}</w:hdr>",
        "word/footer1.xml": f"<w:ftr {NS}>{_p(_run('바닥글 글자'))}</w:ftr>",
        **(parts or {}),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


CHART_TITLED = (
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart><c:title><c:tx>'
    "<c:rich><a:p><a:r><a:t>매출 추이</a:t></a:r></a:p></c:rich></c:tx></c:title></c:chart>"
    "</c:chartSpace>"
)
CHART_BARE = (
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    "<c:chart/></c:chartSpace>"
)


def _full_docx(path: Path) -> Path:
    vml = (
        '<w:r><w:pict><v:shape style="width:225pt;height:100pt">'
        '<v:imagedata r:id="rIdV" o:title="옛 그림"/></v:shape></w:pict></w:r>'
    )
    table = (
        "<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>"
        f"<w:tr><w:tc>{_p(_run('이름'))}</w:tc><w:tc>{_p(_run('값'))}</w:tc></w:tr>"
        f"<w:tr><w:tc>{_p(_run('가|나'))}</w:tc>"
        f"<w:tc>{_p(_run('칸 글'))}{_p(_picture('rIdD', 300000, order=6))}</w:tc></w:tr>"
        '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        f"{_p(_run('합침'))}</w:tc></w:tr></w:tbl>"
    )
    body = "".join(
        [
            _p(_run("개요"), style="1"),
            _p(_run("본문 첫 문단 "), _run("굵게", bold=True), _run(" 끝.")),
            _p(_run("세부"), style="MyH2"),
            _p(_run("셋째 단계"), extra='<w:outlineLvl w:val="2"/>'),
            _item("점 하나", 1),
            _item("점 안의 점", 1, level=1),
            _item("첫째", 2),
            _item("둘째", 2),
            _item("가나다 목록", 3),
            _p(_run("그림 앞 문단")),
            _p(_picture("rIdA", int(BODY_EMU * 0.6), order=1)),
            _p(_run("그림 1. 구성도"), style="a3"),
            _p(
                _run("글 사이 "),
                _picture("rIdA", 1_500_000, descr="설명 글", order=2),
                _run(" 뒤 글"),
            ),
            _p(_picture("rIdB", 6_000_000, svg="rIdSvg", order=3)),
            _p(_chart("rIdC1")),
            _p(_chart("rIdC2")),
            table,
            _p(vml),
            _p(_run("같은 바이트 다른 이름")),
            _p(_picture("rIdA2", 1_000_000, order=8)),
            _p(_run("링크 "), '<w:hyperlink r:id="rIdL">' + _run("누리집") + "</w:hyperlink>"),
        ]
    )
    return _write_docx(
        path,
        body,
        rels={
            "rIdA": ("image", "media/image7.png"),
            "rIdA2": ("image", "media/image9.png"),
            "rIdB": ("image", "media/image2.png"),
            "rIdSvg": ("image", "media/image3.svg"),
            "rIdC1": ("chart", "charts/chart1.xml"),
            "rIdC2": ("chart", "charts/chart2.xml"),
            "rIdD": ("image", "media/image4.png"),
            "rIdV": ("image", "media/image5.png"),
            "rIdL": ("link", "https://example.com/a b"),
        },
        parts={
            "word/media/image7.png": RED,
            "word/media/image9.png": RED,
            "word/media/image2.png": GREEN,
            "word/media/image3.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>',
            "word/media/image4.png": BLUE,
            "word/media/image5.png": GRAY,
            "word/charts/chart1.xml": CHART_BARE,
            "word/charts/chart2.xml": CHART_TITLED,
        },
    )


def test_읽는_차례대로_제목_목록_표_그림_자리를_지킨다(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = sp_docx.extract(_full_docx(tmp_path / "설계서.docx"), out)
    draft = (out / sp_docx.DRAFT).read_text(encoding="utf-8")
    blocks = draft.split("\n\n")

    assert blocks[:4] == ["# 개요", "본문 첫 문단 **굵게** 끝.", "## 세부", "### 셋째 단계"]
    assert blocks[4] == "- 점 하나\n    - 점 안의 점"
    assert blocks[5] == "1. 첫째\n2. 둘째"
    assert blocks[6] == "- 가. 가나다 목록"
    # 그림은 제 줄 하나로, 캡션 문단은 그림의 캡션이 되고 본문에 다시 적지 않는다.
    assert blocks[7:12] == [
        "그림 앞 문단",
        "![그림 1. 구성도](media/image1.png?w=60)",
        "글 사이",
        "![설명 글](media/image1.png?w=30)",
        "뒤 글",
    ]
    assert draft.count("그림 1. 구성도") == 1
    # SVG 는 Word 가 함께 넣은 PNG 사본을 쓴다. 본문보다 넓으면 100.
    assert blocks[12] == "![그림 3](media/image2.png?w=100)"
    assert blocks[13] == "> 그림: 차트 (원본 차트는 옮기지 않음)"
    assert blocks[14] == "> 그림: 매출 추이 (차트 — 원본 차트는 옮기지 않음)"
    assert blocks[15].split("\n") == [
        "| 이름 | 값 |",
        "| --- | --- |",
        "| 가\\|나 | 칸 글 / (그림: 그림 6 — 표 아래) |",
        "| 합침 |  |",
    ]
    # 표 안의 그림도 차례대로 — 표 바로 아래에 제 줄로.
    assert blocks[16] == "![그림 6](media/image3.png?w=10)"
    assert blocks[17] == "![옛 그림](media/image4.png?w=50)"
    # 이름이 달라도 같은 바이트면 한 파일.
    assert blocks[19] == "![그림 8](media/image1.png?w=20)"
    assert blocks[20].rstrip("\n") == "링크 [누리집](https://example.com/a%20b)"
    assert "머리글" not in draft and "바닥글" not in draft

    assert sorted(path.name for path in (out / "media").iterdir()) == [
        "image1.png",
        "image2.png",
        "image3.png",
        "image4.png",
    ]
    assert (out / "media" / "image1.png").read_bytes() == RED
    assert (out / "media" / "image2.png").read_bytes() == GREEN

    manifest = json.loads((out / sp_docx.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["format"] == "sp-docx/1" and manifest["source"] == "설계서.docx"
    first = manifest["figures"][0]
    assert first["file"] == "media/image1.png" and first["original"] == "word/media/image7.png"
    assert first["format"] == "png" and first["converted"] is None and first["width"] == 60
    assert first["caption"] == "그림 1. 구성도" and first["caption_from"] == "캡션 문단"
    assert first["before"] == "그림 앞 문단"
    assert manifest["figures"][1]["before"] == "글 사이"
    assert manifest["figures"][2]["svg"] == "word/media/image3.svg"
    assert manifest["figures"][5]["in_table"] is True
    assert [one["order"] for one in manifest["not_moved"]] == [4, 5]
    assert (
        manifest["not_moved"][0]["kind"] == "chart"
        and "차트" in manifest["not_moved"][0]["reason"]
    )
    assert result.draft_chars == len(draft)


def test_캡션을_위에_다는_문서는_위의_캡션을_쓴다(tmp_path: Path) -> None:
    """캡션이 위 · 아래 둘 다 붙을 수 있는 그림이 이웃 그림의 캡션을 가져가지 않는다."""
    body = "".join(
        [
            _p(_run("그림 1 위 캡션"), style="a3"),
            _p(_picture("rIdA", 1_000_000)),
            _p(_run("그림 2 위 캡션"), style="a3"),
            _p(_picture("rIdB", 1_000_000)),
            _p(_run("표 1. 매출"), style="a3"),
        ]
    )
    path = _write_docx(
        tmp_path / "위.docx",
        body,
        rels={"rIdA": ("image", "media/a.png"), "rIdB": ("image", "media/b.png")},
        parts={"word/media/a.png": RED, "word/media/b.png": GREEN},
    )
    sp_docx.extract(path, tmp_path / "out")
    draft = (tmp_path / "out" / sp_docx.DRAFT).read_text(encoding="utf-8")
    assert draft.split("\n\n") == [
        "![그림 1 위 캡션](media/image1.png?w=20)",
        "![그림 2 위 캡션](media/image2.png?w=20)",
        # 표의 캡션은 그림이 가져가지 않는다.
        "표 1. 매출\n",
    ]


def test_EMF_를_못_바꾸면_자리와_이유를_남긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp_docx, "find_powershell", lambda: None)
    path = _write_docx(
        tmp_path / "visio.docx",
        _p(_picture("rIdE", 2_000_000, descr="공정 흐름도")),
        rels={"rIdE": ("image", "media/image1.emf")},
        parts={"word/media/image1.emf": FAKE_EMF},
    )
    sp_docx.extract(path, tmp_path / "out")
    draft = (tmp_path / "out" / sp_docx.DRAFT).read_text(encoding="utf-8")
    assert draft == "> 그림: 공정 흐름도 (EMF — 바꾸지 못해 옮기지 않았습니다)\n"
    manifest = json.loads((tmp_path / "out" / sp_docx.MANIFEST).read_text(encoding="utf-8"))
    gone = manifest["not_moved"][0]
    assert gone["caption"] == "공정 흐름도" and "PowerShell" in gone["reason"]
    assert manifest["figures"][0]["format"] == "emf" and manifest["figures"][0]["file"] is None
    assert list((tmp_path / "out" / "media").iterdir()) == []


def _powershell_emf(shell: str) -> bytes:
    """가로 200 · 세로 100 화면 단위에 빨간 사각형 — GDI+ 의 Metafile 로 그려 EMF 로."""
    script = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Drawing
$probe = New-Object System.Drawing.Bitmap 1,1
$g0 = [System.Drawing.Graphics]::FromImage($probe)
$hdc = $g0.GetHdc()
$ms = New-Object System.IO.MemoryStream
$rect = New-Object System.Drawing.RectangleF 0,0,200,100
$mf = New-Object System.Drawing.Imaging.Metafile($ms, $hdc, $rect,
  [System.Drawing.Imaging.MetafileFrameUnit]::Pixel, [System.Drawing.Imaging.EmfType]::EmfOnly)
$g0.ReleaseHdc($hdc)
$g = [System.Drawing.Graphics]::FromImage($mf)
$g.FillRectangle([System.Drawing.Brushes]::Red, 10, 10, 100, 50)
$g.Dispose(); $mf.Dispose()
[Console]::Out.Write([Convert]::ToBase64String($ms.ToArray()))
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    done = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=120,
        check=True,
        cwd=str(Path(shell).parent),
    )
    return base64.b64decode(done.stdout.strip())


def _bmp(width: int = 3, height: int = 2) -> bytes:
    row = (b"\x00\x00\xff" * width).ljust((width * 3 + 3) // 4 * 4, b"\x00")
    pixels = row * height
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0
    )
    return b"BM" + struct.pack("<IHHI", 14 + 40 + len(pixels), 0, 0, 54) + header + pixels


@pytest.mark.skipif(sp_docx.find_powershell() is None, reason="Windows PowerShell 이 없다")
def test_EMF_와_BMP_는_PowerShell_로_PNG_가_된다(tmp_path: Path) -> None:
    shell = sp_docx.find_powershell()
    assert shell is not None
    emf = _powershell_emf(shell)
    assert sp_docx.sniff(emf) == "emf"
    body = _p(_picture("rIdE", 2_000_000, descr="Visio 그림")) + _p(_picture("rIdM", 500_000))
    path = _write_docx(
        tmp_path / "visio.docx",
        body,
        rels={"rIdE": ("image", "media/image1.emf"), "rIdM": ("image", "media/image2.bmp")},
        parts={"word/media/image1.emf": emf, "word/media/image2.bmp": _bmp()},
    )
    sp_docx.extract(path, tmp_path / "out")
    draft = (tmp_path / "out" / sp_docx.DRAFT).read_text(encoding="utf-8")
    assert draft.split("\n\n")[0] == "![Visio 그림](media/image1.png?w=30)"
    png = (tmp_path / "out" / "media" / "image1.png").read_bytes()
    assert sp_docx.sniff(png) == "png"
    # 벡터는 긴 변 1600 으로 키워 그린다 — 가로세로 비는 그대로.
    assert _size(png) == (1600, 800)
    # 래스터(BMP)는 크기 그대로 PNG 로.
    assert _size((tmp_path / "out" / "media" / "image2.png").read_bytes()) == (3, 2)
    manifest = json.loads((tmp_path / "out" / sp_docx.MANIFEST).read_text(encoding="utf-8"))
    assert [one["converted"] for one in manifest["figures"]] == ["EMF→PNG", "BMP→PNG"]
    assert manifest["not_moved"] == []


def test_zip_은_풀기_전에_상한을_본다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _full_docx(tmp_path / "큰.docx")
    monkeypatch.setattr(sp_docx, "MAX_ENTRIES", 5)
    with pytest.raises(sp_pipeline.Stop, match=r"항목이 .*풀지 않았습니다"):
        sp_docx.extract(path, tmp_path / "out")
    monkeypatch.setattr(sp_docx, "MAX_ENTRIES", 5_000)
    monkeypatch.setattr(sp_docx, "MAX_UNPACKED", 1_000)
    with pytest.raises(sp_pipeline.Stop, match=r"상한 .*풀지 않았습니다"):
        sp_docx.extract(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_패키지_밖을_가리키는_그림은_풀지_않고_DTD_는_거절한다(tmp_path: Path) -> None:
    path = _write_docx(
        tmp_path / "악성.docx",
        _p(_picture("rIdX", 1_000_000)),
        rels={"rIdX": ("image", "../../../evil.png")},
        parts={"../../evil.png": RED, "evil.png": RED},
    )
    sp_docx.extract(path, tmp_path / "out" / "a")
    manifest = json.loads(
        (tmp_path / "out" / "a" / sp_docx.MANIFEST).read_text(encoding="utf-8")
    )
    assert manifest["figures"][0]["file"] is None
    assert (
        not (tmp_path / "evil.png").exists() and not (tmp_path / "out" / "evil.png").exists()
    )

    bomb = tmp_path / "폭탄.docx"
    with zipfile.ZipFile(bomb, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
            f"<w:document {NS}><w:body>{_p(_run('&b;'))}</w:body></w:document>",
        )
    with pytest.raises(sp_pipeline.Stop, match="DTD"):
        sp_docx.extract(bomb, tmp_path / "out" / "b")


def test_옛_doc_이나_zip_이_아니면_말한다(tmp_path: Path) -> None:
    old = tmp_path / "옛.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(sp_pipeline.Stop, match=r"\.docx"):
        sp_docx.extract(old, tmp_path / "out")


def test_다시_꺼내면_새로_만들고_남의_폴더는_안_덮는다(tmp_path: Path) -> None:
    path = _full_docx(tmp_path / "설계서.docx")
    out = tmp_path / "out"
    sp_docx.extract(path, out)
    (out / "media" / "image9.png").write_bytes(RED)  # 지난 꺼내기의 찌꺼기
    sp_docx.extract(path, out)
    assert not (out / "media" / "image9.png").exists()

    mine = tmp_path / "사람"
    mine.mkdir()
    (mine / "메모.txt").write_text("지우면 안 됨", encoding="utf-8")
    with pytest.raises(sp_pipeline.Stop, match="만들지 않은 파일"):
        sp_docx.extract(path, mine)
    assert (mine / "메모.txt").exists()


def test_명령으로도_돈다(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work = tmp_path / "작업"
    sp_work.init(work, title="설계서")
    _full_docx(work / sp_work.SOURCES / "설계서.docx")
    assert sp_pipeline.main(["docx", str(work / sp_work.SOURCES / "설계서.docx")]) == 0
    shown = capsys.readouterr().out
    assert "꺼냈습니다" in shown and "못 옮김 2" in shown
    assert (work / sp_work.DOCS / "설계서" / sp_docx.DRAFT).is_file()


def test_MCP_도구는_작업_폴더_안만_읽고_쓴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "작업들"
    root.mkdir()
    monkeypatch.setenv("SP_WORK_ROOT", str(root))
    sp_work.init(root / "설계", title="설계서")
    _full_docx(root / "설계" / sp_work.SOURCES / "설계서.docx")
    (tmp_path / "밖.docx").write_bytes(
        (root / "설계" / sp_work.SOURCES / "설계서.docx").read_bytes()
    )

    def call(arguments: dict[str, str]) -> object:
        return asyncio.run(sp_mcp.mcp.call_tool("docx_extract", arguments))

    with pytest.raises(Exception, match="작업 폴더 밖의 경로입니다"):
        call({"work": "../밖", "source": "설계서.docx"})
    with pytest.raises(Exception, match="작업 폴더 밖의 경로입니다"):
        call({"work": "설계", "source": "../../밖.docx"})

    result = sp_mcp.docx_extract("설계", "설계서.docx", name="../../새는-이름")
    assert result["folder"] == "docs/새는-이름"
    made = root / "설계" / "docs" / "새는-이름"
    assert (made / sp_docx.DRAFT).is_file()
    # 올릴 파일은 같은 그림이면 한 번 — 어느 자리들에 쓰였는지와 함께.
    assert result["files"][0] == {
        "file": "media/image1.png",
        "local_path": str(made / "media" / "image1.png"),
        "figures": [1, 2, 8],
    }
    assert len(result["files"]) == 4
    assert result["figures"][0]["line"] == "![그림 1. 구성도](media/image1.png?w=60)"
    assert [one["order"] for one in result["not_moved"]] == [4, 5]
    assert any("attachment_upload_prepare" in step for step in result["next"])
    # 그 뒤의 차례는 안내의 정본(AGENTS.md)에 — 도구가 그 절을 내려준다.
    guide = sp_mcp.pipeline_guide("docx")["content"]
    assert "attachment_upload_prepare" in guide and "photo:<첨부 id>" in guide
    assert "ADR 0053" in guide
