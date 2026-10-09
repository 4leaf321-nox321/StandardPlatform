"""사진 일괄 업로드 — zip 하나의 사진을 **파일 이름으로 객체를 찾아** 파일 속성에 붙인다
(ADR 0012 「남은 것」, 2026-10-08).

시험 결과 수백 건의 사진을 상세 화면마다 한 장씩 올리면 아무도 끝까지 안 한다. 사진은 대개
이미 객체의 식별자 · 이름으로 저장돼 있다(`P-100.jpg`). 그 이름으로 찾아 붙인다.

## 두 걸음 — 계획을 보고 적용한다

작업(`objects_photos`)이다. 계획은 아무것도 바꾸지 않고 파일마다 무엇이 될지 적는다:

    attach      업로드 — 이 객체의 칸에 붙인다
    replace     교체 — 칸에 있던 사진을 떼고 붙인다(「있는 사진」 을 교체로 골랐을 때)
    skip        건너뜀 — 칸에 이미 사진이 있다(기본)
    not_found   못 찾음 — 그 이름의 객체가 없다(안 보이는 것도 없다고 말한다)
    ambiguous   여럿에 맞음 — **짐작하지 않는다**, 파일 이름을 식별자로 바꿔 다시 올린다
    not_image   이미지 아님 — 서버가 열어 보고 PNG · JPEG · GIF · WebP 로 못 읽었다
    too_large   너무 큼 — 한 파일의 상한(`files.services.MAX_BYTES`)을 넘는다
    forbidden   권한 없음 — 보이지만 고칠 수 없는 객체(소유 부서 관리자가 아니다)
    bad_entry   읽지 않음 — zip 밖을 가리키는 경로 · 링크 · 암호 · 깨진 항목

못 붙는 파일이 있어도 **붙는 것은 붙는다** — 일괄 입력(한 줄이 틀리면 아무것도 안 넣는다)과
다르다. 사진 300장 중 5장의 이름이 틀렸다고 295장을 막으면, 사람은 300장을 다시 고르게 된다.
못 붙은 5장은 계획에 이름과 까닭이 남는다. 대신 **적용은 계획과 같을 때만** 한다(지문) — 그
사이 누가 사진을 붙였거나 객체 이름을 바꿨으면 다시 미리 본다.

## 이름으로 찾는 차례 — 짐작은 계획에 드러낸다

`resolve.by_name`(식별자 → 별칭 → 이름)을 그대로 쓴다. 포함으로만 걸린 것은 「못 찾음」 이다
(포함은 짐작이다). 파일 이름이 안 맞으면 둘을 더 본다 — 둘 다 계획의 줄에 무엇으로 찾았는지
적힌다:

1. 파일 이름(확장자를 뗀 것) — `P-100.jpg`
2. 바로 위 폴더의 이름 — `P-100/앞면.jpg`(한 객체의 사진 여러 장)
3. 뒤 번호를 뗀 이름 — `P-100_2.jpg` · `P-100 (2).jpg`(한 객체의 둘째 사진)

## zip 을 믿지 않는다

- **압축 폭탄** — 항목 수(`MAX_ENTRIES`)와 머리에 적힌 풀린 크기의 합(`MAX_TOTAL_BYTES`)을
  풀기 전에 본다. 항목은 머리에 적힌 크기까지만 풀린다(파이썬 `zipfile` 이 그만큼에서 끊고
  CRC 로 확인한다).
- **경로** — 아무것도 디스크에 이름대로 풀지 않는다(바이트를 읽어 내용 주소로 저장한다). 그래도
  `../` · 절대 경로 · 링크는 「읽지 않음」 으로 드러낸다.
- **숨김 파일** — `__MACOSX/` · `._*` · `.DS_Store` · `Thumbs.db` 는 세기만 하고 줄에
  안 싣는다.
- **한글 이름** — Windows 탐색기의 「압축 폴더」 는 이름을 CP949 로 적고 UTF-8 표시(0x800)를
  안 붙인다. 파이썬은 그것을 CP437 로 읽어 「ÇÑ±Û」 처럼 깨진다 — 바이트로 되돌려 UTF-8 →
  CP949 순으로 읽는다. 7-Zip 이 붙이는 유니코드 경로(0x7075)가 있으면 그것을 쓴다. macOS 는
  한글을 풀어 쓴 꼴(NFD)로 적어 이름 비교가 어긋난다 — NFC 로 모은다.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import unicodedata
import uuid
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.files import services as files_services
from app.modules.files.models import Attachment
from app.modules.objects import attachments as object_attachments
from app.modules.objects import resolve, system
from app.modules.objects.services import properties_of
from app.modules.ontology import managed
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import images
from app.shared.errors import AppError, Conflict, NotFound, code

#: zip 안의 항목 수(폴더 포함) 상한 — 풀기 전에 본다.
MAX_ENTRIES = 2000
#: 머리에 적힌 풀린 크기의 합 상한. 작업 파일(zip)은 `job_file_max_bytes`(기본 100MB)까지인데,
#: 사진(JPEG)은 거의 안 줄어드니 정상인 zip 은 이것에 한참 못 미친다 — 넘는 것은 폭탄이다.
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
#: 칸에 이미 사진이 있을 때 — 건너뜀(기본) · 교체 · 추가.
EXISTING_MODES = ("skip", "replace", "add")
#: 계획에서 붙는 줄.
WRITES = ("attach", "replace")
#: 진행률을 이만큼마다 적는다 — 줄마다 적으면 2천 장에 짧은 갱신이 2천 번이다.
PROGRESS_EVERY = 20

_HIDDEN_FILES = {"thumbs.db", "desktop.ini"}
#: 뒤 번호 — `_2` · ` (2)` · `(2)`. 두 자리까지만. **`-2` 는 번호로 안 읽는다** — 품번은
#: `ABC-01` 처럼 하이픈 뒤 두 자리가 흔해서, 아직 안 들어온 `ABC-01` 의 사진이 `ABC` 에
#: 붙는다(2026-10-08).
_SUFFIX = re.compile(r"^(?P<base>.+?)(?:\s*\(\d{1,2}\)|_\d{1,2})$")
_DRIVE = re.compile(r"^[A-Za-z]:")
_UTF8_FLAG = 0x800
_ENCRYPTED_FLAG = 0x1
_UNICODE_PATH_EXTRA = 0x7075
#: 항목을 풀다 나는 것 — CRC 불일치 · 깨진 압축 · 모르는 압축 방식(Deflate64 등) · 암호.
_BROKEN = (
    zipfile.BadZipFile,
    zlib.error,
    NotImplementedError,
    RuntimeError,
    EOFError,
    OSError,
)

_HOW = {
    "id": "id",
    "key": "식별자",
    "alias": "별칭",
    "label": "이름",
    "folder": "폴더 이름",
    "suffix": "뒤 번호를 뗀 이름",
}

Progress = Callable[[str, int, int], None]


@dataclass
class Row:
    """계획의 한 줄 — zip 안의 파일 하나."""

    name: str
    """zip 안의 경로(한글 이름을 풀어 읽은 것)."""
    status: str
    size_bytes: int = 0
    object_id: str | None = None
    object_label: str = ""
    object_key: str | None = None
    matched_by: str = ""
    """`key` · `alias` · `label` · `id` · `folder` · `suffix` — 무엇으로 찾았나."""
    message: str = ""
    replaces: int = 0
    """교체면 떼는 사진 수(그 객체의 칸에 있던 것)."""
    attachment_id: str | None = None
    """적용 뒤 — 붙은 첨부."""


@dataclass(frozen=True)
class _Entry:
    info: zipfile.ZipInfo
    name: str


def require_target(db: Session, object_type: ObjectType, field: str) -> PropertyDef:
    """사진을 붙일 수 있는 타입 · 칸인가 — **넣는 순간에** 거절한다(몇 분 뒤 「실패」 가
    아니라). 작업도 돌 때 다시 본다(그 사이 칸이 지워질 수 있다)."""
    if system.is_system(object_type):
        raise Conflict(
            code("OBJECTS", 120),
            f"{object_type.label}은(는) 다른 표를 비추는 타입이라 사진을 업로드하지 않습니다.",
        )
    managed.require_objects_editable(object_type, what="사진을 업로드하지")
    files = [one for one in properties_of(db, object_type.id) if one.data_type == "file"]
    found = next((one for one in files if one.key == field), None)
    if found is None:
        choices = ", ".join(f"{one.key}({one.label})" for one in files) or "(없음)"
        raise Conflict(
            code("OBJECTS", 121),
            f"{object_type.label}에는 파일 속성 「{field}」 가 없습니다 — 선택할 수 있는 것: "
            f"{choices}.",
            details={"type_slug": object_type.slug, "field": field},
        )
    return found


# --- zip 읽기 ------------------------------------------------------------------------


def _unicode_path(info: zipfile.ZipInfo, raw: bytes) -> str | None:
    """Info-ZIP 유니코드 경로(0x7075) — 7-Zip 등이 옛 이름 곁에 UTF-8 이름을 함께 적는다. 옛
    이름의 CRC 가 맞을 때만 쓴다(이름을 고친 뒤 안 맞게 된 것은 버린다 — 규격의 규칙)."""
    extra = info.extra
    index = 0
    while index + 4 <= len(extra):
        tag, size = struct.unpack("<HH", extra[index : index + 4])
        body = extra[index + 4 : index + 4 + size]
        index += 4 + size
        if tag != _UNICODE_PATH_EXTRA or len(body) < 5 or body[0] != 1:
            continue
        if struct.unpack("<I", body[1:5])[0] != zlib.crc32(raw):
            return None
        try:
            return body[5:].decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def entry_name(info: zipfile.ZipInfo) -> str:
    """zip 항목의 이름 — **한글이 깨지지 않게.**

    UTF-8 표시가 없으면 파이썬은 CP437 로 읽는다. Windows 탐색기(한국어)의 zip 은 CP949 로
    적으므로 「ÇÑ±Û.jpg」 가 된다 — 바이트로 되돌려 UTF-8(표시 없이 UTF-8 로 적는 도구가 있다)
    → CP949 순으로 읽는다. UTF-8 은 엄격해서 CP949 바이트를 UTF-8 로 잘못 읽는 일은 드물다.
    """
    if info.flag_bits & _UTF8_FLAG:
        name = info.orig_filename
    else:
        raw = info.orig_filename.encode("cp437")
        name = _unicode_path(info, raw) or _decoded(raw)
    # macOS 는 한글을 풀어 쓴 꼴(NFD)로 적는다 — 그대로 두면 「사진」 과 같은 글자가 아니다.
    return unicodedata.normalize("NFC", name.replace("\\", "/"))


def _decoded(raw: bytes) -> str:
    """표시 없는 이름의 바이트 — UTF-8 → CP949 → CP437(어떤 바이트든 읽힌다) 순."""
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp437")


def _open(data: bytes) -> tuple[zipfile.ZipFile, list[_Entry]]:
    """zip 을 열고 항목을 이름 차례로 — **풀기 전에** 수 · 크기를 본다."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (*_BROKEN, zipfile.LargeZipFile, ValueError, struct.error):
        raise Conflict(
            code("OBJECTS", 123),
            "zip 파일로 읽을 수 없습니다 — 사진을 선택한 뒤 「압축(zip) 폴더로 보내기」 로 "
            "다시 만들어 업로드하세요.",
        ) from None
    if len(infos) > MAX_ENTRIES:
        raise Conflict(
            code("OBJECTS", 124),
            f"zip 안의 항목이 너무 많습니다({len(infos):,}개). 한 번에 {MAX_ENTRIES:,}개"
            "까지 — 나눠 업로드하세요.",
            details={"entries": len(infos), "max_entries": MAX_ENTRIES},
        )
    total = sum(max(info.file_size, 0) for info in infos)
    if total > MAX_TOTAL_BYTES:
        raise Conflict(
            code("OBJECTS", 124),
            f"zip 의 압축을 풀면 {total // (1024 * 1024):,}MB 입니다. 한 번에 "
            f"{MAX_TOTAL_BYTES // (1024 * 1024):,}MB 까지 — 나눠 업로드하세요.",
            details={"uncompressed_bytes": total, "max_bytes": MAX_TOTAL_BYTES},
        )
    entries = [_Entry(info, entry_name(info)) for info in infos if not info.is_dir()]
    entries.sort(key=lambda one: one.name)
    return archive, entries


def _hidden(name: str) -> bool:
    """macOS · Windows 가 사진 곁에 끼워 넣는 것 — 세기만 하고 줄에 안 싣는다."""
    parts = [one for one in name.split("/") if one not in ("", ".", "..")]
    if any(one == "__MACOSX" or one.startswith(".") for one in parts):
        return True
    return bool(parts) and parts[-1].lower() in _HIDDEN_FILES


def _refused(entry: _Entry) -> str | None:
    """읽지 않을 항목이면 그 까닭."""
    name = entry.name
    if not name.strip("/ "):
        return "이름이 없는 항목입니다 — 읽지 않습니다."
    if name.startswith("/") or _DRIVE.match(name) or ".." in name.split("/"):
        return "zip 밖을 가리키는 경로입니다 — 읽지 않습니다."
    if stat.S_ISLNK(entry.info.external_attr >> 16):
        return "링크(바로 가기)입니다 — 읽지 않습니다."
    if entry.info.flag_bits & _ENCRYPTED_FLAG:
        return "암호가 설정된 항목입니다 — 암호 없이 다시 압축하세요."
    return None


def _read(archive: zipfile.ZipFile, entry: _Entry) -> bytes | None:
    """항목의 바이트 — 머리에 적힌 크기까지만 풀린다. 깨졌으면 None."""
    try:
        return archive.read(entry.info)
    except _BROKEN:
        return None


# --- 이름으로 찾기 ---------------------------------------------------------------------


@dataclass
class _Found:
    status: str
    hit: resolve.Hit | None = None
    how: str = ""
    message: str = ""


class _Finder:
    """파일 이름 → 객체. 같은 이름은 한 번만 묻는다(한 폴더의 사진 스무 장이 같은 폴더 이름을
    스무 번 묻지 않게)."""

    def __init__(self, db: Session, user: User, object_type: ObjectType) -> None:
        self.db = db
        self.user = user
        self.object_type = object_type
        self._asked: dict[str, resolve.Resolution] = {}

    def _ask(self, text: str) -> resolve.Resolution:
        if text not in self._asked:
            self._asked[text] = resolve.by_name(self.db, self.user, self.object_type, text)
        return self._asked[text]

    def find(self, name: str) -> _Found:
        parts = [one for one in name.split("/") if one]
        base = parts[-1]
        stem = base.rsplit(".", 1)[0] if "." in base[1:] else base
        tries: list[tuple[str, str]] = [(stem.strip(), "")]
        if len(parts) > 1:
            tries.append((parts[-2].strip(), "folder"))
        suffixed = _SUFFIX.match(stem.strip())
        if suffixed:
            tries.append((suffixed.group("base").strip(), "suffix"))
        guesses: list[resolve.Hit] = []
        for text, how in tries:
            if not text:
                continue
            got = self._ask(text)
            if got.match == "exact" and got.object is not None:
                return _Found("matched", got.object, how or got.object.matched_by)
            if got.match == "candidates" and got.candidates:
                how_text = _HOW.get(got.candidates[0].matched_by, "이름")
                if got.candidates[0].matched_by != "contains":
                    names = ", ".join(_shown(one) for one in got.candidates[:5])
                    more = " 외" if len(got.candidates) > 5 or got.truncated else ""
                    return _Found(
                        "ambiguous",
                        message=(
                            f"「{text}」 에 맞는 것이 여럿입니다({how_text} 기준): "
                            f"{names}{more} — 파일 이름을 식별자로 바꿔 다시 업로드하세요."
                        ),
                    )
                guesses = guesses or got.candidates
        message = f"「{stem}」 에 맞는 {self.object_type.label}이(가) 없습니다."
        if guesses:
            message += f" 이름에 포함된 것: {', '.join(_shown(one) for one in guesses[:3])}"
            message += " — 맞으면 파일 이름을 그 식별자로 바꾸세요."
        return _Found("not_found", message=message)


def _shown(hit: resolve.Hit) -> str:
    return f"{hit.label}({hit.key})" if hit.key else hit.label


# --- 계획 · 적용 ----------------------------------------------------------------------


@dataclass
class _Target:
    """줄이 붙을 객체 하나 — 권한 · 칸에 있던 사진은 객체마다 한 번만 본다."""

    refused: str | None
    existing: int


def _existing(db: Session, object_id: uuid.UUID, field: str) -> list[Attachment]:
    return list(
        db.scalars(
            select(Attachment).where(
                Attachment.owner_table == "objects",
                Attachment.owner_id == object_id,
                Attachment.owner_field == field,
                Attachment.deleted_at.is_(None),
            )
        )
    )


def _target(db: Session, user: User, object_id: uuid.UUID, field: str) -> _Target:
    try:
        object_attachments.owner(db, user, object_id, field)
    except AppError as refused:
        return _Target(refused=refused.message, existing=0)
    count = (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(
                Attachment.owner_table == "objects",
                Attachment.owner_id == object_id,
                Attachment.owner_field == field,
                Attachment.deleted_at.is_(None),
            )
        )
        or 0
    )
    return _Target(refused=None, existing=int(count))


def _plan(
    db: Session,
    user: User,
    object_type: ObjectType,
    prop: PropertyDef,
    mode: str,
    archive: zipfile.ZipFile,
    entries: list[_Entry],
    progress: Progress,
    *,
    stage: str,
) -> tuple[list[Row], list[_Entry], int]:
    """파일마다 무엇이 될지 — **아무것도 바꾸지 않는다.** (줄들, 줄마다의 항목, 숨김 파일 수).

    항목을 줄과 나란히 돌려준다 — zip 에는 같은 이름이 둘일 수 있어 이름으로 되찾으면 둘째
    줄이 첫째의 사진을 붙인다."""
    finder = _Finder(db, user, object_type)
    targets: dict[uuid.UUID, _Target] = {}
    rows: list[Row] = []
    sources: list[_Entry] = []
    hidden = 0
    for index, entry in enumerate(entries):
        if index % PROGRESS_EVERY == 0:
            progress(stage, index, len(entries))
        if _hidden(entry.name):
            hidden += 1
            continue
        row = Row(name=entry.name, status="", size_bytes=entry.info.file_size)
        rows.append(row)
        sources.append(entry)
        refused = _refused(entry)
        if refused:
            row.status, row.message = "bad_entry", refused
            continue
        if entry.info.file_size > files_services.MAX_BYTES:
            row.status = "too_large"
            row.message = (
                f"한 파일은 {files_services.MAX_BYTES // (1024 * 1024)}MB 까지입니다 — 줄여서 "
                "다시 업로드하세요."
            )
            continue
        body = _read(archive, entry)
        if body is None:
            row.status = "bad_entry"
            row.message = "읽을 수 없는 항목입니다 — 압축이 깨졌거나 지원하지 않는 방식입니다."
            continue
        if not body or images.inspect(body) is None:
            row.status = "not_image"
            row.message = (
                f"{files_services.IMAGE_KINDS} 로 읽히지 않습니다(빈 파일 · 깨진 그림 · "
                "너무 큰 그림 포함)."
            )
            continue
        found = finder.find(entry.name)
        if found.hit is None:
            row.status, row.message = found.status, found.message
            continue
        hit = found.hit
        row.object_id, row.object_label, row.object_key = str(hit.id), hit.label, hit.key
        row.matched_by = found.how
        target = targets.get(hit.id)
        if target is None:
            target = targets[hit.id] = _target(db, user, hit.id, prop.key)
        if target.refused is not None:
            row.status, row.message = "forbidden", target.refused
            continue
        if found.how in ("folder", "suffix"):
            row.message = f"{_HOW[found.how]}({hit.label})으로 찾았습니다. "
        if target.existing and mode == "skip":
            row.status = "skip"
            row.message += f"이 칸에 이미 파일 {target.existing}개가 있어 건너뜁니다."
        elif target.existing and mode == "replace":
            row.status, row.replaces = "replace", target.existing
            row.message += f"이 칸에 있던 파일 {target.existing}개를 해제하고 업로드합니다."
        else:
            row.status = "attach"
            if target.existing:
                row.message += f"이 칸에 있던 파일 {target.existing}개는 그대로 둡니다."
        row.message = row.message.strip()
    progress(stage, len(entries), len(entries))
    return rows, sources, hidden


def fingerprint(rows: list[Row], *, field: str, mode: str) -> str:
    """계획의 지문 — 미리 본 것과 적용하는 것이 **같은 계획**인지(파일 · 무엇이 될지 · 어느
    객체 · 몇 장을 떼나)."""
    body = [(one.name, one.status, one.object_id, one.replaces) for one in rows]
    raw = json.dumps({"field": field, "mode": mode, "rows": body}, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


#: 줄 이름 → 사람의 말. 요약 한 줄에 쓴다(화면은 같은 표를 따로 둔다).
LABELS = {
    "attach": "업로드",
    "replace": "교체",
    "skip": "건너뜀",
    "not_found": "못 찾음",
    "ambiguous": "여럿에 맞음",
    "not_image": "이미지 아님",
    "too_large": "너무 큼",
    "forbidden": "권한 없음",
    "bad_entry": "읽지 않음",
}


def _summary(counts: dict[str, int], hidden: int, *, applied: bool) -> str:
    head = "적용 — " if applied else "계획 — "
    parts = [f"{LABELS[key]} {counts[key]:,}" for key in LABELS if counts.get(key)]
    if hidden:
        parts.append(f"숨김 파일 {hidden:,}개 제외")
    return head + (" · ".join(parts) if parts else "zip 안에 사진이 없습니다")


def run(
    db: Session,
    user: User,
    params: dict[str, Any],
    data: bytes,
    *,
    progress: Progress,
) -> dict[str, Any]:
    """작업 `objects_photos` 의 본문 — 계획, 또는 (`apply`) 지문을 확인하고 적용.

    **커밋하지 않는다** — 워커가 끝에서 한 번 한다. 적용 중 실패하면 붙인 행은 모두 무르고,
    먼저 저장소에 쓴 파일은 아무도 안 가리키는 채 남아 고아 정리가 하루 뒤에 지운다.
    """
    slug = str(params.get("type_slug") or "")
    object_type = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if object_type is None:
        raise NotFound(code("OBJECTS", 10), f"타입을 찾을 수 없습니다: {slug}")
    field = str(params.get("field") or "")
    prop = require_target(db, object_type, field)
    mode = str(params.get("existing") or "skip")
    if mode not in EXISTING_MODES:
        raise Conflict(
            code("OBJECTS", 122),
            f"이미 사진이 있는 칸의 처리는 {', '.join(EXISTING_MODES)} 중 하나입니다: "
            f"{mode!r}",
        )
    applied = bool(params.get("apply"))
    archive, entries = _open(data)
    with archive:
        rows, sources, hidden = _plan(
            db,
            user,
            object_type,
            prop,
            mode,
            archive,
            entries,
            progress,
            # 적용도 계획부터 다시 세운다 — 그것이 미리 본 것과 같은지 지문으로 본다.
            stage="확인" if applied else "계획",
        )
        mark = fingerprint(rows, field=field, mode=mode)
        if applied:
            wanted = params.get("fingerprint")
            if wanted and wanted != mark:
                raise Conflict(
                    code("JOBS", 20),
                    "미리 본 것과 달라졌습니다 — 그 사이에 누군가 사진을 업로드했거나 객체를 "
                    "변경했습니다. 아무것도 업로드하지 않았으니 다시 미리 보고 적용하세요.",
                )
            _apply(db, user, prop, archive, list(zip(rows, sources, strict=True)), progress)
    counts: dict[str, int] = {}
    for one in rows:
        counts[one.status] = counts.get(one.status, 0) + 1
    writes = sum(counts.get(key, 0) for key in WRITES)
    return {
        "photos": True,
        "applied": applied,
        "ok": writes > 0,
        "type_slug": object_type.slug,
        "field": prop.key,
        "field_label": prop.label,
        "existing": mode,
        "files": [asdict(one) for one in rows],
        # `counts` 가 아니라 `tally` — 작업의 공통 판정(홈의 「적용 대기」 · `make_apply`)은
        # `counts` 를 일괄 입력의 새로 · 고침 · 오류로 읽는다. 그 이름이면 붙는 사진이 있어도
        # 「바뀌는 것 없음」 으로 빠진다. 적용할 것이 있나는 `ok` 가 말한다.
        "tally": counts,
        "hidden": hidden,
        "summary": _summary(counts, hidden, applied=applied),
        "fingerprint": mark,
    }


def _apply(
    db: Session,
    user: User,
    prop: PropertyDef,
    archive: zipfile.ZipFile,
    planned: list[tuple[Row, _Entry]],
    progress: Progress,
) -> None:
    """계획대로 붙인다 — 지문을 확인한 **뒤에만** 부른다(쓰기 전에 봐야 한다)."""
    todo = [(row, entry) for row, entry in planned if row.status in WRITES]
    cleared: set[str] = set()
    for index, (row, entry) in enumerate(todo):
        if index % PROGRESS_EVERY == 0:
            progress("적용", index, len(todo))
        assert row.object_id is not None  # 붙는 줄에는 객체가 있다
        object_id = uuid.UUID(row.object_id)
        if row.status == "replace" and row.object_id not in cleared:
            for old in _existing(db, object_id, prop.key):
                files_services.detach(db, user=user, attachment=old)
            cleared.add(row.object_id)
        body = _read(archive, entry)
        assert body is not None  # 계획에서 읽혔다 — 같은 zip 이다
        made = files_services.attach(
            db,
            user=user,
            owner_table="objects",
            owner_id=object_id,
            owner_field=prop.key,
            workspace_slug=None,
            filename=row.name.rsplit("/", 1)[-1],
            content_type=None,
            stream=io.BytesIO(body),
        )
        row.attachment_id = str(made.id)
    progress("적용", len(todo), len(todo))
