"""축으로 보는 기록 분석(ADR 0022) — 커버리지 · 재발 · 한 장 요약 · 비슷한 기록.

축 이름은 시험이 정한다(모델 · 부품 · 고장 메커니즘 · 해석법) — 코드는 그 이름을 모른다. 축
사이의 길도 여러 모양으로 둔다: 모델 → 부품은 관계, 부품 → 메커니즘 · 메커니즘 → 해석법은 참조
칸.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_metric_analysis import _analysis, _refused
from tests.api.test_metrics import _define, _listed, _prop
from tests.api.test_ontology import _make_object, _make_relation, _make_type


def _relate(
    client: TestClient, admin: Signed, slug: str, src: str, rel: str, dst: str
) -> None:
    got = client.post(
        f"/api/objects/{slug}/{src}/relations",
        json={"relation": rel, "dst_object_id": dst},
        headers=admin.headers,
    )
    assert got.status_code == 201, got.text


def _axes_world(
    client: TestClient,
    admin: Signed,
    extra: tuple[tuple[str, str, str, str | None, str | None], ...] = (),
) -> dict[str, Any]:
    """모델 M1 · M2(M2 의 전작은 M1), 부품 P1 · P2 · P3, 메커니즘 피로 · 크리프 · 부식 · 마모,
    해석법 FEA · 시험 · 염수. M1 은 P1 · P2, M2 는 P2 · P3 를 가진다. P1 → 피로 · 크리프,
    P2 → 피로, P3 → 부식. 피로 → FEA · 시험, 크리프 → FEA, 부식 → 염수."""
    model = _make_type(client, admin, label="모델")
    part = _make_type(client, admin, label="부품")
    mech = _make_type(client, admin, label="고장 메커니즘")
    method = _make_type(client, admin, label="해석법")
    report = _make_type(client, admin, label="분석 보고", usage="log")
    _prop(
        client,
        admin,
        model,
        "predecessor",
        "전작",
        data_type="object_ref",
        ref_type_slug=model,
    )
    _prop(
        client,
        admin,
        part,
        "mechanisms",
        "걸리는 메커니즘",
        data_type="object_ref",
        ref_type_slug=mech,
        multi=True,
    )
    _prop(
        client,
        admin,
        mech,
        "methods",
        "맞는 해석법",
        data_type="object_ref",
        ref_type_slug=method,
        multi=True,
    )
    has_part = _make_relation(
        client, admin, "has_part", label="부품", src_type_slugs=[model], dst_type_slugs=[part]
    )
    _prop(client, admin, report, "reported", "보고일", data_type="date")
    for key, label, target in (
        ("ref_model", "모델", model),
        ("ref_part", "부품", part),
        ("ref_mech", "메커니즘", mech),
        ("ref_method", "해석법", method),
    ):
        _prop(
            client,
            admin,
            report,
            key,
            label,
            data_type="object_ref",
            ref_type_slug=target,
            multi=True,
        )

    def obj(type_slug: str, label: str, **props: Any) -> str:
        return str(_make_object(client, admin, type_slug, label=label, properties=props)["id"])

    methods = {name: obj(method, name) for name in ("FEA", "시험", "염수")}
    mechs = {
        "피로": obj(mech, "피로", methods=[methods["FEA"], methods["시험"]]),
        "크리프": obj(mech, "크리프", methods=[methods["FEA"]]),
        "부식": obj(mech, "부식", methods=[methods["염수"]]),
        "마모": obj(mech, "마모"),
    }
    parts = {
        "P1": obj(part, "P1", mechanisms=[mechs["피로"], mechs["크리프"]]),
        "P2": obj(part, "P2", mechanisms=[mechs["피로"]]),
        "P3": obj(part, "P3", mechanisms=[mechs["부식"]]),
    }
    m1 = obj(model, "M1")
    m2 = obj(model, "M2", predecessor=m1)
    for src, dst in ((m1, "P1"), (m1, "P2"), (m2, "P2"), (m2, "P3")):
        _relate(client, admin, model, src, has_part, parts[dst])
    rows = [
        ("R1", m1, "P1", "피로", "FEA"),
        ("R2", m1, "P2", "피로", "시험"),
        ("R3", m2, "P3", "마모", "FEA"),  # 기대 밖 — P3 에는 마모가 안 걸린다
        ("R4", m1, "P1", None, None),  # 태그가 모자란 기록
    ]
    named = {"M1": m1, "M2": m2}
    rows += [(label, named[m], p, k, h) for label, m, p, k, h in extra]
    reports: dict[str, str] = {}
    for label, m, p, k, h in rows:
        props: dict[str, Any] = {
            "reported": "2026-01-15",
            "ref_model": [m],
            "ref_part": [parts[p]],
        }
        if k:
            props["ref_mech"] = [mechs[k]]
        if h:
            props["ref_method"] = [methods[h]]
        reports[label] = obj(report, label, **props)
    metric = _define(
        client,
        admin,
        source=report,
        spec={
            "measure": "count",
            "time": {"address": "properties.reported", "grain": "month"},
            "dimensions": [
                {"name": "model", "address": "properties.ref_model"},
                {"name": "part", "address": "properties.ref_part"},
                {"name": "mechanism", "address": "properties.ref_mech"},
                {"name": "method", "address": "properties.ref_method"},
            ],
        },
        label="분석 보고 수",
    )
    return {
        "types": {"model": model, "part": part, "mech": mech, "method": method},
        "report": report,
        "has_part": has_part,
        "metric": metric,
        "models": {"M1": m1, "M2": m2},
        "parts": parts,
        "mechs": mechs,
        "methods": methods,
        "reports": reports,
    }


LEVELS = "model,part,mechanism,method"


def test_커버리지는_온톨로지의_길로_편_조합과_다룬_조합을_견준다(
    client: TestClient, admin: Signed
) -> None:
    w = _axes_world(client, admin)
    slug = w["metric"]["slug"]
    found = _analysis(client, admin, slug, "coverage", levels=LEVELS)
    levels = found["levels"]
    assert [one["dim"] for one in levels] == ["model", "part", "mechanism", "method"]
    assert levels[1]["way"] == f"out.{w['has_part']}" and levels[0]["way"] is None
    assert levels[2]["way"] == "ref.mechanisms" and levels[3]["way"] == "ref.methods"
    # 다뤄야 할 끝 조합 — M1: P1(피로 x 2, 크리프 x 1) + P2(피로 x 2) = 5,
    # M2: P2(피로 x 2) + P3(부식 x 1) = 3. 다룬 것은 R1 · R2 의 둘.
    assert found["expected_leaves"] == 8 and found["covered_leaves"] == 2
    depths = {one["depth"]: (one["expected"], one["covered"]) for one in found["depths"]}
    assert depths == {0: (2, 2), 1: (4, 3), 2: (5, 2), 3: (8, 2)}
    roots = {one["label"]: one for one in found["roots"]}
    assert roots["M1"]["share"] == 0.4 and roots["M2"]["share"] == 0.0
    assert found["roots"][0]["label"] == "M2"  # 덜 다룬 것부터
    gaps = {" / ".join(one["labels"]): one["leaves"] for one in found["gaps"]}
    assert gaps == {
        "M1 / P1 / 피로 / 시험": 1,
        "M1 / P1 / 크리프": 1,
        "M1 / P2 / 피로 / FEA": 1,
        "M2 / P2": 2,
        "M2 / P3 / 부식": 1,
    }
    assert found["gaps_total"] == 5
    extras = found["extras"]
    assert [one["labels"] for one in extras] == [["M2", "P3", "마모", "FEA"]]
    assert _listed(client, admin, w["report"], extras[0]["drill"]["params"]) == 1
    assert found["untagged"] == 1
    codes = {one["code"] for one in found["caveats"]}
    assert {"extras", "untagged", "roots_from_records", "cross_product"} <= codes

    # 첫 기준을 거르면 그 값만 편다.
    one = _analysis(
        client, admin, slug, "coverage", levels=LEVELS, **{"d.model": w["models"]["M1"]}
    )
    assert one["expected_leaves"] == 5 and one["gaps_total"] == 3
    # 길이 둘이면 고르라고 한다 — 후보를 말한다. 주면 그것으로.
    other = _make_relation(
        client,
        admin,
        "uses_part",
        label="쓰는 부품",
        src_type_slugs=[w["types"]["model"]],
        dst_type_slugs=[w["types"]["part"]],
    )
    refused = _refused(client, admin, slug, "coverage", levels=LEVELS)
    assert refused["code"].endswith("METRICS-0039") and f"out.{other}" in refused["message"]
    chosen = _analysis(
        client, admin, slug, "coverage", levels=LEVELS, via=f"out.{w['has_part']},,"
    )
    assert chosen["expected_leaves"] == 8
    listed = client.get(
        f"/api/metrics/{slug}/analysis/coverage/ways",
        params={"from": "model", "to": "part"},
        headers=admin.headers,
    )
    assert listed.status_code == 200, listed.text
    assert {one["address"] for one in listed.json()} == {
        f"out.{w['has_part']}",
        f"out.{other}",
    }
    # 닿지 않는 길 · 축이 아닌 기준은 이유와 함께 거절한다.
    wrong = _refused(client, admin, slug, "coverage", levels=LEVELS, via="ref.predecessor,,")
    assert wrong["code"].endswith("METRICS-0039")
    assert _refused(client, admin, slug, "coverage", levels="model")["code"].endswith("0046")


# --- 비슷한 기록 ----------------------------------------------------------------------


def test_비슷한_기록은_드문_태그가_겹칠수록_위에_서고_겹친_태그를_말한다(
    client: TestClient, admin: Signed
) -> None:
    """보고 넷 — R1(M1 · P1 · 피로 · FEA)에 비슷한 것. 무게 ln(4/df): M1 은 셋이 가져 0.29, P1
    · 피로 · FEA 는 둘이 가져 0.69, 하나만 가진 것은 1.39. 손셈 점수: R4 0.414 · R2 0.191 · R3
    0.106."""
    w = _axes_world(client, admin)
    base = f"/api/objects/{w['report']}"
    got = client.get(f"{base}/{w['reports']['R1']}/similar", headers=admin.headers)
    assert got.status_code == 200, got.text
    found = got.json()
    assert found["total"] == 4
    assert [one["label"] for one in found["items"]] == ["R4", "R2", "R3"]
    scores = [one["score"] for one in found["items"]]
    assert scores == pytest.approx([0.4144, 0.1908, 0.1061], abs=1e-3)
    r4 = found["items"][0]
    assert [one["value_label"] for one in r4["shared"]] == ["P1", "M1"]  # 무게 큰 것부터
    assert r4["shared"][0]["field_label"] == "부품" and r4["shared"][0]["records"] == 2
    assert r4["extra"] == 0
    query = {one["value_label"]: one for one in found["query"]}
    assert query["M1"]["weight"] == pytest.approx(math.log(4 / 3))
    # 칸을 고르면 그 칸의 태그로만 — 메커니즘만 보면 R2 가 똑같다.
    only = client.get(
        f"{base}/{w['reports']['R1']}/similar",
        params={"fields": "ref_mech"},
        headers=admin.headers,
    ).json()
    assert [(one["label"], one["score"]) for one in only["items"]] == [("R2", 1.0)]
    # 아직 기록이 없는 이슈 — 태그 묶음으로 묻는다.
    asked = client.post(
        f"{base}/similar",
        json={"tags": {"ref_part": [w["parts"]["P3"]]}},
        headers=admin.headers,
    )
    assert asked.status_code == 200, asked.text
    assert [one["label"] for one in asked.json()["items"]] == ["R3"]
    wrong = client.post(
        f"{base}/similar", json={"tags": {"reported": []}}, headers=admin.headers
    )
    assert wrong.status_code == 422 and wrong.json()["error"]["code"].endswith("0110")


# --- 재발 ----------------------------------------------------------------------------


def test_재발은_전작에서_나온_조합이_후속_모델에서_다시_나온_것을_센다(
    client: TestClient, admin: Signed
) -> None:
    """M2 의 전작은 M1. M1 에서 (P1 · 피로) · (P2 · 피로), M2 에서 (P3 · 마모) · (P1 · 피로) —
    다시 나온 것 하나(P1 · 피로), 재발률 1/2, 새로 나온 것 하나."""
    w = _axes_world(client, admin, extra=(("R5", "M2", "P1", "피로", "FEA"),))
    slug = w["metric"]["slug"]
    found = _analysis(
        client, admin, slug, "recurrence", generation="model", signature="part,mechanism"
    )
    assert found["way"] == "ref.predecessor"  # 후속(나를 가리키는 것)은 후보에서 뺐다
    assert found["pairs_total"] == 1 and found["rate"] == 0.5
    pair = found["pairs"][0]
    assert (pair["label"], pair["predecessor_label"]) == ("M2", "M1")
    assert (pair["predecessor_signatures"], pair["signatures"], pair["recurring"]) == (2, 2, 1)
    assert pair["rate"] == 0.5 and pair["new"] == 1
    item = pair["items"][0]
    assert item["labels"] == ["P1", "피로"] and (item["before"], item["now"]) == (1, 1)
    assert _listed(client, admin, w["report"], item["drill"]["params"]) == 1
    assert found["no_predecessor"] == 1  # M1 은 전작이 없다
    assert [one["labels"] for one in found["signatures"]] == [["P1", "피로"]]
    codes = {one["code"] for one in found["caveats"]}
    assert {"order_ignored", "no_predecessor", "overlap_counts"} <= codes
    # 길을 주면 그것으로 — 후속 쪽(나를 전작으로 가리키는 것)으로 걸으면 짝이 거꾸로다.
    backward = _analysis(
        client,
        admin,
        slug,
        "recurrence",
        generation="model",
        signature="part,mechanism",
        via=f"in.{w['types']['model']}:predecessor",
    )
    assert [(one["label"], one["predecessor_label"]) for one in backward["pairs"]] == [
        ("M1", "M2")
    ]
    refused = _refused(
        client, admin, slug, "recurrence", generation="model", signature="model"
    )
    assert refused["code"].endswith("METRICS-0047")
