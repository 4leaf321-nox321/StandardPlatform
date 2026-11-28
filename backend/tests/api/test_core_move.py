"""코어 API 와 타입 옮기기(ADR 0094) — **옮긴 객체가 받는 쪽에 어떻게 가나.**

옮기기는 객체 id 를 그대로 둔 채 타입을 바꾼다. 예전에는 객체의 바뀐 때를 그대로 둬 대상
타입의 `since` 가 옮겨 온 행을 안 줬고, 원 타입은 말없이 비어 받는 쪽이 떠난 행을 영영 살아
있는 것으로 들었다(처음부터 받으면 0건이라 「전부 안 오면 멈춤」, 2026-11-28 점검).

지키는 것:

- 옮긴 행은 대상의 `since` 로 온다(바뀐 때가 옮긴 때로 오른다).
- 원 타입의 카탈로그 · 쪽은 떠난 때와 간 곳(`moved_at` · `moved_to`)을 싣고, 판이 바뀐다.
- 간 곳이 공개 타입이 아니면 간 곳은 밝히지 않는다(떠난 때만).
- 다시 옮기면 마지막에 닿은 곳을, 돌아오면 아무것도 말하지 않는다.
- 옮기며 같은 식별자로 병합된 것은 무덤 대신 이긴 쪽의 지금 행이 간다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from tests.api.conftest import Signed, clock_settled
from tests.api.test_core_api import _open, _rows
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _kind(client: TestClient, admin: Signed, label: str, *, core: bool = True) -> str:
    slug = _make_type(
        client, admin, label=f"{label} {uuid.uuid4().hex[:6]}", key_policy="optional"
    )
    _make_property(client, admin, slug, key="country", label="국가", data_type="text")
    if core:
        _open(client, admin, slug)
    return slug


def _move(
    client: TestClient, admin: Signed, slug: str, to: str, **body: Any
) -> dict[str, Any]:
    clock_settled()  # 옮긴 때가 앞서 받은 시계 뒤에 서게
    got = client.post(
        f"/api/ontology/types/{slug}/move",
        json={"to": to, "apply": True, "accept_core": True, **body},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["applied"] is True, got.json()
    clock_settled()
    return dict(got.json())


def _entry(client: TestClient, admin: Signed, slug: str) -> dict[str, Any]:
    catalog = client.get("/api/core", headers=admin.headers).json()
    return next(one for one in catalog["types"] if one["slug"] == slug)


def _revision(client: TestClient, admin: Signed) -> str:
    return str(client.get("/api/core", headers=admin.headers).json()["revision"])


def test_옮긴_행은_대상의_since_로_오고_원_타입은_떠난_때와_간_곳을_말한다(
    client: TestClient, admin: Signed
) -> None:
    old, new = _kind(client, admin, "협력사"), _kind(client, admin, "공급사")
    for key in ("V-1", "V-2"):
        _make_object(client, admin, old, label=key, key=key, properties={"country": "KR"})
    _make_object(client, admin, new, label="V-9", key="V-9")
    seen_new = _rows(client, admin, new)["as_of"]
    seen_old = _rows(client, admin, old)["as_of"]
    before = _entry(client, admin, old)
    assert before["moved_at"] is None and before["moved_to"] is None
    revision = _revision(client, admin)

    _move(client, admin, old, new)

    # 대상 — 옮겨 온 행이 「바뀐 것」 으로 온다(값 · 식별자 그대로).
    landed = _rows(client, admin, new, since=seen_new)
    got = {one["key"]: one for one in landed["items"]}
    assert set(got) == {"V-1", "V-2"}, landed
    assert not got["V-1"]["deleted"] and got["V-1"]["properties"] == {"country": "KR"}
    assert landed["moved_at"] is None and landed["moved_to"] is None

    # 원 타입 — 빈 쪽이지만 떠난 때와 간 곳을 싣는다(증분 · 처음부터 모두).
    for params in ({"since": seen_old}, {}):
        left = _rows(client, admin, old, **params)
        assert left["items"] == [], left
        assert left["moved_to"] == new and left["moved_at"], left
    entry = _entry(client, admin, old)
    assert entry["moved_to"] == new and entry["moved_at"] == left["moved_at"]
    assert entry["count"] == 0
    assert _entry(client, admin, new)["moved_at"] is None
    # 판이 바뀐다 — 옛 키트는 처음부터 다시 받고, 원 타입이 비어 와서 멈춘다(지우지 않는다).
    assert _revision(client, admin) != revision


def test_공개하지_않은_타입으로_옮기면_간_곳은_밝히지_않는다(
    client: TestClient, admin: Signed
) -> None:
    old, hidden = _kind(client, admin, "협력사"), _kind(client, admin, "비공개", core=False)
    _make_object(client, admin, old, label="V-1", key="V-1")
    _move(client, admin, old, hidden)
    entry = _entry(client, admin, old)
    assert entry["moved_at"] and entry["moved_to"] is None, entry
    page = _rows(client, admin, old)
    assert page["moved_at"] == entry["moved_at"] and page["moved_to"] is None
    assert hidden not in str(page) and hidden not in str(entry)


def test_다시_옮기면_마지막에_닿은_곳을_돌아오면_아무것도_말하지_않는다(
    client: TestClient, admin: Signed
) -> None:
    first, second, third = (_kind(client, admin, name) for name in ("가", "나", "다"))
    _make_object(client, admin, first, label="K-1", key="K-1")
    _make_object(client, admin, second, label="K-2", key="K-2")
    _move(client, admin, first, second)
    _move(client, admin, second, third)
    assert _entry(client, admin, first)["moved_to"] == third
    assert _entry(client, admin, second)["moved_to"] == third

    # 원 타입의 slug 를 바꿔도 그 기록을 따라간다(옮기기 기록에는 그때의 slug 만 있다).
    renamed = f"{first}_x"
    got = client.post(
        f"/api/ontology/types/{first}/rename",
        json={"to": renamed, "apply": True, "accept_core": True},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert _entry(client, admin, renamed)["moved_to"] == third

    # 다시 원 타입으로 — 객체가 돌아왔다. 둘째는 원 타입으로 간 것이 된다.
    _move(client, admin, third, renamed)
    back = _entry(client, admin, renamed)
    assert back["moved_at"] is None and back["moved_to"] is None, back
    assert _entry(client, admin, second)["moved_to"] == renamed
    assert {one["key"] for one in _rows(client, admin, renamed)["items"]} == {"K-1", "K-2"}


def _stream(
    client: TestClient, admin: Signed, slug: str, since: str, limit: int
) -> list[dict[str, Any]]:
    """`since` 뒤의 흐름 전부 — `next` 가 빌 때까지(쪽에 걸쳐 같은 행이 두 번 오는지 본다)."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"since": since, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        page = _rows(client, admin, slug, **params)
        out.extend(page["items"])
        cursor = page["next"]
        if not cursor:
            return out


@pytest.mark.parametrize("filled", [True, False])
def test_옮기며_같은_식별자로_병합된_것은_무덤_대신_이긴_쪽의_행이_한_번_간다(
    client: TestClient, admin: Signed, filled: bool
) -> None:
    """받는 쪽은 행을 (타입, key) 로 쥔다 — 진 쪽의 무덤(`merged_into` 가 제 key)을 그대로
    보내면 받는 쪽은 이긴 쪽의 행을 비활성으로 만든다. 그 자리에 이긴 쪽을 두되 **한 번만**
    — 병합이 이긴 쪽의 빈 칸을 채우면(`filled`) 이긴 쪽이 따로 「바뀐 것」 으로 오는데, 그때도
    무덤 자리에 이긴 쪽을 두면 같은 행이 두 번 가서 형제 SP 가 「같은 식별자」 로
    거절했다(2026-11-28 검토). 한 쪽에 하나씩 받아도(쪽에 걸쳐도) 한 번이다."""
    old, new = _kind(client, admin, "협력사"), _kind(client, admin, "공급사")
    _make_object(client, admin, old, label="에이컴", key="V-1", properties={"country": "KR"})
    _make_object(
        client,
        admin,
        new,
        label="ACME",
        key="V-1",
        properties={} if filled else {"country": "US"},
    )
    seen = _rows(client, admin, new)["as_of"]
    _move(client, admin, old, new, merge_duplicates=True)

    for limit in (500, 1):
        landed = _stream(client, admin, new, seen, limit)
        assert [(one["key"], one["deleted"]) for one in landed] == [("V-1", False)], landed
        winner = landed[0]
        # 이긴 쪽의 지금 행 그대로 — 병합이 진 쪽의 빈 칸 · 이름(별칭)을 채웠다.
        assert winner["label"] == "ACME", winner
        assert winner["properties"]["country"] == ("KR" if filled else "US"), winner
        assert winner["properties"]["aliases"] == ["에이컴"]


def test_옮긴_원_타입을_지우고_같은_slug_로_새로_만들면_옛_옮기기를_물려받지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """옮기기 기록의 원 타입이 slug 로만 맞춰지면 새 타입이 옛 `moved_at` · `moved_to` 를
    물려받고(판도 바뀐다), 그것을 처음 본 코어 구독은 새 타입의 객체를 사용 중지했다
    (2026-11-28 검토). 원 타입은 id 로 맞추고, 원 타입 id 가 없는 옛 기록은 그 slug 를 다른
    타입이 내놓은 때(지움 · slug 변경) 뒤로만 맞춘다."""
    old, new = _kind(client, admin, "협력사"), _kind(client, admin, "공급사")
    _make_object(client, admin, old, label="V-1", key="V-1")
    _move(client, admin, old, new)
    assert _entry(client, admin, old)["moved_to"] == new
    _open(client, admin, old, on=False)
    gone = client.delete(f"/api/ontology/types/{old}", headers=admin.headers)
    assert gone.status_code == 204, gone.text

    again = _make_type(client, admin, slug=old, label="새 협력사", key_policy="optional")
    _open(client, admin, again)
    _make_object(client, admin, again, label="V-1", key="V-1")
    fresh = _entry(client, admin, again)
    assert fresh["moved_at"] is None and fresh["moved_to"] is None, fresh
    assert _rows(client, admin, again)["moved_at"] is None

    # 원 타입 id 가 없는 옛 기록(이 판 전)이어도 — 지운 일이 그 slug 의 앞을 막는다.
    record = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "ontology.type.move",
            AuditEntry.target_label == f"{old} → {new}",
        )
    ).one()
    assert record.changes["from_id"]
    db.execute(
        text("UPDATE audit_entries SET changes = changes - 'from_id' WHERE id = :id"),
        {"id": record.id},
    )
    db.commit()
    assert _entry(client, admin, again)["moved_at"] is None


def test_옮기기_계획은_수신_시스템에_실제로_일어나는_일을_말한다(
    client: TestClient, admin: Signed
) -> None:
    """예전 계획은 「수신 시스템에서 사라집니다」 · 「새 객체를 받습니다」 라고 했지만 바뀐
    때가 그대로라 어느 쪽도 일어나지 않았다. 이제 말하는 대로 된다(위 시험들)."""
    old, new = _kind(client, admin, "협력사"), _kind(client, admin, "공급사")
    hidden = _kind(client, admin, "비공개", core=False)
    _make_object(client, admin, old, label="V-1", key="V-1")
    token = client.post(
        "/api/auth/tokens",
        json={"name": f"core-{uuid.uuid4().hex[:6]}", "scopes": ["core:read"]},
        headers=admin.headers,
    )
    assert token.status_code == 201, token.text  # 받는 쪽이 있어야 통보 경고가 선다

    def warnings(to: str) -> str:
        got = client.post(
            f"/api/ontology/types/{old}/move", json={"to": to}, headers=admin.headers
        )
        assert got.status_code == 200, got.text
        return " ".join(got.json()["warnings"])

    told = warnings(new)
    assert "moved_to" in told and "2026-11-28" in told and "지우지 않고" in told, told
    assert "「바뀐 것」 으로 다시 받습니다" in told, told
    gone = warnings(hidden)
    assert "사라집니다" in gone and "밝히지 않습니다" in gone, gone
