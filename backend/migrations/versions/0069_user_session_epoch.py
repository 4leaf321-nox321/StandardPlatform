"""계정 — 로그인의 세대(`users.session_epoch`).

비밀번호 변경 · 관리자 초기화 · 정지 · 삭제가 refresh 만 끊어, 이미 받은 access 토큰(12시간)이
그대로 통했다. 세션을 끊는 자리가 이 수를 올리고, access 토큰에 박힌 수와 다르면 거절한다.

Revision ID: 0069_user_session_epoch
Revises: 0068_metric_incremental
Create Date: 2026-10-08 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0069_user_session_epoch"
down_revision: Union[str, Sequence[str], None] = "0068_metric_incremental"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("session_epoch", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "session_epoch")
