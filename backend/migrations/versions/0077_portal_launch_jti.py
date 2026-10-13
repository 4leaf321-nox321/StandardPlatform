"""HWAX 포털 launch 토큰의 쓴 jti(`portal_launch_jtis`) — 한 토큰은 한 번만 로그인시킨다(ADR 0028).

포털이 보증한 신원으로 로그인하는 길(`POST /api/auth/portal-callback`)이 생기면서, 90초 사는 launch
토큰이 그 안에서 두 번 쓰이지 않게 jti 를 적어 둔다. 메모리가 아니라 DB 인 까닭은 이중화 두 서버 ·
워커 넷이 같은 토큰을 받을 수 있어서다. 원문 대신 sha256 을 두고, exp 가 지난 줄은 다음 콜백이 지운다.

Revision ID: 0077_portal_launch_jti
Revises: 0076_job_file_store
Create Date: 2026-10-13 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0077_portal_launch_jti"
down_revision: Union[str, Sequence[str], None] = "0076_job_file_store"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portal_launch_jtis",
        sa.Column("jti_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("jti_hash", name=op.f("pk_portal_launch_jtis")),
    )
    op.create_index(
        op.f("ix_portal_launch_jtis_expires_at"),
        "portal_launch_jtis",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    # 지워도 잃는 것이 없다 — 줄은 길어야 100초 산다(토큰의 exp + 여유).
    op.drop_index(op.f("ix_portal_launch_jtis_expires_at"), table_name="portal_launch_jtis")
    op.drop_table("portal_launch_jtis")
