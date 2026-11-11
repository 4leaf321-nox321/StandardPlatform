"""RDF/OWL 내보내기 — 읽기만. 어느 사용자든 자기가 볼 수 있는 것을 받아 간다."""

from __future__ import annotations

import json
import re
import time
from itertools import islice
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from rdflib import Graph, URIRef
from rdflib.paths import Path
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import expandUnicodeEscapes, parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.rdf import export, store
from app.shared import cut_questions
from app.shared.auth import current_user
from app.shared.errors import AppError, Conflict, Forbidden, code
from app.shared.permissions import sees_every_department

router = APIRouter(prefix="/rdf", tags=["rdf"])

FORMATS = {
    "ttl": "ttl",
    "turtle": "ttl",
    "jsonld": "jsonld",
    "json-ld": "jsonld",
    "xml": "xml",
}


def _names(request: Request) -> export.Names:
    # 이 설치의 주소 — 프록시 뒤(TRUST_PROXY)면 https 와 접두어까지 반영된다.
    return export.Names(str(request.base_url))


def _require_size(db: Session, types: list[str] | None, user: User) -> None:
    """그래프를 **세우기 전에** 크기를 본다 — 세운 뒤에 거절하면 이미 메모리를 다 쓴 뒤다."""
    count = export.size_of(db, types, user=user)
    if count > export.MAX_OBJECTS:
        raise Conflict(
            code("RDF", 5),
            f"그래프로 올리기에는 큽니다(객체 {count:,}건, 상한 {export.MAX_OBJECTS:,}). "
            "`types` 로 범위를 좁히세요 — 한 타입 안의 세기 · 거르기는 목록 · 통계가 "
            "빠릅니다.",
            details={"objects": count, "limit": export.MAX_OBJECTS},
        )


def _fmt(value: str) -> str:
    return FORMATS.get(value.lower(), "ttl")


def _respond(graph: Graph, fmt: str, filename: str) -> Response:
    body, media = export.serialize(graph, fmt)
    ext = {"ttl": "ttl", "jsonld": "jsonld", "xml": "rdf"}[fmt]
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{ext}"'},
    )


@router.get("/schema")
def schema(
    request: Request,
    format: str = Query(default="ttl", description="ttl · jsonld · xml"),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """정의(타입 · 속성 · 관계 종류) → OWL."""
    return _respond(export.schema_graph(db, _names(request)), _fmt(format), "schema")


@router.get("/data")
def data(
    request: Request,
    format: str = Query(default="ttl"),
    type: list[str] | None = Query(default=None, description="이 타입들만(비우면 전부)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """데이터(객체 · 값 · 관계) → RDF. 정의는 `/schema` 와 합쳐 쓴다. **목록과 같은 것만**."""
    _require_size(db, type, user)
    return _respond(
        export.data_graph(db, _names(request), type, user=user), _fmt(format), "data"
    )


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    """SPARQL — `SELECT` 또는 `ASK` 만. 쓰기(INSERT · DELETE)와 바깥 호출(SERVICE)은 막는다."""
    types: list[str] | None = None
    """이 타입들만 그래프에 올린다. **안 주면 기록 타입(`usage="log"`)은 빠진다**(ADR
    0031) — 기록까지 물으려면 그 타입을 여기 적는다(5만 객체 상한). 큰 설치에서는 좁히는
    편이 빠르다."""
    infer: bool = False
    """OWL-RL 추론 — 상속 분류 · 역관계 · 이행이 답에 들어온다. 느리니 범위를 좁힌다."""
    limit: int = Field(default=200, ge=1, le=5_000)


class QueryOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    """`limit` 에서 잘렸나 — 잘린 줄 모르고 「전부 이것뿐」 으로 읽으면 안 된다."""
    triples: int
    inferred_triples: int
    elapsed_ms: int
    """질의를 계산해 줄을 꺼내는 데 든 시간 — 그래프 세우기 · 캐시 확인은 뺀다."""
    prefixes: dict[str, str]
    """질의에 쓸 접두어 — `sp:` 가 이 설치의 정의 자리다."""
    left_out: list[str] = []
    """`types` 를 안 줘서 **빠진 기록 타입**(slug) — 답에 그 기록은 없다. 기록까지 물으려면
    `types` 에 적어 다시 부른다."""
    source: str = "memory"
    """어디서 답했나 — `copy`(밤에 디스크에 세운 SPARQL 사본, ADR 0070 — 기록까지 전부, 객체
    상한 없음) · `memory`(질의마다 메모리에 세운 그래프 — 추론 · 부서로 좁힌 설치)."""
    copied_at: str | None = None
    """사본이면 언제 실었나 — SP 의 지금과 다를 수 있다. 답에 함께 말한다."""


_BLOCKED = ("insert", "delete", "load", "clear", "drop", "create", "service", "with")


def _algebra_iris(node: Any, out: set[str] | None = None) -> set[str]:
    """파싱한 질의 나무에 나오는 IRI 전부 — 삼중 패턴 · 속성 경로(`sp:a/sp:b`, `^sp:a`) ·
    FILTER 안까지. 접두어를 무엇으로 적었든 풀린 IRI 로 본다."""
    found = out if out is not None else set()
    if isinstance(node, URIRef):
        found.add(str(node))
    elif isinstance(node, dict):  # CompValue 도 dict 다
        for value in node.values():
            _algebra_iris(value, found)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for value in node:
            _algebra_iris(value, found)
    elif isinstance(node, Path):
        for value in vars(node).values():
            _algebra_iris(value, found)
    return found


def _asks_records(algebra: Any, names: export.Names, slugs: list[str]) -> list[str]:
    """빠진 기록 타입 중 질의가 부르는 것 — 그 타입(`sp:<slug>`) · 그 칸(`sp:<slug>.<칸>`) ·
    그 개체(`<base>o/<slug>/…`)."""
    iris = _algebra_iris(algebra)
    hit: list[str] = []
    for slug in slugs:
        kind = str(names.type_(slug))
        field = f"{names.ns}{slug}."
        individual = f"{names.base}o/{slug}/"
        if any(one == kind or one.startswith((field, individual)) for one in iris):
            hit.append(slug)
    return hit


def _algebra_names(node: Any, seen: set[str] | None = None) -> set[str]:
    """파싱한 질의 나무에 나오는 마디 이름 전부 — `SERVICE` 는 `ServiceGraphPattern` 이다."""
    out = seen if seen is not None else set()
    if isinstance(node, CompValue):
        out.add(node.name)
        for value in node.values():
            _algebra_names(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _algebra_names(value, out)
    return out


@router.post("/query", response_model=QueryOut)
def query(
    payload: QueryRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> QueryOut:
    """SPARQL 로 묻는다 — **MCP 로 들어온 AI 가 쓰는 자리.**

    목록 · 조건 · 통계로는 한 타입 안이 쉽지만, 「코어를 건너뛰어 잇는 물음」(모델 → 과제 →
    프로젝트를 한 번에, 또는 역관계로 거슬러)은 질의어가 낫다. 읽기만 한다 — 쓰기 · 바깥 호출은
    막고, 결과는 `limit` 에서 자른다."""
    # **이스케이프를 펼친 뒤에 본다** — rdflib 은 파싱 전에 `\uXXXX` 를 펼친다. 글자 그대로
    # 보면 `\u0053ERVICE <http://169.254.169.254/…>` 가 차단어를 지나 서버가 그 주소를
    # 불렀다(2026-10-08). 차단어는 첫 겹이고, 아래에서 파싱한 질의의 모양을 다시 본다.
    lowered = expandUnicodeEscapes(payload.query).lower()
    if not ("select" in lowered or "ask" in lowered):
        raise Conflict(code("RDF", 1), "SELECT 또는 ASK 질의만 됩니다.")
    for word in _BLOCKED:
        if re.search(rf"\b{word}\b", lowered):
            raise Conflict(code("RDF", 2), f"질의에 쓸 수 없는 말입니다: {word}")

    names = _names(request)
    # **그래프를 세우기 전에 질의를 푼다** — 접두어만 있으면 된다. 문법이 틀린 질의로 수만
    # 트리플을 세우지 않고, 범위에서 빠진 기록 타입을 부르는지 미리 본다.
    try:
        prepared = translateQuery(
            parseQuery(payload.query),
            initNs=dict(export.bound(names).namespace_manager.namespaces()),
        )
    except Exception as failure:  # rdflib 의 문법 오류는 종류가 많다
        raise Conflict(code("RDF", 4), f"질의를 이해하지 못했습니다: {failure}") from failure
    # **파싱한 모양으로 다시 본다** — 글자 검사는 표기를 바꾸면 비켜 간다. 묻기(SELECT ·
    # ASK)만, 그리고 바깥을 부르는 SERVICE 는 어디에도 없어야 한다.
    if prepared.algebra.name not in ("SelectQuery", "AskQuery"):
        raise Conflict(code("RDF", 1), "SELECT 또는 ASK 질의만 됩니다.")
    if "ServiceGraphPattern" in _algebra_names(prepared.algebra):
        raise Conflict(code("RDF", 2), "질의에 쓸 수 없는 말입니다: service")

    # **사본이 있으면 사본으로**(ADR 0070) — 기록까지 전부 · 객체 상한 없음. 추론은 사본에 없고
    # (속성 경로 `+` · `^` 로 묻는다), 부서로 좁힌 설치에서는 모든 부서를 보는 사람만(사본에는
    # 부서 구분이 없다) — 그 밖은 지금처럼 메모리에 세운다.
    copy = store.available()
    if copy is not None and not payload.infer and sees_every_department(user):
        return _from_copy(copy, payload, names)

    # **안 좁혔으면 기록 타입은 뺀다**(ADR 0031) — 기록 수천만 건이 5만 객체 상한에 걸려 코어를
    # 건너뛰는 물음까지 막히던 것. 빠진 기록을 부르는 질의는 0건이 아니라 거절로 — 0건이면
    # 「없다」 로 읽힌다.
    types = payload.types
    left_out: list[str] = []
    if not types:
        logs = sorted(one.slug for one in export.record_types(db))
        if logs:
            types = export.default_types(db)
            if not types:
                raise Conflict(
                    code("RDF", 6),
                    "기록 타입뿐이라 기본 범위(기록 타입을 뺀 것)가 비었습니다 — `types` 에 "
                    f"물을 기록 타입을 적으세요: {', '.join(logs)}",
                )
            left_out = logs
            asked = _asks_records(prepared.algebra, names, logs)
            if asked:
                cut_questions.note(
                    request,
                    "records",
                    code("RDF", 6),
                    cut_questions.sparql_shape(payload.types, payload.query),
                )
                raise Conflict(
                    code("RDF", 6),
                    f"질의가 기록 타입({', '.join(asked)})을 부르는데, `types` 를 안 주면 "
                    "기록 타입은 범위에서 빠집니다 — 그대로면 0건이라 「없다」 로 읽힙니다. "
                    "`types` 에 그 기록 타입과 질의가 지나는 축 타입을 적어 다시 부르세요(5만 "
                    "객체 상한). 기록을 세거나 나누는 물음이면 통계(objects_summary) · 지표가 "
                    "빠릅니다.",
                    details={"record_types": asked},
                )

    try:
        _require_size(db, types, user)
    except Conflict as refused:
        # 끊긴 질문으로 남긴다 — 트리플 스토어가 정말 필요한지를 실제 물음으로 가른다
        # (ADR 0031).
        cut_questions.note(
            request,
            "too_big",
            refused.code,
            cut_questions.sparql_shape(payload.types, payload.query),
        )
        raise
    graph, stored, added = export.graph_for(db, names, types, payload.infer, user=user)
    if payload.infer and stored > export.INFER_MAX_TRIPLES:
        cut_questions.note(
            request,
            "too_big",
            code("RDF", 3),
            cut_questions.sparql_shape(payload.types, payload.query),
        )
        raise Conflict(
            code("RDF", 3),
            f"추론을 켜기에는 큽니다({stored} 트리플). `types` 로 범위를 좁히세요 "
            f"(상한 {export.INFER_MAX_TRIPLES}).",
        )

    # **줄을 꺼내는 데까지 잰다** — rdflib 은 `query` 에서 질의를 세우기만 하고 줄을 꺼낼 때
    # 계산한다. 예전에는 `query` 만 재서 `elapsed_ms` 가 늘 0 이었고(200만 건 실측 DB 에서
    # 0.5초 걸린 질의도), 꺼내다 난 평가 오류는 409 가 아니라 500 이었다.
    started = time.perf_counter()
    try:
        result = graph.query(prepared)
        answer = bool(result.askAnswer) if result.type == "ASK" else None
        found = [] if answer is not None else list(islice(result, payload.limit + 1))
    except Exception as failure:  # rdflib 의 평가 오류도 종류가 많다
        raise Conflict(code("RDF", 4), f"질의를 이해하지 못했습니다: {failure}") from failure
    elapsed = int((time.perf_counter() - started) * 1000)

    if answer is not None:
        return QueryOut(
            columns=["answer"],
            rows=[{"answer": answer}],
            truncated=False,
            triples=stored,
            inferred_triples=added,
            elapsed_ms=elapsed,
            prefixes=_prefixes(names),
            left_out=left_out,
        )
    columns = [str(one) for one in (result.vars or [])]
    rows: list[dict[str, Any]] = []
    truncated = len(found) > payload.limit
    for row in found[: payload.limit]:
        values = list(row) if isinstance(row, tuple) else []
        rows.append(
            {
                name: export.shorten(
                    graph, values[position] if position < len(values) else None
                )
                for position, name in enumerate(columns)
            }
        )
    return QueryOut(
        columns=columns,
        rows=rows,
        truncated=truncated,
        triples=stored,
        inferred_triples=added,
        elapsed_ms=elapsed,
        prefixes=_prefixes(names),
        left_out=left_out,
    )


def _from_copy(copy: store.Copy, payload: QueryRequest, names: export.Names) -> QueryOut:
    prefixes = _prefixes(names)
    started = time.perf_counter()
    out = store.run(
        copy, payload.query, base=names.base, prefixes=prefixes, limit=payload.limit
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    if out["kind"] == "ask":
        columns: list[str] = ["answer"]
        rows: list[dict[str, Any]] = [{"answer": out["answer"]}]
        truncated = False
    else:
        columns = list(out["vars"])
        rows = [
            {
                name: store.plain(term, prefixes)
                for name, term in zip(columns, row, strict=True)
            }
            for row in out["rows"]
        ]
        truncated = bool(out["truncated"])
    return QueryOut(
        columns=columns,
        rows=rows,
        truncated=truncated,
        triples=copy.triples or 0,
        inferred_triples=0,
        elapsed_ms=elapsed,
        prefixes=prefixes,
        source="copy",
        copied_at=copy.copied_at,
    )


def _sparql_text(request_query: str | None, body: bytes, content_type: str) -> str:
    """SPARQL 1.1 Protocol 의 세 모양 — `GET ?query=` · `POST` 폼(`query=`) · `POST`
    `application/sparql-query`(몸통이 질의)."""
    if request_query:
        return request_query
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind == "application/sparql-query":
        return body.decode("utf-8")
    if kind == "application/x-www-form-urlencoded":
        from urllib.parse import parse_qs

        found = parse_qs(body.decode("utf-8")).get("query")
        if found:
            return found[0]
    raise Conflict(
        code("RDF", 13),
        "질의가 없습니다 — `?query=` 또는 POST(`application/sparql-query` 몸통 · 폼의 "
        "`query`).",
    )


@router.api_route("/sparql", methods=["GET", "POST"])
async def sparql_protocol(
    request: Request,
    query: str | None = Query(default=None, description="SPARQL(SELECT · ASK) — GET 일 때"),
    user: User = Depends(current_user),
) -> Response:
    """**바깥 시스템용 표준 SPARQL 창구**(SPARQL 1.1 Protocol, ADR 0070) — 답은
    `application/sparql-results+json`. 밤에 세운 사본에 묻는다(기록까지 전부).

    토큰(Bearer)으로 인증한다 — SPARQL 도구(Jena · SPARQLWrapper · …)에 머리
    `Authorization: Bearer <토큰>` 을 넣는다. 읽기만(SELECT · ASK), 시간 · 줄 상한이 있다
    (`RDF_QUERY_SECONDS` · `RDF_ROWS` — 잘리면 머리 `X-SP-Truncated: true`). 머리
    `X-SP-Copied-At` 이 언제 실은 사본인지다. 부서로 좁힌 설치에서는 모든 부서를 보는
    사람만."""
    text = _sparql_text(query, await request.body(), request.headers.get("content-type", ""))
    copy = store.available()
    if copy is None:
        raise AppError(
            code("RDF", 14),
            "이 설치에는 SPARQL 사본이 없습니다(밤에 아직 안 실었거나 꺼져 있음) — "
            "`POST /api/rdf/query` 가 메모리에 세워 묻는다(객체 5만 개 상한).",
            status=503,
        )
    if not sees_every_department(user):
        raise Forbidden(
            code("RDF", 15),
            "SPARQL 사본은 모든 부서를 보는 사람만 묻습니다 — 사본에는 부서 구분이 없습니다"
            "(조회가 부서로 좁혀진 설치). `POST /api/rdf/query` 는 보이는 것만으로 답한다.",
        )
    names = _names(request)
    _check(text, names)
    settings = get_settings()
    out = await run_in_threadpool(
        store.run,
        copy,
        text,
        base=names.base,
        prefixes=_prefixes(names),
        limit=settings.rdf_rows,
    )
    headers = {"X-SP-Copied-At": copy.copied_at or "", "Cache-Control": "no-store"}
    if out["kind"] == "ask":
        body: dict[str, Any] = {"head": {}, "boolean": out["answer"]}
    else:
        headers["X-SP-Truncated"] = "true" if out["truncated"] else "false"
        body = {
            "head": {"vars": out["vars"]},
            "results": {
                "bindings": [
                    {
                        name: _binding(term)
                        for name, term in zip(out["vars"], row, strict=True)
                        if term is not None
                    }
                    for row in out["rows"]
                ]
            },
        }
    return Response(
        json.dumps(body, ensure_ascii=False),
        media_type="application/sparql-results+json",
        headers=headers,
    )


def _binding(term: dict[str, Any]) -> dict[str, str]:
    """SPARQL 1.1 Query Results JSON 의 값 하나."""
    if term["t"] == "uri":
        return {"type": "uri", "value": term["v"]}
    if term["t"] == "bnode":
        return {"type": "bnode", "value": term["v"]}
    out = {"type": "literal", "value": term["v"]}
    if term.get("lang"):
        out["xml:lang"] = term["lang"]
    elif term.get("dt") and term["dt"] != "http://www.w3.org/2001/XMLSchema#string":
        out["datatype"] = term["dt"]
    return out


def _check(text: str, names: export.Names) -> None:
    """`query` 와 같은 검사 — 묻기(SELECT · ASK)만, 쓰기 · 바깥 호출(SERVICE)은 막는다."""
    lowered = expandUnicodeEscapes(text).lower()
    if not ("select" in lowered or "ask" in lowered):
        raise Conflict(code("RDF", 1), "SELECT 또는 ASK 질의만 됩니다.")
    for word in _BLOCKED:
        if re.search(rf"\b{word}\b", lowered):
            raise Conflict(code("RDF", 2), f"질의에 쓸 수 없는 말입니다: {word}")
    try:
        prepared = translateQuery(
            parseQuery(text),
            initNs=dict(export.bound(names).namespace_manager.namespaces()),
        )
    except Exception as failure:  # rdflib 의 문법 오류는 종류가 많다
        raise Conflict(code("RDF", 4), f"질의를 이해하지 못했습니다: {failure}") from failure
    if prepared.algebra.name not in ("SelectQuery", "AskQuery"):
        raise Conflict(code("RDF", 1), "SELECT 또는 ASK 질의만 됩니다.")
    if "ServiceGraphPattern" in _algebra_names(prepared.algebra):
        raise Conflict(code("RDF", 2), "질의에 쓸 수 없는 말입니다: service")


def _prefixes(names: export.Names) -> dict[str, str]:
    return {
        "sp": str(names.ns),
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "owl": "http://www.w3.org/2002/07/owl#",
        "skos": "http://www.w3.org/2004/02/skos/core#",
        "dcterms": "http://purl.org/dc/terms/",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
    }


@router.get("/inferred")
def inferred(
    request: Request,
    format: str = Query(default="ttl"),
    type: list[str] | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """OWL-RL 추론으로 **새로 생긴** 트리플만 — 상속으로 얻은 분류 · 역관계 · 이행 관계.

    플랫폼 안에서 돌리므로 별도 추론 서버가 없어도 「추론하면 무엇이 더 나오나」 를 본다.
    규모가 커지면 `/schema` + `/data` 를 트리플 스토어에 넣고 거기서 돌린다."""
    names = _names(request)
    _require_size(db, type, user)
    graph = export.inferred(
        export.schema_graph(db, names), export.data_graph(db, names, type, user=user)
    )
    return _respond(graph, _fmt(format), "inferred")
