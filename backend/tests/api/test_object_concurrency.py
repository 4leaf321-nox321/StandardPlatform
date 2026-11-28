"""함께 온 저장이 **서로의 칸을 지우지 않는다** — 고치기 · 되돌리기 · 일괄 수정
(2026-11-28 점검).

세 길 모두 줄을 읽어 파이썬에서 속성을 병합하고 통째로 다시 썼다. 줄을 잠그지 않아, 두 저장이
같은 옛 값을 읽으면 나중에 끝난 쪽이 먼저 끝난 쪽의 칸을 옛 값으로 되돌렸다 — AI 가 `w` 를,
사람이 `c` 를 함께 고치면 `w` 가 소리 없이 사라졌다.

시험은 **다른 세션이 그 줄을 고치고 아직 커밋하지 않은 때** 요청을 보낸다. 요청이 잠금에 걸려
기다리는 것을 본 뒤 그 세션을 커밋한다 — 잠그고 읽으면 요청은 커밋된 새 값에 병합하고, 안
잠그고 읽으면 커밋 전의 옛 값에 병합해 덮는다. 시각에 기대지 않는다(기다리는 것을 DB 에서
본다).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_history import _history
from tests.api.test_ontology import _make_object, _make_property, _make_type

STALE = code("OBJECTS", 162)


def _part(client: TestClient, admin: Signed) -> str:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="w", label="무게", data_type="number")
    _make_property(client, admin, part, key="c", label="색", data_type="text")
    return part


def _wait_until_blocked(db: Session, seconds: float = 15.0) -> None:
    """이 DB 의 어떤 접속이 **잠금을 기다릴 때까지** — 요청이 그 줄에 닿았다는 뜻이다.

    `pg_stat_activity` 는 트랜잭션 안에서 한 번 찍은 값을 계속 주므로 물을 때마다 끝낸다."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        waiting = db.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                "AND pid <> pg_backend_pid()"
            )
        )
        db.rollback()
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("요청이 잠금에 닿지 않았다")


def _while_other_writes(
    db: Session, object_id: str, patch: dict[str, Any], call: Callable[[], httpx.Response]
) -> httpx.Response:
    """다른 세션이 `patch` 를 그 줄에 써 둔 채(커밋 전) `call` 을 보내고, 기다리는 것을 본 뒤
    커밋한다 — AI 의 저장이 끝나기 직전에 사람의 저장이 들어온 모양."""
    other = SessionLocal()
    got: list[httpx.Response] = []
    try:
        other.execute(
            text(
                "UPDATE objects SET properties = properties || CAST(:patch AS jsonb), "
                "updated_at = now() WHERE id = :id"
            ),
            {"patch": json.dumps(patch), "id": object_id},
        )
        worker = threading.Thread(target=lambda: got.append(call()))
        worker.start()
        _wait_until_blocked(db)
        other.commit()
        worker.join(timeout=30)
        assert not worker.is_alive(), "요청이 끝나지 않았다"
    finally:
        other.rollback()
        other.close()
    assert got, "요청이 답을 못 받았다"
    return got[0]


def _properties(client: TestClient, who: Signed, part: str, object_id: str) -> dict[str, Any]:
    got = client.get(f"/api/objects/{part}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json()["object"]["properties"])


def test_함께_고친_다른_칸이_둘_다_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _part(client, admin)
    bolt = _make_object(client, admin, part, label="볼트", properties={"w": 1, "c": "회색"})

    answer = _while_other_writes(
        db,
        bolt["id"],
        {"w": 2},
        lambda: client.patch(
            f"/api/objects/{part}/{bolt['id']}",
            json={"properties": {"c": "검정"}},
            headers=admin.headers,
        ),
    )
    assert answer.status_code == 200, answer.text
    # 잠그지 않던 때는 w 가 1 로 되돌아갔다(옛 값에 병합해 덮었다).
    assert _properties(client, admin, part, bolt["id"]) == {"w": 2, "c": "검정"}
    # 기록도 지금 값과 맞는다 — 사람의 저장은 c 만 바꿨다.
    top = _history(client, admin, part, bolt["id"])[0]
    assert top["changes"] == {"properties.c": {"before": "회색", "after": "검정"}}


def test_일괄_수정도_함께_고친_다른_칸을_지우지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _part(client, admin)
    bolt = _make_object(client, admin, part, label="볼트", properties={"w": 1, "c": "회색"})
    nut = _make_object(client, admin, part, label="너트", properties={"w": 5, "c": "회색"})

    answer = _while_other_writes(
        db,
        bolt["id"],
        {"w": 2},
        lambda: client.post(
            f"/api/objects/{part}/bulk-edit",
            json={
                "ids": [nut["id"], bolt["id"]],
                "field": "properties.c",
                "value": "검정",
                "apply": True,
            },
            headers=admin.headers,
        ),
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["applied"] is True
    assert _properties(client, admin, part, bolt["id"]) == {"w": 2, "c": "검정"}
    assert _properties(client, admin, part, nut["id"]) == {"w": 5, "c": "검정"}


def test_되돌리기는_잠근_뒤의_값에서_되짚고_기록한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """되돌리기는 칸 전부를 그때 값으로 쓴다 — 그 사이 바뀐 칸도 그때 값이 되는 것이 맞다.
    다만 기록의 「전」 은 **잠근 뒤의 지금 값**이어야 한다. 옛 값에서 세면 그 사이 바뀐 칸이
    기록에서 빠져, 이력이 지금 값에서 거꾸로 되짚을 때 어긋난다."""
    part = _part(client, admin)
    bolt = _make_object(client, admin, part, label="볼트", properties={"w": 1, "c": "회색"})
    response = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"properties": {"c": "검정"}},
        headers=admin.headers,
    )
    assert response.status_code == 200, response.text
    first = _history(client, admin, part, bolt["id"])[-1]

    answer = _while_other_writes(
        db,
        bolt["id"],
        {"w": 2},
        lambda: client.post(
            f"/api/objects/{part}/{bolt['id']}/restore",
            json={"entry_id": first["id"]},
            headers=admin.headers,
        ),
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["properties"] == {"w": 1, "c": "회색"}
    top = _history(client, admin, part, bolt["id"])[0]
    assert top["changes"]["properties.w"] == {"before": 2, "after": 1}
    assert top["changes"]["properties.c"] == {"before": "검정", "after": "회색"}


# --- 읽은 때(`base_updated_at`) -------------------------------------------------------


def _same_instant(left: str, right: str) -> bool:
    return datetime.fromisoformat(left) == datetime.fromisoformat(right)


def test_읽은_뒤_누가_수정했으면_덮지_않고_409(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    bolt = _make_object(client, admin, part, label="볼트", properties={"w": 1, "c": "회색"})
    seen = bolt["updated_at"]

    # AI 가 먼저 w 를 고쳤다.
    ai = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"properties": {"w": 2}, "base_updated_at": seen},
        headers=admin.headers,
    )
    assert ai.status_code == 200, ai.text
    now = ai.json()["updated_at"]
    assert now != seen

    # 사람의 폼은 연 때(seen)의 값을 칸 전부 싣는다 — 덮지 않고 거절한다.
    person = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"properties": {"w": 1, "c": "검정"}, "base_updated_at": seen},
        headers=admin.headers,
    )
    assert person.status_code == 409, person.text
    error = person.json()["error"]
    assert error["code"] == STALE
    assert "그 사이 다른 사람이" in error["message"]
    assert _same_instant(error["details"]["updated_at"], now)
    assert _properties(client, admin, part, bolt["id"]) == {"w": 2, "c": "회색"}

    # 다시 불러온 값(now)으로 보내면 받는다.
    again = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"properties": {"c": "검정"}, "base_updated_at": now},
        headers=admin.headers,
    )
    assert again.status_code == 200, again.text
    assert again.json()["properties"] == {"w": 2, "c": "검정"}

    # 안 보내면 예전 그대로 — 보낸 칸만 병합한다.
    plain = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"properties": {"c": "흰색"}},
        headers=admin.headers,
    )
    assert plain.status_code == 200, plain.text
    assert plain.json()["properties"] == {"w": 2, "c": "흰색"}


def test_되돌리기도_읽은_뒤_수정됐으면_409(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    bolt = _make_object(client, admin, part, label="볼트", properties={"w": 1, "c": "회색"})
    changed = client.patch(
        f"/api/objects/{part}/{bolt['id']}",
        json={"properties": {"c": "검정"}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text
    first = _history(client, admin, part, bolt["id"])[-1]

    stale = client.post(
        f"/api/objects/{part}/{bolt['id']}/restore",
        json={"entry_id": first["id"], "base_updated_at": bolt["updated_at"]},
        headers=admin.headers,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == STALE
    assert _properties(client, admin, part, bolt["id"]) == {"w": 1, "c": "검정"}

    fresh = client.post(
        f"/api/objects/{part}/{bolt['id']}/restore",
        json={"entry_id": first["id"], "base_updated_at": changed.json()["updated_at"]},
        headers=admin.headers,
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["properties"] == {"w": 1, "c": "회색"}


# --- 합치기 ---------------------------------------------------------------------------


def test_합치기는_그_사이_고친_이긴_쪽의_칸을_덮지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """합치기는 이긴 쪽의 빈 칸을 진 쪽 값으로 채워 통째로 쓴다 — 이긴 쪽을 안 잠그고 읽어, 그
    사이 끝난 이긴 쪽의 고치기(색)를 옛 값으로 되돌렸다."""
    part = _part(client, admin)
    winner = _make_object(client, admin, part, label="볼트", properties={"c": "회색"})
    loser = _make_object(client, admin, part, label="볼트(중복)", properties={"w": 5})

    answer = _while_other_writes(
        db,
        winner["id"],
        {"c": "검정"},
        lambda: client.post(
            f"/api/objects/{part}/{loser['id']}/merge",
            json={"into": winner["id"]},
            headers=admin.headers,
        ),
    )
    assert answer.status_code == 200, answer.text
    assert _properties(client, admin, part, winner["id"]) == {"c": "검정", "w": 5}


def test_합치기는_가리키는_것까지_id_차례로_잠가_일괄_수정과_서로_기다리지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """자기 타입을 가리키는 타입에서 — 일괄 수정은 고른 줄을 id 차례로 잠근다. 합치기가 진
    쪽 · 이긴 쪽을 먼저 잡고 가리키는 객체를 나중에 잡으면, 가리키는 객체(작은 id)를 쥔 일괄
    수정이 진 쪽(큰 id)을 기다리는 동안 합치기는 가리키는 객체를 기다린다 — 서로 기다린다.
    합치기가 전부를 한 차례로 잡으면 일괄 수정은 진 쪽을 바로 얻는다."""
    part = _part(client, admin)
    _make_property(
        client,
        admin,
        part,
        key="parent",
        label="상위",
        data_type="object_ref",
        ref_type_slug=part,
    )
    one = _make_object(client, admin, part, label="하나")
    two = _make_object(client, admin, part, label="둘")
    small, large = sorted((one, two), key=lambda row: str(row["id"]))
    winner = _make_object(client, admin, part, label="남는 것")
    # 작은 id 가 큰 id(질 쪽)를 가리킨다.
    pointed = client.patch(
        f"/api/objects/{part}/{small['id']}",
        json={"properties": {"parent": large["id"]}},
        headers=admin.headers,
    )
    assert pointed.status_code == 200, pointed.text

    other = SessionLocal()
    got: list[httpx.Response] = []
    try:
        # 일괄 수정이 id 차례로 잠그는 중 — 가리키는 객체(작은 id)를 쥐었다.
        other.execute(
            text("SELECT id FROM objects WHERE id = :id FOR UPDATE"), {"id": small["id"]}
        )
        worker = threading.Thread(
            target=lambda: got.append(
                client.post(
                    f"/api/objects/{part}/{large['id']}/merge",
                    json={"into": winner["id"]},
                    headers=admin.headers,
                )
            )
        )
        worker.start()
        _wait_until_blocked(db)
        # 다음 줄(큰 id = 질 쪽) — 합치기가 아직 안 잡았으면 바로 얻는다.
        other.execute(text("SET LOCAL lock_timeout = '10s'"))
        other.execute(
            text("SELECT id FROM objects WHERE id = :id FOR UPDATE"), {"id": large["id"]}
        )
        other.commit()
        worker.join(timeout=30)
        assert not worker.is_alive(), "합치기가 끝나지 않았다"
    finally:
        other.rollback()
        other.close()
    assert got and got[0].status_code == 200, got[0].text if got else "답 없음"
    assert _properties(client, admin, part, small["id"]) == {"parent": winner["id"]}
