"""객체를 **식별자 · 비교키 · 이름**으로 찾는 인덱스(ADR 0010).

기록 200만 건 실측(`scripts/scale_rehearsal.py`): `objects.key` 에 인덱스가 없어 식별자 하나를
찾을 때마다(일괄 입력 · 중복 확인 · 이름 풀이) 타입을 통째로 읽었고, 이름 풀이는 식별자가
글자 그대로 안 맞으면 키 있는 행을 전부 파이썬으로 읽어 견줬다 — MCP `object_resolve` 107초.

- `ix_objects_key_type` — 식별자 그대로.
- `ix_objects_type_key_norm` — 비교키(`compare_key`: NFKC · 공백 정리 · 소문자)와 같은 식.
  질의가 **같은 식**을 써야 탄다(`objects.resolve`).
- `ix_objects_type_label_lower` — 이름이 그대로 같은 것(대소문자 무시).

200만 건이면 세 인덱스를 세우는 데 1분 남짓 걸린다.

Revision ID: 0050_object_lookup_indexes
Revises: 0049_object_refs
Create Date: 2026-10-03 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0050_object_lookup_indexes"
down_revision: Union[str, Sequence[str], None] = "0049_object_refs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NORMALIZED_KEY = "lower(normalize(regexp_replace(btrim(key), '\\s+', ' ', 'g'), NFKC))"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_objects_key_type", "objects", ["key", "type_id"], unique=False)
    op.create_index(
        "ix_objects_type_key_norm",
        "objects",
        ["type_id", sa.text(NORMALIZED_KEY)],
        unique=False,
        postgresql_where=sa.text("key IS NOT NULL"),
    )
    op.create_index(
        "ix_objects_type_label_lower",
        "objects",
        ["type_id", sa.text("lower(label)")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_objects_type_label_lower", table_name="objects")
    op.drop_index("ix_objects_type_key_norm", table_name="objects")
    op.drop_index("ix_objects_key_type", table_name="objects")
