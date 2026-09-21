"""작업 — **오래 걸리는 일은 요청이 아니라 표에 산다.**

여기서 보는 것은 작업 자체다: 넣기 · 집기 · 되살리기 · 지문 · 취소 · 누가 보나. 가져오기 규칙
(무엇이 새로/고침/오류인가)은 `test_bulk.py` 가 같은 경로로 본다.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.modules.jobs import services
from app.modules.jobs.models import Job, JobFile, WorkerHeartbeat
from app.modules.notifications.models import Notification
from app.modules.workspaces.models import Workspace
from tests.api.conftest import (
    Signed,
    bundle_import,
    finish_job,
    import_file,
    maintenance_counts,
    notifications_of,
)
from tests.api.test_ontology import _make_object, _make_relation, _make_type


def _submit(
    client: TestClient, who: Signed, type_slug: str, text: str, *, workspace: str | None = None
) -> dict[str, Any]:
    got = client.post(
        f"/api/objects/{type_slug}/import",
        files={"file": ("rows.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
        data={"workspace_slug": workspace or who.workspace},
        headers=who.headers,
    )
    assert got.status_code == 202, got.text
    return dict(got.json())


def test_올리면_곧장_202_이고_워커가_계획을_세운다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    job = _submit(client, admin, part, "key,label\nP-1,볼트\nP-2,너트\n")
    assert job["status"] == "queued" and job["kind"] == "objects_import"
    assert job["input_file_name"] == "rows.csv"

    done = finish_job(client, admin, job)
    assert done["status"] == "done"
    assert done["result"]["counts"]["create"] == 2 and done["result"]["fingerprint"]
    # 진행률이 남는다 — 화면이 「몇 행 중 몇 행」 을 그린다.
    assert done["progress"]["stage"] == "계획" and done["progress"]["total"] == 2

    # 적용은 같은 파일 · 같은 지문 · 부모 작업.
    applied = client.post(f"/api/jobs/{job['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    assert applied.json()["parent_id"] == job["id"]
    assert applied.json()["input_file_name"] == "rows.csv"
    final = finish_job(client, admin, applied.json())
    assert final["status"] == "done" and final["result"]["applied"] is True
    assert client.get(f"/api/objects/{part}", headers=admin.headers).json()["total"] == 2


def test_미리_본_뒤_누가_바꾸면_적용하지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """옛 계획대로 넣으면 그 사이의 변경이 조용히 덮인다. 아무것도 넣지 않고 다시 보게 한다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_object(client, admin, part, label="볼트", key="P-1")
    job = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    assert job["result"]["counts"]["unchanged"] == 1

    # 그 사이에 누군가 이름을 바꿨다 — 계획은 이제 「고침」 이 된다.
    row = db.scalar(select(Job).where(Job.id == uuid.UUID(job["id"])))
    assert row is not None
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"]
    patched = client.patch(
        f"/api/objects/{part}/{listed[0]['id']}",
        json={"label": "볼트(구)"},
        headers=admin.headers,
    )
    assert patched.status_code == 200, patched.text

    applied = client.post(f"/api/jobs/{job['id']}/apply", headers=admin.headers)
    final = finish_job(client, admin, applied.json())
    assert final["status"] == "failed"
    assert "JOBS-0020" in final["error"] and "다시 미리 보고" in final["error"]
    assert (
        client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"][0]["label"]
        == "볼트(구)"
    )


def test_오류가_있는_계획은_적용_작업이_안_만들어진다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    job = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,\n"))
    assert job["result"]["counts"]["error"] == 1
    refused = client.post(f"/api/jobs/{job['id']}/apply", headers=admin.headers)
    assert refused.status_code == 409 and "JOBS-0011" in refused.text


def test_내_것과_내_부서_것만_보인다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """결과에는 계획 표(객체 이름 · 값)가 든다 — 남의 부서 것이 보이면 그 데이터가 새는
    것이다."""
    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    part = _make_type(client, admin, label="부품", key_policy="required")
    theirs = _submit(client, admin, part, "key,label\nP-9,비밀\n", workspace=other.slug)
    mine = _submit(client, admin, part, "key,label\nP-1,볼트\n", workspace=member.workspace)

    listed = client.get("/api/jobs", headers=member.headers).json()["items"]
    seen = {one["id"] for one in listed}
    assert mine["id"] in seen and theirs["id"] not in seen
    assert client.get(f"/api/jobs/{theirs['id']}", headers=member.headers).status_code == 404
    assert client.get(f"/api/jobs/{theirs['id']}", headers=admin.headers).status_code == 200


def test_대기_중이면_바로_취소되고_시킨_사람만_취소한다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    job = _submit(client, admin, part, "key,label\nP-1,볼트\n", workspace=member.workspace)
    denied = client.post(f"/api/jobs/{job['id']}/cancel", headers=member.headers)
    assert denied.status_code == 403
    got = client.post(f"/api/jobs/{job['id']}/cancel", headers=admin.headers)
    assert got.status_code == 200 and got.json()["status"] == "cancelled"
    again = client.post(f"/api/jobs/{job['id']}/cancel", headers=admin.headers)
    assert again.status_code == 409


def test_두_워커는_같은_작업을_안_집는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """A · B 에 워커가 하나씩 뜬다 — `FOR UPDATE SKIP LOCKED` 가 그 둘을 가른다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    first = _submit(client, admin, part, "key,label\nP-1,볼트\n")
    second = _submit(client, admin, part, "key,label\nP-2,너트\n")
    wanted = {uuid.UUID(first["id"]), uuid.UUID(second["id"])}

    from app.database import SessionLocal

    with SessionLocal() as a, SessionLocal() as b:
        # 커밋하지 않은 채 둘이 동시에 집는다 — 다른 시험이 남긴 대기 작업이 있을 수 있어
        # 원하는 둘이 잡힐 때까지 집는다.
        taken: set[uuid.UUID] = set()
        for session in (a, b, a, b, a, b):
            row = db.scalar(select(Job).where(Job.status == "queued"))
            if row is None:
                break
            got = services.claim(session, f"w-{id(session)}")
            if got is not None:
                assert got.id not in taken, "같은 작업을 두 워커가 집었다"
                taken.add(got.id)
        assert wanted <= taken


def test_심장박동이_멎은_작업은_되돌리고_시도가_다하면_실패로(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    job = _submit(client, admin, part, "key,label\nP-1,볼트\n")
    job_id = uuid.UUID(job["id"])
    long_ago = datetime.now(UTC) - timedelta(hours=1)

    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status="running", worker_id="dead", attempts=1, heartbeat_at=long_ago)
    )
    db.commit()
    assert services.recover_stale(db) >= 1
    revived = db.scalar(select(Job).where(Job.id == job_id))
    assert revived is not None and revived.status == "queued" and revived.worker_id is None

    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status="running", worker_id="dead", attempts=3, heartbeat_at=long_ago)
    )
    db.commit()
    services.recover_stale(db)
    db.expire_all()
    gave_up = db.scalar(select(Job).where(Job.id == job_id))
    assert gave_up is not None and gave_up.status == "failed"
    assert gave_up.error is not None and "3번" in gave_up.error


def test_너무_큰_파일은_넣는_순간_거절한다(
    client: TestClient, admin: Signed, monkeypatch: Any
) -> None:
    from app.config import Settings

    part = _make_type(client, admin, label="부품", key_policy="required")
    small = Settings(job_file_max_bytes=16)
    from app.modules.jobs import files

    monkeypatch.setattr(files, "get_settings", lambda: small)
    got = client.post(
        f"/api/objects/{part}/import",
        files={"file": ("rows.csv", io.BytesIO(b"key,label\n" + b"P-1,x\n" * 10), "text/csv")},
        data={"workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert got.status_code == 413 and "JOBS-0001" in got.text


def test_기한_지난_파일은_지우되_작업_기록은_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    job = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    db.execute(update(JobFile).values(expires_at=datetime.now(UTC) - timedelta(days=1)))
    db.commit()
    assert services.purge_expired_files(db) >= 1
    after = client.get(f"/api/jobs/{job['id']}", headers=admin.headers).json()
    assert after["status"] == "done" and after["input_file_name"] is None
    # 파일이 없으면 적용도 없다 — 다시 올리라고 말한다.
    refused = client.post(f"/api/jobs/{job['id']}/apply", headers=admin.headers)
    assert refused.status_code == 409 and "JOBS-0013" in refused.text


def test_워커가_살아_있는지_화면이_안다(client: TestClient, admin: Signed) -> None:
    services.heartbeat("test-worker", None)
    rows = client.get("/api/jobs/workers", headers=admin.headers).json()
    mine = next(one for one in rows if one["worker_id"] == "test-worker")
    assert mine["alive"] is True and mine["current_job_id"] is None


def test_관계_파일도_작업으로_간다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    _make_object(client, admin, vendor, label="Ansys")
    _make_object(client, admin, part, label="볼트", key="P-1")
    plan = import_file(
        client,
        admin,
        part,
        f"src,relation,dst,evidence_note\nP-1,{kind},Ansys,대장\n",
        path="relations/import",
        apply=True,
    )
    assert plan["applied"] is True and plan["counts"]["create"] == 1


def test_내보내기는_작업이_되고_결과_파일을_받는다(client: TestClient, admin: Signed) -> None:
    """목록의 상한은 화면을 위한 것이고 파일은 그 상한을 넘으려고 있다 — 그래서 요청 안에서
    만들면 큰 타입에서 끊긴다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_object(client, admin, part, label="볼트", key="P-1")
    _make_object(client, admin, part, label="너트", key="P-2")

    started = client.post(
        f"/api/objects/{part}/export", params={"p.nothing": "x"}, headers=admin.headers
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, admin, started.json())
    assert done["status"] == "done" and done["has_output"] is True
    assert done["result"]["rows"] == 0  # 거르기가 목록과 같다 — 없는 속성으로 걸렀다

    plain = finish_job(
        client, admin, client.post(f"/api/objects/{part}/export", headers=admin.headers).json()
    )
    got = client.get(f"/api/jobs/{plain['id']}/download", headers=admin.headers)
    assert got.status_code == 200
    lines = got.text.lstrip("﻿").splitlines()
    assert len(lines) == 3 and "볼트" in got.text
    assert "attachment" in got.headers["content-disposition"]


def test_묶음_가져오기도_작업이다(client: TestClient, admin: Signed) -> None:
    slug = f"memo_{uuid.uuid4().hex[:6]}"
    bundle = {
        "ontology": {"types": [{"slug": slug, "label": "메모", "properties": []}]},
        "objects": [{"type_slug": slug, "rows": [{"label": "첫 메모"}]}],
    }
    planned = bundle_import(client, admin, bundle)
    assert planned["ok"] is True and planned["applied"] is False
    assert planned["counts"]["objects_create"] == 1
    # 미리 보기는 아무것도 안 남긴다 — 타입조차 안 생긴다.
    assert client.get(f"/api/objects/{slug}", headers=admin.headers).status_code == 404


def test_타이머는_작업을_넣고_같은_것을_두_번_안_넣는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """예전에는 타이머가 직접 돌렸다 — 지금은 넣기만 한다. 두 서버의 타이머가 같은 시각에
    돌아도, 아직 안 끝난 같은 작업이 있으면 또 넣지 않는다."""
    job = services.enqueue(
        db,
        kind="datasource_sync",
        params={"slug": "없는소스", "apply": False},
        user=None,  # 타이머가 넣는 작업에는 시킨 사람이 없다
        workspace_id=None,
    )
    db.commit()
    assert services.pending_for(db, "datasource_sync", slug="없는소스") is not None
    assert services.pending_for(db, "datasource_sync", slug="다른소스") is None

    # 워커가 돌면 없는 소스라 실패로 끝나고, 그 뒤에는 「안 끝난 작업」 이 아니다.
    services.process_one("test-worker")
    db.expire_all()
    done = db.scalar(select(Job).where(Job.id == job.id))
    assert done is not None and done.status == "failed"
    assert done.error is not None and "데이터 소스를 찾을 수 없습니다" in done.error
    assert services.pending_for(db, "datasource_sync", slug="없는소스") is None


def test_시킨_사람이_필요한_종류는_타이머가_못_넣는다(db: Session) -> None:
    """내보내기는 그 사람이 **볼 수 있는 것만** 낸다 — 시킨 사람이 없으면 누구 눈으로 보는지
    알 수 없다."""
    import pytest

    from app.shared.errors import AppError

    with pytest.raises(AppError, match="시킨 사람"):
        services.enqueue(
            db,
            kind="objects_export",
            params={"type_slug": "x", "format": "csv"},
            user=None,
            workspace_id=None,
        )


def test_오래_걸린_작업만_끝났다고_알린다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """1초에 끝난 것까지 알리면 그때 사람은 아직 그 화면을 보고 있었다 — 본 것을 한 번 더
    말하는 종은 잡음이 되고, 잡음이 된 종은 진짜 하나가 울려도 안 읽힌다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    quick = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    assert quick["status"] == "done"
    assert notifications_of(client, admin, "job.done") == []

    # 같은 작업이 오래 걸렸다면 — 끝난 시각을 뒤로 밀어 그 자리를 만든다.
    slow = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-2,너트\n"))
    row = db.scalar(select(Job).where(Job.id == uuid.UUID(slow["id"])))
    assert row is not None
    row.finished_at = (row.started_at or row.created_at) + timedelta(minutes=3)
    db.commit()
    services.announce(db, row)
    db.commit()

    told = notifications_of(client, admin, "job.done")
    assert len(told) == 1
    assert "계획이 끝났습니다" in told[0]["title"]
    assert "아직 아무것도 안 들어갔습니다" in (told[0]["body"] or "")
    assert told[0]["link"] == "/jobs"


def test_타이머가_넣은_작업은_아무에게도_안_알린다(db: Session) -> None:
    """시킨 사람이 없다 — 누구에게 알릴지 알 수 없고, 알려도 할 일이 없다."""
    job = services.enqueue(
        db,
        kind="datasource_sync",
        params={"slug": "없는소스"},
        user=None,
        workspace_id=None,
    )
    job.status = "failed"
    job.started_at = datetime.now(UTC) - timedelta(minutes=5)
    job.finished_at = datetime.now(UTC)
    db.commit()
    before = db.scalar(select(func.count()).select_from(Notification)) or 0
    services.announce(db, job)
    db.commit()
    assert (db.scalar(select(func.count()).select_from(Notification)) or 0) == before


def test_끝난_기록만_치우고_도는_것은_남긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """도는 것을 지우면 멎은 작업을 되살릴 근거까지 사라진다. 끝난 것만, 기한이 지난 것만."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    done = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    waiting = _submit(client, admin, part, "key,label\nP-2,너트\n")  # queued 로 남는다

    old = datetime.now(UTC) - timedelta(days=99)
    db.execute(update(Job).where(Job.id == uuid.UUID(done["id"])).values(finished_at=old))
    db.execute(update(Job).where(Job.id == uuid.UUID(waiting["id"])).values(created_at=old))
    db.commit()

    assert services.purge_old_jobs(db) >= 1
    assert client.get(f"/api/jobs/{done['id']}", headers=admin.headers).status_code == 404
    assert client.get(f"/api/jobs/{waiting['id']}", headers=admin.headers).status_code == 200


def test_종류와_내_것으로_거른다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """타이머가 소스마다 5분에 한 줄을 넣는다 — 사람이 올린 작업이 그 사이에 파묻히면
    화면이 못 쓰게 된다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    mine = _submit(client, admin, part, "key,label\nP-1,볼트\n", workspace=member.workspace)
    timer = services.enqueue(
        db, kind="datasource_sync", params={"slug": "x"}, user=None, workspace_id=None
    )
    db.commit()

    def ids(**params: Any) -> set[str]:
        got = client.get("/api/jobs", params=params, headers=admin.headers)
        assert got.status_code == 200, got.text
        return {one["id"] for one in got.json()["items"]}

    everything = ids(limit=200)
    assert {mine["id"], str(timer.id)} <= everything
    assert str(timer.id) not in ids(limit=200, kind="objects_import")
    assert mine["id"] not in ids(limit=200, kind="datasource_sync")
    # 「내가 시킨 것만」 — 타이머 것(시킨 사람 없음)은 빠진다.
    assert str(timer.id) not in ids(limit=200, mine="true")
    assert mine["id"] in ids(limit=200, mine="true")


def test_홈이_적용을_기다리는_계획을_말한다(client: TestClient, admin: Signed) -> None:
    """알림을 놓치면 그 계획은 「작업」 화면을 열어 봐야만 보이고, 그 화면은 평소에 아무도 안
    연다 — 며칠 뒤 「그때 그거 안 들어갔네」 가 된다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    # 시험 DB 는 스위트가 함께 쓴다 — 남이 남긴 계획이 이미 있을 수 있어 **차이로 본다.**
    before = maintenance_counts(client, admin).get("job_awaiting_apply", 0)

    planned = finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    assert maintenance_counts(client, admin)["job_awaiting_apply"] == before + 1

    # 적용하면 그 줄은 사라진다 — 할 일이 끝났으니.
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    finish_job(client, admin, applied.json())
    assert maintenance_counts(client, admin).get("job_awaiting_apply", 0) == before


def test_오류가_있는_계획은_할_일로_안_센다(client: TestClient, admin: Signed) -> None:
    """적용할 수 없는 것을 「기다리는 일」 로 세면, 그 줄은 눌러도 없어지지 않는 숫자가
    된다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    before = maintenance_counts(client, admin).get("job_awaiting_apply", 0)
    finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,\n"))
    assert maintenance_counts(client, admin).get("job_awaiting_apply", 0) == before


def test_워커가_멎으면_관리자의_홈이_말한다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """워커가 죽으면 가져오기도 웹훅도 멎는다 — 그 사실이 「작업」 화면 안에만 있으면
    운영자는 사람이 물어볼 때까지 모른다."""
    db.execute(delete(WorkerHeartbeat))
    db.commit()
    part = _make_type(client, admin, label="부품", key_policy="required")
    _submit(client, admin, part, "key,label\nP-1,볼트\n")  # 기다리는 작업 하나

    assert maintenance_counts(client, admin).get("worker_down") == 1
    # 못 고치는 사람에게는 안 띄운다 — 그 줄은 읽고 나서 할 일이 없다.
    assert "worker_down" not in maintenance_counts(client, member)

    services.heartbeat("test-worker", None)
    assert "worker_down" not in maintenance_counts(client, admin)


def test_서버_화면이_작업과_파일을_센다(client: TestClient, admin: Signed) -> None:
    """작업 파일은 DB 에 들어 있다 — 안 보여 주면 덤프가 왜 커졌는지 물을 자리가 없다."""
    part = _make_type(client, admin, label="부품", key_policy="required")
    finish_job(client, admin, _submit(client, admin, part, "key,label\nP-1,볼트\n"))
    got = client.get("/api/server/status", headers=admin.headers)
    assert got.status_code == 200, got.text
    labels = {one["label"]: one["count"] for one in got.json()["counts"]}
    assert labels["작업"] >= 1
    assert any(name.startswith("작업 파일 (") for name in labels)
