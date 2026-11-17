"""AI 가 전부 읽는 길(ADR 0079 · 0080 · 0081 · 0083) — 진짜 앱으로.

- 커서 쪽넘김 · 토큰의 쪽 상한(화면 세션은 200 그대로)
- 세부 기준의 쪽 · 「그 밖에」 조각
- 전수 집계 — 전부 · 검증값 · 큰 결과는 작업으로 · 커서로 받기
- 몰림 — 429 · 503 과 `Retry-After`
- 0건 진단의 가벼운 길
- 자료 범위 표지(`scope` · `mixed`)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.objects import census as census_service
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _token(client: TestClient, who: Signed) -> dict[str, str]:
    """API 토큰(기계) 머리 — MCP 의 AI · 다른 시스템이 부르는 길."""
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"ai_{uuid.uuid4().hex[:6]}", "scopes": ["read"]},
        headers=who.headers,
    )
    assert made.status_code in (200, 201), made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _bulk(db: Session, slug: str, count: int, **props: Any) -> None:
    """객체를 DB 로 한꺼번에 — 쪽 상한을 넘기려면 수백 개가 든다(API 로 하나씩은 느리다)."""
    type_id = db.query(ObjectType.id).filter(ObjectType.slug == slug).scalar()
    db.add_all(
        ObjectInstance(
            type_id=type_id,
            label=f"줄{index:05d}",
            properties={
                key: (value(index) if callable(value) else value)
                for key, value in props.items()
            },
        )
        for index in range(count)
    )
    db.commit()


def _get(
    client: TestClient, headers: dict[str, str], path: str, **params: Any
) -> dict[str, Any]:
    got = client.get(path, params=params, headers=headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


# --- 커서 쪽넘김 · 쪽 상한(ADR 0079) ---------------------------------------------------


def test_커서로_끝까지_받으면_빠지거나_겹치는_줄이_없다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="기록")
    _bulk(db, kind, 23)
    seen: list[str] = []
    cursor = "start"
    pages = 0
    while cursor:
        page = _get(client, admin.headers, f"/api/objects/{kind}", cursor=cursor, limit=5)
        assert page["total"] == 23  # 첫 쪽에서 센 수를 그대로
        assert page["offset"] == len(seen)
        seen += [one["id"] for one in page["items"]]
        cursor = page["next_cursor"]
        pages += 1
    assert pages == 5 and len(seen) == 23 and len(set(seen)) == 23
    assert seen == sorted(seen)  # id 차례

    # offset 으로 넘기면 커서는 늘 비어 있다.
    plain = _get(client, admin.headers, f"/api/objects/{kind}", limit=5)
    assert plain["next_cursor"] is None


def test_커서는_다른_조건에_못_쓰고_망가진_커서는_422(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="기록")
    _make_property(client, admin, kind, key="grade", label="등급", data_type="text")
    _bulk(db, kind, 6, grade=lambda index: "A" if index % 2 else "B")
    first = _get(client, admin.headers, f"/api/objects/{kind}", cursor="start", limit=2)
    assert first["total"] == 6 and first["next_cursor"]
    other = client.get(
        f"/api/objects/{kind}",
        params={"cursor": first["next_cursor"], "limit": 2, "p.grade": "A"},
        headers=admin.headers,
    )
    assert other.status_code == 422 and other.json()["error"]["code"].endswith("OBJECTS-0155")
    broken = client.get(
        f"/api/objects/{kind}", params={"cursor": "zz!"}, headers=admin.headers
    )
    assert broken.status_code == 422 and broken.json()["error"]["code"].endswith(
        "OBJECTS-0154"
    )
    # 같은 조건이면 쪽 크기를 바꿔도 이어진다(쪽 크기는 조건이 아니다).
    rest = _get(
        client, admin.headers, f"/api/objects/{kind}", cursor=first["next_cursor"], limit=10
    )
    assert len(rest["items"]) == 4 and rest["next_cursor"] is None


def test_쪽_상한은_화면_200_토큰_2000(client: TestClient, admin: Signed, db: Session) -> None:
    kind = _make_type(client, admin, label="기록")
    _bulk(db, kind, 230)
    screen = _get(client, admin.headers, f"/api/objects/{kind}", limit=1000)
    assert screen["limit"] == 200 and len(screen["items"]) == 200
    machine = _get(client, _token(client, admin), f"/api/objects/{kind}", limit=1000)
    assert machine["limit"] == 1000 and len(machine["items"]) == 230
    huge = _get(client, _token(client, admin), f"/api/objects/{kind}", limit=99999)
    assert huge["limit"] == 2000


# --- 세부 기준의 쪽 · 「그 밖에」 --------------------------------------------------------


def test_세부_기준이_열둘을_넘으면_그_밖에로_묶고_쪽으로_넘긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="기록")
    _make_property(client, admin, kind, key="grade", label="등급", data_type="text")
    _make_property(client, admin, kind, key="market", label="시장", data_type="text")
    # 시장 15곳 — 시장 i 에 (i+1) 건씩, 등급은 A · B 번갈아.
    rows = [(f"M{i:02d}", "A" if n % 2 else "B") for i in range(15) for n in range(i + 1)]
    type_id = db.query(ObjectType.id).filter(ObjectType.slug == kind).scalar()
    db.add_all(
        ObjectInstance(type_id=type_id, label=f"줄{n}", properties={"market": m, "grade": g})
        for n, (m, g) in enumerate(rows)
    )
    db.commit()
    path = f"/api/objects/{kind}/summary"
    params = {"group_by": "properties.grade", "split_by": "properties.market"}
    screen = _get(client, admin.headers, path, **params, split_limit=200)
    assert len(screen["splits"]) == 12  # 화면은 그림 — 12 까지
    assert screen["split_total"] == 15 and screen["other_splits"] == 3
    for bucket in screen["buckets"]:
        parts = sum(one["count"] for one in bucket["parts"])
        assert parts + bucket["other_part"]["count"] == bucket["count"]
        assert bucket["other_part"]["label"].startswith("그 밖에")

    machine = _token(client, admin)
    whole = _get(client, machine, path, **params, split_limit=200)
    assert len(whole["splits"]) == 15 and whole["other_splits"] == 0
    assert all(one["other_part"] is None for one in whole["buckets"])
    second = _get(client, machine, path, **params, split_limit=10, split_offset=10)
    assert second["split_offset"] == 10 and len(second["splits"]) == 5
    assert second["splits"][0] == whole["splits"][10]


# --- 전수 집계 ---------------------------------------------------------------------------


def test_전수_집계는_묶음을_전부와_합의_검증값을_준다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="기록")
    _make_property(client, admin, kind, key="market", label="시장", data_type="text")
    _bulk(db, kind, 40, market=lambda index: f"M{index % 35:02d}" if index < 38 else None)
    found = _get(
        client, admin.headers, f"/api/objects/{kind}/census", group_by="properties.market"
    )
    assert found["complete"] is True and found["job"] is None
    assert found["groups"] == 36 and len(found["rows"]) == 36  # 35 곳 + (비어 있음)
    assert sum(one["count"] for one in found["rows"]) == 40
    check = found["check"]
    assert check["matches"] is True and check["total"] == check["group_sum"] == 40
    assert "같습니다" in check["message"]
    assert any(one["key"] is None for one in found["rows"])


def test_전수_집계가_크면_작업으로_돌려_커서로_받는다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(census_service, "INLINE_MAX", 3)
    kind = _make_type(client, admin, label="기록")
    _make_property(client, admin, kind, key="market", label="시장", data_type="text")
    _bulk(db, kind, 20, market=lambda index: f"M{index % 7}")
    found = _get(
        client,
        admin.headers,
        f"/api/objects/{kind}/census",
        group_by="properties.market",
        order="key",
    )
    assert found["complete"] is False and found["rows"] == []
    assert found["groups"] == 7 and found["check"]["matches"] is True
    job = finish_job(client, admin, found["job"])
    assert job["status"] == "done", job
    got: list[dict[str, Any]] = []
    cursor = found["next_cursor"]
    while cursor:
        page = _get(
            client, admin.headers, f"/api/objects/{kind}/census/rows", cursor=cursor, limit=3
        )
        assert page["status"] == "done" and page["groups"] == 7
        assert page["check"]["group_sum"] == 20
        got += page["rows"]
        cursor = page["next_cursor"]
    assert [one["key"] for one in got] == [f"M{i}" for i in range(7)]
    assert sum(one["count"] for one in got) == 20

    # 다른 타입의 커서로는 못 읽는다.
    other = _make_type(client, admin, label="다른")
    refused = client.get(
        f"/api/objects/{other}/census/rows",
        params={"cursor": found["next_cursor"]},
        headers=admin.headers,
    )
    assert refused.status_code == 404


def test_전수_집계를_처음부터_작업으로_돌리는_것은_읽기_토큰으로_된다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    kind = _make_type(client, admin, label="기록")
    _make_property(client, admin, kind, key="market", label="시장", data_type="text")
    _bulk(db, kind, 9, market=lambda index: f"M{index % 3}")
    machine = _token(client, admin)
    made = client.post(
        f"/api/objects/{kind}/census/job",
        params={"group_by": "properties.market"},
        headers=machine,
    )
    assert made.status_code == 202, made.text
    job = finish_job(client, admin, made.json())
    assert job["result"]["groups"] == 3 and job["result"]["check"]["matches"] is True
    cursor = census_service.encode_cursor(uuid.UUID(job["id"]), 0)
    page = _get(client, machine, f"/api/objects/{kind}/census/rows", cursor=cursor)
    assert sorted(one["key"] for one in page["rows"]) == ["M0", "M1", "M2"]
    # 틀린 기준은 맡기기 전에 422.
    wrong = client.post(
        f"/api/objects/{kind}/census/job",
        params={"group_by": "properties.nope"},
        headers=machine,
    )
    assert wrong.status_code == 422, wrong.text


# --- 몰림 — 429 · 503 과 Retry-After(ADR 0080) ------------------------------------------


def test_자리가_다_찼으면_429_와_Retry_After(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.database import engine
    from app.shared import readguard

    kind = _make_type(client, admin, label="기록")
    monkeypatch.setattr(get_settings(), "list_slots", 1)
    monkeypatch.setattr(readguard, "SLOT_WAIT", 0.3)
    holder = engine.connect()
    try:
        trans = holder.begin()
        holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('read_screen:0'))"))
        busy = client.get(f"/api/objects/{kind}", headers=admin.headers)
        assert busy.status_code == 429, busy.text
        assert busy.headers["Retry-After"] == "3"
        assert busy.json()["error"]["details"]["retry_after"] == 3
        # 토큰은 자리가 따로다 — 화면이 다 쥐어도 AI 는 읽는다.
        assert (
            client.get(f"/api/objects/{kind}", headers=_token(client, admin)).status_code
            == 200
        )
        trans.rollback()
    finally:
        holder.close()
    assert client.get(f"/api/objects/{kind}", headers=admin.headers).status_code == 200


def test_DB_연결이_바닥나면_500_이_아니라_503_과_Retry_After(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    from app.modules.objects import routes as objects_routes

    def exhausted(*_args: Any, **_kwargs: Any) -> Any:
        raise PoolTimeout("QueuePool limit of size 5 overflow 10 reached")

    kind = _make_type(client, admin, label="기록")
    monkeypatch.setattr(objects_routes, "_scope", exhausted)
    got = client.get(f"/api/objects/{kind}", headers=admin.headers)
    assert got.status_code == 503, got.text
    assert got.headers["Retry-After"] == "5"
    assert got.json()["error"]["code"].endswith("COMMON-0503")


# --- 0건 진단의 가벼운 길(ADR 0081) -------------------------------------------------------


def test_큰_타입의_0건은_가벼운_진단으로_다시_세지_않는다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="기록")
    _make_property(client, admin, kind, key="grade", label="등급", data_type="text")
    _bulk(db, kind, 5, grade="A")
    path = f"/api/objects/{kind}/diagnose"
    full = _get(client, admin.headers, path, **{"p.grade": "Z"})
    assert full["mode"] == "full" and full["reason"] == "filters" and full["filters"]

    monkeypatch.setattr(get_settings(), "diagnose_full_max_rows", 3)
    light = _get(client, admin.headers, path, **{"p.grade": "Z"})
    assert light["mode"] == "light" and light["reason"] == "filters"
    assert light["filters"] == [] and light["type_total"] == 5 and light["hidden"] == 0
    assert "diagnose=full" in light["message"]
    # 부르는 쪽이 고르면 크기와 상관없이 그 길 — 방금 센 수를 넘기면 다시 세지 않는다.
    forced = _get(client, admin.headers, path, mode="full", **{"p.grade": "Z"})
    assert forced["mode"] == "full" and forced["filters"]
    told = _get(client, admin.headers, path, mode="light", total=0, **{"p.grade": "A"})
    assert told["reason"] == "filters"  # 넘긴 0 을 믿는다(다시 세면 5건이다)

    empty = _make_type(client, admin, label="빈")
    assert (
        _get(client, admin.headers, f"/api/objects/{empty}/diagnose", mode="light")["reason"]
        == "empty_type"
    )


@pytest.mark.usefixtures("department_scope")
def test_가벼운_진단도_부서_밖이면_안_보인다고_말한다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    from app.modules.workspaces.models import Workspace

    other = Workspace(slug=f"o-{uuid.uuid4().hex[:6]}", name="다른 부서")
    db.add(other)
    db.commit()
    kind = _make_type(client, admin, label="기록")
    made = client.post(
        f"/api/objects/{kind}",
        json={"workspace_slug": other.slug, "label": "볼트"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    found = _get(client, member.headers, f"/api/objects/{kind}/diagnose", mode="light")
    assert found["mode"] == "light" and found["reason"] == "not_visible"
    assert found["hidden"] == 1 and found["type_total"] == 0
    # 내 부서 것이 하나 생기면 「있다」 — 몇 건이 보이는지는 세지 않는다(부서로 좁힌 사람).
    _make_object(client, admin, kind, label="내 것")
    seen = _get(
        client, member.headers, f"/api/objects/{kind}/diagnose", mode="light", q="없는말"
    )
    assert (
        seen["reason"] == "filters" and seen["type_total"] is None and seen["hidden"] is None
    )


# --- 자료 범위 표지(ADR 0083) ---------------------------------------------------------------


def test_scope_는_목록_통계_전수_집계에_실리고_섞이면_mixed(
    client: TestClient, admin: Signed
) -> None:
    domestic = _make_type(client, admin, label="국내 서비스", scope=" 국내 ")
    overseas = _make_type(client, admin, label="해외 서비스", scope="해외")
    plain = _make_type(client, admin, label="미지정")
    listed_types = client.get("/api/ontology/types", headers=admin.headers).json()
    assert next(one for one in listed_types if one["slug"] == domestic)["scope"] == "국내"
    _make_object(client, admin, domestic, label="하나")

    listed = _get(client, admin.headers, f"/api/objects/{domestic}")
    assert listed["scope"] == "국내" and listed["scope_mix"] == []
    summary = _get(client, admin.headers, f"/api/objects/{domestic}/summary")
    assert summary["scope"] == "국내"
    census = _get(client, admin.headers, f"/api/objects/{domestic}/census", group_by="status")
    assert census["scope"] == "국내"
    assert _get(client, admin.headers, f"/api/objects/{plain}")["scope"] is None

    # 인터페이스로 묶으면 섞인다.
    iface = client.post(
        "/api/ontology/interfaces",
        json={"slug": f"svc_{uuid.uuid4().hex[:6]}", "label": "서비스"},
        headers=admin.headers,
    )
    assert iface.status_code == 201, iface.text
    slug = iface.json()["slug"]
    for one in (domestic, overseas, plain):
        patched = client.patch(
            f"/api/ontology/types/{one}",
            json={"interface_slugs": [slug]},
            headers=admin.headers,
        )
        assert patched.status_code == 200, patched.text
    mixed = _get(client, admin.headers, f"/api/objects/{slug}")
    assert mixed["scope"] == "mixed"
    assert mixed["scope_mix"] == ["(미지정)", "국내", "해외"]
    narrowed = _get(client, admin.headers, f"/api/objects/{slug}", types=domestic)
    assert narrowed["scope"] == "국내"

    # 고치기 — 빈 글자는 미지정으로, 길이 상한.
    cleared = client.patch(
        f"/api/ontology/types/{overseas}", json={"scope": ""}, headers=admin.headers
    )
    assert cleared.status_code == 200 and cleared.json()["scope"] == ""
    too_long = client.patch(
        f"/api/ontology/types/{overseas}", json={"scope": "가" * 41}, headers=admin.headers
    )
    assert too_long.status_code == 422


def test_줄마다_자기_참조의_이름만_싣는다(client: TestClient, admin: Signed) -> None:
    """쪽 전체의 이름을 줄마다 통째로 실으면 줄 수 x 이름 수로 커진다(2,000만 건 DB 에서 줄
    하나 23KB)."""
    vendor = _make_type(client, admin, label="공급사")
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    _make_property(
        client,
        admin,
        part,
        key="others",
        label="다른 공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
        multi=True,
    )
    a = _make_object(client, admin, vendor, label="가공급")
    b = _make_object(client, admin, vendor, label="나공급")
    c = _make_object(client, admin, vendor, label="다공급")
    _make_object(client, admin, part, label="볼트", properties={"vendor": a["id"]})
    _make_object(
        client, admin, part, label="너트", properties={"vendor": b["id"], "others": [c["id"]]}
    )
    rows = {
        one["label"]: one
        for one in _get(client, admin.headers, f"/api/objects/{part}")["items"]
    }
    assert rows["볼트"]["ref_labels"] == {a["id"]: "가공급"}
    assert rows["너트"]["ref_labels"] == {b["id"]: "나공급", c["id"]: "다공급"}
