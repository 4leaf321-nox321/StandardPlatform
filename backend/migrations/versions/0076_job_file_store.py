"""큰 작업 파일은 공용 저장소에 — `job_files.path`, `data` 는 비어도 된다.

사진 zip 의 상한을 2GB 로 올리면서(`job_photos_max_bytes`) 큰 작업 파일은 DB 가 아니라 공용
저장소의 작업 폴더(`FILESTORE_DIR/_jobs/<id>`)에 두고 행에는 경로만 둔다 — bytea 한 값은 1GB
까지이고, 그만한 값이 WAL · 복제로 흐르며 읽으려면 통째로 메모리에 올려야 한다
(`jobs/files.py`). 내용은 한 곳에만 있다(`ck_job_files_one_place`) — 지금 있는 행은 모두
`data` 에 있으므로 그대로 맞는다.

Revision ID: 0076_job_file_store
Revises: 0075_refs_trigger
Create Date: 2026-10-10 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0076_job_file_store"
down_revision: Union[str, Sequence[str], None] = "0075_refs_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_files", sa.Column("path", sa.String(length=255), nullable=True))
    op.alter_column("job_files", "data", existing_type=sa.LargeBinary(), nullable=True)
    op.create_check_constraint(
        op.f("ck_job_files_one_place"), "job_files", "(data IS NULL) <> (path IS NULL)"
    )


def downgrade() -> None:
    # 저장소에 놓인 큰 파일의 행은 옛 모양(`data` 필수)에 못 담는다 — 지운다. 파일은 남고, 새
    # 판의 정리(`sweep_orphans`)나 사람이 `_jobs/` 를 지운다. 그 작업은 「파일이 지워졌습니다」 로
    # 다시 올리라고 말한다(작업 행의 참조는 SET NULL).
    op.drop_constraint(op.f("ck_job_files_one_place"), "job_files", type_="check")
    op.execute("DELETE FROM job_files WHERE data IS NULL")
    op.alter_column("job_files", "data", existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column("job_files", "path")
