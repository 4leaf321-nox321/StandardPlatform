"""SPARQL 사본(ADR 0070) — 디스크에 세워 둔 Oxigraph 저장소에 묻는다.

    <RDF_STORE_DIR>/active → slot-a | slot-b   (지금 판 — 그 안의 manifest.json: 언제 · 몇)

밤마다 쉬는 쪽에 통째로 싣고(`copycli`) `active` 를 바꿔 끼운다. 웹은 질의마다 `active` 를 다시
읽는다 — 다시 띄울 것이 없다(Neo4j 사본과 다르다).

**IRI 의 바탕** — 지금 메모리 그래프는 요청이 들어온 주소로 IRI 를
짓는다(`Names(request.base_url)`). 밤의 사본은 요청이 없으니 고정된 바탕(`COPY_BASE`)으로 짓고,
묻고 답할 때 **요청의 바탕과 서로 바꾼다** — 쓰는 쪽에서는 메모리 그래프와 같은 IRI
다(`rdf_schema` · `/rdf/data` 와도 같다).

질의는 따로 도는 프로세스(`storeq.py`)에서 — 시간(`RDF_QUERY_SECONDS`) ·
메모리(`RDF_QUERY_MEMORY_MB`) 상한을 넘으면 끊는다. 웹 프로세스마다 동시에 `RDF_CONCURRENCY`
개.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.shared.errors import AppError, code

#: 사본을 지을 때의 바탕 IRI — 쓰는 쪽에는 안 보인다(요청의 바탕으로 바꿔 준다). 예약된 이름
#: (`.invalid`)이라 실제 주소와 겹치지 않는다.
COPY_BASE = "https://sp-copy.invalid/"
ACTIVE = "active"
MANIFEST = "manifest.json"
_CHILD = Path(__file__).with_name("storeq.py")

_lock = threading.Lock()
_gate: threading.BoundedSemaphore | None = None
_gate_size = 0


@dataclass
class Copy:
    path: Path
    copied_at: str | None
    triples: int | None
    objects: int | None
    sp_version: str | None


def available() -> Copy | None:
    """지금 판 — 사본이 없거나(설정 없음 · 아직 안 실음) 읽을 수 없으면 None."""
    root = get_settings().rdf_store_dir.strip()
    if not root:
        return None
    active = Path(root) / ACTIVE
    try:
        target = active.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        return None
    manifest = target / MANIFEST
    try:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    return Copy(
        path=target,
        copied_at=meta.get("copied_at"),
        triples=meta.get("triples"),
        objects=meta.get("objects"),
        sp_version=meta.get("sp_version"),
    )


def to_copy(text: str, base: str) -> str:
    """요청의 바탕 → 사본의 바탕(질의 글)."""
    return text.replace(base, COPY_BASE) if base and base != COPY_BASE else text


def from_copy(text: str, base: str) -> str:
    """사본의 바탕 → 요청의 바탕(답의 IRI)."""
    return text.replace(COPY_BASE, base) if base and base != COPY_BASE else text


def _gate_for() -> threading.BoundedSemaphore:
    global _gate, _gate_size
    size = max(1, get_settings().rdf_concurrency)
    with _lock:
        if _gate is None or _gate_size != size:
            _gate, _gate_size = threading.BoundedSemaphore(size), size
        return _gate


def _limit_memory(megabytes: int) -> Any:
    def apply() -> None:  # pragma: no cover - 자식 프로세스에서 돈다
        size = megabytes * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_DATA, (size, size))

    return apply


def run(
    copy: Copy, query: str, *, base: str, prefixes: dict[str, str], limit: int
) -> dict[str, Any]:
    """사본에 묻는다 — 따로 도는 프로세스에서, 시간 · 메모리 상한을 걸고. IRI 는 요청의
    바탕으로 돌려 놓는다. 문법 · 평가 오류는 409, 시간 초과는 504."""
    settings = get_settings()
    asked = {
        "store": str(copy.path),
        "query": to_copy(query, base),
        "prefixes": {name: to_copy(iri, base) for name, iri in prefixes.items()},
        "limit": limit,
    }
    gate = _gate_for()
    if not gate.acquire(timeout=settings.rdf_query_seconds):
        raise AppError(
            code("RDF", 12),
            "SPARQL 사본에 묻는 질의가 밀려 있습니다 — 잠시 뒤 다시 묻습니다.",
            status=503,
        )
    try:
        done = subprocess.run(
            [sys.executable, "-I", str(_CHILD)],
            input=json.dumps(asked, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=settings.rdf_query_seconds,
            preexec_fn=_limit_memory(settings.rdf_query_memory_mb),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
    except subprocess.TimeoutExpired as caught:
        raise AppError(
            code("RDF", 10),
            f"SPARQL 이 {settings.rdf_query_seconds}초 안에 끝나지 않았습니다 — 출발점을 먼저 "
            "좁히는 꼴(부분 질의를 먼저 · `VALUES`)로 다시 씁니다.",
            status=504,
        ) from caught
    finally:
        gate.release()
    if done.returncode != 0 or not done.stdout.strip():
        why = (done.stderr or "").strip().splitlines()[-1:] or ["알 수 없음"]
        if "MemoryError" in done.stderr or done.returncode in (-9, 137):
            raise AppError(
                code("RDF", 11),
                f"SPARQL 이 메모리 상한({settings.rdf_query_memory_mb}MB)을 넘었습니다 — 중간 "
                "결과를 줄입니다(먼저 좁히고 · 집계로).",
                status=422,
            )
        raise AppError(
            code("RDF", 11), f"SPARQL 사본이 답하지 못했습니다: {why[0]}", status=500
        )
    out: dict[str, Any] = json.loads(done.stdout)
    if "error" in out:
        raise AppError(
            code("RDF", 4), f"질의를 이해하지 못했습니다: {out['error'][:500]}", status=409
        )
    if out.get("kind") == "select":
        for row in out["rows"]:
            for term in row:
                if term is not None and term["t"] == "uri":
                    term["v"] = from_copy(term["v"], base)
    return out


XSD = "http://www.w3.org/2001/XMLSchema#"


def plain(term: dict[str, Any] | None, prefixes: dict[str, str]) -> Any:
    """사본의 값 → 메모리 그래프의 답과 같은 모양 — IRI 는 접두어로 줄이고, 리터럴은 값으로."""
    if term is None:
        return None
    value = term["v"]
    if term["t"] == "uri":
        for name, iri in sorted(prefixes.items(), key=lambda one: -len(one[1])):
            if value.startswith(iri) and len(value) > len(iri):
                return f"{name}:{value[len(iri) :]}"
        return value
    if term["t"] == "bnode":
        return f"_:{value}"
    datatype = term.get("dt", "")
    try:
        if datatype in (XSD + "integer", XSD + "long", XSD + "int"):
            return int(value)
        if datatype in (XSD + "decimal", XSD + "double", XSD + "float"):
            number = float(value)
            return int(number) if number.is_integer() and "." not in value else number
        if datatype == XSD + "boolean":
            return value == "true"
    except ValueError:
        return value
    return value
