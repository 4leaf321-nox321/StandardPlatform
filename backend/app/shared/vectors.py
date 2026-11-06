"""pgvector — 뜻으로 찾기의 바탕(ADR 0050).

임베딩(글의 뜻을 숫자 1,024개로 적은 것)을 DB 에 담고 가까운 것을 찾는 확장이다.

**확장은 `extensions` 스키마에 산다.** 앱의 표(public)와 섞이지 않고, 시험이 public 을 통째로
지웠다 세워도 남는다. 그래서 앱은 형식 · 연산자를 늘 스키마를 붙여 적는다
(`extensions.vector(1024)`, `OPERATOR(extensions.<=>)`) — search_path 에 기대면, 그 설정이
따라오지 않는 복구한 DB 에서 「type vector does not exist」 로 멈춘다.

**확장은 superuser 만 만든다** — pg_trgm · btree_gist 와 달리 「신뢰된 확장」 이 아니다.
운영은 `deploy.sh`, 개발 PC 는 `scripts/dev_pgvector.sh` 가 postgres 계정으로 만든다. 그것을
쓰는 표(`object_embeddings`, ADR 0052)가 생긴 뒤로는 없으면 마이그레이션이 할 일을 말하며
멈추고(배포는 그 앞에서 멈춘다 — `require_vector`), 시험의 표 세우기도 그렇다(superuser 인 CI
는 스스로 만든다).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, cast, event, text
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import UserDefinedType

SCHEMA = "extensions"
#: bge-m3 의 차원 — 뜻의 자리를 담는 칸이 이 크기로 박힌다.
DIM = 1024
#: HNSW 색인(가까운 것을 빨리 찾는 그래프)이 0.5 부터다. 우분투 24.04 의 패키지는 0.6.0.
MIN_VERSION = (0, 5, 0)

MISSING = (
    "pgvector 확장이 없습니다(ADR 0050) — 확장은 postgres 계정만 만듭니다. "
    "운영: sudo ./deploy.sh pgvector · 개발 PC: sudo ./scripts/dev_pgvector.sh"
)


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def installed(db: Session | Connection) -> tuple[str, str] | None:
    """(판, 스키마) — 없으면 None."""
    row = db.execute(
        text(
            "SELECT extversion, extnamespace::regnamespace::text FROM pg_extension "
            "WHERE extname = 'vector'"
        )
    ).first()
    return (row[0], row[1]) if row else None


def problem(found: tuple[str, str] | None) -> str | None:
    """쓸 수 없는 까닭 — 쓸 수 있으면 None."""
    if found is None:
        return MISSING
    version, schema = found
    if schema != SCHEMA:
        return (
            f"pgvector 확장이 {schema} 스키마에 있습니다 — {SCHEMA} 로 옮겨야 합니다 "
            "(sudo ./deploy.sh pgvector 가 옮긴다)"
        )
    if _parse(version) < MIN_VERSION:
        return (
            f"pgvector {version} 은 HNSW 색인이 없습니다 — 0.5 이상이 필요합니다 "
            "(패키지를 올린 뒤 sudo ./deploy.sh pgvector)"
        )
    return None


def ensure(connection: Connection) -> None:
    """있으면 그대로, 없으면 superuser 일 때만 만든다 — 아니면 할 일을 말하며 멈춘다."""
    found = installed(connection)
    superuser = text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    if found is None and connection.execute(superuser).scalar():
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {SCHEMA} TO PUBLIC")
        connection.exec_driver_sql(f"CREATE EXTENSION vector SCHEMA {SCHEMA}")
        found = installed(connection)
    why = problem(found)
    if why:
        raise RuntimeError(why)


class Vector(UserDefinedType[list[float]]):
    """뜻의 자리를 담는 칸 — `extensions.vector(1024)`.

    **파이썬 pgvector 패키지를 쓰지 않는다.** 폐쇄망 번들에 의존성이 하나 늘고, 하는 일은 글
    `[0.1,0.2,…]` 로 바꾸고 읽는 것뿐이다. 넣을 때는 그 글을 `CAST(… AS extensions.vector)` 로
    감싼다 — 드라이버가 글로 보내도 DB 가 형식을 안다.
    """

    cache_ok = True

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def get_col_spec(self, **_kw: Any) -> str:
        return f"{SCHEMA}.vector({self.dim})"

    def bind_processor(self, dialect: Dialect) -> Any:
        def write(value: list[float] | None) -> str | None:
            if value is None:
                return None
            return "[" + ",".join(repr(float(one)) for one in value) + "]"

        return write

    def bind_expression(self, bindvalue: Any) -> ColumnElement[Any]:
        return cast(bindvalue, self)

    def result_processor(self, dialect: Dialect, coltype: object) -> Any:
        def read(value: str | None) -> list[float] | None:
            if value is None:
                return None
            body = value.strip()[1:-1]
            return [float(one) for one in body.split(",")] if body else []

        return read


def attach(metadata: MetaData) -> None:
    """`create_all` 앞 — 시험은 스키마를 통째로 지우고 모델로 다시 세운다."""

    def need(_target: Any, connection: Connection, **_kw: Any) -> None:
        ensure(connection)

    event.listen(metadata, "before_create", need)
