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
#: 한 편의 상한 — 플랫폼과 같다.
MAX_CHARS = 1_000_000
#: 본문 안의 그림 — 플랫폼(`objects/docpush.IMAGE`)과 같은 꼴.
IMAGE = re.compile(r"(?P<head>!\[[^\]\n]*\]\()(?P<target>[^)\s?]+)(?P<query>\?w=\d{1,3})?\)")
REMOTE = ("photo:", "http://", "https://", "data:", "/", "#", "mailto:")
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
        out.append(
            Item(
                str(one["type_slug"]),
                str(one["object_id"]),
                str(one["key"]),
                str(one["path"]),
                base,
                str(one.get("note") or "")[:200],
            )
        )
    return out


def _inside(folder: Path, relative: str) -> Path | None:
    path = (folder / relative).resolve()
    return path if folder.resolve() in path.parents else None


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
        one.body = body
        one.size = len(body.encode("utf-8"))
        found, maybe = pipeline.personal_info(body)
        if found:
            shown = " · ".join(found[:5]) + (f" 외 {len(found) - 5}" if len(found) > 5 else "")
            one.problems.append(f"개인 정보 꼴 {len(found)}곳 — {shown}. 가리거나 빼고 올린다")
        if maybe:
            one.warnings.append(f"확인할 꼴 {len(maybe)}곳 — " + " · ".join(maybe[:3]))
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
            if not local.is_file():
                one.problems.append(f"그림 파일이 없습니다: {target}")
                continue
            data = local.read_bytes()
            one.images[target] = local
            one.image_sha[target] = hashlib.sha256(data).hexdigest()
            one.size += len(data)
    return out


def _zip(chunk: list[Prepared]) -> bytes:
    """목록 · 마크다운 · 그림 — 그림은 내용 주소(`img/<sha256>.<확장자>`)로 한 번씩 싣고 본문의
    자리를 그쪽으로 바꾼다(문서마다 폴더가 달라도 겹치지 않는다). 플랫폼은 그 자리를 다시
    `photo:<첨부 id>` 로 바꾸므로 본문의 결과는 같다."""
    buffer = io.BytesIO()
    documents: list[dict[str, Any]] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        stored: set[str] = set()
        for index, one in enumerate(chunk, start=1):
            names: dict[str, str] = {}
            for target, local in one.images.items():
                name = f"img/{one.image_sha[target]}{local.suffix.lower()}"
                names[target] = f"../{name}"
                if name not in stored:
                    # 그림은 이미 눌러 담긴 꼴(PNG · JPEG)이라 다시 누르지 않는다.
                    archive.write(local, name, compress_type=zipfile.ZIP_STORED)
                    stored.add(name)

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


def _chunks(ready: list[Prepared]) -> list[list[Prepared]]:
    out: list[list[Prepared]] = [[]]
    size = 0
    for one in ready:
        if out[-1] and (size + one.size > ZIP_MAX_BYTES or len(out[-1]) >= DOCUMENTS_PER_JOB):
            out.append([])
            size = 0
        out[-1].append(one)
        size += one.size
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
        server, "POST", f"{server.rstrip('/')}/api/jobs", headers, body, who="플랫폼"
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
            for one in shown
        ],
        "rows_omitted": max(0, (len(problems) or len(rows)) - len(shown)),
        "warnings": [
            f"{one.item.path}: {warning}" for one in ready for warning in one.warnings
        ][:ROWS_SHOWN],
    }


def apply(folder: Path, record_path: Path, *, server: str, token: str) -> dict[str, Any]:
    """**사람이 계획을 보고 「좋다」 한 뒤에만** — 올린 기록의 작업마다 적용하고, 끝나면 서버
    본문과 견준다(`check`). 화면에서 이미 적용했으면 그 작업은 건너뛴다(플랫폼이 같은 계획을 두
    번 넣지 않는다 — JOBS-22)."""
    record = pipeline._read_json(record_path, [])
    if not isinstance(record, dict) or not record.get("jobs"):
        raise Stop(f"올린 기록을 읽을 수 없습니다: {record_path}")
    if record.get("server") and record["server"] != server:
        raise Stop(
            f"올린 플랫폼({record['server']})과 다른 플랫폼입니다 — 그 플랫폼으로 "
            "다시 올리세요"
        )
    done: list[dict[str, Any]] = []
    for job in record["jobs"]:
        line: dict[str, Any] = {"job_id": job.get("job_id")}
        done.append(line)
        if job.get("apply_job_id"):
            line.update(state="already", apply_job_id=job["apply_job_id"])
            continue
        if not job.get("ok"):
            line.update(
                state="refused",
                message="계획에 오류가 있거나 쓸 것이 없어 적용하지 않습니다 — 오류 줄을 빼고 "
                "다시 올리세요",
            )
            continue
        # 화면에서 먼저 적용했으면 `already` — 그것으로 친다.
        outcome = pipeline.apply_plan(server, token, str(job["job_id"]))
        line["state"] = outcome["state"]
        if outcome.get("message"):
            line["message"] = outcome["message"]
        if outcome["state"] in ("applied", "already"):
            job["apply_job_id"] = outcome["apply_job_id"]
            line["apply_job_id"] = outcome["apply_job_id"]
        if outcome["state"] == "applied":
            line["counts"] = outcome["result"].get("counts") or {}
    pipeline._write(record_path, record)
    return {
        "applied": done,
        "all_applied": all(one.get("state") in ("applied", "already") for one in done),
        "check": check(folder, record_path, server=server, token=token),
    }


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
        base = f"/api/objects/{one['type_slug']}/{one['object_id']}/documents/{one['key']}"
        try:
            meta = pipeline._get_json(server, token, f"{base}?body=false", who="플랫폼")
            attached_rows = pipeline._get_json(
                server,
                token,
                f"/api/attachments?owner_table=objects&owner_id={one['object_id']}"
                f"&owner_field={quote(str(one['key']))}",
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
