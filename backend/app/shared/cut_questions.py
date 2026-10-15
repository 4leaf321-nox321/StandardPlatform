"""끊긴 질문을 남긴다 — 답을 못 받고 끊긴 물음과 그 모양(ADR 0031, `CutQuestion`).

시간 상한(504)에 걸린 목록 · 통계는 오류 처리기가(`errors.register_error_handlers`), SPARQL 의
거절(그래프가 큼 · 빠진 기록 타입)은 그 경로가 남긴다. 같은 모양이면 같은 물음이라 화면이 묶어
「몇 번 · 몇 명」 을 보인다(`server/performance.cut_stats`).

**따로 세션으로 넣는다** — 요청의 트랜잭션은 거절과 함께 버려진다. **넣다가 실패해도 응답은
그대로다** — 기록하려다 사람이 받을 안내를 잃으면 안 된다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

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


# --- 지표 초안(ADR 0032) ----------------------------------------------------------------

#: 초안의 기준으로 안 삼는 칸 — 기록마다 다른 값(이름 · 식별자)은 셀이 기록 수만큼 된다.
_NOT_DIMS = frozenset({"label", "key", "status", "workspace", "created_year"})
_DATE_KINDS = frozenset({"date", "datetime"})
_NAME_RE = re.compile(r"[^a-z0-9_]+")
#: 기준 이름으로 못 쓰는 말(`metrics/spec.RESERVED` 와 같다 — 셀의 칸 · 집계).
_RESERVED = frozenset(
    {
        "period",
        "cohort",
        "age",
        "workspace",
        "count",
        "value_count",
        "sum",
        "min",
        "max",
        "avg",
    }
)
#: 지표 기준의 상한(`metrics/spec.MAX_DIMENSIONS`).
_MAX_DIMS = 8


def _address(field: str) -> str:
    """조건의 칸 → 지표의 주소. 자기 칸은 `properties.<키>`, 이어진 것 너머는 그대로."""
    if "." in field or field.startswith(("ref.", "out.", "in.")):
        return field
    return f"properties.{field}"


def _dim_name(address: str, taken: set[str]) -> str:
    parts = [one for one in re.split(r"[.:]", address) if one]
    last = parts[-1] if parts else "dim"
    if last in ("label", "key", "status") and len(parts) > 1:
        last = f"{parts[-2]}_{last}"
    base = _NAME_RE.sub("_", last.lower()).strip("_") or "dim"
    if not base[0].isalpha():
        base = f"d_{base}"
    base = base[:28]
    if base in _RESERVED:
        base = f"{base}_dim"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def suggest(
    db: Session, kind: str, shape: dict[str, Any], count: int
) -> dict[str, Any] | None:
    """끊긴 목록 · 통계의 모양 → 지표 정의 초안(저장하지 않는다 — 사람이 계획을 보고 만든다).

    원천 = 그 타입, 집계 = 건수(합계 · 평균이면 그 칸), 날짜 칸 기준이면 시간 칸과 단위, 나머지
    기준 · 세부 기준 · **조건의 칸**은 기준으로 — 조건의 값은 남지 않았고, 지표는 읽을 때
    `d.<기준>=값` 으로 거른다. SPARQL 의 끊김은 짓지 않는다(지표의 모양이 아니다)."""
    from app.modules.objects.services import properties_of
    from app.modules.ontology.models import ObjectType

    type_slug = shape.get("type")
    if kind != "timeout" or not isinstance(type_slug, str):
        return None
    source = db.scalar(select(ObjectType).where(ObjectType.slug == type_slug))
    if source is None:
        return None
    kinds = {one.key: one.data_type for one in properties_of(db, source.id)}

    def is_date(address: str) -> bool:
        return address.startswith("properties.") and (
            kinds.get(address.removeprefix("properties.")) in _DATE_KINDS
        )

    addresses: list[str] = []
    for key in ("group_by", "split_by"):
        value = shape.get(key)
        if isinstance(value, str) and value and value not in _NOT_DIMS:
            addresses.append(value)
    for condition in shape.get("conditions") or []:
        field = str(condition).split(" ")[0].removeprefix("제외")
        if not field or field in _NOT_DIMS:
            continue
        addresses.append(_address(field))
    spec: dict[str, Any] = {
        "measure": "count",
        "dimensions": [],
        "filters": [],
        "settle_days": 0,
    }
    metric = shape.get("metric")
    if (
        isinstance(metric, str)
        and metric in ("sum", "avg", "min", "max")
        and shape.get("metric_field")
    ):
        spec["measure"] = metric
        spec["measure_field"] = str(shape["metric_field"])
    taken: set[str] = set()
    seen: set[str] = set()
    for address in addresses:
        if address in seen:
            continue
        seen.add(address)
        if is_date(address):
            if "time" not in spec:
                grain = shape.get("grain") if isinstance(shape.get("grain"), str) else "month"
                spec["time"] = {"address": address, "grain": grain or "month"}
            continue
        if len(spec["dimensions"]) >= _MAX_DIMS:
            break
        spec["dimensions"].append({"name": _dim_name(address, taken), "address": address})
    if not spec["dimensions"] and "time" not in spec:
        return None
    digest = hashlib.sha1(
        json.dumps([type_slug, spec], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:8]
    names = ", ".join(one["name"] for one in spec["dimensions"]) or "기간"
    return {
        "slug": f"cut_{digest}",
        "label": f"{source.label} — {names} (끊긴 질문)"[:100],
        "description": (
            f"끊긴 질문에서 지은 초안 — {count}번 끊겼다. 조건의 값은 남지 않아 조건의 칸을 "
            "기준으로 두었다: 읽을 때 그 값으로 거른다(d.<기준>=값)."
        ),
        "source_type_slug": type_slug,
        "spec": spec,
    }


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
