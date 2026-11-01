"""자주검증 회의 표(ADR 0044) — 확장 `jajuverify` 의 전용 표 둘.

`jv_meetings`(회의 한 장)와 `jv_rows`(표의 줄). 칸의 사진은 공통 첨부(`attachments`,
`owner_table='jv_rows'`)라 여기 없다. 확장을 안 켠 설치에서는 빈 표로 남는다 — 마이그레이션은
확장을 켰는지와 무관하게 늘 전부 돈다.

**운영에서 드는 것** — 빈 표 둘과 색인을 만들 뿐이라 즉시다(있는 표는 안 건드린다). FK 는 새 표
쪽의 제약이라 가리켜지는 표(부서 · 계정)를 훑지 않는다.

Revision ID: 0090_jajuverify_tables
Revises: 0089_relation_proposals
Create Date: 2026-10-30 23:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0090_jajuverify_tables"
down_revision: Union[str, Sequence[str], None] = "0089_relation_proposals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jv_meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("held_on", sa.Date(), nullable=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('review', 'postmortem', 'briefing')", name="ck_jv_meetings_kind"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jv_meetings_kind_held", "jv_meetings", ["kind", "held_on"], unique=False
    )
    op.create_index(
        op.f("ix_jv_meetings_workspace_id"), "jv_meetings", ["workspace_id"], unique=False
    )

    op.create_table(
        "jv_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("topic", sa.Text(), server_default="", nullable=False),
        sa.Column("problem", sa.Text(), server_default="", nullable=False),
        sa.Column("action", sa.Text(), server_default="", nullable=False),
        sa.Column("remark", sa.Text(), server_default="", nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["jv_meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jv_rows_meeting_position", "jv_rows", ["meeting_id", "position"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_jv_rows_meeting_position", table_name="jv_rows")
    op.drop_table("jv_rows")
    op.drop_index(op.f("ix_jv_meetings_workspace_id"), table_name="jv_meetings")
    op.drop_index("ix_jv_meetings_kind_held", table_name="jv_meetings")
    op.drop_table("jv_meetings")
