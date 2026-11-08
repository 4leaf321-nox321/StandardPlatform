"""로그 — 콘솔과 파일 양쪽에. 콘솔만으로는 창을 닫는 순간 증거가 사라진다.

## 파일 하나를 여러 프로세스가 쓴다

`app.log` 에는 앱(uvicorn 작업자 여럿일 수 있다) · 작업 워커 · 동기화 · 지표 타이머가 함께
쓴다. 예전의 `TimedRotatingFileHandler` 는 프로세스마다 **따로** 자정에 파일을 돌렸다 — 먼저
돌린 쪽이 `app.log` 를 `app.log.<날짜>` 로 옮기면, 나머지는 옮겨진 어제 파일에 계속 썼다. 30일
뒤 그 파일이 지워지면 그 프로세스의 로그는 지워진 파일로 들어가 아무 데도 안 보였다(journald
에는 남는다 — 2026-11-08 점검).

그래서 `SharedDailyFileHandler`:

- **돌리기는 한 번만** — 날이 바뀐 뒤 처음 쓰는 프로세스가 잠금(`app.log.lock`,
  `fcntl.flock`)을 잡고, `app.log` 가 아직 지난날의 것일 때만(마지막으로 쓴 날로 본다)
  `app.log.<그날>` 로 옮기고 오래된 것을 지운다. 늦게 온 프로세스는 이미 새 파일인 것을 보고
  건너뛴다.
- **모두 새 파일로** — `WatchedFileHandler` 라 쓰기 전에 파일이 바뀐 것(옮김 · 지움)을 보고
  다시 연다.
- 이름은 예전과 같다(`app.log.YYYY-MM-DD`) — 운영 안내 · 찾는 습관이 그대로다.

`fcntl` 이 없는 곳(Windows)은 예전 핸들러로 — 열린 파일을 옮길 수 없다.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler, WatchedFileHandler
from pathlib import Path

from app.config import Settings
from app.shared.request_context import get_request_id

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

#: 지난날의 파일 이름 — `app.log.2026-11-07`(예전 `TimedRotatingFileHandler` 와 같은 꼴).
_DATED = re.compile(r"^(?P<stem>.+)\.(?P<day>\d{4}-\d{2}-\d{2})(?:\.\d+)?$")


class SharedDailyFileHandler(WatchedFileHandler):
    """여러 프로세스가 함께 쓰는 날마다 돌리는 파일 — 모듈 머리말. `clock` 은 시험이 바꾼다."""

    def __init__(
        self,
        filename: str | Path,
        *,
        keep_days: int,
        encoding: str = "utf-8",
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(filename, encoding=encoding, delay=True)
        self.keep_days = keep_days
        self.clock = clock
        self.lock_path = f"{self.baseFilename}.lock"
        # 첫 줄을 쓸 때 한 번 본다 — 밤새 아무도 안 써 어제 파일이 그대로면 그때 돌린다.
        self._next = 0.0

    @staticmethod
    def _midnight_after(now: float) -> float:
        tomorrow = datetime.fromtimestamp(now).date() + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.min.time()).timestamp()

    def emit(self, record: logging.LogRecord) -> None:
        now = self.clock()
        if now >= self._next:
            with contextlib.suppress(OSError):
                self.rollover(now)
            self._next = self._midnight_after(now)
        super().emit(record)  # 파일이 바뀌었으면 다시 연다(WatchedFileHandler)

    def rollover(self, now: float) -> None:
        """잠금을 잡고, `app.log` 가 지난날의 것이면 그날 이름으로 옮기고 오래된 것을
        지운다."""
        today = datetime.fromtimestamp(now).date()
        with open(self.lock_path, "a", encoding="utf-8") as lock:
            assert fcntl is not None
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    written = datetime.fromtimestamp(
                        os.stat(self.baseFilename).st_mtime
                    ).date()
                except FileNotFoundError:
                    written = today
                if written < today:
                    os.replace(self.baseFilename, self._free_name(written))
                self._prune(today)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _free_name(self, day: date) -> str:
        target = f"{self.baseFilename}.{day.isoformat()}"
        serial = 1
        while os.path.exists(target):  # 손으로 옮겨 둔 같은 날짜가 있으면 덮지 않는다
            target = f"{self.baseFilename}.{day.isoformat()}.{serial}"
            serial += 1
        return target

    def _prune(self, today: date) -> None:
        base = Path(self.baseFilename)
        oldest = today - timedelta(days=self.keep_days)
        for one in base.parent.glob(f"{base.name}.*"):
            found = _DATED.match(one.name)
            if found is None or found["stem"] != base.name:
                continue
            if date.fromisoformat(found["day"]) < oldest:
                with contextlib.suppress(OSError):
                    one.unlink()


_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def setup_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.app_env == "development" else logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT)
    id_filter = _RequestIdFilter()

    # 콘솔을 UTF-8 로 고정한다. Windows 콘솔 기본 코드페이지(949)로는 줄표 같은
    # 문자를 인코딩하지 못해 **로그 한 줄마다 예외가 나고**, logging 이 그 예외를
    # 대신 출력해 정작 찾으려던 줄이 트레이스백에 묻힌다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(id_filter)
    root.addHandler(console)

    file_handler: logging.Handler
    if fcntl is not None:
        file_handler = SharedDailyFileHandler(
            settings.log_dir / "app.log", keep_days=settings.log_retention_days
        )
    else:  # pragma: no cover - Windows: 열린 파일을 옮길 수 없다
        file_handler = TimedRotatingFileHandler(
            settings.log_dir / "app.log",
            when="midnight",
            backupCount=settings.log_retention_days,
            encoding="utf-8",
        )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(id_filter)
    root.addHandler(file_handler)

    # uvicorn 의 기동·오류·접근 로그를 전부 우리 핸들러로 흘린다. 그러면 접근
    # 로그 줄에도 요청 id 가 붙는다 — 우리가 미들웨어에서 한 줄 더 찍으면 같은
    # 요청이 두 줄이 될 뿐이다.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
