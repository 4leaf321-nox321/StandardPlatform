"""끊긴 질문(`cut_questions`) — 답을 못 받고 끊긴 물음과 그 모양(ADR 0031).

시간 상한(504)에 걸린 목록 · 통계와 SPARQL 의 거절(너무 큼 · 빠진 기록 타입)을 모양과 함께 남긴다.
무엇을 지표로 미리 세어 둘지 · 트리플 스토어가 정말 필요한지를 실제 물음으로 가르려고. 모양만 —
조건의 값과 검색어는 남기지 않는다.

Revision ID: 0079_cut_questions
Revises: 0078_pat_delegated_client
Create Date: 2026-10-16 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0079_cut_questions"
down_revision: Union[str, Sequence[str], None] = "0078_pat_delegated_client"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cut_questions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("route", sa.String(length=300), nullable=False),
        sa.Column(
            "shape",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("request_id", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cut_questions")),
    )
    op.create_index(
        "ix_cut_questions_created_at", "cut_questions", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_cut_questions_created_at", table_name="cut_questions")
    op.drop_table("cut_questions")
