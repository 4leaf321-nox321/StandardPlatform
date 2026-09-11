"""감사 기록의 양 끝 id 인덱스

객체의 변경 이력이 관계 기록을 `changes->>'src' / 'dst'` 로 찾는다. 표현식 인덱스가
없으면 그 조회가 감사 표 전체를 훑고, 표는 매일 자란다.

Revision ID: 0013_audit_endpoint_index
Revises: 0012_system_links
Create Date: 2026-09-12 08:14:06.761282

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0013_audit_endpoint_index"
down_revision: Union[str, Sequence[str], None] = "0012_system_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_audit_entries_changes_dst",
        "audit_entries",
        [sa.literal_column("(changes ->> 'dst')")],
        unique=False,
    )
    op.create_index(
        "ix_audit_entries_changes_src",
        "audit_entries",
        [sa.literal_column("(changes ->> 'src')")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_entries_changes_src", table_name="audit_entries")
    op.drop_index("ix_audit_entries_changes_dst", table_name="audit_entries")
