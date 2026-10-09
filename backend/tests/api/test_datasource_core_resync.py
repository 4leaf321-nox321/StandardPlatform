"""형제 코어의 선을 **처음부터 다시 받을 때** — 끝까지 받은 차례에 상대에서 끊긴 선을 정리한다.

상대가 `reset` 을 주면(무덤의 보관 기간이 지났다) 전량을 다시 받는다. 그 응답에는 끊긴 선이
없으므로 이쪽에서 「안 온 선」 을 끊어야 둘이 맞는다. 전량이 한 번에 받는 상한을 넘으면 여러
차례에 걸치는데, 예전에는 그때 더하기로만 이어 받고 정리를 못 해 끊긴 선이 영영 남았다
(2026-10-08).

여기서 지키는 것: 여러 차례에 걸쳐도 **마지막 쪽까지 받은 차례에** 정리하나, 도중에 실패하면
안 끊나(일부만 본 것으로 끊으면 아직 안 받은 선이 끊긴다), **사람이 이은 선은 안 끊나**,
한꺼번에 너무 많이 안 오면 멈추나.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.modules.datasources import odata
from app.modules.datasources.models import DataSource
from app.modules.objects.models import ObjectInstance, ObjectRelation
from tests.api.conftest import Signed
from tests.api.test_datasource_core import (
    FakeCore,
    _edge,
    _item,
    _partner_kind,
    _saved,
    _source,
    _sync,
    _vendor_type,
    sibling,
)
from tests.api.test_ontology import _link

__all__ = ["sibling"]  # 가짜 형제 설치(픽스처)를 이 파일에서도 쓴다


def _edges(db: Session, kind: str) -> set[tuple[str, str]]:
    """그 관계 종류의 선 — (출발 식별자, 도착 식별자)."""
    src = aliased(ObjectInstance)
    dst = aliased(ObjectInstance)
    rows = db.execute(
        select(src.key, dst.key)
        .select_from(ObjectRelation)
        .join(src, src.id == ObjectRelation.src_object_id)
        .join(dst, dst.id == ObjectRelation.dst_object_id)
        .where(ObjectRelation.relation == kind)
    )
    return {(str(a), str(b)) for a, b in rows}


def _ids(client: TestClient, admin: Signed, vendor: str) -> dict[str, str]:
    got = client.get(f"/api/objects/{vendor}?limit=200", headers=admin.headers).json()
    return {one["key"]: one["id"] for one in got["items"]}


def _hub(kind: str, pairs: list[tuple[int, int]], at: str) -> list[dict[str, Any]]:
    return [_edge(f"V-00{a}", kind, f"V-00{b}", at) for a, b in pairs]


def _world(
    client: TestClient,
    admin: Signed,
    sibling: FakeCore,
    pairs: list[tuple[int, int]],
    **kw: Any,
) -> tuple[str, str, dict[str, Any]]:
    vendor = _vendor_type(client, admin)
    kind = _partner_kind(client, admin, vendor)
    sibling.items = [
        _item(f"V-00{n}", f"공급사 {n}", f"2026-09-0{n}T00:00:00.000000Z") for n in range(1, 5)
    ]
    sibling.edges = _hub(kind, pairs, "2026-09-02T00:00:00.000000Z")
    source = _source(client, admin, vendor, options={"relations": True}, **kw)
    return vendor, kind, source


def _reset(sibling: FakeCore, kind: str, pairs: list[tuple[int, int]]) -> None:
    """상대가 잠든 사이 선을 끊고(무덤은 보관 기간이 지나 사라졌다) 새 선을 이었다 — 다음
    물음에 reset 을 준다."""
    sibling.edges = _hub(kind, pairs, "2026-09-05T00:00:00.000000Z")
    sibling.edges_as_of = "2026-09-06T00:00:00.000000Z"
    sibling.edges_reset = True


def test_한_번에_끝나는_처음부터_다시_받기도_이_소스가_이은_선만_끊는다(
    client: TestClient, admin: Signed, db: Session, sibling: FakeCore
) -> None:
    """예전의 「파일대로 맞춤」 은 온 목록에 나온 (출발 객체 · 관계 종류) 안의 선을 **누가
    이었든** 끊었다 — 사람이 화면에서 이은 V-001 → V-004 도 끊겼다."""
    vendor, kind, source = _world(
        client, admin, sibling, [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]
    )
    assert _sync(client, admin, source["slug"])["counts"]["relations_create"] == 5
    ids = _ids(client, admin, vendor)
    drawn = _link(client, admin, vendor, ids["V-001"], kind, ids["V-004"])
    assert drawn.status_code == 201, drawn.text

    _reset(sibling, kind, [(1, 2), (1, 3), (2, 3), (3, 4)])  # 2 → 4 가 끊겼다
    planned = _sync(client, admin, source["slug"], apply=False)
    assert planned["counts"]["relations_unlink"] == 1, planned["counts"]
    assert ("V-002", "V-004") in _edges(db, kind)  # 미리 보기는 안 끊는다

    done = _sync(client, admin, source["slug"])
    assert done["run"]["status"] == "ok", done["run"]
    assert done["counts"]["relations_unlink"] == 1, done["counts"]
    assert _edges(db, kind) == {
        ("V-001", "V-002"),
        ("V-001", "V-003"),
        ("V-002", "V-003"),
        ("V-003", "V-004"),
        ("V-001", "V-004"),  # 사람이 이은 선은 그대로
    }
    assert any("1줄을 끊었습니다" in one for one in done["run"]["errors"]), done["run"]
    assert _saved(client, admin, source["slug"])["relations_since_mark"] == sibling.edges_as_of


def test_여러_차례에_걸쳐_다시_받으면_마지막_쪽까지_받은_차례에_정리한다(
    client: TestClient,
    admin: Signed,
    db: Session,
    sibling: FakeCore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(odata, "MAX_ROWS", 3)
    vendor, kind, source = _world(
        client, admin, sibling, [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4)], page_size=2
    )
    # 처음 받기도 두 차례다(쪽 경계 — 2줄씩, 상한 3).
    _sync(client, admin, source["slug"])
    assert _sync(client, admin, source["slug"])["counts"]["relations_create"] == 1
    assert len(_edges(db, kind)) == 5
    ids = _ids(client, admin, vendor)
    assert _link(client, admin, vendor, ids["V-004"], kind, ids["V-001"]).status_code == 201

    def stamps() -> dict[Any, tuple[Any, Any, Any]]:
        db.expire_all()
        return {
            edge.id: (edge.updated_at, edge.datasource_id, edge.datasource_seen_at)
            for edge in db.scalars(
                select(ObjectRelation).where(ObjectRelation.relation == kind)
            )
        }

    before = stamps()
    owner = db.scalars(select(DataSource.id).where(DataSource.slug == source["slug"])).one()
    # 주인은 처음 이은 쪽이다 — 이 소스가 이은 다섯 줄과 사람이 이은 한 줄.
    assert sorted(str(one[1]) for one in before.values()) == sorted(
        [str(owner)] * 5 + ["None"]
    )

    # 1 → 3 · 2 → 4 가 끊기고 셋이 새로 이어졌다 — 여섯 줄이라 다시 받기도 두 차례다.
    _reset(sibling, kind, [(1, 2), (1, 4), (2, 3), (3, 1), (3, 2), (4, 2)])
    first = _sync(client, admin, source["slug"])
    assert first["run"]["status"] == "ok", first["run"]
    assert any("여러 차례에 나눠" in one for one in first["run"]["errors"]), first["run"]
    # **아직 안 끊는다** — 남은 쪽에 그 선이 올 수 있다.
    assert {("V-001", "V-003"), ("V-002", "V-004")} <= _edges(db, kind)
    assert first["counts"].get("relations_unlink", 0) == 0
    # 「봤다」 는 따로 적는다 — 안 바뀐 선의 `updated_at` 을 밀면 그 선이 이 설치의 코어 창구로
    # 다시 나가고 지표가 전량을 센다(2026-10-08).
    after = stamps()
    assert all(after[one][0] == before[one][0] for one in before if one in after)
    assert any(after[one][2] is not None for one in before if one in after)
    saved = _saved(client, admin, source["slug"])
    assert saved["relations_since_mark"] != sibling.edges_as_of  # 다 받기 전에는 그대로
    assert not any(key.startswith("_") for key in saved["options"])  # 화면의 설정이 아니다
    row = db.scalars(select(DataSource).where(DataSource.slug == source["slug"])).one()
    db.refresh(row)
    assert row.options["_full_relations"]["started"], row.options

    sibling.edges_reset = False
    last = _sync(client, admin, source["slug"])
    assert last["run"]["status"] == "ok", last["run"]
    assert last["counts"]["relations_unlink"] == 2, last["counts"]
    assert _edges(db, kind) == {
        ("V-001", "V-002"),
        ("V-001", "V-004"),
        ("V-002", "V-003"),
        ("V-003", "V-001"),
        ("V-003", "V-002"),
        ("V-004", "V-002"),
        ("V-004", "V-001"),  # 사람이 이은 선은 그대로
    }
    db.refresh(row)
    assert "_full_relations" not in row.options  # 세대를 닫았다
    assert row.relations_since_mark  # 다 받았으니 시계가 옮겨졌다


def test_다시_받는_도중_실패하면_끊지_않고_다음_차례가_이어서_정리한다(
    client: TestClient,
    admin: Signed,
    db: Session,
    sibling: FakeCore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**일부만 본 것으로 끊으면 아직 안 받은 선이 끊긴다** — 선 창구가 멈추거나, 마지막 쪽의
    적용이 거절되면 그 차례는 아무것도 안 끊고 같은 자리에서 다시 잇는다."""
    monkeypatch.setattr(odata, "MAX_ROWS", 3)
    _vendor, kind, source = _world(
        client, admin, sibling, [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4)], page_size=2
    )
    _sync(client, admin, source["slug"])
    _sync(client, admin, source["slug"])
    _reset(sibling, kind, [(1, 2), (1, 4), (2, 3), (3, 1), (3, 2), (4, 2)])
    _sync(client, admin, source["slug"])  # 첫 차례 — 세대를 연다
    sibling.edges_reset = False

    # 1) 선 창구가 멈췄다.
    sibling.edges_status = 500
    broken = _sync(client, admin, source["slug"])
    assert broken["run"]["status"] == "failed", broken["run"]
    assert {("V-001", "V-003"), ("V-002", "V-004")} <= _edges(db, kind)

    # 2) 창구는 돌아왔는데 마지막 쪽에 이쪽이 모르는 칸이 실려 적용이 통째로 거절된다.
    sibling.edges_status = 200
    sibling.edges[-1]["properties"] = {"weight_kg": 3}
    refused = _sync(client, admin, source["slug"])
    assert refused["run"]["status"] == "failed", refused["run"]
    assert any("모르는 열" in one for one in refused["run"]["errors"]), refused["run"]
    assert {("V-001", "V-003"), ("V-002", "V-004")} <= _edges(db, kind)

    # 3) 고쳐지면 같은 자리에서 이어 받고, 끝난 그 차례에 정리한다.
    sibling.edges[-1].pop("properties")
    done = _sync(client, admin, source["slug"])
    assert done["run"]["status"] == "ok", done["run"]
    assert done["counts"]["relations_unlink"] == 2, done["counts"]
    assert not {("V-001", "V-003"), ("V-002", "V-004")} & _edges(db, kind)


def test_한꺼번에_절반_넘게_안_오면_끊지_않고_멈춘다(
    client: TestClient, admin: Signed, db: Session, sibling: FakeCore
) -> None:
    """상대의 토큰이 부서를 잃거나 공개를 잠시 닫으면 전량이 비어 온다 — 그것을 믿으면 이
    소스가 이은 선이 한꺼번에 끊긴다(객체의 사용 중지와 같은 문턱)."""
    every = [(a, b) for a in range(1, 5) for b in range(1, 5) if a != b]
    _vendor, kind, source = _world(client, admin, sibling, every)
    assert _sync(client, admin, source["slug"])["counts"]["relations_create"] == 12

    _reset(sibling, kind, [])
    held = _sync(client, admin, source["slug"])
    assert held["run"]["status"] == "failed", held["run"]
    assert held["counts"]["relations_unlink"] == 0, held["counts"]
    assert any("절반이 넘어 끊지 않았습니다" in one for one in held["run"]["errors"])
    assert len(_edges(db, kind)) == 12
    # 시계는 옮긴다 — 안 옮기면 다음 차례마다 같은 전량을 다시 받는다.
    assert _saved(client, admin, source["slug"])["relations_since_mark"] == sibling.edges_as_of
