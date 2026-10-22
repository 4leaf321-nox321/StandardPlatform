"""부서의 「공개 범위」(`workspaces.restricted`)를 지운다 — 조회는 부서와 상관없이 누구나(ADR 0034).

이 칸은 화면에서 켜고 끌 수 있었지만 그것을 부르는 조회가 하나도 없어 아무 데도 안 걸렸다.
조회를 전원에게 열면서 가를 것이 없어졌다. 되돌리면 칸만 다시 생긴다(값은 false — 지울 때
들어 있던 값은 남지 않는다. 걸린 곳이 없었으니 잃는 동작도 없다).

Revision ID: 0082_drop_workspace_restricted
Revises: 0081_board
Create Date: 2026-10-22 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0082_drop_workspace_restricted"
down_revision: Union[str, Sequence[str], None] = "0081_board"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("workspaces", "restricted")


def downgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("restricted", sa.Boolean(), server_default="false", nullable=False),
    )
