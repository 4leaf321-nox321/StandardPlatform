"""서버·시스템 관리 라우터.

**한 화면이 답해야 하는 물음이 셋이다**: 지금 뭐가 깔렸나, DB 는 맞춰져 있나,
무엇이 얼마나 쌓였나. 셋을 따로 두면 아무도 다 보지 않는다. 그리고 문제가 났을 때
첫 물음은 언제나 "지금 서버 버전이 뭐냐" 와 "어느 DB 를 보고 있냐" 다.

**세는 것과 남은 일은 이 모듈이 모른다.** 도메인이 `shared/extensions.py` 에
등록하고 여기는 그것을 모아 낸다 — 공통 틀이 도메인 표를 import 하면 방향이
거꾸로 서고, 그때 import 순서 하나로 서버가 안 뜬다.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schema_version, version
from app.config import get_settings
from app.database import engine, get_db
from app.modules.accounts.models import User
from app.modules.server import services
from app.modules.server.schemas import (
    BackupOut,
    DiskOut,
    ExtensionOut,
    ExtensionPatchIn,
    MaintenanceItemOut,
    ServerStatusOut,
    TableCountOut,
)
from app.shared import extensions, ops
from app.shared.auth import current_user, require_system_admin

router = APIRouter(prefix="/server", tags=["server"])

#: 프로세스가 언제 떴나. 재시작을 눈으로 확인할 수 있는 유일한 값이다.
STARTED_AT = datetime.now(UTC)


def _safe_url(url: str) -> str:
    """비밀번호를 지운 접속 문자열. **화면에 그대로 뜨는 값이다.**"""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if ":" in head:
        scheme_user = head.rsplit(":", 1)[0]
        return f"{scheme_user}:***@{tail}"
    return f"{head}@{tail}"


@router.get("/status", response_model=ServerStatusOut)
def status(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> ServerStatusOut:
    settings = get_settings()
    head = schema_version.code_head()
    current = schema_version.db_revision(engine)

    disk: DiskOut | None = None
    try:
        # **filestore 폴더를 그대로 잰다 — 부모가 아니라.**
        #
        # 부모를 재면 컨테이너에서 틀린 값이 나온다: `/data` 는 이미지 안의
        # tmpfs 고 `/data/filestore` 만 호스트에 bind-mount 된다. 실측으로
        # 「전체 64MB, 여유 64MB」 가 나왔다 — 관리자가 보는 화면에서 그것은
        # 곧 터질 디스크로 읽히고, 정작 진짜 볼륨은 아무도 안 보게 된다.
        #
        # 이 폴더는 기동 가드(`_guard_writable_paths`)가 만들어 두므로 있다.
        usage = shutil.disk_usage(settings.filestore_dir)
        disk = DiskOut(
            path=str(settings.filestore_dir),
            total_bytes=usage.total,
            free_bytes=usage.free,
            used_percent=round((usage.total - usage.free) / usage.total * 100, 1),
        )
    except OSError:
        # 경로가 없거나 권한이 없다. **화면은 떠야 한다** — 디스크를 못 읽는다고
        # 서버 상태 전체를 못 보면 정작 원인을 볼 데가 없어진다.
        disk = None

    return ServerStatusOut(
        app_name=settings.app_name,
        app_slug=settings.app_slug,
        extensions=list(services.enabled_names(db)),
        extensions_unknown=list(services.unknown_defaults()),
        version=version.current(),
        app_env=settings.app_env,
        database_url_safe=_safe_url(settings.database_url),
        schema_head=head,
        schema_current=current,
        schema_behind=bool(head and current and head != current),
        disk=disk,
        backup=BackupOut(**vars(ops.backup_status())),
        counts=[
            TableCountOut(label=item.label, count=item.count)
            for item in extensions.stat_items(db)
        ],
        started_at=STARTED_AT,
    )


@router.get("/enabled-extensions", response_model=list[str])
def enabled_extensions(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[str]:
    """지금 켜진 확장 이름 — **화면이 메뉴를 그릴 때 묻는 자리.**

    `index.html` 의 메타는 페이지를 받은 순간의 사진이라, 관리자가 켜고 끈 뒤에도 화면은
    새로 고침 전까지 옛 메뉴를 들고 있다. 개발 서버(Vite)는 `backend/.env` 를 심으므로
    아예 안 맞는다 — **실측으로 「껐는데 메뉴가 그대로」 가 나왔다.** 그래서 목록은 여기서
    받고, 메타는 첫 그림의 씨앗으로만 쓴다.

    시스템 관리자만이 아니라 **누구나** 읽는다. 메뉴는 모든 사람이 그린다.
    """
    return list(services.enabled_names(db))


@router.get("/extensions", response_model=list[ExtensionOut])
def extension_list(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[ExtensionOut]:
    """이 번들에 든 확장과 그 켜짐. **고를 수 있는 것은 여기 있는 것뿐이다.**"""
    return [
        ExtensionOut(name=name, enabled=on, pinned=pinned, updated_at=at)
        for name, on, pinned, at in services.states(db)
    ]


@router.patch("/extensions/{name}", response_model=ExtensionOut)
def extension_toggle(
    name: str,
    payload: ExtensionPatchIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ExtensionOut:
    """확장을 켜거나 끈다 — **시스템 관리자만.**

    끄면 그 확장의 API 는 404 가 되고 메뉴 · 경로가 사라진다. **자료는 남는다** —
    표는 확장과 무관하게 이미 있고(마이그레이션은 전부 돈다), 다시 켜면 그대로다.

    화면은 `/server/enabled-extensions` 를 다시 받아 **새로 고침 없이** 따라온다.
    """
    services.set_enabled(db, user, name, payload.enabled)
    row = next(one for one in services.states(db) if one[0] == name)
    return ExtensionOut(name=row[0], enabled=row[1], pinned=row[2], updated_at=row[3])


@router.get("/maintenance", response_model=list[MaintenanceItemOut])
def maintenance(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MaintenanceItemOut]:
    """남은 일. **홈이 이것을 보여 준다.**

    관리 화면에 들어가야만 보이는 목록은 아무도 안 본다 — 승인 대기가 며칠씩
    방치되고, 안 채워진 자료는 영영 안 채워진다.

    **0 건인 항목은 안 내보낸다**(레지스트리가 거른다). 다 0 인 목록을 매일 보면
    사람은 그 자리를 아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다.
    """
    return [
        MaintenanceItemOut(
            key=item.key,
            label=item.label,
            count=item.count,
            link=item.link,
            severity=item.severity,
        )
        for item in extensions.maintenance_items(db, user)
    ]
