"""지운 속성의 키를 다시 세우면 **남은 저장값이 되살아난다** — 세고, 새 정의에 맞는지 본다.

속성을 지워도 객체의 값은 남는다(`routes.delete_property` — 스냅샷으로 정의를 되살리면 값도
다시 보이게). 그래서 같은 키로 속성을 새로 만들면 그 값이 새 정의의 값으로 보인다 — 예전 글
칸의 「12개」 가 새 숫자 칸에 뜨고, 그 객체는 다음에 고칠 때 「숫자여야 합니다」 로 거절된다.
키 변경 · 옮기기는 같은 키의 남은 값을 이미 막는다(`rename.plan_property` · `move._fields`).
화면의 새 속성과 정의 가져오기의 새 속성(가져오기 · 정의 제안 · 묶음 · 코어 구독)도 같게 한다:

- **객체를 읽기 전에 감사 기록으로 거른다**(`_signal`). 속성에는 통째 색인이 없어(ADR 0010)
  「이 키에 값이 있나」 는 타입을 통째로 훑는다 — 처음 쓰는 키면 맞는 줄이 없어 끝까지
  읽는다(200만 건 타입에서 속성 하나에 1~4초, 2026-11-28 규모 DB 실측). 값을 남기는 길은
  둘뿐이다 — 그 타입에서 그 키의 속성을 지웠다(`ontology.property.delete`), 다른 타입에서
  정의 없는 옛 값을 들고 옮겨 왔다(`ontology.type.move` 의 `carried`). 둘 다 없으면 안 읽는다.
- 있으면 읽는다 — 요청 안에서는 작은 타입(살아 있는 객체가 상한 `bounded.CAP` 이하)만,
  상한까지. 큰 타입은 읽지 않고 「다시 보일 수 있다」 고 말한다(작업 — 묶음 · 코어 구독 —
  은 끝까지 읽는다).
- 남은 값이 있으면 경고 「저장된 값 N건이 다시 보입니다」. **새 정의의 종류(모양)에 안 맞는
  값**(숫자 칸의 글자 · 값 하나 칸의 목록 …)이 있으면 막는다 — 저장값에 맞는 종류로 만든 뒤
  「종류 변경」, 또는 다른 키. 종류는 맞는데 **제약**(고를 값 · 범위 · 모양 규칙 · 소수
  자릿수)만 안 맞는 값은 경고다 — 정의 가져오기가 제약을 조일 때와 같다
  (`importer._warn_property_risks`).
  그래야 같은 정의로 되살리는 스냅샷 복원이 늘 된다(고를 값을 뺀 뒤 지운 속성도).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.objects import typecounts
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import bounded
from app.modules.ontology.conversion import key_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.ontology.retype import READ_CHUNK, kind_label
from app.modules.ontology.services import InvalidValue, check_value

#: 막을 때 싣는 견본 수.
SAMPLES = 3
#: 견본 값의 길이.
SAMPLE_CHARS = 40
#: 값을 남기는 까닭 — 경고의 말.
WHY = {
    "deleted": "예전에 같은 키의 속성을 지운 적이 있어",
    "moved": "다른 타입에서 정의 없는 옛 값을 들고 옮겨 온 적이 있어",
}


@dataclass
class Leftover:
    """그 키에 남은 값 — 셈 · 맞지 않는 값."""

    why: str = "deleted"
    """`WHY` 의 열쇠 — 남은 값이 있을 수 있는 까닭."""
    count: int = 0
    """값이 남은 객체(지운 객체 포함) — 요청 안이면 상한 + 1 에서 멈춘다."""
    misfits: int = 0
    """읽은 것 중 새 정의의 **종류**(모양)에 안 맞는 값 — 막는다."""
    loose: int = 0
    """종류는 맞는데 **제약**(고를 값 · 범위 · 모양 규칙)에 안 맞는 값 — 경고만."""
    samples: list[tuple[str, str]] = field(default_factory=list)
    """(값, 까닭) — 종류에 안 맞는 값의 앞 몇 개."""
    partial: bool = False
    """다 읽지 않았다(요청 안의 상한)."""
    unscanned: int = 0
    """요청 안이라 읽지 않은 큰 타입의 살아 있는 객체 수 — 0 이면 읽었다."""


def _scalar_shape(kind: str, item: Any) -> str | None:
    """원소 하나가 그 종류의 **모양**인가 — 제약(고를 값 · 범위 · 규칙)은 보지 않는다."""
    if kind in ("text", "text_long", "url", "enum"):
        return None if isinstance(item, str) else "글자가 아닙니다"
    if kind == "number":
        ok = isinstance(item, (int, float)) and not isinstance(item, bool)
        return None if ok else "숫자가 아닙니다"
    if kind == "bool":
        return None if isinstance(item, bool) else "예/아니오 값이 아닙니다"
    if kind in ("date", "datetime"):
        parse = date.fromisoformat if kind == "date" else datetime.fromisoformat
        if not isinstance(item, str):
            return "날짜가 아닙니다"
        try:
            parse(item)
        except ValueError:
            return f"{kind_label(kind)} 모양이 아닙니다"
        return None
    if kind == "object_ref":
        try:
            uuid.UUID(str(item))
        except ValueError:
            return "객체 id 가 아닙니다"
        return None if isinstance(item, str) else "객체 id 가 아닙니다"
    # 문서 · 파일은 값이 `properties` 에 살지 않는다 — 남은 값은 무엇이든 안 맞는다.
    return f"{kind_label(kind)} 속성은 값을 properties 에 두지 않습니다"


def shape_problem(definition: PropertyDef, raw: Any) -> str | None:
    """저장값 하나가 정의의 **종류 · 여러 값**에 안 맞는 까닭 — 맞으면 None. 빈 값은 맞는
    것으로 본다(보이지 않는다)."""
    if raw is None or raw == "" or raw == []:
        return None
    if definition.multi:
        if not isinstance(raw, list):
            return "여러 값의 목록이 아닙니다"
        items = raw
    else:
        if isinstance(raw, list):
            return "값 하나만 담는 속성에 목록이 있습니다"
        items = [raw]
    for item in items:
        problem = _scalar_shape(definition.data_type, item)
        if problem:
            return problem
    return None


def constraint_problem(definition: PropertyDef, raw: Any) -> str | None:
    """모양은 맞는 저장값이 정의의 **제약**에 안 맞는 까닭 — 저장할 때와 같은 검사
    (`check_value`)."""
    items = raw if isinstance(raw, list) else [raw]
    try:
        for item in items:
            if item is not None and item != "":
                check_value(definition, item)
    except InvalidValue as caught:
        return caught.message
    return None


def _signal(db: Session, owner: ObjectType, key: str) -> str | None:
    """이 타입의 객체에 `key` 로 남은 값이 **있을 수 있나** — 객체를 안 읽고 감사 기록으로
    (`action` 색인, 규모 DB 에서 1ms 아래). 없으면 None, 있으면 `WHY` 의 열쇠.

    - 속성 삭제 — 이름표가 「<그때의 slug>.<키>」 다. slug 를 바꾼 타입이면 옛 slug 들도 본다
      (`ontology.type.rename` 의 `from`). 같은 slug 를 쓰던 다른 타입의 기록에도 맞을 수 있다 —
      그때는 읽어 볼 뿐이다(잘못 건너뛰는 쪽보다 낫다).
    - 옮겨 온 것 — 옮기기는 원 타입의 정의 없는 옛 값을 그대로 옮긴다. 이 판부터 옮기기
      기록에 그 키들을 적는다(`carried`). 그 전의 기록은 어느 키인지 몰라 있을 수 있다고
      본다."""
    slugs = {owner.slug}
    slugs.update(
        one
        for one in db.scalars(
            select(AuditEntry.changes["from"].astext).where(
                AuditEntry.action == "ontology.type.rename", AuditEntry.target_id == owner.id
            )
        )
        if one
    )
    deleted = db.scalar(
        select(AuditEntry.id)
        .where(
            AuditEntry.action == "ontology.property.delete",
            AuditEntry.target_label.in_(sorted(f"{one}.{key}" for one in slugs)),
        )
        .limit(1)
    )
    if deleted is not None:
        return "deleted"
    for carried in db.scalars(
        select(AuditEntry.changes["carried"]).where(
            AuditEntry.action == "ontology.type.move", AuditEntry.target_id == owner.id
        )
    ):
        if not isinstance(carried, list) or key in carried:
            return "moved"
    return None


def _values(
    db: Session, type_id: uuid.UUID, key: str, *, limit: int | None
) -> Iterator[list[Any]]:
    """그 키에 값이 있는 객체(지운 것 포함)의 그 칸 — 덩어리로. `limit` 까지만."""
    last: uuid.UUID | None = None
    seen = 0
    while True:
        size = READ_CHUNK if limit is None else min(READ_CHUNK, limit - seen)
        if size <= 0:
            return
        stmt = (
            select(ObjectInstance.id, ObjectInstance.properties[key])
            .where(ObjectInstance.type_id == type_id, ObjectInstance.properties.has_key(key))
            .order_by(ObjectInstance.id)
            .limit(size)
        )
        if last is not None:
            stmt = stmt.where(ObjectInstance.id > last)
        rows = list(db.execute(stmt))
        if not rows:
            return
        seen += len(rows)
        last = rows[-1][0]
        yield [raw for _id, raw in rows]


def leftover(
    db: Session, owner: ObjectType, definition: PropertyDef, *, in_request: bool
) -> Leftover | None:
    """`owner` 타입의 객체에 `definition.key` 로 남은 값 — 없으면(또는 남을 길이 없었으면)
    None. 요청 안의 큰 타입은 읽지 않고 `unscanned` 로 돌려준다."""
    why = _signal(db, owner, definition.key)
    if why is None:
        return None
    if in_request:
        live = typecounts.counts(db).get(owner.id, 0)
        if live > bounded.CAP:
            return Leftover(why=why, unscanned=live)
    out = Leftover(why=why)
    for chunk in _values(
        db, owner.id, definition.key, limit=bounded.CAP + 1 if in_request else None
    ):
        for raw in chunk:
            out.count += 1
            if out.count > bounded.CAP and in_request:
                out.partial = True
                continue
            problem = shape_problem(definition, raw)
            if problem is None:
                if raw not in (None, "", []) and constraint_problem(definition, raw):
                    out.loose += 1
                continue
            out.misfits += 1
            if len(out.samples) < SAMPLES:
                out.samples.append((key_of(raw)[:SAMPLE_CHARS], problem))
    return out if out.count else None


def warning(name: str, found: Leftover) -> str:
    """「저장된 값 N건이 다시 보입니다」 — 읽지 못한 큰 타입이면 「다시 보일 수 있습니다」."""
    if found.unscanned:
        return (
            f"속성 {name}: {WHY.get(found.why, WHY['deleted'])} 저장된 값이 다시 보일 수 "
            f"있습니다 — 객체가 많아({found.unscanned:,}건) 요청 안에서는 세지 않았습니다. "
            "새 정의의 종류에 안 맞는 값이 있으면 그 객체는 수정할 때 거절됩니다. 확인하려면 "
            "같은 정의를 묶음 가져오기 작업(POST /api/bundles/import)으로 보냅니다 — 작업이 "
            "끝까지 세고, 안 맞는 값이 있으면 막습니다."
        )
    tail = (
        f" 앞의 {bounded.CAP:,}건만 새 정의에 맞는지 봤습니다(요청 안에서는 다 읽지 않습니다)."
        if found.partial
        else ""
    )
    loose = (
        f" 그중 {found.loose:,}건은 지금 정의의 제약(고를 값 · 범위 · 모양 규칙)에 맞지 "
        "않습니다 — 보이기는 하지만 그 객체를 수정할 때 거절됩니다."
        if found.loose
        else ""
    )
    return (
        f"속성 {name}: 저장된 값 {bounded.shown(found.count)}이 다시 보입니다 — "
        f"{_source(found)} 값입니다(지운 객체 포함).{loose}{tail}"
    )


def _source(found: Leftover) -> str:
    return (
        "다른 타입에서 옮겨 온"
        if found.why == "moved"
        else "예전에 지운 같은 키의 속성이 남긴"
    )


def refusal(name: str, definition: PropertyDef, found: Leftover) -> str:
    """새 정의의 종류에 안 맞는 남은 값이 있어 막는 말."""
    shown = " · ".join(f"「{value}」({why})" for value, why in found.samples)
    return (
        f"속성 {name}: {_source(found)} 값 {bounded.shown(found.count)} 중 "
        f"{found.misfits:,}건이 새 정의의 종류({kind_label(definition.data_type)}"
        f"{' · 여러 값' if definition.multi else ''})에 맞지 않습니다 — {shown}. 이대로 "
        "만들면 그 값이 그대로 보이고 그 객체는 다음에 수정할 때 거절됩니다. 저장값에 맞는 "
        "종류로 만든 뒤 「종류 변경」 으로 바꾸거나(변환할 수 없는 값은 거기서 대체 값으로 "
        "정합니다), 그 값을 살릴 필요가 없으면 다른 키를 쓰세요."
    )
