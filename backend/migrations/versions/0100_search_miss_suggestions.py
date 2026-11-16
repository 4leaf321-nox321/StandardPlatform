"""별칭 후보의 「이것 아닐까」 를 미리 셈해 둔다(ADR 0074).

줄마다 비슷한 객체를 찾는 질의가 축 객체 · 별칭을 다 훑어(운영 모양에서 한 줄 0.5초) 검토함 ·
별칭 후보 창이 그 자리에서 셀 수 없었다 — 대기가 몇만 건이면 검토함이 504 였다(ADR 0073). 워커가
셈해 이 세 칸에 적는다. 비어 있으면(아직 안 셈함) 화면이 지금처럼 시간 안에서 센다.

Revision ID: 0100_search_miss_suggestions
Revises: 0099_graph_calc
Create Date: 2026-11-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0100_search_miss_suggestions"
down_revision: Union[str, Sequence[str], None] = "0099_graph_calc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "search_misses",
        sa.Column("suggested", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "search_misses",
        sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "search_misses",
        sa.Column("suggest_note", sa.String(length=300), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("search_misses", "suggest_note")
    op.drop_column("search_misses", "suggested_at")
    op.drop_column("search_misses", "suggested")
