"""게시판(`board_posts` · `board_comments`) — 설치 전체가 함께 쓰는 글 목록(ADR 0033).

Revision ID: 0081_board
Revises: 0080_metric_insights
Create Date: 2026-10-16 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0081_board"
down_revision: Union[str, Sequence[str], None] = "0080_metric_insights"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "board_posts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_board_posts_created_at"), "board_posts", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_board_posts_deleted_at"), "board_posts", ["deleted_at"], unique=False
    )
    op.create_table(
        "board_comments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("post_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["post_id"], ["board_posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_board_comments_post_id"), "board_comments", ["post_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_board_comments_post_id"), table_name="board_comments")
    op.drop_table("board_comments")
    op.drop_index(op.f("ix_board_posts_deleted_at"), table_name="board_posts")
    op.drop_index(op.f("ix_board_posts_created_at"), table_name="board_posts")
    op.drop_table("board_posts")
