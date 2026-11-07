"""데이터 소스의 마지막 성공 — `data_sources.last_success_at`(받아서 적용까지 성공한 마지막 실행이 끝난 때).

`last_run_at` 은 실패해도 옮겨 가서 「자료가 언제까지 들어왔나」 에 답하지 못했다. 있던 소스는 지난
실행 기록에서 채운다 — 상태 ok · 적용된 실행 중 가장 늦게 끝난 것. 기록이 없는 소스(한 번도 성공
못 했거나 기록을 지웠다)는 비어서 시작한다. 실행 기록은 소스마다 색인(`source_id`)이 있어 즉시다.

Revision ID: 0097_datasource_last_success
Revises: 0096_semantic_pieces
Create Date: 2026-11-08 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0097_datasource_last_success"
down_revision: Union[str, Sequence[str], None] = "0096_semantic_pieces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE data_sources SET last_success_at = (
            SELECT max(finished_at) FROM data_source_runs
            WHERE source_id = data_sources.id AND status = 'ok' AND applied
        )
        """
    )


def downgrade() -> None:
    op.drop_column("data_sources", "last_success_at")
