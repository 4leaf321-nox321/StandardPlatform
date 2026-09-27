"""서버 화면의 응답 형태 — **지금 이 설치가 어떤 상태인가.**"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
    app_slug: str
    extensions: list[str]
    """지금 켜져 있는 확장 — `.env` 가 아니라 **화면에서 정한 것**이다."""
    extensions_unknown: list[str]
    """`.env` 에 적혔는데 이 번들에 없는 이름. **오타는 여기서만 드러난다.**"""
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


class ExtensionOut(BaseModel):
    """확장 하나의 켜짐 — **시스템 관리자 화면이 그리는 줄.**"""

    name: str
    enabled: bool
    pinned: bool
    """화면에서 지정했나. 거짓이면 `.env` 의 `EXTENSIONS` 가 답한 것이다 —
    **아무것도 안 켜 본 설치는 예전과 똑같이 돈다.**"""
    updated_at: datetime | None


class ExtensionPatchIn(BaseModel):
    enabled: bool


class ExtensionEndpointOut(BaseModel):
    """확장의 부를 수 있는 자리 하나 — **경로는 확장 뿌리부터.**"""

    method: str
    path: str
    """`dt/pairs` 처럼 확장 이름 뒤부터. 앞에 `/api/ext/<이름>/` 이 붙는다."""
    summary: str
    """그 자리의 한 줄 설명(독스트링 첫 줄)."""
    query: list[str] = Field(default_factory=list)
    """물음표 뒤에 붙는 것들 — 필수는 뒤에 `*`."""
    body: list[str] = Field(default_factory=list)
    """본문 칸 이름들 — 필수는 뒤에 `*`. 한 단만 펼친다."""


class ExtensionApiOut(BaseModel):
    """켠 확장 하나와 그 자리들."""

    name: str
    endpoints: list[ExtensionEndpointOut]


class MaintenanceItemOut(BaseModel):
    """남은 일 하나. **홈이 이것을 보여 준다** — 관리 화면에 들어가야만 보이면
    아무도 안 본다."""

    key: str
    label: str
    count: int
    link: str | None
    severity: str
    """info · warning. 경고는 색이 붙는다."""
