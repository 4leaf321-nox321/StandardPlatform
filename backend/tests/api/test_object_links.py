"""목록 · 통계의 화면 주소 — **답과 같은 조건의 목록을 사람이 눌러서 연다.**

바깥 AI 가 MCP 로 받은 목록 · 수에는 같은 조건의 화면 주소(`link`)가 실린다. 그 주소는 API 의
인자를 **화면이 읽는 꼴**로 옮긴 것이다 — `p.<칸>` 은 `f.<칸>.eq`, 연도를 안 줬으면 `year=all`
(화면의 기본은 올해다), 쪽 · 이어진 칸은 버리고, 화면이 주소로 못 받는 것(트리의 가지,
인터페이스 목록의 상태 · 기준일 · 통계 설정)은 `link_missing` 에 이름을 적는다. 말없이 빼면
사람은 넓어진 화면을 답과 같은 것으로 읽는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from fastapi.testclient import TestClient

from app.modules.objects.listlinks import list_link
from tests.api.conftest import Signed
from tests.api.test_interfaces import _add_common, _make_interface
from tests.api.test_ontology import (
    _link,
    _make_object,
    _make_property,
    _make_type,
    _tree_type,
)


def _get(
    client: TestClient, who: Signed, path: str, params: list[tuple[str, str]]
) -> dict[str, Any]:
    got = client.get(path, params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _split(link: str) -> tuple[str, list[tuple[str, str]]]:
    """주소 → (경로, 쿼리 쌍) — 화면의 `URLSearchParams` 가 읽는 차례 그대로."""
    parts = urlsplit(link)
    return parts.path, parse_qsl(parts.query, keep_blank_values=True)


def _graded(client: TestClient, admin: Signed, **kw: Any) -> str:
    part = _make_type(client, admin, label="부품", **kw)
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")
    _make_property(client, admin, part, key="memo", label="메모", data_type="text")
    return part


def test_목록의_주소는_화면이_읽는_꼴로_옮긴다(client: TestClient, admin: Signed) -> None:
    part = _graded(client, admin)
    _make_object(client, admin, part, label="볼트 A", properties={"grade": "A", "memo": "m"})
    page = _get(
        client,
        admin,
        f"/api/objects/{part}",
        [
            ("limit", "10"),
            ("offset", "0"),
            ("q", "볼트 A"),
            ("status", "active"),
            ("as_of", "2026-03-31"),
            ("p.grade", "A"),
            ("f.memo.notempty", ""),
        ],
    )
    assert page["total"] == 1
    path, query = _split(page["link"])
    assert path == f"/o/{part}"
    # 쪽은 버리고, `p.` 는 같은 「같음」 조건으로, 연도를 안 줬으니 「전부」.
    assert query == [
        ("q", "볼트 A"),
        ("status", "active"),
        ("as_of", "2026-03-31"),
        ("f.grade.eq", "A"),
        ("f.memo.notempty", ""),
        ("year", "all"),
    ]
    assert page["link_missing"] == []
    # 값은 지표 경보의 링크와 같은 인코딩(빈칸은 `+`)이다.
    assert "q=%EB%B3%BC%ED%8A%B8+A" in page["link"]

    # 연도를 주면 그 해가 실린다.
    dated = _get(client, admin, f"/api/objects/{part}", [("year", "2025")])
    assert _split(dated["link"])[1] == [("year", "2025")]


def test_트리의_가지는_주소로_못_받아_빠졌다고_적는다(
    client: TestClient, admin: Signed
) -> None:
    part, part_of = _tree_type(client, admin)
    top = _make_object(client, admin, part, label="TOP")
    leaf = _make_object(client, admin, part, label="LEAF")
    linked = _link(client, admin, part, leaf["id"], part_of, top["id"])
    assert linked.status_code == 201, linked.text

    page = _get(
        client,
        admin,
        f"/api/objects/{part}",
        [("under", top["id"]), ("deep", "true"), ("q", "LEAF")],
    )
    assert page["total"] == 1
    assert _split(page["link"])[1] == [("q", "LEAF"), ("year", "all")]
    assert page["link_missing"] == ["under", "deep"]

    # 가지 없이 온 「아래 것까지」 는 줄을 안 바꾼다 — 빠졌다고 적지 않는다.
    plain = _get(client, admin, f"/api/objects/{part}", [("deep", "false")])
    assert plain["link_missing"] == []


def test_통계의_주소는_통계를_펼치고_설정을_싣는다(client: TestClient, admin: Signed) -> None:
    part = _graded(client, admin)
    for grade in ("A", "A", "B"):
        _make_object(client, admin, part, label=f"볼트 {grade}", properties={"grade": grade})
    found = _get(
        client,
        admin,
        f"/api/objects/{part}/summary",
        [
            ("group_by", "properties.grade"),
            ("metric", "count"),
            ("order", "key"),
            ("p.memo", "m"),
            ("year", "2026"),
        ],
    )
    path, query = _split(found["link"])
    assert path == f"/o/{part}"
    assert query == [
        ("f.memo.eq", "m"),
        ("year", "2026"),
        ("group", "1"),
        ("group_by", "properties.grade"),
        ("metric", "count"),
        ("order", "key"),
    ]
    assert found["link_missing"] == []

    # 설정을 안 주면 `group=1` 만 — 화면의 기본(상태별 건수, 큰 값부터)이 API 의 기본과 같다.
    bare = _get(client, admin, f"/api/objects/{part}/summary", [])
    assert _split(bare["link"])[1] == [("year", "all"), ("group", "1")]


def test_인터페이스는_화면이_읽는_것만_싣고_나머지는_빠졌다고_적는다(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin, label="설비")
    _add_common(
        client, admin, iface, key="maker", label="제조사", data_type="text"
    ).raise_for_status()
    tester = _make_type(client, admin, label="시험장비", interface_slugs=[iface])
    _make_object(client, admin, tester, label="시험기 1", properties={"maker": "A사"})

    page = _get(
        client,
        admin,
        f"/api/objects/{iface}",
        [
            ("types", tester),
            ("status", "active"),
            ("as_of", "2026-03-31"),
            ("f.maker.eq", "A사"),
        ],
    )
    assert page["total"] == 1
    assert _split(page["link"])[1] == [
        ("types", tester),
        ("f.maker.eq", "A사"),
        ("year", "all"),
    ]
    assert page["link_missing"] == ["status", "as_of"]

    # 통계 — 인터페이스 목록 화면은 통계 설정을 주소로 안 받는다(펼치지도 않는다).
    found = _get(
        client,
        admin,
        f"/api/objects/{iface}/summary",
        [("group_by", "type"), ("q", "시험기")],
    )
    assert _split(found["link"])[1] == [("q", "시험기"), ("year", "all")]
    assert found["link_missing"] == ["group_by"]


def test_투영_타입은_검색어만_싣는다(client: TestClient, admin: Signed) -> None:
    """원 표를 비추는 타입 — 목록 API 가 검색어만 쓰므로 주소도 검색어뿐이다."""
    dept = _make_type(
        client, admin, label="부서", kind_class="system", system_source="workspace"
    )
    page = _get(
        client,
        admin,
        f"/api/objects/{dept}",
        [("q", admin.workspace), ("status", "active"), ("p.x", "y")],
    )
    assert page["link"] == f"/o/{dept}?q={admin.workspace}"
    assert page["link_missing"] == []
    assert _get(client, admin, f"/api/objects/{dept}", [])["link"] == f"/o/{dept}"


def test_주소의_쿼리로_다시_물으면_같은_수가_나온다(client: TestClient, admin: Signed) -> None:
    """화면은 주소의 조건을 그대로 목록 API 에 보낸다 — `year=all` 만 「연도 없음」 으로
    읽는다."""
    part = _graded(client, admin, temporal_kind="lifecycle")
    for label, grade, start in (
        ("옛 볼트", "A", 2010),
        ("새 볼트", "A", 2020),
        ("새 너트", "B", 2020),
        ("옛 너트", "A", 2011),
    ):
        _make_object(
            client,
            admin,
            part,
            label=label,
            properties={"grade": grade, "memo": "m"},
            valid_from_year=start,
            valid_to_year=start + 3 if start < 2015 else None,
        )
    asked = [("p.grade", "A"), ("f.memo.eq", "m"), ("q", "볼트"), ("limit", "1")]
    first = _get(client, admin, f"/api/objects/{part}", asked)
    assert first["total"] == 2  # 연도를 안 줬다 — 몇 해치 전부
    again = [pair for pair in _split(first["link"])[1] if pair != ("year", "all")]
    assert _get(client, admin, f"/api/objects/{part}", again)["total"] == first["total"]

    # 해를 준 물음도 — 주소에 그 해가 실려 같은 수가 나온다.
    dated = _get(client, admin, f"/api/objects/{part}", [*asked, ("year", "2012")])
    assert dated["total"] == 1
    replay = _split(dated["link"])[1]
    assert ("year", "2012") in replay
    assert _get(client, admin, f"/api/objects/{part}", replay)["total"] == 1


def _def(key: str, data_type: str = "text", *, multi: bool = False) -> Any:
    """속성 정의 대신 — `list_link` 가 보는 세 칸만."""
    return SimpleNamespace(key=key, data_type=data_type, multi=multi)


def test_같음은_뜻이_같은_칸만_조건으로_옮긴다() -> None:
    """2026-11-07 검토 — API 의 `p.` 는 글자 비교, 조건의 `eq` 는 칸의 종류대로 견준다. 여러 값
    · 숫자 · 참/거짓 · 고정 칸 이름 · 정의 없는 칸은 옮기면 링크로 연 화면의 수가 답과
    달라진다."""
    defs = [
        _def("grade"),
        _def("tags", multi=True),
        _def("price", "number"),
        _def("flag", "bool"),
        _def("kind", "enum"),
    ]
    asked = [
        ("p.grade", "A"),
        ("p.tags", "A"),
        ("p.price", "1.50"),
        ("p.flag", "yes"),
        ("p.kind", "B"),
        ("p.status", "active"),
        ("p.nothere", "x"),
        ("p.ref.x", "y"),
    ]
    link, missing = list_link("part", asked, interface=False, defs=defs)
    assert link is not None
    assert _split(link)[1] == [("f.grade.eq", "A"), ("f.kind.eq", "B"), ("year", "all")]
    assert missing == ["p.tags", "p.price", "p.flag", "p.status", "p.nothere", "p.ref.x"]
    # 정의를 모르면 옮기지 않는다 — 뜻이 같은지 알 수 없다.
    assert list_link("part", [("p.grade", "A")], interface=False)[1] == ["p.grade"]


def test_같은_키는_마지막_것_조건은_차례대로() -> None:
    """DB 없이 — API 와 같게 하나만 받는 인자는 마지막 것, 조건은 전부 · 차례 그대로."""
    link, missing = list_link(
        "part",
        [
            ("columns", "out.supplied.label"),
            ("p.grade", "A"),
            ("nf1.grade.eq", "C"),
            ("fe1.out.supplied.label.eq", "갑"),
            ("p.grade", "B"),
            ("q", "가"),
            ("q", "나"),
            ("offset", "50"),
            ("f.region.in", "영남|호남"),
            ("view", "v1"),
        ],
        interface=False,
        defs=[_def("grade")],
    )
    assert link is not None
    assert _split(link)[1] == [
        ("f.grade.eq", "B"),
        ("nf1.grade.eq", "C"),
        ("fe1.out.supplied.label.eq", "갑"),
        ("q", "나"),
        ("f.region.in", "영남|호남"),
        ("year", "all"),
    ]
    assert missing == []
    # 목록 API 는 통계 설정을 안 읽는다 — 목록의 주소에서는 버린다(줄을 안 바꾼다).
    assert list_link("part", [("group_by", "status")], interface=False) == (
        "/o/part?year=all",
        [],
    )
