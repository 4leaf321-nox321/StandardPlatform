"""별칭 후보 — **못 찾은 말을 모아, 사람이 별칭으로 붙이면 다음부터 찾힌다.**

이름으로 찾는 자리(이름 풀이 `object_resolve` · 통합 검색 · 목록의 검색어)에서 아무것도 못
찾으면, 찾던 쪽은(사람이든 AI 든) 없는 줄 알고 새로 만든다 — 그러면 같은 것이 둘이 된다. 그
말이 어떤 객체의 다른 이름이었다면 별칭으로 붙이는 순간 다음부터 찾힌다. 그 말을 여기 모은다
(ADR 0025).

## 남기는 것과 안 남기는 것

    남긴다     정규화한 말 · 어디서 찾았나(타입 · 인터페이스, 비면 통합 검색) · 횟수 ·
               몇 사람인가(지문) · 어느 자리(풀이 · 검색 · 목록) · 처음 / 마지막
    안 남긴다  누가 찾았나 · 두 글자 미만 · 숫자뿐 · id · 메일 주소 · 비밀번호 같은 것 ·
               긴 글(이름이 아니라 문장이다) · **있지만 그 사람에게 안 보였던 것**

마지막이 중요하다 — 남의 부서 객체의 이름을 친 사람은 「못 찾았」 지만, 그것은 별칭으로
고칠 일이 아니라 권한의 일이다. 그런 말이 후보에 쌓이면 고칠 수 없는 줄이 목록을 덮는다.

## 찾기를 늦추지도 실패시키지도 않는다

기록은 응답을 보낸 **뒤에** 따로 연 세션으로 한다(`BackgroundTasks`). 실패하면 로그에 한 줄
남기고 삼킨다 — 기록 때문에 찾기가 실패하면 그것은 고치려던 일보다 큰 고장이다. 같은 말은
upsert 한 번으로 횟수만 올린다(동시에 여럿이 못 찾아도 한 줄).

## 치는 중의 글자

목록 검색 · 고르개는 치는 대로 묻는다 — 「앤시」 「앤시스」 가 차례로 못 찾으면 둘 다 남는다.
**방금(30초 안) 한 번 남은 말이 새 말의 앞부분이면 지운다** — 치는 중의 글자였다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import (
    String,
    case,
    delete,
    func,
    literal,
    literal_column,
    or_,
    select,
    union,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.objects import aliases, system
from app.modules.objects.models import (
    NORMALIZED_KEY_SQL,
    ObjectAlias,
    ObjectInstance,
    SearchMiss,
)
from app.modules.objects.scope import find as find_scope
from app.modules.ontology import interfaces, managed
from app.modules.ontology.models import ObjectInterface, ObjectType
from app.shared import audit, extensions
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from app.shared.permissions import is_any_manager, require_owner_edit, visible_owner_clause
from app.shared.text import clean, compare_key

log = logging.getLogger(__name__)

#: 이 글자 수 미만은 안 남긴다 — 통합 검색이 찾기를 시작하는 길이와 같다.
MIN_LEN = 2
#: 이보다 긴 것은 이름이 아니라 문장 · 붙여 넣은 글이다. 별칭(200자)보다 짧게 끊는다.
MAX_LEN = 100
#: 몇 사람인지 셀 지문의 상한 — 스물을 넘으면 「많이」 로 충분하다.
ASKERS_MAX = 20
#: 못 찾은 자리.
VIAS = {"resolve": "이름 풀이", "search": "통합 검색", "list": "목록 검색"}
#: 치는 중의 글자로 볼 시간.
TYPING_SECONDS = 30
#: 정리 — 처리한 줄(붙임 · 무시)은 이만큼 아무도 안 찾으면 지운다.
KEEP_DECIDED_DAYS = 90
#: 정리 — 몇 번 안 찾은 대기 줄은 이만큼 아무도 안 찾으면 지운다.
KEEP_RARE_DAYS = 180
RARE_HITS = 3
#: 대기 줄의 상한. 넘으면 적게 · 오래전에 찾은 것부터 지운다 — 무작위 말을 퍼붓는 스크립트가
#: 표를 끝없이 키우지 않게.
PENDING_MAX = 100_000
#: 프로세스마다 정리를 몇 초에 한 번.
PURGE_EVERY = 3600.0
#: 홈 「남은 일」 에 올리는 문턱 — 이만큼 찾은 말만. 한 번 친 오타까지 세면 그 수는 늘
#: 크고, 늘 큰 수는 아무도 안 읽는다.
REPEAT_MIN = 3
#: 후보마다 「이것 아닐까」 몇 개.
SUGGEST = 3
#: 비슷함(pg_trgm) 문턱 — 이 아래는 제안하지 않는다.
SIMILARITY_MIN = 0.3
#: 제안을 찾는 타입(들)의 객체가 이보다 많으면 건너뛴다 — 비슷함은 인덱스를 못 타서 행을 다
#: 본다. 건너뛴 사실은 그 줄에 적는다.
SUGGEST_MAX_ROWS = 50_000

_DIGITS = re.compile(r"[\d\s.,:;/\\\-+_#()\[\]%]+")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


# --- 거르기 ---------------------------------------------------------------------


def skip_reason(text: str) -> str | None:
    """남기지 않을 말이면 그 까닭, 남길 말이면 None.

    **비밀번호처럼 보이는 것** — 검색 칸에 비밀번호를 잘못 붙여 넣는 일은 실제로 있다. 띄어쓰기
    없이 여덟 자 이상이고 소문자 · 대문자 · 숫자 · 기호를 다 쓴 것, 그리고 토큰 표식(`_pat_`)이
    든 것은 남기지 않는다. 부품번호(`BT-2041`)는 소문자가 없어 걸리지 않는다."""
    if len(text) < MIN_LEN:
        return "짧음"
    if len(text) > MAX_LEN:
        return "김"
    if _DIGITS.fullmatch(text):
        return "숫자뿐"
    try:
        uuid.UUID(text)
    except ValueError:
        pass
    else:
        return "id"
    if _EMAIL.fullmatch(text):
        return "메일 주소"
    if _secret_like(text):
        return "비밀번호 같음"
    return None


def _secret_like(text: str) -> bool:
    if "_pat_" in text:
        return True
    if " " in text or len(text) < 8:
        return False
    kinds = (
        any(one.islower() for one in text),
        any(one.isupper() for one in text),
        any(one.isdigit() for one in text),
        any(not one.isalnum() for one in text),
    )
    return all(kinds)


def asker_of(user_id: uuid.UUID) -> str:
    """사람의 **지문** — 몇 사람인지 세려고. 이름 · id 를 남기지 않는다(설치의 비밀로
    섞는다)."""
    secret = get_settings().jwt_secret
    return hashlib.sha256(f"{secret}:miss:{user_id}".encode()).hexdigest()[:12]


# --- 남기기 ---------------------------------------------------------------------


def note(background: BackgroundTasks, user: User, scope: str, text: str, via: str) -> None:
    """찾기가 못 찾았다 — **응답 뒤에** 남긴다. 걸러질 말이면 아무것도 안 한다."""
    word = clean(text or "")
    if skip_reason(word) is not None:
        return
    background.add_task(record, scope, word, via, asker_of(user.id))


def record(scope: str, text: str, via: str, asker: str) -> None:
    """따로 연 세션으로 남긴다 — **던지지 않는다**(찾기는 이미 답했다)."""
    try:
        with SessionLocal() as db:
            if remember(db, scope, text, via, asker):
                db.commit()
            _maybe_purge(db)
    except Exception:
        log.warning("못 찾은 말을 남기지 못했습니다 — 찾기 응답과는 무관합니다", exc_info=True)


def scope_types(db: Session, scope: str) -> list[ObjectType] | None:
    """그 범위의 타입들 — 비면 객체를 갖는 타입 전부. 모르는 범위 · 원 표를 비추는 타입은
    None(별칭을 붙일 수 없다)."""
    if not scope:
        return [
            one
            for one in db.scalars(select(ObjectType).where(ObjectType.is_active.is_(True)))
            if not system.is_system(one)
        ]
    found = find_scope(db, scope)
    if found is None or not found.types:
        return None
    kinds = [one for one in found.types if not system.is_system(one)]
    return kinds or None


def _known(db: Session, type_ids: list[uuid.UUID], norm: str, text: str) -> bool:
    """그 말이 **있기는 한가** — 보이는지와 상관없이. 있으면 못 찾은 까닭은 이름이 아니라
    권한(부서 밖)이고, 별칭으로 고칠 일이 아니다.

    타입 목록을 꼭 준다 — 인덱스(`type_id` 가 앞인 이름 · 식별자 · 별칭 인덱스)를 타게."""
    if not type_ids:
        return False
    alive = (ObjectInstance.type_id.in_(type_ids), ObjectInstance.deleted_at.is_(None))
    by_label = select(ObjectInstance.id).where(
        *alive, func.lower(ObjectInstance.label) == text.lower()
    )
    # 식별자는 비교키 인덱스로(`resolve.by_name` 과 같은 식).
    by_key = select(ObjectInstance.id).where(
        *alive,
        ObjectInstance.key.isnot(None),
        literal_column(NORMALIZED_KEY_SQL.replace("key", "objects.key"), String)
        == unicodedata.normalize("NFKC", clean(text)).lower(),
    )
    by_alias = select(ObjectAlias.id).where(
        ObjectAlias.type_id.in_(type_ids), ObjectAlias.norm == norm
    )
    return any(db.scalar(stmt.limit(1)) is not None for stmt in (by_label, by_key, by_alias))


def remember(db: Session, scope: str, text: str, via: str, asker: str) -> bool:
    """한 줄 upsert — 있으면 횟수 · 마지막 · 사람 · 자리만. **부르는 쪽이 커밋한다.**

    남겼으면 True. 모르는 범위 · 있는데 안 보였던 말이면 False."""
    types = scope_types(db, scope)
    if types is None:
        return False
    norm = compare_key(text)[:200]
    if _known(db, [one.id for one in types], norm, text):
        return False
    table = SearchMiss.__table__
    stmt = pg_insert(SearchMiss).values(
        id=uuid.uuid4(),
        scope=scope,
        norm=norm,
        text=text[:200],
        hits=1,
        askers=[asker],
        vias=[via],
        status="pending",
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_search_misses_scope_norm",
        set_={
            "hits": table.c.hits + 1,
            "last_at": func.now(),
            "askers": case(
                (
                    or_(
                        table.c.askers.has_key(asker),
                        func.jsonb_array_length(table.c.askers) >= ASKERS_MAX,
                    ),
                    table.c.askers,
                ),
                else_=table.c.askers.op("||", return_type=JSONB)(
                    func.jsonb_build_array(asker)
                ),
            ),
            "vias": case(
                (table.c.vias.has_key(via), table.c.vias),
                else_=table.c.vias.op("||", return_type=JSONB)(func.jsonb_build_array(via)),
            ),
            # 붙였던 말을 **또** 못 찾았다 — 그 별칭이 빠졌다(있으면 위에서 「있다」 로
            # 걸렀다). 다시 대기로. 무시한 말은 무시한 채 횟수만 오른다.
            "status": case((table.c.status == "attached", "pending"), else_=table.c.status),
        },
    )
    db.execute(stmt)
    # 치는 중의 글자 — 방금 한 번 남은 말이 이 말의 앞부분이면 지운다.
    db.execute(
        delete(SearchMiss).where(
            SearchMiss.scope == scope,
            SearchMiss.status == "pending",
            SearchMiss.hits == 1,
            SearchMiss.norm != norm,
            SearchMiss.last_at > func.now() - timedelta(seconds=TYPING_SECONDS),
            func.starts_with(literal(norm, String), SearchMiss.norm),
        )
    )
    return True


_last_purge = [0.0]


def _maybe_purge(db: Session) -> None:
    """정리는 **프로세스마다 한 시간에 한 번**, 남기는 길에 얹는다 — 따로 타이머를 두면 그것이
    멎은 날 표가 자라는 것을 아무도 모른다."""
    now = time.monotonic()
    if _last_purge[0] and now - _last_purge[0] < PURGE_EVERY:
        return
    _last_purge[0] = now
    gone = purge(db)
    db.commit()
    if gone:
        log.info("못 찾은 말 정리: %d줄", gone)


def purge(db: Session) -> int:
    """오래된 것을 지운다 — 지운 줄 수. **부르는 쪽이 커밋한다.**

    처리한 것(붙임 · 무시)은 90일, 몇 번 안 찾은 대기는 180일 아무도 안 찾으면 간다. 계속 찾는
    말은 남는다 — 무시한 말을 또 찾아도 무시한 채로 남아야 다시 안 뜬다. 지운 타입의 줄도 간다.
    """
    total = 0
    total += extensions.rows_changed(
        db.execute(
            delete(SearchMiss).where(
                SearchMiss.status != "pending",
                SearchMiss.last_at < func.now() - timedelta(days=KEEP_DECIDED_DAYS),
            )
        )
    )
    total += extensions.rows_changed(
        db.execute(
            delete(SearchMiss).where(
                SearchMiss.status == "pending",
                SearchMiss.hits < RARE_HITS,
                SearchMiss.last_at < func.now() - timedelta(days=KEEP_RARE_DAYS),
            )
        )
    )
    known = union(select(ObjectType.slug), select(ObjectInterface.slug))
    total += extensions.rows_changed(
        db.execute(
            delete(SearchMiss).where(SearchMiss.scope != "", SearchMiss.scope.not_in(known))
        )
    )
    waiting = int(db.scalar(select(func.count()).where(SearchMiss.status == "pending")) or 0)
    if waiting > PENDING_MAX:
        victims = (
            select(SearchMiss.id)
            .where(SearchMiss.status == "pending")
            .order_by(SearchMiss.hits, SearchMiss.last_at)
            .limit(waiting - PENDING_MAX)
        )
        total += extensions.rows_changed(
            db.execute(delete(SearchMiss).where(SearchMiss.id.in_(victims.scalar_subquery())))
        )
    return total


# --- 보기 -----------------------------------------------------------------------


@dataclass
class Suggestion:
    id: uuid.UUID
    type_slug: str
    type_label: str
    label: str
    key: str | None
    matched: str
    """label · key · alias — 어디가 비슷했나."""
    matched_text: str
    score: float


@dataclass
class Candidate:
    row: SearchMiss
    scope_label: str
    scope_kind: str
    """`type` · `interface` · 빈 값(통합 검색)."""
    object_label: str | None = None
    object_type_slug: str | None = None
    suggestions: list[Suggestion] = field(default_factory=list)
    suggest_note: str = ""
    """제안을 못 낸 까닭 — 「기록 타입」 · 「객체가 많아 건너뜀」."""


def require_reviewer(db: Session, user: User) -> None:
    """후보를 보고 처리하는 것은 **부서 관리자 이상**이다 — 붙이는 일이 객체를 고치는 일이라,
    못 고치는 사람에게 목록을 주면 못 지우는 숫자가 된다(홈 「남은 일」 과 같은 문턱)."""
    if not is_any_manager(db, user):
        raise Forbidden(
            code("OBJECTS", 111),
            "별칭 후보는 부서 관리자 이상이 봅니다 — 별칭을 붙이는 것은 객체를 수정하는 "
            "일입니다.",
        )


def _labels(db: Session) -> dict[str, tuple[str, str]]:
    out = {
        slug: (label, "interface")
        for slug, label in db.execute(select(ObjectInterface.slug, ObjectInterface.label))
    }
    out.update(
        {
            slug: (label, "type")
            for slug, label in db.execute(select(ObjectType.slug, ObjectType.label))
        }
    )
    return out


def page(
    db: Session,
    user: User,
    *,
    scope: str | None,
    status: str,
    limit: int,
    offset: int,
    suggest: bool,
) -> tuple[list[Candidate], int]:
    """후보 한 쪽 — **많이 · 여럿이 찾은 것부터.** 전체 수와 함께."""
    base = select(SearchMiss)
    if status != "all":
        base = base.where(SearchMiss.status == status)
    if scope is not None:
        base = base.where(SearchMiss.scope == scope)
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = list(
        db.scalars(
            base.order_by(
                SearchMiss.hits.desc(),
                func.jsonb_array_length(SearchMiss.askers).desc(),
                SearchMiss.last_at.desc(),
                SearchMiss.id,
            )
            .limit(limit)
            .offset(offset)
        )
    )
    labels = _labels(db)
    return [describe(db, user, one, suggest=suggest, labels=labels) for one in rows], total


def describe(
    db: Session,
    user: User,
    row: SearchMiss,
    *,
    suggest: bool,
    labels: dict[str, tuple[str, str]] | None = None,
) -> Candidate:
    known = labels if labels is not None else _labels(db)
    label, kind = known.get(row.scope, ("통합 검색" if not row.scope else row.scope, ""))
    out = Candidate(row=row, scope_label=label, scope_kind=kind if row.scope else "")
    if row.scope and row.scope not in known:
        out.scope_label = f"{row.scope}(없는 타입)"
    if row.object_id is not None:
        found = db.execute(
            select(ObjectInstance.label, ObjectType.slug)
            .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
            .where(
                ObjectInstance.id == row.object_id,
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        ).first()
        if found is not None:
            out.object_label, out.object_type_slug = found[0], found[1]
    if suggest and row.status == "pending":
        out.suggestions, out.suggest_note = suggestions(db, user, row)
    return out


def suggestions(db: Session, user: User, row: SearchMiss) -> tuple[list[Suggestion], str]:
    """「이것 아닐까」 — 그 범위의 **축** 타입에서 이름 · 식별자 · 별칭이 비슷한 것(pg_trgm).

    기록 타입(시장 서비스 건 · 시험 결과)에는 제안하지 않는다 — 별칭은 사람이 이름으로 부르는
    것(모델 · 부품 · 공급사)에 붙는다. **짐작이다** — 사람이 고른 것만 붙는다."""
    types = scope_types(db, row.scope) or []
    axes = [one for one in types if one.usage == "axis"]
    if not axes:
        return [], "기록 타입이라 제안하지 않습니다 — 별칭은 이름으로 부르는 축에 붙습니다."
    ids = [one.id for one in axes]
    size = int(
        db.scalar(
            select(func.count()).where(
                ObjectInstance.type_id.in_(ids), ObjectInstance.deleted_at.is_(None)
            )
        )
        or 0
    )
    if size > SUGGEST_MAX_ROWS:
        return [], (
            f"찾을 객체가 {size:,}건이라 제안을 건너뛰었습니다 — 타입을 정해 찾거나 "
            "객체를 직접 고르세요."
        )
    norm = row.norm
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    alive = (ObjectInstance.type_id.in_(ids), ObjectInstance.deleted_at.is_(None), seen)
    label_score = func.greatest(
        func.similarity(func.lower(ObjectInstance.label), norm),
        func.word_similarity(norm, func.lower(ObjectInstance.label)),
    )
    key_score = func.coalesce(func.similarity(func.lower(ObjectInstance.key), norm), 0.0)
    best: dict[uuid.UUID, tuple[float, str, str, ObjectInstance]] = {}
    for one, by_label, by_key in db.execute(
        select(ObjectInstance, label_score, key_score)
        .where(*alive, func.greatest(label_score, key_score) >= SIMILARITY_MIN)
        .order_by(func.greatest(label_score, key_score).desc(), ObjectInstance.label)
        .limit(SUGGEST)
    ):
        if by_key > by_label:
            best[one.id] = (float(by_key), "key", one.key or "", one)
        else:
            best[one.id] = (float(by_label), "label", "", one)
    alias_score = func.greatest(
        func.similarity(func.lower(ObjectAlias.value), norm),
        func.word_similarity(norm, func.lower(ObjectAlias.value)),
    )
    for one, value, score in db.execute(
        select(ObjectInstance, ObjectAlias.value, alias_score)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(ObjectAlias.type_id.in_(ids), *alive, alias_score >= SIMILARITY_MIN)
        .order_by(alias_score.desc())
        .limit(SUGGEST)
    ):
        if float(score) > best.get(one.id, (0.0, "", "", one))[0]:
            best[one.id] = (float(score), "alias", value, one)
    by_id = {one.id: one for one in axes}
    ranked = sorted(best.values(), key=lambda got: (-got[0], got[3].label))[:SUGGEST]
    return [
        Suggestion(
            id=one.id,
            type_slug=by_id[one.type_id].slug,
            type_label=by_id[one.type_id].label,
            label=one.label,
            key=one.key,
            matched=how,
            matched_text=text,
            score=round(score, 2),
        )
        for score, how, text, one in ranked
    ], ""


def get(db: Session, miss_id: uuid.UUID) -> SearchMiss:
    row = db.get(SearchMiss, miss_id)
    if row is None:
        raise NotFound(
            code("OBJECTS", 112), "그 별칭 후보가 없습니다 — 이미 정리됐을 수 있습니다."
        )
    return row


# --- 붙이기 · 무시 ----------------------------------------------------------------


@dataclass
class AttachPlan:
    miss: SearchMiss
    target: ObjectInstance
    object_type: ObjectType
    value: str
    before: list[str]
    after: list[str]
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    """이것이 있으면 붙이지 않는다 — 사람이 먼저 할 일."""


def plan_attach(
    db: Session, user: User, miss: SearchMiss, object_id: uuid.UUID, value: str | None
) -> AttachPlan:
    """그 말을 이 객체의 별칭으로 붙이면 **무엇이 바뀌나.** 아무것도 안 바꾼다.

    권한(그 객체를 고칠 수 있나)과 범위(그 말을 찾은 타입의 객체인가)는 여기서 거절한다 —
    계획을 보여 준 뒤에 막히면 사람은 왜 보여 줬는지 묻는다."""
    target = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if target is None:
        raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")
    object_type = db.get(ObjectType, target.type_id)
    if object_type is None:  # pragma: no cover - FK 가 막는다
        raise NotFound(code("OBJECTS", 10), "타입을 찾을 수 없습니다.")
    allowed = scope_types(db, miss.scope) or []
    if object_type.id not in {one.id for one in allowed}:
        where = describe(db, user, miss, suggest=False).scope_label
        raise AppError(
            code("OBJECTS", 113),
            f"「{miss.text}」 은(는) {where}에서 못 찾은 말입니다 — "
            f"{object_type.label}의 객체에는 붙이지 않습니다(그 범위의 객체를 고르세요).",
            status=422,
        )
    managed.require_objects_editable(object_type, what="별칭을 붙이지")
    require_owner_edit(
        db, user, target.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
    )
    word = clean(value if value is not None else miss.text)
    if not word or len(word) > aliases.MAX_VALUE:
        raise AppError(
            code("OBJECTS", 89),
            f"별칭은 1~{aliases.MAX_VALUE}자입니다: 「{word[:40]}」",
            status=422,
        )
    norm = compare_key(word)
    before = aliases.human_of(db, [target.id]).get(target.id, [])
    plan = AttachPlan(
        miss=miss,
        target=target,
        object_type=object_type,
        value=word,
        before=before,
        after=list(before),
    )
    if norm in {compare_key(one) for one in before}:
        plan.warnings.append("이미 이 객체의 별칭입니다 — 붙일 것이 없습니다.")
    else:
        plan.after = [*before, word]
    holder = aliases.taken_by(db, object_type, aliases.HUMAN, [word]).get(norm)
    if holder is not None and holder != target.id:
        other = db.get(ObjectInstance, holder)
        plan.blocking.append(
            f"「{word}」 은(는) 이미 {other.label if other else '다른 객체'}의 별칭입니다 — "
            "같은 별칭이 둘이면 어느 쪽인지 아무도 모릅니다. 그쪽 별칭을 먼저 정리하세요."
        )
    if norm in (compare_key(target.label), compare_key(target.key or "")):
        plan.warnings.append(
            "이 객체의 이름(식별자)과 같습니다 — 찾은 사람에게 안 보였을 수 있습니다(부서 밖)."
        )
    clash = db.scalar(
        select(ObjectInstance.label).where(
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.id != target.id,
            ObjectInstance.deleted_at.is_(None),
            or_(
                func.lower(ObjectInstance.label) == word.lower(),
                func.lower(ObjectInstance.key) == word.lower(),
            ),
        )
    )
    if clash is not None:
        plan.warnings.append(
            f"같은 타입의 「{clash}」 의 이름(식별자)과 같습니다 — 그 표기로 찾으면 둘이 "
            "나옵니다(데이터 품질의 「별칭이 다른 객체의 이름과 같음」)."
        )
    return plan


def attach(db: Session, user: User, plan: AttachPlan) -> int:
    """계획대로 붙이고 그 말을 「별칭으로 붙임」 으로 — 함께 닫은 줄 수(같은 말을 통합 검색 ·
    인터페이스에서 못 찾은 줄도 이제 찾힌다). **부르는 쪽이 커밋한다.**"""
    if plan.blocking:
        raise Conflict(code("OBJECTS", 114), plan.blocking[0])
    miss, target, object_type = plan.miss, plan.target, plan.object_type
    before, after = aliases.set_human(
        db,
        target,
        object_type,
        [
            aliases.Incoming(
                value=plan.value, source="별칭 후보", note=f"못 찾은 말 · {miss.hits}번"
            )
        ],
        mode="add",
        verified=user,
    )
    if before != after:
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=target.id,
            target_label=f"{object_type.slug}:{target.label}",
            workspace_id=target.owner_workspace_id,
            changes={"aliases": {"before": before, "after": after}},
            reason=f"별칭 후보 — 못 찾은 말 「{miss.text}」 을(를) 별칭으로",
        )
    reach = interfaces.load_ends(db).reach_of(object_type.slug)
    now = datetime.now(UTC)
    closed = 0
    for one in db.scalars(
        select(SearchMiss).where(
            SearchMiss.norm == compare_key(plan.value),
            SearchMiss.status == "pending",
            SearchMiss.scope.in_(["", *reach]),
        )
    ):
        if one.id != miss.id:
            closed += 1
        _decide(one, "attached", user, now, target.id)
    _decide(miss, "attached", user, now, target.id)
    return closed


def _decide(
    row: SearchMiss, status: str, user: User | None, now: datetime, object_id: Any = None
) -> None:
    row.status = status
    row.decided_by_id = user.id if user is not None else None
    row.decided_at = now if user is not None else None
    row.object_id = object_id


def decide(
    db: Session, user: User, ids: list[uuid.UUID], action: str
) -> tuple[int, list[str]]:
    """무시(`ignore`)하거나 대기로 되돌린다(`restore`) — `(한 것, 못 한 줄의 이유)`.
    **부르는 쪽이 커밋한다.** 하나가 막혀도 나머지는 간다."""
    rows = {
        one.id: one for one in db.scalars(select(SearchMiss).where(SearchMiss.id.in_(ids)))
    }
    refused = [f"{one} 은(는) 이미 없습니다" for one in ids if one not in rows]
    now = datetime.now(UTC)
    done = 0
    for one in rows.values():
        if action == "ignore":
            if one.status == "attached":
                refused.append(f"「{one.text}」: 이미 별칭으로 붙인 말입니다")
                continue
            _decide(one, "ignored", user, now)
        else:
            _decide(one, "pending", None, now)
        done += 1
    return done, refused


# --- 홈 「남은 일」 ----------------------------------------------------------------


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**여러 번 찾았는데 못 찾은 이름** — 부서 관리자 이상에게. 한 번 친 오타까지 세지 않는다
    (`REPEAT_MIN`) — 그 수는 늘 크고, 늘 큰 수는 아무도 안 읽는다."""
    if not is_any_manager(db, viewer):
        return []
    count = int(
        db.scalar(
            select(func.count()).where(
                SearchMiss.status == "pending", SearchMiss.hits >= REPEAT_MIN
            )
        )
        or 0
    )
    return [
        extensions.MaintenanceItem(
            key="alias_candidates",
            label="여러 번 찾았는데 못 찾은 이름(별칭 후보)",
            count=count,
            link="/quality#alias_candidates",
        )
    ]


def pending_by_scope(db: Session) -> dict[str, int]:
    """범위마다 대기 수 — 채울 곳(`fill.py`)이 「별칭 없는 객체」 의 무게로 쓴다."""
    return {
        scope: int(count)
        for scope, count in db.execute(
            select(SearchMiss.scope, func.count())
            .where(SearchMiss.status == "pending")
            .group_by(SearchMiss.scope)
        )
    }
