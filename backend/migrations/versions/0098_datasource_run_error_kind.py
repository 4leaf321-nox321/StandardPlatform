"""실행 기록의 실패 종류 — `data_source_runs.error_kind`(connect · auth · source, 그 밖은 비움).

받기가 연결 오류 · 잠깐의 거절(429 · 502 · 503 · 504)로 실패하면 정기 실행 간격(보통 하루)을 다
기다리지 않고 15 → 30 → 60분 뒤 다시 넣는다 — 그 판정이 「최근 실행이 연달아 연결 실패였나」 를
읽는다. 인증 거절 · 주소 오류는 다시 해도 같아 사람 몫이다. 있던 기록은 비운 채 둔다(글로만 남아
있어 가를 수 없다 — 다음 실패부터 적힌다).

Revision ID: 0098_datasource_run_error_kind
Revises: 0097_datasource_last_success
Create Date: 2026-11-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0098_datasource_run_error_kind"
down_revision: Union[str, Sequence[str], None] = "0097_datasource_last_success"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "data_source_runs", sa.Column("error_kind", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("data_source_runs", "error_kind")
