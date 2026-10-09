"""되돌릴 기록을 **넣으면서 적는다.**

## 어떻게 켜나

`start(db, ...)` 가 세션에 판을 하나 열고, 그 세션으로 도는 동안 `created` · `changed` ·
`removed` 가 줄을 쌓는다. **판이 안 열려 있으면 전부 아무 일도 안 한다** — 그래서 부르는
자리(`bulk.py` · `tombstones.py`)는 묶음인지 아닌지 몰라도 된다. 화면에서 올린 파일 하나에
이 비용을 물리지 않으려고 이렇게 했다: 켜는 것은 묶음 쪽 한 곳이다.

## 왜 세션에 매다나

`audit.quiet` 와 같은 방식이다(`db.info`). 인자로 들고 다니면 `bulk.py` 의 함수 열댓 개에
매개변수가 하나씩 늘고, 그중 하나를 안 넘긴 자리가 조용히 안 적는다 — 그 사실은 되돌리려는
날에야 드러난다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Table, inspect
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.bundles.models import BundleRun, BundleUndoEntry
from app.shared import copyin

_KEY = "bundle_journal"


@dataclass
class _Open:
    run_id: uuid.UUID
    seq: int = 0
    """적은 줄 수 — **줄을 들고 있지 않는다.** 10만 줄 백필이면 그 목록만으로 참조가 10만
    개고, 세션이 이미 그것을 들고 있다(`db.add`)."""


def start(db: Session, user: User, *, source: str = "", label: str = "") -> uuid.UUID:
    """이 세션의 쓰기를 **한 판으로 묶는다.** 판 번호를 돌려준다."""
    run = BundleRun(
        id=uuid.uuid4(),
        actor_id=user.id,
        actor_label=(user.display_name or user.email)[:200],
        source=source[:100],
        label=label[:300],
        counts={},
    )
    db.add(run)
    db.info[_KEY] = _Open(run_id=run.id)
    return run.id


def open_run(db: Session) -> uuid.UUID | None:
    state = db.info.get(_KEY)
    return state.run_id if isinstance(state, _Open) else None


def finish(db: Session, *, counts: dict[str, Any], label: str = "") -> uuid.UUID | None:
    """판을 닫고 **무엇이 몇 건이었나**를 적는다. 적은 줄이 없으면 판도 남기지 않는다 —
    아무것도 안 바뀐 판이 목록에 쌓이면 되돌릴 판을 그 사이에서 찾게 된다."""
    state = db.info.pop(_KEY, None)
    if not isinstance(state, _Open):
        return None
    # **세션의 새것부터 찾는다** — 이 세션은 스스로 flush 하지 않아(`autoflush=False`),
    # 아무것도 안 쓴 판(무덤만 담아 바뀐 것이 없는 묶음)은 아직 DB 에 없다. `db.get` 은 그것을
    # 못 찾아 「없다」 고 돌아섰고, 판은 커밋에 실려 빈 채로 목록에 남았다(2026-10-08).
    run = next(
        (one for one in db.new if isinstance(one, BundleRun) and one.id == state.run_id),
        None,
    ) or db.get(BundleRun, state.run_id)
    if run is None:  # pragma: no cover - 방금 넣었다
        return None
    if not state.seq:
        if inspect(run).pending:
            db.expunge(run)
        else:
            db.delete(run)
        return None
    run.counts = dict(counts)
    if label:
        run.label = label[:300]
    return run.id


def drop(db: Session) -> None:
    """롤백하는 길 — 판을 세션에서 떼기만 한다(줄은 롤백이 가져간다)."""
    db.info.pop(_KEY, None)


def created(db: Session, table: str, target_id: uuid.UUID, *, label: str = "") -> None:
    """이 판이 **만들었다** — 되돌리면 지운다."""
    _add(db, table, target_id, "create", {}, {}, label)


def changed(
    db: Session,
    table: str,
    target_id: uuid.UUID,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    label: str = "",
) -> None:
    """이 판이 **고쳤다** — 되돌리면 `before` 로.

    `after` 는 그 사이 남이 고쳤는지 보는 근거다."""
    if not before and not after:
        return
    _add(db, table, target_id, "update", before, after, label)


def removed(
    db: Session, table: str, target_id: uuid.UUID, *, before: dict[str, Any], label: str = ""
) -> None:
    """이 판이 **지웠다** — 되돌리면 `before` 로 되살린다."""
    _add(db, table, target_id, "delete", before, {}, label)


def _add(
    db: Session,
    table: str,
    target_id: uuid.UUID,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
    label: str,
) -> None:
    row = _row(db, table, target_id, action, before, after, label)
    if row is not None:
        db.add(BundleUndoEntry(**row))


def _row(
    db: Session,
    table: str,
    target_id: uuid.UUID,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
    label: str,
) -> dict[str, Any] | None:
    """줄 하나의 칸 — 판이 안 열려 있으면 None. **차례(`seq`)는 여기서 매긴다**(부른 차례)."""
    state = db.info.get(_KEY)
    if not isinstance(state, _Open):
        return None
    state.seq += 1
    return {
        "id": uuid.uuid4(),
        "run_id": state.run_id,
        "seq": state.seq,
        "table_name": table,
        "target_id": target_id,
        "action": action,
        "before": before,
        "after": after,
        "label": label[:300],
    }


class Batch:
    """**만든 것**의 줄을 모았다가 한 문장(COPY)으로 넣는다 — 판이 안 열려 있으면 아무 일도
    안 한다(`created` 와 같다).

    묶음의 객체 단계는 새 줄마다 이 기록이 하나다. 줄마다 ORM 객체로 두면 flush 가 그것을
    1,000줄짜리 `INSERT … RETURNING` 으로 보내는데, 감사 기록과 같은 이유로 그것이 느렸다
    (`shared/copyin.py`). 차례(`seq`)는 **부를 때** 매기므로(`_row`) `changed` 처럼 바로
    넣는 줄과 섞여도 되돌리는 차례는 그대로다.

    ⚠️ `write` 전에는 표에 없다 — 부르는 쪽이 커밋 전에 부른다.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.rows: list[dict[str, Any]] = []

    def created(self, table: str, target_id: uuid.UUID, *, label: str = "") -> None:
        row = _row(self.db, table, target_id, "create", {}, {}, label)
        if row is not None:
            self.rows.append(row)

    def write(self) -> None:
        """모은 것을 넣는다 — 여러 번 불러도 된다(넣은 것은 비운다)."""
        if not self.rows:
            return
        # 판(`BundleRun`)이 먼저 들어가야 외래키가 선다 — 이 세션은 스스로 flush 하지 않고
        # (`autoflush=False`), COPY 는 세션을 거치지 않는다.
        self.db.flush()
        copyin.copy_rows(self.db, cast("Table", BundleUndoEntry.__table__), self.rows)
        self.rows.clear()
