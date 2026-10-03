"""객체의 첨부는 **객체의 부서를 따른다** — DB 트리거가 맞춘다(ADR 0012).

첨부의 보기 권한은 첨부 행의 `workspace_id` 로 판정한다(files 는 객체 표를 모른다). 그런데
객체의 소유 부서가 바뀌는 길이 여럿이다 — 일괄 고치기 · 묶음 되돌리기 · 이력 되돌리기 · 부서
통폐합. 코드로 맞추면 한 길이 빠지고, 빠진 길에서는 **객체는 보이는데 사진이 404** 가 된다
(새 부서 사람은 사진을 못 보고, 옛 부서 사람은 id 만 알면 받는다). 그래서 참조 색인(ADR 0010)
처럼 표가 맞춘다.

`UPDATE OF owner_workspace_id` 라 그 칸을 SET 하는 문장에서만 돈다 — 값만 고치는 덩어리
갱신(종류 변경 200만 건)에는 걸리지 않는다.

시험은 모델로 표를 세우므로 메타데이터 이벤트로 걸고, 운영은 마이그레이션
`0055_attachment_images` 가 **그날의 사본**으로 건다. 여기를 고치면 새 마이그레이션도 쓴다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, event

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

#: 이미 어긋난 것을 맞춘다 — 마이그레이션이 한 번 돈다.
BACKFILL = """
UPDATE attachments AS a SET workspace_id = o.owner_workspace_id
  FROM objects AS o
 WHERE a.owner_table = 'objects' AND a.owner_id = o.id
   AND a.workspace_id IS DISTINCT FROM o.owner_workspace_id
"""


def attach(metadata: MetaData) -> None:
    """`create_all` 뒤에 함수 · 트리거를 건다(시험 · 모델로 세우는 길)."""

    def install(_target: Any, connection: Any, **_kw: Any) -> None:
        for sql in (FUNCTION, TRIGGER):
            connection.exec_driver_sql(sql)

    event.listen(metadata, "after_create", install)
