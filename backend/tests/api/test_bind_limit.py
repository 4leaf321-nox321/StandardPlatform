"""바인드 한도(65,535)를 넘는 목록도 **나눠 묻는다**(ADR 0010).

`IN (...)` 은 원소마다 바인드 변수 하나다. 10만 행 일괄 입력이나 10만 건이 가리키는 인기 객체를
한 번에 넘기면 질의가 보내지지도 않고 실패했다(실측: 「number of parameters must be between 0
and 65535」) — 화면에는 「서버 오류」 로만 떴다.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import aliases, refedges, watches
from app.modules.ontology.models import ObjectType
from app.shared.batches import chunks
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_type

MANY = 70_000


def test_나누면_빈_목록은_안_내고_끝까지_다_낸다() -> None:
    assert list(chunks([])) == []
    parts = list(chunks(range(25_001), size=10_000))
    assert [len(one) for one in parts] == [10_000, 10_000, 5_001]
    assert [item for one in parts for item in one] == list(range(25_001))


def test_칠만_개의_id_로도_질의가_나간다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    slug = _make_type(client, admin, label="부품")
    kind = db.scalars(select(ObjectType).where(ObjectType.slug == slug)).one()
    ids = [uuid.uuid4() for _ in range(MANY)]
    user = db.scalars(select(User).where(User.email == admin.email)).one()

    assert aliases.of(db, ids) == {}
    assert refedges._visible_ids(db, user, set(ids)) == set()
    assert aliases.taken_by(db, kind, aliases.HUMAN, [str(one) for one in ids]) == {}
    # 커밋 뒤 지켜보기 알림 — 10만 행 일괄 입력이 한 `IN` 으로 묻다가 통째로 빠졌다.
    assert watches.watchers(db, ids) == {}
