#!/usr/bin/env python3
"""Word(.docx) → **그림 자리가 든 마크다운 초안 + 그림 파일** — 문서 속성에 넣기 전에.

AI 가 Word 에서 글만 뽑아 읽으면 그림이 본문 어디에 있었는지가 사라진다. 이 도구는 .docx 를
읽는 차례대로 풀어 글 초안과 그림 파일을 함께 꺼내고, 그림은 **제 줄 하나로** 그 자리에 둔다:

    python sp_pipeline.py docx 00-원천/설계서.docx          # → docs/설계서/

    docs/설계서/
      draft.md        글 초안 — 그림은 `![캡션](media/image3.png?w=60)` 한 줄
      media/          쓰인 그림만, 읽는 차례대로 image1.png …(같은 그림은 한 파일)
      manifest.json   그림마다 차례 · 파일 · 원래 이름 · 형식 · 바꿨는지 · 폭 · 캡션 ·
                      바로 앞 문단 글, 그리고 못 옮긴 것과 그 이유

그 뒤는 AI 가 한다(`AGENTS.md` 「Word 문서를 문서 속성으로」): 그림마다 플랫폼 MCP 의
`attachment_upload_prepare` → `curl` 로 올려 첨부 id 를 받고, 그림 줄의 경로를
`photo:<첨부 id>` 로 바꿔(`![캡션](photo:<id>?w=60)`) 글을 다듬어 `document_write`.
**이 도구는 플랫폼에 아무것도 올리지 않는다** — 꺼내기만 한다.

ADR 0053 은 「서버가 Word 를 마크다운으로」 를 버렸다(서버에 변환기를 깔면 폐쇄망 번들이
커지고, 글 다듬기는 AI 가 원본을 보고 하는 편이 낫다). 이 도구는 그것과 부딪치지 않는다 —
**사용자 PC 에서 표준 라이브러리만으로** 돌고, 글은 그림 자리를 지키는 「초안」 일 뿐 다듬기는
여전히 AI 몫이다.

- 폭 `w` 는 Word 에서 차지하던 폭: 그림 폭(EMU) / 본문 폭(용지 폭에서 좌우 여백을 뺀 것)의
  백분율을 10 단위로 반올림, 10 ~ 100.
- 그림 형식: PNG · JPEG · GIF · WebP 는 그대로(플랫폼이 이 넷만 그림으로 받는다). SVG 는
  Word 가 함께 넣는 PNG 사본을 쓴다. EMF · WMF(Visio 그림 · 붙여 넣은 엑셀 차트) · TIFF ·
  BMP 는 Windows 의 PowerShell(GDI+)로 PNG 로 바꾼다 — 못 바꾸면 그 자리에 `> 그림: …` 을
  남긴다.
- 차트 · SmartArt · Word 도형은 그림 파일이 아니다 — 자리만 남긴다.
- **zip 을 믿지 않는다.** 항목 수 · 풀린 크기를 풀기 전에 보고, 디스크에 경로째 풀지 않는다
  (그림은 새 이름으로만 쓴다). XML 은 DTD(엔티티 선언)가 있으면 읽지 않는다 — Word 는 DTD 를
  쓰지 않는다.

표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import urllib.parse
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.parsers import expat

import sp_pipeline as pipeline
import sp_work

Stop = pipeline.Stop

FORMAT = "sp-docx/1"
DRAFT = "draft.md"
MANIFEST = "manifest.json"
MEDIA = "media"

# --- zip 을 믿지 않는다 — 풀기 전에 본다 ------------------------------------------
MAX_ENTRIES = 5_000
"""Word 문서 하나의 항목 수 — 그림 수백 장짜리 문서도 천 개 안팎이다."""
MAX_UNPACKED = 1024 * 1024 * 1024
"""풀린 크기의 합(선언된 값 — 읽을 때도 그 이상은 안 나온다)."""
MAX_PART = 256 * 1024 * 1024
"""항목 하나(그림 한 장)."""
MAX_XML = 64 * 1024 * 1024
"""XML 부분 하나 — 본문이 수천 쪽이어도 이 안이다."""

# --- 그림 ---------------------------------------------------------------------
KEEP = {"png": "png", "jpeg": "jpg", "gif": "gif", "webp": "webp"}
"""플랫폼이 그림으로 받는 형식 — 그대로 둔다(형식 → 확장자)."""
LONG_SIDE = 1600
"""EMF · WMF 를 그릴 때 긴 변(px) — 벡터라 키워 그려야 화면에서 흐리지 않다."""
CONVERT_TIMEOUT = 600
DEFAULT_WIDTH = 50
EMU_PER_TWIP = 635
A4_TEXT_TWIPS = 11906 - 2 * 1440
"""구역 정보가 없을 때의 본문 폭 — A4, 좌우 여백 1인치."""

# --- 이름 공간 ----------------------------------------------------------------
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
CHARTEX = "http://schemas.microsoft.com/office/drawing/2014/chartex"
DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPC = "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
STRICT_W = "http://purl.oclc.org/ooxml/wordprocessingml/main"


def _w(local: str) -> str:
    return f"{{{W}}}{local}"


def _q(space: str, local: str) -> str:
    return f"{{{space}}}{local}"


W_P, W_R, W_T, W_TBL, W_TR, W_TC = (_w(x) for x in ("p", "r", "t", "tbl", "tr", "tc"))
W_PPR, W_RPR, W_VAL = _w("pPr"), _w("rPr"), _w("val")
W_SDT, W_SDTCONTENT = _w("sdt"), _w("sdtContent")
W_TXBX = _w("txbxContent")
MC_ALT, MC_CHOICE = _q(MC, "AlternateContent"), _q(MC, "Choice")
PIC_PIC = _q(PIC, "pic")
R_ID, R_EMBED, R_LINK = _q(R, "id"), _q(R, "embed"), _q(R, "link")
VML_SHAPES = frozenset(_q(V, x) for x in ("shape", "rect", "roundrect", "oval", "image"))
TRANSPARENT = frozenset(
    _w(x) for x in ("ins", "moveTo", "smartTag", "customXml", "dir", "bdo", "rubyBase")
)
"""안의 런을 그대로 읽는 감싸개 — 고친 흔적(넣음) · 스마트 태그 · 사용자 XML."""
DROPPED = frozenset(_w(x) for x in ("del", "moveFrom", "rt"))
"""읽지 않는 것 — 지운 흔적 · 옮겨 간 원래 자리 · 윗주."""
HIDDEN_FIELDS = frozenset({"TOC", "PAGE", "NUMPAGES", "SECTIONPAGES", "PAGEREF"})
"""결과를 본문에 안 싣는 필드 — 목차와 쪽 번호(마크다운에는 쪽이 없다)."""

HEADING_NAME = re.compile(r"^(?:heading|제목)\s*([1-9])$", re.IGNORECASE)
TITLE_NAME = re.compile(r"^(?:title|제목)$", re.IGNORECASE)
CAPTION_NAME = re.compile(r"^(?:caption|캡션)$", re.IGNORECASE)
TOC_NAME = re.compile(r"^(?:toc|목차)\s*[1-9]$", re.IGNORECASE)
TABLE_SEQ = frozenset({"표", "table", "tab"})
TABLE_CAPTION = re.compile(r"^[\[<(〈【]?\s*(?:표|table)\s*[0-9]", re.IGNORECASE)
"""표의 캡션 — 그림이 가져가지 않는다(「표 1. 매출」 은 그 아래 표의 것이다)."""
AUTO_ALT = re.compile(r"(자동\s*생성된\s*설명|description automatically generated)\s*$", re.I)
"""Office 가 스스로 붙인 대체 텍스트 — 캡션으로 쓰기에는 소음이다."""


# --------------------------------------------------------------------------
# 패키지(zip)와 XML
# --------------------------------------------------------------------------


def parse_xml(data: bytes, name: str) -> ET.Element:
    """XML 을 읽는다 — **DTD 가 있으면 거절한다**(외부 엔티티 · 엔티티 폭탄의 자리).

    `ElementTree` 의 기본 파서는 내부 엔티티를 펼친다. 같은 expat 를 직접 세워 DOCTYPE 이
    시작되는 순간 멈추고, 나머지는 `TreeBuilder` 로 같은 나무를 만든다."""
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")

    def fixed(raw: str) -> str:
        return "{" + raw if "}" in raw else raw

    def start(tag: str, attrs: dict[str, str]) -> None:
        builder.start(fixed(tag), {fixed(key): value for key, value in attrs.items()})

    def refuse(*_args: Any) -> None:
        raise Stop(
            f"{name}: DTD(엔티티 선언)가 든 XML 은 읽지 않습니다 — Word 가 만든 문서가 "
            "아닙니다"
        )

    def refuse_external(*_args: Any) -> int:
        refuse()
        return 0

    parser.StartElementHandler = start
    parser.EndElementHandler = lambda tag: builder.end(fixed(tag))
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse_external
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = True
    try:
        parser.Parse(data, True)
    except expat.ExpatError as failure:
        raise Stop(f"{name}: XML 을 읽을 수 없습니다 ({failure})") from None
    return builder.close()


@dataclass(frozen=True)
class Rel:
    target: str
    """패키지 안의 부분 이름(바깥이면 주소 그대로)."""
    external: bool
    kind: str
    """관계 종류의 끝 조각 — image · styles · numbering · footnotes …"""


class Package:
    """.docx(zip) — **풀기 전에 항목 수 · 풀린 크기를 본다.** 디스크에는 풀지 않는다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            with path.open("rb") as handle:
                head = handle.read(8)
        except OSError as failure:
            raise Stop(f"원천을 읽을 수 없습니다: {path.name} ({failure})") from None
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            raise Stop(
                f"{path.name}: 옛 Word(.doc)이거나 암호가 걸린 문서입니다 — Word 에서 열어 "
                "암호를 풀고 「Word 문서(.docx)」 로 다시 저장해 주세요"
            )
        try:
            self.archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as failure:
            raise Stop(
                f"{path.name}: Word 문서(.docx)가 아닙니다 — zip 이 아닙니다 ({failure})"
            ) from None
        infos = self.archive.infolist()
        if len(infos) > MAX_ENTRIES:
            self.archive.close()
            raise Stop(
                f"{path.name}: 항목이 {len(infos):,}개 — 상한 {MAX_ENTRIES:,}개를 넘습니다"
                "(풀지 않았습니다)"
            )
        total = sum(info.file_size for info in infos)
        if total > MAX_UNPACKED:
            self.archive.close()
            raise Stop(
                f"{path.name}: 풀면 {_bytes(total)} — 상한 {_bytes(MAX_UNPACKED)} 를 넘습니다"
                "(풀지 않았습니다)"
            )
        # 부분 이름은 대소문자를 가리지 않고, 관계에는 %-인코딩으로 적힐 수 있다(OPC).
        self.parts: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            if not info.is_dir():
                self.parts.setdefault(info.filename.lower(), info)
                self.parts.setdefault(urllib.parse.unquote(info.filename).lower(), info)

    def __enter__(self) -> Package:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.archive.close()

    def has(self, name: str) -> bool:
        return name.lower() in self.parts

    def read(self, name: str, limit: int = MAX_PART) -> bytes:
        info = self.parts.get(name.lower())
        if info is None:
            raise Stop(f"문서 안에 {name} 이 없습니다")
        if info.file_size > limit:
            raise Stop(
                f"{name}: 풀면 {_bytes(info.file_size)} — 상한 {_bytes(limit)} 를 넘습니다"
            )
        try:
            # 선언된 크기까지만 나온다(zipfile 이 자르고 CRC 를 본다) — 그래도 상한을 한 번 더.
            with self.archive.open(info) as handle:
                data = handle.read(limit + 1)
        except (
            zipfile.BadZipFile,
            RuntimeError,
            NotImplementedError,
            OSError,
            EOFError,
            zlib.error,
        ) as failure:
            raise Stop(f"{name}: 풀 수 없습니다 ({failure})") from None
        if len(data) > limit:
            raise Stop(f"{name}: 너무 큽니다")
        return data

    def xml(self, name: str) -> ET.Element | None:
        if not self.has(name):
            return None
        return parse_xml(self.read(name, MAX_XML), name)

    def rels(self, part: str) -> dict[str, Rel]:
        folder, base = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", f"{base}.rels"))
        out: dict[str, Rel] = {}
        if root is None:
            return out
        for one in root.iter(_q(PKG, "Relationship")):
            rid, target = one.get("Id") or "", one.get("Target") or ""
            kind = (one.get("Type") or "").rsplit("/", 1)[-1]
            if (one.get("TargetMode") or "").lower() == "external":
                out[rid] = Rel(target, True, kind)
                continue
            resolved = _resolve(part, target)
            if resolved is not None:
                out[rid] = Rel(resolved, False, kind)
        return out

    def main_part(self) -> str:
        for rel in self.rels("").values():
            if rel.kind == "officeDocument" and not rel.external:
                return rel.target
        return "word/document.xml"


def _bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:,.0f}MB"
    return f"{size:,}바이트"


def _resolve(base: str, target: str) -> str | None:
    """관계의 대상 → 패키지 안 부분 이름. **패키지 밖(`../`)을 가리키면 없는 것으로.**"""
    target = urllib.parse.unquote(target)
    joined = (
        target.lstrip("/")
        if target.startswith("/")
        else posixpath.join(posixpath.dirname(base), target)
    )
    normal = posixpath.normpath(joined)
    if normal in {".", ".."} or normal.startswith(("../", "/")):
        return None
    return normal


# --------------------------------------------------------------------------
# 스타일 · 번호
# --------------------------------------------------------------------------


def _val(parent: ET.Element | None, local: str) -> str | None:
    if parent is None:
        return None
    found = parent.find(_w(local))
    return None if found is None else found.get(W_VAL)


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _on(props: ET.Element | None, local: str) -> bool:
    """`<w:b/>` · `<w:b w:val="1"/>` 은 켬, `w:val="0|false|off"` 는 끔."""
    if props is None:
        return False
    found = props.find(_w(local))
    return found is not None and (found.get(W_VAL) or "1").lower() not in {"0", "false", "off"}


@dataclass
class Style:
    name: str = ""
    based_on: str = ""
    outline: int | None = None
    num_id: str | None = None
    ilvl: int | None = None


@dataclass(frozen=True)
class StyleInfo:
    heading: int | None = None
    caption: bool = False
    toc: bool = False
    num_id: str | None = None
    ilvl: int | None = None


class Styles:
    def __init__(self, root: ET.Element | None) -> None:
        self.styles: dict[str, Style] = {}
        self.default = ""
        self._cache: dict[str, StyleInfo] = {}
        if root is None:
            return
        for one in root.iter(_w("style")):
            if one.get(_w("type")) != "paragraph":
                continue
            style_id = one.get(_w("styleId")) or ""
            ppr = one.find(W_PPR)
            num = ppr.find(_w("numPr")) if ppr is not None else None
            self.styles[style_id] = Style(
                name=_val(one, "name") or "",
                based_on=_val(one, "basedOn") or "",
                outline=_int(_val(ppr, "outlineLvl")),
                num_id=_val(num, "numId"),
                ilvl=_int(_val(num, "ilvl")),
            )
            if (one.get(_w("default")) or "").lower() in {"1", "true"}:
                self.default = style_id

    def _chain(self, style_id: str) -> list[Style]:
        out: list[Style] = []
        seen: set[str] = set()
        while style_id and style_id not in seen and style_id in self.styles and len(out) < 20:
            seen.add(style_id)
            out.append(self.styles[style_id])
            style_id = self.styles[style_id].based_on
        return out

    def info(self, style_id: str) -> StyleInfo:
        if style_id in self._cache:
            return self._cache[style_id]
        chain = self._chain(style_id)
        own = chain[0].name.strip() if chain else ""
        heading: int | None = None
        named = HEADING_NAME.match(own)
        if named:
            heading = int(named.group(1))
        elif TITLE_NAME.match(own):
            heading = 1
        else:
            outline = next((one.outline for one in chain if one.outline is not None), None)
            if outline is not None and 0 <= outline < 9:
                heading = outline + 1
        numbered = next((one for one in chain if one.num_id is not None), None)
        made = StyleInfo(
            heading=heading,
            caption=any(CAPTION_NAME.match(one.name.strip()) for one in chain),
            toc=bool(TOC_NAME.match(own)),
            num_id=numbered.num_id if numbered else None,
            ilvl=numbered.ilvl if numbered else None,
        )
        self._cache[style_id] = made
        return made


@dataclass
class Level:
    fmt: str = "decimal"
    text: str = ""
    start: int = 1
    style: str = ""
    legal: bool = False


GANADA = "가나다라마바사아자차카타파하"
CHOSUNG = "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


ROMAN = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
    (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")
)  # fmt: skip


def _roman(number: int) -> str:
    out = ""
    for value, letters in ROMAN:
        while number >= value:
            out += letters
            number -= value
    return out


def _number(number: int, fmt: str) -> str:
    if number < 1:
        return str(number)
    if fmt == "decimalZero":
        return f"{number:02d}"
    if fmt in {"upperRoman", "lowerRoman"}:
        roman = _roman(number)
        return roman if fmt == "upperRoman" else roman.lower()
    if fmt in {"upperLetter", "lowerLetter"}:
        letter = chr(ord("A") + (number - 1) % 26) * ((number - 1) // 26 + 1)
        return letter if fmt == "upperLetter" else letter.lower()
    if fmt == "ganada":
        return GANADA[(number - 1) % len(GANADA)]
    if fmt == "chosung":
        return CHOSUNG[(number - 1) % len(CHOSUNG)]
    if fmt.startswith("decimalEnclosedCircle"):
        return CIRCLED[number - 1] if number <= len(CIRCLED) else f"({number})"
    if fmt == "decimalEnclosedParen":
        return f"({number})"
    return str(number)


class Numbering:
    """`numbering.xml` — 목록이 글머리표인지 번호인지, 그리고 Word 가 그리는 번호 글자
    (「1.2」 ·
    「가.」). 번호는 같은 추상 번호를 쓰는 목록끼리 이어 센다(Word 와 같다)."""

    def __init__(self, root: ET.Element | None) -> None:
        self.abstracts: dict[str, dict[int, Level]] = {}
        self.links: dict[str, str] = {}
        self.styled: dict[str, str] = {}
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}
        self.counters: dict[str, list[int | None]] = {}
        self.started: set[str] = set()
        if root is None:
            return
        for one in root.findall(_w("abstractNum")):
            key = one.get(_w("abstractNumId")) or ""
            levels: dict[int, Level] = {}
            for lvl in one.findall(_w("lvl")):
                index = _int(lvl.get(_w("ilvl")))
                if index is None:
                    continue
                levels[index] = Level(
                    fmt=_val(lvl, "numFmt") or "decimal",
                    text=_val(lvl, "lvlText") or "",
                    start=_int(_val(lvl, "start")) or 1,
                    style=_val(lvl, "pStyle") or "",
                    legal=lvl.find(_w("isLgl")) is not None,
                )
            self.abstracts[key] = levels
            link = _val(one, "numStyleLink")
            if link:
                self.links[key] = link
            styled = _val(one, "styleLink")
            if styled:
                self.styled[styled] = key
        for one in root.findall(_w("num")):
            overrides: dict[int, int] = {}
            for override in one.findall(_w("lvlOverride")):
                index = _int(override.get(_w("ilvl")))
                start = _int(_val(override, "startOverride"))
                if index is not None and start is not None:
                    overrides[index] = start
            self.nums[one.get(_w("numId")) or ""] = (
                _val(one, "abstractNumId") or "",
                overrides,
            )

    def _levels(self, num_id: str) -> tuple[str, dict[int, Level]] | None:
        if num_id not in self.nums:
            return None
        key = self.nums[num_id][0]
        levels = self.abstracts.get(key, {})
        if not levels and key in self.links:
            key = self.styled.get(self.links[key], key)
            levels = self.abstracts.get(key, {})
        return key, levels

    def level_of_style(self, num_id: str, style_id: str) -> int | None:
        found = self._levels(num_id)
        if found is None:
            return None
        return next((index for index, one in found[1].items() if one.style == style_id), None)

    def advance(self, num_id: str, ilvl: int) -> tuple[Level, str, int] | None:
        """그 목록의 다음 번호 — (단계, Word 가 그리는 번호 글자, 이 단계의 수)."""
        found = self._levels(num_id)
        if found is None or ilvl not in found[1]:
            return None
        key, levels = found
        counters = self.counters.setdefault(key, [None] * 9)
        if num_id not in self.started:
            self.started.add(num_id)
            for index, start in self.nums[num_id][1].items():
                if 0 <= index < 9:
                    counters[index] = start - 1
        if not 0 <= ilvl < 9:
            return None
        current = counters[ilvl]
        counters[ilvl] = levels[ilvl].start if current is None else current + 1
        for deeper in range(ilvl + 1, 9):
            counters[deeper] = None
        level = levels[ilvl]

        def one(found: re.Match[str]) -> str:
            index = int(found.group(1)) - 1
            spec = levels.get(index, Level())
            value = counters[index] if 0 <= index < 9 else None
            number = spec.start if value is None else value
            return _number(number, "decimal" if level.legal else spec.fmt)

        label = re.sub(r"%([1-9])", one, level.text)
        return level, label, counters[ilvl] or level.start


# --------------------------------------------------------------------------
# 읽은 것
# --------------------------------------------------------------------------


@dataclass
class Figure:
    order: int
    kind: str
    """image · chart · smartart · shapes"""
    section: int
    alt: str = ""
    cx: int | None = None
    floating: bool = False
    in_table: bool = False
    part: str | None = None
    svg: str | None = None
    ole: str = ""
    title: str = ""
    texts: list[str] = field(default_factory=list)
    short: str = ""
    """못 옮겼을 때 그 자리에 남길 말."""
    reason: str = ""
    caption: str = ""
    caption_from: str = ""
    before: str = ""
    format: str = ""
    converted: str = ""
    file: str | None = None
    width: int = DEFAULT_WIDTH
    line: str = ""


@dataclass
class Text:
    md: str
    plain: str
    role: str = "para"
    """para · heading · list"""
    level: int = 0
    marker: str = ""
    group: str = ""
    """목록이면 그 번호 목록(numId) — 다른 목록이 시작되면 빈 줄로 가른다."""
    caption: bool = False
    table_caption: bool = False
    used: bool = False


@dataclass
class Table:
    rows: list[list[list[Any]]]
    """줄 → 칸 → 칸 안의 블록(Text · Figure · Table)."""


Block = Text | Figure | Table


@dataclass
class Field:
    instr: str = ""
    result: bool = False

    @property
    def kind(self) -> str:
        words = self.instr.split()
        return words[0].upper() if words else ""


@dataclass
class Inline:
    """문단 하나의 안 — 글 조각 · 줄바꿈 · 그림 · 글상자(블록)를 나온 차례대로."""

    items: list[tuple[str, Any]] = field(default_factory=list)
    seq: str | None = None


def _find(
    element: ET.Element, tags: frozenset[str], skip: frozenset[str] = frozenset()
) -> list[ET.Element]:
    """찾는 태그를 문서 차례대로 — 찾은 것과 `skip` 의 안으로는 내려가지 않는다. 호환
    블록(`mc:AlternateContent`)은 첫 선택만 본다(대체본까지 보면 같은 그림이 둘이 된다)."""
    out: list[ET.Element] = []
    for child in element:
        if child.tag in tags:
            out.append(child)
        elif child.tag in skip:
            continue
        elif child.tag == MC_ALT:
            choice = child.find(MC_CHOICE)
            if choice is not None:
                out.extend(_find(choice, tags, skip))
        else:
            out.extend(_find(child, tags, skip))
    return out


def _text_width(sect: ET.Element) -> int:
    """구역의 본문 폭(EMU) — 용지 폭에서 좌우 여백과 제본 여백을 뺀 것."""
    size = sect.find(_w("pgSz"))
    margin = sect.find(_w("pgMar"))
    page = _int(size.get(_w("w"))) if size is not None else None
    left = right = gutter = None
    if margin is not None:
        left = _int(margin.get(_w("left")) or margin.get(_w("start")))
        right = _int(margin.get(_w("right")) or margin.get(_w("end")))
        gutter = _int(margin.get(_w("gutter")))
    width = (
        (page or 11906) - (1440 if left is None else left) - (1440 if right is None else right)
    )
    width -= gutter or 0
    return (width if width > 0 else A4_TEXT_TWIPS) * EMU_PER_TWIP


VML_UNITS = {
    "pt": 12700,
    "in": 914400,
    "cm": 360000,
    "mm": 36000,
    "pc": 152400,
    "px": 9525,
    "": 9525,
}


def _vml_width(style: str | None) -> int | None:
    found = re.search(
        r"(?:^|;)\s*width\s*:\s*([0-9.]+)\s*([a-z]*)", style or "", re.IGNORECASE
    )
    if not found or found.group(2).lower() not in VML_UNITS:
        return None
    try:
        return int(float(found.group(1)) * VML_UNITS[found.group(2).lower()])
    except ValueError:
        return None


def _alt_of(doc: ET.Element | None) -> str:
    if doc is None:
        return ""
    for key in ("descr", "title"):
        value = " ".join((doc.get(key) or "").split())
        if value and not AUTO_ALT.search(value):
            return value
    return ""


def _caption_text(text: str) -> str:
    """그림 표시의 `[…]` 안 — 줄바꿈 없이, 대괄호 없이(플랫폼의 표시 규칙)."""
    one = " ".join(text.split()).replace("[", "(").replace("]", ")")
    return one[:200].rstrip()


# --------------------------------------------------------------------------
# 본문 읽기
# --------------------------------------------------------------------------


class Reader:
    """본문을 **읽는 차례대로** 블록으로 — 문단 · 표 · 그림. 머리글 · 바닥글은 다른 부분이라
    애초에 읽지 않는다."""

    def __init__(self, package: Package) -> None:
        self.package = package
        self.part = package.main_part()
        root = package.xml(self.part)
        if root is None:
            raise Stop(
                f"{package.path.name}: Word 문서가 아닙니다 — 본문({self.part})이 없습니다"
            )
        if root.tag.startswith(f"{{{STRICT_W}}}"):
            raise Stop(
                f"{package.path.name}: 「Strict Open XML」 문서는 아직 못 읽습니다 — "
                "Word 에서 "
                "「Word 문서(.docx)」 로 다시 저장해 주세요"
            )
        body = root.find(_w("body"))
        if body is None:
            raise Stop(f"{package.path.name}: Word 문서가 아닙니다 — 본문(w:body)이 없습니다")
        self.rels = package.rels(self.part)
        self.styles = Styles(self._related("styles"))
        self.numbering = Numbering(self._related("numbering"))
        self.note_texts = self._notes()
        self.note_numbers: dict[tuple[str, str], int] = {}
        self.figures: list[Figure] = []
        self.sections: list[int] = []
        self.fields: list[Field] = []
        self.remarks: list[str] = []
        self.textboxes = 0
        self.table_depth = 0
        self.body = self.blocks(body)
        if self.fields:
            self.remarks.append(
                "Word 필드가 닫히지 않은 채 끝납니다 — 그 뒤의 글이 빠졌을 수 있습니다"
                "(원본과 맞춰 보세요)"
            )
        if not self.sections:
            self.sections.append(A4_TEXT_TWIPS * EMU_PER_TWIP)

    # --- 곁가지 부분 ----------------------------------------------------------

    def _related(self, kind: str) -> ET.Element | None:
        for rel in self.rels.values():
            if rel.kind == kind and not rel.external:
                return self.package.xml(rel.target)
        return None

    def _notes(self) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for kind, part, tag in (("f", "footnotes", "footnote"), ("e", "endnotes", "endnote")):
            root = self._related(part)
            if root is None:
                continue
            for note in root.findall(_w(tag)):
                if (note.get(_w("type")) or "normal") != "normal":
                    continue  # 구분선 · 이어짐 표시
                texts = [
                    "".join(t.text or "" for t in p.iter(W_T)).strip() for p in note.iter(W_P)
                ]
                text = " ".join(one for one in texts if one)
                if text:
                    out[(kind, note.get(_w("id")) or "")] = text
        return out

    def _note_ref(self, kind: str, note_id: str) -> str:
        if (kind, note_id) not in self.note_texts:
            return ""
        number = self.note_numbers.setdefault((kind, note_id), len(self.note_numbers) + 1)
        return f"[^{number}]"

    def _chart_title(self, rid: str | None) -> str:
        rel = self.rels.get(rid or "")
        if rel is None or rel.external:
            return ""
        try:
            root = self.package.xml(rel.target)
        except Stop:
            return ""
        if root is None:
            return ""
        title = next((one for one in root.iter() if one.tag.endswith("}title")), None)
        if title is None:
            return ""
        return " ".join("".join(t.text or "" for t in title.iter(_q(A, "t"))).split())

    def _smartart_texts(self, rid: str | None) -> list[str]:
        rel = self.rels.get(rid or "")
        if rel is None or rel.external:
            return []
        try:
            root = self.package.xml(rel.target)
        except Stop:
            return []
        out: list[str] = []
        for point in [] if root is None else root.iter(_q(DGM, "pt")):
            if (point.get("type") or "node") not in {"node", "asst"}:
                continue
            words = " ".join("".join(t.text or "" for t in point.iter(_q(A, "t"))).split())
            if words:
                out.append(words)
        return out[:60]

    # --- 블록 ------------------------------------------------------------------

    def blocks(self, container: ET.Element) -> list[Block]:
        out: list[Block] = []
        for child in container:
            tag = child.tag
            if tag == W_P:
                out.extend(self.paragraph(child))
            elif tag == W_TBL:
                out.extend(self.table(child))
            elif tag == W_SDT:
                content = child.find(W_SDTCONTENT)
                if content is not None:
                    out.extend(self.blocks(content))
            elif tag == _w("customXml"):
                out.extend(self.blocks(child))
            elif tag == MC_ALT:
                choice = child.find(MC_CHOICE)
                if choice is not None:
                    out.extend(self.blocks(choice))
            elif tag == _w("altChunk"):
                self.remarks.append("본문에 끼워 넣은 다른 문서(altChunk)는 읽지 않았습니다")
            elif tag == _w("sectPr"):
                self.sections.append(_text_width(child))
        return out

    def table(self, element: ET.Element) -> list[Block]:
        self.table_depth += 1
        rows: list[list[list[Any]]] = []
        try:
            for row in _find(element, frozenset({W_TR}), frozenset({W_TBL})):
                # 줄 앞의 빈 격자(gridBefore)는 빈 칸으로 — 열이 밀리지 않게.
                before = _int(_val(row.find(_w("trPr")), "gridBefore")) or 0
                cells: list[list[Any]] = [[] for _ in range(max(0, before))]
                for cell in _find(row, frozenset({W_TC}), frozenset({W_TBL})):
                    tcpr = cell.find(_w("tcPr"))
                    span = max(1, _int(_val(tcpr, "gridSpan")) or 1)
                    merge = tcpr.find(_w("vMerge")) if tcpr is not None else None
                    continued = (
                        merge is not None and (merge.get(W_VAL) or "continue") == "continue"
                    )
                    content = self.blocks(cell)
                    # 세로로 합친 칸의 아랫부분은 빈 칸 — 병합은 단순하게.
                    cells.append([] if continued else content)
                    cells.extend([] for _ in range(span - 1))
                if any(cells):
                    rows.append(cells)
        finally:
            self.table_depth -= 1
        if not rows:
            return []
        if len(rows) == 1 and len(rows[0]) == 1:
            # 칸 하나짜리 표는 대개 글을 두른 상자다 — 안의 것을 본문으로.
            return list(rows[0][0])
        return [Table(rows)]

    def paragraph(self, element: ET.Element) -> list[Block]:
        ppr = element.find(W_PPR)
        style_id = _val(ppr, "pStyle") or self.styles.default
        info = self.styles.info(style_id)
        inline = Inline()
        self.inline(element, inline)

        heading = info.heading
        direct_outline = _int(_val(ppr, "outlineLvl"))
        if direct_outline is not None:
            heading = direct_outline + 1 if 0 <= direct_outline < 9 else None

        num_pr = ppr.find(_w("numPr")) if ppr is not None else None
        num_id = _val(num_pr, "numId") or info.num_id
        ilvl = _int(_val(num_pr, "ilvl"))
        if ilvl is None:
            ilvl = info.ilvl
        if ilvl is None and num_id:
            ilvl = self.numbering.level_of_style(num_id, style_id)
        numbered = (
            self.numbering.advance(num_id, ilvl or 0) if num_id and num_id != "0" else None
        )

        role, level, marker, prefix = "para", 0, "", ""
        if heading is not None:
            role, level = "heading", heading
            if numbered and numbered[0].fmt not in {"bullet", "none"} and numbered[1].strip():
                prefix = numbered[1].strip() + " "
        elif numbered and numbered[0].fmt != "none":
            spec, label, count = numbered
            role, level = "list", ilvl or 0
            depth = ilvl or 0
            if spec.fmt == "bullet":
                marker = "- "
            elif spec.fmt == "decimal" and re.fullmatch(
                rf"%{depth + 1}[.)]", spec.text.strip()
            ):
                marker = f"{count}. "
            else:
                marker = f"- {label.strip()} " if label.strip() else "- "

        out: list[Block] = []
        if not info.toc:
            caption = info.caption or inline.seq is not None
            out = self._split(inline, role, level, marker, prefix, caption, num_id or "")
        sect = ppr.find(_w("sectPr")) if ppr is not None else None
        if sect is not None:
            # 이 문단이 구역의 끝 — 그 안의 그림은 이 구역의 폭을 쓴다.
            self.sections.append(_text_width(sect))
        return out

    def _split(
        self,
        inline: Inline,
        role: str,
        level: int,
        marker: str,
        prefix: str,
        caption: bool,
        num_id: str,
    ) -> list[Block]:
        """문단 하나 → 블록들. **그림은 제 줄 하나로** — 글 사이에 그림이 있으면 문단을
        나눈다."""
        out: list[Block] = []
        group: list[tuple[str, Any]] = []
        seq_table = (inline.seq or "").lower() in TABLE_SEQ
        first = True

        def flush() -> None:
            nonlocal first
            if not group:
                return
            md, plain = _render_inline(group, bold=not (first and role == "heading"))
            group.clear()
            if not plain.strip():
                return
            if first and role == "heading" and prefix:
                md, plain = prefix + md, prefix + plain
            out.append(
                Text(
                    md=md,
                    plain=plain,
                    # 제목 · 목록의 표시는 첫 조각에만 — 그림 뒤의 글은 여느 문단이다.
                    role=role if first else "para",
                    level=level if first else 0,
                    marker=marker if first else "",
                    group=num_id if first else "",
                    caption=caption,
                    table_caption=caption and (seq_table or bool(TABLE_CAPTION.match(plain))),
                )
            )
            first = False

        for kind, payload in inline.items:
            if kind in {"t", "br", "link"}:
                group.append((kind, payload))
            elif kind == "fig":
                flush()
                out.append(payload)
            elif kind == "blocks":
                flush()
                out.extend(payload)
        flush()
        return out

    # --- 문단 안 ---------------------------------------------------------------

    def _suppressed(self) -> bool:
        return any(not one.result or one.kind in HIDDEN_FIELDS for one in self.fields)

    def _field_done(self, one: Field, inline: Inline) -> None:
        if one.kind == "SEQ":
            words = one.instr.split()
            inline.seq = words[1] if len(words) > 1 else ""

    def inline(self, parent: ET.Element, inline: Inline) -> None:
        for child in parent:
            tag = child.tag
            if tag in {W_PPR, W_RPR}:
                continue
            if tag == W_R:
                rpr = child.find(W_RPR)
                self.run(child, inline, bold=_on(rpr, "b"), hidden=_on(rpr, "vanish"))
            elif tag == _w("hyperlink"):
                self.hyperlink(child, inline)
            elif tag in TRANSPARENT:
                self.inline(child, inline)
            elif tag in DROPPED:
                continue
            elif tag == W_SDT:
                content = child.find(W_SDTCONTENT)
                if content is not None:
                    self.inline(content, inline)
            elif tag == _w("fldSimple"):
                simple = Field(child.get(_w("instr")) or "", result=True)
                self._field_done(simple, inline)
                if simple.kind not in HIDDEN_FIELDS:
                    self.inline(child, inline)
            elif tag in {_q(M, "oMathPara"), _q(M, "oMath")}:
                words = "".join(t.text or "" for t in child.iter(_q(M, "t")))
                if words and not self._suppressed():
                    inline.items.append(("t", (words, False)))
            elif tag == MC_ALT:
                choice = child.find(MC_CHOICE)
                if choice is not None:
                    self.inline(choice, inline)

    def hyperlink(self, element: ET.Element, inline: Inline) -> None:
        rel = self.rels.get(element.get(R_ID) or "")
        url = rel.target if rel is not None and rel.external else ""
        inner = Inline()
        self.inline(element, inner)
        if inner.seq is not None:
            inline.seq = inner.seq
        plain_only = all(kind == "t" for kind, _ in inner.items)
        if url and plain_only and re.match(r"^(https?:|mailto:)", url, re.IGNORECASE):
            words = "".join(text for _, (text, _bold) in inner.items)
            if words.strip():
                inline.items.append(("link", (words, url)))
            return
        inline.items.extend(inner.items)

    def run(self, element: ET.Element, inline: Inline, *, bold: bool, hidden: bool) -> None:
        for child in element:
            tag = child.tag
            if tag == _w("fldChar"):
                kind = child.get(_w("fldCharType"))
                if kind == "begin":
                    self.fields.append(Field())
                elif kind == "separate" and self.fields:
                    self.fields[-1].result = True
                    self._field_done(self.fields[-1], inline)
                elif kind == "end" and self.fields:
                    done = self.fields.pop()
                    if not done.result:
                        self._field_done(done, inline)
                continue
            if tag == _w("instrText"):
                if self.fields and not self.fields[-1].result:
                    self.fields[-1].instr += child.text or ""
                continue
            if tag == MC_ALT:
                choice = child.find(MC_CHOICE)
                if choice is not None:
                    self.run(choice, inline, bold=bold, hidden=hidden)
                continue
            if hidden or self._suppressed():
                continue
            if tag == W_T:
                inline.items.append(("t", (child.text or "", bold)))
            elif tag in {_w("tab"), _w("ptab")}:
                inline.items.append(("t", (" ", bold)))
            elif tag == _w("br"):
                if (child.get(_w("type")) or "textWrapping") == "textWrapping":
                    inline.items.append(("br", None))
            elif tag == _w("cr"):
                inline.items.append(("br", None))
            elif tag == _w("noBreakHyphen"):
                inline.items.append(("t", ("-", bold)))
            elif tag in {_w("footnoteReference"), _w("endnoteReference")}:
                kind = "f" if tag == _w("footnoteReference") else "e"
                mark = self._note_ref(kind, child.get(_w("id")) or "")
                if mark:
                    inline.items.append(("t", (mark, False)))
            elif tag == _w("drawing"):
                self.drawing(child, inline)
            elif tag == _w("pict"):
                self.vml(child, inline)
            elif tag == _w("object"):
                self.ole_object(child, inline)

    # --- 그림 ------------------------------------------------------------------

    def figure(self, kind: str, **values: Any) -> Figure:
        made = Figure(
            order=len(self.figures) + 1,
            kind=kind,
            section=len(self.sections),
            in_table=self.table_depth > 0,
            **values,
        )
        self.figures.append(made)
        return made

    def image(
        self,
        rid: str | None,
        inline: Inline,
        *,
        cx: int | None,
        alt: str,
        floating: bool,
        svg: str | None = None,
        ole: str = "",
    ) -> None:
        made = self.figure("image", cx=cx, alt=alt, floating=floating, svg=svg, ole=ole)
        rel = self.rels.get(rid or "")
        if not rid:
            if svg:
                made.short, made.reason = (
                    "SVG — PNG 사본이 없어 옮기지 않았습니다",
                    "SVG 만 있고 PNG 사본이 없습니다",
                )
            else:
                made.short = made.reason = "그림 파일을 가리키는 연결이 없어 옮기지 않았습니다"
        elif rel is None:
            made.short = made.reason = f"그림 연결({rid})이 문서에 없어 옮기지 않았습니다"
        elif rel.external:
            made.short = "문서 밖 파일에 이어진 그림 — 옮기지 않았습니다"
            made.reason = (
                "그림이 문서 안에 없고 바깥 파일에 이어져 있습니다(Word 의 「파일에 연결」)"
            )
        elif not self.package.has(rel.target):
            made.short = made.reason = "문서 안에 그 그림 파일이 없어 옮기지 않았습니다"
        else:
            made.part = rel.target
        inline.items.append(("fig", made))

    def drawing(self, element: ET.Element, inline: Inline) -> None:
        for holder in element:
            if holder.tag not in {_q(WP, "inline"), _q(WP, "anchor")}:
                continue
            floating = holder.tag == _q(WP, "anchor")
            extent = holder.find(_q(WP, "extent"))
            cx = _int(extent.get("cx")) if extent is not None else None
            alt = _alt_of(holder.find(_q(WP, "docPr")))
            data = holder.find(f"{_q(A, 'graphic')}/{_q(A, 'graphicData')}")
            if data is None:
                continue
            uri = data.get("uri") or ""
            if uri in {CHART, CHARTEX}:
                node = data.find(_q(CHART, "chart"))
                if node is None:
                    node = data.find(_q(CHARTEX, "chart"))
                made = self.figure("chart", cx=cx, alt=alt, floating=floating)
                made.title = self._chart_title(node.get(R_ID) if node is not None else None)
                made.short = "차트 — 원본 차트는 옮기지 않음"
                made.reason = "차트는 그림 파일이 아닙니다 — 원본 차트는 옮기지 않았습니다"
                inline.items.append(("fig", made))
                continue
            if uri == DGM:
                ids = data.find(_q(DGM, "relIds"))
                made = self.figure("smartart", cx=cx, alt=alt, floating=floating)
                made.texts = self._smartart_texts(
                    ids.get(_q(R, "dm")) if ids is not None else None
                )
                made.short = "SmartArt — 원본 도식은 옮기지 않음"
                made.reason = (
                    "SmartArt 는 그림 파일이 아닙니다 — 원본 도식은 옮기지 않았습니다"
                )
                inline.items.append(("fig", made))
                continue
            pictures = _find(data, frozenset({PIC_PIC}), frozenset({W_TXBX}))
            boxes = _find(data, frozenset({W_TXBX}))
            if uri in {WPG, WPC} and not pictures:
                made = self.figure("shapes", cx=cx, alt=alt, floating=floating)
                made.short = "Word 도형 — 옮기지 않음"
                made.reason = "Word 에서 그린 도형 묶음은 그림 파일이 아닙니다"
                inline.items.append(("fig", made))
            for picture in pictures:
                own = None
                if len(pictures) > 1:
                    ext = picture.find(f"{_q(PIC, 'spPr')}/{_q(A, 'xfrm')}/{_q(A, 'ext')}")
                    own = _int(ext.get("cx")) if ext is not None else None
                self.picture(
                    picture,
                    inline,
                    cx=own or cx,
                    alt=alt if len(pictures) == 1 else "",
                    floating=floating,
                )
            for box in boxes:
                self.textbox(box, inline)

    def picture(
        self, picture: ET.Element, inline: Inline, *, cx: int | None, alt: str, floating: bool
    ) -> None:
        blip = picture.find(f"{_q(PIC, 'blipFill')}/{_q(A, 'blip')}")
        rid = None if blip is None else (blip.get(R_EMBED) or blip.get(R_LINK))
        svg = None
        if blip is not None:
            # SVG 를 넣으면 Word 는 PNG 사본을 함께 둔다 — `a:blip` 이 PNG,
            # `asvg:svgBlip` 이 SVG.
            found = next(iter(blip.iter(_q(ASVG, "svgBlip"))), None)
            rel = self.rels.get(found.get(R_EMBED) or "") if found is not None else None
            if rel is not None and not rel.external:
                svg = rel.target
        self.image(rid, inline, cx=cx, alt=alt, floating=floating, svg=svg)

    def textbox(self, box: ET.Element, inline: Inline) -> None:
        inner = self.blocks(box)
        if inner:
            self.textboxes += 1
            inline.items.append(("blocks", inner))

    def vml(self, element: ET.Element, inline: Inline, ole: str = "") -> None:
        """옛 문서의 그림(VML) — `v:imagedata r:id`. 개체(Visio · 수식)의 미리 보기 그림도
        이것이다."""
        for shape in _find(element, VML_SHAPES, frozenset({W_TXBX})):
            data = shape.find(_q(V, "imagedata"))
            if data is not None:
                rid = data.get(R_ID) or data.get(_q(OFFICE, "relid"))
                style = shape.get("style") or ""
                self.image(
                    rid,
                    inline,
                    cx=_vml_width(style),
                    alt=" ".join(
                        (shape.get("alt") or data.get(_q(OFFICE, "title")) or "").split()
                    ),
                    floating="position:absolute" in style.replace(" ", "").lower(),
                    ole=ole,
                )
            for box in _find(shape, frozenset({W_TXBX})):
                self.textbox(box, inline)

    def ole_object(self, element: ET.Element, inline: Inline) -> None:
        found = element.find(_q(OFFICE, "OLEObject"))
        prog = (found.get("ProgID") or "") if found is not None else ""
        for child in element:
            if child.tag == _w("drawing"):
                self.drawing(child, inline)
        self.vml(element, inline, ole=prog)


def _render_inline(items: list[tuple[str, Any]], *, bold: bool) -> tuple[str, str]:
    """글 조각 → (마크다운, 맨 글). 이어진 굵은 조각은 하나로 묶고, 앞뒤 빈칸은 `**` 밖으로."""
    merged: list[list[Any]] = []
    plain: list[str] = []
    for kind, payload in items:
        if kind == "br":
            merged.append(["\n", False, "br"])
            plain.append("\n")
        elif kind == "link":
            words, url = payload
            safe = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            label = words.replace("[", "(").replace("]", ")")
            merged.append([f"[{label}]({safe})", False, "link"])
            plain.append(words)
        else:
            text, strong = payload
            strong = strong and bold
            plain.append(text)
            if merged and merged[-1][2] is None and merged[-1][1] == strong:
                merged[-1][0] += text
            else:
                merged.append([text, strong, None])
    out: list[str] = []
    for text, strong, _kind in merged:
        core = text.strip()
        if strong and core:
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()) :]
            out.append(f"{lead}**{core}**{trail}")
        else:
            out.append(text)
    return "".join(out).strip(), "".join(plain).strip()


# --------------------------------------------------------------------------
# 캡션 · 앞 문단 · 그림 파일
# --------------------------------------------------------------------------


def _containers(blocks: list[Any]) -> Iterator[list[Any]]:
    yield blocks
    for one in blocks:
        if isinstance(one, Table):
            for row in one.rows:
                for cell in row:
                    yield from _containers(cell)


def _caption_candidate(block: Any) -> bool:
    return (
        isinstance(block, Text)
        and block.caption
        and not block.used
        and not block.table_caption
    )


def attach_captions(body: list[Block]) -> None:
    """그림 바로 뒤(또는 앞)의 캡션 문단을 그 그림의 캡션으로. **문서의 버릇을 먼저 본다** —
    캡션을 그림 아래에 다는 문서와 위에 다는 문서가 있다. 둘 다 붙을 수 있는 그림이 이웃 그림의
    캡션을 가져가지 않게 많은 쪽을 먼저 쓴다(같으면 아래)."""
    after = before = 0
    for blocks in _containers(body):
        for index, one in enumerate(blocks):
            if isinstance(one, Figure):
                after += index + 1 < len(blocks) and _caption_candidate(blocks[index + 1])
                before += index > 0 and _caption_candidate(blocks[index - 1])
    steps = (1, -1) if after >= before else (-1, 1)
    for blocks in _containers(body):
        for index, one in enumerate(blocks):
            if not isinstance(one, Figure) or one.caption:
                continue
            for step in steps:
                near = index + step
                if 0 <= near < len(blocks) and _caption_candidate(blocks[near]):
                    one.caption, one.caption_from = blocks[near].plain, "캡션 문단"
                    blocks[near].used = True
                    break


def _walk(blocks: list[Any]) -> Iterator[Any]:
    """읽는 차례대로 — 표는 칸 안으로 내려간다."""
    for one in blocks:
        if isinstance(one, Table):
            for row in one.rows:
                for cell in row:
                    yield from _walk(cell)
        else:
            yield one


def _tail(text: str, size: int = 80) -> str:
    one = " ".join(text.split())
    return one if len(one) <= size else "…" + one[-size:]


def mark_before(body: list[Block]) -> None:
    """그림마다 **바로 앞 문단의 글** — 초안과 원본에서 자리를 맞춰 보는 손잡이."""
    last = ""
    for one in _walk(body):
        if isinstance(one, Text) and not one.used:
            last = one.plain
        elif isinstance(one, Figure):
            one.before = _tail(last)


def sniff(data: bytes) -> str:
    """머리 바이트로 형식을 본다 — 확장자는 믿지 않는다."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF":
        return "emf"
    if data[:4] in {b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00"}:
        return "wmf"
    if data[:4] in {b"II*\x00", b"MM\x00*"}:
        return "tiff"
    if data[:2] == b"BM":
        return "bmp"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if b"<svg" in data[:4096]:
        return "svg"
    return ""


def _gunzip(data: bytes) -> bytes:
    """`.emz` · `.wmz`(gzip 으로 싼 EMF · WMF) — 풀린 크기를 상한으로 묶는다."""
    unpack = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = unpack.decompress(data, MAX_PART + 1)
    except zlib.error as failure:
        raise Stop(f"gzip 을 풀 수 없습니다 ({failure})") from None
    if len(out) > MAX_PART or unpack.unconsumed_tail:
        raise Stop("gzip 을 풀면 너무 큽니다")
    return out


# --- PowerShell(GDI+)로 PNG 로 ------------------------------------------------------

POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Drawing
$long = __LONG__
$utf8 = New-Object System.Text.UTF8Encoding($false)
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  if ($line.Trim() -eq '') { continue }
  $parts = $line.Split("`t")
  $id = $parts[0]
  try {
    $bytes = [Convert]::FromBase64String($parts[1])
    $source = New-Object System.IO.MemoryStream(,$bytes)
    $img = [System.Drawing.Image]::FromStream($source)
    try {
      $w = [double]$img.Width; $h = [double]$img.Height
      if ($w -le 0 -or $h -le 0) { throw 'empty image' }
      $scale = 1.0
      if ($img -is [System.Drawing.Imaging.Metafile]) { $scale = $long / [Math]::Max($w, $h) }
      $tw = [int][Math]::Max(1, [Math]::Round($w * $scale))
      $th = [int][Math]::Max(1, [Math]::Round($h * $scale))
      $bmp = New-Object System.Drawing.Bitmap($tw, $th)
      try {
        $bmp.SetResolution(96, 96)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        try {
          $g.Clear([System.Drawing.Color]::White)
          $g.SmoothingMode = 'AntiAlias'
          $g.InterpolationMode = 'HighQualityBicubic'
          $g.PixelOffsetMode = 'HighQuality'
          $g.TextRenderingHint = 'AntiAliasGridFit'
          $g.DrawImage($img, 0, 0, $tw, $th)
        } finally { $g.Dispose() }
        $out = New-Object System.IO.MemoryStream
        $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
        [Console]::Out.WriteLine($id + "`tok`t" + [Convert]::ToBase64String($out.ToArray()))
      } finally { $bmp.Dispose() }
    } finally { $img.Dispose() }
  } catch {
    $said = $utf8.GetBytes($_.Exception.Message)
    [Console]::Out.WriteLine($id + "`terr`t" + [Convert]::ToBase64String($said))
  }
}
"""
"""표준 입력으로 `<번호>\\t<base64>` 를 줄마다 받아 `<번호>\\tok|err\\t<base64>` 로
답한다 — 파일 경로를 주고받지 않으니 WSL ↔ Windows 의 경로 바꾸기가 필요 없다.
메타파일(EMF · WMF)은 긴 변 `LONG_SIDE` 로 키워 흰 바탕에 그린다(투명 바탕은 어두운 화면에서
선이 사라진다)."""


def find_powershell() -> str | None:
    """Windows 의 PowerShell — Windows 면 PATH, WSL 이면 `/mnt/c/…`."""
    if sys.platform == "win32":
        found = shutil.which("powershell.exe") or shutil.which("powershell")
        if found:
            return found
        root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
        candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return str(candidate) if candidate.is_file() else None
    wsl = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(wsl) if wsl.is_file() else None


def to_png(images: dict[str, bytes]) -> tuple[dict[str, bytes | str], str]:
    """그림들을 PNG 로 — 한 번 띄운 PowerShell 에 몰아 준다. (번호 → PNG 바이트 또는 못 바꾼
    이유, 쓴 변환기)."""
    if not images:
        return {}, ""
    shell = find_powershell()
    if shell is None:
        why = "PowerShell 이 없습니다 — EMF · WMF 는 Windows(또는 WSL)에서만 PNG 로 바꿉니다"
        return dict.fromkeys(images, why), ""
    script = POWERSHELL_SCRIPT.replace("__LONG__", str(LONG_SIDE))
    command = [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
    ]
    payload = "".join(
        f"{key}\t{base64.b64encode(data).decode('ascii')}\n" for key, data in images.items()
    )
    try:
        done = subprocess.run(
            command,
            input=payload.encode("ascii"),
            capture_output=True,
            timeout=CONVERT_TIMEOUT,
            check=False,
            cwd=str(Path(shell).parent),
            # MCP 서버는 창 없이 돈다 — 콘솔 창이 번쩍 뜨지 않게(Windows).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        return dict.fromkeys(images, f"PowerShell 을 돌리지 못했습니다 ({failure})"), shell
    out: dict[str, bytes | str] = {}
    for line in done.stdout.decode("ascii", "replace").splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 3 or parts[0] not in images:
            continue
        key, status, body = parts
        try:
            data = base64.b64decode(body)
        except ValueError:
            continue
        if status == "ok" and sniff(data) == "png":
            out[key] = data
        else:
            out[key] = (
                data.decode("utf-8", "replace").strip() or "PowerShell 이 바꾸지 못했습니다"
            )
    said = done.stderr.decode("utf-8", "replace").strip().splitlines()
    for key in images:
        out.setdefault(
            key, "PowerShell 이 답하지 않았습니다" + (f" ({said[-1][:200]})" if said else "")
        )
    return out, shell


def _bring_files(package: Package, figures: list[Figure]) -> tuple[dict[str, bytes], str]:
    """쓰인 그림만 읽는 차례대로 `image1.png` … — 같은 그림(같은 바이트)은 한 파일."""
    loaded: dict[str, tuple[bytes, str, str]] = {}
    pending: dict[str, bytes] = {}
    for one in figures:
        if one.part is None or one.part in loaded:
            continue
        try:
            data = package.read(one.part)
            fmt = sniff(data)
            if fmt == "gzip":
                data = _gunzip(data)
                fmt = sniff(data)
        except Stop as stop:
            one.short, one.reason = "그림 파일을 풀 수 없어 옮기지 않았습니다", str(stop)
            continue
        digest = hashlib.sha256(data).hexdigest()
        loaded[one.part] = (data, fmt, digest)
        if fmt not in KEEP and fmt != "svg":
            pending[digest] = data
    converted, shell = to_png(pending)
    files: dict[str, bytes] = {}
    names: dict[str, str] = {}
    for one in figures:
        if one.part is None or one.part not in loaded or one.reason:
            continue
        data, fmt, digest = loaded[one.part]
        one.format = fmt or "알 수 없음"
        label = fmt.upper() if fmt else "알 수 없는 형식"
        if fmt == "svg":
            one.short = "SVG — PNG 사본이 없어 옮기지 않았습니다"
            one.reason = (
                "SVG 만 있습니다 — 플랫폼은 PNG · JPEG · GIF · WebP 만 그림으로 받습니다"
            )
            continue
        ext = KEEP.get(fmt)
        if ext is None:
            result = converted.get(digest)
            if not isinstance(result, bytes):
                one.short = f"{label} — 바꾸지 못해 옮기지 않았습니다"
                one.reason = f"{label} 를 PNG 로 바꾸지 못했습니다: {result}"
                continue
            data, ext, one.converted = result, "png", f"{label}→PNG"
        if digest not in names:
            names[digest] = f"image{len(names) + 1}.{ext}"
            files[names[digest]] = data
        one.file = f"{MEDIA}/{names[digest]}"
    return files, shell


# --------------------------------------------------------------------------
# 초안 쓰기
# --------------------------------------------------------------------------


def _width(cx: int | None, body: int) -> int:
    if not cx or cx <= 0 or body <= 0:
        return DEFAULT_WIDTH
    tens = math.floor(cx / body * 10 + 0.5)
    return max(10, min(100, tens * 10))


def _finish(figures: list[Figure], sections: list[int]) -> None:
    for one in figures:
        body = sections[min(one.section, len(sections) - 1)]
        one.width = _width(one.cx, body)
        if not one.caption:
            if one.alt:
                one.caption, one.caption_from = one.alt, "대체 텍스트"
            elif one.kind == "chart" and one.title:
                one.caption, one.caption_from = one.title, "차트 제목"
            elif one.kind == "image":
                one.caption, one.caption_from = f"그림 {one.order}", "차례"
        caption = _caption_text(one.caption)
        if one.file:
            one.line = f"![{caption}]({one.file}?w={one.width})"
            continue
        default = {"chart": "차트", "smartart": "SmartArt", "shapes": "도형"}.get(
            one.kind, "그림"
        )
        if caption:
            one.line = f"> 그림: {caption} ({one.short})"
        else:
            # 「> 그림: 차트 (원본 차트는 옮기지 않음)」 — 종류가 곧 이름이면 되풀이하지
            # 않는다.
            rest = (
                one.short.split(" — ", 1)[-1] if one.short.startswith(default) else one.short
            )
            one.line = f"> 그림: {default} ({rest})"
        if one.texts:
            one.line += "\n> 글: " + " · ".join(one.texts)


def _cell_text(cell: list[Any], moved: list[Figure]) -> str:
    parts: list[str] = []
    for one in cell:
        if isinstance(one, Text):
            if not one.used:
                parts.append((one.marker + one.md).replace("\n", " "))
        elif isinstance(one, Figure):
            moved.append(one)
            parts.append(f"(그림: {_caption_text(one.caption) or '아래'} — 표 아래)")
        elif isinstance(one, Table):
            for row in one.rows:
                parts.append(
                    " · ".join(text for text in (_cell_text(c, moved) for c in row) if text)
                )
    return " / ".join(part for part in parts if part).replace("|", "\\|")


def _table_md(table: Table) -> tuple[str, list[Figure]]:
    moved: list[Figure] = []
    rows = [[_cell_text(cell, moved) for cell in row] for row in table.rows]
    width = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        row = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(row) + " |")
        if index == 0:
            lines.append("|" + " --- |" * width)
    return "\n".join(lines), moved


def render(body: list[Block], notes: list[tuple[int, str]]) -> str:
    """블록 → 마크다운. 목록 항목끼리는 한 줄씩(빽빽한 목록), 그 밖은 빈 줄로 가른다. 목록의
    들여쓰기는 앞 항목보다 한 단계까지만 깊어진다 — Word 의 단계를 그대로 쓰면 첫 항목이 들여
    쓰여 코드 블록이 된다. 문단 안 줄바꿈은 `\\` + 줄바꿈(마크다운의 강제 줄바꿈)."""
    chunks: list[tuple[str, bool]] = []
    """(글, 앞 목록에 이어 붙나)"""
    depth, group = -1, ""

    for one in body:
        if isinstance(one, Text):
            if one.used:
                continue
            if one.role == "list":
                fresh = depth < 0 or (one.level == 0 and one.group != group)
                depth = min(one.level, 0 if fresh else depth + 1)
                group = one.group if depth == 0 else group
                text = one.md.replace("\n", "\\\n")
                chunks.append(("    " * depth + one.marker + text, not fresh))
                continue
            depth = -1
            if one.role == "heading":
                chunks.append(
                    ("#" * min(one.level, 6) + " " + one.md.replace("\n", " "), False)
                )
            else:
                chunks.append((one.md.replace("\n", "\\\n"), False))
        elif isinstance(one, Figure):
            depth = -1
            chunks.append((one.line, False))
        else:
            depth = -1
            table, moved = _table_md(one)
            chunks.append((table, False))
            chunks.extend((figure.line, False) for figure in moved)
    out = ""
    for index, (text, joined) in enumerate(chunks):
        if index:
            out += "\n" if joined else "\n\n"
        out += text
    if notes:
        out += "\n\n" + "\n".join(f"[^{number}]: {text}" for number, text in notes)
    return out + "\n"


# --------------------------------------------------------------------------
# 꺼내기
# --------------------------------------------------------------------------


@dataclass
class Extracted:
    folder: Path
    draft_chars: int
    figures: list[dict[str, Any]]
    not_moved: list[dict[str, Any]]
    notes: list[str]


def folder_name(name: str) -> str:
    """출력 폴더 이름 — 경로 글자를 지운다(`../` 로 밖을 가리킬 수 없다)."""
    clean = re.sub(r"[^\w가-힣.-]+", "-", name).strip("-.")
    return clean or "문서"


def default_out(source: Path) -> Path:
    """작업 폴더의 원천이면 `<작업>/docs/<이름>`, 아니면 원천 옆 `<이름>-초안`."""
    source = source.resolve()
    work = source.parent.parent
    if source.parent.name == sp_work.SOURCES and (work / sp_work.WORK).exists():
        return work / sp_work.DOCS / folder_name(source.stem)
    return source.parent / f"{folder_name(source.stem)}-초안"


def _figure_entry(one: Figure) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "order": one.order,
        "kind": one.kind,
        "moved": one.file is not None,
        "file": one.file,
        "original": one.part,
        "format": one.format or None,
        "converted": one.converted or None,
        "width": one.width if one.file else None,
        "caption": one.caption,
        "caption_from": one.caption_from or None,
        "before": one.before,
        "line": one.line,
        "in_table": one.in_table,
        "floating": one.floating,
    }
    if one.svg:
        entry["svg"] = one.svg
    if one.ole:
        entry["ole"] = one.ole
    if one.file is None:
        entry["reason"] = one.reason
    return entry


def _check_out(out: Path) -> None:
    """이 도구가 만든 폴더만 다시 쓴다 — 사람이 둔 파일을 지우지 않는다."""
    if not out.exists():
        return
    if not out.is_dir():
        raise Stop(f"출력 자리에 파일이 있습니다: {out}")
    if not any(out.iterdir()):
        return
    try:
        mark = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mark = None
    if not isinstance(mark, dict) or mark.get("format") != FORMAT:
        raise Stop(
            f"{out} 에 이 도구가 만들지 않은 파일이 있습니다 — 다른 이름(name)으로 꺼내세요"
        )


def extract(source: Path, out: Path) -> Extracted:
    """.docx 하나 → `out/` 에 `draft.md` · `media/` · `manifest.json`. 같은 자리에 다시 꺼내면
    새로 만든다(그 폴더는 이 도구가 만든 것이어야 한다)."""
    _check_out(out)
    with Package(source) as package:
        try:
            reader = Reader(package)
        except RecursionError:
            raise Stop(f"{source.name}: 문서의 짜임이 너무 깊어 읽지 않았습니다") from None
        attach_captions(reader.body)
        mark_before(reader.body)
        files, shell = _bring_files(package, reader.figures)
    _finish(reader.figures, reader.sections)
    notes = [(number, reader.note_texts[key]) for key, number in reader.note_numbers.items()]
    draft = render(reader.body, sorted(notes))

    remarks = list(dict.fromkeys(reader.remarks))
    if reader.textboxes:
        remarks.append(f"글상자 {reader.textboxes}개의 글을 그 자리에 문단으로 옮겼습니다")
    figures = [_figure_entry(one) for one in reader.figures]
    not_moved = [
        {key: entry[key] for key in ("order", "kind", "caption", "before", "line", "reason")}
        for entry in figures
        if not entry["moved"]
    ]
    manifest = {
        "format": FORMAT,
        "source": source.name,
        "source_sha256": _sha256(source),
        "made_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "draft": DRAFT,
        "converter": shell or None,
        "figures": figures,
        "not_moved": not_moved,
        "notes": remarks,
    }

    # 새 폴더에 다 쓴 뒤 바꿔 끼운다 — 중간에 멈춰도 옛 결과가 반쯤 지워진 채 남지 않게.
    fresh = out.parent / f".{out.name}.새로"
    old = out.parent / f".{out.name}.옛것"
    for leftover in (fresh, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    (fresh / MEDIA).mkdir(parents=True)
    for name, data in files.items():
        (fresh / MEDIA / name).write_bytes(data)
    (fresh / DRAFT).write_text(draft, encoding="utf-8")
    pipeline._write(fresh / MANIFEST, manifest)
    try:
        if out.exists():
            out.rename(old)
        fresh.rename(out)
    except OSError as failure:
        raise Stop(
            f"{out} 를 바꾸지 못했습니다 — 그 폴더를 연 창을 닫고 다시 ({failure})"
        ) from None
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    return Extracted(out, len(draft), figures, not_moved, remarks)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def summary(result: Extracted, shown: str) -> str:
    moved = [one for one in result.figures if one["moved"]]
    lines = [
        f"꺼냈습니다: {shown}",
        f"- 초안 {DRAFT} ({result.draft_chars:,}자)",
        f"- 그림 {len(result.figures)}개 — 옮김 {len(moved)} · "
        f"못 옮김 {len(result.not_moved)}",
    ]
    lines += [
        f"  {one['order']}. {one['file']} · 폭 {one['width']}% · {one['caption']}"
        + (f" ({one['converted']})" if one["converted"] else "")
        for one in moved
    ]
    lines += [f"  {one['order']}. 못 옮김 — {one['reason']}" for one in result.not_moved]
    lines += [f"- {note}" for note in result.notes]
    lines.append(
        "플랫폼에는 아무것도 올리지 않았습니다. 다음: 그림마다 플랫폼 MCP "
        "attachment_upload_prepare → curl, 그림 줄의 경로를 photo:<첨부 id> 로, "
        "다듬어 document_write"
    )
    return "\n".join(lines)


def cmd_docx(source: Path, out: Path | None = None) -> str:
    if not source.is_file():
        raise Stop(f"원천이 없습니다: {source}")
    target = out or default_out(source)
    return summary(extract(source, target), str(target))
