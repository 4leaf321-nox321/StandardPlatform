#!/usr/bin/env python3
"""표(CSV) → 실행 폴더 — **규칙으로** 옮기는 정제.

    python sp_table.py <대응.json> <원천.csv> runs/2026-09-13-plm-models

한 행에 여러 타입이 섞인 표(프로젝트 · 과제 · 모델이 한 줄에)를 **대응 파일**대로 타입마다
나눠 실행 폴더를 만든다. 그 뒤는 여느 실행과 같다 — `sp_pipeline.py validate · preview ·
apply`.

- 행마다 AI 가 판단하지 않는다. 같은 원천과 같은 대응이면 **늘 같은 결과**가 나온다.
- 같은 식별자가 여러 행에 나오면 한 객체로 모은다. **행마다 값이 다르면 짐작하지 않고**
  `unresolved.json` 에 올린다 — 그 칸이 사실은 윗단계가 아니라 행의 속성이라는 신호다.
- 이름 안에 뜻이 박힌 코드(`모델_지역_버전_사업자`)는 **해석기**로 조각을 칸에 나눈다.
  못 나눈 행은 넣되 조각 칸을 비우고 보고서에 올린다.
- 보고서(`table-report.txt`)는 값을 **가린 패턴**(영문 A · 숫자 9 · 한글 가)으로만 적는다 —
  사내 데이터를 들고 나오지 않고도 결과를 옮겨 규칙을 고칠 수 있다.

대응 파일의 모양은 같은 폴더의 `AGENTS.md` 가 정본이다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import sp_pipeline as pipeline

FORMAT = "sp-table/1"
REPORT = "table-report.txt"
ENCODINGS = ("utf-8-sig", "cp949")
"""엑셀이 저장한 CSV 는 대개 둘 중 하나다 — BOM 붙은 UTF-8, 또는 한글 윈도의 CP949."""
DATE_RE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$")
BLANK = "<blank>"
DATE = "<date>"
TOP = 10

Stop = pipeline.Stop


def mask(value: str) -> str:
    """값을 모양만 남긴다 — `SM-F907N_KOR` → `AA-A999A_AAA`."""
    out: list[str] = []
    for char in value:
        if "가" <= char <= "힣" or "ㄱ" <= char <= "ㆎ":
            out.append("가")
        elif char.isdigit():
            out.append("9")
        elif char.isalpha():
            out.append("A")
        else:
            out.append(char)
    return "".join(out)


def _top(counter: Counter[str], limit: int = TOP) -> str:
    shown = " · ".join(f"{name} {count}" for name, count in counter.most_common(limit))
    rest = len(counter) - limit
    return shown + (f" · 외 {rest}종" if rest > 0 else "")


# --------------------------------------------------------------------------
# 원천 읽기
# --------------------------------------------------------------------------


@dataclass
class Table:
    name: str
    encoding: str
    header: list[str]
    rows: list[tuple[int, dict[str, str]]]
    """(시트의 행 번호 — 머리글이 1, 값) — 출처(`_source.row`)에 그대로 쓴다."""
    blank_rows: int
    sha256: str


def read_table(path: Path, *, encoding: str = "auto", delimiter: str = ",") -> Table:
    try:
        raw = path.read_bytes()
    except OSError as failure:
        raise Stop(f"원천을 읽을 수 없습니다: {path} ({failure})") from failure
    tried = ENCODINGS if encoding == "auto" else (encoding,)
    for name in tried:
        try:
            text = raw.decode(name)
        except UnicodeDecodeError:
            continue
        break
    else:
        raise Stop(
            f"{path.name}: 글자를 읽을 수 없습니다({', '.join(tried)}) — "
            "대응 파일의 source.encoding 에 인코딩을 적으세요"
        )
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = [one.strip() for one in next(reader, [])]
    if not any(header):
        raise Stop(f"{path.name}: 첫 줄(머리글)이 비어 있습니다")
    doubled = sorted(name for name, count in Counter(header).items() if count > 1 and name)
    if doubled:
        raise Stop(f"{path.name}: 머리글에 같은 이름이 둘 이상입니다 — {', '.join(doubled)}")
    rows: list[tuple[int, dict[str, str]]] = []
    blank = 0
    for number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            blank += 1
            continue
        if len(record) > len(header) and any(cell.strip() for cell in record[len(header) :]):
            raise Stop(f"{path.name} {number}행: 머리글보다 칸이 많습니다({len(record)}칸)")
        rows.append(
            (
                number,
                {
                    name: (record[i] if i < len(record) else "")
                    for i, name in enumerate(header)
                },
            )
        )
    return Table(
        name=path.name,
        encoding=name,
        header=header,
        rows=rows,
        blank_rows=blank,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


# --------------------------------------------------------------------------
# 해석기 — 이름 안에 박힌 조각을 칸으로
# --------------------------------------------------------------------------


@dataclass
class Slot:
    name: str
    optional: bool = False
    values: set[str] | None = None
    pattern: re.Pattern[str] | None = None
    kinds: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)

    def match(self, token: str) -> tuple[bool, str | None]:
        if self.values is not None and token not in self.values:
            return False, None
        if self.pattern is not None and not self.pattern.search(token):
            return False, None
        if not self.kinds:
            return True, None
        # 종류는 **적은 차례대로** 본다 — 먼저 맞는 것이 그 조각의 종류다.
        for kind, pattern in self.kinds:
            if pattern.search(token):
                return True, kind
        return False, None


@dataclass
class Parsed:
    slots: dict[str, tuple[str, str | None]]
    """조각 이름 → (값, 종류)."""
    failure: str = ""
    """비어 있으면 읽힌 것. `못 나눔` · `두 갈래` 는 해석 실패, `검증:` 은 규칙 위반."""


@dataclass
class ParserStats:
    total: int = 0
    ok: int = 0
    failures: dict[str, Counter[str]] = field(default_factory=dict)
    kinds: dict[str, Counter[str]] = field(default_factory=dict)
    values: dict[str, Counter[str]] = field(default_factory=dict)
    present: Counter[str] = field(default_factory=Counter)


class Parser:
    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        dictionaries: dict[str, set[str]],
        header: list[str],
    ) -> None:
        self.name = name
        self.column = _column(spec, header, where=f"parsers.{name}")
        self.separator = str(spec.get("separator") or "_")
        self.rare_below = int(spec.get("rare_below") or 0)
        self.slots: list[Slot] = []
        for index, one in enumerate(spec.get("slots") or [], start=1):
            where = f"parsers.{name}.slots[{index}]"
            if not isinstance(one, dict) or not one.get("name"):
                raise Stop(f"대응 파일: {where} 에 name 이 없습니다")
            values: set[str] | None = None
            if "values" in one:
                values = {str(value) for value in one["values"]}
            if "dictionary" in one:
                wanted = str(one["dictionary"])
                if wanted not in dictionaries:
                    raise Stop(
                        f"대응 파일: {where} 의 사전 {wanted!r} 가 dictionaries 에 없습니다"
                    )
                values = (values or set()) | dictionaries[wanted]
            self.slots.append(
                Slot(
                    name=str(one["name"]),
                    optional=bool(one.get("optional")),
                    values=values,
                    pattern=_regex(one.get("pattern"), where),
                    kinds=[
                        (str(kind), _compiled(pattern, where))
                        for kind, pattern in (one.get("kinds") or {}).items()
                    ],
                )
            )
        if not self.slots:
            raise Stop(f"대응 파일: parsers.{name} 에 slots 가 없습니다")
        self.slot_names = {slot.name for slot in self.slots}
        self.checks: list[dict[str, Any]] = []
        for index, check in enumerate(spec.get("checks") or [], start=1):
            where = f"parsers.{name}.checks[{index}]"
            slot = str(check.get("slot") or check.get("require") or "")
            if slot not in self.slot_names:
                raise Stop(f"대응 파일: {where} 의 조각 {slot!r} 가 slots 에 없습니다")
            if "suffix_of" in check:
                _column({"column": check["suffix_of"]}, header, where=where)
            elif "require" in check:
                _column({"column": check.get("when_column")}, header, where=where)
            else:
                raise Stop(f"대응 파일: {where} 는 suffix_of 나 require 중 하나여야 합니다")
            self.checks.append(check)
        self.stats = ParserStats()
        self._cache: dict[int, Parsed] = {}

    def _assign(self, tokens: list[str], at: int, slot: int) -> list[dict[str, Any]]:
        if slot == len(self.slots):
            return [{}] if at == len(tokens) else []
        current = self.slots[slot]
        found: list[dict[str, Any]] = []
        if current.optional:
            found.extend(self._assign(tokens, at, slot + 1))
        if at < len(tokens):
            ok, kind = current.match(tokens[at])
            if ok:
                for rest in self._assign(tokens, at + 1, slot + 1):
                    found.append({current.name: (tokens[at], kind), **rest})
        return found

    def parse(self, number: int, row: dict[str, str]) -> Parsed:
        if number in self._cache:
            return self._cache[number]
        text = row[self.column].strip()
        parsed = self._parse(text, row)
        self._cache[number] = parsed
        self._count(text, parsed)
        return parsed

    def _parse(self, text: str, row: dict[str, str]) -> Parsed:
        if not text:
            return Parsed({}, "비어 있음")
        tokens = text.split(self.separator)
        ways = self._assign(tokens, 0, 0)
        if not ways:
            return Parsed({}, f"못 나눔(조각 {len(tokens)}개)")
        if len(ways) > 1:
            return Parsed({}, "두 갈래로 읽힘")
        slots = ways[0]
        for check in self.checks:
            if "suffix_of" in check:
                name = str(check["slot"])
                if name not in slots:
                    continue
                joiner = str(check.get("joiner", self.separator))
                other = row[str(check["suffix_of"])].strip()
                if not other.endswith(f"{joiner}{slots[name][0]}"):
                    return Parsed({}, f"검증: {name} 이(가) {check['suffix_of']} 의 끝과 다름")
            else:
                name = str(check["require"])
                when = row[str(check["when_column"])].strip()
                if when in {str(one) for one in check.get("in") or []} and name not in slots:
                    return Parsed(
                        {}, f"검증: {check['when_column']} 이(가) {when} 인데 {name} 없음"
                    )
        return Parsed(slots)

    def _count(self, text: str, parsed: Parsed) -> None:
        stats = self.stats
        stats.total += 1
        if parsed.failure:
            stats.failures.setdefault(parsed.failure, Counter())[mask(text)] += 1
            return
        stats.ok += 1
        for name, (value, kind) in parsed.slots.items():
            stats.present[name] += 1
            stats.values.setdefault(name, Counter())[value] += 1
            if kind is not None:
                stats.kinds.setdefault(name, Counter())[kind] += 1

    def report(self) -> list[str]:
        stats = self.stats
        failed = sum(sum(one.values()) for one in stats.failures.values())
        lines = [
            f"[해석 {self.name}] {self.column} {stats.total}행"
            f" · 읽음 {stats.ok} · 못 읽음 {failed}"
        ]
        for reason, patterns in sorted(stats.failures.items()):
            lines.append(f"  {reason} {sum(patterns.values())}: {_top(patterns)}")
        for slot in self.slots:
            values = stats.values.get(slot.name, Counter())
            head = f"  조각 {slot.name}: 있음 {stats.present[slot.name]}"
            if slot.optional:
                head += f" · 없음 {stats.ok - stats.present[slot.name]}"
            head += f" · 값 {len(values)}종"
            if slot.name in stats.kinds:
                head += f" — {_top(stats.kinds[slot.name])}"
            lines.append(head)
            if self.rare_below:
                rare = Counter(
                    {
                        value: count
                        for value, count in values.items()
                        if count < self.rare_below
                    }
                )
                if rare:
                    shapes: Counter[str] = Counter()
                    for value, count in rare.items():
                        shapes[mask(value)] += count
                    lines.append(
                        f"    {self.rare_below}행 미만인 값 {len(rare)}종"
                        f"({sum(rare.values())}행): {_top(shapes)}"
                    )
        return lines


def _compiled(pattern: Any, where: str) -> re.Pattern[str]:
    try:
        return re.compile(str(pattern))
    except re.error as failure:
        raise Stop(
            f"대응 파일: {where} 의 정규식 {pattern!r} 가 틀렸습니다 ({failure})"
        ) from None


def _regex(pattern: Any, where: str) -> re.Pattern[str] | None:
    return None if pattern in (None, "") else _compiled(pattern, where)


# --------------------------------------------------------------------------
# 대응 — 칸 하나를 행에서 어떻게 얻나
# --------------------------------------------------------------------------


def _column(spec: dict[str, Any], header: list[str], *, where: str) -> str:
    name = str(spec.get("column") or "").strip()
    if not name:
        raise Stop(f"대응 파일: {where} 에 column 이 없습니다")
    if name not in header:
        raise Stop(
            f"대응 파일: {where} 의 열 {name!r} 가 원천에 없습니다"
            f" — 원천의 열: {', '.join(header)}"
        )
    return name


@dataclass
class FieldStats:
    problems: dict[str, Counter[str]] = field(default_factory=dict)


class Mapping:
    def __init__(self, spec: dict[str, Any], table: Table) -> None:
        if spec.get("format") != FORMAT:
            raise Stop(f"대응 파일: format 이 {FORMAT} 가 아닙니다 ({spec.get('format')!r})")
        self.spec = spec
        self.table = table
        dictionaries = {
            str(name): {str(value) for value in values}
            for name, values in (spec.get("dictionaries") or {}).items()
        }
        self.parsers = {
            str(name): Parser(str(name), one, dictionaries, table.header)
            for name, one in (spec.get("parsers") or {}).items()
        }
        self.types: list[dict[str, Any]] = list(spec.get("types") or [])
        if not self.types:
            raise Stop("대응 파일: types 가 비어 있습니다")
        self.by_slug: dict[str, dict[str, Any]] = {}
        for index, one in enumerate(self.types, start=1):
            slug = str(one.get("type_slug") or "")
            if not slug or "key" not in one:
                raise Stop(f"대응 파일: types[{index}] 에 type_slug · key 가 있어야 합니다")
            if slug in self.by_slug:
                raise Stop(f"대응 파일: 타입 {slug} 이(가) 두 번 나옵니다")
            self.by_slug[slug] = one
        for one in self.types:
            slug = one["type_slug"]
            specs = {"key": one["key"], "label": one.get("label") or one["key"]}
            specs.update(one.get("fields") or {})
            for name, value in specs.items():
                self._check(value, where=f"{slug}.{name}")
        self.stats: dict[tuple[str, str], FieldStats] = {}

    def _check(self, spec: Any, *, where: str) -> None:
        if not isinstance(spec, dict):
            raise Stop(f"대응 파일: {where} 는 {{...}} 여야 합니다")
        if "column" in spec:
            _column(spec, self.table.header, where=where)
        elif "key_of" in spec:
            if spec["key_of"] not in self.by_slug:
                raise Stop(
                    f"대응 파일: {where} 의 key_of {spec['key_of']!r} 가 types 에 없습니다"
                )
        elif "parser" in spec:
            parser = self.parsers.get(str(spec["parser"]))
            if parser is None:
                raise Stop(
                    f"대응 파일: {where} 의 해석기 {spec['parser']!r} 가 parsers 에 없습니다"
                )
            if spec.get("slot") not in parser.slot_names:
                raise Stop(
                    f"대응 파일: {where} 의 조각 {spec.get('slot')!r} 가 해석기에 없습니다"
                )
        elif "value" not in spec:
            raise Stop(
                f"대응 파일: {where} 는 column · key_of · parser · value 중"
                " 하나가 있어야 합니다"
            )

    def value(
        self, spec: dict[str, Any], number: int, row: dict[str, str], *, at: tuple[str, str]
    ) -> Any:
        if "value" in spec:
            return spec["value"]
        if "key_of" in spec:
            target = self.by_slug[spec["key_of"]]
            return self.value(target["key"], number, row, at=(target["type_slug"], "key"))
        if "parser" in spec:
            parsed = self.parsers[str(spec["parser"])].parse(number, row)
            got = parsed.slots.get(str(spec["slot"]))
            if got is None:
                return None
            return got[1] if spec.get("part") == "kind" else got[0]

        raw = row[str(spec["column"])]
        text = raw.strip()
        if spec.get("upper"):
            text = text.upper()
        blanks = {str(one) for one in spec.get("blank") or []}
        if not text or text in blanks:
            mapping = spec.get("map") or {}
            return mapping.get(BLANK) if BLANK in mapping else None
        if "map" in spec:
            mapping = spec["map"]
            if text in mapping:
                return mapping[text]
            if DATE in mapping and _as_date(text) is not None:
                return mapping[DATE]
            self._problem(at, "대응 없는 값", text)
            return None
        if spec.get("date"):
            iso = _as_date(text)
            if iso is None:
                self._problem(at, "날짜가 아닌 값", text)
            return iso
        return text

    def _problem(self, at: tuple[str, str], what: str, text: str) -> None:
        stats = self.stats.setdefault(at, FieldStats())
        stats.problems.setdefault(what, Counter())[mask(text)] += 1


def _as_date(text: str) -> str | None:
    found = DATE_RE.match(text)
    if not found:
        return None
    try:
        return date(*(int(part) for part in found.groups())).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 모으기 — 같은 식별자는 한 객체로, 값이 갈리면 미해결로
# --------------------------------------------------------------------------


@dataclass
class Gathered:
    key: str
    first_row: int
    rows: int = 0
    values: dict[str, dict[str, tuple[Any, list[int]]]] = field(default_factory=dict)
    """칸 → (값의 지문 → (값, 그 값이 나온 행들))."""


@dataclass
class TypeResult:
    slug: str
    workspace_slug: str | None
    objects: list[dict[str, Any]]
    blank_keys: int
    conflicts: Counter[str]
    unresolved: list[dict[str, Any]]


def gather(mapping: Mapping, one: dict[str, Any]) -> TypeResult:
    slug = str(one["type_slug"])
    specs: dict[str, Any] = {"label": one.get("label") or one["key"]}
    specs.update(one.get("fields") or {})
    found: dict[str, Gathered] = {}
    blank_keys = 0
    for number, row in mapping.table.rows:
        key = mapping.value(one["key"], number, row, at=(slug, "key"))
        if key in (None, ""):
            blank_keys += 1
            continue
        key = str(key)
        entry = found.get(key)
        if entry is None:
            entry = found[key] = Gathered(key=key, first_row=number)
        entry.rows += 1
        for name, spec in specs.items():
            value = mapping.value(spec, number, row, at=(slug, name))
            fingerprint = json.dumps(value, ensure_ascii=False, sort_keys=True)
            seen = entry.values.setdefault(name, {})
            if fingerprint in seen:
                seen[fingerprint][1].append(number)
            else:
                seen[fingerprint] = (value, [number])

    objects: list[dict[str, Any]] = []
    conflicts: Counter[str] = Counter()
    unresolved: list[dict[str, Any]] = []
    for entry in found.values():
        out: dict[str, Any] = {"key": entry.key}
        for name, seen in entry.values.items():
            if len(seen) == 1:
                out[name] = next(iter(seen.values()))[0]
                continue
            # **짐작하지 않는다.** 칸을 비워 두면(안 보냄) 플랫폼 값이 그대로 남고, 사람이
            # 답하기 전에는 미해결이 넣기를 막는다.
            conflicts[name] += 1
            unresolved.append(
                {
                    "what": f"{slug} {entry.key!r} 의 칸 {name}",
                    "question": "같은 식별자인데 행마다 값이 다릅니다 — 어느 값이 맞나요?"
                    " (늘 갈린다면 이 칸은 이 타입이 아니라 행 쪽 타입의 칸입니다)",
                    "options": [value for value, _ in seen.values()],
                    "_source": {
                        "file": mapping.table.name,
                        "rows": [rows[:10] for _, rows in seen.values()],
                    },
                }
            )
        out["_source"] = {"file": mapping.table.name, "row": entry.first_row}
        if entry.rows > 1:
            out["_note"] = f"{entry.rows}행에서 모음"
        objects.append(out)
    return TypeResult(
        slug=slug,
        workspace_slug=one.get("workspace_slug") or mapping.spec.get("workspace_slug"),
        objects=objects,
        blank_keys=blank_keys,
        conflicts=conflicts,
        unresolved=unresolved,
    )


# --------------------------------------------------------------------------
# 명령
# --------------------------------------------------------------------------


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as failure:
        raise Stop(f"대응 파일을 읽을 수 없습니다: {path} ({failure})") from failure
    except ValueError as failure:
        raise Stop(f"대응 파일이 JSON 이 아닙니다: {path} ({failure})") from failure
    if not isinstance(body, dict):
        raise Stop(f"대응 파일은 {{...}} 여야 합니다: {path}")
    return body


def _mapping_notes(mapping: Mapping, mapping_path: Path, digest: str) -> str:
    lines = [f"sp_table 로 옮김 — 대응 {mapping_path.name}(sha256 {digest[:12]})"]
    for one in mapping.types:
        specs: dict[str, Any] = {"key": one["key"], "label": one.get("label") or one["key"]}
        specs.update(one.get("fields") or {})
        parts = []
        for name, spec in specs.items():
            if "column" in spec:
                source = spec["column"]
            elif "key_of" in spec:
                source = f"{spec['key_of']} 의 식별자"
            elif "parser" in spec:
                part = " 종류" if spec.get("part") == "kind" else ""
                source = f"{spec['parser']}.{spec['slot']}{part}"
            else:
                source = "고정값"
            parts.append(f"{name} ← {source}")
        lines.append(f"{one['type_slug']}: " + " · ".join(parts))
    return "\n".join(lines)


def convert(mapping_path: Path, source_path: Path, run: Path) -> tuple[bool, str]:
    """실행 폴더를 만든다. (미해결이 없나, 보고서)."""
    spec = _load_mapping(mapping_path)
    source = spec.get("source") or {}
    table = read_table(
        source_path,
        encoding=str(source.get("encoding") or "auto"),
        delimiter=str(source.get("delimiter") or ","),
    )
    if not table.rows:
        raise Stop(f"{table.name}: 값이 있는 행이 없습니다")
    mapping = Mapping(spec, table)
    ontology: dict[str, Any] | None = None
    if spec.get("ontology"):
        ontology_path = (mapping_path.parent / str(spec["ontology"])).resolve()
        loaded = _load_mapping(ontology_path)
        ontology = loaded

    results = [gather(mapping, one) for one in mapping.types]

    pipeline.cmd_init(run, title=str(spec.get("title") or table.name))
    manifest_path = run / pipeline.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mapping_digest = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    manifest["sources"] = [
        {
            "name": table.name,
            "sha256": table.sha256,
            "rows": len(table.rows),
            "encoding": table.encoding,
        }
    ]
    manifest["objects_order"] = [result.slug for result in results]
    manifest["notes"] = _mapping_notes(mapping, mapping_path, mapping_digest)
    pipeline._write(manifest_path, manifest)
    if ontology is not None:
        pipeline._write(run / pipeline.ONTOLOGY, ontology)

    unresolved: list[dict[str, Any]] = []
    for result in results:
        chunks = [
            result.objects[at : at + pipeline.MAX_ROWS]
            for at in range(0, len(result.objects), pipeline.MAX_ROWS)
        ]
        for index, chunk in enumerate(chunks, start=1):
            name = result.slug if len(chunks) == 1 else f"{result.slug}-{index:03d}"
            pipeline._write(
                run / pipeline.OBJECTS_DIR / f"{name}.json",
                {
                    "type_slug": result.slug,
                    "workspace_slug": result.workspace_slug,
                    "rows": chunk,
                },
            )
        unresolved.extend(result.unresolved)
    pipeline._write(run / pipeline.UNRESOLVED, unresolved)

    text = _report(mapping, mapping_path, results, run, len(unresolved))
    (run / REPORT).write_text(text + "\n", encoding="utf-8")
    return not unresolved, text


def _report(
    mapping: Mapping, mapping_path: Path, results: list[TypeResult], run: Path, unresolved: int
) -> str:
    table = mapping.table
    lines = [
        f"원천: {len(table.rows)}행(빈 행 {table.blank_rows})"
        f" · {table.encoding} · 열 {len(table.header)}",
        f"대응: {mapping_path.name}",
        "",
    ]
    for result in results:
        head = (
            f"[{result.slug}] 객체 {len(result.objects)} · 식별자가 빈 행 {result.blank_keys}"
            f" · 값이 갈린 칸 {sum(result.conflicts.values())}"
        )
        if result.conflicts:
            head += (
                " (" + " · ".join(f"{k} {v}" for k, v in result.conflicts.most_common()) + ")"
            )
        lines.append(head)
        for (slug, name), stats in sorted(mapping.stats.items()):
            if slug != result.slug:
                continue
            for what, patterns in sorted(stats.problems.items()):
                lines.append(
                    f"  칸 {name} — {what} {sum(patterns.values())}: {_top(patterns)}"
                )
    for parser in mapping.parsers.values():
        lines.append("")
        lines.extend(parser.report())
    lines.append("")
    if unresolved:
        lines.append(f"미해결 {unresolved}건 → {pipeline.UNRESOLVED} — 답을 정한 뒤 비우세요")
    lines.append(f"다음: python sp_pipeline.py validate {run}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_table", description=__doc__.split("\n")[0])
    parser.add_argument("mapping", type=Path, help="대응 파일(JSON)")
    parser.add_argument("source", type=Path, help="원천 표(CSV)")
    parser.add_argument("run", type=Path, help="만들 실행 폴더 — 없는 폴더여야 한다")
    args = parser.parse_args(argv)
    try:
        ok, text = convert(args.mapping, args.source, args.run)
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
