"""관계 — 이 선을 이은 데이터 소스(`object_relations.datasource_id` · `datasource_seen_at`).

코어 소스가 「처음부터 다시」 받을 때 상대에서 끊긴 선을 이쪽에서도 끊는다. 끊어도 되는 선(그
소스가 이은 것)을 가르고, 여러 차례에 걸친 다시 받기에서 「이번에 봤다」 를 적는 자리다 —
감사 기록의 표식과 `updated_at` 으로 하던 것을 칸으로.

Revision ID: 0071_relation_datasource
Revises: 0070_search_misses
Create Date: 2026-10-08 23:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0071_relation_datasource"
down_revision: Union[str, Sequence[str], None] = "0070_search_misses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("object_relations", sa.Column("datasource_id", sa.UUID(), nullable=True))
    op.add_column(
        "object_relations",
        sa.Column("datasource_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_object_relations_datasource_id_data_sources"),
        "object_relations",
        "data_sources",
        ["datasource_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # 빈 칸뿐이라 표를 한 번 훑고 끝난다(부분 색인 — 든 것이 없다).
    op.create_index(
        "ix_object_relations_datasource",
        "object_relations",
        ["datasource_id"],
        unique=False,
        postgresql_where=sa.text("datasource_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_object_relations_datasource",
        table_name="object_relations",
        postgresql_where=sa.text("datasource_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("fk_object_relations_datasource_id_data_sources"),
        "object_relations",
        type_="foreignkey",
    )
    op.drop_column("object_relations", "datasource_seen_at")
    op.drop_column("object_relations", "datasource_id")
