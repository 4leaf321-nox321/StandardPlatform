"""검토함 — **모으고, 고르고, 미리 보고, 적용하고, 기록한다**(ADR 0027).

항목은 제공자가 원래 자리에서 읽는다(`registry.py`). 여기는 종류를 모르고 그 위의 일만 한다:

    list      제공자마다 모아 거르고(종류 · 타입 · 부서 · 확신도 · 처리할 수 있는 것)
              세우고 묶는다
    plan      결정들이 무엇이 되나 — 「추천대로 모두」 면 문턱 이상을 고르고, 막히는 것은
              따로 센다. **아무것도 안 바꾼다.** 지문을 준다
    apply     같은 결정을 다시 계획해 지문이 같을 때만 — 항목마다 원래 기능으로 처리하고
              결정 기록 한 줄과 함께 **항목마다 커밋한다**(하나가 막혀도 나머지는 간다).
              크면 작업으로 간다
    undo      묶음 하나를 — 종류마다 원래 기능의 되돌리기로
    accuracy  사람이 추천을 따랐나 — 종류 · 확신도 구간마다

## 왜 항목마다 커밋하나

결정 여럿 중 하나가 그 사이 막히면(남이 먼저 처리 · 권한이 바뀜) 나머지까지 물리면 사람은 그
목록을 다시 안 본다. 그리고 병합은 스스로 커밋한다(`lifecycle.merge_into`) — 묶음 전체를 한
트랜잭션으로 둘 수가 없다. 그래서 항목 하나 = 처리 + 결정 기록 한 줄 = 커밋 하나. 작업 안에서는
그 커밋마다 워커의 검사(「아직 내가 쥔 작업인가」)가 돈다(`work.db`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import String, case, cast, func, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.review import registry
from app.modules.review.models import REVIEW_MODES, ReviewAutoRule, ReviewDecision
from app.modules.review.registry import IGNORE, Filters, Item, Provider
from app.modules.workspaces.models import Workspace, WorkspaceMember
from app.shared import querycap, readguard
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from app.shared.permissions import is_any_manager, workspace_by_slug

log = logging.getLogger(__name__)

#: 「추천대로 모두」 의 기본 문턱 — 사람이 바꾼다.
DEFAULT_THRESHOLD = 0.9
#: 이보다 많은 결정 · 줄이면 요청이 아니라 작업으로 간다(화면이 멈추지 않게). 줄의 문턱은
#: 요청 하나를 3초 안에 두는 수다 — 지워진 것을 가리키는 칸은 바뀌는 칸만 검사하고 덩어리로
#: 쓰는데(`objects/review.py`), 200만 건 DB 에서 1,000행 0.42초 · 1만 행 4.8초(줄당 약 0.5ms)라
#: 5,000행이면 미리 보기 검사까지 2.7초 남짓이다(2026-10-10 실측). 예전 길(일괄 수정 — 줄당
#: 약 5ms)일 때는 같은 3초가 500행이었다.
INLINE_DECISIONS = 100
INLINE_ROWS = 5000
#: 한 번에 계획하는 결정의 상한 — 넘으면 거르기를 좁히라고 말한다.
MAX_DECISIONS = 5000
#: 미리 보기 응답에 싣는 결과 줄 — 수는 전부 센다.
SHOWN_OUTCOMES = 500
#: 부서 거르기에서 「전역」 을 뜻하는 값 — slug 에는 `@` 가 없다.
GLOBAL = "@global"
#: 검토함 한 쪽을 모으는 시간(초) — 프록시가 먼저 끊지 않게(흔히 60초) 넉넉히 짧게. 종류
#: 하나만 볼 때(`kind`)는 그 종류가 이것을 다 쓴다. 운영에서 별칭 후보 하나가 60초를 넘겨
#: 검토함 전체가 504 였다(2026-11-16) — 종류마다 원래 자리에서 읽으니 어느 하나가 느려지면
#: 나머지까지 못 본다. 「추천대로 모두」 · 「거른 전부」 의 미리 보기는 목록을 읽고(이
#: 시간까지) 고른 항목을 한 번 더 읽어(대개 비슷한 시간) 이것의 두 배 남짓이 든다 — 그래서
#: 15초.
COLLECT_SECONDS = 15.0
#: 여러 종류를 함께 볼 때 종류 하나가 쓰는 시간(초) — 하나가 느려도 나머지가 선다.
KIND_SECONDS = 8.0
#: 시간이 다 된 종류를 끊기까지 더 기다리는 시간(초). 줄마다 일하는 종류는 시간이 다 되면
#: 스스로 멈추고 거기까지 낸다(`Filters.deadline` — 하던 줄은 마친다). 그러지 못하는 것(큰
#: 질의 하나 · 멈출 자리가 없는 되풀이)은 이만큼 뒤에 도는 질의를 취소하고 그 종류를 건너뛴다.
KIND_GRACE = 4.0

MODES = ("decisions", "recommended", "all")
#: 「전부 작업으로」(ADR 0075)가 한 번에 정하는 결정의 상한 — 넘으면 거르기로 나눈다.
BULK_MAX_DECISIONS = 50_000
#: 자동 확인(ADR 0075) — 문턱의 아래 끝 · 근거가 되는 결정 수(사람이 하나씩 정한 것) · 그
#: 구간에서 추천대로 한 몫 · 밤마다 상한.
AUTO_MIN_THRESHOLD = 0.9
AUTO_MIN_DECIDED = 30
AUTO_MIN_RATE = 0.95
AUTO_MAX = 1000
#: 자동 확인의 근거로 세는 결정 — 사람이 하나씩 본 것만. 「추천대로 모두」 · 「거른 전부」 ·
#: 자동은 늘 추천을 「따른」 것으로 남아, 세면 정확도가 저절로 오른다.
AUTO_EVIDENCE_MODES = ("one", "selected")


def require_viewer(db: Session, user: User) -> None:
    """검토함은 **부서 관리자 이상**이다 — 판단을 처리하는 것은 자료를 고치는 일이라, 못 고치는
    사람에게 목록을 주면 못 지우는 숫자가 된다(데이터 품질 · 별칭 후보와 같은 문턱)."""
    if not is_any_manager(db, user):
        raise Forbidden(
            code("REVIEW", 1),
            "검토함은 부서 관리자 이상이 봅니다 — 판단을 처리하는 것은 자료를 수정하는 "
            "일입니다.",
        )


# --- 모으기 ---------------------------------------------------------------------


@dataclass
class KindSummary:
    provider: Provider
    total: int
    """원래 자리에 있는 수."""
    matched: int
    """읽은 것 중 거르기에 맞는 수."""
    scanned: int
    notes: list[str] = field(default_factory=list)
    late: bool = False
    """시간 안에 다 읽지 못했다 — 앞의 것만(`Collected.cut`) 또는 하나도 못 읽고 건너뜀.
    「없다」 가 아니다."""
    seconds: float = 0.0
    """이 종류를 모으는 데 든 시간 — 어느 종류가 느린지 보인다."""


@dataclass
class Listed:
    kinds: list[KindSummary]
    items: list[Item]
    notes: list[str]


@dataclass(frozen=True)
class Query:
    kind: str | None = None
    type_slug: str | None = None
    workspace: str | None = None
    """부서 slug · `GLOBAL`(전역만) · None(전부)."""
    min_confidence: float | None = None
    max_confidence: float | None = None
    actionable: bool = False
    """처리할 수 있는 것만 — 처리 길이 있고 막히지 않은 것."""
    recommendation: str | None = None
    """`with` — 추천이 있는 것만 · `without` — 추천이 없는 것만(ADR 0075). 별칭 후보에서 추천이
    없는 줄은 글자가 비슷한 객체가 없는 말이다 — 대개 별칭 감이 아니지만, 글자가 다른 이름
    (「앤시스」 → Ansys)일 수 있다."""


def filters_of(db: Session, query: Query) -> Filters:
    if query.workspace == GLOBAL:
        return Filters(type_slug=query.type_slug, workspace_global=True)
    if query.workspace:
        return Filters(
            type_slug=query.type_slug, workspace_id=workspace_by_slug(db, query.workspace).id
        )
    return Filters(type_slug=query.type_slug)


def _chosen(kind: str | None) -> list[Provider]:
    if kind is None:
        return registry.providers()
    found = registry.get(kind)
    if found is None:
        known = ", ".join(one.kind for one in registry.providers())
        raise AppError(
            code("REVIEW", 2), f"모르는 종류입니다: {kind} — 있는 것: {known}", status=422
        )
    return [found]


def _passes(item: Item, provider: Provider, flt: Filters, query: Query) -> bool:
    if flt.workspace_global and item.workspace_id is not None:
        return False
    if flt.workspace_id is not None and item.workspace_id != flt.workspace_id:
        return False
    if query.min_confidence is not None and (
        item.confidence is None or item.confidence < query.min_confidence
    ):
        return False
    if query.max_confidence is not None and (
        item.confidence is None or item.confidence >= query.max_confidence
    ):
        return False
    if query.recommendation == "with" and item.recommended is None:
        return False
    if query.recommendation == "without" and item.recommended is not None:
        return False
    return not (query.actionable and (provider.check is None or item.blocked))


class _Late(Exception):
    """종류 하나가 끊을 시각을 넘겼다 — 그 종류가 읽은 것은 버렸다."""


#: DB 의 질의 상한을 끊는 신호보다 이만큼(초) 늦게 둔다 — 보통은 신호가 먼저 끊고, 신호가 안
#: 닿은 날에만 DB 가 끊는다.
DB_CAP_MARGIN = 0.5


def _within[T](db: Session, cutoff: float, work: Callable[[], T]) -> T:
    """`work` 를 `cutoff`(`time.monotonic()`)까지만 — 넘기면 도는 질의를 취소하고 다음 질의를
    보내지 않는다(`readguard` 의 길 — 큰 질의 하나도, 짧은 질의를 여럿 부르는 되풀이도
    끊긴다). 세이브포인트 안에서 돌려 끊겨도 요청의 트랜잭션은 산다. 끊기면 `_Late`.

    **끊는 신호가 안 닿는 날에도 멈춘다.** 신호는 「지금 도는 질의」 를 끊는 것이라 질의 사이에
    닿으면 아무 일도 없고, 취소 연결이 막히면 닿지 않는다 — 그러면 큰 질의 하나가 DB 의 질의
    상한(`read_query_seconds`, 60초)까지 돌아 앞단이 504 를 냈다. 그래서 (1) 신호를 끝날
    때까지 `readguard.POLL` 마다 다시 보내고(`readguard.run` 과 같다), (2) 세이브포인트
    안에서 DB 의 질의 상한을 남은 시간(+ `DB_CAP_MARGIN`)으로 낮춰 DB 가 스스로 끊게 한다.
    끝나면 원래 상한으로 돌린다(끊기면 세이브포인트를 되돌리며 함께 돌아간다)."""
    nested = db.begin_nested()
    lock = threading.Lock()
    finished = threading.Event()
    guard: readguard.Guard | None = None
    previous = _statement_cap(db)
    capped = _cap_statements(db, cutoff, previous)
    try:
        with readguard.watching() as guard:
            mine = guard

            def fire() -> None:
                # 시간이 되면 끝날 때까지 다시 보낸다 — 끝난 뒤에는 보내지 않는다(다음 종류의
                # 질의가 맞을 수 있다).
                if finished.wait(max(cutoff - time.monotonic(), 0.0)):
                    return
                while True:
                    with lock:
                        if finished.is_set():
                            return
                        readguard.stop(mine, "time")
                    if finished.wait(readguard.POLL):
                        return

            timer = threading.Thread(target=fire, name="review-cutoff", daemon=True)
            timer.start()
            try:
                result = work()
            finally:
                with lock:
                    finished.set()
            if guard.why is not None:
                # 마지막 질의가 끝난 바로 뒤에 시간이 됐다 — 끊긴 것과 같이 본다(같은 요청에
                # 같은 답이 나게).
                raise _Late
    except BaseException as caught:
        # 끊긴 질의 뒤의 트랜잭션은 실패 상태다 — 세이브포인트로 돌려야 다음 종류가 읽는다.
        # `watching()` 을 나온 뒤라 이 되돌리기는 막히지 않는다. 낮춘 상한도 함께 돌아간다.
        nested.rollback()
        timed_out = guard is not None and guard.why is not None
        if isinstance(caught, _Late) or (
            (timed_out or capped)
            and (
                isinstance(caught, readguard.Stopped)
                or (isinstance(caught, OperationalError) and querycap.cancelled(caught))
            )
        ):
            raise _Late from caught
        raise
    if capped:
        _set_statement_cap(db, previous)
    nested.commit()
    return result


def _statement_cap(db: Session) -> int:
    """지금 이 세션의 DB 질의 상한(ms, 0 이면 없음) — `pg_settings` 는 단위를 ms 로 준다."""
    return int(
        db.execute(
            text("SELECT setting::int FROM pg_settings WHERE name = 'statement_timeout'")
        ).scalar_one()
    )


def _cap_statements(db: Session, cutoff: float, previous: int) -> bool:
    """DB 의 질의 상한을 남은 시간(+ 여유)으로 낮춘다 — 원래 상한이 더 짧으면 그대로.
    낮췄나."""
    left = max(1, round((cutoff - time.monotonic() + DB_CAP_MARGIN) * 1000))
    if previous and previous <= left:
        return False
    _set_statement_cap(db, left)
    return True


def _set_statement_cap(db: Session, ms: int) -> None:
    db.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": str(int(ms))})


def _late_note(provider: Provider, took: float, alone: bool) -> str:
    if alone:
        return (
            f"「{provider.label}」 을(를) {took:.0f}초 안에 읽지 못했습니다 — 원래 화면에서 "
            "보거나, 계속되면 운영 담당자에게 알려 주세요(서버 로그에 종류와 시간이 "
            "남습니다)."
        )
    return (
        f"「{provider.label}」 은(는) {took:.0f}초 안에 읽지 못해 이번에는 건너뛰었습니다 — "
        "다른 종류는 그대로 보입니다. 이 종류만 골라 보면 시간을 더 씁니다."
    )


def collect(db: Session, user: User, query: Query, *, whole: bool = False) -> Listed:
    """종류마다 원래 자리에서 읽어 거른다 — **볼 수 있는 종류만.** 못 보는 종류는 수도 안
    말한다 (데이터 소스는 시스템 관리자의 것이다).

    **종류마다 시간이 있다** — 여럿을 함께 보면 하나에 `KIND_SECONDS`, 다 합쳐
    `COLLECT_SECONDS`(종류 하나만 보면 그것을 다 쓴다). 시간이 다 되면 줄마다 일하는 종류는
    거기까지 내고(`Collected.cut`), 스스로 멈추지 못하는 종류는 `KIND_GRACE` 뒤에 끊고
    건너뛴다. 어느 쪽이든 그 종류는 `late` 이고 까닭은 그 종류의 `notes` 에 선다 — 한 종류가
    느려도 나머지는 보인다.

    `whole` 이면(작업 안 — 「전부 작업으로」 · 자동 확인, ADR 0075) 시간을 안 보고 상한 없이
    읽는다(`Filters.whole` — 지원하는 종류만, 나머지는 평소 상한)."""
    flt = filters_of(db, query)
    kinds: list[KindSummary] = []
    items: list[Item] = []
    notes: list[str] = []
    alone = query.kind is not None
    until = time.monotonic() + COLLECT_SECONDS
    for provider in _chosen(query.kind):
        if not provider.can_view(db, user):
            if alone:
                notes.append(f"「{provider.label}」 은(는) 볼 수 없는 종류입니다.")
            continue
        started = time.monotonic()
        if whole:
            got = provider.collect(db, user, dataclasses.replace(flt, whole=True))
            mine = [one for one in got.items if _passes(one, provider, flt, query)]
            mine.sort(key=lambda one: (-one.priority, one.question))
            kinds.append(
                KindSummary(
                    provider=provider,
                    total=got.total,
                    matched=len(mine),
                    scanned=len(got.items),
                    notes=got.notes,
                    seconds=round(time.monotonic() - started, 2),
                )
            )
            items.extend(mine)
            continue
        if started >= until:
            kinds.append(
                KindSummary(
                    provider=provider,
                    total=0,
                    matched=0,
                    scanned=0,
                    late=True,
                    notes=[
                        f"앞의 종류들이 {COLLECT_SECONDS:.0f}초를 다 써 「{provider.label}」 "
                        "은(는) 이번에 읽지 않았습니다 — 이 종류만 골라 보세요."
                    ],
                )
            )
            continue
        soft = until if alone else min(started + KIND_SECONDS, until)
        timed = dataclasses.replace(flt, deadline=soft)
        try:
            got = _within(db, soft + KIND_GRACE, partial(provider.collect, db, user, timed))
        except _Late:
            took = time.monotonic() - started
            log.warning(
                "검토함: 「%s」(%s)를 %.1f초 안에 읽지 못해 건너뜀",
                provider.label,
                provider.kind,
                took,
            )
            kinds.append(
                KindSummary(
                    provider=provider,
                    total=0,
                    matched=0,
                    scanned=0,
                    late=True,
                    seconds=round(took, 2),
                    notes=[_late_note(provider, took, alone)],
                )
            )
            continue
        took = time.monotonic() - started
        if got.cut:
            log.warning(
                "검토함: 「%s」(%s)는 %.1f초에 %d건만 읽음(전체 %d)",
                provider.label,
                provider.kind,
                took,
                len(got.items),
                got.total,
            )
        mine = [one for one in got.items if _passes(one, provider, flt, query)]
        mine.sort(key=lambda one: (-one.priority, one.question))
        kinds.append(
            KindSummary(
                provider=provider,
                total=got.total,
                matched=len(mine),
                scanned=len(got.items),
                notes=got.notes,
                late=got.cut,
                seconds=round(took, 2),
            )
        )
        items.extend(mine)
    return Listed(kinds=kinds, items=items, notes=notes)


def late_notes(listed: Listed) -> list[str]:
    """시간 안에 다 못 읽은 종류 — 「거른 전부」 · 「추천대로 모두」 가 그 종류에서는 보인 것만
    고른다고 말한다."""
    return [
        f"「{one.provider.label}」 은(는) 시간 안에 다 읽지 못해 읽은 것"
        f"({one.scanned:,}건)에서만 골랐습니다."
        if one.scanned
        else f"「{one.provider.label}」 은(는) 시간 안에 읽지 못해 이번에는 고르지 않았습니다."
        for one in listed.kinds
        if one.late
    ]


def grouped(items: list[Item]) -> list[Item]:
    """「같은 것끼리 묶기」 — 같은 종류 · 같은 묶음 열쇠(같은 말 · 같은 지워진 객체 · 같은
    출처)의 줄을 한 줄로 접는다. 접은 줄의 후보는 **모두에게 있는 것만**, 추천은 모두 같을
    때만, 확신도는 가장 낮은 것. 결정하면 펼친 줄마다 같은 결정이 간다(`members`)."""
    out: list[Item] = []
    buckets: dict[tuple[str, str], list[Item]] = {}
    for one in items:
        if not one.group:
            out.append(one)
            continue
        slot = (one.kind, one.group)
        if slot not in buckets:
            buckets[slot] = []
            out.append(one)
        buckets[slot].append(one)
    folded: list[Item] = []
    for one in out:
        members = buckets.get((one.kind, one.group)) if one.group else None
        if not members or len(members) == 1:
            folded.append(one)
            continue
        common = [
            choice
            for choice in members[0].choices
            if all(any(c.value == choice.value for c in m.choices) for m in members[1:])
        ]
        recs = {m.recommended for m in members}
        confidences = [m.confidence for m in members]
        blocked = [m.blocked for m in members if m.blocked]
        folded.append(
            Item(
                kind=one.kind,
                id=f"group:{one.group}",
                question=f"{one.group_label} — {len(members)}줄을 한 번에",
                evidence=[m.question for m in members[:5]]
                + ([f"… 그 밖에 {len(members) - 5}줄"] if len(members) > 5 else []),
                choices=common,
                recommended=next(iter(recs)) if len(recs) == 1 else None,
                confidence=min(c for c in confidences if c is not None)
                if all(c is not None for c in confidences)
                else None,
                reason=one.reason
                if len(recs) == 1
                else "줄마다 추천이 다릅니다 — 줄마다 추천대로",
                type_slug=one.type_slug
                if all(m.type_slug == one.type_slug for m in members)
                else "",
                type_label=one.type_label
                if all(m.type_slug == one.type_slug for m in members)
                else "여러 타입",
                workspace_id=one.workspace_id
                if all(m.workspace_id == one.workspace_id for m in members)
                else None,
                rows=sum(m.rows for m in members),
                group=one.group,
                group_label=one.group_label,
                blocked=blocked[0] if len(blocked) == len(members) else None,
                link=one.link,
                ignorable=all(m.ignorable for m in members),
                priority=sum(m.priority for m in members),
                payload={"members": [m.id for m in members]},
            )
        )
    return folded


# --- 계획 -----------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    kind: str
    item_id: str
    action: str
    value: str | None = None


@dataclass
class Outcome:
    decision: Decision
    status: str
    """계획에서는 `apply` · `blocked`, 적용한 뒤에는 `done` · `refused`."""
    question: str = ""
    choice: str = ""
    choice_label: str = ""
    effect: str = ""
    message: str = ""
    rows: int = 0
    undoable: bool = False
    heavy: bool = False
    recommended: str | None = None
    confidence: float | None = None
    item: Item | None = None


@dataclass
class Planned:
    decisions: list[Decision]
    outcomes: list[Outcome]
    fingerprint: str
    skipped: int = 0
    """「추천대로 모두」 에서 문턱 아래 · 추천 없음이라 고르지 않은 수."""
    notes: list[str] = field(default_factory=list)
    unread: int = 0
    """상한 때문에 **읽지 않은** 수(종류마다 원래 자리에 있는 수 - 읽은 수) — 그만큼은 고르지
    않았다. 전부에 하려면 「전부 작업으로」(ADR 0075)."""

    @property
    def counts(self) -> dict[str, int]:
        found = Counter(one.status for one in self.outcomes)
        return {"apply": found.get("apply", 0), "blocked": found.get("blocked", 0)}

    @property
    def rows(self) -> int:
        return sum(one.rows for one in self.outcomes if one.status == "apply")

    @property
    def as_job(self) -> bool:
        return needs_job(self.outcomes)


def needs_job(outcomes: list[Outcome]) -> bool:
    """요청 안에서 하기에 큰가 — 결정 수 · 손대는 줄 수 · 무거운 항목(가리키는 기록이 많은
    병합). 요청 안에서 1만 줄을 고치면 분 단위가 되고 클라이언트가 먼저 끊는다."""
    going = [one for one in outcomes if one.status == "apply"]
    return (
        len(going) > INLINE_DECISIONS
        or sum(one.rows for one in going) > INLINE_ROWS
        or any(one.heavy for one in going)
    )


def fingerprint_of(outcomes: list[Outcome]) -> str:
    """미리 본 것과 적용하는 것이 **같은 계획**인가 — 무엇을 무엇으로, 막히나, 몇 줄인가."""
    body = [
        [
            one.decision.kind,
            one.decision.item_id,
            one.decision.action,
            one.choice,
            one.status,
            one.rows,
        ]
        for one in outcomes
    ]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_same(outcomes: list[Outcome], fingerprint: str) -> None:
    """미리 본 것과 같은 계획인가 — 아니면 아무것도 안 하고 거절한다."""
    if fingerprint_of(outcomes) != fingerprint:
        raise Conflict(
            code("REVIEW", 3),
            "미리 본 것과 달라졌습니다 — 그 사이 누군가 처리했거나 후보가 바뀌었습니다. "
            "아무것도 처리하지 않았으니 다시 미리 보세요.",
        )


def resolve(
    db: Session,
    user: User,
    *,
    mode: str,
    decisions: list[Decision],
    query: Query,
    threshold: float,
    action: str,
    whole: bool = False,
) -> tuple[list[Decision], int, list[str], int]:
    """화면이 보낸 것을 결정 목록으로 — 고른 것은 그대로, 「추천대로 모두」 · 「거른 전부」 는
    **지금 거르기에 맞는 항목을 원래 자리에서 다시 읽어** 고른다. (결정들, 문턱 아래로 뺀
    수, 시간 안에 다 못 읽은 종류의 말, 상한 때문에 읽지 않은 수). `whole` 이면 상한 없이
    (작업 안 — ADR 0075)."""
    notes: list[str] = []
    unread = 0
    if mode == "decisions":
        out = decisions
        skipped = 0
    else:
        listed = collect(db, user, query, whole=whole)
        notes = late_notes(listed)
        handled = {one.provider.kind for one in listed.kinds if one.provider.check is not None}
        for one in listed.kinds:
            if one.provider.kind in handled and one.total > one.scanned and not one.late:
                unread += one.total - one.scanned
                if whole:
                    notes.append(
                        f"「{one.provider.label}」 은(는) 한 번에 {one.scanned:,}건까지만 "
                        f"읽습니다(전체 {one.total:,}건) — 처리한 뒤 다시 하면 다음 것을 "
                        "고릅니다."
                    )
        out = []
        skipped = 0
        for item in listed.items:
            if item.kind not in handled:
                continue
            if mode == "recommended":
                if (
                    item.recommended is None
                    or item.confidence is None
                    or item.confidence < threshold
                ):
                    skipped += 1
                    continue
                out.append(Decision(item.kind, item.id, "accept"))
            else:
                out.append(Decision(item.kind, item.id, action))
    limit = BULK_MAX_DECISIONS if whole else MAX_DECISIONS
    if len(out) > limit:
        raise AppError(
            code("REVIEW", 4),
            f"한 번에 {limit:,}건까지 정합니다({len(out):,}건) — 종류 · 타입 · 부서로 "
            "좁혀 나눠 하세요.",
            status=422,
        )
    return out, skipped, notes, unread


def _blocked(decision: Decision, message: str, item: Item | None = None) -> Outcome:
    return Outcome(
        decision=decision,
        status="blocked",
        question=item.question if item else "",
        message=message,
        recommended=item.recommended if item else None,
        confidence=item.confidence if item else None,
        item=item,
    )


def plan(
    db: Session, user: User, decisions: list[Decision], progress: Progress | None = None
) -> list[Outcome]:
    """결정마다 무엇이 되나 — **항목을 원래 자리에서 다시 읽어** 본다. 아무것도 안 바꾼다.
    `progress` 를 주면(작업 안) 200건마다 알린다 — 수만 건이면 몇 분이다."""
    wanted: dict[str, set[str]] = {}
    for one in decisions:
        wanted.setdefault(one.kind, set()).add(one.item_id)
    items: dict[tuple[str, str], Item] = {}
    for kind, ids in wanted.items():
        provider = registry.get(kind)
        if provider is None or provider.check is None or not provider.can_view(db, user):
            continue
        for got in provider.collect(db, user, Filters(item_ids=frozenset(ids))).items:
            items[(kind, got.id)] = got
    outcomes: list[Outcome] = []
    seen: set[tuple[str, str]] = set()
    for index, one in enumerate(decisions):
        if progress is not None and index % 200 == 0:
            progress("미리 보기", index, len(decisions))
        provider = registry.get(one.kind)
        if provider is None:
            outcomes.append(_blocked(one, f"모르는 종류입니다: {one.kind}"))
            continue
        if provider.check is None:
            outcomes.append(
                _blocked(one, f"「{provider.label}」 은(는) 원래 화면에서 처리합니다.")
            )
            continue
        if not provider.can_view(db, user):
            outcomes.append(
                _blocked(one, f"「{provider.label}」 은(는) 볼 수 없는 종류입니다.")
            )
            continue
        slot = (one.kind, one.item_id)
        if slot in seen:
            outcomes.append(
                _blocked(one, "같은 항목을 두 번 선택했습니다 — 앞의 결정만 봅니다.")
            )
            continue
        seen.add(slot)
        item = items.get(slot)
        if item is None:
            outcomes.append(
                _blocked(one, "이미 처리했거나(그 사이 누군가) 볼 수 없는 항목입니다.")
            )
            continue
        if one.action == "accept":
            value = item.recommended
            if value is None:
                outcomes.append(
                    _blocked(one, "추천이 없습니다 — 「다른 것으로」 선택하세요.", item)
                )
                continue
        elif one.action == "choose":
            value = one.value
            if not value:
                outcomes.append(_blocked(one, "고른 값이 없습니다.", item))
                continue
        elif one.action == "ignore":
            if not item.ignorable:
                outcomes.append(
                    _blocked(
                        one,
                        f"「{provider.label}」 에는 무시가 없습니다 — 후보 중에서 선택하세요.",
                        item,
                    )
                )
                continue
            value = IGNORE
        else:
            outcomes.append(_blocked(one, f"모르는 동작입니다: {one.action}", item))
            continue
        found = provider.check(db, user, item, value)
        outcomes.append(
            Outcome(
                decision=one,
                status=found.status,
                question=item.question,
                choice=value,
                choice_label=found.choice_label,
                effect=found.effect,
                message=found.message,
                rows=found.rows if found.status == "apply" else 0,
                undoable=found.undoable,
                heavy=found.heavy,
                recommended=item.recommended,
                confidence=item.confidence,
                item=item,
            )
        )
    return outcomes


def preview(
    db: Session,
    user: User,
    *,
    mode: str,
    decisions: list[Decision],
    query: Query,
    threshold: float,
    action: str,
) -> Planned:
    resolved, skipped, notes, unread = resolve(
        db,
        user,
        mode=mode,
        decisions=decisions,
        query=query,
        threshold=threshold,
        action=action,
    )
    outcomes = plan(db, user, resolved)
    if mode == "recommended" and skipped:
        notes.append(
            f"추천이 없거나 확신도가 {threshold:.2f} 아래인 {skipped:,}건은 선택하지 "
            "않았습니다 — 사람이 봐야 합니다."
        )
    if unread:
        notes.append(
            f"한 번에 읽는 상한 때문에 {unread:,}건은 보지 않았습니다 — 그것까지 하려면 "
            "「전부 작업으로」(작업이 전부를 읽어 미리 보고, 확인하면 적용합니다)."
        )
    return Planned(
        decisions=resolved,
        outcomes=outcomes,
        fingerprint=fingerprint_of(outcomes),
        skipped=skipped,
        notes=notes,
        unread=unread,
    )


# --- 적용 -----------------------------------------------------------------------


@dataclass
class Result:
    batch_id: uuid.UUID | None
    outcomes: list[Outcome]
    job: Job | None = None

    @property
    def counts(self) -> dict[str, int]:
        found = Counter(one.status for one in self.outcomes)
        return {"done": found.get("done", 0), "refused": found.get("refused", 0)}


Progress = Callable[[str, int, int], None]


def _record(
    db: Session,
    user: User,
    batch_id: uuid.UUID,
    one: Outcome,
    *,
    mode: str,
    threshold: float | None,
    undo: dict[str, Any],
) -> None:
    item = one.item
    recommended = item.recommended if item else None
    db.add(
        ReviewDecision(
            batch_id=batch_id,
            kind=one.decision.kind,
            item_id=one.decision.item_id[:300],
            question=one.question[:500],
            action=one.decision.action,
            choice=one.choice[:200],
            choice_label=one.choice_label[:300],
            recommended=recommended,
            confidence=item.confidence if item else None,
            # 추천을 따랐나 — 추천이 없었으면 셈하지 않는다(NULL). 무시는 따르지 않은 것이다.
            followed=None if recommended is None else one.choice == recommended,
            mode=mode if mode in REVIEW_MODES else "selected",
            threshold=threshold if mode == "recommended" else None,
            status=one.status,
            message=one.message[:500],
            rows=one.rows,
            signature=(item.signature if item else "")[:64],
            undo=undo,
            undoable=one.status == "done" and one.undoable,
            type_slug=(item.type_slug if item else "")[:64],
            workspace_id=item.workspace_id if item else None,
            decided_by_id=user.id,
        )
    )


def apply(
    db: Session,
    user: User,
    decisions: list[Decision],
    fingerprint: str,
    *,
    mode: str,
    threshold: float | None,
    in_job: bool = False,
    progress: Progress | None = None,
    planned: list[Outcome] | None = None,
) -> Result:
    """미리 본 그 계획대로 — **지문이 다르면 아무것도 안 한다.** 항목마다 처리 + 기록 +
    커밋. `planned` 를 주면(방금 같은 결정으로 계획한 것) 다시 계획하지 않는다."""
    outcomes = planned if planned is not None else plan(db, user, decisions)
    require_same(outcomes, fingerprint)
    batch_id = uuid.uuid4()
    user_id = user.id
    total = len(outcomes)
    for index, one in enumerate(outcomes):
        if progress is not None:
            progress("적용", index, total)
        if one.status != "apply" or one.item is None:
            continue
        provider = registry.get(one.decision.kind)
        assert provider is not None and provider.apply is not None
        try:
            applied = provider.apply(db, user, one.item, one.choice, in_job)
            one.status = "done"
            one.rows = applied.rows
            if applied.message:
                one.message = applied.message
            db.flush()
            _record(db, user, batch_id, one, mode=mode, threshold=threshold, undo=applied.undo)
            db.commit()
        except AppError as refused:
            db.rollback()
            one.status = "refused"
            one.rows = 0
            one.message = refused.message
            _record(db, user, batch_id, one, mode=mode, threshold=threshold, undo={})
            db.commit()
        except Exception as caught:
            # 하나가 터져도 나머지는 간다 — 터진 것은 그 줄에 까닭을 남기고 로그에 스택을.
            db.rollback()
            log.exception("검토함 적용 실패: %s %s", one.decision.kind, one.decision.item_id)
            one.status = "refused"
            one.rows = 0
            one.message = f"처리 중 오류가 났습니다({type(caught).__name__})."
            _record(db, user, batch_id, one, mode=mode, threshold=threshold, undo={})
            db.commit()
        user = db.get(User, user_id) or user
    if progress is not None:
        progress("적용", total, total)
    return Result(batch_id=batch_id, outcomes=outcomes)


def enqueue(
    db: Session,
    user: User,
    decisions: list[Decision],
    fingerprint: str,
    *,
    mode: str,
    threshold: float | None,
) -> Job:
    """큰 묶음 — 작업으로. 워커가 같은 함수(`apply`)를 같은 지문으로 부른다."""
    job = job_services.enqueue(
        db,
        kind="review_apply",
        params={
            "decisions": [asdict(one) for one in decisions],
            "fingerprint": fingerprint,
            "mode": mode,
            "threshold": threshold,
        },
        user=user,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return job


def outcome_dict(one: Outcome) -> dict[str, Any]:
    provider = registry.get(one.decision.kind)
    return {
        "kind": one.decision.kind,
        "kind_label": provider.label if provider else one.decision.kind,
        "item_id": one.decision.item_id,
        "question": one.question,
        "action": one.decision.action,
        "choice": one.choice,
        "choice_label": one.choice_label,
        "status": one.status,
        "effect": one.effect,
        "message": one.message,
        "rows": one.rows,
        "undoable": one.undoable,
        "recommended": one.recommended,
        "confidence": one.confidence,
        "followed": None if one.recommended is None else one.choice == one.recommended,
    }


def run_job(
    db: Session, user: User, params: dict[str, Any], progress: Progress
) -> dict[str, Any]:
    """작업 `review_apply` — 요청 경로와 **같은 함수**. 줄 목록(`rows`)은 작업 목록이 요약만
    싣는다(`sp_job_brief` · `rows_omitted`)."""
    decisions = [
        Decision(
            kind=str(one["kind"]),
            item_id=str(one["item_id"]),
            action=str(one["action"]),
            value=None if one.get("value") is None else str(one["value"]),
        )
        for one in params.get("decisions") or []
    ]
    unknown = sorted({one.kind for one in decisions if registry.get(one.kind) is None})
    if unknown:
        # 제공자는 `app/main.py` 의 조립이 등록한다 — 워커가 그것을 안 거치고 떴으면 비어 있다.
        # 그대로 두면 모든 결정이 「모르는 종류」 로 막혀 「미리 본 것과 달라졌습니다」 라는
        # 엉뚱한 말로 실패한다(지문이 달라진다).
        raise AppError(
            code("REVIEW", 8),
            f"이 프로세스에 검토함의 종류가 등록되지 않았습니다({', '.join(unknown)}) — "
            "워커가 앱과 같은 조립으로 떴는지 보세요.",
            status=409,
        )
    threshold = params.get("threshold")
    result = apply(
        db,
        user,
        decisions,
        str(params.get("fingerprint") or ""),
        mode=str(params.get("mode") or "selected"),
        threshold=float(threshold) if threshold is not None else None,
        in_job=True,
        progress=progress,
    )
    return {
        "applied": True,
        "batch_id": str(result.batch_id),
        "counts": result.counts,
        "rows": [outcome_dict(one) for one in result.outcomes],
    }


# --- 전부 작업으로(ADR 0075) --------------------------------------------------------
#
# 「추천대로 모두」 · 「거른 전부」 는 화면이 읽은 것(별칭 후보 200줄) 안에서만 고른다 — 대기가
# 3만 건이면 150번을 눌러야 한다. 여기는 **작업이** 상한 없이 전부 읽어(`whole`) 미리 보고,
# 사람이 그 미리 보기를 보고 적용한다(두 단계 작업 `review_bulk` — 같은 결정 · 같은 지문). 수만
# 건의 결정은 결과에 싣지 않고 결과 파일(CSV — 사람도 받아 본다)에 둔다 — 작업 목록이 결과를
# 싣기 때문이다.

#: 결과 파일의 열 — 앞의 넷을 적용이 다시 읽는다.
BULK_COLUMNS = ("종류", "항목", "동작", "값", "물음", "무엇이 되나", "상태", "말")


def _query_of(raw: dict[str, Any]) -> Query:
    return Query(
        kind=raw.get("kind") or None,
        type_slug=raw.get("type") if raw.get("type") is not None else None,
        workspace=raw.get("workspace") or None,
        min_confidence=raw.get("min_confidence"),
        max_confidence=raw.get("max_confidence"),
        actionable=bool(raw.get("actionable")),
        recommendation=raw.get("recommendation") or None,
    )


def kind_lines(outcomes: list[Outcome]) -> list[dict[str, Any]]:
    """종류마다 — 적용 · 막힘 · 줄 수 · 복원할 수 있나(미리 보기 창의 표)."""
    by_kind: dict[str, dict[str, Any]] = {}
    for one in outcomes:
        provider = registry.get(one.decision.kind)
        line = by_kind.setdefault(
            one.decision.kind,
            {
                "kind": one.decision.kind,
                "label": provider.label if provider else one.decision.kind,
                "apply": 0,
                "blocked": 0,
                "rows": 0,
                "undoable": True,
                "undo_note": provider.undo_note if provider else "",
            },
        )
        if one.status == "apply":
            line["apply"] += 1
            line["rows"] += one.rows
            line["undoable"] = line["undoable"] and one.undoable
        else:
            line["blocked"] += 1
    return list(by_kind.values())


def bulk_csv(outcomes: list[Outcome]) -> bytes:
    """미리 본 전부 — 결정마다 한 줄. 엑셀이 한글을 알아보게 BOM 을 붙인다."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(BULK_COLUMNS)
    for one in outcomes:
        writer.writerow(
            [
                one.decision.kind,
                one.decision.item_id,
                one.decision.action,
                one.decision.value or "",
                one.question,
                one.effect,
                one.status,
                one.message,
            ]
        )
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def bulk_decisions(data: bytes) -> list[Decision]:
    """결과 파일에서 결정을 다시 읽는다 — 미리 본 그 결정들(`bulk_csv`)."""
    import csv
    import io

    reader = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    header = next(reader, None)
    if header is None or tuple(header[:4]) != BULK_COLUMNS[:4]:
        raise AppError(
            code("REVIEW", 9),
            "미리 보기의 결과 파일 모양이 다릅니다 — 다시 미리 보세요.",
            status=409,
        )
    return [
        Decision(kind=row[0], item_id=row[1], action=row[2], value=row[3] or None)
        for row in reader
        if len(row) >= 4
    ]


def bulk_plan(
    db: Session, user: User, params: dict[str, Any], progress: Progress
) -> tuple[dict[str, Any], bytes]:
    """「전부 작업으로」 의 미리 보기 — (결과, 결과 파일). **아무것도 안 바꾼다.**"""
    mode = str(params.get("mode") or "recommended")
    if mode not in ("recommended", "all"):
        raise AppError(code("REVIEW", 4), f"모르는 방식입니다: {mode}", status=422)
    threshold = float(params.get("threshold") or DEFAULT_THRESHOLD)
    action = str(params.get("action") or "accept")
    progress("읽기", 0, 0)
    resolved, skipped, notes, _unread = resolve(
        db,
        user,
        mode=mode,
        decisions=[],
        query=_query_of(dict(params.get("filters") or {})),
        threshold=threshold,
        action=action,
        whole=True,
    )
    outcomes = plan(db, user, resolved, progress=progress)
    progress("미리 보기", len(resolved), len(resolved))
    if mode == "recommended" and skipped:
        notes.append(
            f"추천이 없거나 확신도가 {threshold:.2f} 아래인 {skipped:,}건은 선택하지 "
            "않았습니다 — 사람이 봐야 합니다."
        )
    found = Counter(one.status for one in outcomes)
    counts = {"apply": found.get("apply", 0), "blocked": found.get("blocked", 0)}
    shown = sorted(outcomes, key=lambda one: 0 if one.status == "blocked" else 1)
    result = {
        "summary": (
            f"전부 미리 보기 — 적용 {counts['apply']:,} · 막힘 {counts['blocked']:,}"
            + (f" · 고르지 않음 {skipped:,}" if skipped else "")
            + (" — 「적용」 으로 처리합니다" if counts["apply"] else "")
        ),
        "ok": counts["apply"] > 0,
        "applied": False,
        "fingerprint": fingerprint_of(outcomes),
        "mode": mode,
        "threshold": threshold if mode == "recommended" else None,
        "decisions": [],
        "decided": len(resolved),
        "counts": counts,
        "by_kind": kind_lines(outcomes),
        "outcomes": [outcome_dict(one) for one in shown[:SHOWN_OUTCOMES]],
        "outcomes_omitted": max(0, len(shown) - SHOWN_OUTCOMES),
        "skipped": skipped,
        "rows": sum(one.rows for one in outcomes if one.status == "apply"),
        "as_job": True,
        "unread": 0,
        "notes": notes,
    }
    return result, bulk_csv(outcomes)


def bulk_apply(
    db: Session,
    user: User,
    params: dict[str, Any],
    decisions: list[Decision],
    progress: Progress,
) -> dict[str, Any]:
    """「전부 작업으로」 의 적용 — 미리 본 **그 결정들**을(결과 파일에서) 다시 계획해 지문이
    같을 때만. 그 사이 새로 생긴 항목은 들지 않는다(다음에 다시)."""
    mode = str(params.get("mode") or "recommended")
    threshold = params.get("threshold")
    outcomes = plan(db, user, decisions, progress=progress)
    require_same(outcomes, str(params.get("fingerprint") or ""))
    result = apply(
        db,
        user,
        decisions,
        str(params.get("fingerprint") or ""),
        mode=mode,
        threshold=float(threshold)
        if mode == "recommended" and threshold is not None
        else None,
        in_job=True,
        progress=progress,
        planned=outcomes,
    )
    counts = result.counts
    undoable = sum(1 for one in result.outcomes if one.status == "done" and one.undoable)
    return {
        "summary": (
            f"{counts['done']:,}건 처리"
            + (
                f" · 그 사이 막혀 처리 안 함 {counts['refused']:,}"
                if counts["refused"]
                else ""
            )
        ),
        "applied": True,
        "batch_id": str(result.batch_id),
        "counts": counts,
        "undoable": undoable,
        "rows": [outcome_dict(one) for one in result.outcomes[:SHOWN_OUTCOMES]],
    }


def enqueue_bulk(
    db: Session,
    user: User,
    *,
    mode: str,
    filters: dict[str, Any],
    threshold: float,
    action: str,
) -> Job:
    """「전부 작업으로」 의 미리 보기 작업을 넣는다 — 적용은 그 작업의 `POST
    /api/jobs/{id}/apply`."""
    job = job_services.enqueue(
        db,
        kind="review_bulk",
        params={
            "mode": mode,
            "filters": filters,
            "threshold": threshold,
            "action": action,
        },
        user=user,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return job


# --- 자동 확인(ADR 0075) -----------------------------------------------------------


@dataclass
class AutoEvidence:
    decided: int
    """그 구간(확신도 ≥ 문턱)에서 사람이 하나씩 정한 결정 수 — 추천이 있던 것만."""
    followed: int
    """그중 추천대로 한 것(복원하지 않은 것)."""
    undone_auto: int
    """켠 뒤 자동으로 정한 것을 누가 복원한 수 — 하나라도 있으면 멈춘다."""

    @property
    def rate(self) -> float | None:
        return self.followed / self.decided if self.decided else None


@dataclass
class AutoState:
    provider: Provider
    rule: ReviewAutoRule | None
    evidence: AutoEvidence
    ready: bool
    """지금 돌릴 수 있나 — 켜져 있고 근거가 충분하다."""
    why: str
    """못 도는 까닭(사람 말). 돌 수 있으면 빈 글자."""


def auto_evidence(
    db: Session, kind: str, threshold: float, since: datetime | None
) -> AutoEvidence:
    """자동 확인의 근거 — **사람이 하나씩 정한 결정**(`AUTO_EVIDENCE_MODES`)만 센다. 부서와
    상관없이 그 종류 전부(설치 전체의 규칙이다)."""
    decided, followed = db.execute(
        select(
            func.count(),
            func.count().filter(
                ReviewDecision.followed.is_(True), ReviewDecision.undone_at.is_(None)
            ),
        ).where(
            ReviewDecision.kind == kind,
            ReviewDecision.status == "done",
            ReviewDecision.recommended.isnot(None),
            ReviewDecision.confidence >= threshold,
            ReviewDecision.mode.in_(AUTO_EVIDENCE_MODES),
        )
    ).one()
    undone = 0
    if since is not None:
        undone = int(
            db.scalar(
                select(func.count()).where(
                    ReviewDecision.kind == kind,
                    ReviewDecision.mode == "auto",
                    ReviewDecision.undone_at.isnot(None),
                    ReviewDecision.decided_at >= since,
                )
            )
            or 0
        )
    return AutoEvidence(decided=int(decided), followed=int(followed), undone_auto=undone)


def _auto_why(rule: ReviewAutoRule | None, evidence: AutoEvidence) -> str:
    if rule is None or not rule.enabled:
        return "꺼져 있습니다."
    if evidence.undone_auto:
        return (
            f"자동으로 처리한 것을 {evidence.undone_auto:,}건 복원한 적이 있어 멈췄습니다 — "
            "추천을 확인한 뒤 다시 켜면 이어 합니다."
        )
    if evidence.decided < AUTO_MIN_DECIDED:
        return (
            f"근거가 모자랍니다 — 확신도 {rule.threshold:.2f} 이상에서 사람이 하나씩 정한 "
            f"결정이 {evidence.decided:,}건입니다({AUTO_MIN_DECIDED}건 필요)."
        )
    rate = evidence.rate or 0.0
    if rate < AUTO_MIN_RATE:
        return (
            f"추천이 충분히 맞지 않습니다 — 확신도 {rule.threshold:.2f} 이상에서 추천대로 "
            f"한 몫이 {rate:.0%}입니다({AUTO_MIN_RATE:.0%} 이상 필요, 결정 "
            f"{evidence.decided:,}건)."
        )
    return ""


def auto_states(db: Session) -> list[AutoState]:
    """처리 길이 있는 종류마다 자동 확인의 상태 — 켜져 있나 · 근거 · 지금 돌 수 있나."""
    rules = {one.kind: one for one in db.scalars(select(ReviewAutoRule))}
    out: list[AutoState] = []
    for provider in registry.providers():
        if provider.check is None:
            continue
        rule = rules.get(provider.kind)
        threshold = rule.threshold if rule is not None else 0.95
        evidence = auto_evidence(
            db, provider.kind, threshold, rule.enabled_at if rule is not None else None
        )
        why = _auto_why(rule, evidence)
        out.append(
            AutoState(provider=provider, rule=rule, evidence=evidence, ready=not why, why=why)
        )
    return out


def set_auto(
    db: Session, user: User, kind: str, *, enabled: bool, threshold: float
) -> ReviewAutoRule:
    """자동 확인을 켜고 끈다 — **시스템 관리자만**(설치 전체의 자료를 밤마다 고친다). 켜면 그
    사람의 이름으로 처리하고, 켠 때부터 「자동으로 정한 것을 복원했나」 를 다시 센다. 부르는
    쪽이 커밋한다."""
    from app.shared import audit

    if not user.is_system_admin:
        raise Forbidden(
            code("REVIEW", 10),
            "자동 확인은 시스템 관리자가 켭니다 — 설치 전체의 자료를 밤마다 처리합니다.",
        )
    provider = registry.get(kind)
    if provider is None or provider.check is None:
        raise AppError(
            code("REVIEW", 2), f"처리 길이 있는 종류가 아닙니다: {kind}", status=422
        )
    if not AUTO_MIN_THRESHOLD <= threshold <= 1.0:
        raise AppError(
            code("REVIEW", 11),
            f"자동 확인의 문턱은 {AUTO_MIN_THRESHOLD} 이상 1 이하입니다({threshold}).",
            status=422,
        )
    rule = db.get(ReviewAutoRule, kind)
    before = (
        {"enabled": rule.enabled, "threshold": rule.threshold} if rule is not None else None
    )
    if rule is None:
        rule = ReviewAutoRule(kind=kind)
        db.add(rule)
    changed = rule.enabled != enabled or rule.threshold != threshold
    rule.threshold = threshold
    if enabled and changed:
        rule.enabled_by_id = user.id
        rule.enabled_at = datetime.now(UTC)
        rule.last_note = ""
    rule.enabled = enabled
    db.flush()
    if changed:
        audit.record(
            db,
            action="review.auto",
            actor=user,
            target_table="review_auto_rules",
            target_id=None,
            target_label=f"자동 확인: {provider.label}",
            changes={
                "rule": {
                    "before": before,
                    "after": {"enabled": enabled, "threshold": threshold},
                }
            },
            reason="검토함 자동 확인 " + ("켬" if enabled else "끔"),
        )
    return rule


def schedule_auto(db: Session, now: datetime | None = None) -> list[str]:
    """**워커가 5분마다 부른다** — 켠 종류마다 하루 한 번(`REVIEW_AUTO_HOUR` 이 지난 뒤) 작업을
    넣는다(넣은 종류들). 근거가 모자라면 넣지 않고 그 까닭을 규칙에 적는다. 켠 사람이 더 시스템
    관리자가 아니면 끈다 — 그 이름으로 처리하기 때문이다."""
    from sqlalchemy import text as sql_text

    from app.config import get_settings

    if not db.scalar(
        sql_text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": "review_auto:schedule"},
    ):
        return []
    now = now or datetime.now(UTC)
    local = now.astimezone()
    if local.hour < get_settings().review_auto_hour:
        return []
    queued: list[str] = []
    for rule in db.scalars(select(ReviewAutoRule).where(ReviewAutoRule.enabled.is_(True))):
        if (
            rule.last_run_at is not None
            and rule.last_run_at.astimezone().date() >= local.date()
        ):
            continue
        busy = db.scalar(
            select(Job.id)
            .where(
                Job.kind == "review_auto",
                Job.status.in_(("queued", "running")),
                Job.params["kind"].astext == rule.kind,
            )
            .limit(1)
        )
        if busy is not None:
            continue
        rule.last_run_at = now
        owner = db.get(User, rule.enabled_by_id) if rule.enabled_by_id else None
        if owner is None or owner.deleted_at is not None or not owner.is_system_admin:
            rule.enabled = False
            rule.last_note = (
                "켠 사람이 더 시스템 관리자가 아니어서 껐습니다 — 그 이름으로 처리합니다. "
                "다시 켜세요."
            )
            continue
        provider = registry.get(rule.kind)
        if provider is None:
            rule.last_note = "이 프로세스에 그 종류가 없습니다 — 확장을 껐을 수 있습니다."
            continue
        why = _auto_why(rule, auto_evidence(db, rule.kind, rule.threshold, rule.enabled_at))
        if why:
            rule.last_note = f"{local:%m-%d} 건너뜀 — {why}"
            continue
        job = job_services.enqueue(
            db, kind="review_auto", params={"kind": rule.kind}, user=owner, workspace_id=None
        )
        rule.last_job_id = job.id
        queued.append(rule.kind)
    db.commit()
    return queued


def run_auto(
    db: Session, user: User, params: dict[str, Any], progress: Progress
) -> dict[str, Any]:
    """작업 `review_auto` — 그 종류의 「추천대로 모두」 를 **상한 없이 · 밤마다 `AUTO_MAX`
    까지**. 도는 때에 근거를 다시 본다(그 사이 복원했을 수 있다). 결정은 묶음 하나
    (`mode="auto"`)."""
    kind = str(params.get("kind") or "")
    rule = db.get(ReviewAutoRule, kind)
    if rule is None or not rule.enabled:
        return {"summary": "자동 확인이 꺼져 있어 건너뛰었습니다.", "applied": False}
    threshold = rule.threshold
    why = _auto_why(rule, auto_evidence(db, kind, threshold, rule.enabled_at))
    if why:
        rule.last_note = f"건너뜀 — {why}"
        return {"summary": f"건너뜀 — {why}", "applied": False}
    progress("읽기", 0, 0)
    resolved, _skipped, _notes, _unread = resolve(
        db,
        user,
        mode="recommended",
        decisions=[],
        query=Query(kind=kind),
        threshold=threshold,
        action="accept",
        whole=True,
    )
    more = max(0, len(resolved) - AUTO_MAX)
    resolved = resolved[:AUTO_MAX]
    outcomes = plan(db, user, resolved, progress=progress)
    blocked = sum(1 for one in outcomes if one.status == "blocked")
    result = apply(
        db,
        user,
        resolved,
        fingerprint_of(outcomes),
        mode="auto",
        threshold=threshold,
        in_job=True,
        progress=progress,
        planned=outcomes,
    )
    counts = result.counts
    summary = (
        f"자동 확인 — {counts['done']:,}건 처리"
        + (f" · 막힘 {blocked:,}" if blocked else "")
        + (f" · 그 사이 막힘 {counts['refused']:,}" if counts["refused"] else "")
        + (f" · 남은 {more:,}건은 다음 밤" if more else "")
    )
    rule = db.get(ReviewAutoRule, kind) or rule
    rule.last_note = summary
    return {
        "summary": summary,
        "applied": True,
        "batch_id": str(result.batch_id),
        "counts": counts,
        "rows": [outcome_dict(one) for one in result.outcomes[:SHOWN_OUTCOMES]],
    }


# --- 결정 기록 · 되돌리기 · 정확도 --------------------------------------------------


def _visible_decisions(db: Session, user: User) -> Any:
    """결정 기록은 **내 것 + 내가 관리하는 부서의 것**, 시스템 관리자는 전부."""
    if user.is_system_admin:
        return ReviewDecision.id.isnot(None)
    managed = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user.id, WorkspaceMember.role == "manager"
    )
    return or_(
        ReviewDecision.decided_by_id == user.id, ReviewDecision.workspace_id.in_(managed)
    )


def decisions_page(
    db: Session,
    user: User,
    *,
    kind: str | None,
    batch_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[ReviewDecision], int]:
    stmt = select(ReviewDecision).where(_visible_decisions(db, user))
    if kind:
        stmt = stmt.where(ReviewDecision.kind == kind)
    if batch_id is not None:
        stmt = stmt.where(ReviewDecision.batch_id == batch_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


@dataclass
class Batch:
    batch_id: uuid.UUID
    decided_at: datetime
    decided_by_id: uuid.UUID | None
    mode: str
    count: int
    done: int
    refused: int
    undoable: int
    undone: int
    kinds: list[str]


def batches(db: Session, user: User, *, limit: int) -> list[Batch]:
    """최근 적용한 묶음 — 복원할 수 있는 것이 몇이나 남았나."""
    rows = db.execute(
        select(
            ReviewDecision.batch_id,
            func.min(ReviewDecision.decided_at),
            # uuid 에는 min 이 없다 — 글자로 모아 꺼낸다(한 묶음은 한 사람이 적용한다).
            func.min(cast(ReviewDecision.decided_by_id, String)),
            func.min(ReviewDecision.mode),
            func.count(),
            func.count().filter(ReviewDecision.status == "done"),
            func.count().filter(ReviewDecision.status == "refused"),
            func.count().filter(
                ReviewDecision.undoable.is_(True), ReviewDecision.undone_at.is_(None)
            ),
            func.count().filter(ReviewDecision.undone_at.isnot(None)),
            func.array_agg(func.distinct(ReviewDecision.kind)),
        )
        .where(_visible_decisions(db, user))
        .group_by(ReviewDecision.batch_id)
        .order_by(func.min(ReviewDecision.decided_at).desc())
        .limit(limit)
    ).all()
    return [
        Batch(
            batch_id=row[0],
            decided_at=row[1],
            decided_by_id=uuid.UUID(row[2]) if row[2] else None,
            mode=row[3],
            count=int(row[4]),
            done=int(row[5]),
            refused=int(row[6]),
            undoable=int(row[7]),
            undone=int(row[8]),
            kinds=sorted(row[9] or []),
        )
        for row in rows
    ]


@dataclass
class UndoLine:
    decision: ReviewDecision
    status: str
    """계획: `undo` · `blocked`, 적용 뒤: `done` · `refused`."""
    message: str


def undo_batch(db: Session, user: User, batch_id: uuid.UUID, *, apply: bool) -> list[UndoLine]:
    """묶음 하나를 복원한다 — `apply=False` 면 무엇이 복원되나만. 종류마다 원래 기능의
    되돌리기(일괄 수정의 묶음 되돌리기 · 별칭 빼기 · 후보 대기로)로 간다. 병합은 복원이
    없다."""
    rows = list(
        db.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.batch_id == batch_id, _visible_decisions(db, user))
            .order_by(ReviewDecision.decided_at, ReviewDecision.id)
        )
    )
    if not rows:
        raise NotFound(
            code("REVIEW", 5), "그 묶음을 찾을 수 없습니다 — 검토함에서 적용한 것이 아닙니다."
        )
    lines: list[UndoLine] = []
    for row in rows:
        provider = registry.get(row.kind)
        if row.status != "done":
            lines.append(UndoLine(row, "blocked", "적용하지 못한 결정입니다."))
            continue
        if row.undone_at is not None:
            lines.append(UndoLine(row, "blocked", "이미 복원했습니다."))
            continue
        if not row.undoable or provider is None or provider.undo is None:
            note = provider.undo_note if provider and provider.undo_note else ""
            lines.append(UndoLine(row, "blocked", note or "복원할 수 없는 결정입니다."))
            continue
        ok, message = provider.undo(db, user, dict(row.undo or {}), False)
        lines.append(UndoLine(row, "undo" if ok else "blocked", message))
    if not apply:
        return lines
    user_id = user.id
    for line in lines:
        if line.status != "undo":
            continue
        provider = registry.get(line.decision.kind)
        assert provider is not None and provider.undo is not None
        decision_id = line.decision.id
        try:
            ok, message = provider.undo(db, user, dict(line.decision.undo or {}), True)
            if not ok:
                raise Conflict(code("REVIEW", 6), message)
            target = db.get(ReviewDecision, decision_id)
            if target is not None:
                target.undone_at = datetime.now(UTC)
                target.undone_by_id = user_id
            db.commit()
            line.status, line.message = "done", message
        except AppError as refused:
            db.rollback()
            line.status, line.message = "refused", refused.message
        user = db.get(User, user_id) or user
    return lines


@dataclass
class Accuracy:
    kind: str
    label: str
    decided: int
    recommended: int
    followed: int


#: 확신도 구간 — 「0.9 이상은 믿어도 되나」 를 이것으로 잰다.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("0.9 이상", 0.9, 1.01),
    ("0.7 ~ 0.9", 0.7, 0.9),
    ("0.5 ~ 0.7", 0.5, 0.7),
    ("0.5 미만", 0.0, 0.5),
)


def accuracy(db: Session, user: User) -> tuple[list[Accuracy], list[Accuracy]]:
    """추천을 따랐나 — 처리한(복원하지 않은) 결정 중 추천이 있었던 것만 센다. 복원한 결정은
    「그 추천은 틀렸다」 일 수 있어 따른 것으로 세지 않는다."""
    alive = (
        _visible_decisions(db, user),
        ReviewDecision.status == "done",
    )
    followed = func.count().filter(
        ReviewDecision.followed.is_(True), ReviewDecision.undone_at.is_(None)
    )
    by_kind = []
    for kind, decided, recommended, kept in db.execute(
        select(
            ReviewDecision.kind,
            func.count(),
            func.count().filter(ReviewDecision.recommended.isnot(None)),
            followed,
        )
        .where(*alive)
        .group_by(ReviewDecision.kind)
        .order_by(ReviewDecision.kind)
    ):
        provider = registry.get(kind)
        by_kind.append(
            Accuracy(
                kind=kind,
                label=provider.label if provider else kind,
                decided=int(decided),
                recommended=int(recommended),
                followed=int(kept),
            )
        )
    band = case(
        *[(ReviewDecision.confidence >= low, name) for name, low, _high in BANDS if low > 0],
        else_=BANDS[-1][0],
    )
    found = {
        name: (int(decided), int(kept))
        for name, decided, kept in db.execute(
            select(band, func.count(), followed)
            .where(*alive, ReviewDecision.recommended.isnot(None))
            .group_by(band)
        )
    }
    by_band = [
        Accuracy(
            kind="",
            label=name,
            decided=found.get(name, (0, 0))[0],
            recommended=found.get(name, (0, 0))[0],
            followed=found.get(name, (0, 0))[1],
        )
        for name, _low, _high in BANDS
    ]
    return by_kind, by_band


def maintenance_links(db: Session, user: User) -> dict[str, str]:
    """홈 「남은 일」 의 줄 → 검토함의 그 종류. **볼 수 있는 종류만** — 못 보는 사람의 홈은
    원래 화면으로 간다."""
    if not is_any_manager(db, user):
        return {}
    out: dict[str, str] = {}
    for provider in registry.providers():
        if not provider.maintenance_keys or not provider.can_view(db, user):
            continue
        for key in provider.maintenance_keys:
            out[key] = f"/review?kind={provider.kind}"
    return out


def workspace_labels(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str]]:
    if not ids:
        return {}
    return {
        row.id: (row.slug, row.name)
        for row in db.scalars(select(Workspace).where(Workspace.id.in_(ids)))
    }


def user_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {
        row.id: row.display_name for row in db.scalars(select(User).where(User.id.in_(ids)))
    }
