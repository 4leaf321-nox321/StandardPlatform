"""타입별 객체 수의 계수표와 그래프 선 굵기의 찍어 둔 값(ADR 0038).

스키마 · 타입 목록 · 자기소개가 읽을 때마다 객체 표 전체를 셌다(운영 24초 ~ 1분), 그래프 첫
화면은 관계 · 참조 색인 · 링크 전부를 셌다(운영 4분). 객체 수는 트리거가 적는 증감 줄
(`object_type_counts`)로, 선 굵기는 작업이 찍어 두는 한 줄(`graph_edge_snapshots`)로 바꾼다.

**처음 한 번 객체 표 전체를 센다**(지금 있는 것) — 운영에서 수십 초.

Revision ID: 0085_type_counts
Revises: 0084_slide_refs
Create Date: 2026-10-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0085_type_counts"
down_revision: Union[str, Sequence[str], None] = "0084_slide_refs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 그날의 사본 — 앱 코드를 import 하지 않는다(`objects/typecounts.py` 와 같은 SQL).
FUNCTIONS = """
CREATE OR REPLACE FUNCTION object_type_counts_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO object_type_counts (type_id, delta)
    SELECT type_id, count(*) FROM counted_new WHERE deleted_at IS NULL GROUP BY type_id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_type_counts_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO object_type_counts (type_id, delta)
    SELECT type_id, -count(*) FROM counted_old WHERE deleted_at IS NULL GROUP BY type_id;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_type_counts_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.deleted_at IS NULL THEN
        INSERT INTO object_type_counts (type_id, delta) VALUES (OLD.type_id, -1);
    END IF;
    IF NEW.deleted_at IS NULL THEN
        INSERT INTO object_type_counts (type_id, delta) VALUES (NEW.type_id, 1);
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION object_type_counts_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM object_type_counts;
    RETURN NULL;
END $$;
"""

TRIGGERS = """
DROP TRIGGER IF EXISTS object_type_counts_insert ON objects;
CREATE TRIGGER object_type_counts_insert AFTER INSERT ON objects
    REFERENCING NEW TABLE AS counted_new
    FOR EACH STATEMENT EXECUTE FUNCTION object_type_counts_insert();

DROP TRIGGER IF EXISTS object_type_counts_delete ON objects;
CREATE TRIGGER object_type_counts_delete AFTER DELETE ON objects
    REFERENCING OLD TABLE AS counted_old
    FOR EACH STATEMENT EXECUTE FUNCTION object_type_counts_delete();

DROP TRIGGER IF EXISTS object_type_counts_update ON objects;
CREATE TRIGGER object_type_counts_update AFTER UPDATE OF type_id, deleted_at ON objects
    FOR EACH ROW WHEN (
        OLD.type_id IS DISTINCT FROM NEW.type_id
        OR (OLD.deleted_at IS NULL) IS DISTINCT FROM (NEW.deleted_at IS NULL)
    )
    EXECUTE FUNCTION object_type_counts_update();

DROP TRIGGER IF EXISTS object_type_counts_truncate ON objects;
CREATE TRIGGER object_type_counts_truncate AFTER TRUNCATE ON objects
    FOR EACH STATEMENT EXECUTE FUNCTION object_type_counts_truncate();
"""

BACKFILL = """
DELETE FROM object_type_counts;
INSERT INTO object_type_counts (type_id, delta)
SELECT type_id, count(*) FROM objects WHERE deleted_at IS NULL GROUP BY type_id;
"""


def upgrade() -> None:
    op.create_table(
        "object_type_counts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type_id", sa.UUID(), nullable=False),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "graph_edge_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("relations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("links", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # 트리거를 걸고 같은 트랜잭션에서 센다 — 앱이 내려가 있을 때 돈다(배포가 마이그레이션 뒤에
    # 띄운다), 그 사이 들어온 객체가 빠지지 않는다.
    op.execute(FUNCTIONS)
    op.execute(TRIGGERS)
    op.execute(BACKFILL)


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS object_type_counts_insert ON objects;
        DROP TRIGGER IF EXISTS object_type_counts_delete ON objects;
        DROP TRIGGER IF EXISTS object_type_counts_update ON objects;
        DROP TRIGGER IF EXISTS object_type_counts_truncate ON objects;
        DROP FUNCTION IF EXISTS object_type_counts_insert();
        DROP FUNCTION IF EXISTS object_type_counts_delete();
        DROP FUNCTION IF EXISTS object_type_counts_update();
        DROP FUNCTION IF EXISTS object_type_counts_truncate();
        """
    )
    op.drop_table("graph_edge_snapshots")
    op.drop_table("object_type_counts")
