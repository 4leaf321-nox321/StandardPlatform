"""도메인이 공통 화면에 자기를 끼우는 자리 — **세 개뿐이다.**

공통 틀에는 세 개의 "모두가 보는 화면" 이 있고, 그 셋은 도메인이 무엇인지 모른다.

    홈의 「남은 일」      -> maintenance   무엇이 아직 안 채워졌나
    서버 화면의 「쌓인 것」 -> stats         무엇이 얼마나 있나
    부서 삭제 확인       -> references    무엇이 이 부서를 가리키나

## 왜 레지스트리인가

`shared` 가 도메인 모델을 import 하면 방향이 거꾸로 서고, 그때 import 순서 하나로
서버가 안 뜬다. 그래서 **도메인이 등록하고 공통이 부른다.** 등록은 각 모듈의
`__init__.py` 가 아니라 `app/main.py` 가 부르는 `register()` 하나에서 한다 —
조립 지점이 하나여야 "이게 왜 안 뜨지" 를 물을 자리가 생긴다.

## 세 화면이 지키는 규칙

- **0 건인 항목은 안 내보낸다**(maintenance). 다 0 인 목록을 매일 보면 사람은 그
  자리를 아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다.
- **막는 참조는 반드시 표시한다**(references). 누르기 전에 아는 것이 이 틀의
  무늬다 — 지우고 나서 「장비 12대가 사라졌다」 를 알게 되면 되돌릴 방법이 없다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.accounts.models import User


@dataclass(frozen=True)
class MaintenanceItem:
    """홈의 「남은 일」 한 줄."""

    key: str
    label: str
    count: int
    link: str | None = None
    severity: str = "info"
    """info · warning. 경고는 색이 붙는다 — **아무거나 경고로 두지 않는다.**
    전부 노란색이면 노란색은 아무 뜻도 못 갖는다."""


@dataclass(frozen=True)
class StatItem:
    """서버 화면의 「쌓인 것」 한 칸."""

    label: str
    count: int


@dataclass(frozen=True)
class WorkspaceReference:
    """이 부서를 가리키는 참조 하나."""

    table: str
    label: str
    count: int
    blocks_delete: bool
    """지우려면 먼저 정리해야 하는가. **DB 가 RESTRICT 로 거부하는 것은 반드시
    True 다** — False 로 두면 화면은 지울 수 있다고 말하고 서버는 500 을 낸다."""


#: 홈이 부르는 것들. 보는 사람에 따라 다른 답을 낼 수 있어 User 를 받는다
#: (가입 승인 대기는 시스템 관리자에게만 뜬다).
MaintenanceProvider = Callable[[Session, User], list[MaintenanceItem]]

#: 서버 화면이 부르는 것들.
StatProvider = Callable[[Session], list[StatItem]]

#: 부서 삭제 확인이 부르는 것들.
ReferenceProvider = Callable[[Session, uuid.UUID], list[WorkspaceReference]]

_maintenance: list[MaintenanceProvider] = []
_stats: list[StatProvider] = []
_references: list[ReferenceProvider] = []


# 셋 다 **같은 것을 두 번 넣어도 안전하다.** `create_app()` 은 시험에서 두 번
# 불릴 수 있고, 그때 목록이 늘면 홈이 같은 줄을 두 번 보여 준다 — 그 화면을 보는
# 사람은 그것을 데이터가 두 배라고 읽는다.


def register_maintenance(provider: MaintenanceProvider) -> None:
    if provider not in _maintenance:
        _maintenance.append(provider)


def register_stats(provider: StatProvider) -> None:
    if provider not in _stats:
        _stats.append(provider)


def register_workspace_reference(provider: ReferenceProvider) -> None:
    if provider not in _references:
        _references.append(provider)


def maintenance_items(db: Session, viewer: User) -> list[MaintenanceItem]:
    """**0 건은 버린다.** 거르는 자리를 여기 한 곳에 두면 등록하는 쪽이 매번
    같은 조건을 다시 적지 않아도 되고, 빠뜨릴 자리도 없다."""
    out: list[MaintenanceItem] = []
    for provider in _maintenance:
        out.extend(item for item in provider(db, viewer) if item.count)
    return out


def stat_items(db: Session) -> list[StatItem]:
    """**0 건도 내보낸다.** 여기는 「지금 이 설치에 뭐가 있나」 라서, 0 이라는
    답 자체가 정보다 — 남은 일과 성격이 다르다."""
    out: list[StatItem] = []
    for provider in _stats:
        out.extend(provider(db))
    return out


def workspace_references(db: Session, workspace_id: uuid.UUID) -> list[WorkspaceReference]:
    """**0 건은 버린다.** 안 걸린 참조를 늘어놓으면 정작 막는 하나가 묻힌다."""
    out: list[WorkspaceReference] = []
    for provider in _references:
        out.extend(item for item in provider(db, workspace_id) if item.count)
    return out
