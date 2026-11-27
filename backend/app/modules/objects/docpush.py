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
    아님)

**한 줄이라도 오류면 적용하지 않는다**(일괄 입력과 같다) — 문서 569건 중 몇 건의 판이 그 사이
올랐으면, 그 줄을 빼거나 판을 맞춰 다시 올린다. 적용은 **계획과 같을 때만**(지문 — 판 · 본문 ·
그림). 그림은 같은 문서 칸에 **같은 내용(sha256)이 이미 붙어 있으면 그것을 쓴다** — 다시 올려도
첨부가 늘지 않는다. 새 그림은 적용할 때 붙는다(계획은 아무것도 바꾸지 않는다).

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
#: 판의 「어디서」 — 화면 · MCP 의 쓰기와 가른다.
VIA = "문서 올리기"
#: 본문 안의 그림 — `![캡션](경로?w=폭)`. 경로에 빈칸 · 괄호는 없다(키트가 그렇게 쓴다).
IMAGE = re.compile(r"(?P<head>!\[[^\]\n]*\]\()(?P<target>[^)\s?]+)(?P<query>\?w=\d{1,3})?\)")
#: 이미 플랫폼의 것 · 바깥 주소 — 올리지 않는다.
REMOTE = ("photo:", "http://", "https://", "data:", "/", "#", "mailto:")
#: 진행률을 이만큼마다.
PROGRESS_EVERY = 20


def digest(body: str) -> str:
    """본문의 sha256 — 서버가 저장하는 꼴(줄바꿈 `\\n`)의 UTF-8 로."""
    return hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()


@dataclass
class Image:
    """본문이 가리키는 그림 하나."""

    path: str
    """zip 안의 경로(마크다운 파일의 폴더에서 풀었다)."""
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


@dataclass
class _Planned:
    row: Row
    body: str
    note: str
    target: ObjectInstance | None = None
    entries: dict[str, photos._Entry] = field(default_factory=dict)


def _error(row: Row, message: str) -> Row:
    row.action = "error"
    row.message = message
    return row


def _manifest(archive: zipfile.ZipFile, entries: dict[str, photos._Entry]) -> list[Any]:
    entry = entries.get(MANIFEST)
    if entry is None:
        raise Conflict(
            code("OBJECTS", 161),
            f"zip 맨 위에 {MANIFEST} 가 없습니다 — 정제 키트의 document_push 가 만든 zip 을 "
            "올리세요.",
        )
    raw = photos._read(archive, entry)
    try:
        body = json.loads((raw or b"").decode("utf-8-sig"))
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
        planned = _Planned(row, "", str(item.get("note") or "")[:200])
        out.append(planned)
        if not isinstance(raw, dict):
            _error(row, "목록의 줄이 객체({...})가 아닙니다")
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
        raw_body = photos._read(archive, entry)
        try:
            body = (raw_body or b"").decode("utf-8-sig").replace("\r\n", "\n")
        except UnicodeDecodeError:
            _error(row, f"UTF-8 로 읽을 수 없는 마크다운입니다: {path}")
            continue
        if raw_body is None:
            _error(row, f"zip 의 그 항목이 깨졌습니다: {path}")
            continue
        if len(body) > object_documents.MAX_CHARS:
            _error(
                row,
                f"본문이 {len(body):,}자입니다 — 한 편에 {object_documents.MAX_CHARS:,}자까지",
            )
            continue
        planned.body = body
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
        problem = _images(db, archive, entries, planned, object_id, prop_key)
        if problem:
            _error(row, problem)
            continue
        final = _final(planned)
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
    progress(stage, len(documents), len(documents))
    return out


def _images(
    db: Session,
    archive: zipfile.ZipFile,
    entries: dict[str, photos._Entry],
    planned: _Planned,
    object_id: uuid.UUID,
    prop_key: str,
) -> str:
    """본문이 가리키는 그림 — 없는 파일 · 이미지 아님 · 너무 큼이면 그 까닭."""
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
    for hit in IMAGE.finditer(planned.body):
        target = hit.group("target")
        if target.lower().startswith(REMOTE) or target in found:
            continue
        path = unicodedata.normalize(
            "NFC", posixpath.normpath(posixpath.join(folder, unquote(target)))
        )
        if path.startswith("../") or path == ".." or path.startswith("/"):
            return f"그림 경로가 zip 밖을 가리킵니다: {target}"
        entry = entries.get(path)
        if entry is None:
            return f"zip 에 그 그림이 없습니다: {target}"
        if entry.info.file_size > files_services.max_bytes():
            return f"그림이 너무 큽니다: {target}"
        data = photos._read(archive, entry)
        if data is None:
            return f"zip 의 그 그림이 깨졌습니다: {target}"
        if images.inspect(data) is None:
            return f"이미지(PNG · JPEG · GIF · WebP)가 아닙니다: {target}"
        sha = hashlib.sha256(data).hexdigest()
        attached = existing.get(sha)
        found[target] = Image(
            entry.name,
            sha,
            len(data),
            attachment_id=str(attached.id) if attached is not None else None,
            new=attached is None,
        )
        planned.entries[target] = entry
    planned.row.images = list(found.values())
    return ""


def _final(planned: _Planned) -> str:
    """그림 자리를 `photo:<첨부 id>` 로 — 새 그림은 아직 id 가 없어 sha256 을 자리에 둔다
    (계획의 글자 수 · 지문용, 적용은 붙인 뒤 다시 만든다)."""
    by_target = {}
    for target, entry in planned.entries.items():
        image = next(one for one in planned.row.images if one.path == entry.name)
        by_target[target] = image

    def swap(hit: re.Match[str]) -> str:
        image = by_target.get(hit.group("target"))
        if image is None:
            return hit.group(0)
        where = image.attachment_id or f"new-{image.sha256[:24]}"
        return f"{hit.group('head')}photo:{where}{hit.group('query') or ''})"

    return IMAGE.sub(swap, planned.body)


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
    return {
        "documents_push": True,
        "applied": applied,
        "ok": writes > 0 and counts["error"] == 0,
        "rows": [_row_out(one) for one in rows],
        "counts": counts,
        "summary": _summary(counts, applied=applied),
        "fingerprint": mark,
    }


def _row_out(row: Row) -> dict[str, Any]:
    out = asdict(row)
    out["images"] = [asdict(one) for one in row.images]
    return out


def _summary(counts: dict[str, int], *, applied: bool) -> str:
    head = "썼습니다" if applied else "쓸 것"
    parts = [
        f"새로 {counts['create']:,}",
        f"고침 {counts['update']:,}",
        f"그대로 {counts['unchanged']:,}",
    ]
    if counts["error"]:
        parts.append(f"오류 {counts['error']:,}")
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
        row, target = one.row, one.target
        assert target is not None and row.object_id is not None  # 쓰는 줄에는 객체가 있다
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
        for target_ref, entry in one.entries.items():
            image = next(each for each in row.images if each.path == entry.name)
            if not image.new:
                continue
            data = photos._read(archive, entry)
            assert data is not None  # 계획에서 읽혔다 — 같은 zip 이다
            made = files_services.attach(
                db,
                user=user,
                owner_table="objects",
                owner_id=target.id,
                owner_field=row.property,
                workspace_slug=None,
                filename=posixpath.basename(target_ref) or "그림",
                content_type=None,
                stream=io.BytesIO(data),
            )
            image.attachment_id = str(made.id)
        final = _final(one)
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
