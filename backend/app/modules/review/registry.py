"""검토함의 종류 — **원래 자리에서 읽어 모으는 제공자**(ADR 0027).

사람의 판단을 기다리는 일은 자리마다 따로 있다 — 못 찾은 말은 `search_misses`, 기계가 붙인
별칭은 `object_aliases.verified_at`, 이름이 같은 객체와 지워진 것을 가리키는 칸은 객체 표 그
자체. 그것을 **새 표에 복사해 두지 않는다**: 복사하면 원래 자리에서 처리한 것이 검토함에는
남고, 검토함에서 처리한 것이 원래 화면에는 남는다 — 두 벌은 반드시 어긋난다. 대신 종류마다
제공자 하나가 원래 자리에서 읽어 「물음 · 근거 · 후보 · 추천 · 확신도」 한 줄로 만들고,
처리는 원래 기능의 서비스 함수(같은 감사 기록)를 그대로 부른다.

    collect   거르기에 맞는 항목들(원래 자리에서 읽는다)
    check     결정 하나가 무엇이 되나 — **아무것도 안 바꾼다**(미리 보기)
    apply     결정 하나를 원래 기능으로 처리한다 — **커밋하지 않는다**(검토함이 항목마다)
    undo      처리한 것을 되돌린다 — 원래 기능의 되돌리기 · 감사 기록으로. 없으면 None

등록은 `app/main.py` 의 `_register_extensions()` 한 곳에서 한다(`maintenance()` 등록과 같은
무늬). 처리 길이 없는 종류(데이터 소스의 선 기다림 · 적용을 기다리는 계획)는 `check` 가 없고
「원래 화면으로」 링크만 낸다.

이 모듈은 도메인을 모른다 — 모델도 서비스도 import 하지 않는다. 결정 기록 표는
`review/models.py`, 모으고 고르고 적는 일은 `review/services.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.modules.accounts.models import User

#: 결정의 동작 — 추천대로 · 다른 것으로(후보 중 하나) · 무시.
ACTIONS = ("accept", "choose", "ignore")

#: 「무시」 를 고른 값으로 적을 때의 표식 — 후보의 값(객체 id 등)과 겹치지 않는 글자.
IGNORE = "ignore"


@dataclass(frozen=True)
class Filters:
    """거르기 — 제공자는 할 수 있는 것을 질의에 싣고, 못 싣는 것은 검토함이 뒤에서 거른다."""

    type_slug: str | None = None
    workspace_id: uuid.UUID | None = None
    """그 부서 것만. `workspace_global` 과 함께 쓰지 않는다."""
    workspace_global: bool = False
    """전역(부서 없음)만."""
    item_ids: frozenset[str] | None = None
    """이 항목들만 — 미리 보기 · 적용이 결정의 항목을 **원래 자리에서 다시** 읽을 때."""


@dataclass
class Choice:
    """후보 하나 — 「이것으로」 고를 수 있는 값."""

    value: str
    label: str
    detail: str = ""
    score: float | None = None
    """후보마다의 점수(0~1) — 비슷함 등. 없으면 점수를 매기지 않는 후보(「비움」 ·
    「삭제」)."""


@dataclass
class Item:
    """검토함의 한 줄 — **물음 + 근거 + 후보 + 추천 + 확신도.**"""

    kind: str
    id: str
    """종류 안에서 이 항목을 다시 찾는 열쇠 — 원래 자리의 id 이거나 거기서 만든 글자."""
    question: str
    evidence: list[str]
    choices: list[Choice]
    recommended: str | None
    """추천 — `choices` 의 값 하나. 없으면 사람이 골라야 한다."""
    confidence: float | None
    """추천의 확신도(0~1). 「추천대로 모두」 가 이 값을 문턱과 견준다."""
    reason: str
    """왜 이 추천인가 — 확신도의 근거를 사람 말로."""
    type_slug: str
    type_label: str
    workspace_id: uuid.UUID | None
    rows: int = 1
    """결정 하나가 손대는 줄 수 — 같은 지워진 객체를 가리키는 칸 300행이면 300."""
    group: str = ""
    """「같은 것끼리 묶기」 의 열쇠 — 같은 말 · 같은 지워진 객체 · 같은 출처. 비면 안
    묶는다."""
    group_label: str = ""
    blocked: str | None = None
    """이 사람이 이 항목을 처리할 수 없는 까닭(남의 부서 · 잠긴 타입). 없으면 None."""
    link: str = ""
    """원래 화면 — 거기서 더 보거나 처리한다."""
    signature: str = ""
    """내용의 지문 — 「무시」 를 결정 기록으로만 기억하는 종류가 쓴다(이름이 같은 객체가 하나
    더 생기면 지문이 바뀌어 다시 뜬다)."""
    ignorable: bool = True
    priority: float = 0.0
    """같은 종류 안의 차례 — 클수록 위(찾은 횟수 · 줄 수)."""
    heavy: bool = False
    """요청 안에서 처리하기에 큰 것 — 그러면 그 묶음은 작업으로 간다."""
    payload: dict[str, Any] = field(default_factory=dict)
    """제공자가 `check` · `apply` 에서 다시 쓰는 것 — 화면에는 안 나간다."""


@dataclass
class Collected:
    items: list[Item]
    total: int
    """원래 자리에 있는 수 — `items` 는 그중 읽은 만큼(상한)이다."""
    notes: list[str] = field(default_factory=list)
    """빠진 것 · 어림한 것 — 「큰 타입은 훑지 않았다」 같은 말. 0건의 이유도 여기."""


@dataclass
class Check:
    """결정 하나의 미리 보기."""

    status: str
    """`apply`(적용된다) · `blocked`(막힌다 — 까닭은 `message`)."""
    effect: str
    """무엇이 되나 — 「「앤시스」 → Ansys 의 별칭」."""
    choice_label: str
    message: str = ""
    rows: int = 0
    undoable: bool = False
    heavy: bool = False


@dataclass
class Applied:
    """결정 하나를 처리한 결과."""

    rows: int
    message: str = ""
    undo: dict[str, Any] = field(default_factory=dict)
    """되돌릴 때 쓸 것 — 원래 기능의 되돌리기에 넘길 값(묶음 번호 · 그때의 별칭 …)."""


#: (db, 보는 사람) → 이 종류를 볼 수 있나.
CanView = Callable[[Session, User], bool]
#: (db, 보는 사람, 거르기) → 항목들.
Collect = Callable[[Session, User, Filters], Collected]
#: (db, 사람, 항목, 고른 값) → 미리 보기.
CheckOne = Callable[[Session, User, Item, str], Check]
#: (db, 사람, 항목, 고른 값, 작업 안인가) → 처리. 막히면 `AppError` 를 던진다.
ApplyOne = Callable[[Session, User, Item, str, bool], Applied]
#: (db, 사람, 되돌릴 것, 적용하나) → (되돌릴 수 있나, 말). 적용하면 처리까지 — 커밋하지 않는다.
UndoOne = Callable[[Session, User, dict[str, Any], bool], tuple[bool, str]]


@dataclass(frozen=True)
class Provider:
    kind: str
    label: str
    description: str
    link: str
    """그 종류의 원래 화면."""
    can_view: CanView
    collect: Collect
    check: CheckOne | None = None
    """없으면 처리 길이 없는 종류 — 「원래 화면으로」 링크만 낸다."""
    apply: ApplyOne | None = None
    undo: UndoOne | None = None
    undo_note: str = ""
    """되돌릴 수 없으면 왜 — 미리 보기가 그대로 싣는다."""
    maintenance_keys: tuple[str, ...] = ()
    """홈 「남은 일」 의 어느 줄이 이 종류인가 — 그 줄의 링크가 검토함의 이 종류로 간다."""
    order: int = 100


_registry: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """같은 것을 두 번 넣어도 안전하다 — `create_app()` 은 시험에서 여러 번 불린다."""
    _registry[provider.kind] = provider


def get(kind: str) -> Provider | None:
    return _registry.get(kind)


def providers() -> list[Provider]:
    return sorted(_registry.values(), key=lambda one: (one.order, one.kind))
