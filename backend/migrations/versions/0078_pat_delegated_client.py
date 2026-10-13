"""개인 토큰의 위임 표지(`personal_access_tokens.delegated_client`) — HWAX 게이트웨이 위임(ADR 0028).

포털의 MCP 게이트웨이가 사람마다 `POST /api/auth/sso` 로 그 사람 명의의 개인 토큰을 받아 간다(Report
Archive 와 같은 계약). 그렇게 만든 토큰에 client(`X-Heax-Client`)를 적어 둔다 — 다음 발급이 **같은
client 의 직전 토큰만** 회수하고, 그 토큰으로 든 요청을 감사가 게이트웨이 통로로 적는다. 사람이 만든
토큰은 NULL 그대로다(행을 채우지 않는다).

Revision ID: 0078_pat_delegated_client
Revises: 0077_portal_launch_jti
Create Date: 2026-10-13 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0078_pat_delegated_client"
down_revision: Union[str, Sequence[str], None] = "0077_portal_launch_jti"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL 을 허용하는 칸 하나 — PostgreSQL 은 표를 다시 쓰지 않는다(토큰 표는 작기도 하다).
    op.add_column(
        "personal_access_tokens",
        sa.Column("delegated_client", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    # 지우면 위임 토큰이 사람이 만든 토큰처럼 보인다 — 다음 발급이 그것을 회수하지 못해 하루
    # (토큰 수명) 동안 둘이 겹쳐 산다. 그 뒤로는 저절로 만료된다.
    op.drop_column("personal_access_tokens", "delegated_client")
