"""객체 병합 — 지는 쪽이 이긴 쪽을 가리킨다

**같은 것이 둘이 되면 둘 다 못 믿게 된다.** 합칠 길이 없으면 사람은 한쪽을 지우고,
그것을 가리키던 참조는 빈 칸이 된다. 합친 뒤 옛 주소가 새 것으로 가려면 지는 쪽이
이긴 쪽을 알아야 한다.

Revision ID: 0009_object_merged_into
Revises: 0008_ontology_snapshots
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_object_merged_into"
down_revision = "0008_ontology_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("objects", sa.Column("merged_into_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_objects_merged_into_id_objects"),
        "objects",
        "objects",
        ["merged_into_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("fk_objects_merged_into_id_objects"), "objects", type_="foreignkey"
    )
    op.drop_column("objects", "merged_into_id")
