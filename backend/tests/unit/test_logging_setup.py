"""파일 로그를 여러 프로세스가 함께 쓴다 — 날마다 한 번만 돌리고, 모두 새 파일로 옮겨 간다
(`app/logging_setup.py`)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.logging_setup import SharedDailyFileHandler

pytestmark = pytest.mark.skipif(os.name == "nt", reason="fcntl 이 있는 곳에서만")


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at.timestamp()


def _line(handler: logging.Handler, text: str) -> None:
    handler.emit(logging.LogRecord("t", logging.INFO, __file__, 1, text, None, None))


def _touch(path: Path, at: datetime) -> None:
    os.utime(path, (at.timestamp(), at.timestamp()))


def test_먼저_돌린_쪽만_옮기고_다른_프로세스도_새_파일에_쓴다(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    yesterday = datetime(2026, 11, 7, 23, 50)
    clock = _Clock(yesterday)
    # 두 프로세스 — 같은 파일을 따로 연다.
    app = SharedDailyFileHandler(log, keep_days=30, clock=clock)
    worker = SharedDailyFileHandler(log, keep_days=30, clock=clock)
    _line(app, "앱 어제")
    _line(worker, "워커 어제")
    _touch(log, yesterday)

    clock.at = datetime(2026, 11, 8, 0, 5)  # 자정이 지났다
    _line(app, "앱 오늘")  # 앱이 먼저 돌린다
    _line(worker, "워커 오늘")  # 워커는 이미 새 파일인 것을 보고 그 파일로 옮겨 간다
    for one in (app, worker):
        one.close()

    old = (tmp_path / "app.log.2026-11-07").read_text(encoding="utf-8")
    new = log.read_text(encoding="utf-8")
    assert old.splitlines() == ["앱 어제", "워커 어제"]
    assert new.splitlines() == ["앱 오늘", "워커 오늘"]  # 어제 파일에 쓰는 프로세스가 없다
    assert sorted(p.name for p in tmp_path.glob("app.log.2*")) == ["app.log.2026-11-07"]


def test_새로_뜬_프로세스는_첫_줄에서_어제_파일을_돌린다(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("어제 줄\n", encoding="utf-8")
    _touch(log, datetime(2026, 11, 7, 22, 0))
    handler = SharedDailyFileHandler(log, keep_days=30, clock=_Clock(datetime(2026, 11, 8, 9)))
    _line(handler, "오늘 첫 줄")
    handler.close()
    assert (tmp_path / "app.log.2026-11-07").read_text(encoding="utf-8") == "어제 줄\n"
    assert log.read_text(encoding="utf-8") == "오늘 첫 줄\n"


def test_오래된_것은_지우고_같은_날짜는_덮지_않는다(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    today = datetime(2026, 11, 8, 9)
    for days in (1, 29, 31, 40):
        day = (today - timedelta(days=days)).date().isoformat()
        (tmp_path / f"app.log.{day}").write_text(day, encoding="utf-8")
    (tmp_path / "other.log.2020-01-01").write_text("남의 것", encoding="utf-8")
    log.write_text("어제 줄\n", encoding="utf-8")
    _touch(log, today - timedelta(days=1))
    handler = SharedDailyFileHandler(log, keep_days=30, clock=_Clock(today))
    _line(handler, "오늘")
    handler.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "app.log.2026-11-07" in names and "app.log.2026-11-07.1" in names  # 덮지 않았다
    assert (tmp_path / "app.log.2026-11-07.1").read_text(encoding="utf-8") == "어제 줄\n"
    assert "app.log.2026-10-10" in names  # 29일 전 — 남긴다
    assert "app.log.2026-10-08" not in names and "app.log.2026-09-29" not in names
    assert "other.log.2020-01-01" in names
