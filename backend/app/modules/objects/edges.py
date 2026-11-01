"""선을 고르는 규칙 — **「어느 선이 그 날 살아 있나」 를 가르는 한 자리**(ADR 0039).

## 유효 기간

관계 종류가 유효 기간을 받으면(`RelationType.effectivity`) 그 종류의 선은 `valid_from`(포함) ·
`valid_to`(제외)를 받는다 — **반열린 구간 [from, to)**. 둘 다 비면 늘 유효, 한쪽만 비면 그쪽이
열린 구간이다.

반열린 까닭: 「3월 1일부터 B 로 바뀜」 을 A 의 to = B 의 from = 3월 1일 하나로 적는다. 닫힌
구간이면 3월 1일에 A 와 B 가 함께 살아 있고(같은 자리에 부품 둘), 그것을 피하려면 A 의 끝을
2월 28일(윤년이면 29일)로 적어야 한다 — 사람은 바뀌는 날을 알지, 그 전날을 알지 않는다.

## 기준일

읽는 쪽은 **기준일**(`at`)을 준다 — 그날 살아 있는 선만 고른다. 화면 · API 의 기본은 **오늘**
이라, 유효 기간이 지난 선은 기본 화면에서 빠진다. `None` 은 기간을 보지 않는다(지난 · 앞으로의
선까지 모두 — 「관련 객체」 의 「지난 · 앞으로의 선 포함」, 순환 검사, 내보내기의 전부). 유효
기간이 꺼진 종류의 선은 두 칸이 늘 비어 있어 어느 기준일에도 살아 있다 — 예전과 같다.

## 판정이 여기 하나인 까닭

트리 · 하위 전체 · 계층 축 · 롤업 · 연결 경로 · 이웃 그림 · 관련 객체 · 조건 걸음 · 통계 ·
지표 · 코어 API · RDF · 내보내기가 **모두** `alive`(ORM) · `alive_sql`(SQL 글) 를 부른다. 같은
뜻이 여러 곳에 갈라져 있으면 하나만 고쳐지고, 그때 트리에는 없는 부품이 롤업에는 더해진다.

**다음 규칙도 여기에 더한다** — 선을 고르는 조건이 새로 생기면 `alive` · `alive_sql` 두 곳에
함께 넣는다(둘은 같은 규칙의 두 표기다 — `tests/api/test_relation_periods.py` 의
`test_ORM_판정과_SQL_판정이_같은_줄을_고른다` 가 둘을 같은 줄로 맞춰 본다). 부르는 쪽은
아무것도 안 바꾼다. 경로 계산(②, ADR 0040)과 파생 · 계보의 낡음 판정(③, ADR 0041 —
`objects/lineage.py`)은 선을 고르지 않고 **고른 선 위에서** 센다 — 그래서 둘 다 여기를 거치고,
여기에 더해지는 조건을 그대로 따른다.

**제안된 관계(④, ADR 0042)는 여기에 없다** — 제안은 따로 둔 표(`relation_proposals`)라 선을
고르는 판정에 들지 않는다. 처음에는 선에 상태 칸을 두고 여기서 「확정된 선만」 을 거르려
했으나, 이 판정 밖에서 `object_relations` 를 읽는 자리(개수 제약 · 순환 검사 · 유일 색인 ·
계수표 트리거 · 묶음 · 삭제 확인 …)가 많아 한 곳이라도 빠지면 확정 안 된 선이 섞인다. 확정은
보통의 선 만들기다.

참조 칸으로 이은 선(`object_refs`)과 원 표와 이은 선(`object_links`)에는 기간이 없다 — 늘 살아
있다.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.modules.objects import effectivity
from app.modules.objects.models import ObjectRelation
from app.modules.ontology.models import RelationType
from app.modules.ontology.services import InvalidValue
from app.shared.errors import AppError, Conflict, code

#: 그 선이 기준일에 — 지금(살아 있음) · 지났음 · 앞으로.
State = Literal["current", "past", "future"]

#: 기준일 인자 — 날짜 · `"today"`(부를 때의 오늘) · None(기간을 안 본다). 오래 사는 것
#: (`paths.Resolver` · `axes.JoinPlan`)이 기본값으로 「오늘」 을 들 때 쓴다.
AtArg = date | Literal["today"] | None


def today() -> date:
    """기본 기준일 — 서버의 오늘(지표의 방문 창 · 머무는 기간과 같은 시계)."""
    return date.today()


def at(as_of: date | None = None, *, every: bool = False) -> date | None:
    """요청이 준 기준일 → 판정에 넘길 날. **비우면 오늘**, `every` 면 None(기간을 안 본다)."""
    if every:
        return None
    return as_of or today()


def resolved(at_arg: AtArg) -> date | None:
    """`AtArg` → 판정에 넘길 날."""
    return today() if at_arg == "today" else at_arg


def alive(rel: Any, day: date | None) -> ColumnElement[bool]:
    """그날 살아 있는 선 — `rel` 은 `ObjectRelation` 이나 그 별칭. `day` 가 None 이면 거르지
    않는다. `alive_sql` 과 **같은 규칙**이다."""
    if day is None:
        return true()
    return and_(
        or_(rel.valid_from.is_(None), rel.valid_from <= day),
        or_(rel.valid_to.is_(None), rel.valid_to > day),
    )


def alive_sql(alias: str, day: date | None) -> str:
    """`alive` 의 SQL 글 — 재귀 CTE 처럼 글로 적은 질의가 쓴다(`alias` 는 `object_relations` 의
    별칭). 날짜는 글로 박는다 — `date.isoformat()` 은 숫자와 대시뿐이라 따옴표가 샐 일이 없고,
    상수라야 플래너가 행 수를 어림한다."""
    if day is None:
        return "TRUE"
    when = f"DATE '{day.isoformat()}'"
    return (
        f"(({alias}.valid_from IS NULL OR {alias}.valid_from <= {when}) "
        f"AND ({alias}.valid_to IS NULL OR {alias}.valid_to > {when}))"
    )


def state(valid_from: date | None, valid_to: date | None, day: date | None) -> State:
    """그 기간이 기준일에 어디인가 — 화면 · MCP 가 「지난 선」 「앞으로의 선」 을 적는 근거."""
    when = day or today()
    if valid_to is not None and valid_to <= when:
        return "past"
    if valid_from is not None and valid_from > when:
        return "future"
    return "current"


def overlaps(rel: Any, valid_from: date | None, valid_to: date | None) -> ColumnElement[bool]:
    """그 선의 기간이 [valid_from, valid_to) 와 겹치나 — 빈 끝은 열린 끝. 배제 제약
    (`daterange && daterange`)과 같은 뜻이다."""
    parts: list[ColumnElement[bool]] = []
    if valid_to is not None:
        parts.append(or_(rel.valid_from.is_(None), rel.valid_from < valid_to))
    if valid_from is not None:
        parts.append(or_(rel.valid_to.is_(None), rel.valid_to > valid_from))
    return and_(true(), *parts)


def describe(valid_from: date | None, valid_to: date | None) -> str:
    """사람이 읽을 기간 — 「2026-03-01 부터 2026-09-01 전까지」."""
    if valid_from is None and valid_to is None:
        return "늘 유효"
    head = f"{valid_from.isoformat()} 부터" if valid_from else "처음부터"
    tail = f"{valid_to.isoformat()} 전까지" if valid_to else "끝없이"
    return f"{head} {tail}"


def checked_period(
    kind: RelationType, valid_from: date | None, valid_to: date | None
) -> tuple[date | None, date | None]:
    """받은 기간이 그 종류에 맞나 — 꺼진 종류에 기간이 오면, 시작일이 끝날보다 늦으면 거절한다.

    DB 도 같은 것을 막는다(트리거 · 검사 제약) — 여기는 사람이 읽을 말로 먼저 거절하는 자리다.
    """
    if (valid_from is not None or valid_to is not None) and not kind.effectivity:
        raise InvalidValue(
            code("OBJECTS", 126),
            f"{kind.label}은 유효 기간을 받지 않는 관계 종류입니다 — 관계 종류 편집에서 "
            "「유효 기간을 받는다」 를 켜면 기간을 적을 수 있습니다.",
        )
    if valid_from is not None and valid_to is not None and valid_from >= valid_to:
        raise InvalidValue(
            code("OBJECTS", 127),
            f"유효 기간의 시작일({valid_from.isoformat()})이 끝날({valid_to.isoformat()})보다 "
            "앞이어야 합니다 — 끝날은 그날부터 효력이 없는 날(제외)입니다.",
        )
    return valid_from, valid_to


def find_overlap(
    db: Session,
    *,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    relation: str,
    valid_from: date | None,
    valid_to: date | None,
    skip: uuid.UUID | None = None,
) -> ObjectRelation | None:
    """같은 두 객체 · 같은 종류의 선 중 기간이 겹치는 것(자기 자신 `skip` 은 빼고)."""
    stmt = select(ObjectRelation).where(
        ObjectRelation.src_object_id == src_id,
        ObjectRelation.dst_object_id == dst_id,
        ObjectRelation.relation == relation,
        overlaps(ObjectRelation, valid_from, valid_to),
    )
    if skip is not None:
        stmt = stmt.where(ObjectRelation.id != skip)
    return db.scalar(stmt.limit(1))


def overlap_conflict(found: ObjectRelation | None = None) -> Conflict:
    """같은 선의 기간이 겹친다 — 꺼진 종류에서는 「이미 이어져 있다」 와 같은 말이다."""
    if found is not None and (found.valid_from is not None or found.valid_to is not None):
        return Conflict(
            code("OBJECTS", 128),
            "같은 두 객체 사이에 기간이 겹치는 같은 관계가 이미 있습니다"
            f"({describe(found.valid_from, found.valid_to)}). 기간을 겹치지 않게 고치거나 "
            "있는 관계의 기간을 먼저 줄이세요.",
        )
    return Conflict(code("OBJECTS", 29), "이미 이어져 있습니다.")


def require_no_overlap(
    db: Session,
    *,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    relation: str,
    valid_from: date | None,
    valid_to: date | None,
    skip: uuid.UUID | None = None,
) -> None:
    """같은 선이 겹치면 사람이 읽을 말로 거절한다 — 꺼진 종류는 기간이 늘 비어 「같은 선 하나」
    와 같은 검사가 된다. **동시성은 DB 제약이 지킨다**(`translated`)."""
    found = find_overlap(
        db,
        src_id=src_id,
        dst_id=dst_id,
        relation=relation,
        valid_from=valid_from,
        valid_to=valid_to,
        skip=skip,
    )
    if found is not None:
        raise overlap_conflict(found)


def translated(caught: IntegrityError) -> AppError | None:
    """DB 가 거절한 선(같은 선 · 기간 겹침 · 기간 모양)을 사람의 말로 — 앱의 검사를 지나 같은
    순간에 들어온 두 요청 중 늦은 쪽이 여기로 온다. 다른 무결성 오류면 None."""
    diag = getattr(getattr(caught, "orig", None), "diag", None)
    name = getattr(diag, "constraint_name", None) or ""
    if name == effectivity.OVERLAP_CONSTRAINT:
        return Conflict(
            code("OBJECTS", 128),
            "같은 두 객체 사이에 기간이 겹치는 같은 관계가 이미 있습니다 — 방금 다른 곳에서 "
            "이었을 수 있습니다. 새로 고쳐 보고 기간을 겹치지 않게 적으세요.",
        )
    if name == effectivity.SAME_EDGE:
        return Conflict(code("OBJECTS", 29), "이미 이어져 있습니다.")
    if name == effectivity.PERIOD_CHECK:
        return InvalidValue(
            code("OBJECTS", 127),
            "유효 기간이 맞지 않습니다 — 시작일은 끝날보다 앞이어야 하고, 기간은 유효 기간을 "
            "받는 관계 종류에만 적습니다.",
        )
    return None


def flush(db: Session) -> None:
    """선을 쓰고 flush — DB 가 거절하면 사람의 말로(`translated`)."""
    try:
        db.flush()
    except IntegrityError as caught:
        found = translated(caught)
        if found is None:
            raise
        raise found from caught


def kinds_with_periods(db: Session) -> set[str]:
    """유효 기간을 받는 관계 종류의 slug — 증분 · 코어 창구가 「경계일이 지났나」 를 볼
    종류."""
    return set(db.scalars(select(RelationType.slug).where(RelationType.effectivity.is_(True))))


def dated_count(db: Session, relation: str) -> int:
    """그 종류의 선 중 **기간이 든 것**의 수 — 유효 기간을 끄기 전에 본다."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(ObjectRelation)
            .where(
                ObjectRelation.relation == relation,
                or_(
                    ObjectRelation.valid_from.is_not(None),
                    ObjectRelation.valid_to.is_not(None),
                ),
            )
        )
        or 0
    )


def require_can_turn_off(db: Session, kind: RelationType) -> None:
    """유효 기간을 꺼도 되나 — 기간이 든 선이 있으면 거절한다. 끄면 그 기간이 뜻을 잃고(늘
    유효로 읽힌다), 기간을 달리한 같은 선이 「같은 선 하나」 를 어긴다. 조용히 기간을 지우면
    지난 구성이 지금 구성에 섞인다 — 사람이 먼저 정리한다."""
    dated = dated_count(db, kind.slug)
    if dated:
        raise Conflict(
            code("ONTOLOGY", 95),
            f"{kind.label}의 유효 기간을 끌 수 없습니다 — 기간이 든 관계가 {dated}개 "
            "있습니다. 그 관계들의 기간을 먼저 비우거나(같은 두 객체 사이의 관계는 하나만 "
            "남기고) "
            "정리하세요.",
            details={"dated_relations": dated},
        )


def crossed(db: Session, relations: set[str] | list[str], *, after: date, until: date) -> bool:
    """그 종류들의 선 중 **유효 기간의 경계(시작일 · 끝날)가 (after, until] 에 든 것**이 있나.

    기준일을 「오늘」 로 읽는 계산(지표)은 날짜가 넘어가기만 해도 답이 바뀐다 — 3월 1일에 끝난
    선은 2월 28일의 계산에는 들고 3월 1일의 계산에는 안 든다. 선도 기록도 안 바뀌었으니
    `updated_at` 으로는 못 본다. 지표의 증분(ADR 0024)이 이것으로 「시계가 경계를 넘었다」 를
    보고 전량으로 간다."""
    if not relations or until <= after:
        return False
    rel = ObjectRelation
    return (
        db.scalar(
            select(rel.id)
            .where(
                rel.relation.in_(list(relations)),
                or_(
                    and_(rel.valid_from > after, rel.valid_from <= until),
                    and_(rel.valid_to > after, rel.valid_to <= until),
                ),
            )
            .limit(1)
        )
        is not None
    )
