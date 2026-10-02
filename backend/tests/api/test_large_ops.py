"""수만 건을 고치는 일 — **덩어리로 하고, 결과는 전과 같다**(ADR 0009 · 0010).

인기 모델은 기록 10만 건이 가리킨다. 그 모델을 합치거나(참조를 이긴 쪽으로) 참조를 비우고
지우면 가리키는 객체를 전부 고친다 — 예전에는 하나씩 실어 고치고 속성 전체를 이력에 두 번
담았다. 내보내기는 타입 전체를 한 번에 실었다. 여기서는 덩어리를 2건으로 줄여 여러 덩어리를
타게 한다.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.jobs import kinds
from app.modules.objects import rewrite
from tests.api.conftest import Signed, export_file, finish_job
from tests.api.test_ontology import _make_object, _make_property, _make_type


@pytest.fixture(autouse=True)
def _small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rewrite, "CHUNK", 2)
    monkeypatch.setattr(kinds, "EXPORT_CHUNK", 2)


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:6]
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    _make_property(
        client,
        admin,
        model,
        key="alt",
        label="대체 모델",
        data_type="object_ref",
        ref_type_slug=model,
    )
    loser = _make_object(client, admin, model, key=f"{tag}-OLD", label="옛 모델")
    winner = _make_object(
        client,
        admin,
        model,
        key=f"{tag}-NEW",
        label="새 모델",
        properties={"alt": loser["id"]},
    )
    case = _make_type(client, admin, label="시장 서비스", usage="log")
    _make_property(
        client,
        admin,
        case,
        key="model",
        label="모델",
        data_type="object_ref",
        ref_type_slug=model,
    )
    _make_property(
        client,
        admin,
        case,
        key="seen",
        label="관련 모델",
        data_type="object_ref",
        ref_type_slug=model,
        multi=True,
    )
    cases = [
        _make_object(client, admin, case, label=f"건 {n}", properties={"model": loser["id"]})[
            "id"
        ]
        for n in range(5)
    ]
    both = _make_object(
        client, admin, case, label="둘 다", properties={"seen": [loser["id"], winner["id"]]}
    )["id"]
    return {
        "tag": tag,
        "model": model,
        "case": case,
        "loser": loser["id"],
        "winner": winner["id"],
        "cases": cases,
        "both": both,
    }


def _props(client: TestClient, admin: Signed, slug: str, object_id: str) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}/{object_id}", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json()["object"]["properties"])


def test_합치면_가리키던_기록을_덩어리로_옮기고_이력은_바뀐_칸만(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    merged = client.post(
        f"/api/objects/{w['model']}/{w['loser']}/merge",
        json={"into": w["winner"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["property_refs"] == 7  # 건 다섯 · 둘 다 · 새 모델의 「대체 모델」

    assert {_props(client, admin, w["case"], one)["model"] for one in w["cases"]} == {
        w["winner"]
    }
    # 여러 값 칸은 자리만 바뀌고 겹치면 하나로.
    assert _props(client, admin, w["case"], w["both"])["seen"] == [w["winner"]]
    # 이긴 쪽이 진 쪽을 가리키던 칸 — 세션에 실린 옛 값으로 덮이지 않는다.
    assert _props(client, admin, w["model"], w["winner"])["alt"] == w["winner"]

    history = client.get(
        f"/api/objects/{w['case']}/{w['cases'][0]}/history", headers=admin.headers
    ).json()
    assert history[0]["changes"] == {
        "properties.model": {"before": w["loser"], "after": w["winner"]}
    }


def test_참조를_비우고_지우면_가리키던_칸을_덩어리로_비운다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    gone = client.delete(
        f"/api/objects/{w['model']}/{w['loser']}",
        params={"mode": "detach"},
        headers=admin.headers,
    )
    assert gone.status_code == 204, gone.text
    assert all("model" not in _props(client, admin, w["case"], one) for one in w["cases"])
    assert _props(client, admin, w["case"], w["both"])["seen"] == [w["winner"]]


def test_내보내기는_덩어리로_읽고_크면_zip_으로_낸다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kinds, "EXPORT_PLAIN_MAX", 3)
    w = _world(client, admin)
    got = export_file(client, admin, f"{w['case']}/export")
    assert got.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
        (name,) = archive.namelist()
        text = archive.read(name).decode("utf-8").lstrip("﻿")
    lines = text.splitlines()
    assert name.endswith(".csv") and len(lines) == 7  # 머리줄 + 6건
    assert lines[0].startswith("id,key,label")
    # 참조는 상대의 식별자로 — 다시 넣을 수 있게(건 다섯 · 여러 값 칸의 「둘 다」).
    assert sum(f"{w['tag']}-OLD" in one for one in lines) == 6

    as_json = export_file(client, admin, f"{w['case']}/export", params={"format": "json"})
    with zipfile.ZipFile(io.BytesIO(as_json.content)) as archive:
        (name,) = archive.namelist()
        rows = json.loads(archive.read(name))["rows"]
    assert len(rows) == 6 and {one["label"] for one in rows} >= {"건 0", "둘 다"}

    monkeypatch.setattr(kinds, "EXPORT_PLAIN_MAX", 100)
    plain = export_file(client, admin, f"{w['case']}/export", params={"format": "json"})
    assert len(plain.json()["rows"]) == 6


def test_가리키는_기록이_많으면_합치기와_지우기를_작업으로_한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """인기 모델(기록 10만 건)의 병합이 31초였다 — 요청 안에서 하면 클라이언트가 먼저 끊는다.
    문턱을 넘으면 서버가 작업 주소를 주고, 작업은 같은 함수를 부른다."""
    from app.modules.objects import lifecycle

    monkeypatch.setattr(lifecycle, "REWRITE_INLINE", 3)
    w = _world(client, admin)
    refused = client.post(
        f"/api/objects/{w['model']}/{w['loser']}/merge",
        json={"into": w["winner"]},
        headers=admin.headers,
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"].endswith(
        "OBJECTS-0096"
    )
    job_path = refused.json()["error"]["details"]["job_path"]
    assert job_path == f"/api/objects/{w['model']}/{w['loser']}/merge/job"
    made = client.post(job_path, json={"into": w["winner"]}, headers=admin.headers)
    assert made.status_code == 202, made.text
    done = finish_job(client, admin, made.json())
    assert done["status"] == "done", done
    assert done["result"]["op"] == "merge" and done["result"]["property_refs"] == 7
    assert {_props(client, admin, w["case"], one)["model"] for one in w["cases"]} == {
        w["winner"]
    }

    # 여럿을 지울 때 그 객체는 그 줄만 남기고 작업 주소를 적는다.
    other = _world(client, admin)
    planned = client.post(
        f"/api/objects/{other['model']}/bulk-delete",
        json={"ids": [other["loser"]], "mode": "detach", "apply": True},
        headers=admin.headers,
    ).json()
    row = planned["rows"][0]
    assert row["action"] == "error"
    assert row["job_path"] == f"/api/objects/{other['model']}/{other['loser']}/detach/job"
    made = client.post(row["job_path"], json={}, headers=admin.headers)
    done = finish_job(client, admin, made.json())
    assert done["status"] == "done" and done["result"]["op"] == "detach", done
    assert all(
        "model" not in _props(client, admin, other["case"], one) for one in other["cases"]
    )
