"""묶어 보기 — **목록과 같은 거르기 위에서 센다.**

따로 세면 「목록에는 12건인데 묶어 보면 15건」 이 되고, 그때 어느 쪽이 맞는지 아무도
모른다. 그래서 이 시험은 언제나 **목록의 total 과 묶음의 합**을 나란히 본다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type

GRADES = ["A", "A", "B", "C", None]


def _parts(client: TestClient, admin: Signed) -> str:
    """등급(고를 값)·무게(숫자)를 가진 타입 하나와 객체 다섯."""
    part = _make_type(client, admin, label="부품")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B", "C"],
    )
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    for index, grade in enumerate(GRADES, start=1):
        properties: dict[str, Any] = {"weight": index * 10}
        if grade:
            properties["grade"] = grade
        _make_object(client, admin, part, label=f"부품{index}", properties=properties)
    return part


def _summary(client: TestClient, who: Signed, part: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/objects/{part}/summary", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_속성으로_묶으면_빈_값도_한_칸이_된다(client: TestClient, admin: Signed) -> None:
    part = _parts(client, admin)
    found = _summary(client, admin, part, group_by="properties.grade")

    assert found["group_label"] == "등급"
    assert found["metric_label"] == "건수"
    assert found["total"] == 5
    buckets = {one["label"]: one["count"] for one in found["buckets"]}
    assert buckets == {"A": 2, "B": 1, "C": 1, "(비어 있음)": 1}
    # **막대의 합이 전체와 맞는다.** 빈 값을 숨기면 그 차이가 화면 어디에도 안 적힌다.
    assert sum(one["count"] for one in found["buckets"]) == found["total"]
    assert found["other_groups"] == 0 and found["other_count"] == 0
    # 많은 순으로 — 눈이 맨 위부터 읽는다.
    assert [one["count"] for one in found["buckets"]] == sorted(
        (one["count"] for one in found["buckets"]), reverse=True
    )


def test_거르기를_목록과_같이_받는다(client: TestClient, admin: Signed) -> None:
    part = _parts(client, admin)
    listed = client.get(
        f"/api/objects/{part}", params={"p.grade": "A"}, headers=admin.headers
    ).json()
    found = _summary(client, admin, part, group_by="status", **{"p.grade": "A"})
    assert found["total"] == listed["total"] == 2
    assert [one["label"] for one in found["buckets"]] == ["사용"]


def test_합과_평균은_숫자_칸에서만(client: TestClient, admin: Signed) -> None:
    part = _parts(client, admin)
    found = _summary(
        client,
        admin,
        part,
        group_by="properties.grade",
        metric="sum",
        metric_field="properties.weight",
    )
    values = {one["label"]: one["value"] for one in found["buckets"]}
    assert values["A"] == 30  # 10 + 20
    assert values["(비어 있음)"] == 50
    assert found["metric_label"] == "합계"

    denied = client.get(
        f"/api/objects/{part}/summary",
        params={
            "group_by": "status",
            "metric": "avg",
            "metric_field": "properties.grade",
        },
        headers=admin.headers,
    )
    assert denied.status_code == 422
    assert "숫자 칸이 아니라" in denied.json()["error"]["message"]

    missing = client.get(
        f"/api/objects/{part}/summary",
        params={"group_by": "status", "metric": "sum"},
        headers=admin.headers,
    )
    assert missing.status_code == 422


def test_숫자가_아닌_값이_섞여도_안_죽는다(client: TestClient, admin: Signed) -> None:
    """**행 하나가 질의를 통째로 죽인다** — JSONB 는 글자를 담아 옛 값에 문자가 있을
    수 있고, 그때 화면에는 「합계를 낼 수 없음」 이 아니라 500 이 뜬다."""
    part = _parts(client, admin)
    dirty = _make_object(client, admin, part, label="옛것")
    # 검증을 지나 값이 들어간 상태를 흉내 낸다 — 가져오기 이전의 데이터가 이렇다.
    from app.database import SessionLocal
    from app.modules.objects.models import ObjectInstance

    with SessionLocal() as db:
        row = db.get(ObjectInstance, dirty["id"])
        assert row is not None
        row.properties = {**(row.properties or {}), "weight": "약 3kg"}
        db.commit()

    found = _summary(
        client,
        admin,
        part,
        group_by="status",
        metric="sum",
        metric_field="properties.weight",
    )
    # 못 읽는 값은 셈에서 빠지고, 행 수는 그대로 센다.
    assert found["buckets"][0]["value"] == 150
    assert found["buckets"][0]["count"] == 6


def test_묶을_수_없는_축은_이유를_말한다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label="문서")
    _make_property(client, admin, part, key="body", label="본문", data_type="text_long")
    _make_property(
        client, admin, part, key="tags", label="꼬리표", data_type="text", multi=True
    )

    long_text = client.get(
        f"/api/objects/{part}/summary",
        params={"group_by": "properties.body"},
        headers=admin.headers,
    )
    assert long_text.status_code == 422
    assert "그룹이 행 수만큼" in long_text.json()["error"]["message"]

    many = client.get(
        f"/api/objects/{part}/summary",
        params={"group_by": "properties.tags"},
        headers=admin.headers,
    )
    assert many.status_code == 422
    assert "합이 전체와 안 맞" in many.json()["error"]["message"]

    # 고르개에도 안 뜬다 — 고를 수 없는 것을 보여 주고 나서 거절하지 않는다.
    options = _summary(client, admin, part, group_by="status")["group_options"]
    fields = {one["field"] for one in options}
    assert "properties.body" not in fields and "properties.tags" not in fields
    assert {"status", "workspace", "created_year"} <= fields


def test_투영_타입은_여기서_세지_않는다(client: TestClient, admin: Signed) -> None:
    """행이 없다 — 원 표에 물어야 하는 일이고, 그 표의 축을 이 틀은 모른다."""
    projected = _make_type(
        client, admin, label="부서(투영)", kind_class="system", system_source="workspace"
    )
    denied = client.get(f"/api/objects/{projected}/summary", headers=admin.headers)
    assert denied.status_code == 409
    assert "비추는 타입" in denied.json()["error"]["message"]
