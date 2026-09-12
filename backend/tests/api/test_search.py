"""전역 찾기 — **타입을 모르는 사람이 찾는 자리.**

목록은 타입마다 따로다. 여기서 지키는 것은 셋: 별칭으로도 걸리나, 왜 걸렸는지 말하나,
남의 부서 것이 새지 않나.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_type


def _find(client: TestClient, who: Signed, q: str, **params: Any) -> dict[str, Any]:
    got = client.get("/api/search", params={"q": q, **params}, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_이름_식별자_별칭으로_찾고_왜_걸렸는지_말한다(
    client: TestClient, admin: Signed
) -> None:
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품", key_policy="optional")
    bolt = _make_object(client, admin, part, label=f"볼트{tag}", key=f"P-{tag}")
    client.put(
        f"/api/objects/{part}/{bolt['id']}/aliases",
        json={"aliases": [f"헥사{tag}"]},
        headers=admin.headers,
    )

    by_label = _find(client, admin, f"볼트{tag}")
    assert by_label["total"] == 1
    assert by_label["items"][0]["matched"] == "label"
    assert by_label["items"][0]["type_label"] == "부품"

    by_key = _find(client, admin, f"P-{tag}")
    assert by_key["items"][0]["matched"] == "key"

    # **찾는 사람이 아는 유일한 이름이 별칭일 수 있다** — 외부에서 온 것은 저쪽 코드로
    # 기억된다. 그때 왜 걸렸는지 안 적으면 엉뚱해 보이는 줄이 오류로 읽힌다.
    by_alias = _find(client, admin, f"헥사{tag}")
    assert by_alias["total"] == 1
    assert by_alias["items"][0]["matched"] == "alias"
    assert f"헥사{tag}" in by_alias["items"][0]["matched_text"]
    assert by_alias["items"][0]["label"] == f"볼트{tag}"


def test_타입마다_몇_건인지_세고_좁힐_수_있다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    test_type = _make_type(client, admin, label="시험")
    _make_object(client, admin, part, label=f"공용{tag} 볼트")
    _make_object(client, admin, part, label=f"공용{tag} 너트")
    _make_object(client, admin, test_type, label=f"공용{tag} 인장")

    all_found = _find(client, admin, f"공용{tag}")
    assert all_found["total"] == 3
    counts = {one["type_slug"]: one["count"] for one in all_found["types"]}
    assert counts == {part: 2, test_type: 1}
    # 많은 것부터 — 찾는 사람이 먼저 볼 곳이 거기다.
    assert all_found["types"][0]["type_slug"] == part

    narrowed = _find(client, admin, f"공용{tag}", type=test_type)
    assert narrowed["total"] == 1
    assert [one["label"] for one in narrowed["items"]] == [f"공용{tag} 인장"]


def test_부서도_찾는다(client: TestClient, admin: Signed) -> None:
    """부서·계정은 `objects` 에 행이 없지만 **사람이 찾는 것은 그 둘이 가장 많다.**"""
    tag = uuid.uuid4().hex[:6]
    projected = _make_type(
        client, admin, label="부서(투영)", kind_class="system", system_source="workspace"
    )
    made = client.post(
        "/api/workspaces",
        json={"slug": f"ws-{tag}", "name": f"찾을부서{tag}"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    found = _find(client, admin, f"찾을부서{tag}")
    # **같은 원 표를 비추는 타입이 여럿일 수 있다**(시험 DB 를 스위트가 함께 쓴다) —
    # 그러면 같은 부서가 타입마다 한 번씩 걸린다. 내 타입의 것만 본다.
    mine = [one for one in found["items"] if one["type_slug"] == projected]
    assert [one["label"] for one in mine] == [f"찾을부서{tag}"]
    assert projected in {one["type_slug"] for one in found["types"]}

    # 좁혀도 같은 것이 나온다 — 좁히는 순간 다른 API 로 넘어가지 않는다.
    narrowed = _find(client, admin, f"찾을부서{tag}", type=projected)
    assert [one["label"] for one in narrowed["items"]] == [f"찾을부서{tag}"]


def test_너무_짧으면_안_찾는다(client: TestClient, admin: Signed) -> None:
    """한 글자로 거의 모든 행을 돌려주면 그것은 찾기가 아니라 목록이다."""
    short = _find(client, admin, "볼")
    assert short["total"] == 0 and short["items"] == []
    assert short["min_query"] == 2


def test_와일드카드를_글자로_본다(client: TestClient, admin: Signed) -> None:
    """`%` 를 그대로 넣으면 「50%」 가 「50 뒤에 아무거나」 로 읽혀 엉뚱한 것이 걸린다."""
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label=f"수율{tag} 50% 목표")
    _make_object(client, admin, part, label=f"수율{tag} 50건 달성")

    found = _find(client, admin, "50%")
    labels = [one["label"] for one in found["items"]]
    assert f"수율{tag} 50% 목표" in labels
    assert f"수율{tag} 50건 달성" not in labels


def test_남의_부서_것은_수에도_안_잡힌다(
    client: TestClient, admin: Signed, member: Signed, db: Any
) -> None:
    """**수에만 잡혀도 그것은 샌 것이다** — 이름이 보이는 순간 그 부서가 무엇을 하는지가
    드러난다."""
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    other = client.post(
        "/api/workspaces",
        json={"slug": f"secret-{tag}", "name": "비밀팀"},
        headers=admin.headers,
    ).json()
    client.patch(
        f"/api/workspaces/{other['slug']}", json={"restricted": True}, headers=admin.headers
    )
    made = client.post(
        f"/api/objects/{part}",
        json={"label": f"기밀{tag}", "workspace_slug": other["slug"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text

    mine = _find(client, member, f"기밀{tag}")
    assert mine["total"] == 0 and mine["items"] == []
    assert _find(client, admin, f"기밀{tag}")["total"] == 1
