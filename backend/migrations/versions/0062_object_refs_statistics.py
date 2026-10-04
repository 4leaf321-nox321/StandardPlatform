"""참조 색인의 두 칸을 함께 어림하게 — 확장 통계(ADR 0017).

들어오는 참조 걸음(「기본 모델 ← 개발모델 ← 서비스 기록」)은 참조 색인을 `(src_type_id, key)` 와
`(dst_id, key)` 로 거꾸로 걷는다. 그런데 플래너는 두 칸을 **따로 어림해 곱한다** — 「타입 = 개발모델,
칸 = base」 를 11줄로 어림했고 실제는 6,000줄이었다. 그 어림으로 200만 건을 한 줄씩 찾아가는 계획을
골라, 두 걸음 거꾸로 거르기를 세는 데 28초였다. 두 칸이 묶여 있다는 것(어느 타입의 어느 칸)을 알려
주면 6.2초다(기록 200만 건 실측).

모델로 세우는 시험 DB 에는 없다 — 자료가 작아 어림이 틀려도 계획이 같다.

Revision ID: 0062_object_refs_statistics
Revises: 0061_date_function
Create Date: 2026-10-04 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0062_object_refs_statistics"
down_revision: Union[str, Sequence[str], None] = "0061_date_function"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "CREATE STATISTICS IF NOT EXISTS st_object_refs_type_key (dependencies, ndistinct) "
        "ON src_type_id, key FROM object_refs"
    )
    op.execute(
        "CREATE STATISTICS IF NOT EXISTS st_object_refs_dst_key (dependencies, ndistinct) "
        "ON dst_id, key FROM object_refs"
    )
    # 통계 객체는 다음 ANALYZE 때 찬다 — 기다리지 않고 지금(200만 건 참조 색인에서 1초 안).
    op.execute("ANALYZE object_refs")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP STATISTICS IF EXISTS st_object_refs_dst_key")
    op.execute("DROP STATISTICS IF EXISTS st_object_refs_type_key")
