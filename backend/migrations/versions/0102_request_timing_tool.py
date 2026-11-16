"""요청 시간 기록에 MCP 도구 칸(ADR 0078).

MCP 가 백엔드를 부를 때 도구 이름과 부름 id 를 머리에 싣는다(`X-SP-Tool` · `X-SP-Tool-Call`). 요청
시간 기록이 그것과 함께 0건이었나 · 실패한 응답의 오류 코드를 남겨, 관리 › 서버 › 성능이 「AI 도구마다」
부른 수 · 0건 · 거절을 센다. 칸은 모두 비어 있을 수 있다 — 지금까지의 줄 · 사람의 요청.

Revision ID: 0102_request_timing_tool
Revises: 0101_review_auto_rules
Create Date: 2026-11-17 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0102_request_timing_tool"
down_revision: Union[str, Sequence[str], None] = "0101_review_auto_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 칸을 더하기만 한다 — 기본값이 없는 nullable 칸이라 표를 다시 쓰지 않는다(큰 표다).
    op.add_column("request_timings", sa.Column("tool", sa.String(length=64), nullable=True))
    op.add_column(
        "request_timings", sa.Column("tool_call", sa.String(length=24), nullable=True)
    )
    op.add_column("request_timings", sa.Column("empty", sa.Boolean(), nullable=True))
    op.add_column(
        "request_timings", sa.Column("error_code", sa.String(length=40), nullable=True)
    )
    # 부분 색인 — 표는 한 번 훑지만 지금까지의 줄은 모두 비어 있어 든 것이 없다.
    op.create_index(
        "ix_request_timings_tool",
        "request_timings",
        ["tool", "created_at"],
        unique=False,
        postgresql_where=sa.text("tool IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_timings_tool",
        table_name="request_timings",
        postgresql_where=sa.text("tool IS NOT NULL"),
    )
    op.drop_column("request_timings", "error_code")
    op.drop_column("request_timings", "empty")
    op.drop_column("request_timings", "tool_call")
    op.drop_column("request_timings", "tool")
