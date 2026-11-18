"""묶음 판의 원천(ADR 0087).

묶음이 어디서 왔나 — 원천 파이프라인의 실행(`{system, pipeline, run, url, trace}`)을 묶음 판에 둔다.
그 판이 넣은 객체의 이력이 이것을 보이며 원천의 추적으로 링크한다. 판 표는 작다(30일 보관) — 기본값이
있는 칸이어도 바로 끝난다.

Revision ID: 0105_bundle_origin
Revises: 0104_core_subscriptions
Create Date: 2026-11-18 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0105_bundle_origin"
down_revision: Union[str, Sequence[str], None] = "0104_core_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bundle_runs",
        sa.Column(
            "origin",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("bundle_runs", "origin")
