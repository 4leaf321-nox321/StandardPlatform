"""문서 올리기 — 로컬 마크다운(과 그 그림)을 **파일 그대로** 플랫폼 객체의 문서 칸에 넣는다
(ADR 0099).

플랫폼 MCP 의 `document_write` 는 본문이 도구 인자다 — 모델이 글자 그대로 옮겨 적어야 해서
Word 569건 · 270만 자면 토큰이 들고 옮기다 틀린다. 여기서는 키트가 파일을 읽어 zip 하나(목록
· 마크다운 · 그림)로 묶어 플랫폼 작업(`documents_push`)으로 올린다. 플랫폼이 계획을
세우고(무엇이 새로 · 고침 · 그대로 · 오류인지), **사람이 그 계획을 보고 「좋다」 한 뒤에**
적용한다 — 대화에서 말하면 AI 가 `apply`(MCP `document_apply`)로, 화면을 보고 싶으면 그 플랫폼
「작업」 에서 「적용」. 적용하면 그림을 붙이고(같은 그림이 이미 붙어 있으면 그것) 본문의 그림
자리를 `photo:<첨부 id>` 로 바꿔 쓴다.

대조(`check`)는 본문을 다시 받지 않는다 — 서버의 판 · sha256(`?body=false`)과 붙은 그림의
sha256 으로, 로컬 파일을 플랫폼이 쓰는 꼴로 바꾼 것의 sha256 을 견준다.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import sp_pipeline as pipeline
from sp_pipeline import Stop

#: 작업 폴더 안 — 올린 기록(`push.json`)을 둔다.
PUSH_DIR = Path("docs") / ".push"
RECORD = "push.json"
FORMAT = "sp-documents/1"
KIND = "documents_push"
#: 한 번에 올리는 zip 의 크기 — 넘으면 나눠 여러 작업으로(플랫폼 상한 2GB 보다 넉넉히 작게,
#: 키트가 메모리에 들고 보낸다).
ZIP_MAX_BYTES = 400 * 1024 * 1024
#: 한 작업의 문서 수 — 플랫폼 상한(2000)보다 작게.
DOCUMENTS_PER_JOB = 1000
#: 한 zip 의 항목 수(목록 + 마크다운 + 서로 다른 그림) — 플랫폼은 2000 개를 넘는 zip 을 통째로
#: 거절한다(`objects/photos.MAX_ENTRIES`). 문서 1000 편에 그림이 1000 장을 넘으면 작업 전체가
#: 막혔다(0.4.107 점검) — 넉넉히 작게 나눈다.
ZIP_MAX_ENTRIES = 1900
#: 한 편의 상한 — 플랫폼과 같다.
MAX_CHARS = 1_000_000
#: 본문 안의 그림 — 플랫폼(`objects/docpush.IMAGE`)과 **글자까지 같은** 꼴. 캡션 · 경로에 `[` ·
#: `]` · `(` · `)` 가 없다 — 옛 꼴은 `![](` 가 되풀이되면 되짚기가 제곱으로 늘어(8만 자에 30초)
#: 계획 하나가 워커를 붙잡았다.
IMAGE = re.compile(
    r"(?P<head>!\[[^\[\]\n]*\]\()(?P<target>[^()\[\]\s?]+)(?P<query>\?w=\d{1,3})?\)"
)
REMOTE = ("photo:", "http://", "https://", "data:", "/", "#", "mailto:")
#: 본문에 그대로 남겨도 되는 그림 자리 — 이미 플랫폼의 것과 바깥 주소. 그 밖은 올릴 파일이어야
#: 한다(아니면 로컬 경로가 본문에 남는다).
KEPT = ("photo:", "http://", "https://")
INLINE_CODE = re.compile(r"`[^`\n]*`")
FENCES = ("```", "~~~")
#: 올린 기록의 자리 — `document_push` 가 남긴 것만 적용 · 대조에 받는다.
RECORD_PLACE = re.compile(r"^docs/\.push/[^/]+/push\.json$")
#: 그림 파일의 확장자 — 이것만 그림으로 싣는다(플랫폼이 다시 열어 본다).
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
#: 결과에 싣는 줄 — 나머지는 `push.json` 과 그 플랫폼 화면.
ROWS_SHOWN = 30


def digest(body: str) -> str:
    """플랫폼이 저장하는 꼴(줄바꿈 `\\n`)의 UTF-8 sha256 — 플랫폼의 답과 견준다."""
    return hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()


@dataclass
class Item:
    """올릴 문서 하나 — 어느 객체의 어느 문서 칸에 어느 파일을."""

    type_slug: str
    object_id: str
    key: str
    path: str
    """작업 폴더에서 본 마크다운 경로(예: `docs/백서/draft.md`)."""
    base_revision: int | None = None
    """읽고 고친 판 — 비우면 올릴 때의 지금 판(그 사이 오르면 적용이 거절한다)."""
    note: str = ""
    allow_empty: bool = False
    """빈 본문(공백뿐)을 올려 **문서를 비운다** — 사람이 그 문서를 비우라고 말했을 때만. 없으면
    빈 본문은 키트가 막는다(옮기다 빠진 본문이 판으로 덮이지 않게)."""


@dataclass
class Prepared:
    item: Item
    body: str = ""
    images: dict[str, Path] = field(default_factory=dict)
    """본문의 그림 자리(경로 그대로) → 로컬 파일."""
    image_sha: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    size: int = 0


def items_of(raw: Any) -> list[Item]:
    """목록(JSON) → 문서들. `[{...}]` 또는 `{"documents": [{...}]}`."""
    rows = raw.get("documents") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        raise Stop('목록은 [{"type_slug","object_id","key","path"}, …] 이어야 합니다')
    out: list[Item] = []
    for index, one in enumerate(rows, start=1):
        if not isinstance(one, dict):
            raise Stop(f"목록의 {index}번째 줄이 객체({{...}})가 아닙니다")
        missing = [
            name for name in ("type_slug", "object_id", "key", "path") if not one.get(name)
        ]
        if missing:
            raise Stop(f"목록의 {index}번째 줄에 {', '.join(missing)} 가 없습니다")
        base = one.get("base_revision")
        if base is not None and (
            not isinstance(base, int) or isinstance(base, bool) or base < 0
        ):
            raise Stop(f"목록의 {index}번째 줄의 base_revision 은 0 이상의 정수입니다")
        empty = one.get("allow_empty", False)
        if not isinstance(empty, bool):
            raise Stop(f"목록의 {index}번째 줄의 allow_empty 는 true · false 입니다")
        out.append(
            Item(
                str(one["type_slug"]),
                str(one["object_id"]),
                str(one["key"]),
                str(one["path"]),
                base,
                str(one.get("note") or "")[:200],
                empty,
            )
        )
    return out


def _inside(folder: Path, relative: str) -> Path | None:
    path = (folder / relative).resolve()
    return path if folder.resolve() in path.parents else None


def record_path(folder: Path, relative: str = "") -> Path:
    """올린 기록 — 비우면 마지막 것. **`document_push` 가 남긴 자리
    (`docs/.push/<때>/push.json`)만** 받는다: 작업 폴더의 아무 JSON 이나 기록으로 주면 거기
    적힌 작업 번호를 적용했다(0.4.107 점검)."""
    if not relative:
        return latest(folder)
    path = _placed(folder, (folder / relative).resolve())
    if not path.is_file():
        raise Stop(f"올린 기록이 없습니다: {relative}")
    return path


def _placed(folder: Path, path: Path) -> Path:
    base = folder.resolve()
    try:
        shown = path.resolve().relative_to(base).as_posix()
    except ValueError:
        shown = ""
    if not RECORD_PLACE.match(shown):
        raise Stop(
            f"document_push 가 남긴 기록(docs/.push/<때>/push.json)이 아닙니다: {path.name} — "
            "record 를 비우면 마지막 기록을 쓴다"
        )
    return path


def unconverted(body: str) -> list[str]:
    """그림 자리처럼 보이는데 **올리지 못하는 것** — 그대로 두면 로컬 경로가 본문에 남는다.

    올리는 그림 줄은 `![캡션](media/그림.png?w=60)` 꼴(`IMAGE`)이다. 제목(`"…"`) · 경로의 빈칸
    · 네 자리 폭 · 캡션의 `]` 처럼 그 꼴을 벗어난 줄은 키트도 플랫폼도 그림으로 읽지 않아, 예전
    에는 경로가 그대로 써지고 대조는 「같음」 이라 했다. `/` 로 시작하는 경로 · `data:` 도
    올리지 않는다. 코드 블록 · 인라인 코드 안은 보지 않는다. 줄마다 앞에서 뒤로 한 번만
    훑는다."""
    found: list[str] = []
    fenced = False
    for number, line in enumerate(body.split("\n"), start=1):
        if line.lstrip().startswith(FENCES):
            fenced = not fenced
            continue
        if fenced or "![" not in line:
            continue
        text = INLINE_CODE.sub(lambda hit: " " * len(hit.group(0)), line)
        for hit in IMAGE.finditer(text):
            target = hit.group("target")
            lowered = target.lower()
            if lowered.startswith(KEPT) or not lowered.startswith(REMOTE):
                continue
            if lowered.startswith("data:"):
                why = "본문에 박은 그림(data:)은 올리지 않습니다 — 파일로 두고 상대 경로로"
            elif target.startswith("/"):
                why = "절대 경로는 올리지 않습니다 — 마크다운 파일에서 본 상대 경로로(media/…)"
            else:
                why = "그림 파일을 가리키지 않습니다"
            found.append(f"{number}번째 줄 그림 `{target[:80]}`: {why}")
        rest = IMAGE.sub(lambda hit: " " * len(hit.group(0)), text)
        start = rest.find("![")
        while start >= 0:
            close = rest.find("](", start + 2)
            if close < 0:
                break
            if "][" in rest[start + 2 : close]:
                # 참조식 그림(`![logo][1]`) 뒤의 링크(`[안내](guide.md)`) — 그림 자리가 아니다.
                start = rest.find("![", start + 2)
                continue
            end = rest.find(")", close + 2)
            stop = end if end >= 0 else len(rest)
            target = rest[close + 2 : stop].strip().strip("<>").strip()
            if target and not target.lower().startswith(KEPT):
                shown = rest[start : stop + 1]
                shown = shown if len(shown) <= 80 else shown[:77] + "…"
                found.append(
                    f"{number}번째 줄 그림 자리 `{shown}` 를 그림으로 읽지 못했습니다 — "
                    "`![캡션](media/그림.png?w=60)` 꼴로: 경로에 빈칸 · 괄호 · "
                    '제목("…") 없이, 캡션에 [ ] 없이, 폭은 ?w=10~100'
                )
            start = rest.find("![", stop)
    return found


def prepare(folder: Path, items: list[Item]) -> list[Prepared]:
    """파일을 읽어 본다 — 없는 파일 · 폴더 밖 · 너무 김 · 개인 정보 꼴 · 없는 그림은 문제로."""
    out: list[Prepared] = []
    seen: dict[tuple[str, str], int] = {}
    for index, item in enumerate(items, start=1):
        one = Prepared(item)
        out.append(one)
        mark = (item.object_id, item.key)
        if mark in seen:
            one.problems.append(f"같은 객체 · 문서 칸을 {seen[mark]}번째 줄이 씁니다")
            continue
        seen[mark] = index
        try:
            uuid.UUID(item.object_id)
        except ValueError:
            one.problems.append(f"object_id 가 객체 id(uuid)가 아닙니다: {item.object_id}")
            continue
        path = _inside(folder, item.path)
        if path is None:
            one.problems.append(f"작업 폴더 밖의 파일입니다: {item.path}")
            continue
        if path.suffix.lower() not in pipeline.DOCUMENT_SUFFIXES:
            one.problems.append(f"마크다운(.md)이 아닙니다: {item.path}")
            continue
        try:
            body = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except FileNotFoundError:
            one.problems.append(f"파일이 없습니다: {item.path}")
            continue
        except (OSError, UnicodeDecodeError) as failure:
            one.problems.append(
                f"읽을 수 없습니다(UTF-8 이어야 한다): {item.path} — {failure}"
            )
            continue
        if len(body) > MAX_CHARS:
            one.problems.append(f"{len(body):,}자 — 한 편에 {MAX_CHARS:,}자까지")
            continue
        nul = pipeline.nul_line(body)
        if nul:
            # 플랫폼의 글자 칸은 NUL 을 못 담는다 — 예전에는 계획을 지나 적용에서 DataError 로
            # 작업 전체가 멈췄다(0.4.107 점검).
            one.problems.append(
                f"{nul}번째 줄에 NUL 문자(\\x00)가 있습니다 — 플랫폼이 저장하지 못합니다. "
                "지우고 올린다"
            )
            continue
        if not body.strip() and not item.allow_empty:
            # **빈 본문은 문서를 비운다** — 옮기다 빠진 본문이 판으로 덮이지 않게, 사람이 그
            # 문서를 비우라고 했을 때만(`allow_empty`) 올린다(2026-11-27 사용자 결정).
            one.problems.append(
                "본문이 비었습니다 — 올리면 그 문서가 빈 판으로 덮입니다. 사람이 그 문서를 "
                "비우라고 했을 때만 그 줄에 allow_empty 를 켠다"
            )
            continue
        one.body = body
        one.size = len(body.encode("utf-8"))
        found, maybe = pipeline.personal_info(body)
        if found:
            shown = " · ".join(found[:5]) + (f" 외 {len(found) - 5}" if len(found) > 5 else "")
            one.problems.append(f"개인 정보 꼴 {len(found)}곳 — {shown}. 가리거나 빼고 올린다")
        if maybe:
            one.warnings.append(f"확인할 꼴 {len(maybe)}곳 — " + " · ".join(maybe[:3]))
        odd = unconverted(body)
        if odd:
            one.problems.append(
                f"그림으로 못 읽는 그림 자리 {len(odd)}곳 — "
                + " / ".join(odd[:3])
                + (f" 외 {len(odd) - 3}" if len(odd) > 3 else "")
            )
        # 같은 파일(`./img/a.png` 와 `img/a.png`)은 한 번 읽고, 같은 내용(sha256)은 한 번
        # 싣는다 — 플랫폼이 같은 그림을 두 번 붙이지 않게.
        read: dict[Path, str] = {}
        for hit in IMAGE.finditer(body):
            target = hit.group("target")
            if target.lower().startswith(REMOTE) or target in one.images:
                continue
            local = (path.parent / unquote(target)).resolve()
            if folder.resolve() not in local.parents:
                one.problems.append(f"그림이 작업 폴더 밖에 있습니다: {target}")
                continue
            if local.suffix.lower() not in IMAGE_SUFFIXES:
                one.problems.append(f"그림 파일(PNG · JPEG · GIF · WebP)이 아닙니다: {target}")
                continue
            if local not in read:
                if not local.is_file():
                    one.problems.append(f"그림 파일이 없습니다: {target}")
                    continue
                data = local.read_bytes()
                sha = hashlib.sha256(data).hexdigest()
                if sha not in one.image_sha.values():
                    one.size += len(data)
                read[local] = sha
            one.images[target] = local
            one.image_sha[target] = read[local]
        distinct = len(set(one.image_sha.values()))
        if distinct > ZIP_MAX_ENTRIES - 2:
            one.problems.append(
                f"서로 다른 그림이 {distinct:,}장입니다 — 한 편에 "
                f"{ZIP_MAX_ENTRIES - 2:,}장까지(나눠 쓴다)"
            )
    return out


def _zip(chunk: list[Prepared]) -> bytes:
    """목록 · 마크다운 · 그림 — 그림은 내용 주소(`img/<sha256>.<확장자>`)로 한 번씩 싣고 본문의
    자리를 그쪽으로 바꾼다(문서마다 폴더가 달라도 겹치지 않는다). 플랫폼은 그 자리를 다시
    `photo:<첨부 id>` 로 바꾸므로 본문의 결과는 같다."""
    buffer = io.BytesIO()
    documents: list[dict[str, Any]] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # 내용(sha256)마다 한 항목 — 확장자만 다른 같은 그림도 한 자리를 가리켜, 플랫폼이 같은
        # 그림을 두 번 붙이지 않는다.
        stored: dict[str, str] = {}
        for index, one in enumerate(chunk, start=1):
            names: dict[str, str] = {}
            for target, local in one.images.items():
                sha = one.image_sha[target]
                name = stored.get(sha)
                if name is None:
                    name = f"img/{sha}{local.suffix.lower()}"
                    # 그림은 이미 눌러 담긴 꼴(PNG · JPEG)이라 다시 누르지 않는다.
                    archive.write(local, name, compress_type=zipfile.ZIP_STORED)
                    stored[sha] = name
                names[target] = f"../{name}"

            def swap(hit: re.Match[str], names: dict[str, str] = names) -> str:
                moved = names.get(hit.group("target"))
                if moved is None:
                    return hit.group(0)
                return f"{hit.group('head')}{moved}{hit.group('query') or ''})"

            arcname = f"docs/{index:05d}.md"
            archive.writestr(arcname, IMAGE.sub(swap, one.body).encode("utf-8"))
            item = one.item
            documents.append(
                {
                    "type_slug": item.type_slug,
                    "object_id": item.object_id,
                    "key": item.key,
                    "path": arcname,
                    "note": item.note,
                    **(
                        {"base_revision": item.base_revision}
                        if item.base_revision is not None
                        else {}
                    ),
                }
            )
        archive.writestr(
            "manifest.json",
            json.dumps({"format": FORMAT, "documents": documents}, ensure_ascii=False),
        )
    return buffer.getvalue()


def entries(chunk: list[Prepared]) -> int:
    """그 묶음의 zip 항목 수 — 목록 하나 + 마크다운마다 하나 + 서로 다른 그림마다 하나."""
    return 1 + len(chunk) + len({sha for one in chunk for sha in one.image_sha.values()})


def _chunks(ready: list[Prepared]) -> list[list[Prepared]]:
    """크기 · 문서 수 · **zip 항목 수**로 나눈다 — 어느 것도 플랫폼의 상한을 넘지 않게."""
    out: list[list[Prepared]] = [[]]
    size = 0
    shas: set[str] = set()
    for one in ready:
        mine = set(one.image_sha.values())
        grown = 1 + len(out[-1]) + 1 + len(shas) + len(mine - shas)
        if out[-1] and (
            size + one.size > ZIP_MAX_BYTES
            or len(out[-1]) >= DOCUMENTS_PER_JOB
            or grown > ZIP_MAX_ENTRIES
        ):
            out.append([])
            size = 0
            shas = set()
        out[-1].append(one)
        size += one.size
        shas |= mine
    return [one for one in out if one]


def _multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[str, bytes]:
    boundary = f"sp-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode("utf-8")
            + b"\r\n"
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{quote(filename)}"\r\nContent-Type: application/zip\r\n\r\n'.encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def _submit(server: str, token: str, data: bytes, filename: str) -> dict[str, Any]:
    content_type, body = _multipart({"kind": KIND, "params": "{}"}, filename, data)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "X-Client": pipeline.CLIENT,
    }
    status, answer = pipeline._send(
        server, "POST", pipeline.api_url(server, "/api/jobs"), headers, body, who="플랫폼"
    )
    if status != 202:
        pipeline._refused("플랫폼", status, answer)
    job = pipeline._wait_job(server, token, answer)
    if not isinstance(job.get("result"), dict):
        raise Stop(f"플랫폼 응답을 읽을 수 없습니다: {job!r}")
    return job


def push(
    folder: Path, items: list[Item], *, server: str, token: str, platform: str = ""
) -> dict[str, Any]:
    """미리 보기 — 파일을 읽어 묶어 올리고 플랫폼의 계획을 받는다. **아무것도 쓰지 않는다** —
    사람이 계획을 보고 「좋다」 한 뒤 `apply`(또는 그 플랫폼 화면 「작업」 의 「적용」)."""
    ready = prepare(folder, items)
    local = [one for one in ready if one.problems]
    if local:
        lines = [
            f"{one.item.path} → {one.item.type_slug}:{one.item.key}: "
            + " / ".join(one.problems)
            for one in local
        ]
        raise Stop(
            f"올리기 전에 고칠 것 {len(local)}건(플랫폼에 아무것도 보내지 않았습니다):\n"
            + "\n".join(lines[:ROWS_SHOWN])
            + (f"\n… 외 {len(lines) - ROWS_SHOWN}건" if len(lines) > ROWS_SHOWN else "")
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    record_dir = folder / PUSH_DIR / stamp
    record_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    offset = 0
    for part, chunk in enumerate(_chunks(ready), start=1):
        job = _submit(server, token, _zip(chunk), f"documents-{stamp}-{part}.zip")
        result = job["result"]
        jobs.append(
            {
                "job_id": str(job.get("id") or ""),
                "counts": result.get("counts") or {},
                "ok": bool(result.get("ok")),
                "link": pipeline.jobs_link(server, str(job.get("id") or "")),
            }
        )
        for planned in result.get("rows") or []:
            planned = dict(planned)
            at = int(planned.get("row") or 0) - 1
            if 0 <= at < len(chunk):
                planned["local_path"] = chunk[at].item.path
            planned["row"] = offset + int(planned.get("row") or 0)
            rows.append(planned)
        offset += len(chunk)
    record = {
        "at": stamp,
        "server": server,
        "platform": platform,
        "jobs": jobs,
        "documents": [
            {
                "type_slug": one.item.type_slug,
                "object_id": one.item.object_id,
                "key": one.item.key,
                "path": one.item.path,
                "local_sha256": digest(one.body),
                "images": dict(one.image_sha),
            }
            for one in ready
        ],
    }
    pipeline._write(record_dir / RECORD, record)
    counts: dict[str, int] = {}
    for job in jobs:
        for name, count in job["counts"].items():
            counts[name] = counts.get(name, 0) + int(count or 0)
    problems = [one for one in rows if one.get("action") == "error"]
    shown = (problems or rows)[:ROWS_SHOWN]
    return {
        "ok": all(job["ok"] for job in jobs),
        "counts": counts,
        "jobs": jobs,
        "record": (record_dir / RECORD).relative_to(folder).as_posix(),
        "rows": [
            {
                key: one.get(key)
                for key in (
                    "row",
                    "action",
                    "label",
                    "local_path",
                    "revision",
                    "chars",
                    "sha256",
                    "message",
                )
            }
            | {"images": len(one.get("images") or [])}
            # 플랫폼이 줄에 붙인 경고(있으면) — 사람에게 함께 보인다.
            | ({"warnings": one["warnings"]} if one.get("warnings") else {})
            for one in shown
        ],
        "rows_omitted": max(0, (len(problems) or len(rows)) - len(shown)),
        "warnings": (
            [f"{one.item.path}: {warning}" for one in ready for warning in one.warnings]
            + [
                f"{one.get('local_path') or one.get('label')}: {warning}"
                for one in rows
                for warning in (one.get("warnings") or [])
                if isinstance(warning, str)
            ]
        )[:ROWS_SHOWN],
    }


#: 적용이 끝난 줄의 상태 — 이것뿐이면 `all_applied`.
SETTLED = ("applied", "already", "unchanged")


def _not_pushable(plan: Any) -> str:
    """그 작업이 **적용할 수 있는 문서 올리기 계획**이 아니면 그 까닭 — 기록에 적힌 번호를 믿지
    않고 플랫폼에 묻는다(남의 계획 · 다른 종류의 작업을 이 길로 적용하지 않는다)."""
    if not isinstance(plan, dict):
        return "작업을 읽을 수 없습니다"
    if plan.get("kind") != KIND:
        return (
            f"문서 올리기의 계획이 아닙니다(종류 {plan.get('kind')}) — 이 기록으로 적용하지 "
            "않습니다"
        )
    if (plan.get("params") or {}).get("apply"):
        return "적용 작업입니다 — 계획 작업이 아닙니다"
    if plan.get("status") != "done":
        return f"계획이 아직 끝나지 않았습니다({plan.get('status')})"
    result = plan.get("result") if isinstance(plan.get("result"), dict) else {}
    errors = int((result.get("counts") or {}).get("error") or 0)
    if errors or not result:
        return (
            f"계획에 오류 줄이 {errors}개 있습니다 — 한 줄이라도 오류면 적용하지 않는다. 오류 "
            "줄을 빼거나 고쳐 다시 올린다"
        )
    return ""


def _writes(plan: dict[str, Any]) -> int:
    counts = (plan.get("result") or {}).get("counts") or {}
    return int(counts.get("create") or 0) + int(counts.get("update") or 0)


def apply(folder: Path, record_path: Path, *, server: str, token: str) -> dict[str, Any]:
    """**사람이 계획을 보고 「좋다」 한 뒤에만** — 올린 기록의 작업을 적용하고, 끝나면 서버
    본문과 견준다(`check`).

    작업이 여럿이면(크면 나눠 올린다) **먼저 전부를 본다** — 문서 올리기의 계획인가 · 끝났나 ·
    오류 줄이 없나. 하나라도 틀리면 **아무것도 넣지 않는다**(「한 줄이라도 오류면 적용 안
    함」 이 작업 사이에서도 지켜지게). 그다음 차례로 넣고, 그 사이 플랫폼이 뒤의 것을
    거절하면(경합 — 누가 그 문서를 고침) 거기서 멈추고 **어느 작업은 들어가고 어느 작업은 안
    들어갔는지** 그대로 말한다. 화면에서 이미 적용했으면 건너뛰고(JOBS-22), 넣었는데 아직
    대기열에 있는 적용은 「적용함」 으로 적어 두지 않는다 — 다음에 부르면 그 적용에 다시
    묻는다."""
    record_path = _placed(folder, record_path)
    record = pipeline._read_json(record_path, [])
    if not isinstance(record, dict) or not record.get("jobs"):
        raise Stop(f"올린 기록을 읽을 수 없습니다: {record_path}")
    if record.get("server") and record["server"] != server:
        raise Stop(
            f"올린 플랫폼({record['server']})과 다른 플랫폼입니다 — 그 플랫폼으로 "
            "다시 올리세요"
        )
    jobs: list[dict[str, Any]] = [one for one in record["jobs"] if isinstance(one, dict)]
    if len(jobs) != len(record["jobs"]):
        raise Stop(f"올린 기록을 읽을 수 없습니다: {record_path}")
    # **번호는 uuid 꼴만** — 아무 플랫폼에도 묻기 전에, 주소에 넣기 전에. 틀린 줄은 **그 줄만**
    # 「거절」 로 두고 묻지 않는다. 예전에는 한 줄 때문에 적용 전체가 멈춤(Stop)으로 끝나 어느
    # 줄이 깨졌는지 · 나머지는 어떻게 됐는지가 답에 안 남았다. 거절이 하나라도 있으면 아래(2)의
    # 규칙대로 **아무것도 넣지 않는다**(나머지는 「넣지 않음」 과 그 까닭).
    lines: list[dict[str, Any]] = []
    blocked = 0
    for job in jobs:
        try:
            job["job_id"] = pipeline.job_uuid(job.get("job_id"), "계획 작업의 번호")
            if job.get("apply_job_id"):
                job["apply_job_id"] = pipeline.job_uuid(
                    job["apply_job_id"], "적용 작업의 번호"
                )
        except Stop as failure:
            lines.append(
                {
                    "job_id": str(job.get("job_id") or "")[:80],
                    "state": "refused",
                    "message": f"올린 기록의 이 줄이 깨졌습니다 — {failure}. 이 줄의 문서들은 "
                    "document_push 로 다시 올린다",
                }
            )
            blocked += 1
            continue
        lines.append({"job_id": job["job_id"]})

    def finish(message: str) -> dict[str, Any]:
        for line in lines:
            line.setdefault("state", "not_applied")
        pipeline._write(record_path, record)
        return {
            "applied": lines,
            "all_applied": all(one["state"] in SETTLED for one in lines),
            "waiting": any(one["state"] == "waiting" for one in lines),
            "message": message,
            "check": check(folder, record_path, server=server, token=token),
        }

    # 1. 앞서 넣은 적용 — 끝났나 다시 묻는다(대기 중이던 것은 「적용함」 으로 적지 않았다).
    stuck = ""
    for job, line in zip(jobs, lines, strict=True):
        if "state" in line or not job.get("apply_job_id"):
            continue
        if job.get("apply_state") == "applied":
            line.update(state="already", apply_job_id=job["apply_job_id"])
            continue
        outcome = pipeline.apply_job_state(
            server, token, job["apply_job_id"], plan_job_id=job.get("job_id")
        )
        if outcome["state"] == "applied":
            job["apply_state"] = "applied"
            line.update(state="already", apply_job_id=job["apply_job_id"])
        elif outcome["state"] == "failed":
            # 실패한 적용은 잠그지 않는다 — 다음에 부르면 다시 본 뒤 넣는다.
            line.update(state="failed", apply_job_id=job.pop("apply_job_id"))
            line["message"] = outcome["message"]
            job["apply_state"] = "failed"
            stuck = stuck or "앞서 넣은 적용이 실패했습니다 — 까닭을 사람에게 말한다"
        else:
            job["apply_state"] = "waiting"
            line.update(
                state="waiting",
                apply_job_id=job["apply_job_id"],
                message=pipeline.waiting_message(server, job["apply_job_id"]),
            )
            stuck = stuck or "앞서 넣은 적용이 아직 끝나지 않았습니다 — 끝난 뒤 다시 부른다"
    if stuck:
        return finish(stuck + "(나머지 작업은 이번에 넣지 않았습니다)")

    # 2. 넣을 작업 **전부를 먼저 본다** — 하나라도 틀리면 아무것도 넣지 않는다.
    todo: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for job, line in zip(jobs, lines, strict=True):
        if "state" in line:
            continue
        plan = pipeline._get_json(
            server, token, pipeline.api_path("api", "jobs", job["job_id"]), who="플랫폼"
        )
        reason = _not_pushable(plan)
        if reason:
            line.update(state="refused", message=reason)
            blocked += 1
            continue
        if not _writes(plan):
            line.update(state="unchanged", message="쓸 것이 없습니다(전부 그대로)")
            continue
        earlier = str(plan.get("applied_by") or "")
        if earlier:
            # 화면에서 먼저 넣은 적용 — 끝났나 그 적용 작업에 묻는다(대기 중이면
            # 「적용함」 아님).
            earlier = pipeline.job_uuid(earlier, "적용 작업의 번호")
            seen = pipeline.apply_job_state(server, token, earlier, plan_job_id=job["job_id"])
            if seen["state"] == "applied":
                job.update(apply_job_id=earlier, apply_state="applied")
                line.update(state="already", apply_job_id=earlier)
                continue
            if seen["state"] != "failed":
                job.update(apply_job_id=earlier, apply_state="waiting")
                line.update(
                    state="waiting",
                    apply_job_id=earlier,
                    message=pipeline.waiting_message(server, earlier),
                )
                blocked += 1
                continue
        todo.append((job, line))
    if blocked:
        for _job, line in todo:
            line.update(
                state="not_applied",
                message="다른 작업이 적용할 수 없는 상태라 넣지 않았습니다 — 문서 올리기는 "
                "작업 전부가 깨끗할 때만 넣는다",
            )
        return finish(
            f"작업 {len(lines)}개 중 {blocked}개가 적용할 수 없는 상태라 아무것도 넣지 "
            "않았습니다 — 그 작업의 까닭(message)을 사람에게 말한다"
        )

    # 3. 차례로 넣는다 — 플랫폼이 하나를 거절하면(경합) 그 뒤는 넣지 않는다.
    for at, (job, line) in enumerate(todo):
        outcome = pipeline.apply_plan(server, token, job["job_id"])
        state = outcome["state"]
        result = outcome.get("result") or {}
        if state in ("applied", "already") and result.get("applied"):
            job.update(apply_job_id=outcome["apply_job_id"], apply_state="applied")
            line.update(state=state, apply_job_id=outcome["apply_job_id"])
            if state == "applied":
                line["counts"] = result.get("counts") or {}
            continue
        line["message"] = outcome.get("message") or "적용했는데 들어가지 않았습니다"
        if state == "waiting":
            job.update(apply_job_id=outcome["apply_job_id"], apply_state="waiting")
            line.update(state="waiting", apply_job_id=outcome["apply_job_id"])
        else:
            line["state"] = state if state in ("refused", "failed") else "failed"
            job["apply_state"] = "failed"
        for _later, rest in todo[at + 1 :]:
            rest.update(
                state="not_applied",
                message=f"앞 작업({job['job_id']})이 "
                + ("아직 대기 중이라" if state == "waiting" else "거절돼")
                + " 넣지 않았습니다",
            )
        went = [one["job_id"] for one in lines if one.get("state") in ("applied", "already")]
        head = f"작업 {len(went)}개는 적용했고({', '.join(went)}) " if went else ""
        later = len(todo) - at - 1
        if state == "waiting":
            return finish(
                f"{head}{job['job_id']} 의 적용이 아직 대기 중이라 그 뒤 {later}개는 넣지 "
                "않았습니다 — 끝난 뒤 document_apply 를 다시 부르면 이어서 넣는다"
            )
        return finish(
            f"{head}{job['job_id']} 가 거절돼 그 뒤 {later}개는 넣지 않았습니다 — "
            + ("적용한 작업의 문서는 판이 올랐다. " if went else "")
            + "거절된 작업의 까닭(message)을 고쳐 그 문서들만 다시 올린다"
        )
    return finish(f"작업 {len(lines)}개 — 넣을 것을 모두 적용했습니다")


def latest(folder: Path) -> Path:
    base = folder / PUSH_DIR
    found = sorted(base.glob(f"*/{RECORD}")) if base.is_dir() else []
    if not found:
        raise Stop("올린 기록이 없습니다 — 먼저 document_push 로 올리세요")
    return found[-1]


def expected(
    body: str, images: dict[str, str], attached: dict[str, str]
) -> tuple[str, list[str]]:
    """로컬 본문을 플랫폼이 쓰는 꼴로 — 그림 자리를 붙은 첨부(같은 sha256)의 `photo:<id>` 로.
    (본문, 아직 안 붙은 그림)."""
    missing: list[str] = []

    def swap(hit: re.Match[str]) -> str:
        target = hit.group("target")
        sha = images.get(target)
        if sha is None:
            return hit.group(0)
        found = attached.get(sha)
        if found is None:
            missing.append(target)
            return hit.group(0)
        return f"{hit.group('head')}photo:{found}{hit.group('query') or ''})"

    return IMAGE.sub(swap, body), missing


def check(folder: Path, record_path: Path, *, server: str, token: str) -> dict[str, Any]:
    """올린 것과 서버의 지금 본문이 같은가 — **본문을 받지 않는다**(판 · sha256 · 붙은
    그림)."""
    record_path = _placed(folder, record_path)
    record = pipeline._read_json(record_path, [])
    if not isinstance(record, dict):
        raise Stop(f"올린 기록을 읽을 수 없습니다: {record_path}")
    rows: list[dict[str, Any]] = []
    counts = {"same": 0, "different": 0, "not_written": 0, "local_changed": 0, "error": 0}
    for one in record.get("documents") or []:
        row: dict[str, Any] = {
            key: one.get(key) for key in ("type_slug", "object_id", "key", "path")
        }
        rows.append(row)
        try:
            # 기록의 칸을 주소에 그대로 잇지 않는다 — 꼴을 보고(uuid · slug · 키), 한 마디씩
            # 감싸고, 질의는 params 로. `..` 인 slug · 키는 감싸도 그대로라 다른 길이 됐다.
            object_id = pipeline.job_uuid(one.get("object_id"), "객체 id")
            slug = pipeline.require_slug(one.get("type_slug"), "기록의 타입")
            key = pipeline.require_key(one.get("key"), "기록의 문서 속성")
            meta = pipeline._get_json(
                server,
                token,
                pipeline.api_path("api", "objects", slug, object_id, "documents", key),
                params={"body": "false"},
                who="플랫폼",
            )
            attached_rows = pipeline._get_json(
                server,
                token,
                "/api/attachments",
                params={"owner_table": "objects", "owner_id": object_id, "owner_field": key},
                who="플랫폼",
            )
        except Stop as failure:
            row.update(state="error", message=str(failure))
            counts["error"] += 1
            continue
        row["revision"] = meta.get("revision")
        row["server_sha256"] = meta.get("sha256")
        path = _inside(folder, str(one.get("path") or ""))
        try:
            body = path.read_text(encoding="utf-8-sig") if path else None
        except OSError:
            body = None
        if body is None or digest(body) != one.get("local_sha256"):
            row["state"] = "local_changed"
            row["message"] = (
                "올린 뒤 로컬 파일이 바뀌었거나 없어졌습니다 — 올린 때의 것과 견주지 못합니다"
            )
            counts["local_changed"] += 1
            continue
        attached = {
            str(each.get("sha256")): str(each.get("id"))
            for each in (attached_rows if isinstance(attached_rows, list) else [])
        }
        wanted, missing = expected(body, dict(one.get("images") or {}), attached)
        row["expected_sha256"] = digest(wanted)
        if not meta.get("revision"):
            row["state"] = "not_written"
            row["message"] = "아직 안 썼습니다(판 0) — 적용 전이거나 거절됐습니다"
            counts["not_written"] += 1
        elif missing:
            row["state"] = "different"
            row["message"] = f"그림이 아직 안 붙었습니다: {', '.join(missing[:3])}"
            counts["different"] += 1
        elif digest(wanted) == meta.get("sha256"):
            row["state"] = "same"
            counts["same"] += 1
        else:
            row["state"] = "different"
            row["message"] = (
                "서버의 본문이 올린 것과 다릅니다 — 적용 전이거나 그 뒤에 누가 고쳤습니다"
            )
            counts["different"] += 1
    odd = [one for one in rows if one.get("state") != "same"]
    return {
        "record": record_path.relative_to(folder).as_posix(),
        "counts": counts,
        "all_same": counts["same"] == len(rows) and bool(rows),
        "rows": odd[:ROWS_SHOWN],
        "rows_omitted": max(0, len(odd) - ROWS_SHOWN),
    }
