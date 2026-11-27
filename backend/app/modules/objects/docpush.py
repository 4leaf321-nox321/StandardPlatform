"""문서 올리기 — zip 하나(목록 · 마크다운 · 그림)로 **여러 객체의 문서 칸**에 본문을 쓴다
(ADR 0099).

문서 칸(ADR 0053)의 본문은 MCP `document_write` 의 도구 인자였다 — 모델이 본문을 글자 그대로
옮겨 적어야 해서, Word 569건 · 270만 자를 넣으려면 토큰이 들고 옮기다 틀린다. 그림은 이미
`attachment_upload_prepare` 의 curl 로 바이트가 모델을 안 거치는데 본문만 그랬다. 정제 키트가
마크다운 · 그림 파일을 zip 하나로 묶어 올리면 여기서 계획을 세우고, 사람이 「작업」 화면에서
보고 「적용」 한다 — 사진 일괄 업로드(`photos.py`)와 같은 두 걸음이다.

## zip 의 모양

    manifest.json   {"format": "sp-documents/1", "documents": [
                       {"type_slug", "object_id", "key", "path", "base_revision"?, "note"?}]}
    <path>          마크다운(UTF-8) — 그림은 제 자리에 `![캡션](media/image3.png?w=60)`
    <그림 파일>     본문이 가리키는 그림(마크다운 파일의 폴더에서 본 경로)

## 계획 — 문서마다

    create      처음 쓴다(판 0 → 1)
    update      판이 하나 오른다
    unchanged   본문 · 그림이 지금 것과 같다 — 쓰지 않는다
    error       못 쓴다(객체 · 문서 칸 · 권한 · 원천 잠금 · 판이 다름 · 없는 그림 · 이미지
    아님 · 너무 큼 · NUL 문자)

**한 줄이라도 오류면 적용하지 않는다**(일괄 입력과 같다) — 문서 569건 중 몇 건의 판이 그 사이
올랐으면, 그 줄을 빼거나 판을 맞춰 다시 올린다. 적용은 **계획과 같을 때만**(지문 — 판 · 본문 ·
그림). 그림은 같은 문서 칸에 **같은 내용(sha256)이 이미 붙어 있으면 그것을 쓴다** — 다시 올려도
첨부가 늘지 않는다. 한 문서 안에서 같은 그림을 여러 자리가 가리켜도(`./img/a.png` 와
`img/a.png`, 이름만 다른 같은 파일) 한 번만 붙는다. 새 그림은 적용할 때 붙는다(계획은 아무것도
바꾸지 않는다).

줄의 `warnings` 는 막지 않지만 사람이 읽고 적용해야 하는 것이다 — 지금 본문을 빈 본문으로
바꿈, 그림 꼴로 읽지 못해 글자 그대로 남는 그림 자리(따옴표 제목 · 경로의 빈칸 · 네 자리 폭 ·
캡션의 대괄호 · `/` 로 시작하는 경로). 작업 결과의 줄을 자를 때 오류 다음으로 앞세운다
(`jobs.services.trim_rows`).

## zip 을 믿지 않는다 — 항목마다 상한

사진 일괄 업로드의 검사(`photos._open` — 항목 수 · 풀린 크기의 합)에 더해 **항목마다** 머리에
적힌 크기를 풀기 전에 보고, 머리가 거짓이어도 상한까지만 푼다(`_read`) — 목록
`MANIFEST_MAX_BYTES`, 마크다운 `BODY_MAX_BYTES`, 그림 `files.services.max_bytes()`. 전에는
목록 · 마크다운을 상한 없이 통째로 읽었다 — 1MB zip 에 든 1GB 목록이 워커의 메모리를 다 썼고,
작업은 다시 돌아 같은 일을 되풀이했다(2026-11-27 점검). 본문은 계획 동안 한 편씩만 메모리에
두고 적용할 때 zip 에서 다시 읽는다.

## 적용의 답 — 대조할 수 있게

줄마다 새 판 · 글자 수 · **본문의 sha256**(서버가 저장한 그대로 — 줄바꿈을 `\\n` 으로 맞춘
UTF-8)
과 그림마다 붙은 첨부 id. 정제 키트의 `document_check` 가 이것과 서버의 지금 본문
(`GET …/documents/{key}?body=false` 의 `sha256`)을 견준다 — 본문을 모델로 다시 받지 않는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import unicodedata
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, BinaryIO
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.files import services as files_services
from app.modules.files.models import Attachment
from app.modules.objects import access, photos
from app.modules.objects import documents as object_documents
from app.modules.objects.models import ObjectDocument, ObjectInstance
from app.modules.objects.services import properties_of
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import images
from app.shared.errors import AppError, Conflict, code
from app.shared.permissions import visible_owner_clause

Progress = Callable[[str, int, int], None]

MANIFEST = "manifest.json"
FORMAT = "sp-documents/1"
#: 한 번에 올리는 문서 수 — 이보다 많으면 나눈다(키트가 나눠 올린다).
MAX_DOCUMENTS = 2000
#: 목록(`manifest.json`)의 상한(바이트) — 문서 2천 건이면 1MB 남짓이다(경로 · 메모가
#: 한글이어도).
MANIFEST_MAX_BYTES = 4 * 1024 * 1024
#: 마크다운 한 편의 상한(바이트) — 본문 상한(글자)의 UTF-8 최대(한 글자 4바이트)와 BOM. 이것을
#: 넘는 파일은 글자로 세도 본문 상한을 넘는다(줄바꿈 `\r\n` 두 바이트도 한 글자가 될 뿐이다).
BODY_MAX_BYTES = object_documents.MAX_CHARS * 4 + 3
#: 판의 「어디서」 — 화면 · MCP 의 쓰기와 가른다.
VIA = "문서 올리기"
#: 본문 안의 그림 — `![캡션](경로?w=폭)`. 캡션에 대괄호, 경로에 빈칸 · 괄호 · 대괄호가
#: 없다(키트가 그렇게 쓴다). 키트(`sp_docpush.IMAGE`)와 **같은 꼴**이어야 대조(sha256)가
#: 맞는다.
#: **빼는 글자에 `(` · `[` 가 있어야 한다** — 전에는 경로가 `)` · 빈칸만 빼서, `![](` 가
#: 이어진 본문에서 경로가 뒤의 `![](` 를 줄 끝까지 삼켰다가 되물렀다. 본문 길이의 제곱으로
#: 느려졌다(2만 자 2초 · 4만 자 8초 · 8만 자 31초 — 본문 상한 100만 자면 워커가 한 시간 넘게
#: 멈춘다, 2026-11-27 점검).
IMAGE = re.compile(
    r"(?P<head>!\[[^\[\]\n]*\]\()(?P<target>[^()\[\]\s?]+)(?P<query>\?w=\d{1,3})?\)"
)
#: 이미 플랫폼의 것 · 바깥 주소 — 올리지 않는다.
REMOTE = ("photo:", "http://", "https://", "data:", "/", "#", "mailto:")
#: 그림 자리처럼 보이는 것의 여는 쪽 — `](` 뒤의 대상(빈칸 · 닫는 괄호까지). `IMAGE` 가
#: 그림으로 읽지 못한 자리를 찾을 때만 쓴다(`_leftover`). 대상은 길이를 묶는다 — 묶지 않으면
#: 한 줄에 이어진 `](` 가 줄 끝까지 다시 읽힌다.
_OPENING = re.compile(r"\]\(\s*<?(?P<target>[^\s)>]{0,300})")
#: 주소의 앞머리(`https:` · `photo:` · `data:` · `mailto:`) — 한 글자(`C:`)는 Windows 의
#: 드라이브다.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")
#: `](` 앞에서 `![` 를 찾는 거리 — 캡션이 이보다 길면 그림 자리로 안 본다(경고일 뿐이다).
_CAPTION_REACH = 300
#: 경고에 싣는 자리의 보기 수 · 길이.
_SHOWN = 3
_SNIPPET = 60
#: 진행률을 이만큼마다.
PROGRESS_EVERY = 20


def digest(body: str) -> str:
    """본문의 sha256 — 서버가 저장하는 꼴(줄바꿈 `\\n`)의 UTF-8 로."""
    return hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()


@dataclass
class Image:
    """본문이 가리키는 그림 하나 — **내용(sha256)마다 하나**다. 같은 그림을 여러 자리가
    가리켜도(경로의 `./` · 이름만 다른 같은 파일) 한 번만 싣고 한 번만 붙인다."""

    path: str
    """zip 안의 경로(마크다운 파일의 폴더에서 풀었다) — 그 내용을 처음 가리킨 자리의 것."""
    sha256: str
    size: int
    attachment_id: str | None = None
    """이미 붙어 있으면(같은 sha256) 그 첨부, 새 것은 적용한 뒤에 채운다."""
    new: bool = False


@dataclass
class Row:
    row: int
    action: str
    label: str
    key: str
    """「<타입>:<문서 칸>」 — 화면의 계획 표가 이름 옆에 적는다."""
    object_id: str | None
    message: str = ""
    changes: list[str] = field(default_factory=list)
    type_slug: str = ""
    property: str = ""
    path: str = ""
    base_revision: int | None = None
    revision: int | None = None
    """계획이면 지금 판, 적용했으면 새 판."""
    chars: int = 0
    sha256: str | None = None
    """적용했으면 쓴 본문의 sha256. 계획이면 새 그림이 없을 때만(있으면 첨부 id 가 정해져야
    안다)."""
    body_digest: str = ""
    """그림 자리를 경로 그대로 둔 본문의 sha256 — 지문에 쓴다."""
    images: list[Image] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """막지 않지만 읽고 적용할 것 — 빈 본문으로 바꿈 · 그림 꼴로 읽지 못한 그림 자리. 지문에는
    넣지 않는다(본문 · 판이 같으면 같은 경고다)."""


@dataclass
class _Planned:
    row: Row
    note: str
    entry: photos._Entry | None = None
    """마크다운 — 본문은 계획 동안 한 편씩만 들고, 적용할 때 여기서 다시 읽는다."""
    target: ObjectInstance | None = None
    shas: dict[str, str] = field(default_factory=dict)
    """본문의 그림 자리(쓴 그대로) → 그림의 sha256."""
    sources: dict[str, photos._Entry] = field(default_factory=dict)
    """그림의 sha256 → zip 항목(처음 가리킨 것)."""
    names: dict[str, str] = field(default_factory=dict)
    """그림의 sha256 → 붙일 때의 파일 이름."""


def _error(row: Row, message: str) -> Row:
    row.action = "error"
    row.message = message
    return row


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):,.1f}MB"


def _read(archive: zipfile.ZipFile, entry: photos._Entry, cap: int) -> bytes | None:
    """항목의 바이트 — **`cap` 바이트까지만** 푼다. 깨졌으면(또는 머리에 적힌 것보다 길게
    풀리면) None. 머리에 적힌 크기(`file_size`)가 `cap` 을 넘는지는 부르는 쪽이 먼저 보고 그
    까닭을 말한다 — 여기서는 머리가 거짓이어도 그 이상을 메모리에 올리지 않는다."""
    try:
        with archive.open(entry.info) as stream:
            data = stream.read(cap + 1)
    except photos._BROKEN:
        return None
    return data if len(data) <= cap else None


def _manifest(archive: zipfile.ZipFile, entries: dict[str, photos._Entry]) -> list[Any]:
    entry = entries.get(MANIFEST)
    if entry is None:
        raise Conflict(
            code("OBJECTS", 161),
            f"zip 맨 위에 {MANIFEST} 가 없습니다 — 정제 키트의 document_push 가 만든 zip 을 "
            "올리세요.",
        )
    if entry.info.file_size > MANIFEST_MAX_BYTES:
        raise Conflict(
            code("OBJECTS", 161),
            f"{MANIFEST} 가 {_mb(entry.info.file_size)} 입니다 — 목록은 "
            f"{_mb(MANIFEST_MAX_BYTES)} 까지(문서 {MAX_DOCUMENTS:,}건이어도 1MB 남짓)입니다. "
            "정제 키트의 document_push 가 만든 zip 을 올리세요.",
            details={"bytes": entry.info.file_size, "max_bytes": MANIFEST_MAX_BYTES},
        )
    raw = _read(archive, entry, MANIFEST_MAX_BYTES)
    if raw is None:
        raise Conflict(
            code("OBJECTS", 161),
            f"zip 의 {MANIFEST} 를 읽을 수 없습니다 — 압축이 깨졌거나 적힌 크기와 다릅니다.",
        )
    try:
        body = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise Conflict(
            code("OBJECTS", 161), f"{MANIFEST} 를 JSON 으로 읽을 수 없습니다."
        ) from None
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise Conflict(
            code("OBJECTS", 161), f'{MANIFEST} 의 format 이 "{FORMAT}" 가 아닙니다.'
        )
    documents = body.get("documents")
    if not isinstance(documents, list) or not documents:
        raise Conflict(code("OBJECTS", 161), f"{MANIFEST} 에 documents 가 없습니다.")
    if len(documents) > MAX_DOCUMENTS:
        raise Conflict(
            code("OBJECTS", 161),
            f"문서가 {len(documents):,}건입니다 — 한 번에 {MAX_DOCUMENTS:,}건까지, 나눠 "
            "올리세요.",
        )
    return documents


def _body(archive: zipfile.ZipFile, entry: photos._Entry, path: str) -> tuple[str | None, str]:
    """마크다운 한 편 — (본문, 못 읽은 까닭). 상한을 넘으면 풀지 않는다."""
    size = entry.info.file_size
    if size > BODY_MAX_BYTES:
        return None, (
            f"마크다운이 {_mb(size)} 입니다 — 한 편에 "
            f"{object_documents.MAX_CHARS:,}자까지입니다: {path}"
        )
    raw = _read(archive, entry, BODY_MAX_BYTES)
    if raw is None:
        return None, f"zip 의 그 항목이 깨졌습니다: {path}"
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n"), ""
    except UnicodeDecodeError:
        return None, f"UTF-8 로 읽을 수 없는 마크다운입니다: {path}"


def _nul(text: str) -> str:
    """NUL 문자(`\\0`)가 있으면 그 자리(몇째 줄) — PostgreSQL 의 글자 칸은 그 글자를 저장하지
    못한다. 계획을 지나 적용에서 DB 오류로 작업 전체가 실패했다(2026-11-27 점검)."""
    at = text.find("\x00")
    if at < 0:
        return ""
    return f"{text.count(chr(10), 0, at) + 1}째 줄"


def _plan(
    db: Session,
    user: User,
    archive: zipfile.ZipFile,
    listed: list[photos._Entry],
    progress: Progress,
    *,
    stage: str,
) -> list[_Planned]:
    entries = {
        one.name: one
        for one in listed
        if not photos._hidden(one.name) and photos._refused(one) is None
    }
    documents = _manifest(archive, entries)
    types: dict[str, ObjectType | None] = {}
    defs: dict[str, dict[str, PropertyDef]] = {}
    seen: dict[tuple[str, str], int] = {}
    pictures: dict[str, tuple[str, int] | str] = {}
    out: list[_Planned] = []
    for index, raw in enumerate(documents, start=1):
        if index % PROGRESS_EVERY == 0:
            progress(stage, index, len(documents))
        item = raw if isinstance(raw, dict) else {}
        slug = str(item.get("type_slug") or "")
        prop_key = str(item.get("key") or "")
        path = str(item.get("path") or "")
        row = Row(index, "error", path or f"{index}번째", f"{slug}:{prop_key}", None)
        row.type_slug, row.property, row.path = slug, prop_key, path
        planned = _Planned(row, str(item.get("note") or "")[:200])
        out.append(planned)
        if not isinstance(raw, dict):
            _error(row, "목록의 줄이 객체({...})가 아닙니다")
            continue
        if _nul(slug + prop_key + path + planned.note):
            # 찾는 말(slug · 키 · 경로)에 들면 DB 가 질의를 거절해 작업 전체가 실패한다.
            _error(row, "목록의 줄에 NUL 문자(\\0)가 있습니다 — 그 글자를 빼고 다시 올립니다")
            continue
        base = item.get("base_revision")
        if base is not None and (
            not isinstance(base, int) or isinstance(base, bool) or base < 0
        ):
            _error(row, "base_revision 은 0 이상의 정수입니다(비우면 지금 판)")
            continue
        row.base_revision = base
        try:
            object_id = uuid.UUID(str(item.get("object_id") or ""))
        except ValueError:
            _error(row, "object_id 가 객체 id(uuid)가 아닙니다")
            continue
        row.object_id = str(object_id)
        if slug not in types:
            types[slug] = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
            found_type = types[slug]
            defs[slug] = (
                {one.key: one for one in properties_of(db, found_type.id)}
                if found_type
                else {}
            )
        object_type = types[slug]
        if object_type is None:
            _error(row, f"타입을 찾을 수 없습니다: {slug}")
            continue
        definition = defs[slug].get(prop_key)
        if definition is None or definition.data_type != "document":
            _error(row, f"{slug} 에 문서 칸 {prop_key!r} 이 없습니다")
            continue
        target = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.id == object_id,
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        if target is None:
            _error(row, f"객체를 찾을 수 없습니다: {slug} {object_id}")
            continue
        row.label = f"{target.label} · {definition.label}"
        planned.target = target
        mark = (str(object_id), prop_key)
        if mark in seen:
            _error(
                row, f"같은 객체 · 문서 칸을 {seen[mark]}번째 줄이 씁니다 — 하나만 올립니다"
            )
            continue
        seen[mark] = index
        refused = managed.objects_refusal(object_type, what="문서를 고치지")
        if refused:
            _error(row, refused)
            continue
        try:
            access.require_edit(db, user, object_type, target, code_value=code("OBJECTS", 16))
        except AppError as denied:
            _error(row, denied.message)
            continue
        entry = entries.get(unicodedata.normalize("NFC", path))
        if entry is None:
            _error(row, f"zip 에 그 마크다운이 없습니다: {path}")
            continue
        planned.entry = entry
        body, problem = _body(archive, entry, path)
        if body is None:
            _error(row, problem)
            continue
        if len(body) > object_documents.MAX_CHARS:
            _error(
                row,
                f"본문이 {len(body):,}자입니다 — 한 편에 {object_documents.MAX_CHARS:,}자까지",
            )
            continue
        where = _nul(body)
        if where:
            _error(
                row,
                f"본문에 NUL 문자(\\0)가 있습니다({where}) — 저장할 수 없는 글자라 빼고 다시 "
                "올립니다",
            )
            continue
        current = db.scalar(
            select(ObjectDocument).where(
                ObjectDocument.object_id == object_id, ObjectDocument.key == prop_key
            )
        )
        now_revision = current.revision if current is not None else 0
        row.revision = now_revision
        if base is not None and base != now_revision:
            _error(
                row,
                f"그 사이 판이 올랐습니다(올린 것은 판 {base} 기준, 지금 판 {now_revision}) — "
                "지금 본문을 읽고 맞춘 뒤 다시 올리거나 base_revision 을 비웁니다",
            )
            continue
        if base is None:
            row.base_revision = now_revision
        problem = _images(db, archive, entries, planned, body, object_id, prop_key, pictures)
        if problem:
            _error(row, problem)
            continue
        final = _final(planned, body)
        if len(final) > object_documents.MAX_CHARS:
            _error(
                row,
                f"그림 자리를 첨부로 바꾸면 {len(final):,}자입니다 — 한 편에 "
                f"{object_documents.MAX_CHARS:,}자까지",
            )
            continue
        row.chars = len(final)
        row.body_digest = digest(body)
        news = sum(1 for one in row.images if one.new)
        if not news and current is not None and current.body == final:
            row.action = "unchanged"
            row.sha256 = digest(final)
        else:
            row.action = "create" if now_revision == 0 else "update"
            row.sha256 = None if news else digest(final)
        row.changes = [f"{row.chars:,}자"]
        if row.images:
            reused = len(row.images) - news
            row.changes.append(
                f"그림 {len(row.images)}"
                + (f"(새로 {news} · 붙어 있는 것 {reused})" if news and reused else "")
                + (" — 새로 붙임" if news and not reused else "")
            )
        if row.action == "update":
            row.changes.append(f"판 {now_revision} → {now_revision + 1}")
            if current is not None and current.body.strip() and not final.strip():
                row.warnings.append(
                    f"지금 본문 {current.chars:,}자가 빈 본문이 됩니다 — 비우려던 것이 아니면 "
                    "적용하지 마세요(적용해도 지금 본문은 판 이력에 남아 되돌릴 수 있습니다)"
                )
        left = _leftover(final)
        if left:
            count, shown = left
            row.warnings.append(
                f"그림 꼴로 읽지 않은 자리 {count:,}곳 — 첨부하지 않고 글자 그대로 둡니다: "
                + " · ".join(f"「{one}」" for one in shown)
                + ". 그림은 ![캡션](상대 경로) 또는 ![캡션](상대 경로?w=폭) 꼴입니다(경로에 "
                "빈칸 · 괄호 · 따옴표 제목 없이, 폭은 세 자리까지)"
            )
    progress(stage, len(documents), len(documents))
    return out


def _images(
    db: Session,
    archive: zipfile.ZipFile,
    entries: dict[str, photos._Entry],
    planned: _Planned,
    body: str,
    object_id: uuid.UUID,
    prop_key: str,
    pictures: dict[str, tuple[str, int] | str],
) -> str:
    """본문이 가리키는 그림 — 없는 파일 · 이미지 아님 · 너무 큼이면 그 까닭. `pictures` 는 zip
    항목 → (sha256, 크기) 또는 까닭 — 여러 문서가 같은 그림을 가리켜도 한 번만 푼다."""
    folder = posixpath.dirname(unicodedata.normalize("NFC", planned.row.path))
    existing = {
        one.sha256: one
        for one in db.scalars(
            select(Attachment).where(
                Attachment.owner_table == "objects",
                Attachment.owner_id == object_id,
                Attachment.owner_field == prop_key,
                Attachment.deleted_at.is_(None),
            )
        )
    }
    found: dict[str, Image] = {}
    for hit in IMAGE.finditer(body):
        target = hit.group("target")
        if target.lower().startswith(REMOTE) or target in planned.shas:
            continue
        # `./img/a.png` 와 `img/a.png` 는 같은 항목이다 — 경로를 풀어 zip 의 이름으로 본다.
        path = unicodedata.normalize(
            "NFC", posixpath.normpath(posixpath.join(folder, unquote(target)))
        )
        if path.startswith("../") or path == ".." or path.startswith("/"):
            return f"그림 경로가 zip 밖을 가리킵니다: {target}"
        entry = entries.get(path)
        if entry is None:
            return f"zip 에 그 그림이 없습니다: {target}"
        known = pictures.get(entry.name)
        if known is None:
            known = pictures[entry.name] = _picture(archive, entry)
        if isinstance(known, str):
            return f"{known}: {target}"
        sha, size = known
        planned.shas[target] = sha
        if sha in found:
            continue  # 같은 그림을 다른 자리가 먼저 가리켰다 — 한 번만 붙인다.
        attached = existing.get(sha)
        found[sha] = Image(
            entry.name,
            sha,
            size,
            attachment_id=str(attached.id) if attached is not None else None,
            new=attached is None,
        )
        planned.sources[sha] = entry
        planned.names[sha] = posixpath.basename(target) or "그림"
    planned.row.images = list(found.values())
    return ""


def _picture(archive: zipfile.ZipFile, entry: photos._Entry) -> tuple[str, int] | str:
    """그림 항목 하나 — (sha256, 크기) 또는 못 쓰는 까닭."""
    limit = files_services.max_bytes()
    if entry.info.file_size > limit:
        return f"그림이 너무 큽니다(한 파일은 {_mb(limit)} 까지)"
    data = _read(archive, entry, limit)
    if data is None:
        return "zip 의 그 그림이 깨졌습니다"
    if images.inspect(data) is None:
        return "이미지(PNG · JPEG · GIF · WebP)가 아닙니다"
    return hashlib.sha256(data).hexdigest(), len(data)


def _final(planned: _Planned, body: str) -> str:
    """그림 자리를 `photo:<첨부 id>` 로 — 새 그림은 아직 id 가 없어 sha256 을 자리에 둔다
    (계획의 글자 수 · 지문용, 적용은 붙인 뒤 다시 만든다)."""
    by_sha = {one.sha256: one for one in planned.row.images}

    def swap(hit: re.Match[str]) -> str:
        sha = planned.shas.get(hit.group("target"))
        image = by_sha.get(sha) if sha is not None else None
        if image is None:
            return hit.group(0)
        where = image.attachment_id or f"new-{image.sha256[:24]}"
        return f"{hit.group('head')}photo:{where}{hit.group('query') or ''})"

    return IMAGE.sub(swap, body)


def _leftover(body: str) -> tuple[int, list[str]] | None:
    """그림 꼴(`IMAGE`)로 읽지 못한 **로컬 그림 자리** — (수, 보기 몇). 없으면 None.

    `![…](대상` 인데 대상이 주소(`https:` · `photo:` · `data:` …)도 쪽 안 자리(`#…`)도 아닌
    것 — 따옴표 제목(`![a](img/a.png "t")`) · 경로의 빈칸 · 네 자리 폭(`?w=1000`) · 캡션의
    대괄호 · `/` 로 시작하는 경로. 전에는 그런 자리가 말없이 글자로 남아, 적용한 뒤에야 그림이
    빠진 것을 알았다. **본문 길이에 비례해서만** 본다 — `](` 마다 앞의 `_CAPTION_REACH` 글자
    안에서 `![` 를 찾는다(그 사이에 다른 `](` 가 있으면 그 `![` 의 것이 아니다)."""
    count = 0
    shown: list[str] = []
    for hit in _OPENING.finditer(body):
        start = hit.start()
        reach = max(0, start - _CAPTION_REACH)
        line = max(reach, body.rfind("\n", reach, start) + 1)
        bang = body.rfind("![", line, start)
        caption = body[bang + 2 : start] if bang >= 0 else ""
        # 그 사이에 다른 `](` 가 있거나 참조식 그림(`![logo][1]`) 뒤의 링크면 그림 자리가
        # 아니다.
        if bang < 0 or "](" in caption or "][" in caption:
            continue
        target = hit.group("target")
        if not target or target.startswith("#") or _SCHEME.match(target):
            continue
        count += 1
        if len(shown) < _SHOWN:
            close = body.find(")", hit.end(), hit.end() + _SNIPPET)
            end = close + 1 if close >= 0 else min(len(body), bang + _SNIPPET)
            snippet = body[bang:end].split("\n", 1)[0]
            shown.append(snippet if len(snippet) <= _SNIPPET else snippet[:_SNIPPET] + "…")
    return (count, shown) if count else None


def fingerprint(rows: list[Row]) -> str:
    """미리 본 계획과 적용 직전 계획이 같은가 — 판 · 본문 · 그림 · 할 일."""
    body = [
        [
            one.row,
            one.object_id,
            one.property,
            one.action,
            one.base_revision,
            one.body_digest,
            sorted((image.sha256, image.attachment_id or "") for image in one.images),
            one.message,
        ]
        for one in rows
    ]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run(
    db: Session,
    user: User,
    params: dict[str, Any],
    data: bytes | BinaryIO,
    *,
    progress: Progress,
) -> dict[str, Any]:
    """작업 `documents_push` 의 본문 — 계획, 또는 (`apply`) 지문을 확인하고 적용. **커밋하지
    않는다** — 워커가 끝에서 한 번 한다(적용 중 실패하면 쓴 판 · 붙인 그림이 모두 물러진다)."""
    applied = bool(params.get("apply"))
    archive, listed = photos._open(data)
    with archive:
        planned = _plan(
            db, user, archive, listed, progress, stage="확인" if applied else "계획"
        )
        rows = [one.row for one in planned]
        mark = fingerprint(rows)
        if applied:
            wanted = params.get("fingerprint")
            if wanted and wanted != mark:
                raise Conflict(
                    code("JOBS", 20),
                    "미리 본 것과 달라졌습니다 — 그 사이 누가 그 문서를 고쳤거나 그림이 "
                    "붙었습니다. "
                    "아무것도 쓰지 않았으니 다시 올려 미리 보고 적용하세요.",
                )
            if any(one.action == "error" for one in rows):
                raise Conflict(code("JOBS", 11), "오류가 있는 계획은 적용하지 않습니다.")
            _apply(db, user, archive, planned, progress)
    counts = {"create": 0, "update": 0, "unchanged": 0, "error": 0}
    for one in rows:
        counts[one.action] = counts.get(one.action, 0) + 1
    writes = counts["create"] + counts["update"]
    warned = sum(1 for one in rows if one.warnings)
    return {
        "documents_push": True,
        "applied": applied,
        "ok": writes > 0 and counts["error"] == 0,
        "rows": [_row_out(one) for one in rows],
        "counts": counts,
        # 줄과 상관없는 오류 — 없다(그런 것은 작업을 실패로 끝낸다). 「작업」 화면의 계획 표가
        # 일괄 입력과 같은 모양으로 읽는다 — 없으면 화면이 그 자리에서 멈췄다(2026-11-27).
        "errors": [],
        "warned": warned,
        "summary": _summary(counts, warned, applied=applied),
        "fingerprint": mark,
    }


def _row_out(row: Row) -> dict[str, Any]:
    return asdict(row)


def _summary(counts: dict[str, int], warned: int, *, applied: bool) -> str:
    head = "썼습니다" if applied else "쓸 것"
    parts = [
        f"새로 {counts['create']:,}",
        f"고침 {counts['update']:,}",
        f"그대로 {counts['unchanged']:,}",
    ]
    if counts["error"]:
        parts.append(f"오류 {counts['error']:,}")
    if warned:
        parts.append(f"확인할 것 {warned:,}")
    return f"문서 {head} — " + " · ".join(parts)


def _apply(
    db: Session,
    user: User,
    archive: zipfile.ZipFile,
    planned: list[_Planned],
    progress: Progress,
) -> None:
    """계획대로 쓴다 — 지문을 확인한 **뒤에만** 부른다."""
    todo = [one for one in planned if one.row.action in ("create", "update")]
    for index, one in enumerate(todo):
        if index % PROGRESS_EVERY == 0:
            progress("적용", index, len(todo))
        row, target, entry = one.row, one.target, one.entry
        # 쓰는 줄에는 객체 · 마크다운이 있다.
        assert target is not None and row.object_id is not None and entry is not None
        body, _problem = _body(archive, entry, row.path)
        assert body is not None  # 계획에서 읽혔다 — 같은 zip 이다
        found = db.scalar(
            select(ObjectDocument)
            .where(ObjectDocument.object_id == target.id, ObjectDocument.key == row.property)
            .with_for_update()
        )
        now_revision = found.revision if found is not None else 0
        if now_revision != row.base_revision:
            raise Conflict(
                code("OBJECTS", 149),
                f"{row.label}: 다른 곳에서 먼저 수정했습니다(지금 판 {now_revision}) — 다시 "
                "올려 미리 보세요. 아무것도 쓰지 않았습니다.",
            )
        for image in row.images:
            if not image.new:
                continue
            data = _read(archive, one.sources[image.sha256], files_services.max_bytes())
            assert data is not None  # 계획에서 읽혔다 — 같은 zip 이다
            made = files_services.attach(
                db,
                user=user,
                owner_table="objects",
                owner_id=target.id,
                owner_field=row.property,
                workspace_slug=None,
                filename=one.names.get(image.sha256) or "그림",
                content_type=None,
                stream=io.BytesIO(data),
            )
            image.attachment_id = str(made.id)
        final = _final(one, body)
        document, _changed = object_documents.write_body(
            db,
            user,
            target,
            row.property,
            final,
            note=one.note or "문서 올리기",
            found=found,
            via=VIA,
        )
        row.revision = document.revision
        row.chars = len(final)
        row.sha256 = digest(final)
        db.flush()
    progress("적용", len(todo), len(todo))
