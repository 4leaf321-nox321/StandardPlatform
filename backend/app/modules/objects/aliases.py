"""별칭 — **같은 것을 다르게 불러도 같은 것으로 풀린다.**

사람이 붙인 다른 이름(`alias`)과 바깥 시스템의 식별자(`source:<slug>`)를 한 표에 둔다.
찾기·참조 풀이·파일·동기화가 전부 여기를 본다 — 한 곳만 보면 「파일로는 풀리는데 화면
찾기에는 안 걸리는」 상태가 되고, 그때 어느 쪽이 맞는지 알 방법이 없다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import InvalidValue
from app.shared import audit
from app.shared.errors import AppError, code
from app.shared.permissions import require_owner_edit, visible_owner_clause
from app.shared.text import compare_key

HUMAN = "alias"

MAX_VALUE = 200
"""별칭 하나의 글자 수 — `object_aliases.value` 칸의 크기다."""


@dataclass(frozen=True)
class Incoming:
    """붙일 별칭 하나 — **어디서 왔고 무슨 메모가 붙나.**

    글자만 받으면 수천 개가 붙은 뒤에 「이건 어디서 온 이름이냐」 를 물을 자리가 없다.
    그때 사람은 지워도 되는지 판단할 수 없어서 아무것도 안 지운다.
    """

    value: str
    source: str = ""
    note: str = ""
    verified: bool = False
    """**보낸 쪽에서 사람이 이미 확인한 것**인가(허브 → 쌍둥이). 참이면 받는 쪽에서도
    검수 대기로 두지 않는다 — 안 그러면 허브에서 본 것이 쌍둥이에는 영영 남는다."""


def incoming(values: Sequence[str | Incoming]) -> list[Incoming]:
    """글자와 `Incoming` 이 섞여 와도 하나로 — 부르는 쪽이 둘을 가르지 않게."""
    return [one if isinstance(one, Incoming) else Incoming(value=str(one)) for one in values]


def source_kind(slug: str) -> str:
    return f"source:{slug}"


def clean(values: list[str]) -> list[str]:
    """빈 것·겹치는 것(비교키 기준)을 뺀, 사람이 적은 차례 그대로."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = (raw or "").strip()
        if not text:
            continue
        norm = compare_key(text)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(text)
    return out


def split_long(values: list[str]) -> tuple[list[str], list[str]]:
    """쓸 수 있는 것과 **너무 긴 것**으로 가른다 — `(ok, long)`.

    ⚠️ 예전에는 여기서 `value[:200]` 으로 **조용히 잘랐다.** 잘린 것은 다른 이름이라 원래
       이름으로는 검색이 안 되고, 앞 200자가 같은 둘은 서로 충돌한다. 넣은 사람은 둘 다
       모른다 — 그래서 자르지 않고, 뺀 것을 말한다.
    """
    ok = [one for one in values if len(one) <= MAX_VALUE]
    long = [one for one in values if len(one) > MAX_VALUE]
    return ok, long


def of(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ObjectAlias]]:
    """객체별 별칭 전부 — 목록 한 쪽을 한 질의로."""
    out: dict[uuid.UUID, list[ObjectAlias]] = defaultdict(list)
    if not ids:
        return out
    for row in db.scalars(
        select(ObjectAlias)
        .where(ObjectAlias.object_id.in_(ids))
        # **id 까지 보고 세운다.** 한 번에 붙인 별칭은 `created_at` 이 같아(트랜잭션 시작
        # 시각) 차례가 질의마다 달라졌다 — 그러면 허브와 쌍둥이를 견주는 자리에서 **매번
        # 「별칭이 바뀌었다」** 가 되어, 바뀐 것이 없는데도 다시 쓴다(실측).
        .order_by(ObjectAlias.created_at, ObjectAlias.id)
    ):
        out[row.object_id].append(row)
    return out


def human_of(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    return {
        object_id: [one.value for one in rows if one.kind == HUMAN]
        for object_id, rows in of(db, ids).items()
    }


def taken_by(
    db: Session, object_type: ObjectType, kind: str, values: list[str]
) -> dict[str, uuid.UUID]:
    """이 값들을 **이미 쓰는 다른 객체** — {비교키: 객체 id}."""
    norms = [compare_key(one) for one in values]
    if not norms:
        return {}
    rows = db.execute(
        select(ObjectAlias.norm, ObjectAlias.object_id).where(
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.kind == kind,
            ObjectAlias.norm.in_(norms),
        )
    )
    return {norm: object_id for norm, object_id in rows}


def split_free(
    db: Session,
    object_type: ObjectType,
    values: list[str],
    *,
    exclude_id: uuid.UUID | None,
    kind: str = HUMAN,
) -> tuple[list[str], list[str]]:
    """쓸 수 있는 것과 **남이 쓰는 것**으로 가른다 — `(free, taken)`.

    가져오기가 쓴다. 별칭 하나가 겹친다고 파일 전체를 물리면(전부 아니면 무) **한 줄 때문에
    수백 줄이 안 들어간다** — 실측으로 고장 모드 적재가 그렇게 통째로 막혔다. 별칭은 이름을
    거드는 값이지 정체성이 아니므로, 겹치는 것만 빼고 넣되 **어느 것이 빠졌는지 계획에
    적는다.** 사람이 손으로 붙일 때는 여전히 거절한다(`require_free`) — 그 자리에서는 고칠
    사람이 화면 앞에 있다.
    """
    taken_map = taken_by(db, object_type, kind, values)
    free: list[str] = []
    taken: list[str] = []
    for value in values:
        holder = taken_map.get(compare_key(value))
        if holder is not None and holder != exclude_id:
            taken.append(value)
        else:
            free.append(value)
    return free, taken


def require_free(
    db: Session,
    object_type: ObjectType,
    values: list[str],
    *,
    exclude_id: uuid.UUID | None,
    kind: str = HUMAN,
) -> None:
    """다른 객체가 쓰는 별칭이면 거절한다 — **어느 객체인지 말하며.**"""
    taken = taken_by(db, object_type, kind, values)
    for value in values:
        holder = taken.get(compare_key(value))
        if holder is not None and holder != exclude_id:
            other = db.get(ObjectInstance, holder)
            raise InvalidValue(
                code("OBJECTS", 80),
                f"「{value}」 은 이미 {other.label if other else '다른 객체'}의 별칭입니다. "
                "같은 별칭이 둘이면 어느 쪽인지 아무도 모릅니다.",
            )


def set_human(
    db: Session,
    row: ObjectInstance,
    object_type: ObjectType,
    values: Sequence[str | Incoming],
    *,
    mode: str = "replace",
    verified: User | None = None,
) -> tuple[list[str], list[str]]:
    """사람이 붙인 별칭. **부르는 쪽이 커밋하고 감사 기록을 남긴다.** (전, 후) 를 돌려준다.

    `mode="add"` 는 **있는 것을 지우지 않고 더한다.** 파일로 다시 적재할 때 이것이 아니면,
    사람이 화면에서 붙여 둔 별칭이 조용히 사라진다 — 그 사실은 아무 데도 안 적히고, 몇 달 뒤
    「그 이름으로 검색이 안 된다」 로 만난다(실측). `replace` 는 파일을 정본으로 보는 자리
    (허브 → 쌍둥이)에서 쓴다 — 허브에서 뺀 별칭이 받는 쪽에 남으면 둘이 갈린다.
    """
    asked = incoming(values)
    meta = {compare_key(one.value): one for one in asked}
    wanted, long = split_long(clean([one.value for one in asked]))
    if long:
        # 손으로 넣는 자리다 — 사람이 바로 고칠 수 있으니 자르지 말고 거절한다.
        raise InvalidValue(
            code("OBJECTS", 89),
            f"별칭이 {MAX_VALUE}자를 넘습니다: 「{long[0][:40]}…」 "
            f"({len(long[0])}자). 잘라서 넣으면 원래 이름으로는 검색이 안 됩니다.",
        )
    require_free(db, object_type, wanted, exclude_id=row.id)
    current = [
        one
        for one in db.scalars(
            select(ObjectAlias).where(
                ObjectAlias.object_id == row.id, ObjectAlias.kind == HUMAN
            )
        )
    ]
    before = [one.value for one in current]
    keep = {compare_key(one) for one in wanted}
    if mode == "replace":
        for one in current:
            if one.norm not in keep:
                db.delete(one)
    else:
        before_norms = {one.norm for one in current}
        wanted = [*before, *(one for one in wanted if compare_key(one) not in before_norms)]
    have = {one.norm for one in current}
    now = datetime.now(UTC)
    if [one.value for one in current] != wanted:
        _touch(row)
    for value in wanted:
        norm = compare_key(value)
        if norm in have:
            # 이미 있는 것에 **출처 · 메모만 채운다** — 비어 있을 때만(사람이 적은 것을
            # 기계가 덮어쓰지 않게).
            found = next((one for one in current if one.norm == norm), None)
            asked_one = meta.get(norm)
            if found is not None and asked_one is not None:
                if asked_one.source and not found.source:
                    found.source = asked_one.source[:80]
                if asked_one.note and not found.note:
                    found.note = asked_one.note[:200]
                if (asked_one.verified or verified is not None) and found.verified_at is None:
                    # **보낸 쪽에서 이미 본 것**은 여기서 다시 묻지 않는다 — 안 그러면
                    # 허브에서 확인한 줄이 쌍둥이에는 영영 검수 대기로 남는다.
                    found.verified_by_id = verified.id if verified is not None else None
                    found.verified_at = now
            continue
        asked_one = meta.get(norm) or Incoming(value=value)
        db.add(
            ObjectAlias(
                object_id=row.id,
                type_id=object_type.id,
                kind=HUMAN,
                value=value,
                norm=norm,
                source=asked_one.source[:80],
                note=asked_one.note[:200],
                # **사람이 화면에서 붙인 것은 곧 확인한 것이다.** 기계가 붙인 것만 검수
                # 대기로 남는다 — 그것을 가르지 않으면 목록이 곧 수천 줄이 되어 안 읽힌다.
                # 보낸 쪽에서 이미 확인한 것(`verified`)도 여기서 다시 묻지 않는다.
                verified_by_id=verified.id if verified is not None else None,
                verified_at=now if verified is not None or asked_one.verified else None,
            )
        )
    db.flush()
    return before, wanted


def add_fresh(
    db: Session,
    row: ObjectInstance,
    object_type: ObjectType,
    values: Sequence[str | Incoming],
    *,
    verified: User | None = None,
) -> list[str]:
    """**방금 만든 객체**에 별칭을 붙인다 — 있던 것을 묻지 않는다(없다).

    ⚠️ 부르는 쪽이 겹침을 이미 가린 자리에서만 쓴다(일괄 가져오기의 `split_free`).
       `set_human` 은 줄마다 세 번 묻는다(있던 것 · 겹침 · 넣기) — 2만 줄이면 그것이
       6만 번이고, 실측으로 적용 시간의 대부분이 거기였다.
    """
    asked = incoming(values)
    wanted, long = split_long(clean([one.value for one in asked]))
    if long:  # pragma: no cover - 부르는 쪽이 이미 걸렀다
        raise InvalidValue(
            code("OBJECTS", 89), f"별칭이 {MAX_VALUE}자를 넘습니다: 「{long[0][:40]}…」"
        )
    meta = {compare_key(one.value): one for one in asked}
    now = datetime.now(UTC)
    if wanted:
        _touch(row)
    for value in wanted:
        norm = compare_key(value)
        one = meta.get(norm) or Incoming(value=value)
        db.add(
            ObjectAlias(
                object_id=row.id,
                type_id=object_type.id,
                kind=HUMAN,
                value=value,
                norm=norm,
                source=one.source[:80],
                note=one.note[:200],
                verified_by_id=verified.id if verified is not None else None,
                verified_at=now if verified is not None or one.verified else None,
            )
        )
    return wanted


def _touch(row: ObjectInstance) -> None:
    """객체의 **바뀐 때**를 지금으로 — 별칭만 바뀌어도.

    ⚠️ 별칭은 다른 표에 있어서, 그것만 바꾸면 객체 행은 손대지 않는다. 그러면
       `objects.updated_at` 이 그대로고, **코어 API 의 「지난번 이후」 가 그 객체를 안 준다**
       — 받는 쪽은 새 이름을 영영 모른다(실측). 시계는 DB 것을 쓴다(`now()`).
    """
    row.updated_at = func.now()


def set_external(
    db: Session, row: ObjectInstance, object_type: ObjectType, slug: str, value: str
) -> None:
    """데이터 소스의 외부 식별자 — 소스마다 하나. 있으면 값을 갈아 끼운다."""
    kind = source_kind(slug)
    found = db.scalar(
        select(ObjectAlias).where(ObjectAlias.object_id == row.id, ObjectAlias.kind == kind)
    )
    norm = compare_key(value)
    if found is not None:
        found.value = value[:200]
        found.norm = norm
        return
    db.add(
        ObjectAlias(
            object_id=row.id, type_id=object_type.id, kind=kind, value=value[:200], norm=norm
        )
    )


def pending(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    limit: int,
    offset: int,
) -> tuple[list[tuple[ObjectAlias, str, str | None]], int]:
    """검수 대기 별칭 — `(별칭, 객체 이름, 객체 식별자)` 와 전체 수.

    **사람 별칭만** 본다(외부 식별자는 동기화가 관리한다). 오래된 것부터 — 먼저 붙은 것이
    먼저 쓰인다.
    """
    base = (
        select(ObjectAlias, ObjectInstance.label, ObjectInstance.key)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.kind == HUMAN,
            ObjectAlias.verified_at.is_(None),
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(ObjectAlias.created_at, ObjectAlias.id).limit(limit).offset(offset)
    ).all()
    return [(one, label, key) for one, label, key in rows], total


def review(
    db: Session,
    user: User,
    object_type: ObjectType,
    ids: list[uuid.UUID],
    *,
    approve: bool,
) -> tuple[int, list[str]]:
    """고른 별칭을 확인하거나 지운다 — `(한 것, 못 한 줄의 이유)`.

    **부르는 쪽이 커밋한다.** 못 한 것은 그 줄만 남긴다(남의 부서 것, 이미 없는 것) —
    하나가 막혀 나머지가 안 되면 사람은 그 목록을 다시 안 본다.
    """
    if not ids:
        return 0, []
    rows = db.execute(
        select(ObjectAlias, ObjectInstance)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(
            ObjectAlias.id.in_(ids),
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.kind == HUMAN,
        )
    ).all()
    found = {one.id for one, _ in rows}
    refused = [f"{one} 은 이미 없습니다" for one in ids if one not in found]
    done = 0
    now = datetime.now(UTC)
    for alias_row, owner in rows:
        try:
            require_owner_edit(
                db,
                user,
                owner.owner_workspace_id,
                what="객체",
                code_value=code("OBJECTS", 12),
            )
        except AppError as denied:
            # **그 줄만 막는다** — 하나가 막혀 나머지가 안 되면 사람은 목록을 다시 안 본다.
            refused.append(f"「{alias_row.value}」: {denied.message}")
            continue
        if approve:
            alias_row.verified_by_id = user.id
            alias_row.verified_at = now
        else:
            db.delete(alias_row)
            # 지운 별칭은 이름 풀이에서 빠진다 — 밖도 그것을 알아야 한다.
            _touch(owner)
        done += 1
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=owner.id,
            target_label=f"{object_type.slug}:{owner.label}",
            workspace_id=owner.owner_workspace_id,
            changes={
                "aliases": {
                    "before": alias_row.value,
                    "after": alias_row.value if approve else None,
                }
            },
            reason="별칭 검수 — 확인" if approve else "별칭 검수 — 지움",
        )
    return done, refused


def lookup(
    db: Session, object_type: ObjectType, text: str, *, kind: str | None = None
) -> list[uuid.UUID]:
    """이 글자를 별칭(또는 외부 식별자)으로 가진 객체들 — 지워진 것은 뺀다."""
    stmt = (
        select(ObjectAlias.object_id)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.norm == compare_key(text),
            ObjectInstance.deleted_at.is_(None),
        )
    )
    if kind is not None:
        stmt = stmt.where(ObjectAlias.kind == kind)
    return list(dict.fromkeys(db.scalars(stmt)))


def index_of(db: Session, object_type: ObjectType) -> dict[str, list[uuid.UUID]]:
    """타입 하나의 별칭 전부 — {비교키: [객체 id]}. 파일을 풀 때 한 번 읽는다."""
    out: dict[str, list[uuid.UUID]] = defaultdict(list)
    rows = db.execute(
        select(ObjectAlias.norm, ObjectAlias.object_id)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(ObjectAlias.type_id == object_type.id, ObjectInstance.deleted_at.is_(None))
    )
    for norm, object_id in rows:
        if object_id not in out[norm]:
            out[norm].append(object_id)
    return out


def move(db: Session, loser: ObjectInstance, winner: ObjectInstance) -> tuple[int, int]:
    """합치기 — 지는 쪽의 별칭·외부 식별자와 **이름**을 이긴 쪽으로. 겹치면 버린다.
    (옮긴 수, 버린 수)."""
    winner_rows = list(
        db.scalars(select(ObjectAlias).where(ObjectAlias.object_id == winner.id))
    )
    have = {(one.kind, one.norm) for one in winner_rows}
    have.add((HUMAN, compare_key(winner.label)))
    moved = dropped = 0
    for one in db.scalars(select(ObjectAlias).where(ObjectAlias.object_id == loser.id)):
        if (one.kind, one.norm) in have:
            db.delete(one)
            dropped += 1
            continue
        one.object_id = winner.id
        have.add((one.kind, one.norm))
        moved += 1
    # 지는 쪽 이름도 별칭으로 — 같은 표기로 다시 와도 같은 것으로 풀리게.
    loser_norm = compare_key(loser.label)
    if (HUMAN, loser_norm) not in have and loser_norm:
        db.add(
            ObjectAlias(
                object_id=winner.id,
                type_id=winner.type_id,
                kind=HUMAN,
                value=loser.label[:200],
                norm=loser_norm,
            )
        )
        moved += 1
    db.flush()
    return moved, dropped


def drop_all(db: Session, object_id: uuid.UUID) -> None:
    """지운 객체의 별칭을 비운다 — 남기면 그 이름을 다른 객체가 못 쓴다."""
    db.execute(delete(ObjectAlias).where(ObjectAlias.object_id == object_id))
