"""타입의 앞머리(ADR 0088).

트리에만 있던 「이름 앞에 붙일 값(앞머리)」(`list_view.tree.prefix`, ADR 0086)을 타입의 칸
(`object_types.label_prefix`)으로 넓힌다 — 목록 · 상세 제목 · 참조 링크 · 관계 목록 · 검색 · 검토함이
같은 값을 이름 앞에 붙인다. 옛 자리에 적어 둔 값은 새 칸으로 옮기고 옛 자리에서 뺀다(둘에 두면
어느 쪽이 맞는지 갈린다). 타입 표는 작다.

Revision ID: 0106_type_label_prefix
Revises: 0105_bundle_origin
Create Date: 2026-11-19 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0106_type_label_prefix"
down_revision: Union[str, Sequence[str], None] = "0105_bundle_origin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "object_types",
        sa.Column("label_prefix", sa.String(length=80), server_default="", nullable=False),
    )
    op.execute(
        """
        UPDATE object_types
           SET label_prefix = COALESCE(list_view -> 'tree' ->> 'prefix', ''),
               list_view = jsonb_set(list_view, '{tree}', (list_view -> 'tree') - 'prefix')
         WHERE jsonb_typeof(list_view -> 'tree') = 'object'
           AND list_view -> 'tree' ? 'prefix'
        """
    )


def downgrade() -> None:
    # 트리가 있는 타입만 옛 자리로 돌린다 — 옛 판은 트리 밖의 앞머리를 모른다.
    op.execute(
        """
        UPDATE object_types
           SET list_view = jsonb_set(
                   list_view, '{tree}',
                   (list_view -> 'tree') || jsonb_build_object('prefix', label_prefix))
         WHERE label_prefix <> ''
           AND jsonb_typeof(list_view -> 'tree') = 'object'
        """
    )
    op.drop_column("object_types", "label_prefix")
