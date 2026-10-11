"""작업으로 한 일의 **바깥 알림** — 웹훅은 작업마다 요약 한 건, 지켜보기는 객체마다.

예전에는 워커에 듣는 쪽이 없어 작업으로 한 일(일괄 입력 · 묶음 · 사진 · 부서 자료 이동)은
웹훅도 지켜보기 알림도 하나도 안 갔다. 줄마다 보내면 일괄 입력 10만 줄이 웹훅 10만 건이라,
웹훅은 작업이 끝난 뒤 「무엇이 몇 건」 한 건으로 받는다(2026-10-10).
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from tests.api.conftest import Signed, import_file, notifications_of
from tests.api.test_ontology import _make_object, _make_type
from tests.api.test_watches import _watch
from tests.api.test_webhooks import _hook, inbox

__all__ = ["inbox"]  # 받는 쪽 흉내(픽스처)를 이 파일에서도 쓴다


def test_일괄_입력은_웹훅에_요약_한_건만_보낸다(
    client: TestClient, admin: Signed, inbox: list[httpx.Request]
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    other = _make_type(client, admin, label="공급사", key_policy="required")
    _hook(client, admin, type_slugs=[part])
    inbox.clear()

    rows = "key,label\nP-1,볼트\nP-2,너트\nP-3,와셔류\n"
    plan = import_file(client, admin, part, rows)
    assert plan["counts"]["create"] == 3
    assert inbox == [], "계획은 아무것도 안 바꾼다 — 알릴 것이 없다"

    done = import_file(client, admin, part, rows, apply=True)
    assert done["applied"] is True
    assert len(inbox) == 1, [json.loads(one.content)["event"] for one in inbox]
    body = json.loads(inbox[0].content)
    assert body["event"] == "object.import"
    assert body["target"]["type_slug"] == part
    assert body["changes"]["events"]["object.create"] == 3
    assert body["changes"]["job_kind"] == "objects_import"
    assert body["changes"]["job_status"] == "done"
    assert body["request_id"] == f"job:{body['changes']['job_id']}"

    # 타입을 정한 웹훅은 다른 타입의 일괄 입력 요약을 안 받는다.
    inbox.clear()
    import_file(client, admin, other, "key,label\nV-1,ACME\n", apply=True)
    assert inbox == []


def test_작업으로_고쳐도_지켜보는_사람에게_객체마다_알린다(
    client: TestClient, admin: Signed, manager: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="required")
    bolt = _make_object(client, admin, part, label="볼트", key="P-1")
    _make_object(client, admin, part, label="너트", key="P-2")
    assert _watch(client, manager, part, bolt["id"], True).json()["watching"] is True
    before = len(notifications_of(client, manager, "object.changed"))

    done = import_file(
        client, admin, part, "key,label\nP-1,육각 볼트\nP-2,육각 너트\n", apply=True
    )
    assert done["applied"] is True and done["counts"]["update"] == 2

    after = notifications_of(client, manager, "object.changed")
    # 지켜보는 볼트 하나만 — 너트는 아무도 안 지켜본다.
    assert len(after) == before + 1
    assert after[0]["link"] == f"/o/{part}/{bolt['id']}"
