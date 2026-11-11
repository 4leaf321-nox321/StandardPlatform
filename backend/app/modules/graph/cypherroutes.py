"""그래프 사본에 묻는 경로(4단계) — 모양 · 상태 · Cypher 읽기 질의.

질의는 웹 워커 안에서 사본(같은 서버의 Neo4j)에 묻는다 — 셈은 사본이 하고, 여기는 지키기
(읽기만 · 상한 · 보는 범위)와 값을 옮기는 일만 한다(`cypher`).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.graph import copyschema, cypher
from app.modules.graph.cypherschemas import (
    CypherAnswerOut,
    CypherAsk,
    CypherShapeOut,
    CypherStatusOut,
)
from app.shared.auth import current_user
from app.shared.errors import AppError

router = APIRouter(prefix="/graph/cypher", tags=["graph"])


@router.get("", response_model=CypherStatusOut)
def cypher_status(user: User = Depends(current_user)) -> Any:
    """사본이 이 설치에 있나 · 닿나 · 언제 실었나 · 상한. 닿지 않아도 200 —
    `reachable` 이 말한다."""
    cypher.require_visible(user)
    seconds, rows = cypher.limits()
    out: dict[str, Any] = {
        "enabled": cypher.enabled(),
        "reachable": False,
        "copy": None,
        "error": None,
        "query_seconds": seconds,
        "max_rows": rows,
    }
    if not out["enabled"]:
        out["error"] = "이 설치에는 그래프 사본이 없습니다(GRAPHDB_URL 이 비어 있음)."
        return out
    try:
        out["copy"] = asdict(cypher.copy_info(fresh=True))
        out["reachable"] = True
    except AppError as caught:
        out["error"] = caught.message
    return out


@router.get("/schema", response_model=CypherShapeOut)
def cypher_schema(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Any:
    """사본의 라벨(타입) · 관계 종류(관계 · 참조 칸) · 속성과 쓰는 규칙 — SP 의 정의에서
    그대로. 질의를 쓰기 전에 본다(이름을 추측하지 않는다)."""
    cypher.require_visible(user)
    return asdict(copyschema.shape(db))


@router.post("", response_model=CypherAnswerOut)
def cypher_query(body: CypherAsk, user: User = Depends(current_user)) -> Any:
    """Cypher **읽기** 질의 하나 — 열 · 줄(상한까지, 넘으면 `truncated`) · 걸린 시간 ·
    사본의 판.

    노드는 `{_node, id, type, key, label, props}`(SP 의 id 그대로 — 상세는 SP 로), 선은
    `{_rel, start, end, props}`, 경로는 `{_path, nodes, rels}` 로 온다."""
    cypher.require_visible(user)
    answer = cypher.run(body.query, body.params, limit=body.limit)
    return {
        "columns": answer.columns,
        "rows": answer.rows,
        "truncated": answer.truncated,
        "seconds": answer.seconds,
        "copy": asdict(answer.copy),
        "notes": answer.notes,
    }
