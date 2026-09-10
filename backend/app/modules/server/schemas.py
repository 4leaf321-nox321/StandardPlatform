"""서버 화면의 응답 형태 — **지금 이 설치가 어떤 상태인가.**"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DiskOut(BaseModel):
    path: str
    total_bytes: int
    free_bytes: int
    used_percent: float


class TableCountOut(BaseModel):
    label: str
    count: int


class BackupOut(BaseModel):
    """마지막 백업이 언제였나. **「없다」 와 「설정이 없다」 를 구별한다** —
    같게 말하면 사람은 안 해도 되는 일을 하러 간다."""

    configured: bool
    path: str | None
    last_at: datetime | None
    age_hours: float | None
    stale: bool
    problem: str | None


class ServerStatusOut(BaseModel):
    app_name: str
    version: str
    app_env: str
    database_url_safe: str
    """비밀번호를 지운 접속 문자열. **어느 DB 를 보고 있는지**가 문제 추적의
    첫 물음인데, 여러 플랫폼이 한 서버에 있으면 그것을 알 방법이 없다."""
    schema_head: str | None
    schema_current: str | None
    schema_behind: bool
    """DB 가 코드보다 뒤처져 있나. **화면이 말해 주지 않으면** 사람은 그 사실을
    엉뚱한 화면의 500 으로 만난다."""
    disk: DiskOut | None
    backup: BackupOut
    counts: list[TableCountOut]
    """`shared/extensions.py` 의 레지스트리가 채운다 — 도메인이 등록한 만큼 는다."""
    started_at: datetime


class MaintenanceItemOut(BaseModel):
    """남은 일 하나. **홈이 이것을 보여 준다** — 관리 화면에 들어가야만 보이면
    아무도 안 본다."""

    key: str
    label: str
    count: int
    link: str | None
    severity: str
    """info · warning. 경고는 색이 붙는다."""
