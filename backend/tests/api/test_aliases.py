"""별칭 — **같은 것을 다르게 불러도 같은 것으로 풀린다.**

못 찾은 사람은 새로 만들고, 그러면 같은 것이 둘이 된다. 찾기·참조 풀이·파일·합치기가 전부
별칭을 봐야 그 일이 안 생긴다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed, export_file
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _aliases(
    client: TestClient, who: Signed, type_slug: str, object_id: str, values: list[str]
) -> Any:
    return client.put(
        f"/api/objects/{type_slug}/{object_id}/aliases",
        json={"aliases": values},
        headers=who.headers,
    )


def test_별칭을_붙이면_찾기에_걸리고_이력이_남는다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    got = _aliases(client, admin, vendor, ansys["id"], ["앤시스", " ANSYS Inc. ", "앤시스"])
    assert got.status_code == 200, got.text
    assert got.json()["aliases"] == ["앤시스", "ANSYS Inc."]

    found = client.get(f"/api/objects/{vendor}", params={"q": "앤시"}, headers=admin.headers)
    assert [row["label"] for row in found.json()["items"]] == ["Ansys"]
    graph = client.get("/api/graph/search", params={"q": "ANSYS Inc"}, headers=admin.headers)
    assert any(hit["id"] == ansys["id"] for hit in graph.json())

    history = client.get(
        f"/api/objects/{vendor}/{ansys['id']}/history", headers=admin.headers
    ).json()
    assert history[0]["changes"]["aliases"]["after"] == ["앤시스", "ANSYS Inc."]


def test_같은_타입의_다른_객체가_쓰는_별칭은_거절한다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    other = _make_object(client, admin, vendor, label="Altair")
    assert _aliases(client, admin, vendor, ansys["id"], ["Anssys"]).status_code == 200
    denied = _aliases(client, admin, vendor, other["id"], ["anssys"])
    assert denied.status_code == 422
    assert "Ansys의 별칭" in denied.json()["error"]["message"]


def test_파일의_참조가_별칭으로_풀린다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="required")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    ansys = _make_object(client, admin, vendor, label="Ansys")
    _aliases(client, admin, vendor, ansys["id"], ["ANSYS Inc."])

    done = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [
                {
                    "key": "P-1",
                    "label": "볼트",
                    "vendor": "ansys inc.",
                    "aliases": "bolt;볼트M6",
                }
            ],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    listed = client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"]
    bolt = listed[0]
    assert bolt["properties"]["vendor"] == ansys["id"]
    assert bolt["aliases"] == ["bolt", "볼트M6"]

    # 내보내면 별칭 열이 있고, 그대로 다시 넣으면 「그대로」 다.
    exported = export_file(client, admin, f"{part}/export", {"format": "json"}).json()["rows"]
    assert exported[0]["aliases"] == "bolt;볼트M6"
    again = client.post(
        f"/api/objects/{part}/import-rows",
        json={"rows": exported, "workspace_slug": admin.workspace, "apply": False},
        headers=admin.headers,
    ).json()
    assert [r["action"] for r in again["rows"]] == ["unchanged"]

    # 관계 파일의 끝점도 별칭으로.
    from tests.api.test_ontology import _make_relation

    kind = _make_relation(
        client,
        admin,
        "supplied_by",
        label="공급받음",
        src_type_slugs=[part],
        dst_type_slugs=[vendor],
    )
    linked = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={"rows": [{"src": "bolt", "relation": kind, "dst": "ANSYS Inc."}], "apply": True},
        headers=admin.headers,
    ).json()
    assert linked["applied"] is True, linked


def test_합치면_지는_쪽_이름이_별칭으로_남는다(client: TestClient, admin: Signed) -> None:
    """같은 표기로 다시 와도 같은 것으로 풀린다 — 중복이 다시 안 생긴다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    dup = _make_object(client, admin, vendor, label="ANSYS Inc.")
    _aliases(client, admin, vendor, dup["id"], ["앤시스"])
    merged = client.post(
        f"/api/objects/{vendor}/{dup['id']}/merge",
        json={"into": ansys["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    winner = client.get(f"/api/objects/{vendor}/{ansys['id']}", headers=admin.headers).json()
    assert set(winner["object"]["aliases"]) == {"앤시스", "ANSYS Inc."}

    # 이제 「ANSYS Inc.」 로 넣으면 새로 만들지 않고 같은 것을 가리킨다.
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    plan = client.post(
        f"/api/objects/{part}/import-rows",
        json={"rows": [{"label": "볼트", "vendor": "ANSYS Inc."}], "apply": True},
        headers=admin.headers,
    ).json()
    assert plan["applied"] is True
    row = client.get(f"/api/objects/{part}", headers=admin.headers).json()["items"][0]
    assert row["properties"]["vendor"] == ansys["id"]


def test_지는_쪽_이름을_다른_객체가_별칭으로_가졌어도_합친다(
    client: TestClient, admin: Signed
) -> None:
    """타입 안에서 별칭 (종류, 값)은 하나뿐이다 — 제3의 객체가 이미 그 이름을 별칭으로
    가졌으면 이긴 쪽 별칭으로 못 남긴다. 예전에는 그대로 넣어 합치기가 500 이었다
    (2026-10-08). 남기지 못한 겹침은 품질 검사가 센다."""
    vendor = _make_type(client, admin, label="공급사", key_policy="optional")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    dup = _make_object(client, admin, vendor, label="ANSYS Inc.")
    other = _make_object(client, admin, vendor, label="앤시스 코리아")
    _aliases(client, admin, vendor, other["id"], ["ANSYS Inc."])
    merged = client.post(
        f"/api/objects/{vendor}/{dup['id']}/merge",
        json={"into": ansys["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text
    winner = client.get(f"/api/objects/{vendor}/{ansys['id']}", headers=admin.headers).json()
    assert "ANSYS Inc." not in winner["object"]["aliases"]


def test_별칭이_다른_객체의_이름과_같으면_품질에_뜬다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label="공급사")
    ansys = _make_object(client, admin, vendor, label="Ansys")
    _make_object(client, admin, vendor, label="Altair")
    _aliases(client, admin, vendor, ansys["id"], ["altair"])
    report = client.get(
        "/api/objects/quality/report", params={"kind": "alias_clash"}, headers=admin.headers
    ).json()
    finding = next(f for f in report["findings"] if f["type_slug"] == vendor)
    assert finding["count"] == 1 and "Altair" in finding["hits"][0]["detail"]


def test_별칭이_다른_객체_이름과_같으면_적재_줄에도_적는다(
    client: TestClient, admin: Signed
) -> None:
    """**조용히 별칭 쪽에 붙는다** — 이름 풀이가 식별자 → 별칭 → 이름 차례이기 때문이다.

    품질 보고서는 같은 사실을 목록으로 보여 주지만, 그것은 **나중에** 보는 자리다. 넣는
    사람은 그 줄에서 알아야 한다 — 어느 쪽을 가리킬 셈이었는지는 그 사람만 안다.
    """
    vendor = _make_type(client, admin, label="공급사", key_policy="required")
    tool = _make_type(client, admin, label="툴", key_policy="required")
    _make_property(
        client,
        admin,
        tool,
        key="vendor",
        label="개발사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    ansys = _make_object(client, admin, vendor, label="Ansys", key="V-001")
    _make_object(client, admin, vendor, label="Altair", key="V-002")
    _aliases(client, admin, vendor, ansys["id"], ["Altair"])

    plan = client.post(
        f"/api/objects/{tool}/import-rows",
        json={"rows": [{"key": "T-1", "label": "툴1", "vendor": "Altair"}]},
        headers=admin.headers,
    ).json()
    row = plan["rows"][0]
    assert row["action"] == "create", plan
    assert "다른 객체의 **이름**이기도" in row["message"], row


def test_기계가_붙인_별칭은_검수_대기로_남고_한_번에_확인한다(
    client: TestClient, admin: Signed
) -> None:
    """**본 것과 안 본 것을 가르는 칸이 없으면 오타 표기까지 정본처럼 쓰인다.**

    적재는 별칭을 수천 개 붙인다. 화면에서 사람이 붙인 것은 붙이는 순간 확인한 것으로 두어야
    이 목록이 읽을 만한 크기로 남는다 — 안 그러면 곧 수천 줄이 되어 아무도 안 읽는다.
    """
    mode = _make_type(client, admin, label="고장 모드", key_policy="required")
    got = client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [
                {
                    "key": "F-1",
                    "label": "박리",
                    # 글자와 **{value, source, note}** 를 섞어 보낸다.
                    "aliases": [
                        "코팅 벗김",
                        {
                            "value": "박리현상",
                            "source": "고장모드 리스트 v3",
                            "note": "옛 표기",
                        },
                    ],
                }
            ],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text

    pending = client.get(f"/api/objects/{mode}/aliases/pending", headers=admin.headers).json()
    assert pending["total"] == 2
    by_value = {one["value"]: one for one in pending["items"]}
    assert by_value["박리현상"]["source"] == "고장모드 리스트 v3"
    assert by_value["박리현상"]["note"] == "옛 표기"
    assert by_value["코팅 벗김"]["source"] == ""

    # 품질 보고서에도 같은 수가 뜬다 — 고치러 가는 자리가 거기다.
    report = client.get(
        "/api/objects/quality/report",
        params={"kind": "alias_pending"},
        headers=admin.headers,
    ).json()
    finding = next(one for one in report["findings"] if one["type_slug"] == mode)
    assert finding["count"] == 2

    # 하나는 확인하고 하나는 지운다 — 둘 다 **한 번에.**
    approved = client.post(
        f"/api/objects/{mode}/aliases/review",
        json={"ids": [by_value["박리현상"]["id"]], "action": "approve"},
        headers=admin.headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["done"] == 1
    removed = client.post(
        f"/api/objects/{mode}/aliases/review",
        json={"ids": [by_value["코팅 벗김"]["id"]], "action": "remove"},
        headers=admin.headers,
    )
    assert removed.status_code == 200, removed.text

    left = client.get(f"/api/objects/{mode}/aliases/pending", headers=admin.headers).json()
    assert left["total"] == 0
    rows = client.get(f"/api/objects/{mode}", headers=admin.headers).json()["items"]
    made = next(one for one in rows if one["key"] == "F-1")
    detail = client.get(f"/api/objects/{mode}/{made['id']}", headers=admin.headers).json()
    # 확인한 것만 남았다 — 지운 것은 이름 풀이에서도 빠진다.
    assert detail["object"]["aliases"] == ["박리현상"]

    # **화면에서 붙인 것은 검수 대기가 아니다.**
    put = client.put(
        f"/api/objects/{mode}/{made['id']}/aliases",
        json={"aliases": ["박리현상", "사람이 붙인 것"]},
        headers=admin.headers,
    )
    assert put.status_code == 200, put.text
    assert (
        client.get(f"/api/objects/{mode}/aliases/pending", headers=admin.headers).json()[
            "total"
        ]
        == 0
    )

    # 외부 식별자는 파일로 받지 않는다 — 동기화가 남기는 것이다.
    denied = client.post(
        f"/api/objects/{mode}/import-rows",
        json={
            "rows": [
                {
                    "key": "F-1",
                    "label": "박리",
                    "aliases": [{"value": "X", "kind": "source:plm"}],
                }
            ]
        },
        headers=admin.headers,
    ).json()
    assert denied["rows"][0]["action"] == "error"
    assert "데이터 소스가 남깁니다" in denied["rows"][0]["message"]
