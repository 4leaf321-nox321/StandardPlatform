"""관계에 `updated_at` 과 **무덤** — 바뀐 선과 끊긴 선을 밖에 알린다.

선은 지금까지 `created_at` 만 있었고, 행을 정말 지웠다. 그래서 둘을 몰랐다.

    바뀐 선   근거·속성을 고쳐도 코어 API 의 「지난번 이후」 에 안 걸렸다
    끊긴 선   받는 쪽(쌍둥이 · 외부 시스템)은 **끊긴 것을 영영 몰랐다**

`updated_at` 은 기존 행을 `created_at` 으로 채운다 — 그것이 마지막으로 바뀐 시각에 가장
가깝고, 받는 쪽이 한 번 더 받는 것은 해가 없다(같은 값을 덮어쓴다).

무덤의 `removed_at` 은 **`clock_timestamp()`** 다. `now()` 는 트랜잭션이 시작한 시각이라
한 요청에서 선을 여럿 끊으면 전부 같은 시각이 되고, 그러면 쪽 넘김의 순서가 질의마다
달라진다(0032 · 0036 과 같은 이유).

Revision ID: 0037_relation_tombstones
Revises: 0036_snapshot_clock_timestamp
Create Date: 2026-09-29 21:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037_relation_tombstones"
down_revision: Union[str, Sequence[str], None] = "0036_snapshot_clock_timestamp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "object_relations",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute("UPDATE object_relations SET updated_at = created_at")
    op.create_index(
        "ix_object_relations_updated", "object_relations", ["relation", "updated_at"]
    )

    op.create_table(
        "object_relation_tombstones",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("src_object_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("dst_object_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column(
            "removed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    # 이름은 **모델이 만드는 이름과 같아야 한다** — 다르면 「모델과 마이그레이션이 같나」
    # 점검이 매번 새 작업을 찾아낸다(`index=True` 는 `ix_<표>_<칸>` 으로 짓는다).
    op.create_index(
        "ix_object_relation_tombstones_src_object_id",
        "object_relation_tombstones",
        ["src_object_id"],
    )
    op.create_index(
        "ix_object_relation_tombstones_relation", "object_relation_tombstones", ["relation"]
    )
    op.create_index(
        "ix_relation_tombstones_removed", "object_relation_tombstones", ["removed_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("object_relation_tombstones")
    op.drop_index("ix_object_relations_updated", table_name="object_relations")
    op.drop_column("object_relations", "updated_at")
