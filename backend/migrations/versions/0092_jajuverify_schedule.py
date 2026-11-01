"""자주검증 — 개선대책과 일정을 나눈다(ADR 0044 「폭 · 사진 · 일정 · AI · 옮기기」, 2026-10-31).

`jv_rows.schedule` — 일정 단계 목록 `[{"date": "YYYY-MM-DD", "label": ""}]`. 지금까지의 완료
예정일(`due_on`)은 **일정 한 줄로 옮긴다**(이름은 비운다). `due_on` 은 지우지 않고 일정의 가장 늦은
날(파생값)로 남는다 — 기한 지남 · 정렬에 쓴다.

**운영에서 드는 것** — 상수 기본값 칸 하나(즉시)와, 예정일이 있는 줄만 고치는 UPDATE 하나다.
1단계 표라 줄이 적다.

내리면 일정 칸을 지운다 — 단계의 이름은 사라지고, 가장 늦은 날은 `due_on` 에 남아 있다.

Revision ID: 0092_jajuverify_schedule
Revises: 0091_jajuverify_shared_editing
Create Date: 2026-10-31 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0092_jajuverify_schedule"
down_revision: Union[str, Sequence[str], None] = "0091_jajuverify_shared_editing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jv_rows",
        sa.Column(
            "schedule",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE jv_rows
           SET schedule = jsonb_build_array(
                   jsonb_build_object('date', to_char(due_on, 'YYYY-MM-DD'), 'label', '')
               )
         WHERE due_on IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("jv_rows", "schedule")
