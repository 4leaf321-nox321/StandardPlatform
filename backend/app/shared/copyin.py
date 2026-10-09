"""행 여럿을 **COPY 한 문장으로** 넣는다 — 일괄 입력의 쓰기 길.

ORM 의 flush 는 새 행을 1,000줄짜리 `INSERT … VALUES (…), (…) RETURNING …` 으로 묶어 보낸다.
그 문장은 4KB 를 훌쩍 넘어 psycopg 가 캐시하지 못하고, **덩어리마다 문장 전체를 다시
쪼갠다**(`_query2pg_nocache`) — 5만 줄 적용에서 그것만 수 초였다. 한 줄씩 보내는
executemany 는 그 비용이 없지만, `objects` 의 참조 색인 트리거가 **문장마다** 돌아(문장 단위
트리거, `refindex.py`) 오히려 더 느렸다. 객체 5만 줄을 넣는 데(2026-10-09, 개발 DB 실측):

    ORM flush        15.7초      감사 5만 줄  8.2초
    executemany      20.9초                   2.6초
    COPY              9.6초                   0.9초

COPY 는 ORM 을 거치지 않는다 — 그래서 부르는 쪽이 알아야 할 것:

- 파이썬 쪽 기본값(`default=uuid.uuid4` 같은)이 안 붙는다. 넘기지 않은 칸은 **DB 기본값**
  (server_default · identity)만 받는다 — DB 기본값 없이 파이썬 기본값만 있는 칸을 빼면
  거절한다(조용히 NULL 이 들어가면 ORM 으로 넣은 줄과 모양이 갈린다).
- JSON 칸의 `None` 은 ORM 과 같게 JSON `null` 이다(`none_as_null` 을 켠 칸만 SQL NULL).
- 세션의 identity map 에 안 든다. 행은 **곧바로** DB 에 들어가므로 같은 트랜잭션의 질의는 본다.
- ORM 이벤트는 안 돈다. 표의 트리거는 돈다(COPY 도 INSERT 트리거를 부른다 — 문장 단위
  트리거는 COPY 한 번에 한 번).
- 세션에 아직 안 쓴(flush 안 한) 행을 가리키는 외래키는 서지 않는다 — 부르는 쪽이 먼저
  flush 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import JSON, Table
from sqlalchemy.orm import Session


def copy_rows(db: Session, table: Table, rows: Sequence[Mapping[str, Any]]) -> int:
    """`rows`(칸 이름 → 값, **모두 같은 칸**)를 `table` 에 COPY 로 넣는다. 넣은 줄 수.

    세션의 트랜잭션(세이브포인트 포함) 안에서 돈다 — 롤백하면 함께 사라진다."""
    if not rows:
        return 0
    names = list(rows[0])
    _require_defaults(table, names)
    as_json = [_json_null(table, name) for name in names]
    driver = cast("psycopg.Connection[Any]", db.connection().connection.driver_connection)
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(*([table.schema] if table.schema else []), table.name),
        sql.SQL(", ").join(sql.Identifier(table.c[name].name) for name in names),
    )
    with driver.cursor() as cursor, cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(
                [
                    row[name]
                    if json_null is None or (row[name] is None and json_null)
                    else Jsonb(row[name])
                    for name, json_null in zip(names, as_json, strict=True)
                ]
            )
    return len(rows)


def _json_null(table: Table, name: str) -> bool | None:
    """JSON 칸이면 「`None` 을 SQL NULL 로 넣나」(`none_as_null`), JSON 칸이 아니면 None."""
    kind = table.c[name].type
    return bool(kind.none_as_null) if isinstance(kind, JSON) else None


def _require_defaults(table: Table, names: list[str]) -> None:
    """빠진 칸이 **DB 기본값으로 ORM 과 같아지나** — 파이썬 기본값만 있는 칸이 빠지면 거절."""
    given = set(names)
    unknown = sorted(given - set(table.c.keys()))
    if unknown:
        raise ValueError(f"{table.name} 에 없는 칸입니다: {', '.join(unknown)}")
    for column in table.c:
        if column.key in given:
            continue
        if column.server_default is not None or column.identity is not None:
            continue
        if column.default is None and column.nullable:
            continue
        raise ValueError(
            f"{table.name}.{column.key} 는 DB 기본값이 없습니다 — 값을 넘겨야 합니다"
        )
