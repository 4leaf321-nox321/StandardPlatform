"""별칭에 **출처 · 메모 · 검수** 칸 — 어디서 온 이름이고, 사람이 봤나.

적재가 수천 개를 붙이면 「이건 어디서 온 이름이냐」 를 물을 자리가 없었다. 그때 사람은
지워도 되는지 판단할 수 없어서 **아무것도 안 지운다** — 오타 표기까지 정본처럼 쓰인다.

    source        어디서 온 이름인가(「고장모드 리스트 v3」). `kind` 와 다르다 —
                  `kind` 는 그 이름이 무엇인지(사람 별칭 · 외부 식별자)를 말한다
    note          사람이 남기는 한 줄(「오타 표기」)
    verified_by   · verified_at   **사람이 확인했나.** 비면 검수 대기

기존 행은 비운다 — 「예전에 붙은 것은 전부 확인했다」 로 두면 검수 목록이 처음부터
비어서, 이 칸을 둔 뜻이 없어진다.

Revision ID: 0038_alias_review
Revises: 0037_relation_tombstones
Create Date: 2026-09-29 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038_alias_review"
down_revision: Union[str, Sequence[str], None] = "0037_relation_tombstones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "object_aliases",
        sa.Column("source", sa.String(length=80), server_default="", nullable=False),
    )
    op.add_column(
        "object_aliases",
        sa.Column("note", sa.String(length=200), server_default="", nullable=False),
    )
    op.add_column(
        "object_aliases", sa.Column("verified_by_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "object_aliases",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_object_aliases_verified_by",
        "object_aliases",
        "users",
        ["verified_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_object_aliases_verified", "object_aliases", ["type_id", "verified_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_object_aliases_verified", table_name="object_aliases")
    op.drop_constraint("fk_object_aliases_verified_by", "object_aliases", type_="foreignkey")
    for name in ("verified_at", "verified_by_id", "note", "source"):
        op.drop_column("object_aliases", name)
