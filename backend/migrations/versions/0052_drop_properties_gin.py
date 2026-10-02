"""`objects.properties` 통째 GIN 인덱스를 뺀다 — 쓰는 질의는 없고 쓰기마다 값을 치른다(ADR 0010).

거르기는 칸 하나씩(`properties->>'k'`, `properties->'k' @> …`)이라 통째 인덱스를 못 탄다. 「값
있음」(`?`)은 늘 타입으로 먼저 좁힌다. 실측 사용은 개발 DB 6회 · 실험 DB 7회였다. 반면 속성을
고칠 때마다 행의 모든 칸이 다시 들어가, 기록 200만 건의 종류 변경에서 덩어리 하나(5천 행)
2.93초 중 2.5초가 이 인덱스였다. 크기도 1.3GB 로 다른 인덱스를 다 합친 것만 했다.

Revision ID: 0052_drop_properties_gin
Revises: 0051_trigram_search
Create Date: 2026-10-03 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0052_drop_properties_gin"
down_revision: Union[str, Sequence[str], None] = "0051_trigram_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_objects_properties", table_name="objects", postgresql_using="gin")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        "ix_objects_properties",
        "objects",
        ["properties"],
        unique=False,
        postgresql_using="gin",
    )
