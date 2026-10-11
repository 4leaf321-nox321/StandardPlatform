"""워커는 **앱과 같은 조립**으로 돈다 — 따로 띄운 프로세스에서 본다(2026-10-10).

시험은 앱을 띄운 프로세스 안에서 워커(`process_one`)를 돌린다 — 그 프로세스에는 앱이 건 등록이
이미 있어서, 운영의 워커(`python -m app.worker`, `app.main` 을 안 거친다)에 등록이 **하나도
없다**는 것을 못 봤다. 그래서 사진 일괄 업로드의 적용은 운영에서 첨부 자리를 몰라 객체의 부서
대신 전역으로 판정했고, 부서 자료 이동은 멤버 말고는 옮길 종류를 몰랐다. 여기서는 새 프로세스를
띄워 본다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

_PROBE = """
import json
from app import worker
from app.shared import events, extensions
before = len(extensions._contents)
worker._assemble()
print(json.dumps({
    "before": before,
    "contents": sorted(one.__module__ for one in extensions._contents),
    "owners": sorted(extensions._attachment_owners),
    "listeners": sorted(f"{one.__module__}.{one.__name__}" for one in events._listeners),
    "summary_only": sorted(f"{one.__module__}.{one.__name__}" for one in events._summary_only),
}))
"""


def test_워커는_앱과_같은_등록으로_돌고_웹훅은_작업마다_요약만_받는다(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "FILESTORE_DIR": str(tmp_path / "filestore"),
        "LOG_DIR": str(tmp_path / "logs"),
        "EXTENSIONS": "",
    }
    ran = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    found = json.loads(ran.stdout.strip().splitlines()[-1])
    # 조립 전에는 비어 있다 — 이것이 운영 워커가 처음에 놓였던 자리다.
    assert found["before"] == 0
    assert {
        "app.modules.objects.services",
        "app.modules.files.services",
        "app.modules.datasources.services",
        "app.modules.metrics.services",
    } <= set(found["contents"])
    assert found["owners"] == ["objects"]
    # 변경 이벤트도 앱과 같이 듣는다 — 예전에는 비워 둬서 작업으로 한 일은 웹훅 · 지켜보기
    # 알림이 하나도 안 갔다. 웹훅은 작업 안에서 줄마다 말고 요약 한 건만(2026-10-10).
    assert found["listeners"] == [
        "app.modules.objects.watches.on_events",
        "app.modules.webhooks.services.on_events",
    ]
    assert found["summary_only"] == ["app.modules.webhooks.services.on_events"]
