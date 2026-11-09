"""DB 세션과 선언적 기반.

제약 이름 규약을 여기서 고정한다. 이름이 자동으로 정해지면 Alembic 이
autogenerate 에서 제약을 지웠다 만드는 diff 를 내고, 마이그레이션이 매번 흔들린다.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,  # 사내망에서 유휴 커넥션이 끊겨도 조용히 재연결
    future=True,
    # **질의를 서버에 준비해 두지 않는다**(psycopg 기본은 같은 글 다섯 번째부터 준비한다).
    # 준비된 질의가 몇 번 더 쓰이면 Postgres 가 값을 보지 않는 일반 계획으로 바꿀 수 있는데,
    # 글자로 찾기(`ILIKE '%…%'` · 조각 색인)는 그 계획이 재앙이다 — 일반 사용자의 「과제 30」
    # 섞어 찾기가 0.03초에서 48초 · 한 번은 7분이 됐다(2,000만 건 실측 DB, 관리자의 같은 질의
    # 일곱 번 뒤). 끄면 질의마다 값을 보고 계획을 짠다 — 처음 다섯 번이 늘 하던 일이다
    # (ADR 0066).
    connect_args={"prepare_threshold": None},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
