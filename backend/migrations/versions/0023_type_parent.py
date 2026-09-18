"""타입의 상위 타입 — 「개발모델은 제품이다」 를 정의에 적는 자리.

플랫폼은 속성 그래프라 스스로 추론하지 않지만, RDF/OWL 로 내보낼 때 rdfs:subClassOf 가
되어 추론기가 상속을 푼다. 화면에서는 아직 표시만 한다.

Revision ID: 0023_type_parent
Revises: 0022_ref_inverse_label
Create Date: 2026-09-18 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0023_type_parent"
down_revision: Union[str, Sequence[str], None] = "0022_ref_inverse_label"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "object_types", sa.Column("parent_slug", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("object_types", "parent_slug")
