"""Alembic 환경 — 접속 정보와 메타데이터를 앱 설정에서 가져온다.

alembic.ini 에 DSN 을 적지 않는다. 적어 두면 운영 서버에서 .env 와 ini 두 곳이
서로 다른 DB 를 가리키는 사고가 난다.
"""

from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import Base

# 모든 모델이 Base.metadata 에 등록되도록 한 곳에서 모아 import 한다.
# 빠뜨리면 autogenerate 가 **기존 표를 지우는** 마이그레이션을 만든다.
import app.all_models  # noqa: F401  isort:skip

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: 앱이 실행 중에 세우고 지우는 색인 — 모델에 없으니 자동 생성이 지우자고 들지 않게 거른다
#: (지표의 날짜 칸 색인, `app/modules/metrics/timeindex.py`).
_RUNTIME_INDEXES = ("ix_objects_mt_",)


#: **마이그레이션이 잠금을 기다리는 상한** — 넘으면 그 마이그레이션은 실패한다(한 트랜잭션이라
#: DB 는 그대로). 상한이 없던 때는 03:00 백업의 `pg_dump`(표마다 AccessShareLock 을 덤프가 끝날
#: 때까지 쥔다) · 긴 작업 · 이중화의 상대 서버가 쥔 표에서 `ALTER TABLE` 이 끝없이 기다렸고,
#: 그동안 `deploy.sh update` 는 서비스를 내린 채 멈춰 있었다(2026-11-28 점검). 실패하면
#: update 가 직전 판으로 되돌리고 다시 켠다 — 쥔 것이 끝난 뒤 다시 치면 된다. 바꾸려면
#: `MIGRATION_LOCK_TIMEOUT`(PostgreSQL 의 시간 표기 — `60s` · `5min`, `0` 은 상한 없음).
LOCK_TIMEOUT_DEFAULT = "60s"


def lock_timeout() -> str:
    """잠금 대기 상한 — 환경 변수가 있으면 그것(모양이 틀리면 기본값)."""
    given = os.environ.get("MIGRATION_LOCK_TIMEOUT", "").strip()
    if re.fullmatch(r"\d+\s*(ms|s|min|h)?", given):
        return given.replace(" ", "")
    return LOCK_TIMEOUT_DEFAULT


def _options() -> str:
    """접속 `options` — 주소(`DATABASE_URL` 의 `?options=`)에 이미 준 것 뒤에 잠금 상한을
    붙인다(덮으면 그 설정이 조용히 사라진다)."""
    given = make_url(config.get_main_option("sqlalchemy.url") or "").query.get("options")
    before = " ".join(given) if isinstance(given, tuple) else (given or "")
    return f"{before} -c lock_timeout={lock_timeout()}".strip()


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    return not (type_ == "index" and reflected and name and name.startswith(_RUNTIME_INDEXES))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # 접속할 때 건다(libpq `options`) — 세션 내내 간다. `SET` 문으로 걸면 alembic 이 열
        # 트랜잭션 앞에서 따로 커밋해야 한다.
        connect_args={"options": _options()},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
