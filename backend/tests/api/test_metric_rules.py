"""규칙 기준(ADR 0015) — **조건 목록의 첫 일치로 값을 붙이는 기준.**

열 가지 물음 ⑧ 「성격(제품결함 · NTF · 고객과실 · SW)으로 분류한 뒤 그 구성비 추이」 에 답한다.
이 시험은 늘 셋을 나란히 본다: 규칙대로 센 수가 손으로 가른 정답과 같은가, 셀마다 「N건 보기」
조건으로 부른 목록의 수가 **셀의 수와 정확히** 같은가(규칙이 겹쳐도 — 「≈」 로 넘기지 않는다),
규칙이 바뀌면 지난 기간도 새 규칙으로 다시 세고 그 판이 계산 기록에 남는가.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _analysis, _month, _plant
from tests.api.test_metric_incremental import _cells, _ids, _patch, _recount
from tests.api.test_metrics import _define, _listed, _prop, _read, _slug
from tests.api.test_ontology import _make_object, _make_type

#: ADR 0015 의 예와 같은 모양 — 차례가 뜻이다. 다섯째 규칙은 **참조 너머의 칸**(모델의 시리즈).
RULES: list[dict[str, Any]] = [
    {"label": "SW", "when": [{"field": "action", "op": "starts", "value": "SW"}]},
    {"label": "고객과실", "when": [{"field": "diag", "op": "in", "value": "C01|C02|C07"}]},
    {
        "label": "NTF",
        "when": [
            {"field": "parts", "op": "empty", "value": ""},
            {"field": "action", "op": "eq", "value": "N00"},
        ],
    },
    {"label": "제품결함", "when": [{"field": "parts", "op": "notempty", "value": ""}]},
    {"label": "모델결함", "when": [{"field": "ref.model.series", "op": "eq", "value": "Z"}]},
]

#: 손으로 가른 정답 — 기록 열 건.
EXPECTED = {"SW": 2, "고객과실": 2, "NTF": 1, "제품결함": 2, "모델결함": 1, "기타": 2}


def _nature(rules: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    return {
        "name": "nature",
        "label": "성격",
        "rules": rules if rules is not None else RULES,
        "else": "기타",
        **kw,
    }


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    """기록 ─model→ 모델(시리즈). 기록 열 — 조치 코드 · 진단 코드 · 교체 부품(여럿) · 센터.

    겹침: 1 은 SW 이면서 고객과실 · 제품결함, 5 는 고객과실이면서 NTF, 10 은 제품결함이면서
    모델결함. 빈 값: 2 · 6 · 9 는 조치 코드가 없다(「SW 로 시작」 이 NULL — 안 맞음). 8 은
    소문자 `sw3`(「로 시작」 은 대소문자를 안 가린다 — 목록 조건과 같다)."""
    part = _make_type(client, admin, label="부품")
    model = _make_type(client, admin, label="모델")
    _prop(client, admin, model, "series", "시리즈", data_type="text")
    case = _make_type(client, admin, label="서비스", usage="log")
    _prop(client, admin, case, "received", "접수일", data_type="date")
    _prop(client, admin, case, "action", "조치 코드", data_type="text")
    _prop(
        client,
        admin,
        case,
        "diag",
        "진단 코드",
        data_type="enum",
        enum_options=[f"C0{n}" for n in range(1, 10)],
    )
    _prop(
        client,
        admin,
        case,
        "parts",
        "교체 부품",
        data_type="object_ref",
        ref_type_slug=part,
        multi=True,
    )
    _prop(client, admin, case, "model", "모델", data_type="object_ref", ref_type_slug=model)
    _prop(client, admin, case, "center", "센터", data_type="text")
    _prop(client, admin, case, "cost", "비용", data_type="number")
    p1 = _make_object(client, admin, part, label="P1")["id"]
    p2 = _make_object(client, admin, part, label="P2")["id"]
    mx = _make_object(client, admin, model, label="MX", properties={"series": "X"})["id"]
    my = _make_object(client, admin, model, label="MY", properties={"series": "Y"})["id"]
    mz = _make_object(client, admin, model, label="MZ", properties={"series": "Z"})["id"]
    rows: list[tuple[str, str | None, str | None, list[str], str | None, str]] = [
        ("2026-01-05", "SW1", "C01", [p1], mx, "서울"),  # SW
        ("2026-01-06", None, "C02", [], mx, "서울"),  # 고객과실
        ("2026-01-07", "N00", None, [], mx, "부산"),  # NTF
        ("2026-01-08", "R10", "C05", [p1], mx, "부산"),  # 제품결함
        ("2026-02-01", "N00", "C07", [], mx, "서울"),  # 고객과실(NTF 보다 앞)
        ("2026-02-02", None, None, [], mz, "서울"),  # 모델결함(참조 너머)
        ("2026-02-03", "X", "C09", [], my, "부산"),  # 기타
        ("2026-02-04", "sw3", None, [], mx, "부산"),  # SW(소문자)
        ("2026-02-05", None, None, [], None, "서울"),  # 기타(모델 없음)
        ("2026-02-06", "N00", "C03", [p2], mz, "부산"),  # 제품결함(모델결함보다 앞)
    ]
    for index, (received, action, diag, parts, owner, center) in enumerate(rows, 1):
        properties: dict[str, Any] = {
            "received": received,
            "parts": parts,
            "center": center,
            "cost": index * 10,
        }
        if action is not None:
            properties["action"] = action
        if diag is not None:
            properties["diag"] = diag
        if owner is not None:
            properties["model"] = owner
        _make_object(client, admin, case, label=f"건{index}", properties=properties)
    return {"case": case, "model": model, "mx": mx, "my": my, "mz": mz, "p1": p1}


def _spec(**more: Any) -> dict[str, Any]:
    return {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [_nature()],
        **more,
    }


def _plan(
    client: TestClient, admin: Signed, source: str, spec: dict[str, Any]
) -> dict[str, Any]:
    got = client.post(
        "/api/metrics/plan",
        json={"source_type_slug": source, "spec": spec},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _check_drills(
    client: TestClient, admin: Signed, type_slug: str, items: list[tuple[int, dict[str, Any]]]
) -> int:
    """셀마다 건 보기 조건으로 목록을 불러 **같은 수**인가 — `partial` 이 없어야 한다."""
    seen = 0
    for count, drill in items:
        if count == 0:
            continue
        assert drill["partial"] == [], drill
        assert _listed(client, admin, type_slug, drill["params"]) == count, drill
        seen += 1
    return seen


def test_규칙_기준은_첫_일치로_세고_기타가_남는다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_spec(), label="성격")
    assert metric["last_status"] == "ok", metric
    (dim,) = metric["dims"]
    assert dim["name"] == "nature" and dim["kind"] == "rule" and dim["label"] == "성격"
    # 저장된 정의는 규칙 기준의 모양 그대로(주소 · 단위 없이, `else` 이름으로).
    stored = metric["spec"]["dimensions"][0]
    assert stored["else"] == "기타" and "address" not in stored and "else_" not in stored

    table = _read(client, admin, metric["slug"], dims="nature")
    counts = {one["dims"]["nature"]: one["count"] for one in table["cells"]}
    assert counts == EXPECTED
    assert {one["labels"]["nature"] for one in table["cells"]} == set(EXPECTED)
    # 셀마다 「N건 보기」 = 셀의 수 — 겹치는 규칙(1 · 5 · 10)도, 빈 칸(2 · 6 · 9)도.
    assert _check_drills(
        client, admin, w["case"], [(one["count"], one["drill"]) for one in table["cells"]]
    ) == len(EXPECTED)
    other = next(one for one in table["cells"] if one["dims"]["nature"] == "기타")
    # 기타 = 모든 규칙의 제외 묶음.
    groups = {key.split(".", 1)[0] for key in other["drill"]["params"] if key.startswith("nf")}
    assert groups == {f"nf{n}" for n in range(1, len(RULES) + 1)}
    customer = next(one for one in table["cells"] if one["dims"]["nature"] == "고객과실")
    assert customer["drill"]["params"]["f.diag.in"] == "C01|C02|C07"
    assert customer["drill"]["params"]["nf1.action.starts"] == "SW"

    # 기간별로도 — 1월 넷 · 2월 여섯.
    by_period = _read(client, admin, metric["slug"], dims="nature", by="period")
    jan = {
        one["dims"]["nature"]: one["count"]
        for one in by_period["cells"]
        if one["period"] == "2026-01-01"
    }
    assert jan == {"SW": 1, "고객과실": 1, "NTF": 1, "제품결함": 1}
    assert _check_drills(
        client, admin, w["case"], [(one["count"], one["drill"]) for one in by_period["cells"]]
    ) == len(by_period["cells"])

    # 기준 값 목록 · 추이도 이름 그대로.
    values = client.get(
        f"/api/metrics/{metric['slug']}/dims", params={"name": "nature"}, headers=admin.headers
    ).json()
    assert values["kind"] == "rule" and {one["value"] for one in values["values"]} == set(
        EXPECTED
    )
    series = _read(client, admin, metric["slug"], "series", split="nature")
    assert {line["key"] for line in series["lines"]} == set(EXPECTED)


def test_다른_기준_거르기_분모와_함께_돈다(client: TestClient, admin: Signed) -> None:
    """규칙 기준 + 보통 기준(센터) + 정의의 거르기(같은 칸 · 같은 연산이 규칙과 겹친다) +
    분모(판매 대수, 기간끼리). 건 보기는 거르기와 규칙의 `diag in` 을 **둘 다 맞는** 하나로
    합쳐 적는다."""
    w = _world(client, admin)
    sales = _make_type(client, admin, label="판매", usage="log")
    _prop(client, admin, sales, "month", "판매월", data_type="date")
    _prop(client, admin, sales, "units", "대수", data_type="number")
    for month, sold in (("2026-01-01", 100), ("2026-02-01", 200)):
        _make_object(
            client,
            admin,
            sales,
            label=f"판매 {month}",
            properties={"month": month, "units": sold},
        )
    units = _define(
        client,
        admin,
        source=sales,
        spec={
            "measure": "sum",
            "measure_field": "properties.units",
            "time": {"address": "properties.month", "grain": "month"},
        },
        label="판매 대수",
    )
    metric = _define(
        client,
        admin,
        source=w["case"],
        spec=_spec(
            dimensions=[_nature(), {"name": "center", "address": "properties.center"}],
            filters=[{"field": "diag", "op": "in", "value": "C01|C02|C05|C07|C03"}],
            denominator={"metric": units["slug"], "on": [], "time": "period", "per": 100},
        ),
        label="성격 x 센터",
    )
    table = _read(client, admin, metric["slug"], dims="nature,center", by="period")
    found = {
        (one["period"], one["dims"]["nature"], one["dims"]["center"]): one["count"]
        for one in table["cells"]
    }
    # 거르기를 통과한 것: 1(C01) · 2(C02) · 4(C05) · 5(C07) · 10(C03).
    assert found == {
        ("2026-01-01", "SW", "서울"): 1,
        ("2026-01-01", "고객과실", "서울"): 1,
        ("2026-01-01", "제품결함", "부산"): 1,
        ("2026-02-01", "고객과실", "서울"): 1,
        ("2026-02-01", "제품결함", "부산"): 1,
    }
    sw = next(one for one in table["cells"] if one["dims"]["nature"] == "SW")
    assert sw["ratio"] == pytest.approx(1 / 100 * 100)
    customer = next(
        one
        for one in table["cells"]
        if one["dims"]["nature"] == "고객과실" and one["period"] == "2026-02-01"
    )
    assert customer["ratio"] == pytest.approx(1 / 200 * 100)
    # 규칙의 `diag in C01|C02|C07` 과 거르기의 `diag in …` — 겹치는 값으로 합친다.
    assert customer["drill"]["params"]["f.diag.in"] == "C01|C02|C07"
    assert _check_drills(
        client, admin, w["case"], [(one["count"], one["drill"]) for one in table["cells"]]
    ) == len(table["cells"])


def test_건_보기는_기간_범위와_거르기의_같은_칸을_둘_다_맞게_합친다(
    client: TestClient, admin: Signed
) -> None:
    """거르기 `received gte 2026-01-07` 과 1월 셀의 `received gte 2026-01-01` 은 같은 키다 —
    예전에는 셀의 것이 거르기를 덮어 1월 1~6일이 목록에 섞였다. 좁은 쪽 하나로 합친다."""
    w = _world(client, admin)
    metric = _define(
        client,
        admin,
        source=w["case"],
        spec=_spec(filters=[{"field": "received", "op": "gte", "value": "2026-01-07"}]),
        label="성격(1월 7일부터)",
    )
    table = _read(client, admin, metric["slug"], dims="nature", by="period")
    jan = [one for one in table["cells"] if one["period"] == "2026-01-01"]
    assert sum(one["count"] for one in jan) == 2  # 3(1월 7일) · 4(1월 8일)
    for one in jan:
        assert one["drill"]["params"]["f.received.gte"] == "2026-01-07"
    assert _check_drills(
        client, admin, w["case"], [(one["count"], one["drill"]) for one in table["cells"]]
    ) == len(table["cells"])


def test_계획이_규칙별_수와_막힌_규칙과_기타의_몫을_말한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    rules = [
        *RULES,
        # 앞의 SW 가 다 가져간다 — 막힌 규칙.
        {"label": "SW 재작업", "when": [{"field": "action", "op": "starts", "value": "SW1"}]},
        # 맞는 기록이 없다.
        {"label": "없는 진단", "when": [{"field": "diag", "op": "eq", "value": "C04"}]},
    ]
    found = _plan(client, admin, w["case"], _spec(dimensions=[_nature(rules)]))
    assert found["ok"] is True, found
    (plan,) = found["rules"]
    assert plan["name"] == "nature" and plan["seen"] == 10 and plan["sampled"] is False
    by_label = {one["label"]: one for one in plan["rules"]}
    assert {label: one["won"] for label, one in by_label.items()} == {
        "SW": 2,
        "고객과실": 2,
        "NTF": 1,
        "제품결함": 2,
        "모델결함": 1,
        "SW 재작업": 0,
        "없는 진단": 0,
    }
    assert plan["other"] == "기타" and plan["other_count"] == 2
    # 맞는 수(앞 규칙과 상관없이)와 누가 가져갔나.
    assert by_label["고객과실"]["matched"] == 3  # 1 · 2 · 5 — 1 은 SW 가
    assert by_label["고객과실"]["taken_by"] == [{"rule": 1, "count": 1}]
    assert by_label["NTF"]["matched"] == 2 and by_label["NTF"]["taken_by"] == [
        {"rule": 2, "count": 1}
    ]
    assert by_label["SW 재작업"]["blocked"] is True and by_label["SW 재작업"]["matched"] == 1
    assert by_label["없는 진단"]["matched"] == 0 and by_label["없는 진단"]["blocked"] is False
    warnings = " / ".join(found["warnings"])
    assert "「SW 재작업」 은 막힌 규칙" in warnings and "규칙 1 「SW」" in warnings
    assert "「없는 진단」 에 맞는 기록이" in warnings
    assert plan["fingerprint"] and len(plan["fingerprint"]) == 64
    # 기준 정보에도 선다 — 값의 가짓수.
    assert found["dims"][0]["kind"] == "rule" and found["dims"][0]["distinct"] == 6

    # 기타가 크면 경고한다 — SW 규칙 하나면 열 중 여덟이 기타.
    lonely = _plan(client, admin, w["case"], _spec(dimensions=[_nature(RULES[:1])]))
    assert lonely["rules"][0]["other_count"] == 8
    assert any("「기타」 가 80%" in one for one in lonely["warnings"]), lonely["warnings"]


@pytest.mark.parametrize(
    ("dimension", "said"),
    [
        (_nature([]), "규칙이 없습니다"),
        (_nature([{"label": "SW", "when": []}]), "조건이 없습니다"),
        (
            _nature([{"label": "SW", "when": [{"field": "nope", "op": "eq", "value": "1"}]}]),
            "규칙 1 「SW」: 없는 칸입니다: nope",
        ),
        (
            _nature(
                [{"label": "SW", "when": [{"field": "diag", "op": "starts", "value": "C"}]}]
            ),
            "「starts」 는 이 칸에 못 겁니다",
        ),
        (_nature([RULES[0], {**RULES[1], "label": "SW"}]), "규칙 2 「SW」 의 이름이 규칙 1"),
        (_nature([{**RULES[0], "label": "기타"}]), "「기타」 의 이름과 같습니다"),
        (_nature([{**RULES[0], "label": " "}]), "규칙 1 의 이름(성격)이 비었습니다"),
        (
            _nature([{"label": f"R{n}", "when": RULES[0]["when"]} for n in range(21)]),
            "규칙은 20개까지",
        ),
        (
            _nature(
                [
                    {
                        "label": "SW",
                        "when": [
                            {"field": "action", "op": "starts", "value": "SW"},
                            {"field": "action", "op": "starts", "value": "SX"},
                        ],
                    }
                ]
            ),
            "같은 칸 · 같은 연산이 두 번",
        ),
        (
            _nature(
                [
                    {
                        "label": "SW",
                        "when": [
                            {"field": "cost", "op": op, "value": "1"}
                            for op in ("eq", "ne", "gt", "gte", "lt", "lte", "in")
                        ]
                        + [
                            {"field": "action", "op": op, "value": "1"}
                            for op in ("eq", "ne", "contains", "starts")
                        ],
                    }
                ]
            ),
            "조건은 10개까지",
        ),
        (_nature(address="properties.center"), "주소(address)를 두지 않습니다"),
        ({**_nature(), "else": ""}, "「기타」 이름이 비었습니다"),
        ({"name": "center", "address": "properties.center", "label": "센터"}, "규칙 기준에만"),
        ({"name": "center"}, "주소(address)나 규칙(rules)"),
    ],
)
def test_틀린_규칙은_계획에서_거절한다(
    client: TestClient, admin: Signed, dimension: dict[str, Any], said: str
) -> None:
    w = _world(client, admin)
    found = _plan(client, admin, w["case"], _spec(dimensions=[dimension]))
    assert found["ok"] is False
    assert any(said in one for one in found["errors"]), found["errors"]
    saved = client.post(
        "/api/metrics",
        json={
            "slug": _slug("r"),
            "label": "x",
            "source_type_slug": w["case"],
            "spec": _spec(dimensions=[dimension]),
        },
        headers=admin.headers,
    )
    assert saved.status_code == 422, saved.text


def _stamped_before(object_id: Any, watermark: str) -> bool:
    """그 기록의 바뀐 때가 그 계산의 워터마크보다 앞서나 — 시계가 뒤로 튄 표시."""
    import uuid

    from app.database import SessionLocal
    from app.modules.objects.models import ObjectInstance

    with SessionLocal() as db:
        row = db.get(ObjectInstance, uuid.UUID(str(object_id)))
        assert row is not None
        return row.updated_at <= datetime.fromisoformat(watermark)


def test_규칙을_바꾸면_지난_기간도_다시_세고_계산_기록에_규칙_판이_남는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_spec(), label="성격")
    slug = metric["slug"]
    (first,) = client.get(f"/api/metrics/{slug}/runs", headers=admin.headers).json()
    (version,) = first["rules"]
    assert version["name"] == "nature" and version["rules"] == len(RULES)
    assert version["changed_by"] and version["changed_at"]
    assert first["stats"]["rules"][0]["fingerprint"] == version["fingerprint"]

    # 바뀐 것이 없으면 바뀐 기간만 — 규칙 판은 그대로다.
    idle = _recount(client, admin, slug)
    assert idle["mode"] == "incremental" and idle["rules"] == first["rules"]

    # 기록 하나를 고치면(기타 → SW) 그 기간만 다시 세고, 전부 다시 센 것과 같다.
    seventh = _ids(w["case"])["건7"]
    _patch(client, admin, w["case"], seventh, properties={"action": "SW9"})
    bit = _recount(client, admin, slug)
    # **시계가 뒤로 튀면 증분이 그 고침을 못 본다** — 이 PC(WSL)의 시계는 몇 초씩 되감기고,
    # 그러면 고친 기록의 시각이 앞 계산의 워터마크보다 앞선다(2026-10-13 실측 — 세 번에 한 번).
    # 운영 서버의 시계는 뒤로 안 간다. 그때는 증분 단언을 건너뛰고 아래 전량으로 이어 본다.
    behind = _stamped_before(seventh, idle["watermark"])
    if not behind:
        assert bit["mode"] == "incremental" and "2026-02-01" in bit["periods"]
    after_increment = _cells(slug)
    full = _recount(client, admin, slug, reason="manual")
    assert full["mode"] == "full" and (behind or _cells(slug) == after_increment)
    counts = {
        one["dims"]["nature"]: one["count"]
        for one in _read(client, admin, slug, dims="nature")["cells"]
    }
    assert counts["SW"] == 3 and counts["기타"] == 1

    # 차례를 바꾸면(NTF 를 고객과실 앞으로) 지난 기간도 새 규칙으로 — 정의 지문이 달라 전량.
    swapped = [RULES[0], RULES[2], RULES[1], *RULES[3:]]
    changed = client.patch(
        f"/api/metrics/{slug}",
        json={"spec": _spec(dimensions=[_nature(swapped)])},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text
    redone = _recount(client, admin, slug, reason="updated")
    assert redone["mode"] == "full" and redone["note"].startswith("정의가 바뀌었다")
    (new_version,) = redone["rules"]
    assert new_version["fingerprint"] != version["fingerprint"]
    assert new_version["changed_at"] > version["changed_at"]
    counts = {
        one["dims"]["nature"]: one["count"]
        for one in _read(client, admin, slug, dims="nature")["cells"]
    }
    # 5 는 이제 NTF(1월의 3 과 함께) — 1월 기간의 셀도 새 규칙으로 셌다.
    assert counts["NTF"] == 2 and counts["고객과실"] == 1
    # 감사에 어느 판에서 어느 판으로 바뀌었는지가 남는다.
    import uuid

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.audit.models import AuditEntry

    with SessionLocal() as db:
        latest = db.scalars(
            select(AuditEntry)
            .where(
                AuditEntry.action == "metric.updated",
                AuditEntry.target_id == uuid.UUID(metric["id"]),
            )
            .order_by(AuditEntry.created_at.desc())
        ).first()
        assert latest is not None
        assert latest.changes["rules"]["nature"] == {
            "from": version["fingerprint"],
            "to": new_version["fingerprint"],
        }

    # 이름 · 기타의 이름만 바꾼 것이 아니면 다른 판 — 보여 줄 이름(label)만 바꾸면 같은 판이다.
    relabelled = client.patch(
        f"/api/metrics/{slug}",
        json={"spec": _spec(dimensions=[_nature(swapped, label="성격(새)")])},
        headers=admin.headers,
    )
    assert relabelled.status_code == 200, relabelled.text
    again = _recount(client, admin, slug, reason="updated")
    assert again["rules"][0]["fingerprint"] == new_version["fingerprint"]


def test_규칙_조건이_다른_객체_너머를_보면_그_객체가_바뀔_때_전부_센다(
    client: TestClient, admin: Signed
) -> None:
    """모델결함은 모델의 시리즈를 본다 — 모델이 바뀌어도 기록의 시각은 안 오른다. 증분이 그것을
    놓치면 모델결함이 낡은 채 남는다."""
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_spec(), label="성격")
    slug = metric["slug"]
    _recount(client, admin, slug)  # 증분 한 번 — 아래의 전량이 「너머」 때문인 것을 보려고
    _patch(client, admin, w["model"], w["mz"], properties={"series": "Q"})
    redone = _recount(client, admin, slug)
    assert redone["mode"] == "full", redone
    assert redone["note"] == "참조 · 관계 너머의 객체가 바뀌었다"
    counts = {
        one["dims"]["nature"]: one["count"]
        for one in _read(client, admin, slug, dims="nature")["cells"]
    }
    assert "모델결함" not in counts and counts["기타"] == 3


def test_구성비는_기간마다_합이_100이다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_spec(), label="성격")
    slug = metric["slug"]
    table = _read(client, admin, slug, dims="nature", by="period", composition="nature")
    assert table["composition"] == "nature"
    totals: dict[str, float] = {}
    for one in table["cells"]:
        totals[one["period"]] = totals.get(one["period"], 0.0) + one["composition"]
    assert totals == {
        "2026-01-01": pytest.approx(100.0),
        "2026-02-01": pytest.approx(100.0),
    }
    jan_sw = next(
        one
        for one in table["cells"]
        if one["period"] == "2026-01-01" and one["dims"]["nature"] == "SW"
    )
    assert jan_sw["composition"] == pytest.approx(25.0)
    # 묶지 않으면 전체에서의 몫.
    whole = _read(client, admin, slug, dims="nature", composition="nature")
    assert sum(one["composition"] for one in whole["cells"]) == pytest.approx(100.0)
    other = next(one for one in whole["cells"] if one["dims"]["nature"] == "기타")
    assert other["composition"] == pytest.approx(20.0)

    # 추이 — 점마다 그 기간의 선들 사이의 몫.
    series = _read(client, admin, slug, "series", split="nature", composition="true")
    assert series["composition"] is True
    per_period: dict[str, float] = {}
    for line in series["lines"]:
        for point in line["points"]:
            per_period[point["period"]] = per_period.get(point["period"], 0.0) + (
                point["composition"] or 0.0
            )
    assert per_period == {
        "2026-01-01": pytest.approx(100.0),
        "2026-02-01": pytest.approx(100.0),
    }

    # 막을 것 — 묶지 않은 기준, 선을 안 나눈 추이, 더할 수 없는 집계.
    def refused(shape: str, **params: Any) -> str:
        got = client.get(f"/api/metrics/{slug}/{shape}", params=params, headers=admin.headers)
        assert got.status_code == 422, got.text
        return str(got.json()["error"]["message"])

    assert "묶을 기준(dims)" in refused("values", dims="", composition="nature")
    assert "split" in refused("series", composition="true")
    average = _define(
        client,
        admin,
        source=w["case"],
        spec=_spec(measure="avg", measure_field="properties.cost"),
        label="평균 비용",
    )
    got = client.get(
        f"/api/metrics/{average['slug']}/values",
        params={"dims": "nature", "composition": "nature"},
        headers=admin.headers,
    )
    assert got.status_code == 422 and "더할 수 없는" in got.json()["error"]["message"]


def test_분석과_경보가_규칙_기준에_걸린다(client: TestClient, admin: Signed) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_spec(), label="성격")
    slug = metric["slug"]
    # 파레토 — 세어 둔 셀 그대로, 값마다 건 보기가 그 수다.
    pareto = _analysis(client, admin, slug, "pareto", dim="nature")
    assert {one["label"]: one["count"] for one in pareto["items"]} == EXPECTED
    for one in pareto["items"]:
        assert one["drill"]["partial"] == []
        assert _listed(client, admin, w["case"], one["drill"]["params"]) == one["count"]

    # 심은 셀: SW 만 30번째 달부터 두 배 — 두 기간의 구성비 비교와 성격마다 훑기.
    first = date(2026, 1, 1)
    _plant(
        slug,
        [
            {
                "period": _month(first, i),
                "dims": {"nature": nature},
                "count": 2 * count if nature == "SW" and i >= 30 else count,
            }
            for i in range(48)
            for nature, count in (("SW", 100), ("NTF", 150), ("기타", 80))
        ],
        datetime(2030, 2, 15, tzinfo=UTC),
    )
    compared = _analysis(
        client,
        admin,
        slug,
        "pareto",
        dim="nature",
        period_from="2026-01-01",
        period_to="2027-01-01",
        compare_from="2029-01-01",
        compare_to="2030-01-01",
    )
    items = {one["key"]: one for one in compared["comparison"]["items"]}
    assert items["SW"]["notable"] is True and items["SW"]["residual"] > 3
    assert items["SW"]["share_a"] == pytest.approx(100 / 330)
    assert items["SW"]["share_b"] == pytest.approx(200 / 430)
    scan = _analysis(client, admin, slug, "changes/scan", by="nature")
    assert scan["items"][0]["key"] == "SW" and scan["items"][0]["direction"] == "up"
    assert scan["items"][0]["drill"]["params"]["f.action.starts"] == "SW"

    # 경보 — 기준 이름으로(값마다 훑기 · 거르기 둘 다).
    for params in ({"by": "nature", "axis": "period"}, {"axis": "period", "d.nature": "SW"}):
        made = client.post(
            f"/api/metrics/{slug}/alerts",
            json={"name": "성격", "recipe": "changes", "params": params},
            headers=admin.headers,
        )
        assert made.status_code == 201, made.text
        assert made.json()["last_status"] == "ok", made.json()
