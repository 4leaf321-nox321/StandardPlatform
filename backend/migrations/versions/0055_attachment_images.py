"""첨부의 이미지 판별 · 미리보기, 그리고 파일 칸의 「받는 종류」(ADR 0012).

`media` 는 서버가 열어 보고 정한 것이다 — NULL 은 이 칸이 생기기 전에 올라온 첨부라 아직 안
봤다는 뜻이고, 처음 목록에 나올 때 본다(파일을 읽어야 해서 마이그레이션이 하지 않는다).

객체의 첨부는 객체의 부서를 따른다 — 트리거가 맞춘다(`objects/attachment_sync.py` 의 그날
사본). 이미 어긋난 것도 한 번 맞춘다.

Revision ID: 0055_attachment_images
Revises: 0054_bigram_search
Create Date: 2026-10-03 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0055_attachment_images"
down_revision: Union[str, Sequence[str], None] = "0054_bigram_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 그날의 사본 — 앱 코드를 import 하지 않는다(`objects/attachment_sync.py` 와 같은 SQL).
FUNCTION = """
CREATE OR REPLACE FUNCTION attachments_follow_object() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE attachments SET workspace_id = NEW.owner_workspace_id
     WHERE owner_table = 'objects' AND owner_id = NEW.id
       AND workspace_id IS DISTINCT FROM NEW.owner_workspace_id;
    RETURN NULL;
END $$;
"""

TRIGGER = """
DROP TRIGGER IF EXISTS attachments_follow_object ON objects;
CREATE TRIGGER attachments_follow_object AFTER UPDATE OF owner_workspace_id ON objects
    FOR EACH ROW WHEN (OLD.owner_workspace_id IS DISTINCT FROM NEW.owner_workspace_id)
    EXECUTE FUNCTION attachments_follow_object();
"""

BACKFILL = """
UPDATE attachments AS a SET workspace_id = o.owner_workspace_id
  FROM objects AS o
 WHERE a.owner_table = 'objects' AND a.owner_id = o.id
   AND a.workspace_id IS DISTINCT FROM o.owner_workspace_id
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("attachments", sa.Column("media", sa.String(length=8), nullable=True))
    op.add_column("attachments", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("attachments", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("attachments", sa.Column("thumb_path", sa.String(length=300), nullable=True))
    op.add_column("property_defs", sa.Column("accept", sa.String(length=16), nullable=True))
    op.execute(FUNCTION)
    op.execute(TRIGGER)
    op.execute(BACKFILL)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS attachments_follow_object ON objects")
    op.execute("DROP FUNCTION IF EXISTS attachments_follow_object()")
    op.drop_column("property_defs", "accept")
    op.drop_column("attachments", "thumb_path")
    op.drop_column("attachments", "height")
    op.drop_column("attachments", "width")
    op.drop_column("attachments", "media")
