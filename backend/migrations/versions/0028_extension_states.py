"""확장 모듈의 켜짐 — extension_states.

`.env` 의 `EXTENSIONS` 를 기본값으로 남기고, **화면에서 바꾼 것만** 이 표에 남는다.
행이 없으면 예전과 똑같이 `.env` 가 답하므로, 이 마이그레이션은 도는 순간 아무것도
바꾸지 않는다.

시드를 넣지 않는 이유: 4 워커 · A · B 두 대에서 같은 시드를 동시에 넣게 되고, 그보다
「행이 없으면 기본값」 이 읽기만으로 끝난다.

Revision ID: 0028_extension_states
Revises: 0027_webhook_core
Create Date: 2026-09-24 15:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0028_extension_states"
down_revision: Union[str, Sequence[str], None] = "0027_webhook_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "extension_states",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("extension_states")
