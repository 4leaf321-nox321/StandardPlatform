"""타입의 자료 범위 표지(ADR 0083).

타입 정의에 `scope`(「국내」 · 「해외」 · 「plm」 같은 설치의 말)를 둔다. 목록 · 통계 · 지표 · 분석의
응답이 관련 타입들의 값을 모아 `scope` 로 싣는다(여럿이면 `mixed`). 비어 있으면 미지정 —
지금까지의 타입은 모두 비어 있다.

Revision ID: 0103_type_scope
Revises: 0102_request_timing_tool
Create Date: 2026-11-17 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0103_type_scope"
down_revision: Union[str, Sequence[str], None] = "0102_request_timing_tool"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 타입 정의 표는 작다(수백 줄) — 기본값이 있는 칸이어도 바로 끝난다.
    op.add_column(
        "object_types",
        sa.Column("scope", sa.String(length=40), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("object_types", "scope")
