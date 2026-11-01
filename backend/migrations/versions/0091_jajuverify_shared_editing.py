"""자주검증 — 여럿이 나눠 쓰는 표(ADR 0044 「여럿이 나눠 쓰기」, 2026-10-31 사용자 결정).

- `jv_groups` — 묶음(표 중간의 머리줄). `jv_rows.group_id` 가 가리킨다(비면 묶음 없음).
- 판 번호 `revision` — 회의 · 묶음 · 줄. 회의의 쓰기마다 회의의 것이 하나 오르고 바뀐 줄 · 묶음에
  그 값이 적힌다. 화면의 자동 반영이 이 번호만 묻는다(시각을 커서로 쓰지 않는다 — ADR 0024).
- 칸마다 마지막으로 고친 사람 · 때 `edits` — 덮어쓰기 충돌이 「누가 언제」 를 칸 단위로 말한다.
- 작성 완료(`jv_rows.done` · `done_by_id` · `done_at`) · 줄의 마지막 수정자(`updated_by_id`).
- 회의 확정(`jv_meetings.confirmed_at` · `confirmed_by_id`).
- `jv_presence` — 지금 수정하는 사람(회의마다 사람마다 한 줄).

**운영에서 드는 것** — 새 표 둘과 칸 더하기다. 더하는 칸은 모두 NULL 이거나 상수 기본값이라
(PostgreSQL 11+) 표를 다시 쓰지 않고 즉시다. 1단계 표(`jv_rows`)는 아직 작다.

Revision ID: 0091_jajuverify_shared_editing
Revises: 0090_jajuverify_tables
Create Date: 2026-10-31 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0091_jajuverify_shared_editing"
down_revision: Union[str, Sequence[str], None] = "0090_jajuverify_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _revision() -> sa.Column:
    return sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False)


def _edits() -> sa.Column:
    return sa.Column(
        "edits", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "jv_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("title", sa.String(length=200), server_default="", nullable=False),
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
        _revision(),
        _edits(),
        sa.ForeignKeyConstraint(["meeting_id"], ["jv_meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jv_groups_meeting_position", "jv_groups", ["meeting_id", "position"], unique=False
    )

    op.add_column("jv_meetings", _revision())
    op.add_column("jv_meetings", _edits())
    op.add_column(
        "jv_meetings", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "jv_meetings",
        sa.Column("confirmed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jv_meetings_confirmed_by_id_users",
        "jv_meetings",
        "users",
        ["confirmed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "jv_rows", sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_jv_rows_group_id_jv_groups",
        "jv_rows",
        "jv_groups",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "jv_rows", sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_jv_rows_updated_by_id_users",
        "jv_rows",
        "users",
        ["updated_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "jv_rows", sa.Column("done", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "jv_rows", sa.Column("done_by_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_jv_rows_done_by_id_users",
        "jv_rows",
        "users",
        ["done_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("jv_rows", sa.Column("done_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jv_rows", _revision())
    op.add_column("jv_rows", _edits())

    op.create_table(
        "jv_presence",
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field", sa.String(length=16), nullable=True),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["jv_meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["row_id"], ["jv_rows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["jv_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id", "user_id"),
    )
    op.create_index("ix_jv_presence_seen_at", "jv_presence", ["seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jv_presence_seen_at", table_name="jv_presence")
    op.drop_table("jv_presence")

    op.drop_column("jv_rows", "edits")
    op.drop_column("jv_rows", "revision")
    op.drop_column("jv_rows", "done_at")
    op.drop_constraint("fk_jv_rows_done_by_id_users", "jv_rows", type_="foreignkey")
    op.drop_column("jv_rows", "done_by_id")
    op.drop_column("jv_rows", "done")
    op.drop_constraint("fk_jv_rows_updated_by_id_users", "jv_rows", type_="foreignkey")
    op.drop_column("jv_rows", "updated_by_id")
    op.drop_constraint("fk_jv_rows_group_id_jv_groups", "jv_rows", type_="foreignkey")
    op.drop_column("jv_rows", "group_id")

    op.drop_constraint(
        "fk_jv_meetings_confirmed_by_id_users", "jv_meetings", type_="foreignkey"
    )
    op.drop_column("jv_meetings", "confirmed_by_id")
    op.drop_column("jv_meetings", "confirmed_at")
    op.drop_column("jv_meetings", "edits")
    op.drop_column("jv_meetings", "revision")

    op.drop_index("ix_jv_groups_meeting_position", table_name="jv_groups")
    op.drop_table("jv_groups")
