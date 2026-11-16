"""별칭 후보의 「이것 아닐까」 미리 셈(ADR 0074).

줄마다 비슷한 객체를 찾는 질의가 축 객체 · 별칭을 다 훑어(운영 모양에서 한 줄 0.5초) 대기가
몇만 건이면 검토함이 504 였다(2026-11-16, ADR 0073). 워커가 셈해 `search_misses` 에 적어 두고
화면은 읽기만 한다. 여기서 보는 것: 셈한 것을 읽기만 하나(그 자리에서 셈하지 않는다) · 읽을 때
지금의 객체로 거르나(지운 것 · 안 보이는 것 · 바꾼 이름) · 무엇을 언제 셈하나 · 작업이 다른
작업에 비켜 주나 · 타이머가 한 번만 넣나.

시험 DB 를 스위트가 함께 쓰므로 셈은 **이 시험이 만든 타입**으로 좁힌다(`scope`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.jobs.models import Job
from app.modules.objects import misses
from app.modules.objects.models import SearchMiss
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, _login, _make_user
from tests.api.test_ontology import _make_object, _make_type
from tests.api.test_review import _delete_quietly, _items, _miss


def _refresh(db: Session, scope: str, **kw: Any) -> dict[str, Any]:
    return misses.refresh(db, progress=lambda *_: None, commit=db.commit, scope=scope, **kw)


def test_워커가_셈해_두면_검토함과_별칭_후보_창은_읽기만_한다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _make_type(client, admin, "tool", label="툴")
    kestrel = _make_object(client, admin, tool, label="Kestrel Mesher")
    heron = _make_object(client, admin, tool, label="Heron Solver")
    first = _miss(db, tool, "Kestrel Meshr", times=5)
    _miss(db, tool, "Heron Solvr", times=4)

    done = _refresh(db, tool)
    assert done["done"] == 2 and done["suggested"] == 2 and done["left"] == 0
    db.expire_all()
    row = db.get(SearchMiss, first.id)
    assert row is not None and row.suggested_at is not None
    # 비슷한 것은 하나뿐이다(「Heron Solver」 와는 비슷함 0).
    stored = row.suggested or []
    assert [one["id"] for one in stored] == [kestrel["id"]]
    assert stored[0]["matched"] == "label" and stored[0]["score"] > 0.5

    # 이제 그 자리에서 셈하지 않는다 — 셈하면 `similar` 가 실패한다.
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("미리 셈한 줄을 그 자리에서 다시 셈했다")

    monkeypatch.setattr(misses, "similar", refuse)
    page = _items(client, admin, kind="alias_candidate", type=tool)
    (kind,) = page["kinds"]
    assert kind["late"] is False and kind["total"] == 2
    # 다 셈해 두었으면 「셈하는 중」 이라는 말은 없다.
    assert not any("미리 계산합니다" in note for note in kind["notes"])
    by_text = {one["question"]: one for one in page["items"]}
    item = by_text["「Kestrel Meshr」 을(를) 무엇의 별칭으로 추가할까요?"]
    assert item["recommended"] == kestrel["id"] and item["confidence"] > 0.5
    assert any("에 계산한 것" in line for line in item["evidence"])
    assert (
        by_text["「Heron Solvr」 을(를) 무엇의 별칭으로 추가할까요?"]["recommended"]
        == (heron["id"])
    )

    listed = client.get(
        "/api/objects/alias-candidates", params={"type": tool}, headers=admin.headers
    )
    assert listed.status_code == 200, listed.text
    for one in listed.json()["items"]:
        assert one["suggestions"] and one["suggested_at"] is not None
    single = client.get(f"/api/objects/alias-candidates/{first.id}", headers=admin.headers)
    assert single.json()["suggestions"][0]["id"] == kestrel["id"]

    # 바꾼 이름은 지금의 이름으로 — 이름은 적어 두지 않고 객체에서 읽는다.
    renamed = client.patch(
        f"/api/objects/{tool}/{kestrel['id']}",
        json={"label": "Kestrel Mesher 2"},
        headers=admin.headers,
    )
    assert renamed.status_code == 200, renamed.text
    again = _items(client, admin, kind="alias_candidate", type=tool)
    item = next(one for one in again["items"] if one["recommended"] == kestrel["id"])
    assert item["recommended_label"] == "Kestrel Mesher 2"

    # 지운 객체는 빠진다 — 다 빠지면 그렇다고 말한다(「없다」 가 아니다).
    _delete_quietly(db, kestrel["id"])
    gone = client.get(f"/api/objects/alias-candidates/{first.id}", headers=admin.headers)
    assert gone.json()["suggestions"] == []
    assert gone.json()["suggest_note"] == misses.GONE_NOTE
    after = _items(client, admin, kind="alias_candidate", type=tool)
    item = next(one for one in after["items"] if "Kestrel Meshr" in one["question"])
    assert item["recommended"] is None and item["reason"] == misses.GONE_NOTE


def test_아직_셈하지_않은_줄만_그_자리에서_세고_셈하는_중이라고_말한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """배포 직후 · 새로 세 번을 넘긴 줄 — 워커가 세기 전에는 지금처럼 그 자리에서 센다."""
    tool = _make_type(client, admin, "tool", label="툴")
    kestrel = _make_object(client, admin, tool, label="Kestrel Mesher")
    _miss(db, tool, "Kestrel Meshr", times=5)

    page = _items(client, admin, kind="alias_candidate", type=tool)
    (item,) = page["items"]
    assert item["recommended"] == kestrel["id"]
    assert not any("에 계산한 것" in line for line in item["evidence"])
    # 타입으로 거른 검토함이면 그 타입의 수로 말한다.
    assert "세 번 이상 찾은 1건 중 0건을 계산했습니다" in " ".join(page["kinds"][0]["notes"])


@pytest.mark.usefixtures("department_scope")
def test_부서를_가리지_않고_셈해_두고_읽을_때_그_사람에게_보이는_것만_남긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """워커는 사람이 없다 — 셈할 때 부서로 거를 수 없다. 대신 넉넉히(6개) 셈해 두고 읽을 때
    그 사람에게 안 보이는 것을 뺀다. 안 빼면 남의 부서 객체의 이름이 제안으로 새어 나간다."""
    tool = _make_type(client, admin, "tool", label="툴")
    mine = _make_object(client, admin, tool, label="Kestrel Mesher")
    first = _miss(db, tool, "Kestrel Meshr", times=5)
    _refresh(db, tool)

    other = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(other)
    db.commit()
    boss = _make_user(db, other, label="far-boss", is_system_admin=False, role="manager")
    far = Signed(email=boss.email, token=_login(client, boss.email), workspace=other.slug)

    seen = client.get(f"/api/objects/alias-candidates/{first.id}", headers=admin.headers)
    assert [one["id"] for one in seen.json()["suggestions"]] == [mine["id"]]
    hidden = client.get(f"/api/objects/alias-candidates/{first.id}", headers=far.headers)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["suggestions"] == []
    assert hidden.json()["suggest_note"] == misses.GONE_NOTE


def test_무엇을_언제_셈하나(client: TestClient, admin: Signed, db: Session) -> None:
    """검토함에 서는 줄(세 번 이상)은 안 셈했으면 곧 · 밤이 지나면 다시. 몇 번 안 찾은 줄은
    밤에(생기자마자 세면 오타마다 작업이 하나다) · 7일마다. 처리한 줄은 안 센다."""
    tool = _make_type(client, admin, "tool", label="툴")
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    mark = misses.night_mark(now)
    assert mark.hour == get_settings().alias_suggest_night_hour
    assert mark <= now < mark + timedelta(days=1)
    rows = {
        "shown_never": (5, None, mark - timedelta(days=2), "pending"),
        "shown_before_night": (
            5,
            mark - timedelta(hours=1),
            mark - timedelta(days=2),
            "pending",
        ),
        "shown_after_night": (
            5,
            mark + timedelta(minutes=5),
            mark - timedelta(days=2),
            "pending",
        ),
        "rare_never_old": (1, None, mark - timedelta(hours=1), "pending"),
        "rare_never_today": (1, None, mark + timedelta(hours=1), "pending"),
        "rare_3_days": (1, mark - timedelta(days=3), mark - timedelta(days=9), "pending"),
        "rare_7_days": (1, mark - timedelta(days=7), mark - timedelta(days=9), "pending"),
        "ignored_never": (5, None, mark - timedelta(days=2), "ignored"),
    }
    for name, (hits, at, first_at, status) in rows.items():
        made = SearchMiss(
            scope=tool,
            norm=f"{name}-{uuid.uuid4().hex[:6]}",
            text=name,
            hits=hits,
            status=status,
            suggested_at=at,
            suggested=[] if at is not None else None,
        )
        db.add(made)
        db.flush()
        made.first_at = first_at
    db.commit()
    due = set(
        db.scalars(
            select(SearchMiss.text).where(misses.due_clause(mark), SearchMiss.scope == tool)
        )
    )
    assert due == {"shown_never", "shown_before_night", "rare_never_old", "rare_7_days"}

    # 셈하는 차례 — 검토함에 서는 줄(안 셈한 것 먼저), 그다음 나머지.
    order: list[str] = []
    real = misses.suggestions

    def spy(db: Session, user: Any, row: SearchMiss, axes: Any = None) -> Any:
        order.append(row.text)
        return real(db, user, row, axes)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(misses, "suggestions", spy)
        done = _refresh(db, tool, now=now)
    assert done["done"] == 4 and done["left"] == 0
    assert order[:2] == ["shown_never", "shown_before_night"]
    assert set(order[2:]) == {"rare_never_old", "rare_7_days"}
    # 셈한 줄은 다시 세지 않는다(같은 밤 안).
    assert _refresh(db, tool, now=now + timedelta(hours=1))["done"] == 0


def test_작업은_시간이_되거나_다른_작업이_기다리면_묶음까지만_하고_비켜_준다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """워커는 작업을 하나씩 돌린다 — 셈이 오래 쥐면 사람이 넣은 일괄 입력이 그 뒤에 선다."""
    tool = _make_type(client, admin, "tool", label="툴")
    _make_object(client, admin, tool, label="Kestrel Mesher")
    for word in ("Kestrel Meshr", "Kestrel Meshar", "Kestrel Mesh"):
        _miss(db, tool, word)
    monkeypatch.setattr(misses, "SUGGEST_BATCH", 1)

    timed = _refresh(db, tool, seconds=0.0)
    assert timed == {**timed, "done": 1, "left": 2, "stopped": "time"}

    monkeypatch.setattr(misses, "_others_waiting", lambda _db: True)
    yielded = _refresh(db, tool)
    assert yielded == {**yielded, "done": 1, "left": 1, "stopped": "yield"}

    monkeypatch.setattr(misses, "_others_waiting", lambda _db: False)
    rest = _refresh(db, tool)
    assert rest == {**rest, "done": 1, "left": 0, "stopped": ""}


def test_타이머는_셈할_줄이_있을_때_하나만_넣고_실패한_뒤에는_한_시간_기다린다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tool = _make_type(client, admin, "tool", label="툴")
    _make_object(client, admin, tool, label="Kestrel Mesher")
    _miss(db, tool, "Kestrel Meshr")
    mine: list[uuid.UUID] = []

    def ours() -> list[Job]:
        return list(
            db.scalars(
                select(Job).where(
                    Job.kind == misses.SUGGEST_KIND, Job.status.in_(("queued", "running"))
                )
            )
        )

    try:
        # 남이 남긴 것이 기다리면 그것부터 치운다(이 시험이 넣는 것만 본다).
        for left in ours():
            left.status = "cancelled"
            left.finished_at = datetime.now(UTC) - timedelta(hours=2)
        db.commit()
        assert misses.schedule(db) is True
        (queued,) = ours()
        mine.append(queued.id)
        assert queued.requested_by_id is None and queued.params == {"reason": "timer"}
        # 기다리는 동안은 또 넣지 않는다(두 워커가 같은 때 봐도 하나).
        assert misses.schedule(db) is False

        # 실패하면 한 시간은 다시 안 넣는다 — 같은 실패를 1분마다 쌓지 않게.
        queued.status = "failed"
        queued.finished_at = datetime.now(UTC)
        db.commit()
        assert misses.schedule(db) is False
        assert misses.schedule(db, now=datetime.now(UTC) + timedelta(hours=2)) is True
        (second,) = ours()
        mine.append(second.id)
    finally:
        # 남은 작업을 남기지 않는다 — 다른 시험의 워커가 집어 DB 전체를 셈한다.
        db.rollback()
        db.execute(
            update(Job)
            .where(Job.id.in_(mine), Job.status.in_(("queued", "running")))
            .values(status="cancelled", finished_at=datetime.now(UTC) - timedelta(hours=2))
        )
        db.commit()


def test_작업으로_돌면_그_범위를_셈한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """작업 종류가 등록돼 있고(워커가 돌린다) 본문이 `refresh` 다."""
    from app.modules.jobs import kinds
    from app.modules.jobs import services as job_services

    tool = _make_type(client, admin, "tool", label="툴")
    kestrel = _make_object(client, admin, tool, label="Kestrel Mesher")
    first = _miss(db, tool, "Kestrel Meshr")
    kind = kinds.get(misses.SUGGEST_KIND)
    assert kind is not None and kind.allow_system and not kind.public
    job = job_services.enqueue(
        db, kind=misses.SUGGEST_KIND, params={"scope": tool}, user=None, workspace_id=None
    )
    db.commit()
    current: Job | None = None
    for _ in range(20):
        db.expire_all()
        current = db.get(Job, job.id)
        assert current is not None
        if current.status in ("done", "failed", "cancelled"):
            break
        assert job_services.process_one("test-worker")
    assert current is not None and current.status == "done", current and current.error
    result = current.result or {}
    assert result["done"] == 1 and result["left"] == 0
    # 작업 화면은 결과의 한 줄(`summary`)을 그대로 싣는다.
    assert result["summary"] == "「이것 아닐까」 1줄 계산(제안 있음 1) · 남은 것 없음"
    row = db.get(SearchMiss, first.id)
    assert row is not None and row.suggested and row.suggested[0]["id"] == kestrel["id"]
