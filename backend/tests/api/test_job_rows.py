"""작업 결과의 **줄 목록**과 워커가 일을 집는 빠르기.

5만 줄 파일의 계획은 결과 하나가 17 ~ 19MB 다. 작업 목록이 그것을 작업마다 통째로 실어 목록
한 번이 93MB 였고, 작업 화면은 도는 작업이 있으면 그것을 3초마다 다시 받아 「적용」 을 누르면
화면이 멈춘 듯했다. 적용 뒤의 지표 다시 세기(시킨 사람 없음)가 워커를 잡아 바로 이은 적용이
그 뒤에 줄을 섰고, 빈 워커는 5초까지 늦춰 물었다(2026-10-09).

여기서 지키는 것: 목록은 줄 목록을 비우고 수만 싣나, 상세는 문제 줄을 앞세워 상한까지만
싣나, CSV 는 모든 줄을 주나, 사람이 넣은 작업을 먼저 집나, 작업을 넣으면 쉬던 워커가 깨나.
"""

from __future__ import annotations

import io
import threading
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.jobs import kinds, services
from app.modules.jobs.models import Job
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_type


def _submit(client: TestClient, who: Signed, type_slug: str, text: str) -> dict[str, Any]:
    got = client.post(
        f"/api/objects/{type_slug}/import",
        files={"file": ("rows.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
        data={"workspace_slug": who.workspace},
        headers=who.headers,
    )
    assert got.status_code == 202, got.text
    return dict(got.json())


def test_목록은_줄_목록을_빼고_상세는_문제_줄을_앞세워_자르고_CSV_는_전부(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "ROWS_SHOWN", 3)
    part = _make_type(client, admin, label="부품", key_policy="required")
    # 다섯째 줄은 식별자가 없어 오류다 — 파일 뒤쪽에 있어도 상세의 앞 세 줄에 든다.
    lines = ["key,label", "P-1,하나", "P-2,둘", "P-3,셋", "P-4,넷", ",식별자 없음", "P-6,여섯"]
    job = finish_job(client, admin, _submit(client, admin, part, "\n".join(lines) + "\n"))
    assert job["status"] == "done", job

    detail = job["result"]
    assert [one["row"] for one in detail["rows"]] == [1, 2, 5]  # 오류 줄 + 앞 두 줄, 파일 순서
    assert detail["rows"][-1]["action"] == "error"
    assert detail["rows_omitted"] == 3
    assert detail["counts"]["create"] == 5 and detail["counts"]["error"] == 1

    listed = client.get(
        "/api/jobs", params={"kind": "objects_import", "mine": True}, headers=admin.headers
    )
    assert listed.status_code == 200, listed.text
    (mine,) = [one for one in listed.json()["items"] if one["id"] == job["id"]]
    # 목록은 줄 목록을 비우고 수만 — 건수 · 오류는 그대로라 화면이 「적용」 을 가를 수 있다.
    assert mine["result"]["rows"] == [] and mine["result"]["rows_omitted"] == 6
    assert mine["result"]["counts"] == detail["counts"]
    assert mine["result"]["fingerprint"] == detail["fingerprint"]

    csv = client.get(f"/api/jobs/{job['id']}/rows.csv", headers=admin.headers)
    assert csv.status_code == 200, csv.text
    assert csv.text.startswith("﻿")  # 엑셀이 한글을 읽게
    body = csv.text.lstrip("﻿").splitlines()
    assert len(body) == 1 + 6 and body[0].startswith("row,action,key,label,message")
    assert "식별자 없음" in csv.text


def test_묶음처럼_안쪽에_있는_줄_목록도_자른다() -> None:
    result: dict[str, Any] = {
        "ok": True,
        "objects": [
            {
                "type_slug": "a",
                "plan": {"rows": [{"row": n, "action": "create"} for n in range(5)]},
            },
            {"type_slug": "b", "plan": {"rows": [{"row": 1, "action": "error"}]}},
        ],
        "errors": ["하나"],
    }
    trimmed = services.trim_rows(result, 2)
    assert [one["row"] for one in trimmed["objects"][0]["plan"]["rows"]] == [0, 1]
    assert trimmed["objects"][0]["plan"]["rows_omitted"] == 3
    assert "rows_omitted" not in trimmed["objects"][1]["plan"]
    assert result["objects"][0]["plan"]["rows"][4]["row"] == 4  # 저장된 결과는 그대로
    assert [where for where, _ in services.all_rows(result)] == ["a"] * 5 + ["b"]


def test_사람이_넣은_작업을_타이머_작업보다_먼저_집는다(db: Session, admin: Signed) -> None:
    """적재 뒤의 지표 다시 세기(시킨 사람 없음)가 먼저 들어와 있어도 사람의 적용이 먼저다."""
    from app.database import SessionLocal
    from app.modules.accounts.models import User

    name = f"quiet_{uuid.uuid4().hex[:6]}"
    kinds.register(
        kinds.Kind(name, "말없는 작업", False, False, lambda _w: {}, allow_system=True)
    )
    try:
        person = db.query(User).filter(User.email == admin.email).one()
        system = services.enqueue(db, kind=name, params={}, user=None, workspace_id=None)
        db.commit()
        human = services.enqueue(db, kind=name, params={}, user=person, workspace_id=None)
        db.commit()
        order: list[uuid.UUID] = []
        with SessionLocal() as worker:
            for _ in range(200):
                got = services.claim(worker, "w-priority")
                if got is None:
                    break
                order.append(got.id)
                if {system.id, human.id} <= set(order):
                    break
        assert order.index(human.id) < order.index(system.id)
    finally:
        kinds._registry.pop(name, None)
        db.query(Job).filter(Job.kind == name).delete()
        db.commit()


def test_작업을_넣으면_쉬던_워커가_바로_깬다(db: Session) -> None:
    from app.worker import Waker

    name = f"quiet_{uuid.uuid4().hex[:6]}"
    kinds.register(
        kinds.Kind(name, "말없는 작업", False, False, lambda _w: {}, allow_system=True)
    )
    waker = Waker()
    try:
        waker.wait(0.05)  # 듣기 시작 — 그 전의 알림은 못 듣는다

        def put() -> None:
            time.sleep(0.3)
            from app.database import SessionLocal

            with SessionLocal() as other:
                services.enqueue(other, kind=name, params={}, user=None, workspace_id=None)
                other.commit()

        threading.Thread(target=put).start()
        started = time.monotonic()
        waker.wait(10.0)
        assert time.monotonic() - started < 5.0, "넣었는데 시간이 다 될 때까지 잤다"
    finally:
        waker.close()
        kinds._registry.pop(name, None)
        db.query(Job).filter(Job.kind == name).delete()
        db.commit()
