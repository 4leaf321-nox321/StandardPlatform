"""감사 기록의 차례

`created_at` 은 트랜잭션 시작 시각이라 한 트랜잭션의 기록 여럿이 같은 값을 갖는다.
합치기·승격처럼 객체 여럿을 한 번에 고치면 그 안의 순서가 사라지고, 이력 재구성이
그 순서를 거꾸로 밟다가 틀린 시점 값을 만든다. 넣은 차례를 따로 둔다.

Revision ID: 0011_audit_seq
Revises: 0010_saved_views
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0011_audit_seq"
down_revision = "0010_saved_views"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 있던 행은 만들어진 순서(created_at, id)대로 번호를 받는다 — 같은 시각이면 id 순.
    op.add_column(
        "audit_entries",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.execute(
        """
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS n FROM audit_entries
        )
        UPDATE audit_entries a SET seq = numbered.n FROM numbered WHERE a.id = numbered.id
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('audit_entries', 'seq'),
            COALESCE((SELECT MAX(seq) FROM audit_entries), 0) + 1,
            false
        )
        """
    )
    op.create_unique_constraint(op.f("uq_audit_entries_seq"), "audit_entries", ["seq"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("uq_audit_entries_seq"), "audit_entries", type_="unique")
    op.drop_column("audit_entries", "seq")
