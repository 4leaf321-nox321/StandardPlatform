"""코어 카탈로그의 판(`revision`) — **같은 구조면 어느 프로세스에서도 같은 값.**

받는 쪽은 이 값이 바뀌면 「구조가 달라졌다」 를 통지한다. 허브를 다시 띄울 때마다 값이
달라지면 그 통지는 곧 아무도 안 읽는다 — 그리고 진짜 변경도 함께 묻힌다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.modules.coreapi.services import revision_of

MARKS = ["plm_model:task:object_ref>plm_task,series:enum=X1/X2;plm_derived_from"]


def test_판은_정해진_값이다() -> None:
    """값을 박아 둔다 — 내장 `hash()` 로 돌아가면 실행마다 씨가 달라 여기서 걸린다."""
    assert revision_of(MARKS) == "1-9669625867"


def test_판은_해시_씨가_달라도_같다() -> None:
    """허브를 다시 띄운 것과 같다 — 씨를 바꾼 두 프로세스가 같은 판을 내야 한다."""
    root = Path(__file__).resolve().parents[2]
    code = (
        f"from app.modules.coreapi.services import revision_of;print(revision_of({MARKS!r}))"
    )
    seen = {
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2")
    }
    assert seen == {revision_of(MARKS)}


def test_판은_구조가_바뀌면_바뀐다() -> None:
    assert revision_of(MARKS) != revision_of([MARKS[0] + ",x:text"])
    assert revision_of(MARKS) != revision_of([*MARKS, "plm_task:"])
