"""옮긴 객체의 옛 주소 — 옮긴 **뒤에** 타입의 slug 를 바꿔도 지금 자리를 안내한다(ADR 0094 ·
0093), 원 표를 비추는 타입의 거절 문장.

옮기기 기록은 그때의 slug(「원 → 대상」)로 적혀 있다. 그 글자로만 잇던 때는 옮긴 뒤 대상의 slug
를 바꾸면 옛 주소가 안내 없이 404 로 끝났다(2026-11-27 점검).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_move import _made, _move
from tests.api.test_ontology import _make_property, _make_type


def _hex() -> str:
    return uuid.uuid4().hex[:8]


def _rename(client: TestClient, admin: Signed, slug: str, to: str) -> None:
    done = client.post(
        f"/api/ontology/types/{slug}/rename",
        json={"to": to, "apply": True},
        headers=admin.headers,
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text


def _old(client: TestClient, admin: Signed, slug: str, oid: str) -> dict[str, object]:
    got = client.get(f"/api/objects/{slug}/{oid}", headers=admin.headers)
    assert got.status_code == 404, got.text
    assert got.json()["error"]["code"] == code("OBJECTS", 160), got.text
    return dict(got.json()["error"]["details"])


def test_옮긴_뒤_대상_slug_를_바꿔도_옛_주소가_지금_자리를_안내한다(
    client: TestClient, admin: Signed
) -> None:
    first = _make_type(client, admin, label=f"처음 {_hex()}", key_policy="optional")
    _make_property(client, admin, first, key="memo", label="메모", data_type="text")
    second = _make_type(client, admin, label=f"둘째 {_hex()}", key_policy="optional")
    _make_property(client, admin, second, key="memo", label="메모", data_type="text")
    made = _made(client, admin, first, label="에이", properties={"memo": "가"})
    done = _move(client, admin, first, second, apply=True)
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    assert _old(client, admin, first, made["id"]) == {
        "moved_to": second,
        "object_id": made["id"],
    }

    renamed = f"renamed_{_hex()}"
    _rename(client, admin, second, renamed)
    assert _old(client, admin, first, made["id"]) == {
        "moved_to": renamed,
        "object_id": made["id"],
    }

    # 원 타입의 slug 를 바꿔도 — 옛 slug 의 주소는 화면이 새 slug 로 옮겨 연다(ADR 0093).
    origin = f"origin_{_hex()}"
    _rename(client, admin, first, origin)
    assert _old(client, admin, origin, made["id"]) == {
        "moved_to": renamed,
        "object_id": made["id"],
    }

    # 그 뒤 다른 타입이 옛 slug 를 다시 써도 그 주소는 옮기기 전의 주소다 — 지금 자리를
    # 안내한다.
    reused = _make_type(client, admin, slug=first, label=f"다시 쓴 {_hex()}")
    assert reused == first
    assert _old(client, admin, first, made["id"]) == {
        "moved_to": renamed,
        "object_id": made["id"],
    }


def test_slug_를_바꾼_뒤_옛_slug_로_적힌_남의_옮기기는_따라가지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """A 를 A2 로 바꾼 뒤 새 타입이 A 를 쓰고 그 객체를 C 로 옮겼다 — A2 의 주소는 그 옮기기의
    것이 아니다(옮기기 기록의 원 타입은 그때의 slug 로만 적힌다)."""
    first = _make_type(client, admin, label=f"처음 {_hex()}", key_policy="optional")
    renamed = f"renamed_{_hex()}"
    _rename(client, admin, first, renamed)
    newcomer = _make_type(client, admin, slug=first, label=f"새로 쓴 {_hex()}")
    assert newcomer == first
    third = _make_type(client, admin, label=f"셋째 {_hex()}", key_policy="optional")
    made = _made(client, admin, newcomer, label="씨")
    done = _move(client, admin, newcomer, third, apply=True)
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    assert _old(client, admin, first, made["id"]) == {
        "moved_to": third,
        "object_id": made["id"],
    }
    other = client.get(f"/api/objects/{renamed}/{made['id']}", headers=admin.headers)
    assert other.status_code == 404, other.text
    assert other.json()["error"]["code"] != code("OBJECTS", 160), other.text


def test_두_번_옮긴_뒤_가운데_타입의_slug_를_바꿔도_첫_주소가_끝을_안내한다(
    client: TestClient, admin: Signed
) -> None:
    first = _make_type(client, admin, label=f"처음 {_hex()}", key_policy="optional")
    second = _make_type(client, admin, label=f"둘째 {_hex()}", key_policy="optional")
    third = _make_type(client, admin, label=f"셋째 {_hex()}", key_policy="optional")
    made = _made(client, admin, first, label="비")
    for src, dst in ((first, second), (second, third)):
        done = _move(client, admin, src, dst, apply=True)
        assert done.status_code == 200 and done.json()["applied"] is True, done.text
    middle = f"middle_{_hex()}"
    _rename(client, admin, second, middle)
    assert _old(client, admin, first, made["id"]) == {
        "moved_to": third,
        "object_id": made["id"],
    }


def test_원_표를_비추는_타입의_거절은_조사를_고르지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """「부서은 다른 표를 …」 — 이름의 받침에 따라 조사가 달라 「은(는)」 으로 적는다."""
    projected = _make_type(
        client, admin, label="부서", kind_class="system", system_source="workspace"
    )
    denied = client.post(
        f"/api/objects/{projected}",
        json={"label": "새 부서", "workspace_slug": admin.workspace},
        headers=admin.headers,
    )
    assert denied.status_code == 403, denied.text
    message = denied.json()["error"]["message"]
    assert message.startswith("부서은(는) 다른 표를 비추는 타입이라"), message
