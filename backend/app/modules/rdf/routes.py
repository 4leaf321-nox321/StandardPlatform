"""RDF/OWL 내보내기 — 읽기만. 어느 사용자든 자기가 볼 수 있는 것을 받아 간다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from rdflib import Graph
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.rdf import export
from app.shared.auth import current_user

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
