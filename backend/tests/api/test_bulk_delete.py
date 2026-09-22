"""여럿 골라 지우기 — **계획 먼저, 가리키는 것이 있으면 이유를 적고.**

여기서 지키는 것: 계획이 아무것도 안 지우나, 걸린 한 건 때문에 나머지가 취소되지 않나,
그리고 **한 건씩 지우는 길과 같은 규칙인가**(다르면 「목록에서 지운 것」 과 「상세에서
지운 것」 이 갈린다).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _delete(client: TestClient, who: Signed, slug: str, **body: Any) -> Any:
    return client.post(f"/api/objects/{slug}/bulk-delete", json=body, headers=who.headers)


def _three(client: TestClient, admin: Signed) -> tuple[str, list[str]]:
    part = _make_type(client, admin, label="부품")
    ids = [_make_object(client, admin, part, label=f"부품{i}")["id"] for i in range(3)]
    return part, ids


def _alive(client: TestClient, who: Signed, slug: str, object_id: str) -> bool:
    got = client.get(f"/api/objects/{slug}/{object_id}", headers=who.headers)
    return bool(got.status_code == 200)


def test_계획이_먼저고_아무것도_안_지운다(client: TestClient, admin: Signed) -> None:
    part, ids = _three(client, admin)
    planned = _delete(client, admin, part, ids=ids)
    assert planned.status_code == 200, planned.text
    body = planned.json()
    assert body["applied"] is False and body["counts"]["delete"] == 3
    assert all(_alive(client, admin, part, one) for one in ids)


def test_적용하면_지워진다(client: TestClient, admin: Signed) -> None:
    part, ids = _three(client, admin)
    done = _delete(client, admin, part, ids=ids, apply=True).json()
    assert done["applied"] is True and done["counts"]["delete"] == 3
    assert not any(_alive(client, admin, part, one) for one in ids)


def test_가리키는_것이_있으면_그_행만_거절하고_나머지는_지운다(
    client: TestClient, admin: Signed
) -> None:
    """**전부 아니면 무로 두지 않는다** — 스무 개 중 하나가 걸렸다고 열아홉을 못 지우면
    사람은 그 하나를 찾아 빼고 다시 고른다."""
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
    acme = _make_object(client, admin, vendor, label="ACME")["id"]
    free = _make_object(client, admin, vendor, label="여유")["id"]
    _make_object(client, admin, part, label="볼트", properties={"vendor": acme})

    done = _delete(client, admin, vendor, ids=[acme, free], apply=True).json()
    assert done["counts"]["delete"] == 1 and done["counts"]["error"] == 1
    blocked = next(one for one in done["rows"] if one["id"] == acme)
    assert "가리키는 것이 1개" in blocked["message"]
    assert _alive(client, admin, vendor, acme)
    assert not _alive(client, admin, vendor, free)

    # 「참조를 비우고 삭제」 로 부르면 지워지고, 가리키던 칸이 빈다.
    detached = _delete(client, admin, vendor, ids=[acme], mode="detach", apply=True).json()
    assert detached["counts"]["delete"] == 1
    assert not _alive(client, admin, vendor, acme)
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()
    assert not listed["items"][0]["properties"].get("vendor")


def test_못_지우는_것은_이유를_적는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    part, ids = _three(client, admin)
    denied = _delete(client, member, part, ids=ids).json()
    assert denied["counts"]["delete"] == 0 and denied["counts"]["error"] == 3
    assert all(_alive(client, admin, part, one) for one in ids)

    mixed = _delete(
        client, admin, part, ids=[*ids, "00000000-0000-0000-0000-000000000000"]
    ).json()
    assert mixed["counts"]["delete"] == 3 and mixed["counts"]["error"] == 1
    assert "찾을 수 없습니다" in mixed["rows"][-1]["message"]
