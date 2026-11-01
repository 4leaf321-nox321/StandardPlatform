"""지식 그래프의 전체 지도(ADR 0046) — **허브 칸 · 해시 자리 · 판 · 보는 사람 기준 · 확대
상한.**

시험 DB 는 스위트가 함께 쓴다 — 지도는 설치 전체의 것이라 다른 시험이 만든 객체도 판에 든다.
그래서 여기서는 **이 시험이 만든 것**의 자리 · 수만 본다. 배치의 반복 수는 낮춘다(자리의 질이
아니라 약속을 본다).
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.database import SessionLocal
from app.modules.graph import mapbuild, maphash, maplines, mapread
from app.modules.graph.models import MapCounted, MapLayout
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from tests.api.conftest import Signed, finish_job
from tests.api.test_graph import _hidden_workspace
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
)


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mapbuild, "FIRST_ITERS", 30)
    monkeypatch.setattr(mapbuild, "RELAYOUT_ITERS", 15)
    monkeypatch.setattr(mapbuild, "APPEND_ITERS", 10)
    mapread.forget()


def _relayout(client: TestClient, admin: Signed) -> dict[str, Any]:
    made = client.post("/api/graph/map/relayout", headers=admin.headers)
    assert made.status_code == 202, made.text
    done = finish_job(client, admin, made.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _run(kind: str) -> Job:
    """타이머가 넣는 것처럼(시킨 사람 없이) 넣고 이 프로세스에서 돌린다."""
    with SessionLocal() as db:
        job = job_services.enqueue(db, kind=kind, params={}, user=None, workspace_id=None)
        db.commit()
        job_id = job.id
    for _ in range(50):
        with SessionLocal() as db:
            status = db.scalar(select(Job.status).where(Job.id == job_id))
        if status not in ("queued", "running"):
            break
        if not job_services.process_one("test-worker"):
            break
    with SessionLocal() as db:
        row = db.get(Job, job_id)
        assert row is not None
        db.expunge(row)
        return row


def _map(client: TestClient, who: Signed) -> Any:
    got = client.get("/api/graph/map", headers=who.headers)
    assert got.status_code == 200, got.text
    return got.json()


def _points(client: TestClient, who: Signed, box: tuple[float, ...], **params: Any) -> Any:
    x0, y0, x1, y1 = box
    got = client.get(
        "/api/graph/map/points",
        params={"x0": x0, "y0": y0, "x1": x1, "y1": y1, **params},
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return got.json()


def _disc_of(body: Any, hub_id: str) -> dict[str, Any] | None:
    """허브의 원반 — 자리 · 반지름 · 타입별 수."""
    ids = body["masters"]["ids"]
    if hub_id not in ids:
        return None
    at = ids.index(hub_id)
    discs = body["discs"]
    if at not in discs["hub"]:
        return None
    k = discs["hub"].index(at)
    counts: dict[str, int] = {}
    for d, t, n in zip(
        discs["count_disc"], discs["count_type"], discs["count_n"], strict=True
    ):
        if d == k:
            counts[body["types"][t]["slug"]] = n
    return {"x": discs["x"][k], "y": discs["y"][k], "r": discs["r"][k], "counts": counts}


def _slot_of(body: Any, type_slug: str) -> dict[str, Any] | None:
    slugs = [one["slug"] for one in body["types"]]
    if type_slug not in slugs:
        return None
    at = slugs.index(type_slug)
    discs = body["discs"]
    if at not in discs["slot"]:
        return None
    k = discs["slot"].index(at)
    n = sum(
        c
        for d, t, c in zip(
            discs["count_disc"], discs["count_type"], discs["count_n"], strict=True
        )
        if d == k and t == at
    )
    return {"x": discs["x"][k], "y": discs["y"][k], "r": discs["r"][k], "n": n}


def _world(client: TestClient, admin: Signed, *, records: int = 4) -> dict[str, Any]:
    """기준정보 둘(모델 · 부품)과 기록(필수 모델 칸 + 부품 칸)."""
    model = _make_type(client, admin, label="모델")
    part = _make_type(client, admin, label="부품")
    case = _make_type(client, admin, label="서비스", usage="log")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
        required=True,
    )
    _make_property(
        client,
        admin,
        case,
        key="part",
        label="부품",
        data_type="object_ref",
        ref_type_slug=part,
    )
    hub = _make_object(client, admin, model, label="허브 모델", workspace_slug=None)
    bolt = _make_object(client, admin, part, label="볼트", workspace_slug=None)
    made = [
        _make_object(
            client,
            admin,
            case,
            label=f"건{n}",
            properties={"model": hub["id"], "part": bolt["id"]},
        )
        for n in range(records)
    ]
    return {
        "model": model,
        "part": part,
        "case": case,
        "hub": hub,
        "bolt": bolt,
        "records": made,
    }


# --- 허브 칸 --------------------------------------------------------------------------


def test_자동_허브_칸은_필수_값하나_기준정보_중_허브가_많은_칸이고_관리자가_바꾼다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    few = _make_type(client, admin, label="국가")
    many = _make_type(client, admin, label="판매 모델")
    log = _make_type(client, admin, label="기록", usage="log")
    other_log = _make_type(client, admin, label="다른 기록", usage="log")
    for n in range(2):
        _make_object(client, admin, few, label=f"국가{n}", workspace_slug=None)
    for n in range(5):
        _make_object(client, admin, many, label=f"모델{n}", workspace_slug=None)
    _make_property(
        client,
        admin,
        log,
        key="country",
        label="국가",
        data_type="object_ref",
        ref_type_slug=few,
        required=True,
    )
    _make_property(
        client,
        admin,
        log,
        key="sku",
        label="모델",
        data_type="object_ref",
        ref_type_slug=many,
        required=True,
    )
    # 필수가 아닌 칸 · 여러 값 · 기록을 가리키는 칸은 자동이 안 고른다.
    _make_property(
        client,
        admin,
        log,
        key="maybe",
        label="아마",
        data_type="object_ref",
        ref_type_slug=many,
    )
    _make_property(
        client,
        admin,
        log,
        key="tags",
        label="여럿",
        data_type="object_ref",
        ref_type_slug=many,
        multi=True,
        required=True,
    )
    _make_property(
        client,
        admin,
        log,
        key="prev",
        label="앞 기록",
        data_type="object_ref",
        ref_type_slug=other_log,
        required=True,
    )

    def mine() -> dict[str, Any]:
        rows = client.get("/api/graph/map/hub-keys", headers=member.headers).json()
        return next(one for one in rows if one["type_slug"] == log)

    got = mine()
    # 필수 칸이 먼저 — 필수 아닌 「아마」 가 같은 타입을 가리켜도 「모델」 이다.
    assert (got["mode"], got["key"], got["auto_key"]) == ("auto", "sku", "sku")
    assert {one["key"] for one in got["candidates"]} == {"country", "sku", "maybe"}

    # 관리자가 바꾼다 — 필수가 아니어도 값 하나 · 기준정보면 된다.
    put = client.put(
        f"/api/graph/map/hub-keys/{log}",
        json={"mode": "key", "key": "maybe"},
        headers=admin.headers,
    )
    assert put.status_code == 200, put.text
    assert (put.json()["mode"], put.json()["key"]) == ("admin", "maybe")
    assert (
        client.put(
            f"/api/graph/map/hub-keys/{log}",
            json={"mode": "key", "key": "tags"},
            headers=admin.headers,
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/graph/map/hub-keys/{log}", json={"mode": "none"}, headers=member.headers
        ).status_code
        == 403
    )
    none = client.put(
        f"/api/graph/map/hub-keys/{log}", json={"mode": "none"}, headers=admin.headers
    )
    assert (none.json()["mode"], none.json()["key"]) == ("none", None)

    # 필수 칸이 하나도 없으면 필수 아닌 칸 중에서 고른다(빈 기록은 미지정 자리로).
    loose = _make_type(client, admin, label="느슨한 기록", usage="log")
    _make_property(
        client,
        admin,
        loose,
        key="where",
        label="국가",
        data_type="object_ref",
        ref_type_slug=few,
    )
    _make_property(
        client,
        admin,
        loose,
        key="what",
        label="모델",
        data_type="object_ref",
        ref_type_slug=many,
    )
    rows = client.get("/api/graph/map/hub-keys", headers=member.headers).json()
    assert next(one for one in rows if one["type_slug"] == loose)["auto_key"] == "what"

    # 설정은 다음 전체 배치에 반영된다 — 그 전에는 판과 다르다고 말한다.
    _relayout(client, admin)
    assert log not in _map(client, admin)["drift"]
    back = client.put(
        f"/api/graph/map/hub-keys/{log}", json={"mode": "auto"}, headers=admin.headers
    )
    assert (back.json()["mode"], back.json()["key"], back.json()["used_key"]) == (
        "auto",
        "sku",
        None,
    )
    assert log in _map(client, admin)["drift"]


# --- 해시 자리 ---------------------------------------------------------------------------


def test_기록의_자리는_id_해시로_정해지고_SQL_과_같다(
    client: TestClient, admin: Signed
) -> None:
    ids = [uuid.uuid4() for _ in range(20)]
    with SessionLocal() as db:
        got = db.execute(
            text("SELECT id, sp_map_hash(id) FROM unnest(CAST(:ids AS uuid[])) AS t(id)"),
            {"ids": ids},
        ).all()
    assert {one: h for one, h in got} == {one: maphash.hash_of(one) for one in ids}
    for one in ids:
        x, y = maphash.place(maphash.hash_of(one), 10.0, -5.0, 2.0)
        assert math.hypot(x - 10.0, y + 5.0) <= 2.0 + 1e-9
        assert maphash.place(maphash.hash_of(one), 10.0, -5.0, 2.0) == (x, y)

    w = _world(client, admin, records=6)
    _relayout(client, admin)
    body = _map(client, admin)
    disc = _disc_of(body, w["hub"]["id"])
    assert disc is not None and disc["counts"] == {w["case"]: 6}
    r = disc["r"]
    pts = _points(
        client,
        admin,
        (disc["x"] - r, disc["y"] - r, disc["x"] + r, disc["y"] + r),
        types=w["case"],
    )
    assert pts["over"] is False
    mine = {one["id"] for one in w["records"]}
    placed = {i: (x, y) for i, x, y in zip(pts["ids"], pts["x"], pts["y"], strict=True)}
    assert mine <= set(placed)
    for one in mine:
        want = maphash.place(maphash.hash_of(one), disc["x"], disc["y"], r)
        assert math.dist(placed[one], want) < 1e-3
    # 같은 판 안에서는 늘 같은 자리 — 다시 물어도.
    again = _points(
        client,
        admin,
        (disc["x"] - r, disc["y"] - r, disc["x"] + r, disc["y"] + r),
        types=w["case"],
    )
    assert {i: (x, y) for i, x, y in zip(again["ids"], again["x"], again["y"], strict=True)}[
        w["records"][0]["id"]
    ] == placed[w["records"][0]["id"]]
    # 객체 하나의 자리(주소의 at)도 같은 자리다.
    loc = client.get(
        "/api/graph/map/locate", params={"id": w["records"][0]["id"]}, headers=admin.headers
    ).json()
    assert loc["kind"] == "record"
    assert math.dist((loc["x"], loc["y"]), placed[w["records"][0]["id"]]) < 1e-3
    hub_loc = client.get(
        "/api/graph/map/locate", params={"id": w["hub"]["id"]}, headers=admin.headers
    ).json()
    assert hub_loc["kind"] == "master"
    assert (hub_loc["x"], hub_loc["y"]) == pytest.approx((disc["x"], disc["y"]), abs=1e-3)


# --- 판 ---------------------------------------------------------------------------------


def test_매일_덧붙임은_기존_자리를_움직이지_않고_새_기준정보만_놓는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    _relayout(client, admin)
    before = _map(client, admin)
    old = dict(
        zip(
            before["masters"]["ids"],
            zip(before["masters"]["x"], before["masters"]["y"], strict=True),
            strict=True,
        )
    )
    fresh = _make_object(client, admin, w["model"], label="새 모델", workspace_slug=None)
    _make_object(
        client,
        admin,
        w["case"],
        label="새 건",
        properties={"model": fresh["id"], "part": w["bolt"]["id"]},
    )
    # 판 밖 기준정보를 가리키는 기록은 덧붙임 전까지 자리가 없다 — 수만 말한다.
    waiting = _map(client, admin)
    assert fresh["id"] not in waiting["masters"]["ids"]
    job = _run("graph_map_append")
    assert job.status == "done", job.error
    assert job.result is not None and job.result["kind"] == "append" and job.result["new"] >= 1
    after = _map(client, admin)
    assert after["layout"]["kind"] == "append"
    assert after["layout"]["version"] > before["layout"]["version"]
    assert after["layout"]["full_version"] == before["layout"]["full_version"]
    now = dict(
        zip(
            after["masters"]["ids"],
            zip(after["masters"]["x"], after["masters"]["y"], strict=True),
            strict=True,
        )
    )
    moved = [one for one in old if one in now and now[one] != old[one]]
    assert moved == []
    assert fresh["id"] in now
    disc = _disc_of(after, fresh["id"])
    assert disc is not None and disc["counts"] == {w["case"]: 1}


def test_전체_다시_배치는_판을_올리고_앞_자리에서_출발한다(
    client: TestClient, admin: Signed
) -> None:
    _world(client, admin)
    first = _relayout(client, admin)
    second = _relayout(client, admin)
    assert second["layout"] > first["layout"]
    assert second["kind"] == "full"
    # 앞 판이 있으면 그 자리에서 출발하고 얼마나 움직였는지 적는다.
    assert set(second["moved"]) == {"p50", "p95", "max"}
    body = _map(client, admin)
    assert body["layout"]["full_version"] == second["layout"]
    assert body["layout"]["by_timer"] is False


# --- 보는 사람 기준 ------------------------------------------------------------------------


@pytest.mark.usefixtures("department_scope")
def test_원반_크기와_수는_보는_사람이_볼_수_있는_부서의_것만(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    hidden = _hidden_workspace(client, admin)
    model = _make_type(client, admin, label="모델")
    part = _make_type(client, admin, label="부품")
    case = _make_type(client, admin, label="서비스", usage="log")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
        required=True,
    )
    _make_property(
        client,
        admin,
        case,
        key="part",
        label="부품",
        data_type="object_ref",
        ref_type_slug=part,
    )
    hub = _make_object(client, admin, model, label="함께 쓰는 모델", workspace_slug=None)
    bolt = _make_object(client, admin, part, label="볼트", workspace_slug=None)
    both = {"model": hub["id"], "part": bolt["id"]}
    mine = [
        _make_object(client, manager, case, label=f"우리{n}", properties=both)
        for n in range(3)
    ]
    for n in range(47):
        _make_object(
            client, admin, case, label=f"남의{n}", properties=both, workspace_slug=hidden
        )
    _relayout(client, admin)
    scale = _map(client, admin)["scale"]

    everything = _disc_of(_map(client, admin), hub["id"])
    assert everything is not None and everything["counts"] == {case: 50}
    assert everything["r"] == pytest.approx(scale * math.sqrt(50), rel=1e-3)

    body = _map(client, member)
    seen = _disc_of(body, hub["id"])
    assert seen is not None and seen["counts"] == {case: 3}
    assert seen["r"] == pytest.approx(scale * math.sqrt(3), rel=1e-3)
    assert next(one for one in body["types"] if one["slug"] == case)["count"] == 3
    # 응답 어디에도 남의 부서 수(47 · 50)가 원반의 수로 실리지 않는다.
    assert 47 not in body["discs"]["count_n"] and 50 not in body["discs"]["count_n"]
    r = seen["r"]
    pts = _points(
        client,
        member,
        (seen["x"] - r, seen["y"] - r, seen["x"] + r, seen["y"] + r),
        types=case,
    )
    assert set(pts["ids"]) == {one["id"] for one in mine}
    assert pts["expected"] <= 4
    # 공동 참조 선도 — 부서로 좁힌 설치는 판을 만들 때 부서마다 따로 센다.
    links = client.get(
        "/api/graph/map/links", params={"hub": hub["id"]}, headers=member.headers
    ).json()
    assert links["withheld"] is False
    assert [(one["id"], one["count"]) for one in links["items"]] == [(bolt["id"], 3)]
    every = client.get(
        "/api/graph/map/links", params={"hub": hub["id"]}, headers=admin.headers
    ).json()
    assert [(one["id"], one["count"]) for one in every["items"]] == [(bolt["id"], 50)]


@pytest.mark.usefixtures("department_scope")
def test_안_보이는_기준정보는_지도에_없고_그_기록은_미지정_자리로(
    client: TestClient, admin: Signed, manager: Signed, member: Signed
) -> None:
    hidden = _hidden_workspace(client, admin)
    model = _make_type(client, admin, label="모델")
    case = _make_type(client, admin, label="서비스", usage="log")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
        required=True,
    )
    secret = _make_object(client, admin, model, label="남의 모델", workspace_slug=hidden)
    # 우리 부서 기록이 남의 부서 모델을 가리킨다(부서를 옮긴 모델 — 시스템 관리자가 넣었다).
    mine = [
        _make_object(
            client,
            admin,
            case,
            label=f"우리{n}",
            properties={"model": secret["id"]},
            workspace_slug=manager.workspace,
        )
        for n in range(2)
    ]
    _relayout(client, admin)
    assert _disc_of(_map(client, admin), secret["id"]) is not None

    body = _map(client, member)
    assert secret["id"] not in body["masters"]["ids"]
    assert "남의 모델" not in body["masters"]["label"]
    slot = _slot_of(body, case)
    assert slot is not None and slot["n"] == 2
    r = slot["r"]
    pts = _points(
        client,
        member,
        (slot["x"] - r, slot["y"] - r, slot["x"] + r, slot["y"] + r),
        types=case,
    )
    assert set(pts["ids"]) == {one["id"] for one in mine}
    loc = client.get(
        "/api/graph/map/locate", params={"id": secret["id"]}, headers=member.headers
    ).json()
    assert loc["kind"] == "none"


# --- 확대 -------------------------------------------------------------------------------


def test_상자의_기대_수가_상한을_넘으면_기록을_읽지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(client, admin, records=12)
    _relayout(client, admin)
    disc = _disc_of(_map(client, admin), w["hub"]["id"])
    assert disc is not None
    r = disc["r"]
    box = (disc["x"] - r, disc["y"] - r, disc["x"] + r, disc["y"] + r)
    monkeypatch.setattr(mapread, "MAX_POINTS", 5)
    over = _points(client, admin, box, types=w["case"])
    assert over["over"] is True and over["ids"] == [] and over["expected"] >= 12
    monkeypatch.setattr(mapread, "MAX_POINTS", 50_000)
    fine = _points(client, admin, box, types=w["case"])
    assert fine["over"] is False and fine["count"] >= 12
    assert fine["labels"] is not None  # 5천 점 이하면 이름도
    # 기록 타입을 끄면 그 점은 안 온다.
    assert w["records"][0]["id"] not in _points(client, admin, box, types=w["model"])["ids"]
    # 허브를 고르면 주 참조 밖의 연결(공동 참조)을 준다.
    links = client.get(
        "/api/graph/map/links", params={"hub": w["hub"]["id"]}, headers=admin.headers
    ).json()
    assert {one["id"]: one["count"] for one in links["items"]}.get(w["bolt"]["id"]) == 12


# --- 선 ---------------------------------------------------------------------------------


def _line_pairs(body: Any) -> set[tuple[str, str, str]]:
    """멀리 보기의 기준정보끼리 선 — (출발 id, 도착 id, 종류 slug)."""
    ids = body["masters"]["ids"]
    kinds = [one["slug"] for one in body["relations"]]
    lines = body["lines"]
    return {
        (ids[a], ids[b], kinds[k])
        for a, b, k in zip(lines["src"], lines["dst"], lines["rel"], strict=True)
    }


def _point_pairs(pts: Any) -> set[tuple[str, str, str]]:
    """확대한 점의 선 — (출발 id, 도착 id, 종류 slug). 끝 번호가 점 수 이상이면 기준정보."""
    lines = pts["lines"]
    n = pts["count"]

    def end(k: int) -> str:
        return str(pts["ids"][k] if k < n else lines["masters"][k - n])

    return {
        (end(a), end(b), lines["kinds"][k])
        for a, b, k in zip(lines["src"], lines["dst"], lines["rel"], strict=True)
    }


@pytest.mark.usefixtures("department_scope")
def test_기준정보끼리의_선은_참조_칸과_관계이고_양_끝이_보이는_것만(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    hidden = _hidden_workspace(client, admin)
    w = _world(client, admin)
    _make_property(
        client,
        admin,
        w["part"],
        key="fits",
        label="맞는 모델",
        data_type="object_ref",
        ref_type_slug=w["model"],
    )
    hub = w["hub"]["id"]
    nut = _make_object(
        client, admin, w["part"], label="너트", properties={"fits": hub}, workspace_slug=None
    )
    secret = _make_object(
        client,
        admin,
        w["part"],
        label="남의 부품",
        properties={"fits": hub},
        workspace_slug=hidden,
    )
    kind = _make_relation(client, admin, "pairs_with", label="짝")
    assert _link(client, admin, w["part"], w["bolt"]["id"], kind, nut["id"]).status_code == 201
    # 우리 부서 기록이 남의 부서 부품을 가리킨다 — 확대한 점의 선에도 그 부품은 없다.
    ours = _make_object(
        client,
        admin,
        w["case"],
        label="우리 건",
        properties={"model": hub, "part": secret["id"]},
        workspace_slug=member.workspace,
    )
    _relayout(client, admin)
    fits = f"ref:{w['part']}.fits"
    every = _map(client, admin)
    pairs = _line_pairs(every)
    assert {(nut["id"], hub, fits), (secret["id"], hub, fits)} <= pairs
    assert (w["bolt"]["id"], nut["id"], kind) in pairs
    table = {one["slug"]: one for one in every["relations"]}
    assert table[fits]["label"] == "맞는 모델" and table[fits]["count"] >= 2
    # 기록의 참조 칸도 종류 표에 있다 — 확대한 점의 선이 이 표의 색을 쓴다.
    assert f"ref:{w['case']}.model" in table

    body = _map(client, member)
    seen = _line_pairs(body)
    assert (nut["id"], hub, fits) in seen
    assert not any(secret["id"] in (a, b) for a, b, _ in seen)
    disc = _disc_of(body, hub)
    assert disc is not None
    r = disc["r"]
    pts = _points(
        client,
        member,
        (disc["x"] - r, disc["y"] - r, disc["x"] + r, disc["y"] + r),
        types=w["case"],
    )
    assert ours["id"] in pts["ids"]
    lines = _point_pairs(pts)
    assert (ours["id"], hub, f"ref:{w['case']}.model") in lines
    assert secret["id"] not in pts["lines"]["masters"]


def test_확대한_점의_선은_문턱_이하일_때_다른_끝도_그림에_있는_것만(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(client, admin, records=3)
    kind = _make_relation(client, admin, "follows", label="뒤따름")
    first, second, _third = w["records"]
    assert _link(client, admin, w["case"], second["id"], kind, first["id"]).status_code == 201
    _relayout(client, admin)
    disc = _disc_of(_map(client, admin), w["hub"]["id"])
    assert disc is not None
    r = disc["r"]
    box = (disc["x"] - r, disc["y"] - r, disc["x"] + r, disc["y"] + r)
    pts = _points(client, admin, box, types=w["case"])
    assert pts["lines"] is not None and pts["lines_up_to"] == maplines.LINES_UP_TO
    got = _point_pairs(pts)
    model, part = f"ref:{w['case']}.model", f"ref:{w['case']}.part"
    for one in w["records"]:
        assert {(one["id"], w["hub"]["id"], model), (one["id"], w["bolt"]["id"], part)} <= got
    assert (second["id"], first["id"], kind) in got
    # 문턱을 넘으면 점만 — 선은 비고, 화면이 「더 확대하면」 을 말할 문턱을 준다.
    monkeypatch.setattr(maplines, "LINES_UP_TO", 1)
    few = _points(client, admin, box, types=w["case"])
    assert few["count"] >= 3 and few["lines"] is None
    assert few["lines_up_to"] == 1 and few["lines_over"] is False


def test_주_참조가_빈_기록은_타입별_미지정_자리에(client: TestClient, admin: Signed) -> None:
    model = _make_type(client, admin, label="모델")
    case = _make_type(client, admin, label="서비스", usage="log")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
    )
    hub = _make_object(client, admin, model, label="허브", workspace_slug=None)
    assert (
        client.put(
            f"/api/graph/map/hub-keys/{case}",
            json={"mode": "key", "key": "model"},
            headers=admin.headers,
        ).status_code
        == 200
    )
    with_hub = _make_object(client, admin, case, label="있음", properties={"model": hub["id"]})
    empty = [_make_object(client, admin, case, label=f"빈{n}") for n in range(3)]
    _relayout(client, admin)
    body = _map(client, admin)
    assert next(one for one in body["types"] if one["slug"] == case)["hub_key"] == "model"
    slot = _slot_of(body, case)
    assert slot is not None and slot["n"] == 3
    r = slot["r"]
    pts = _points(
        client, admin, (slot["x"] - r, slot["y"] - r, slot["x"] + r, slot["y"] + r), types=case
    )
    assert {one["id"] for one in empty} <= set(pts["ids"])
    assert with_hub["id"] not in pts["ids"]


# --- 작업 ---------------------------------------------------------------------------------


def test_지도_작업은_겹쳐_돌지_않는다(client: TestClient, admin: Signed) -> None:
    _world(client, admin)
    _relayout(client, admin)
    # 집계는 다른 지도 작업이 잠금을 쥐고 있으면 건너뛴다.
    with SessionLocal() as holder:
        assert mapbuild.lock(holder, wait=False) is True
        skipped = _run("graph_map_count")
        assert skipped.status == "done" and "skipped" in (skipped.result or {})
        holder.rollback()
    counted = _run("graph_map_count")
    assert "skipped" not in (counted.result or {})
    # 전체 다시 배치를 두 번 눌러도 작업은 하나다.
    first = client.post("/api/graph/map/relayout", headers=admin.headers).json()
    second = client.post("/api/graph/map/relayout", headers=admin.headers).json()
    assert first["id"] == second["id"]
    # 지도 작업이 기다리는 동안 타이머는 아무것도 안 넣는다.
    with SessionLocal() as db:
        assert mapbuild.schedule(db) is None
    finish_job(client, admin, first)


def test_타이머는_집계와_덧붙임의_차례를_DB_시각으로_본다(
    client: TestClient, admin: Signed
) -> None:
    from datetime import UTC, datetime, timedelta

    _world(client, admin)
    _relayout(client, admin)
    with SessionLocal() as db:
        assert mapbuild.schedule(db) is None  # 방금 판 · 방금 집계
        db.execute(
            update(MapCounted).values(counted_at=datetime.now(UTC) - timedelta(hours=2))
        )
        db.commit()
        assert mapbuild.schedule(db) == "graph_map_count"
        db.execute(
            update(Job)
            .where(Job.kind.in_(mapbuild.KINDS), Job.status == "queued")
            .values(status="cancelled")
        )
        layout = mapbuild.current(db)
        assert layout is not None
        db.execute(
            update(MapLayout)
            .where(MapLayout.id == layout.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=31))
        )
        db.commit()
        assert mapbuild.schedule(db) == "graph_map_append"
        db.execute(
            update(Job)
            .where(Job.kind.in_(mapbuild.KINDS), Job.status == "queued")
            .values(status="cancelled")
        )
        db.execute(
            update(MapLayout)
            .where(MapLayout.id == layout.id)
            .values(created_at=datetime.now(UTC))
        )
        db.commit()


def test_기준정보가_상한을_넘으면_판을_만들지_않고_까닭을_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _world(client, admin)
    monkeypatch.setattr(mapbuild, "MAX_MASTERS", 1)
    made = client.post("/api/graph/map/relayout", headers=admin.headers)
    done = finish_job(client, admin, made.json())
    assert done["status"] == "failed" and "상한" in (done["error"] or "")
    assert "상한" in (_map(client, admin)["last_error"] or "")


def test_멀리_보기는_ETag_로_같은_판이면_304(client: TestClient, admin: Signed) -> None:
    _world(client, admin)
    _relayout(client, admin)
    first = client.get("/api/graph/map", headers=admin.headers)
    tag = first.headers.get("etag")
    assert tag
    again = client.get("/api/graph/map", headers={**admin.headers, "If-None-Match": tag})
    assert again.status_code == 304
    assert client.post("/api/graph/map/relayout", headers=admin.headers).status_code == 202
    # 지도 작업이 기다리면 ETag 가 바뀐다(화면이 「배치 중」 을 말한다).
    changed = client.get("/api/graph/map", headers={**admin.headers, "If-None-Match": tag})
    assert changed.status_code == 200 and changed.json()["job"] is not None
    with SessionLocal() as db:
        pending = db.scalar(
            select(Job).where(Job.kind == "graph_map_full", Job.status == "queued").limit(1)
        )
        assert pending is not None
    finish_job(client, admin, {"id": str(pending.id)})


def test_새_기록은_다음_집계부터_점으로_보이고_지운_기록은_바로_빠진다(
    client: TestClient, admin: Signed
) -> None:
    """확대 점 표는 시간마다의 집계가 맞춘다(쓰기 길에 아무것도 안 붙인다 — ADR 0046
    「형태」)."""
    w = _world(client, admin, records=3)
    _relayout(client, admin)
    disc = _disc_of(_map(client, admin), w["hub"]["id"])
    assert disc is not None
    r = disc["r"]
    box = (disc["x"] - r, disc["y"] - r, disc["x"] + r, disc["y"] + r)
    fresh = _make_object(
        client,
        admin,
        w["case"],
        label="막 넣은 건",
        properties={"model": w["hub"]["id"], "part": w["bolt"]["id"]},
    )
    assert fresh["id"] not in _points(client, admin, box, types=w["case"])["ids"]
    counted = _run("graph_map_count")
    assert counted.status == "done", counted.error
    assert (counted.result or {}).get("points") == "changed"
    assert fresh["id"] in _points(client, admin, box, types=w["case"])["ids"]
    gone = w["records"][0]["id"]
    assert client.delete(
        f"/api/objects/{w['case']}/{gone}", headers=admin.headers
    ).status_code in (
        200,
        204,
    )
    assert gone not in _points(client, admin, box, types=w["case"])["ids"]
