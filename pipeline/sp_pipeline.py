#!/usr/bin/env python3
"""StandardPlatform 정제 파이프라인 — AI 결과물을 검토받고 넣는 길.

원천 데이터를 AI(Claude Code · Gemini CLI)가 곧바로 플랫폼에 넣으면
그룹마다 판단이 달라지고, 다시 넣을 때마다 같은 것이 둘씩 생기고,
무엇이 왜 들어갔는지 남지 않는다. 그래서 AI 는 **실행 폴더(run)** 에
결과물 묶음을 만들고, 사람은 이 도구로 검증 · 미리 보기 · 적재한다.

    python sp_pipeline.py init     runs/2026-09-13-parts
    python sp_pipeline.py validate runs/2026-09-13-parts
    python sp_pipeline.py preview  runs/2026-09-13-parts
    python sp_pipeline.py apply    runs/2026-09-13-parts

서버 주소와 토큰은 `SP_SERVER` · `SP_TOKEN` 환경 변수(또는 `--server`
· `--token`). 토큰은 플랫폼 「내 정보」 에서 발급한 개인 토큰이다.

표준 라이브러리만 쓴다 — 받는 사람 PC 에 무엇을 더 깔게 하지 않는다.
실행 폴더의 모양은 같은 폴더의 `AGENTS.md` 가 정본이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "sp-bundle/1"
MANIFEST = "bundle.json"
ONTOLOGY = "ontology.json"
OBJECTS_DIR = "objects"
RELATIONS_DIR = "relations"
UNRESOLVED = "unresolved.json"
PREVIEW = "preview.json"
APPLIED = "applied.json"

#: 감사 기록에 남는 통로 이름 — 사람이 화면에서 넣은 것과 가른다.
CLIENT = "sp-pipeline"
#: 플랫폼이 한 번에 받는 행 수(파일로 넣기와 같다).
MAX_ROWS = 5000
RELATION_FIELDS = ("src", "relation", "dst", "evidence_note", "properties")
"""관계 한 줄의 칸. `properties` 는 **그 관계 종류의 속성**이다(인과 관계의 근거 건수처럼) —
모양은 플랫폼이 관계 종류의 정의로 본다."""
#: 이보다 낮은 확신도의 행은 넣지 않는다 — 모델링 규약 5장. unresolved.json 으로 간다.
LOW_CONFIDENCE = 0.7
ONTOLOGY_KEYS = {"groups", "types", "relation_types"}

#: (method, url, headers, body) -> (status, json). 시험이 갈아 끼운다.
Sender = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


def _urllib_send(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as failure:
        raw = failure.read()
        try:
            return failure.code, json.loads(raw or b"null")
        except ValueError:
            text = raw.decode("utf-8", errors="replace")
            return failure.code, {"error": {"message": text}}


SEND: Sender = _urllib_send
#: 작업을 기다리는 사이의 잠. 시험이 no-op 으로 바꾼다.
WAIT: Callable[[float], None] = time.sleep
POLL_SECONDS = 1.5
#: 「대기」 가 이만큼 이어지면 멈추고 말한다 — **집을 워커가 없다는 뜻이다.** 말없이 계속
#: 기다리면 사람은 제 묶음이 잘못된 줄 알고 몇 번을 다시 만든다. 도는 중(running)이면 안 센다.
QUEUED_LIMIT_SECONDS = 120.0


# --------------------------------------------------------------------------
# 실행 폴더 읽기
# --------------------------------------------------------------------------


@dataclass
class Batch:
    type_slug: str
    file: str
    rows: list[Any]
    workspace_slug: str | None = None


@dataclass
class Run:
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    ontology: dict[str, Any] | None = None
    objects: list[Batch] = field(default_factory=list)
    relations: list[Batch] = field(default_factory=list)
    unresolved: list[Any] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """파일을 읽다가 난 문제 — 모양을 검사하기도 전에 막힌 것."""


def _read_json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as failure:
        problems.append(f"{path.name}: JSON 을 읽을 수 없습니다 ({failure})")
        return None


def _batches(run: Run, folder: str) -> list[Batch]:
    base = run.path / folder
    if not base.is_dir():
        return []
    found: dict[str, Batch] = {}
    for file in sorted(base.glob("*.json")):
        body = _read_json(file, run.problems)
        if body is None:
            continue
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            run.problems.append(f'{folder}/{file.name}: {{"rows": [...]}} 모양이어야 합니다')
            continue
        slug = str(body.get("type_slug") or file.stem)
        found[file.name] = Batch(
            type_slug=slug,
            file=f"{folder}/{file.name}",
            rows=body["rows"],
            workspace_slug=body.get("workspace_slug"),
        )
    # **적는 차례대로 넣는다.** 참조하는 타입은 참조되는 타입 뒤에 와야 한다.
    order = [str(one) for one in run.manifest.get(f"{folder}_order") or []]
    ranked = sorted(
        found.values(),
        key=lambda one: order.index(one.type_slug) if one.type_slug in order else len(order),
    )
    return ranked


def load(path: Path) -> Run:
    run = Run(path=path)
    manifest = _read_json(path / MANIFEST, run.problems)
    run.manifest = manifest if isinstance(manifest, dict) else {}
    if manifest is None and not (path / MANIFEST).exists():
        run.problems.append(f"{MANIFEST} 가 없습니다 — 먼저 init 하세요")
    ontology = _read_json(path / ONTOLOGY, run.problems)
    if isinstance(ontology, dict) and any(ontology.get(key) for key in ONTOLOGY_KEYS):
        run.ontology = ontology
    elif ontology is not None and not isinstance(ontology, dict):
        run.problems.append(f"{ONTOLOGY}: 객체({{...}})여야 합니다")
    run.objects = _batches(run, OBJECTS_DIR)
    run.relations = _batches(run, RELATIONS_DIR)
    unresolved = _read_json(path / UNRESOLVED, run.problems)
    run.unresolved = unresolved if isinstance(unresolved, list) else []
    return run


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """`_` 로 시작하는 칸(출처 · 확신도 · 메모)은 **플랫폼에 안 보낸다.**

    출처는 행 옆에 두어야 AI 가 빠뜨리지 않고, 사람이 행을 보며 확인한다. 그런데
    플랫폼은 모르는 열을 거절한다 — 그래서 보내기 전에 여기서 뗀다.
    """
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def payload(run: Run) -> dict[str, Any]:
    """플랫폼 `POST /api/bundles/import` 에 보내는 몸통(`apply` 제외).

    허브에서 받은 실행(`bundle.json` 의 `source`)이면 그 이름을 싣는다 — 플랫폼은 그 허브가
    관리하는 정의 · 객체만 이 길로 고치게 한다."""
    body = {
        "ontology": run.ontology,
        "objects": [
            {
                "type_slug": one.type_slug,
                "workspace_slug": one.workspace_slug,
                "rows": [_clean(row) for row in one.rows if isinstance(row, dict)],
            }
            for one in run.objects
        ],
        "relations": [
            {
                "type_slug": one.type_slug,
                "rows": [_clean(row) for row in one.rows if isinstance(row, dict)],
            }
            for one in run.relations
        ],
    }
    source = str(run.manifest.get("source") or "").strip()
    if source:
        body["source"] = source
    return body


def digest(body: dict[str, Any]) -> str:
    """보낼 몸통의 지문. **미리 본 것과 적용하는 것이 같은지** 이것으로 가른다."""
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# 검증 — 서버에 보내기 전에 잡을 수 있는 것
# --------------------------------------------------------------------------


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate(run: Run, *, allow_unresolved: bool = False) -> Report:
    report = Report(errors=list(run.problems))
    if run.manifest and run.manifest.get("format") != FORMAT:
        report.errors.append(
            f"{MANIFEST}: format 이 {FORMAT} 가 아닙니다 ({run.manifest.get('format')!r})"
        )

    if run.ontology is not None:
        unknown = sorted(set(run.ontology) - ONTOLOGY_KEYS)
        if unknown:
            report.errors.append(f"{ONTOLOGY}: 모르는 항목 {', '.join(unknown)}")
        for index, one in enumerate(run.ontology.get("types") or [], start=1):
            if not isinstance(one, dict) or not one.get("slug") or not one.get("label"):
                report.errors.append(f"{ONTOLOGY}: types[{index}] 에 slug · label 이 없습니다")

    if run.ontology is None and not run.objects and not run.relations:
        report.errors.append(
            "묶음이 비어 있습니다 — 정의 · 객체 · 관계 중 하나는 있어야 합니다"
        )

    received = bool(str(run.manifest.get("source") or "").strip())
    keys: dict[str, dict[str, str]] = {}
    for batch in run.objects:
        _check_rows(batch, report, max_rows=MAX_ROWS, received=received)
        if not batch.workspace_slug and not received:
            report.warnings.append(
                f"{batch.file}: workspace_slug 이 없어 **전역**으로 들어갑니다"
                " (시스템 관리자만)"
            )
        seen = keys.setdefault(batch.type_slug, {})
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict):
                continue
            if not row.get("label") and not row.get("key"):
                report.errors.append(f"{batch.file} {index}행: label 도 key 도 없습니다")
            key = str(row.get("key") or "").strip()
            if key:
                where = f"{batch.file} {index}행"
                if key in seen:
                    report.errors.append(
                        f"{where}: 식별자 {key!r} 가 겹칩니다 (먼저 {seen[key]})"
                    )
                else:
                    seen[key] = where

    for batch in run.relations:
        _check_rows(batch, report, max_rows=MAX_ROWS, received=received)
        no_evidence = 0
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict):
                continue
            missing = [name for name in ("src", "relation", "dst") if not row.get(name)]
            if missing:
                report.errors.append(
                    f"{batch.file} {index}행: {', '.join(missing)} 가 없습니다"
                )
            extra = sorted(
                key
                for key in row
                if not str(key).startswith("_") and key not in RELATION_FIELDS
            )
            if extra:
                report.errors.append(
                    f"{batch.file} {index}행: 관계 행에 없는 칸 {', '.join(extra)}"
                    f" (쓸 수 있는 것: {', '.join(RELATION_FIELDS)})"
                )
            if "properties" in row and not isinstance(row["properties"], dict):
                report.errors.append(
                    f"{batch.file} {index}행: properties 는 {{키: 값}} 이어야 합니다"
                )
            if not row.get("evidence_note"):
                no_evidence += 1
        if no_evidence and not received:
            report.warnings.append(
                f"{batch.file}: 근거(evidence_note)가 없는 관계 {no_evidence}줄"
                " — 왜 이었는지 남지 않습니다"
            )

    if run.unresolved:
        message = f"{UNRESOLVED}: 미해결 {len(run.unresolved)}건 — 사람이 판단한 뒤 비우세요"
        (report.warnings if allow_unresolved else report.errors).append(message)
    return report


def _rows(indexes: list[int], limit: int = 5) -> str:
    shown = ", ".join(str(one) for one in indexes[:limit])
    return shown + (f" 외 {len(indexes) - limit}" if len(indexes) > limit else "")


def _check_rows(
    batch: Batch, report: Report, *, max_rows: int, received: bool = False
) -> None:
    if len(batch.rows) > max_rows:
        report.errors.append(
            f"{batch.file}: {len(batch.rows)}행 — 한 파일에 {max_rows}행까지입니다. 나누세요"
        )
    not_dict = sum(1 for row in batch.rows if not isinstance(row, dict))
    if not_dict:
        report.errors.append(f"{batch.file}: 객체({{...}})가 아닌 행 {not_dict}개")
    rows = [row for row in batch.rows if isinstance(row, dict)]
    weak = [
        index
        for index, row in enumerate(rows, start=1)
        if isinstance(row.get("_confidence"), int | float)
        and row["_confidence"] < LOW_CONFIDENCE
    ]
    if weak:
        report.errors.append(
            f"{batch.file}: 확신도 {LOW_CONFIDENCE} 미만인 행 {len(weak)}개({_rows(weak)}행)"
            " — 넣지 말고 unresolved.json 으로 옮기세요"
        )
    if received:
        # 허브에서 받은 행은 원천이 허브다 — 출처 · 인용을 행마다 요구하지 않는다.
        return
    # 문서(쪽 · 슬라이드)에서 뽑은 행은 원문 인용이 있어야 검토하는 사람이 원문을 안 연다.
    unquoted = [
        index
        for index, row in enumerate(rows, start=1)
        if isinstance(row.get("_source"), dict)
        and ("page" in row["_source"] or "slide" in row["_source"])
        and not row["_source"].get("quote")
    ]
    if unquoted:
        report.warnings.append(
            f"{batch.file}: 문서에서 뽑았는데 원문 인용(_source.quote)이 없는 행"
            f" {len(unquoted)}개({_rows(unquoted)}행) — 검토할 때 원문을 열어야 합니다"
        )
    no_source = sum(
        1 for row in batch.rows if isinstance(row, dict) and not row.get("_source")
    )
    if no_source:
        report.warnings.append(
            f"{batch.file}: 출처(_source)가 없는 행 {no_source}개 — 어디서 왔는지 모릅니다"
        )


# --------------------------------------------------------------------------
# 플랫폼에 보내기
# --------------------------------------------------------------------------


class Stop(Exception):
    """사람에게 말하고 멈출 일."""


def _post_bundle(server: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    """묶음을 보내고 **작업이 끝날 때까지 기다린다.**

    플랫폼은 묶음을 요청 안에서 처리하지 않는다 — 1만 객체에 44초라 클라이언트가 먼저 끊었다.
    202 로 작업이 오고, 여기서 그 작업을 보다가 끝난 결과(옛 응답과 같은 모양)를 돌려준다.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # 감사 기록에 「이 도구가 넣었다」 가 남는다.
        "X-Client": CLIENT,
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{server.rstrip('/')}/api/bundles/import"
    status, answer = _send(server, "POST", url, headers, raw, who="플랫폼")
    if status != 202:
        _refused("플랫폼", status, answer)
    job = _wait_job(server, token, answer)
    result = job.get("result")
    if not isinstance(result, dict):
        raise Stop(f"플랫폼 응답을 읽을 수 없습니다: {job!r}")
    return result


def _send(
    server: str,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    who: str,
) -> tuple[int, Any]:
    try:
        return SEND(method, url, headers, body)
    except (urllib.error.URLError, OSError) as failure:
        # **닿지 않은 것과 거절당한 것을 가른다.** 트레이스백을 보여 주면 사람은 무엇을
        # 고칠지(주소 · 서버가 떠 있나 · 사내망) 모른다.
        reason = getattr(failure, "reason", failure)
        raise Stop(
            f"{who}에 닿지 않습니다: {server} ({reason}) — 주소와 서버가 떠 있는지 확인하세요"
        ) from failure


def _refused(who: str, status: int, answer: Any) -> None:
    error = answer.get("error", {}) if isinstance(answer, dict) else {}
    raise Stop(
        f"{who}이 거절했습니다 ({status}): [{error.get('code', '?')}] "
        f"{error.get('message', answer)}"
    )


def _wait_job(server: str, token: str, job: Any, *, who: str = "플랫폼") -> dict[str, Any]:
    """작업이 끝날 때까지 본다. 실패 · 취소면 그 이유로 멈춘다 — 「끝났다」 로 얼버무리지
    않는다."""
    if not isinstance(job, dict) or not job.get("id"):
        raise Stop(f"{who}이 작업을 돌려주지 않았습니다: {job!r}")
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = f"{server.rstrip('/')}/api/jobs/{job['id']}"
    shown = ""
    queued_for = 0.0
    while True:
        status, current = _send(server, "GET", url, headers, None, who=who)
        if status != 200:
            _refused(who, status, current)
        if not isinstance(current, dict):
            raise Stop(f"{who} 응답을 읽을 수 없습니다: {current!r}")
        state = str(current.get("status"))
        progress = current.get("progress") or {}
        line = (
            f"{progress.get('stage', '')} {progress.get('done', 0)}/{progress.get('total', 0)}"
        )
        if line != shown and state == "running":
            print(f"  … {line}", file=sys.stderr)
            shown = line
        if state == "done":
            return current
        if state in ("failed", "cancelled"):
            raise Stop(
                f"{who}의 작업이 {'실패했습니다' if state == 'failed' else '취소됐습니다'}: "
                f"{current.get('error') or ''}"
            )
        queued_for = queued_for + POLL_SECONDS if state == "queued" else 0.0
        if queued_for >= QUEUED_LIMIT_SECONDS:
            raise Stop(
                f"{who}의 작업(<{job['id']}>)을 {int(queued_for)}초 동안 아무도 집어 가지 "
                "않았습니다 — 작업 워커가 꺼져 있는 것 같습니다. 운영자에게 "
                "'systemctl status <slug>-worker' 를 확인해 달라고 하세요. "
                "묶음은 서버에 남아 있으니 워커가 살아나면 그대로 이어집니다."
            )
        WAIT(POLL_SECONDS)


def _get_json(server: str, token: str, path: str) -> Any:
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    status, answer = _send(
        server, "GET", f"{server.rstrip('/')}{path}", headers, None, who="허브"
    )
    if status != 200:
        _refused("허브", status, answer)
    return answer


def _export_bundle(hub: str, hub_token: str, group: str) -> Any:
    """허브의 내보내기도 작업이다 — 넣고, 기다리고, 결과 파일을 받는다."""
    headers = {
        "Authorization": f"Bearer {hub_token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    raw = json.dumps({"group": group}, ensure_ascii=False).encode("utf-8")
    status, answer = _send(
        hub, "POST", f"{hub.rstrip('/')}/api/bundles/export", headers, raw, who="허브"
    )
    if status != 202:
        _refused("허브", status, answer)
    job = _wait_job(hub, hub_token, answer, who="허브")
    return _get_json(hub, hub_token, f"/api/jobs/{job['id']}/download")


def resolve_names(
    server: str, token: str, type_slug: str, names: list[str]
) -> dict[str, dict[str, Any]] | None:
    """이름 여럿을 **플랫폼의 판정으로** 푼다 — `POST /objects/{타입}/resolve-many`.

    도구가 스스로 맞추지 않는 이유: 식별자 → 별칭 → 이름 순서와 겹침 규칙은 플랫폼의 것이고,
    두 벌로 두면 「도구는 된다는데 넣으면 안 되는」 상태가 생긴다.
    """
    out: dict[str, dict[str, Any]] = {}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    unique = list(dict.fromkeys(one for one in names if one))
    url = f"{server.rstrip('/')}/api/objects/{urllib.parse.quote(type_slug)}/resolve-many"
    for at in range(0, len(unique), 500):
        raw = json.dumps({"names": unique[at : at + 500]}, ensure_ascii=False).encode("utf-8")
        status, body = _send(server, "POST", url, headers, raw, who="플랫폼")
        if status == 404:
            # **그 타입이 플랫폼에 아직 없다.** 같은 묶음의 정의가 만들 것일 수 있으므로
            # 여기서 멈추지 않는다 — 부르는 쪽이 판단한다.
            return None
        if status != 200:
            _refused("플랫폼", status, body)
        for one in (body or {}).get("items") or []:
            out[str(one.get("name"))] = one
    return out


def fetch_keys(server: str, token: str, type_slug: str) -> set[str]:
    """플랫폼에서 그 타입의 식별자를 전부 받는다(쪽마다) — 원천의 코드가 코어에 붙나를
    볼 때."""
    keys: set[str] = set()
    offset = 0
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    while True:
        url = (
            f"{server.rstrip('/')}/api/objects/{urllib.parse.quote(type_slug)}"
            f"?limit=500&offset={offset}"
        )
        try:
            status, body = SEND("GET", url, headers, None)
        except (urllib.error.URLError, OSError) as failure:
            reason = getattr(failure, "reason", failure)
            raise Stop(
                f"플랫폼에 닿지 않습니다: {server} ({reason}) — "
                "주소(SP_SERVER)와 서버가 떠 있는지 확인하세요"
            ) from failure
        if status != 200 or not isinstance(body, dict):
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise Stop(
                f"코어 식별자를 받지 못했습니다({type_slug}, {status}): "
                f"{error.get('message', body)}"
            )
        items = body.get("items") or []
        keys.update(str(one["key"]) for one in items if one.get("key"))
        offset += len(items)
        if not items or offset >= int(body.get("total") or 0):
            return keys


def read_keys_file(path: Path) -> set[str]:
    """식별자 파일 — 한 줄에 하나. 허브에 닿지 않는 PC 에서 코어 식별자를 옮겨 올 때."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as failure:
        raise Stop(f"식별자 파일을 읽을 수 없습니다: {path} ({failure})") from failure
    return {line.strip() for line in lines if line.strip()}


def summarize(result: dict[str, Any], *, limit: int = 20) -> str:
    """사람이 읽는 요약 — 오류는 **행 번호와 이유**까지."""
    lines: list[str] = []
    state = (
        "적용함" if result.get("applied") else ("괜찮음" if result.get("ok") else "오류 있음")
    )
    lines.append(f"결과: {state}")
    ontology = result.get("ontology")
    if ontology:
        changed = [c for c in ontology.get("changes", []) if c.get("action") != "unchanged"]
        lines.append(f"정의: 바뀌는 것 {len(changed)}건")
        for change in changed[:limit]:
            lines.append(f"  {change.get('action')} {change.get('kind')} {change.get('slug')}")
        for warning in ontology.get("warnings", []):
            lines.append(f"  경고: {warning}")
        for error in ontology.get("errors", []):
            lines.append(f"  오류: {error}")
    for name, label in (("objects", "객체"), ("relations", "관계")):
        for batch in result.get(name, []):
            plan = batch.get("plan") or {}
            counts = " · ".join(
                f"{k} {v}" for k, v in sorted((plan.get("counts") or {}).items())
            )
            lines.append(f"{label} {batch.get('type_slug')}: {counts or '-'}")
            if batch.get("error"):
                lines.append(f"  오류: {batch['error']}")
            for error in plan.get("errors", []):
                lines.append(f"  오류: {error}")
            bad = [row for row in plan.get("rows", []) if row.get("action") == "error"]
            for row in bad[:limit]:
                lines.append(
                    f"  {row.get('row')}행 {row.get('label') or ''}: {row.get('message')}"
                )
            if len(bad) > limit:
                lines.append(f"  … 외 {len(bad) - limit}행")
    graves = result.get("tombstones") or {}
    if graves.get("rows"):
        counts = " · ".join(
            f"{k} {v}" for k, v in sorted((graves.get("counts") or {}).items())
        )
        lines.append(f"사라진 것: {counts or '-'}")
        for row in graves["rows"][:limit]:
            lines.append(
                f"  {row.get('action')} {row.get('label') or ''}: {row.get('message') or ''}"
            )
        if len(graves["rows"]) > limit:
            lines.append(f"  … 외 {len(graves['rows']) - limit}줄")
    for error in result.get("errors", []):
        lines.append(f"오류: {error}")
    return "\n".join(lines)


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(path: Path, body: Any) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 명령
# --------------------------------------------------------------------------


def cmd_init(path: Path, *, title: str = "") -> str:
    if (path / MANIFEST).exists():
        raise Stop(f"이미 실행 폴더입니다: {path} — 새 실행은 새 폴더로")
    (path / OBJECTS_DIR).mkdir(parents=True, exist_ok=True)
    (path / RELATIONS_DIR).mkdir(parents=True, exist_ok=True)
    _write(
        path / MANIFEST,
        {
            "format": FORMAT,
            "title": title,
            "created_at": _stamp(),
            "sources": [],
            "objects_order": [],
            "relations_order": [],
            "notes": "",
        },
    )
    _write(path / ONTOLOGY, {"groups": [], "types": [], "relation_types": []})
    _write(path / UNRESOLVED, [])
    return f"실행 폴더를 만들었습니다: {path}"


def cmd_pull(path: Path, *, hub: str, hub_token: str, group: str, source: str = "hub") -> str:
    """허브가 내보낸 묶음(사이드바 묶음 하나)을 **새 실행 폴더로** 받는다.

    받은 것도 여느 실행과 같다 — `validate` → `preview`(받는 플랫폼) → 사람이 확인 → `apply`.
    `bundle.json` 의 `source` 가 실려 가므로, 받는 플랫폼은 그 타입들을 허브 관리로 둔다.
    """
    if not group.strip():
        raise Stop("받을 사이드바 묶음(--group)이 필요합니다 — PLM 기준정보면 plm")
    body = _export_bundle(hub, hub_token, group.strip())
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise Stop(f"허브의 응답이 묶음이 아닙니다: {str(body)[:200]}")
    cmd_init(path, title=f"허브에서 받기 — {group}")
    manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    manifest["source"] = source
    manifest["sources"] = [
        {
            "name": hub,
            "group": group,
            "exported_at": body.get("exported_at"),
            "counts": body.get("counts") or {},
        }
    ]
    batches = body.get("objects") or []
    manifest["objects_order"] = list(dict.fromkeys(str(one["type_slug"]) for one in batches))
    manifest["notes"] = "\n".join(str(one) for one in body.get("warnings") or [])
    _write(path / MANIFEST, manifest)
    _write(path / ONTOLOGY, body.get("ontology") or {})
    for index, batch in enumerate(batches, start=1):
        _write(path / OBJECTS_DIR / f"{index:03d}-{batch['type_slug']}.json", batch)
    for index, batch in enumerate(body.get("relations") or [], start=1):
        _write(path / RELATIONS_DIR / f"{index:03d}-{batch['type_slug']}.json", batch)
    counts = body.get("counts") or {}
    lines = [
        f"받았습니다: {path}",
        f"허브 {hub} · 묶음 {group} · 내보낸 때 {body.get('exported_at')}",
        "타입 {types} · 관계 종류 {relation_types} · 객체 {objects} · 관계 {relations}".format(
            **{
                key: counts.get(key, 0)
                for key in ("types", "relation_types", "objects", "relations")
            }
        ),
        *[f"경고: {one}" for one in body.get("warnings") or []],
        f"다음: python sp_pipeline.py validate {path}"
        " → preview → 확인 → apply (받는 플랫폼에)",
    ]
    return "\n".join(lines)


def check_endpoints(run: Run, server: str, token: str) -> Report:
    """**끝점이 풀리나** — 플랫폼에 물어 미리 본다(검증의 선택 단계).

    예전에는 이것을 `preview` 에서야 알았다. 미리 보기는 계획을 세우느라 몇 분이 걸리고,
    그 몇 분 뒤에 「이름이 여럿과 맞는다」 를 듣는다. 물어서 아는 것은 먼저 묻는다.

    보는 것 셋:
    - 관계의 출발점(`src`)이 그 타입에서 **하나로 정해지나**
    - 객체 행이 가리키는 참조 칸(실행 폴더의 `ontology.json` 이 말하는 `object_ref`)이 풀리나
    - **별칭으로 풀린 것**은 경고로 — 다른 객체의 이름과 같은 별칭이 있으면 조용히 그쪽에
      붙는다(그 사실은 아무 데도 안 적힌다)
    """
    report = Report()
    defined: set[str] = {
        str(one.get("slug"))
        for one in (((run.ontology or {}).get("types") or []) if run.ontology else [])
        if isinstance(one, dict)
    }
    ref_of: dict[str, dict[str, str]] = {}
    for one in ((run.ontology or {}).get("types") or []) if run.ontology else []:
        if not isinstance(one, dict):
            continue
        fields = {
            str(prop.get("key")): str(prop.get("ref_type_slug") or "")
            for prop in (one.get("properties") or [])
            if isinstance(prop, dict) and prop.get("data_type") == "object_ref"
        }
        if fields:
            ref_of[str(one.get("slug"))] = fields

    asked: dict[str, set[str]] = {}
    for batch in run.relations:
        for row in batch.rows:
            if isinstance(row, dict) and row.get("src"):
                asked.setdefault(batch.type_slug, set()).add(str(row["src"]))
    for batch in run.objects:
        for key, target in (ref_of.get(batch.type_slug) or {}).items():
            if not target:
                continue
            for row in batch.rows:
                if not isinstance(row, dict):
                    continue
                value = row.get(key)
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item.strip():
                        asked.setdefault(target, set()).add(item.strip())
    if not asked:
        return report

    # **이 실행이 만드는 것은 아직 없어도 된다** — 같은 묶음의 객체 단계가 넣는다.
    making: dict[str, set[str]] = {}
    for batch in run.objects:
        for row in batch.rows:
            if not isinstance(row, dict):
                continue
            for name in ("key", "label"):
                if row.get(name):
                    making.setdefault(batch.type_slug, set()).add(str(row[name]))

    for type_slug, names in sorted(asked.items()):
        left = sorted(names - (making.get(type_slug) or set()))
        if not left:
            continue
        found = resolve_names(server, token, type_slug, left)
        if found is None:
            where = report.warnings if type_slug in defined else report.errors
            where.append(
                f"{type_slug}: 이 타입이 플랫폼에 아직 없어 끝점을 못 봤습니다 "
                f"({len(left)}건) — 이 묶음의 정의가 만드는 타입이면 정상입니다"
            )
            continue
        missing = [one for one in left if (found.get(one) or {}).get("match") == "none"]
        several = [one for one in left if (found.get(one) or {}).get("match") == "candidates"]
        by_alias = [
            one
            for one in left
            if ((found.get(one) or {}).get("object") or {}).get("matched_by") == "alias"
        ]
        if missing:
            report.errors.append(
                f"{type_slug}: 가리키는 것이 플랫폼에 없습니다 {len(missing)}건 — "
                + ", ".join(missing[:10])
                + (" …" if len(missing) > 10 else "")
            )
        if several:
            report.errors.append(
                f"{type_slug}: 이름이 여럿과 맞습니다 {len(several)}건(그때 플랫폼은 "
                "고르지 않는다) — " + ", ".join(several[:10])
            )
        if by_alias:
            report.warnings.append(
                f"{type_slug}: 별칭으로 풀린 것 {len(by_alias)}건 — 다른 객체의 이름과 같은 "
                "별칭이면 그쪽에 붙습니다: " + ", ".join(by_alias[:10])
            )
    return report


def cmd_validate(
    path: Path,
    *,
    allow_unresolved: bool = False,
    server: str = "",
    token: str = "",
) -> tuple[bool, str]:
    run = load(path)
    report = validate(run, allow_unresolved=allow_unresolved)
    asked = ""
    if server and token:
        remote = check_endpoints(run, server, token)
        report.errors.extend(remote.errors)
        report.warnings.extend(remote.warnings)
        asked = " · 끝점 확인함"
    lines = [f"오류: {one}" for one in report.errors] + [
        f"경고: {one}" for one in report.warnings
    ]
    total = sum(len(one.rows) for one in run.objects)
    links = sum(len(one.rows) for one in run.relations)
    head = (
        f"정의 {'있음' if run.ontology else '없음'} · 객체 {total}행({len(run.objects)}파일)"
        f" · 관계 {links}줄{asked} · 오류 {len(report.errors)} · 경고 {len(report.warnings)}"
    )
    return not report.errors, "\n".join([head, *lines])


def cmd_preview(path: Path, *, server: str, token: str) -> tuple[bool, str]:
    run = load(path)
    report = validate(run)
    if report.errors:
        raise Stop(
            "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {e}" for e in report.errors)
        )
    body = payload(run)
    result = _post_bundle(server, token, {**body, "apply": False})
    _write(
        path / PREVIEW,
        {"at": _stamp(), "server": server, "digest": digest(body), "result": result},
    )
    return bool(result.get("ok")), summarize(result)


def cmd_apply(path: Path, *, server: str, token: str) -> tuple[bool, str]:
    run = load(path)
    report = validate(run)
    if report.errors:
        raise Stop(
            "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {e}" for e in report.errors)
        )
    body = payload(run)
    seen = _read_json(path / PREVIEW, [])
    # **미리 본 것만 넣는다.** 미리 본 뒤 파일을 고쳤으면 사람이 본 적 없는 것이 들어간다.
    if not isinstance(seen, dict):
        raise Stop("먼저 preview 로 미리 보고 확인하세요")
    if seen.get("digest") != digest(body):
        raise Stop("미리 본 뒤에 묶음이 바뀌었습니다 — preview 를 다시 하고 확인하세요")
    if seen.get("server") != server:
        raise Stop(
            f"미리 본 서버({seen.get('server')})와 다른 서버입니다 — preview 를 다시 하세요"
        )
    if not (seen.get("result") or {}).get("ok"):
        raise Stop("미리 보기에서 오류가 있었습니다 — 고치고 preview 를 다시 하세요")
    result = _post_bundle(server, token, {**body, "apply": True})
    _write(
        path / APPLIED,
        {"at": _stamp(), "server": server, "digest": digest(body), "result": result},
    )
    return bool(result.get("applied")), summarize(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_pipeline", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="빈 실행 폴더를 만든다")
    init.add_argument("run", type=Path)
    init.add_argument("--title", default="")

    check = sub.add_parser("validate", help="서버에 보내기 전에 모양을 본다")
    check.add_argument("run", type=Path)
    check.add_argument("--allow-unresolved", action="store_true")
    # **주소와 토큰이 있으면 끝점을 미리 묻는다.** 없으면 모양만 본다(예전과 같다).
    check.add_argument("--server", default=os.environ.get("SP_SERVER", ""))
    check.add_argument("--token", default=os.environ.get("SP_TOKEN", ""))

    pull = sub.add_parser("pull", help="허브가 내보낸 묶음을 새 실행 폴더로 받는다")
    pull.add_argument("run", type=Path)
    pull.add_argument("--group", required=True, help="사이드바 묶음 slug — PLM 기준정보면 plm")
    pull.add_argument("--hub", default=os.environ.get("SP_HUB_SERVER", ""))
    pull.add_argument("--hub-token", default=os.environ.get("SP_HUB_TOKEN", ""))
    pull.add_argument("--source", default="hub")

    for name, what in (
        ("preview", "아무것도 저장하지 않고 미리 본다"),
        ("apply", "미리 본 것을 넣는다"),
    ):
        one = sub.add_parser(name, help=what)
        one.add_argument("run", type=Path)
        one.add_argument("--server", default=os.environ.get("SP_SERVER", ""))
        one.add_argument("--token", default=os.environ.get("SP_TOKEN", ""))

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(cmd_init(args.run, title=args.title))
            return 0
        if args.command == "validate":
            ok, text = cmd_validate(
                args.run,
                allow_unresolved=args.allow_unresolved,
                server=args.server,
                token=args.token,
            )
            print(text)
            return 0 if ok else 1
        if args.command == "pull":
            if not args.hub or not args.hub_token:
                raise Stop(
                    "허브 주소와 토큰이 필요합니다 — SP_HUB_SERVER · SP_HUB_TOKEN 또는 "
                    "--hub · --hub-token"
                )
            print(
                cmd_pull(
                    args.run,
                    hub=args.hub,
                    hub_token=args.hub_token,
                    group=args.group,
                    source=args.source,
                )
            )
            return 0
        if not args.server or not args.token:
            raise Stop(
                "서버와 토큰이 필요합니다 — SP_SERVER · SP_TOKEN 또는 --server · --token"
            )
        command = cmd_preview if args.command == "preview" else cmd_apply
        ok, text = command(args.run, server=args.server, token=args.token)
        print(text)
        return 0 if ok else 1
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
