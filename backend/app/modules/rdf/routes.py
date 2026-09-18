"""RDF/OWL 내보내기 — 읽기만. 어느 사용자든 자기가 볼 수 있는 것을 받아 간다."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from rdflib import Graph
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.rdf import export
from app.shared.auth import current_user
from app.shared.errors import Conflict, code

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
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """데이터(객체 · 값 · 관계) → RDF. 정의는 `/schema` 와 합쳐 쓴다."""
    return _respond(export.data_graph(db, _names(request), type), _fmt(format), "data")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    """SPARQL — `SELECT` 또는 `ASK` 만. 쓰기(INSERT · DELETE)와 바깥 호출(SERVICE)은 막는다."""
    types: list[str] | None = None
    """이 타입들만 그래프에 올린다. **큰 설치에서는 좁히는 편이 빠르다.**"""
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
    prefixes: dict[str, str]
    """질의에 쓸 접두어 — `sp:` 가 이 설치의 정의 자리다."""


_BLOCKED = ("insert", "delete", "load", "clear", "drop", "create", "service", "with")


@router.post("/query", response_model=QueryOut)
def query(
    payload: QueryRequest,
    request: Request,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> QueryOut:
    """SPARQL 로 묻는다 — **MCP 로 들어온 AI 가 쓰는 자리.**

    목록 · 조건 · 통계로는 한 타입 안이 쉽지만, 「코어를 건너뛰어 잇는 물음」(모델 → 과제 →
    프로젝트를 한 번에, 또는 역관계로 거슬러)은 질의어가 낫다. 읽기만 한다 — 쓰기 · 바깥 호출은
    막고, 결과는 `limit` 에서 자른다."""
    lowered = payload.query.lower()
    if not ("select" in lowered or "ask" in lowered):
        raise Conflict(code("RDF", 1), "SELECT 또는 ASK 질의만 됩니다.")
    for word in _BLOCKED:
        if re.search(rf"\b{word}\b", lowered):
            raise Conflict(code("RDF", 2), f"질의에 쓸 수 없는 말입니다: {word}")

    names = _names(request)
    graph, stored, added = export.graph_for(db, names, payload.types, payload.infer)
    if payload.infer and stored > export.INFER_MAX_TRIPLES:
        raise Conflict(
            code("RDF", 3),
            f"추론을 켜기에는 큽니다({stored} 트리플). `types` 로 범위를 좁히세요 "
            f"(상한 {export.INFER_MAX_TRIPLES}).",
        )

    started = time.perf_counter()
    try:
        result = graph.query(payload.query)
    except Exception as failure:  # rdflib 의 문법 오류는 종류가 많다
        raise Conflict(code("RDF", 4), f"질의를 이해하지 못했습니다: {failure}") from failure
    elapsed = int((time.perf_counter() - started) * 1000)

    if result.type == "ASK":
        return QueryOut(
            columns=["answer"],
            rows=[{"answer": bool(result.askAnswer)}],
            truncated=False,
            triples=stored,
            inferred_triples=added,
            elapsed_ms=elapsed,
            prefixes=_prefixes(names),
        )
    columns = [str(one) for one in (result.vars or [])]
    rows: list[dict[str, Any]] = []
    truncated = False
    for index, row in enumerate(result):
        if index >= payload.limit:
            truncated = True
            break
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
    )


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
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """OWL-RL 추론으로 **새로 생긴** 트리플만 — 상속으로 얻은 분류 · 역관계 · 이행 관계.

    플랫폼 안에서 돌리므로 별도 추론 서버가 없어도 「추론하면 무엇이 더 나오나」 를 본다.
    규모가 커지면 `/schema` + `/data` 를 트리플 스토어에 넣고 거기서 돌린다."""
    names = _names(request)
    graph = export.inferred(export.schema_graph(db, names), export.data_graph(db, names, type))
    return _respond(graph, _fmt(format), "inferred")
