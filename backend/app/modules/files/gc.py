"""첨부 저장소의 **고아 파일 정리** — 아무 첨부도 안 가리키는 파일을 지운다(ADR 0012).

filestore 는 내용 주소(sha256)라 같은 내용을 여러 첨부가 가리킬 수 있다. 그래서 첨부를 지울
때는 **행만** 지우고(`deleted_at`) 파일은 남겼다 — 그 사이 거절된 업로드 · 뗀 첨부 · 지운
타입의 파일이 디스크에 쌓이기만 했다. 여기서 따로 훑어 「살아 있는 첨부 행이 하나도 안
가리키는 파일」 만 지운다.

**지우는 것** — 둘뿐이다.
- 내용 주소 모양(`ab/cd/<sha256 64자>`)의 파일 중 살아 있는 첨부(원본 · 미리보기)가 안 가리키고
  **하루가 지난** 것. 하루를 두는 이유: 업로드는 파일을 먼저 쓰고 행을 나중에 넣는다 — 그
  사이의 파일은 아직 아무도 안 가리킨다.
- `_incoming/` 의 하루 지난 임시 파일(끊긴 업로드의 찌꺼기).

**안 지우는 것** — 그 밖의 모든 것. filestore 아래에 다른 것을 두는 설치가 있다(데이터 소스의
파일 폴더 `incoming/` 을 이 아래에 두라고 권한다). 모양이 다르면 손대지 않는다. **큰 작업
파일의 폴더(`_jobs/`)는 아예 안 들어간다**(2026-10-10) — 그 파일은 첨부가 아니라 작업의 것이고,
작업 파일의 만료(`jobs/files.purge_expired`)가 지운다. 여기서 「아무 첨부도 안 가리킨다」 로
지우면 계획을 본 뒤 적용을 기다리는 사진 zip 이 사라진다.

**지운 첨부를 되살리는 길은 없다** — 휴지통에 간 객체의 첨부는 행이 살아 있어(객체만 지운다)
그대로 남는다. 지우기 직전에 한 번 더 본다: 그 사이 같은 내용이 다시 올라와 행이 생겼거나
(`filestore.save` 가 같은 내용이면 파일 시각을 새로 한다) 시각이 새로워졌으면 남긴다.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.files.models import Attachment
from app.modules.notifications import services as notifications
from app.modules.notifications.models import Notification
from app.shared import filestore

log = logging.getLogger(__name__)

#: 이보다 새 파일은 고아여도 안 지운다 — 업로드는 파일을 먼저 쓰고 행을 나중에 넣는다.
GRACE = timedelta(hours=24)
#: 내용 주소 모양 — 이것만 지울 수 있다.
CONTENT_PATH = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")
INCOMING = "_incoming"
#: 큰 작업 파일의 폴더(`jobs/files.JOBS_DIR`) — 첨부가 아니다. 이름만 맞춘다(files 가 jobs 를
#: import 하지 않게).
JOBS = "_jobs"
#: 결과에 싣는 예시 수.
SAMPLE = 20
#: 자동 정리가 멈추는 문턱 — 저장소의 파일 가운데 **이 DB 가 모르는 것**(하루 지난 고아와
#: 오늘 올린 고아)이 이 비율보다 많고 하루 지난 고아가 `FOREIGN_FLOOR` 개 이상이거나,
#: **전부를** 모르면 지우지 않는다. 남의 저장소면 오늘 올린 파일까지 모른다 — 그래서 오늘
#: 올린 고아는 「모르는 것」 에 넣고, 오늘 올려 가리키는 파일은 「아는 것」 에 넣는다(사진
#: 일괄 「교체」 로 옛 사진 100장이 고아가 된 다음 날, 새 사진 100장이 아는 것이라 멈추지
#: 않는다).
FOREIGN_LIMIT = 0.5
#: 그 비율을 보는 가장 작은 고아 수 — 첨부 몇 개짜리 저장소에서 한두 개를 뗀 것은 정상이다.
#: 전에는 바닥이 20이고 「모르는 것」 에 오늘 올린 고아를 안 넣어, 하루 지난 고아 16개에 오늘
#: 올린 파일 하나만 있어도 16개를 지웠다(2026-11-27 점검 — 개발 환경에서 원본이 사라진 첨부도
#: 16개였다).
FOREIGN_FLOOR = 3
#: 자동 정리가 멈췄다는 알림을 이 안에 또 보내지 않는다 — 사람이 정리할 때까지 차례마다 멈추고,
#: 차례가 한 시간마다인 설치(`FILESTORE_GC_HOURS=1`)도 있다.
HALT_NOTICE_EVERY = timedelta(hours=20)

Progress = Callable[[str, int, int], None] | None


@dataclass
class Found:
    """훑은 결과 — 지울 것과 그 크기."""

    orphans: list[tuple[str, int]] = field(default_factory=list)
    """(상대 경로, 바이트) — 아무 첨부도 안 가리키고 하루 지난 것."""
    temp: list[tuple[str, int]] = field(default_factory=list)
    """`_incoming/` 의 하루 지난 임시 파일."""
    recent: int = 0
    """고아지만 하루가 안 지나 남긴 것."""
    scanned: int = 0

    @property
    def orphan_bytes(self) -> int:
        return sum(size for _, size in self.orphans)

    @property
    def temp_bytes(self) -> int:
        return sum(size for _, size in self.temp)


def _referenced(db: Session) -> set[str]:
    """살아 있는 첨부가 가리키는 파일(원본 · 미리보기)."""
    out: set[str] = set()
    for original, thumb in db.execute(
        select(Attachment.relative_path, Attachment.thumb_path).where(
            Attachment.deleted_at.is_(None)
        )
    ):
        out.add(original)
        if thumb:
            out.add(thumb)
    return out


def scan(db: Session, *, now: datetime | None = None, progress: Progress = None) -> Found:
    """filestore 를 훑어 지울 것을 고른다 — **아무것도 안 지운다.**"""
    base = filestore.root()
    found = Found()
    if not base.is_dir():
        return found
    cutoff = (now or datetime.now(UTC)).timestamp() - GRACE.total_seconds()
    referenced = _referenced(db)
    tops = sorted(
        (entry for entry in os.scandir(base) if entry.is_dir(follow_symlinks=False)),
        key=lambda entry: entry.name,
    )
    for index, top in enumerate(tops):
        if progress is not None:
            progress("훑기", index, len(tops))
        if top.name == JOBS:
            continue
        if top.name == INCOMING:
            for entry in os.scandir(top.path):
                if entry.is_file(follow_symlinks=False) and entry.name.startswith("tmp"):
                    # 훑는 사이 사라질 수 있다 — 업로드가 끝나 옮겼거나, 같은 저장소를 쓰는
                    # 다른 서버의 정리가 지웠다. 그 파일 하나로 정리 전체가 멈추면 안 된다.
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if info.st_mtime < cutoff:
                        found.temp.append((f"{INCOMING}/{entry.name}", info.st_size))
            continue
        for path in Path(top.path).glob("*/*"):
            relative = path.relative_to(base).as_posix()
            if not CONTENT_PATH.match(relative):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # 다른 서버의 정리가 방금 지웠다
            if not stat.S_ISREG(info.st_mode):
                continue  # 폴더 · 심볼릭 링크
            found.scanned += 1
            fresh = info.st_mtime >= cutoff
            if relative in referenced:
                continue
            if fresh:
                found.recent += 1
                continue
            found.orphans.append((relative, info.st_size))
    return found


@dataclass
class Cleaned:
    files: int = 0
    bytes: int = 0
    temp: int = 0
    kept: int = 0
    """훑은 뒤 지우기 직전에 다시 보니 가리키는 행이 생겼거나 새로워져 남긴 것."""


def looks_foreign(found: Found) -> str:
    """이 DB 가 저장소의 파일 대부분을 모른다 — **다른 DB 의 저장소일 수 있다.** 까닭 또는 빈
    글자.

    파일스토어는 DB 와 짝인데 그것을 묶는 표지가 없다. 다른 DB(시험 · 규모 실측 · 복원
    리허설)에 붙은 워커가 같은 폴더를 보면, 그 DB 가 가리키지 않는 파일이 모두 고아로 보여 하루
    지난 것이 다 지워졌다(개발 환경의 첨부 16개 원본이 그렇게 사라진 것으로 본다, 2026-11-26).
    자동 정리는 여기서 멈추고, 사람이 「지금 세어 보기」 의 계획을 읽고 적용할 때만 지운다.

    **이 DB 가 모르는 파일**(하루 지난 고아 + 오늘 올린 고아)을 훑은 전부와 견준다 —
    전에는 오늘 올린 고아를 「모르는 것」 에 안 넣어, 오늘 올린 파일 하나가 「전부 고아」 를
    「하나 빼고 고아」 로 바꿔 멈춤을 비껴 갔다."""
    orphans = len(found.orphans)
    if not orphans:
        return ""
    unknown = orphans + found.recent
    scanned = max(found.scanned, unknown)
    if unknown >= scanned or (orphans >= FOREIGN_FLOOR and unknown > FOREIGN_LIMIT * scanned):
        return (
            f"저장소의 파일 {scanned:,}개 가운데 {unknown:,}개를 이 DB 의 어느 첨부도 "
            f"가리키지 않습니다(하루 지난 것 {orphans:,}개) — 다른 DB 의 저장소를 보고 "
            "있을 수 있습니다(FILESTORE_DIR · "
            "DATABASE_URL 을 확인하세요). 자동 정리는 삭제하지 않습니다 — 이 설치의 저장소가 "
            "맞으면 서버 화면의 「고아 파일 지금 세어 보기」 로 계획을 보고 적용합니다."
        )
    return ""


def halt(db: Session, found: Found, reason: str, *, now: datetime | None = None) -> bool:
    """자동 정리를 멈췄다 — 로그에 남기고 **시스템 관리자에게 알린다**(`HALT_NOTICE_EVERY` 안에
    한 번). 알렸으면 참. 커밋한다.

    임시 파일(`_incoming/`)의 정리도 함께 미룬다 — 저장소가 이 설치의 것이 아닐 수 있다. 로그만
    남기면 아무도 안 읽어, 고아가 쌓이는 것도 저장소가 남의 것인 것도 모른 채 지나간다."""
    deferred = (
        f" 임시 파일(_incoming) {len(found.temp):,}개의 정리도 함께 미룹니다."
        if found.temp
        else ""
    )
    log.warning("고아 첨부 정리를 멈춤: %s%s", reason, deferred)
    since = (now or datetime.now(UTC)) - HALT_NOTICE_EVERY
    recent = db.scalar(
        select(Notification.id)
        .where(
            Notification.kind == notifications.FILES_CLEANUP_HALTED,
            Notification.created_at >= since,
        )
        .limit(1)
    )
    if recent is not None:
        return False
    notifications.notify_system_admins(
        db,
        kind=notifications.FILES_CLEANUP_HALTED,
        title="첨부 저장소의 자동 정리를 멈췄습니다",
        body=reason + deferred,
        link="/admin/server",
    )
    db.commit()
    return True


def clean(db: Session, found: Found, *, now: datetime | None = None) -> Cleaned:
    """고른 것을 지운다 — **지우기 직전에 한 번 더 본다**(그 사이 같은 내용이 올라왔나)."""
    base = filestore.root()
    cutoff = (now or datetime.now(UTC)).timestamp() - GRACE.total_seconds()
    done = Cleaned()
    wanted = [relative for relative, _ in found.orphans]
    again: set[str] = set()
    for start in range(0, len(wanted), 1000):
        chunk = wanted[start : start + 1000]
        for original, thumb in db.execute(
            select(Attachment.relative_path, Attachment.thumb_path).where(
                Attachment.deleted_at.is_(None),
                or_(Attachment.relative_path.in_(chunk), Attachment.thumb_path.in_(chunk)),
            )
        ):
            again.add(original)
            if thumb:
                again.add(thumb)
    for relative, size in found.orphans:
        path = base / relative
        try:
            if relative in again or path.stat().st_mtime >= cutoff:
                done.kept += 1
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        done.files += 1
        done.bytes += size
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()  # 비었을 때만 지워진다
            except OSError:
                break
    for relative, _size in found.temp:
        try:
            (base / relative).unlink()
        except FileNotFoundError:
            continue
        done.temp += 1
    return done
