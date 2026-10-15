"""끊긴 질문을 남긴다 — 답을 못 받고 끊긴 물음과 그 모양(ADR 0031, `CutQuestion`).

시간 상한(504)에 걸린 목록 · 통계는 오류 처리기가(`errors.register_error_handlers`), SPARQL 의
거절(그래프가 큼 · 빠진 기록 타입)은 그 경로가 남긴다. 같은 모양이면 같은 물음이라 화면이 묶어
「몇 번 · 몇 명」 을 보인다(`server/performance.cut_stats`).

**따로 세션으로 넣는다** — 요청의 트랜잭션은 거절과 함께 버려진다. **넣다가 실패해도 응답은
그대로다** — 기록하려다 사람이 받을 안내를 잃으면 안 된다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.database import SessionLocal
from app.modules.audit.models import CutQuestion
from app.shared.access_log import USER_ID_SCOPE_KEY, route_template
from app.shared.request_context import get_request_id

log = logging.getLogger(__name__)

KIND_LABELS = {
    "timeout": "시간 상한",
    "too_big": "그래프가 큼",
    "records": "빠진 기록 타입",
}

#: SPARQL 질의문은 이만큼만 — 물음을 알아볼 만큼. 긴 VALUES 목록이 표를 키우지 않게.
QUERY_TEXT_MAX = 2000

#: 값 없이 「걸었다」 만 남기는 거르기.
_FLAGS = ("q", "under")
#: 값까지 남기는 것 — 물음의 모양이고 사람이 친 글자가 아니다.
_VALUES = (
    "group_by",
    "split_by",
    "metric",
    "metric_field",
    "grain",
    "status",
    "year",
    "types",
)


def objects_shape(request: Request) -> dict[str, Any]:
    """목록 · 통계 · 추이의 모양 — 타입 · 기준 · 세부 기준 · 조건의 **칸과 연산**(값은
    뺀다)."""
    params = request.query_params
    shape: dict[str, Any] = {}
    type_slug = request.path_params.get("type_slug")
    if type_slug:
        shape["type"] = str(type_slug)
    for key in _VALUES:
        if params.get(key):
            shape[key] = params.get(key)
    for key in _FLAGS:
        if params.get(key):
            shape[key] = True
    conditions: set[str] = set()
    for key in params:
        if key.startswith("f.") and "." in key[2:]:
            field, op = key[2:].rsplit(".", 1)
            conditions.add(f"{field} {op}")
        elif key.startswith("nf") and key.count(".") >= 2:
            _, rest = key.split(".", 1)
            field, op = rest.rsplit(".", 1)
            conditions.add(f"제외 {field} {op}")
        elif key.startswith("p."):
            conditions.add(f"{key[2:]} eq")
    if conditions:
        shape["conditions"] = sorted(conditions)
    return shape


def sparql_shape(types: list[str] | None, query: str) -> dict[str, Any]:
    """SPARQL 의 모양 — 범위(`types`)와 질의문(앞부분)."""
    return {"types": sorted(types or []), "query": query.strip()[:QUERY_TEXT_MAX]}


def note(request: Request, kind: str, code: str, shape: dict[str, Any]) -> None:
    """한 줄 남긴다. 루프 위(async 처리기)에서는 `run_in_threadpool` 로 부른다."""
    factory = getattr(request.app.state, "session_factory", SessionLocal)
    try:
        with factory() as db:
            db.add(
                CutQuestion(
                    kind=kind,
                    code=code,
                    method=request.method,
                    route=route_template(request.scope, request.url.path),
                    shape=shape,
                    user_id=request.scope.get(USER_ID_SCOPE_KEY),
                    request_id=get_request_id() or None,
                )
            )
            db.commit()
    except Exception:
        log.warning("끊긴 질문을 남기지 못했습니다: %s %s", kind, code, exc_info=True)
