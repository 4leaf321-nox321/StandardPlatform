"""합친 객체 참조에 색인 — 객체를 지울 때마다 객체 표를 통째로 훑던 것.

`objects.merged_into_id` 는 같은 표를 가리키는 외래키다(ON DELETE SET NULL). 객체 하나를 지울
때마다 Postgres 는 「이것에 합쳐졌던 행」 을 찾아 NULL 로 바꾸는데, 그 열에 색인이 없으면 **표
전체를 훑는다.** 객체 255만 행에서 임시 타입 33만 건을 영구 삭제하는 질의가 3분이 넘도록 끝나지
않았다(규모 리허설, 2026-10-03 — 한 건에 표 한 번이라 수십 시간 걸릴 일). 큰 타입을 지우거나
되돌리기로 많은 객체를 지우는 날 같은 일이 난다.

합친 객체는 드물어 값이 있는 행만 담는 **부분 색인**이면 된다 — 외래키 검사의 `= $1` 은
`IS NOT NULL` 을 품으므로 그것을 탄다.

Revision ID: 0059_objects_merged_into_index
Revises: 0058_datasource_source_name
Create Date: 2026-10-03 21:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0059_objects_merged_into_index"
down_revision: Union[str, Sequence[str], None] = "0058_datasource_source_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_objects_merged_into",
        "objects",
        ["merged_into_id"],
        postgresql_where=sa.text("merged_into_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_objects_merged_into", table_name="objects")
