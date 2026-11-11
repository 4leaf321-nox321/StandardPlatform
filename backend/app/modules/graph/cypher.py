"""그래프 사본에 묻기(4단계) — **Cypher 읽기만**.

사본은 설치마다 Neo4j 무료판 하나(127.0.0.1)이고 밤마다 SP 에서 통째로 다시 싣는다
(`copyexport` · `deploy/graphcopy.sh`). 「불합격 8건 이상 모델의 BOM 중 최근 설계 변경
부품의 공급사 순위」 처럼 관계 여럿을 엮는 물음을 한 문장으로 묻는 자리다 — SP 의 조건으로는
여러 번 불러야 했다(견주기 Q4 142번 · 27초 → Neo4j 0.6초).

무료판에는 역할이 없다(모든 사용자가 관리자). 그래서 **SP 가 지킨다**:

- 읽기만 — EXPLAIN 으로 질의의 종류를 먼저 보고(`r` 만), 실제로는 **읽기 거래** 안에서
  돌린다(쓰기 절은 Neo4j 가 거절한다). 프로시저는 사본 설정의 허용 목록만 뜬다 — 파일 ·
  바깥 주소를 읽는 것(`LOAD CSV` · `apoc.load` …)은 아예 없다. 여기서도 한 번 더 막는다.
- 상한 — 거래 시간(`GRAPHDB_QUERY_SECONDS`), 거래 메모리(사본 설정), 줄(`GRAPHDB_ROWS`).
- 보는 범위 — 사본에는 부서 구분이 없다. 조회를 부서로 좁힌 설치에서는 **모든 부서를 보는
  사람만** 묻는다(수 · 이름이 남의 부서 객체를 드러낸다 — 밤 그래프 계산과 같은 규칙).
- 늘 「언제 실은 사본인가」(`copied_at`)를 함께 돌려준다 — SP 의 지금과 다를 수 있다.
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import neo4j
from neo4j import READ_ACCESS, GraphDatabase, Query
from neo4j import exceptions as neo4j_errors
from neo4j.graph import Node, Path, Relationship

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.graph.copyexport import META_LABEL, NODE_LABEL
from app.shared.errors import AppError, Forbidden, code
from app.shared.permissions import sees_every_department

DATABASE = "neo4j"
#: 질의 글의 상한 — 사람이 · AI 가 쓰는 한 문장이다.
MAX_QUERY = 20_000
#: 한 번에 돌려줄 줄의 상한 — 설정(`GRAPHDB_ROWS`)이 이보다 크면 이것.
MAX_ROWS = 5_000
#: 노드 · 선의 글 속성은 앞부분만 돌려준다 — 보고서 본문이 줄마다 실리지 않게.
CLIP = 300
#: 읽기 거래 밖에서도 막는 것 — 파일 · 바깥 주소 · 다른 DB · 다른 질의를 부르는 것.
_FORBIDDEN = re.compile(
    r"\bLOAD\s+CSV\b|\bUSE\s+[`\w]|\bdbms\s*\.|\bapoc\s*\.\s*(load|import|export|cypher|"
    r"periodic|trigger|systemdb|refactor|create|merge|lock|do|when|case|log|util|schema|"
    r"cluster|config|metrics|warmup)\b",
    re.IGNORECASE,
)
#: 앞의 `EXPLAIN` · `PROFILE` — 떼고 돌린다(여기서 늘 EXPLAIN 으로 먼저 보므로 둘이 부딪힌다).
_LEADING = re.compile(r"^\s*(EXPLAIN|PROFILE)\s+", re.IGNORECASE)
#: 판 표시를 몇 초 들고 있나 — 질의마다 묻지 않게(밤에 한 번 바뀐다).
_META_SECONDS = 60


class Unavailable(AppError):
    """사본이 없거나(이 설치에 안 깔렸다) 닿지 않는다(다시 싣는 중 · 꺼짐)."""


@dataclass
class Copy:
    copied_at: str | None = None
    sp_version: str | None = None
    nodes: int | None = None
    relationships: int | None = None


@dataclass
class Answer:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    seconds: float
    copy: Copy
    notes: list[str] = field(default_factory=list)


_lock = threading.Lock()
_driver: neo4j.Driver | None = None
_driver_key: tuple[str, str, str] | None = None
_meta: tuple[float, Copy] | None = None


def enabled() -> bool:
    return bool(get_settings().graphdb_url)


def driver() -> neo4j.Driver:
    """설정의 사본에 붙는 드라이버 하나(프로세스마다) — 설정이 바뀌면 새로."""
    global _driver, _driver_key
    settings = get_settings()
    if not settings.graphdb_url:
        raise Unavailable(
            code("GRAPH", 50),
            "이 설치에는 그래프 사본(Neo4j)이 없습니다 — 관계를 엮는 물음은 SP 의 조건 · "
            "통계로(개수 · 위 전부 · 부분 목록) 묻습니다.",
            status=503,
        )
    key = (settings.graphdb_url, settings.graphdb_user, settings.graphdb_password)
    with _lock:
        if _driver is None or _driver_key != key:
            if _driver is not None:
                _driver.close()
            _driver = GraphDatabase.driver(
                settings.graphdb_url,
                auth=(settings.graphdb_user, settings.graphdb_password),
                connection_timeout=5,
                max_connection_pool_size=20,
            )
            _driver_key = key
        return _driver


def close() -> None:
    global _driver, _driver_key, _meta
    with _lock:
        if _driver is not None:
            _driver.close()
        _driver, _driver_key, _meta = None, None, None


def require_visible(user: User) -> None:
    if not sees_every_department(user):
        raise Forbidden(
            code("GRAPH", 51),
            "그래프 사본은 모든 부서를 보는 사람만 묻습니다 — 사본에는 부서 구분이 없어 "
            "수 · 이름이 남의 부서 객체를 드러냅니다(조회가 부서로 좁혀진 설치).",
        )


def _bad(number: int, message: str) -> AppError:
    return AppError(code("GRAPH", number), message, status=422)


def check(query: str) -> str:
    """질의 글 — 비었거나 · 너무 길거나 · 막는 것을 부르면 거절. 끝의 `;` 와 앞의
    `EXPLAIN` · `PROFILE` 은 뗀다."""
    text = _LEADING.sub("", query.strip().rstrip(";").strip()).strip()
    if not text:
        raise _bad(52, "질의가 비었습니다.")
    if len(text) > MAX_QUERY:
        raise _bad(52, f"질의가 {MAX_QUERY:,}자를 넘습니다 — 나눠 묻습니다.")
    if ";" in text:
        raise _bad(52, "한 번에 한 문장만 묻습니다(`;` 로 잇지 않는다).")
    found = _FORBIDDEN.search(text)
    if found:
        raise _bad(
            53,
            f"그래프 사본에서는 「{found.group(0)}」 를 못 씁니다 — 읽기 질의(MATCH · "
            "RETURN · 경로 · 집계)와 허용된 읽기 프로시저(apoc.path · apoc.coll …)만.",
        )
    return text


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > CLIP:
        return value[:CLIP] + "…"
    return value


def _node(one: Node) -> dict[str, Any]:
    labels = sorted(label for label in one.labels if label != NODE_LABEL)
    props = {
        key: _value(item)
        for key, item in one.items()
        if key not in ("id", "key", "label", "status", "ws")
    }
    return {
        "_node": True,
        "id": one.get("id"),
        "type": labels[0] if labels else None,
        "key": one.get("key"),
        "label": one.get("label"),
        **({"props": props} if props else {}),
    }


def _value(value: Any) -> Any:
    """Neo4j 값 → JSON — 노드는 SP 의 id · 타입 · 식별자 · 이름, 선은 종류 · 두 끝 · 칸."""
    if isinstance(value, Node):
        return _node(value)
    if isinstance(value, Relationship):
        start, end = value.start_node, value.end_node
        props = {key: _value(item) for key, item in value.items()}
        return {
            "_rel": value.type,
            "start": start.get("id") if start is not None else None,
            "end": end.get("id") if end is not None else None,
            **({"props": props} if props else {}),
        }
    if isinstance(value, Path):
        return {
            "_path": True,
            "nodes": [_node(one) for one in value.nodes],
            "rels": [_value(one) for one in value.relationships],
        }
    if isinstance(value, list | tuple):
        return [_value(one) for one in value]
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bool | int | float | str) or value is None:
        return _clip(value)
    iso = getattr(value, "iso_format", None)
    return iso() if callable(iso) else str(value)


def limits() -> tuple[int, int]:
    """(시간 상한 초, 줄 상한)."""
    settings = get_settings()
    return max(1, settings.graphdb_query_seconds), max(1, min(settings.graphdb_rows, MAX_ROWS))


def copy_info(*, fresh: bool = False) -> Copy:
    """사본의 판 표시(`_SPCopy`) — 언제 실었나. 닿지 않으면 `Unavailable`."""
    global _meta
    now = time.monotonic()
    if not fresh and _meta is not None and now - _meta[0] < _META_SECONDS:
        return _meta[1]
    query = Query(f"MATCH (m:{META_LABEL}) RETURN m LIMIT 1", timeout=5)
    try:
        with driver().session(database=DATABASE, default_access_mode=READ_ACCESS) as session:
            record = session.run(query).single()
    except (neo4j_errors.Neo4jError, neo4j_errors.DriverError, OSError) as caught:
        raise _unavailable(caught) from caught
    meta = record["m"] if record is not None else None
    found = Copy(
        copied_at=meta.get("copied_at") if meta is not None else None,
        sp_version=meta.get("sp_version") if meta is not None else None,
        nodes=meta.get("nodes") if meta is not None else None,
        relationships=meta.get("relationships") if meta is not None else None,
    )
    _meta = (now, found)
    return found


def _unavailable(caught: Exception) -> Unavailable:
    return Unavailable(
        code("GRAPH", 54),
        "그래프 사본에 닿지 않습니다 — 밤에 다시 싣는 중(새벽 몇 분)이거나 서비스가 꺼져 "
        "있습니다. 잠시 뒤 다시 묻거나 SP 의 조건 · 통계로 묻습니다. "
        f"({type(caught).__name__})",
        status=503,
    )


def _refused(caught: neo4j_errors.Neo4jError) -> AppError:
    """Neo4j 의 오류 → 사람이 고칠 수 있는 말."""
    found = caught.code or ""
    message = (caught.message or "").strip().splitlines()
    first = message[0][:500] if message else ""
    if "TransactionTimedOut" in found or "Terminated" in found:
        seconds, _rows = limits()
        return AppError(
            code("GRAPH", 55),
            f"질의가 {seconds}초 안에 끝나지 않았습니다 — 출발점을 좁히거나(`MATCH (m:타입 "
            "{key: …})`) 걸음 수 상한(`*1..6`)을 둡니다.",
            status=504,
        )
    if "MemoryPoolOutOfMemory" in found or "OutOfMemory" in found:
        return _bad(
            56,
            "질의가 거래 메모리 상한을 넘었습니다 — 중간 결과를 줄입니다(먼저 좁히고 · "
            "`DISTINCT` · 집계로).",
        )
    if "AccessMode" in found or "Forbidden" in found:
        return _bad(53, "그래프 사본은 읽기만 됩니다(쓰기 · 고치기는 SP 에서).")
    return _bad(57, f"Cypher 오류: {first} [{found}]")


def run(
    query: str, params: dict[str, Any] | None = None, *, limit: int | None = None
) -> Answer:
    """읽기 질의 하나. 줄은 `limit`(설정 상한 안)까지 — 넘으면 `truncated`."""
    text = check(query)
    seconds, cap = limits()
    wanted = max(1, min(limit or cap, cap))
    started = time.monotonic()
    copy = copy_info()
    notes: list[str] = []
    rows: list[list[Any]] = []
    columns: list[str] = []
    truncated = False
    try:
        with driver().session(
            database=DATABASE,
            default_access_mode=READ_ACCESS,
            fetch_size=min(wanted + 1, 1000),
        ) as session:
            explained = Query(f"EXPLAIN {text}", timeout=seconds)
            plan = session.run(explained, params or {}).consume()
            if plan.query_type != "r":
                raise _bad(
                    53,
                    "그래프 사본은 읽기만 됩니다 — 이 질의는 "
                    f"「{plan.query_type}」(쓰기 · 정의)입니다. 고치기는 SP 에서.",
                )
            for one in plan.summary_notifications or []:
                if str(one.severity_level) == "NotificationSeverity.WARNING":
                    notes.append(f"{one.title}: {one.description}"[:300])
            with session.begin_transaction(timeout=seconds) as tx:
                result = tx.run(text, params or {})
                columns = list(result.keys())
                for record in result:
                    if len(rows) >= wanted:
                        truncated = True
                        break
                    rows.append([_value(item) for item in record.values()])
                tx.rollback()
    except AppError:
        raise
    except neo4j_errors.ServiceUnavailable as caught:
        raise _unavailable(caught) from caught
    except neo4j_errors.Neo4jError as caught:
        raise _refused(caught) from caught
    except (neo4j_errors.DriverError, OSError) as caught:
        raise _unavailable(caught) from caught
    return Answer(
        columns=columns,
        rows=rows,
        truncated=truncated,
        seconds=round(time.monotonic() - started, 3),
        copy=copy,
        notes=notes,
    )


def build_indexes(labels: list[str], *, wait_seconds: int = 900) -> int:
    """밤에 새로 실은 사본에 색인을 세운다 — 타입 라벨마다 `id` · `key`, 그리고 `Obj(id)`.
    `neo4j-admin import` 는 색인을 안 만든다. **배포의 싣기(`graphcopy.sh`)만 부른다** — 쓰기
    거래다(질의 경로는 읽기만)."""
    made = 0
    with driver().session(database=DATABASE) as session:
        targets = [NODE_LABEL, *labels]
        for label in targets:
            for prop in ("id", "key") if label != NODE_LABEL else ("id",):
                name = f"sp_{label}_{prop}"
                session.run(
                    f"CREATE INDEX `{name}` IF NOT EXISTS FOR (n:`{label}`) ON (n.`{prop}`)"
                ).consume()
                made += 1
        session.run("CALL db.awaitIndexes($s)", {"s": wait_seconds}).consume()
    return made
