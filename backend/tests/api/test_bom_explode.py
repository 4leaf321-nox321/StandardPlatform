"""경로 계산 — 수량 곱 BOM 전개와 수량 반영 롤업(ADR 0040).

    P(제품)
    ├─ A (2개)            ← 「구성」 관계의 수량 칸 qty
    │   └─ S (3개)
    │       └─ BOLT (4개)
    └─ B (1개)
        └─ S (5개)        ← 같은 서브 조립품을 두 곳에서

제품 하나에 BOLT 는 2 곱하기 3 곱하기 4 = 24(A 를 거쳐) + 1 곱하기 5 곱하기 4 = 20(B 를 거쳐) =
**44개**, S 는 2 곱하기 3 + 1 곱하기 5 = 11개. 선을 고르는 판정은 `edges.alive`
하나이고(기준일), 보이는 범위는 롤업 · 하위 포함과 같다(지나가기는 전부, 내보이는 것은 보이고
지우지 않은 것만).
"""

from __future__ import annotations

import csv
import io
import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type

FEB = "2026-02-15"
MAR = "2026-03-01"
APR = "2026-04-15"


@dataclass(frozen=True)
class World:
    part: str
    kind: str
    ids: dict[str, str]


def _uniq(base: str) -> str:
    return f"{base}_{uuid.uuid4().hex[:10]}"


def _import(client: TestClient, admin: Signed, body: dict[str, Any], *, apply: bool) -> Any:
    return client.post(
        "/api/ontology/import",
        json=body,
        params={"dry_run": "false" if apply else "true"},
        headers=admin.headers,
    )


def _kind(
    client: TestClient,
    admin: Signed,
    part: str,
    *,
    effectivity: bool = False,
    acyclic: bool = True,
    quantity: bool = True,
) -> str:
    """부품끼리의 「구성」 관계 — 관계 속성 qty(숫자) · memo(글자), 수량 칸 qty."""
    slug = _uniq("bom")
    made = _import(
        client,
        admin,
        {
            "relation_types": [
                {
                    "slug": slug,
                    "label": "구성",
                    "inverse_label": "구성품",
                    "transitive": acyclic,
                    "acyclic": acyclic,
                    "effectivity": effectivity,
                    "src_type_slugs": [part],
                    "dst_type_slugs": [part],
                    "properties": [
                        {"key": "qty", "label": "수량", "data_type": "number"},
                        {"key": "memo", "label": "메모", "data_type": "text"},
                    ],
                }
            ]
        },
        apply=True,
    )
    assert made.status_code == 200, made.text
    if quantity:
        got = client.patch(
            f"/api/ontology/relation-types/{slug}",
            json={"quantity_key": "qty"},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        assert got.json()["quantity_key"] == "qty"
    return slug


def _part(client: TestClient, admin: Signed) -> str:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(client, admin, part, key="rel", label="신뢰도", data_type="number")
    return part


def _tree(
    client: TestClient, admin: Signed, part: str, kind: str, rollups: list[dict[str, Any]]
) -> None:
    got = client.patch(
        f"/api/ontology/types/{part}",
        json={"list_view": {"tree": {"relation": kind, "parent": "dst"}, "rollups": rollups}},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text


def _world(
    client: TestClient,
    admin: Signed,
    edges: list[tuple[str, str, Any]],
    *,
    props: dict[str, dict[str, Any]] | None = None,
    rollups: list[dict[str, Any]] | None = None,
    effectivity: bool = False,
    acyclic: bool = True,
) -> World:
    """(자식, 부모, 수량) 선들로 BOM 하나를 세운다 — 수량이 None 이면 칸을 비운다."""
    part = _part(client, admin)
    kind = _kind(client, admin, part, effectivity=effectivity, acyclic=acyclic)
    _tree(client, admin, part, kind, rollups or [])
    names = sorted({name for edge in edges for name in edge[:2]})
    ids = {
        name: _make_object(
            client, admin, part, label=name, key=name, properties=(props or {}).get(name, {})
        )["id"]
        for name in names
    }
    for child, parent, qty in edges:
        _connect(client, admin, World(part, kind, ids), child, parent, qty)
    return World(part, kind, ids)


def _connect(
    client: TestClient,
    admin: Signed,
    world: World,
    child: str,
    parent: str,
    qty: Any,
    **period: Any,
) -> None:
    properties = {} if qty is None else {"qty": qty}
    got = _link(
        client,
        admin,
        world.part,
        world.ids[child],
        world.kind,
        world.ids[parent],
        properties=properties,
        **period,
    )
    assert got.status_code == 201, got.text


SHARED = [("A", "P", 2), ("B", "P", 1), ("S", "A", 3), ("S", "B", 5), ("BOLT", "S", 4)]


def _explode(
    client: TestClient, who: Signed, world: World, name: str, **params: Any
) -> dict[str, Any]:
    got = client.get(
        f"/api/objects/{world.part}/{world.ids[name]}/explode",
        params=params,
        headers=who.headers,
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def _by_label(lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {one["label"]: one for one in lines}


# --- 곱과 더함 -----------------------------------------------------------------


def test_수량을_경로_따라_곱한다(client: TestClient, admin: Signed) -> None:
    """2 곱하기 3 곱하기 4 = 24 — 제품 하나에 볼트 24개."""
    world = _world(client, admin, [("A", "P", 2), ("S", "A", 3), ("BOLT", "S", 4)])
    found = _explode(client, admin, world, "P")
    assert found["mode"] == "summary"
    assert found["quantity_key"] == "qty" and found["quantity_label"] == "수량"
    assert found["root"]["label"] == "P"
    rows = _by_label(found["lines"])
    assert [one["label"] for one in found["lines"]] == ["A", "S", "BOLT"]
    assert rows["BOLT"]["total"] == 24
    assert (rows["A"]["total"], rows["S"]["total"]) == (2, 6)
    assert rows["BOLT"]["level"] == 3 and rows["BOLT"]["paths"] == 1
    assert found["totals"] == {
        "objects": 3,
        "lines": 3,
        "lines_total": 3,
        "links": 3,
        "missing": 0,
        "hidden": 0,
        "deleted": 0,
    }
    assert found["truncated"] is False

    indented = _explode(client, admin, world, "P", mode="indented")
    assert [
        (one["level"], one["label"], one["quantity"], one["total"])
        for one in indented["lines"]
    ] == [(1, "A", 2, 2), (2, "S", 3, 6), (3, "BOLT", 4, 24)]


def test_같은_하위_조립품이_두_경로로_오면_더한다(client: TestClient, admin: Signed) -> None:
    """S 를 A 와 B 가 함께 쓴다 — 총수량은 경로마다의 곱을 더한 것, 경로 수는 둘."""
    world = _world(client, admin, SHARED)
    rows = _by_label(_explode(client, admin, world, "P")["lines"])
    assert rows["S"]["total"] == 2 * 3 + 1 * 5
    assert rows["BOLT"]["total"] == (2 * 3 + 1 * 5) * 4 == 44
    assert rows["BOLT"]["paths"] == 2 and rows["S"]["paths"] == 2
    assert (rows["BOLT"]["level"], rows["BOLT"]["depth_max"]) == (3, 3)

    # 단계별은 경로마다 한 줄 — 형제는 이름순(트리 화면과 같다), 공유 서브는 두 번.
    indented = _explode(client, admin, world, "P", mode="indented")
    assert [(one["level"], one["label"], one["total"]) for one in indented["lines"]] == [
        (1, "A", 2),
        (2, "S", 6),
        (3, "BOLT", 24),
        (1, "B", 1),
        (2, "S", 5),
        (3, "BOLT", 20),
    ]
    assert indented["totals"]["lines_total"] == 6 and indented["totals"]["links"] == 5


def test_수량이_비면_1로_세고_말한다_0은_0이다(client: TestClient, admin: Signed) -> None:
    world = _world(
        client, admin, [("A", "P", None), ("S", "A", 3), ("Z", "P", 0), ("ZZ", "Z", 7)]
    )
    found = _explode(client, admin, world, "P")
    rows = _by_label(found["lines"])
    assert rows["A"]["total"] == 1 and rows["S"]["total"] == 3
    assert rows["A"]["missing"] == 1 and rows["S"]["missing"] == 0
    assert rows["Z"]["total"] == 0 and rows["ZZ"]["total"] == 0
    assert found["totals"]["missing"] == 1

    lines = _by_label(_explode(client, admin, world, "P", mode="indented")["lines"])
    assert lines["A"]["quantity"] is None and lines["A"]["quantity_missing"] is True
    assert lines["S"]["quantity_missing"] is False


def test_수량_칸이_없는_종류는_모두_1이고_수량_없음으로_세지_않는다(
    client: TestClient, admin: Signed
) -> None:
    part = _part(client, admin)
    kind = _kind(client, admin, part, quantity=False)
    _tree(client, admin, part, kind, [])
    world = World(
        part,
        kind,
        {n: _make_object(client, admin, part, label=n)["id"] for n in ("P", "A", "S")},
    )
    _connect(client, admin, world, "A", "P", 2)
    _connect(client, admin, world, "S", "A", 3)
    found = _explode(client, admin, world, "P")
    assert found["quantity_key"] == ""
    assert {one["label"]: one["total"] for one in found["lines"]} == {"A": 1, "S": 1}
    assert found["totals"]["missing"] == 0


def test_기준일을_바꾸면_총수량이_바뀐다(client: TestClient, admin: Signed) -> None:
    """유효 기간을 받는 종류 — 3월 1일부터 A 가 2개에서 5개로(같은 두 객체의 선 둘)."""
    world = _world(client, admin, [("S", "A", 3)], effectivity=True)
    world.ids["P"] = _make_object(client, admin, world.part, label="P", key="P")["id"]
    _connect(client, admin, world, "A", "P", 2, valid_to=MAR)
    _connect(client, admin, world, "A", "P", 5, valid_from=MAR)

    feb = _by_label(_explode(client, admin, world, "P", as_of=FEB)["lines"])
    apr = _by_label(_explode(client, admin, world, "P", as_of=APR)["lines"])
    assert (feb["A"]["total"], feb["S"]["total"]) == (2, 6)
    assert (apr["A"]["total"], apr["S"]["total"]) == (5, 15)
    assert _explode(client, admin, world, "P", as_of=FEB)["as_of"] == FEB


# --- 역전개 ----------------------------------------------------------------------


def test_어디에_쓰였나는_맨_위_조상마다_총수량을_준다(
    client: TestClient, admin: Signed
) -> None:
    """BOLT 에서 위로 — 제품 P 하나에 44개, S 를 직접 7개 쓰는 Q 하나에 28개."""
    world = _world(client, admin, [*SHARED, ("S", "Q", 7)])
    found = _explode(client, admin, world, "BOLT", mode="where_used")
    assert found["root"]["label"] == "BOLT"
    assert [(one["label"], one["total"], one["paths"]) for one in found["tops"]] == [
        ("P", 44, 2),
        ("Q", 28, 1),
    ]
    # 위로 올라가는 줄 — 바로 위 부모와 그 선의 수량(부모 하나에 아래 것 몇 개), 누적.
    assert [
        (one["level"], one["label"], one["quantity"], one["total"]) for one in found["lines"]
    ] == [
        (1, "S", 4, 4),
        (2, "A", 3, 12),
        (3, "P", 2, 24),
        (2, "B", 5, 20),
        (3, "P", 1, 20),
        (2, "Q", 7, 28),
    ]


# --- 상한 ------------------------------------------------------------------------


def test_단계별은_상한에서_자르고_전체_줄_수를_말한다(
    client: TestClient, admin: Signed
) -> None:
    world = _world(client, admin, SHARED)
    found = _explode(client, admin, world, "P", mode="indented", limit=4)
    assert len(found["lines"]) == 4 and found["truncated"] is True
    assert found["totals"]["lines"] == 4 and found["totals"]["lines_total"] == 6
    # 정리는 객체마다 한 줄 — 같은 상한 안이면 안 잘린다.
    summary = _explode(client, admin, world, "P", limit=4)
    assert summary["truncated"] is False and summary["totals"]["lines_total"] == 4

    over = client.get(
        f"/api/objects/{world.part}/{world.ids['P']}/explode",
        params={"limit": 5001},
        headers=admin.headers,
    )
    assert over.status_code == 422


# --- 보이는 범위 ------------------------------------------------------------------


def test_남의_부서_객체는_안_보이지만_그_아래는_센다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    db: Session,
    department_scope: None,
) -> None:
    """조회를 부서로 좁힌 설치 — P(내 부서) ← H(남의 부서, 2개) ← BOLT(내 부서, 3개). 멤버의
    정리에는 H 가 없고 BOLT 는 6개, 단계별에는 H 자리를 가린 줄이 지킨다. 롤업도 같은 뜻."""
    other = Workspace(slug=f"other-{uuid.uuid4().hex[:8]}", name="남의 부서")
    db.add(other)
    db.commit()
    part = _part(client, admin)
    kind = _kind(client, admin, part)
    _tree(
        client,
        admin,
        part,
        kind,
        [
            {"property": "weight", "fn": "sum"},
            {"property": "weight", "fn": "sum", "weighted": True, "label": "총 무게"},
        ],
    )
    ids = {
        "P": _make_object(client, admin, part, label="P")["id"],
        "H": _make_object(
            client, admin, part, label="H", workspace_slug=other.slug, properties={"weight": 9}
        )["id"],
        "BOLT": _make_object(client, admin, part, label="BOLT", properties={"weight": 0.5})[
            "id"
        ],
    }
    world = World(part, kind, ids)
    _connect(client, admin, world, "H", "P", 2)
    _connect(client, admin, world, "BOLT", "H", 3)

    seen = _explode(client, member, world, "P")
    assert [(one["label"], one["total"]) for one in seen["lines"]] == [("BOLT", 6)]
    assert seen["totals"]["hidden"] == 1 and seen["totals"]["objects"] == 1

    lines = _explode(client, member, world, "P", mode="indented")["lines"]
    assert [(one["level"], one["label"], one["id"]) for one in lines] == [
        (1, "(볼 수 없는 객체)", None),
        (2, "BOLT", ids["BOLT"]),
    ]
    assert lines[0]["key"] is None and lines[0]["hidden"] is True

    # 관리자는 다 본다.
    assert [one["label"] for one in _explode(client, admin, world, "P")["lines"]] == [
        "H",
        "BOLT",
    ]

    rollup = client.get(
        f"/api/objects/{part}/{ids['P']}/rollup", headers=member.headers
    ).json()
    assert [(one["value"], one["descendants"]) for one in rollup] == [(0.5, 1), (3.0, 1)]

    # 지운 객체도 지나간다(하위 포함 · 롤업과 같다) — H 를 지워도 BOLT 는 6개.
    db.execute(
        update(ObjectInstance)
        .where(ObjectInstance.id == uuid.UUID(ids["H"]))
        .values(deleted_at=datetime.now(UTC))
    )
    db.commit()
    gone = _explode(client, admin, world, "P")
    assert [(one["label"], one["total"]) for one in gone["lines"]] == [("BOLT", 6)]
    assert gone["totals"]["deleted"] == 1
    masked = _explode(client, admin, world, "P", mode="indented")["lines"][0]
    assert (masked["label"], masked["deleted"], masked["id"]) == ("(삭제된 객체)", True, None)


# --- 순환 ------------------------------------------------------------------------


def test_순환이면_어디서_돌았는지_말한다(client: TestClient, admin: Signed) -> None:
    """순환을 막지 않는 종류로 트리를 정한 타입 — 수량을 곱하면 끝이 없다."""
    world = _world(
        client,
        admin,
        [("B", "A", 2), ("C", "B", 3), ("A", "C", 4)],
        acyclic=False,
        props={"B": {"weight": 1}, "C": {"weight": 2}},
        rollups=[
            {"property": "weight", "fn": "sum"},
            {"property": "weight", "fn": "sum", "weighted": True, "label": "총 무게"},
        ],
    )
    got = client.get(
        f"/api/objects/{world.part}/{world.ids['A']}/explode", headers=admin.headers
    )
    assert got.status_code == 409, got.text
    error = got.json()["error"]
    assert error["code"].endswith("OBJECTS-0130")
    assert "A → B → C → A" in error["message"]

    # 롤업은 수량 반영만 값 없이 까닭을 단다 — 나머지는 예전처럼 센다.
    rollup = client.get(
        f"/api/objects/{world.part}/{world.ids['A']}/rollup", headers=admin.headers
    ).json()
    assert rollup[0]["value"] == 3 and rollup[0]["note"] == ""
    assert rollup[1]["value"] is None and "순환" in rollup[1]["note"]


def test_트리가_없는_타입은_전개를_거절한다(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    one = _make_object(client, admin, part, label="외톨이")
    got = client.get(f"/api/objects/{part}/{one['id']}/explode", headers=admin.headers)
    assert got.status_code == 409
    assert got.json()["error"]["code"].endswith("OBJECTS-0131")


# --- 수량 반영 롤업 ----------------------------------------------------------------


def test_수량_반영_롤업(client: TestClient, admin: Signed) -> None:
    """A(무게 10) · B(20)는 중간 조립품, S(1) · BOLT(0.5). 총수량 A 2 · B 1 · S 11 · BOLT 11
    (BOLT 선은 수량이 비어 1개로 센다)."""
    rollups: list[dict[str, Any]] = [
        {"property": "weight", "fn": "sum"},
        {"property": "weight", "fn": "sum", "weighted": True},
        {"property": "weight", "fn": "sum", "weighted": True, "leaves_only": True},
        {"property": "weight", "fn": "sum", "leaves_only": True},
        {"property": "weight", "fn": "count", "weighted": True},
        {"property": "weight", "fn": "avg", "weighted": True},
        {"property": "weight", "fn": "min", "weighted": True},
        {"property": "rel", "fn": "product"},
        {"property": "rel", "fn": "product", "weighted": True},
    ]
    world = _world(
        client,
        admin,
        [*SHARED[:4], ("BOLT", "S", None)],
        props={
            "A": {"weight": 10},
            "B": {"weight": 20},
            "S": {"weight": 1, "rel": 0.999},
            "BOLT": {"weight": 0.5, "rel": 0.99},
        },
        rollups=rollups,
    )
    got = client.get(
        f"/api/objects/{world.part}/{world.ids['P']}/rollup", headers=admin.headers
    )
    assert got.status_code == 200, got.text
    rows = got.json()
    # BOLT 선의 수량이 비어 1개로 셌다 — BOLT 총수량 11.
    weighted_sum = 10 * 2 + 20 * 1 + 1 * 11 + 0.5 * 11
    assert rows[0]["value"] == 31.5 and rows[0]["weighted"] is False
    assert rows[1]["value"] == weighted_sum and rows[1]["quantity_missing"] == 1
    assert rows[2]["value"] == 0.5 * 11 and rows[2]["descendants"] == 1
    assert rows[2]["leaves_only"] is True
    assert rows[3]["value"] == 0.5 and rows[3]["descendants"] == 1
    assert rows[4]["value"] == 2 + 1 + 11 + 11
    assert math.isclose(rows[5]["value"], weighted_sum / 25)
    assert rows[6]["value"] == 0.5
    assert math.isclose(rows[7]["value"], 0.999 * 0.99)
    assert math.isclose(rows[8]["value"], 0.999**11 * 0.99**11)
    assert rows[7]["label"] == "신뢰도 곱"
    assert rows[0]["quantity_missing"] == 0


def test_롤업_스펙은_새_칸을_검사한다(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    kind = _kind(client, admin, part)
    bad = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [{"property": "weight", "fn": "sum", "weighted": "yes"}],
            }
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422
    assert "weighted" in bad.json()["error"]["message"]
    unknown = client.patch(
        f"/api/ontology/types/{part}",
        json={
            "list_view": {
                "tree": {"relation": kind, "parent": "dst"},
                "rollups": [{"property": "weight", "fn": "geomean"}],
            }
        },
        headers=admin.headers,
    )
    assert unknown.status_code == 422 and "product" in unknown.json()["error"]["message"]


# --- 수량 칸 ----------------------------------------------------------------------


def test_수량_칸은_그_종류의_숫자_관계_속성만_된다(client: TestClient, admin: Signed) -> None:
    part = _part(client, admin)
    kind = _kind(client, admin, part, quantity=False)

    def patch(key: str) -> Any:
        return client.patch(
            f"/api/ontology/relation-types/{kind}",
            json={"quantity_key": key},
            headers=admin.headers,
        )

    missing = patch("nope")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"].endswith("ONTOLOGY-0096")
    text_field = patch("memo")
    assert text_field.status_code == 422
    assert "숫자 관계 속성" in text_field.json()["error"]["message"]

    ok = patch("qty")
    assert ok.status_code == 200 and ok.json()["quantity_key"] == "qty"
    listed = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    mine = next(one for one in listed if one["slug"] == kind)
    assert mine["quantity_key"] == "qty"
    assert {(one["key"], one["data_type"]) for one in mine["properties"]} == {
        ("qty", "number"),
        ("memo", "text"),
    }

    # 만들 때는 관계 속성이 아직 없다 — 수량 칸을 적으면 「없는 칸」.
    fresh = client.post(
        "/api/ontology/relation-types",
        json={"slug": _uniq("bom"), "label": "구성", "quantity_key": "qty"},
        headers=admin.headers,
    )
    assert fresh.status_code == 422

    cleared = patch("")
    assert cleared.status_code == 200 and cleared.json()["quantity_key"] == ""


def test_수량_칸인_속성은_숫자_아닌_것으로_못_바꾼다(
    client: TestClient, admin: Signed
) -> None:
    """정의 가져오기가 관계 속성을 바꾸는 유일한 길이다 — 계획에서 막는다(적용 전에)."""
    part = _part(client, admin)
    kind = _kind(client, admin, part)

    def plan(prop: dict[str, Any], **extra: Any) -> dict[str, Any]:
        got = _import(
            client,
            admin,
            {
                "relation_types": [
                    {"slug": kind, "label": "구성", "properties": [prop], **extra}
                ]
            },
            apply=False,
        )
        assert got.status_code == 200, got.text
        return dict(got.json())

    multi = plan({"key": "qty", "label": "수량", "data_type": "number", "multi": True})
    assert any("수량 칸이라" in one for one in multi["errors"]), multi["errors"]
    retyped = plan({"key": "qty", "label": "수량", "data_type": "text"})
    assert any("수량 칸이라" in one for one in retyped["errors"]), retyped["errors"]

    # 파일이 수량 칸을 글자 칸으로 정하면 그 말로 거절한다.
    to_text = plan({"key": "memo", "label": "메모"}, quantity_key="memo")
    assert any("숫자 관계 속성이어야" in one for one in to_text["errors"]), to_text["errors"]
    # 같은 파일이 새 숫자 칸을 만들며 수량 칸으로 정하는 것은 된다.
    new_one = plan({"key": "n", "label": "개수", "data_type": "number"}, quantity_key="n")
    assert new_one["errors"] == []


def test_수량_칸은_내보내기_가져오기_스냅샷_카탈로그로_왕복한다(
    client: TestClient, admin: Signed
) -> None:
    part = _part(client, admin)
    kind = _kind(client, admin, part)
    got = client.get("/api/ontology/export?format=json", headers=admin.headers)
    assert got.status_code == 200
    body = json.loads(got.content)
    mine = next(one for one in body["relation_types"] if one["slug"] == kind)
    assert mine["quantity_key"] == "qty"

    # 비운 뒤 내보낸 파일을 다시 넣으면 수량 칸이 돌아온다.
    assert (
        client.patch(
            f"/api/ontology/relation-types/{kind}",
            json={"quantity_key": ""},
            headers=admin.headers,
        ).status_code
        == 200
    )
    again = _import(client, admin, {"relation_types": [mine]}, apply=True)
    assert again.status_code == 200, again.text
    listed = client.get("/api/ontology/relation-types", headers=admin.headers).json()
    assert next(one for one in listed if one["slug"] == kind)["quantity_key"] == "qty"

    # 정의 엑셀의 관계 종류 표에도 실린다.
    from openpyxl import load_workbook

    book = load_workbook(
        io.BytesIO(
            client.get("/api/ontology/export?format=xlsx", headers=admin.headers).content
        )
    )
    sheet = list(book["관계 종류"].values)
    header = list(sheet[0])
    line = next(row for row in sheet[1:] if row[0] == kind)
    assert line[header.index("수량 칸")] == "qty"


def test_전개는_파일로도_받는다(client: TestClient, admin: Signed) -> None:
    world = _world(client, admin, SHARED)
    got = client.get(
        f"/api/objects/{world.part}/{world.ids['P']}/explode/export",
        params={"mode": "indented"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("text/csv")
    assert got.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(got.content.decode("utf-8-sig"))))
    assert rows[0] == ["단계", "식별자", "이름", "타입", "수량", "누적 수량"]
    assert rows[3] == ["3", "BOLT", "    BOLT", "부품", "4", "24"]
    assert len(rows) == 7

    summary = client.get(
        f"/api/objects/{world.part}/{world.ids['P']}/explode/export",
        headers=admin.headers,
    )
    table = list(csv.reader(io.StringIO(summary.content.decode("utf-8-sig"))))
    assert table[0][:5] == ["가장 얕은 단계", "식별자", "이름", "타입", "총수량"]
    assert ["3", "BOLT", "BOLT", "부품", "44", "3", "2", "0"] in table
