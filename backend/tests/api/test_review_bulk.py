"""검토함이 많을 때(ADR 0075) — 쌓이지 않게 · 목록 밖까지 · 자동 확인 · 종류마다 따로.

- **추천 유무로 거른다** — 추천이 없는 줄(별칭 후보면 글자가 비슷한 객체가 없는 말)만 모아
  「거른 전부 무시」.
- **「전부 작업으로」** — 화면이 읽는 상한(별칭 후보 200줄) 밖까지 작업이 미리 보고, 사람이
  보고 적용한다(두 단계 · 같은 지문 · 결정 전부는 결과 파일).
- **자동 확인** — 시스템 관리자가 켠 종류를 밤마다 「추천대로」. 사람이 하나씩 정한 결정이 그
  구간에서 충분히 맞을 때만, 복원하면 멈춘다.
- **종류 목록** — 모으지 않고 이름만(화면이 종류마다 따로 부른다).

시험 DB 를 스위트가 함께 쓰므로 늘 **이 시험이 만든 타입 · 종류**로 거른다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.jobs.models import Job
from app.modules.objects import misses
from app.modules.objects import review as objreview
from app.modules.objects.models import SearchMiss
from app.modules.review import registry
from app.modules.review import services as review_services
from app.modules.review.models import ReviewAutoRule, ReviewDecision
from app.modules.review.registry import Applied, Check, Collected, Filters, Item
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_object, _make_type
from tests.api.test_review import _apply, _items, _miss, _plan


def _ready(db: Session, scope: str) -> None:
    """「이것 아닐까」 를 미리 계산해 둔다(워커의 작업 본문, ADR 0074)."""
    misses.refresh(db, progress=lambda *_: None, commit=db.commit, scope=scope)


def _kestrels(client: TestClient, admin: Signed, db: Session) -> tuple[str, dict[str, Any]]:
    tool = _make_type(client, admin, "tool", label="툴")
    kestrel = _make_object(client, admin, tool, label="Kestrel Mesher")
    for word in ("Kestrel Meshr", "Kestrel Meshar", "Kestrel Mesherr", "Kestrel Meshe"):
        _miss(db, tool, word)
    return tool, kestrel


# --- 쌓이지 않게 ---------------------------------------------------------------------


def test_추천이_없는_줄만_골라_한꺼번에_무시하고_올리지_않은_줄은_그렇다고_말한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool, kestrel = _kestrels(client, admin, db)
    room = _miss(db, tool, "회의실 예약")
    # 한 사람이 한 번에 세 번 — 검토함에 안 선다(별칭 후보 창에는 있다).
    for _ in range(3):
        misses.remember(db, tool, "Kestrel Msh", "resolve", "혼자")
    db.commit()
    _ready(db, tool)

    without = _items(
        client, admin, kind="alias_candidate", type=tool, recommendation="without"
    )
    assert [one["question"] for one in without["items"]] == [
        "「회의실 예약」 을(를) 무엇의 별칭으로 추가할까요?"
    ]
    notes = " ".join(without["kinds"][0]["notes"])
    assert "올리지 않았습니다" in notes and "1건" in notes
    withs = _items(client, admin, kind="alias_candidate", type=tool, recommendation="with")
    assert {one["recommended"] for one in withs["items"]} == {kestrel["id"]}
    assert len(withs["items"]) == 4

    plan = _plan(
        client,
        admin,
        mode="all",
        action="ignore",
        filters={"kind": "alias_candidate", "type": tool, "recommendation": "without"},
    )
    assert plan["counts"] == {"apply": 1, "blocked": 0}
    assert _apply(client, admin, plan, mode="all").status_code == 200
    db.expire_all()
    assert db.get(SearchMiss, room.id).status == "ignored"  # type: ignore[union-attr]


# --- 전부 작업으로 -------------------------------------------------------------------


def test_전부_작업으로는_상한_밖까지_미리_보고_보고_나서_같은_결정을_적용한다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """화면은 별칭 후보를 많이 찾은 것부터 200줄만 읽는다(여기서는 상한 2) — 대기가 3만 건이면
    「추천대로 모두」 를 150번 눌러야 했다."""
    tool, kestrel = _kestrels(client, admin, db)
    _ready(db, tool)
    monkeypatch.setattr(objreview, "CANDIDATE_CAP", 2)

    # 화면의 미리 보기는 읽은 둘만 — 안 본 둘을 말하고 「전부 작업으로」 를 가리킨다.
    small = _plan(
        client,
        admin,
        mode="recommended",
        threshold=0.3,
        filters={"kind": "alias_candidate", "type": tool},
    )
    assert small["counts"]["apply"] == 2 and small["unread"] == 2
    assert any("전부 작업으로" in line for line in small["notes"])

    started = client.post(
        "/api/review/bulk",
        json={
            "mode": "recommended",
            "threshold": 0.3,
            "filters": {"kind": "alias_candidate", "type": tool},
        },
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    job = finish_job(client, admin, {"id": started.json()["job_id"]})
    assert job["status"] == "done", job.get("error")
    result = job["result"]
    assert result["counts"] == {"apply": 4, "blocked": 0} and result["ok"] is True
    assert result["applied"] is False and len(result["outcomes"]) == 4
    assert "적용 4" in result["summary"]
    # 미리 보기는 아무것도 안 바꾼다 — 결정 전부는 결과 파일(CSV)에.
    pending = db.scalars(
        select(SearchMiss.status).where(SearchMiss.scope == tool, SearchMiss.hits >= 3)
    ).all()
    assert set(pending) == {"pending"}
    csv = client.get(f"/api/jobs/{job['id']}/download", headers=admin.headers)
    assert csv.status_code == 200
    lines = csv.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("종류,항목,동작,값") and len(lines) == 5

    applied = client.post(f"/api/jobs/{job['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done", done.get("error")
    assert done["result"]["counts"] == {"done": 4, "refused": 0}
    db.expire_all()
    statuses = db.scalars(
        select(SearchMiss.status).where(SearchMiss.scope == tool, SearchMiss.hits >= 3)
    ).all()
    assert set(statuses) == {"attached"}
    batches = client.get("/api/review/batches", headers=admin.headers).json()
    assert any(one["batch_id"] == done["result"]["batch_id"] for one in batches)
    assert kestrel["id"]  # 붙은 객체


def test_전부_작업으로는_그_사이_바뀌면_아무것도_안_한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool, _kestrel = _kestrels(client, admin, db)
    _ready(db, tool)
    started = client.post(
        "/api/review/bulk",
        json={"threshold": 0.3, "filters": {"kind": "alias_candidate", "type": tool}},
        headers=admin.headers,
    )
    job = finish_job(client, admin, {"id": started.json()["job_id"]})
    assert job["result"]["counts"]["apply"] == 4
    # 그 사이 누가 하나를 무시했다 — 미리 본 것과 다르다.
    one = db.scalar(select(SearchMiss).where(SearchMiss.scope == tool).limit(1))
    assert one is not None
    misses.decide(db, _admin_user(db, admin), [one.id], "ignore")
    db.commit()
    applied = client.post(f"/api/jobs/{job['id']}/apply", headers=admin.headers)
    failed = finish_job(client, admin, applied.json())
    assert failed["status"] == "failed" and "달라졌습니다" in (failed["error"] or "")
    db.expire_all()
    left = db.scalars(select(SearchMiss.status).where(SearchMiss.scope == tool)).all()
    assert "attached" not in left


def _admin_user(db: Session, who: Signed) -> User:
    user = db.scalar(select(User).where(User.email == who.email))
    assert user is not None
    return user


# --- 자동 확인 ------------------------------------------------------------------------


@pytest.fixture
def fake_kind(monkeypatch: pytest.MonkeyPatch, db: Session) -> Iterator[dict[str, Any]]:
    """이 시험만의 종류 — 남의 결정 기록이 근거에 섞이지 않게. 항목 셋(확신도 0.97 · 0.96 ·
    0.5), 처리하면 `done` 에 남고 되돌리면 빠진다."""
    kind = f"auto_fake_{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {"kind": kind, "done": set()}
    confidences = {"a": 0.97, "b": 0.96, "c": 0.5}

    def collect(_db: Session, _user: User, flt: Filters) -> Collected:
        items = [
            Item(
                kind=kind,
                id=key,
                question=f"{key} 를 확인할까요?",
                evidence=[],
                choices=[registry.Choice(value="yes", label="예")],
                recommended="yes",
                confidence=score,
                reason="시험",
                type_slug="",
                type_label="",
                workspace_id=None,
            )
            for key, score in confidences.items()
            if key not in state["done"] and (flt.item_ids is None or key in flt.item_ids)
        ]
        return Collected(items=items, total=len(items))

    def check(_db: Session, _user: User, item: Item, value: str) -> Check:
        return Check(
            status="apply", effect=f"{item.id} 확인", choice_label="예", rows=1, undoable=True
        )

    def apply(_db: Session, _user: User, item: Item, value: str, _in_job: bool) -> Applied:
        state["done"].add(item.id)
        return Applied(rows=1, undo={"id": item.id})

    def undo(
        _db: Session, _user: User, data: dict[str, Any], really: bool
    ) -> tuple[bool, str]:
        if really:
            state["done"].discard(data.get("id"))
        return True, "되돌림"

    monkeypatch.setitem(
        registry._registry,
        kind,
        registry.Provider(
            kind=kind,
            label="시험 종류",
            description="",
            link="/review",
            can_view=lambda _db, _user: True,
            collect=collect,
            check=check,
            apply=apply,
            undo=undo,
            order=999,
        ),
    )
    monkeypatch.setattr(get_settings(), "review_auto_hour", 0)
    yield state
    db.rollback()
    db.execute(delete(ReviewAutoRule).where(ReviewAutoRule.kind == kind))
    db.execute(
        update(Job)
        .where(Job.kind == "review_auto", Job.status.in_(("queued", "running")))
        .values(status="cancelled")
    )
    db.commit()


def _evidence(db: Session, kind: str, count: int, followed: int, admin: User) -> None:
    """사람이 하나씩 정한 결정 — 확신도 0.97, `followed` 건은 추천대로."""
    batch = uuid.uuid4()
    for index in range(count):
        db.add(
            ReviewDecision(
                batch_id=batch,
                kind=kind,
                item_id=f"old-{index}",
                action="accept" if index < followed else "choose",
                choice="yes" if index < followed else "no",
                recommended="yes",
                confidence=0.97,
                followed=index < followed,
                mode="one",
                status="done",
                decided_by_id=admin.id,
            )
        )
    db.commit()


def _auto(client: TestClient, who: Signed, kind: str) -> dict[str, Any]:
    got = client.get("/api/review/auto", headers=who.headers)
    assert got.status_code == 200, got.text
    return next(one for one in got.json() if one["kind"] == kind)


def test_자동_확인은_시스템_관리자가_켜고_근거가_충분할_때만_돈다(
    client: TestClient,
    admin: Signed,
    manager: Signed,
    db: Session,
    fake_kind: dict[str, Any],
) -> None:
    kind = fake_kind["kind"]
    path = f"/api/review/auto/{kind}"
    refused = client.put(
        path, json={"enabled": True, "threshold": 0.95}, headers=manager.headers
    )
    assert refused.status_code == 403
    low = client.put(path, json={"enabled": True, "threshold": 0.5}, headers=admin.headers)
    assert low.status_code == 422
    turned = client.put(path, json={"enabled": True, "threshold": 0.95}, headers=admin.headers)
    assert turned.status_code == 200, turned.text
    state = turned.json()
    assert state["enabled"] is True and state["ready"] is False
    assert "근거가 모자랍니다" in state["why"]
    seen = _auto(client, manager, kind)  # 부서 관리자는 볼 수만 있다
    assert seen["can_edit"] is False and seen["enabled"] is True

    # 근거가 모자라면 넣지 않고 까닭을 적는다.
    assert kind not in review_services.schedule_auto(db)
    assert "건너뜀" in _auto(client, admin, kind)["last_note"]

    # 사람이 하나씩 정한 결정 30건 중 29건 추천대로(97%) — 충분하다. 다음 날 돈다.
    user = _admin_user(db, admin)
    _evidence(db, kind, 30, 29, user)
    assert _auto(client, admin, kind)["ready"] is True
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    assert kind in review_services.schedule_auto(db, now=tomorrow)
    assert kind not in review_services.schedule_auto(db, now=tomorrow)  # 하루 한 번
    job_id = _auto(client, admin, kind)["last_job_id"]
    job = finish_job(client, admin, {"id": job_id})
    assert job["status"] == "done", job.get("error")
    # 문턱(0.95) 이상인 둘만 — 0.5 는 사람이 본다.
    assert job["result"]["counts"] == {"done": 2, "refused": 0}
    assert fake_kind["done"] == {"a", "b"}
    assert "2건 처리" in _auto(client, admin, kind)["last_note"]
    decided = db.scalars(
        select(ReviewDecision.mode).where(
            ReviewDecision.kind == kind, ReviewDecision.item_id.in_(["a", "b"])
        )
    ).all()
    assert set(decided) == {"auto"}
    # 자동은 근거에 안 든다 — 늘 추천을 「따른」 것이라 세면 정확도가 저절로 오른다.
    assert _auto(client, admin, kind)["decided"] == 30

    # 자동으로 정한 것을 복원하면 멈춘다.
    undone = client.post(
        f"/api/review/batches/{job['result']['batch_id']}/undo",
        json={"apply": True},
        headers=admin.headers,
    )
    assert undone.status_code == 200, undone.text
    stopped = _auto(client, admin, kind)
    assert stopped["ready"] is False and "복원한 적이 있어 멈췄습니다" in stopped["why"]
    assert kind not in review_services.schedule_auto(db, now=tomorrow + timedelta(days=1))
    # 다시 켜면(켠 때부터 다시 센다) 이어 한다.
    client.put(path, json={"enabled": False, "threshold": 0.95}, headers=admin.headers)
    again = client.put(path, json={"enabled": True, "threshold": 0.95}, headers=admin.headers)
    assert again.json()["ready"] is True


def test_자동_확인은_정확도가_모자라면_돌지_않는다(
    client: TestClient, admin: Signed, db: Session, fake_kind: dict[str, Any]
) -> None:
    kind = fake_kind["kind"]
    _evidence(db, kind, 40, 30, _admin_user(db, admin))  # 75%
    state = client.put(
        f"/api/review/auto/{kind}",
        json={"enabled": True, "threshold": 0.95},
        headers=admin.headers,
    ).json()
    assert state["ready"] is False and "75%" in state["why"]
    assert kind not in review_services.schedule_auto(
        db, now=datetime.now(UTC) + timedelta(days=1)
    )


# --- 종류마다 따로 ---------------------------------------------------------------------


def test_종류_목록은_모으지_않고_볼_수_있는_것만(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    got = client.get("/api/review/kinds", headers=manager.headers)
    assert got.status_code == 200, got.text
    kinds = {one["kind"]: one for one in got.json()}
    assert kinds["alias_candidate"]["actionable"] is True
    assert kinds["alias_candidate"]["label"] == "별칭 후보"
    assert client.get("/api/review/kinds", headers=member.headers).status_code == 403
    # 관리자 쪽은 더 많이(또는 같게) 본다 — 데이터 소스의 선 기다림은 시스템 관리자의 것이다.
    assert len(client.get("/api/review/kinds", headers=admin.headers).json()) >= len(kinds)
