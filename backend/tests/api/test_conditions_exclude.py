"""목록 조건의 **제외** — 조건 묶음 하나를 부정한다(`nf<번호>.<칸>.<연산>=<값>`, ADR 0015).

규칙 기준의 건 보기가 「규칙 k 의 조건 AND 앞 규칙들 어느 것에도 안 맞음」 을 이것으로 적는다.
그래서 이 시험은 늘 **나머지의 수**를 본다: 묶음에 맞는 것과 묶음을 제외한 것의 합이 전체와
같아야 한다 — 칸이 비어 비교가 NULL 인 기록이 양쪽 어디에도 안 들면(맨 `NOT`) 셀과 목록이
어긋난다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object
from tests.api.test_views import _labels, _world


def _total(client: TestClient, who: Signed, part: str, params: dict[str, str]) -> int:
    got = client.get(f"/api/objects/{part}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return int(got.json()["total"])


def test_제외_묶음은_묶음에_맞는_것을_뺀다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    # 지역 · 무게 · 제조일이 다 빈 넷째 — 비교가 NULL 이면 「안 맞음」 이라 남는다.
    _make_object(client, admin, part, key="P-D", label="핀")
    assert _labels(client, admin, part, **{"nf1.region.eq": "영남"}) == ["너트", "와셔", "핀"]
    # 묶음 안은 AND — 「호남이면서 10 이상」 인 것만 뺀다.
    assert _labels(
        client, admin, part, **{"nf1.region.eq": "호남", "nf1.weight.gte": "10"}
    ) == ["볼트", "와셔", "핀"]
    # 묶음끼리는 AND — 둘 다 빠진다.
    assert _labels(
        client, admin, part, **{"nf1.region.eq": "영남", "nf2.weight.gte": "20"}
    ) == ["너트", "핀"]
    # 보통 조건과 함께.
    assert _labels(
        client, admin, part, **{"f.weight.gte": "10", "nf1.region.eq": "수도권"}
    ) == ["너트"]
    # 여러 값 칸 · 참조 · 날짜 · 글자(로 시작)도 같은 연산이다.
    assert _labels(client, admin, part, **{"nf1.tags.eq": "x"}) == ["너트", "와셔", "핀"]
    assert _labels(client, admin, part, **{"nf1.vendor.notempty": ""}) == [
        "너트",
        "와셔",
        "핀",
    ]
    assert _labels(client, admin, part, **{"nf1.made.lt": "2026-02-01"}) == ["너트", "핀"]
    assert _labels(client, admin, part, **{"nf1.label.starts": "볼"}) == ["너트", "와셔", "핀"]


def test_묶음에_맞는_것과_제외한_것의_합은_전체다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    _make_object(client, admin, part, key="P-D", label="핀")
    everything = _total(client, admin, part, {})
    groups: list[dict[str, str]] = [
        {"region.eq": "영남"},
        {"region.ne": "영남"},
        {"region.in": "영남|호남", "weight.lt": "15"},
        {"tags.in": "x|z"},
        {"made.gte": "2026-01-01"},
        {"weight.gt": "5", "made.lt": "2026-02-01"},
        {"vendor.empty": ""},
        {"label.contains": "트"},
    ]
    for group in groups:
        hit = _total(client, admin, part, {f"f.{key}": value for key, value in group.items()})
        rest = _total(
            client, admin, part, {f"nf3.{key}": value for key, value in group.items()}
        )
        assert hit + rest == everything, group


def test_틀린_제외_조건은_무엇이_틀렸는지_말한다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    for params, said in (
        ({"nf0.region.eq": "영남"}, "1 부터"),
        ({"nf1.color.eq": "x"}, "color"),
        ({"nf1.region": "영남"}, "nf<번호>.<칸>.<연산>"),
        ({"nf1.region.gt": "x"}, "gt"),
    ):
        got = client.get(f"/api/objects/{part}", params=params, headers=admin.headers)
        assert got.status_code == 422, (params, got.text)
        assert said in got.json()["error"]["message"], got.json()


def test_저장된_뷰가_제외_조건을_담고_검사한다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    conditions: list[dict[str, Any]] = [
        {"field": "weight", "op": "gte", "value": "10"},
        {"field": "region", "op": "eq", "value": "수도권", "exclude": 1},
    ]
    made = client.post(
        f"/api/objects/{part}/views",
        json={"name": "무거운 것(수도권 빼고)", "query": {"q": "", "conditions": conditions}},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    stored = made.json()["query"]["conditions"]
    assert stored[0]["exclude"] == 0 and stored[1]["exclude"] == 1
    # 뷰가 담은 조건으로 그대로 열면(주소의 `nf1.`) 같은 목록.
    assert _labels(
        client, admin, part, **{"f.weight.gte": "10", "nf1.region.eq": "수도권"}
    ) == ["너트"]
    bad = client.patch(
        f"/api/objects/{part}/views/{made.json()['id']}",
        json={
            "query": {
                "q": "",
                "conditions": [{"field": "color", "op": "eq", "value": "x", "exclude": 2}],
            }
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422 and "color" in bad.json()["error"]["message"]


def test_0건의_진단은_제외_묶음을_한_조건으로_뺀다(client: TestClient, admin: Signed) -> None:
    part, _, _ = _world(client, admin)
    params = {"f.weight.gte": "10", "nf1.weight.gte": "10", "nf1.region.ne": "영남"}
    assert _total(client, admin, part, params) == 0
    got = client.get(f"/api/objects/{part}/diagnose", params=params, headers=admin.headers)
    assert got.status_code == 200, got.text
    effects = {one["name"]: one for one in got.json()["filters"]}
    assert effects["nf1"]["label"] == "제외: (weight gte 10 그리고 region ne 영남)"
    assert effects["nf1"]["remaining"] == 2 and effects["nf1"]["unknown"] is None
    # 통계도 같은 거르기다.
    summary = client.get(
        f"/api/objects/{part}/summary",
        params={"group_by": "properties.region", "nf1.region.eq": "영남"},
        headers=admin.headers,
    )
    assert summary.status_code == 200, summary.text
    assert {one["label"] for one in summary.json()["buckets"]} == {"호남", "수도권"}
