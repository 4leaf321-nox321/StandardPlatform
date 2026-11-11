"""SPARQL 사본의 흘려 싣기 · 바뀐 것만 고치기(ADR 0071) — **고친 사본 = 지금 DB 를 통째로
내보낸 것**.

바뀐 것만 고치기는 지난 판의 지문과 지금 DB 를 견줘 바뀐 줄만 지우고 다시 넣는다. 한 곳이라도
빠지면 사본이 조용히 어긋난다 — 그래서 바뀌는 꼴마다(만들기 · 값 · 식별자 · 지우기 · 합치기 ·
별칭 · 선 넣고 빼기 · 고정 · 날짜만 지나 유효해진 선) 고친 뒤 사본 **전체**를 통째로 내보낸
것과 견준다. 빈 노드(구체화한 진술)는 이름이 아니라 몸통으로 센다 — 싣기마다 이름을 새로
짓는다.
"""

from __future__ import annotations

import io
import json
import sys
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pyoxigraph import BlankNode, DefaultGraph, RdfFormat, Store
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.objects import edges
from app.modules.objects.models import ObjectRelation
from app.modules.ontology.models import ObjectType
from app.modules.rdf import copyapply, copycli, copyexport, copystate, store
from tests.api.conftest import Signed
from tests.api.test_aliases import _aliases
from tests.api.test_edge_fields import _bom_kind
from tests.api.test_ontology import _link, _make_object, _make_property, _make_type

Shape = tuple[set[tuple[str, str, str]], Counter[frozenset[tuple[str, str]]]]


def _term(node: Any) -> str:
    # 빈 노드는 이름이 싣기마다 다르다 — 목적어 자리의 빈 노드(정의의 목록 따위)는 하나로 센다.
    return "_:b" if isinstance(node, BlankNode) else str(node)


def _shape(quads: Any) -> Shape:
    plain: set[tuple[str, str, str]] = set()
    bodies: dict[str, set[tuple[str, str]]] = {}
    for quad in quads:
        if isinstance(quad.subject, BlankNode):
            bodies.setdefault(quad.subject.value, set()).add(
                (str(quad.predicate), _term(quad.object))
            )
        else:
            plain.add((str(quad.subject), str(quad.predicate), _term(quad.object)))
    return plain, Counter(frozenset(one) for one in bodies.values())


def _copied(root: Path) -> Shape:
    found = Store.read_only(str((root / store.ACTIVE).resolve()))
    return _shape(found.quads_for_pattern(None, None, None, DefaultGraph()))


def _wanted(db: Session, today: date) -> Shape:
    out = io.StringIO()
    schema, _ = copyexport.schema_text(db)
    out.write(schema)
    copyexport.write(db, out, today=today)
    db.rollback()
    # 메모리 저장소에 한 번 실었다 읽는다 — Oxigraph 는 형이 있는 값을 표준 꼴로 둔다
    # (`…25.837590+09:00` → `…25.83759+09:00`). 사본과 같은 꼴로 견준다.
    memory = Store()
    memory.load(input=out.getvalue().encode("utf-8"), format=RdfFormat.N_TRIPLES)
    return _shape(memory.quads_for_pattern(None, None, None, DefaultGraph()))


def _same(root: Path, db: Session, today: date) -> None:
    got, want = _copied(root), _wanted(db, today)
    missing, extra = sorted(want[0] - got[0])[:5], sorted(got[0] - want[0])[:5]
    assert not missing and not extra, f"빠진 줄 {missing} · 남은 줄 {extra}"
    assert got[1] == want[1], (
        f"구체화한 진술: 빠짐 {want[1] - got[1]} · 남음 {got[1] - want[1]}"
    )


def _manifest(root: Path) -> dict[str, Any]:
    found: dict[str, Any] = json.loads(
        ((root / store.ACTIVE).resolve() / store.MANIFEST).read_text(encoding="utf-8")
    )
    return found


@pytest.fixture(autouse=True)
def _no_weekly(monkeypatch: pytest.MonkeyPatch) -> None:
    """시험 날이 통째로 하는 요일이어도 바뀐 것만 고치게."""
    monkeypatch.setattr(get_settings(), "rdf_copy_full_weekday", "")


@pytest.fixture
def world(client: TestClient, admin: Signed) -> dict[str, Any]:
    vendor = _make_type(client, admin, label="업체", key_policy="optional")
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    product = _make_type(client, admin, label="제품", key_policy="optional")
    kind = _bom_kind(client, admin, product, part)
    ids: dict[str, str] = {}
    for name, slug, key in (
        ("KR", vendor, "KR-1"),
        ("US", vendor, "US-1"),
        ("P1", product, "P-1"),
        ("P2", product, "P-2"),
    ):
        ids[name] = _make_object(client, admin, slug, label=name, key=key)["id"]
    parts: tuple[tuple[str, str | None, dict[str, Any]], ...] = (
        ("X", "X-1", {"weight": 2.5, "vendor": ids["KR"]}),
        ("Y", "Y-1", {"weight": 3}),
        ("Z", None, {}),
        ("W", "W-1", {"vendor": ids["US"]}),
    )
    for name, part_key, props in parts:
        made = _make_object(client, admin, part, label=name, key=part_key, properties=props)
        ids[name] = made["id"]
    assert _aliases(client, admin, part, ids["X"], ["엑스"]).status_code == 200
    assert _aliases(client, admin, part, ids["Y"], ["와이", "Y자"]).status_code == 200
    soon = (edges.today() + timedelta(days=10)).isoformat()
    edges_made: dict[str, str] = {}
    for name, src, dst, extra in (
        ("P1X", "P1", "X", {}),
        ("P1Y", "P1", "Y", {"valid_from": "2026-01-01"}),
        ("P1Z", "P1", "Z", {"valid_from": soon}),
        ("P2X", "P2", "X", {"valid_to": soon}),
        ("P2W", "P2", "W", {}),
    ):
        made = _link(client, admin, product, ids[src], kind, ids[dst], **extra)
        assert made.status_code == 201, made.text
        edges_made[name] = made.json()["relation_id"]
    return {
        "vendor": vendor,
        "part": part,
        "product": product,
        "kind": kind,
        "ids": ids,
        "edges": edges_made,
    }


def test_흘려_실은_사본은_통째로_내보낸_것과_같다(
    db: Session, world: dict[str, Any], tmp_path: Path
) -> None:
    """임시 파일 없이 자식 프로세스가 흘린 것을 받아 싣는다 — 지문 파일이 함께 선다."""
    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "full" and done["reason"] == "지난 판 없음"
    assert not (tmp_path / "import").exists()
    meta = copystate.read_meta((tmp_path / store.ACTIVE).resolve() / copystate.STATE)
    assert meta is not None and meta["format"] == copystate.FORMAT
    assert int(meta["objects"]) == done["objects"]
    _same(tmp_path, db, edges.today())


def test_바뀐_것만_고친_사본은_통째로_내보낸_것과_같다(
    client: TestClient,
    admin: Signed,
    db: Session,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids, part, product, vendor = world["ids"], world["part"], world["product"], world["vendor"]
    assert copycli.load(tmp_path) == 0
    first = (tmp_path / store.ACTIVE).resolve().name

    def patch(slug: str, name: str, **body: Any) -> None:
        got = client.patch(
            f"/api/objects/{slug}/{ids[name]}", json=body, headers=admin.headers
        )
        assert got.status_code == 200, got.text

    # 만들기 — 참조 칸 · 선까지
    ids["N"] = _make_object(
        client, admin, part, label="N", key="N-1", properties={"vendor": ids["US"]}
    )["id"]
    assert _link(client, admin, product, ids["P2"], world["kind"], ids["N"]).status_code == 201
    # 값 · 설명
    patch(part, "X", properties={"weight": 4.25}, description="고친 설명")
    # 식별자 — 가리켜지는 쪽(X 의 참조가 따라가야 한다) · 선의 출발(구체화한 진술까지)
    patch(vendor, "KR", key="KR-9")
    patch(product, "P1", key="P-1b")
    # 지우기 — 들어오는 선과 나가는 참조가 있는 것
    gone = client.delete(
        f"/api/objects/{part}/{ids['W']}", params={"mode": "detach"}, headers=admin.headers
    )
    assert gone.status_code == 204, gone.text
    # 합치기 — Y 를 Z 로(지는 쪽 이름이 별칭이 되고 선이 옮겨 간다)
    merged = client.post(
        f"/api/objects/{part}/{ids['Y']}/merge", json={"into": ids["Z"]}, headers=admin.headers
    )
    assert merged.status_code == 200, merged.text
    # 별칭 바꾸기(지운 것 · 더한 것)
    assert _aliases(client, admin, part, ids["X"], ["엑스2"]).status_code == 200
    # 선 끊기
    cut = client.delete(
        f"/api/objects/{product}/{ids['P1']}/relations/{world['edges']['P1X']}",
        headers=admin.headers,
    )
    assert cut.status_code == 204, cut.text
    # 고정 — `updated_at` 을 그대로 두는 길(`lineage._write_pin` 과 같다)
    db.execute(
        update(ObjectRelation)
        .where(ObjectRelation.id == uuid.UUID(world["edges"]["P2X"]))
        .values(
            pinned_revision="C",
            pinned_at=datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC),
            updated_at=ObjectRelation.updated_at,
        )
    )
    db.commit()

    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "changes", done
    assert done["changed_objects"] >= 6 and done["changed_relations"] >= 3, done
    assert (tmp_path / store.ACTIVE).resolve().name != first
    _same(tmp_path, db, edges.today())

    # 날짜만 지나면 — P1→Z 가 유효해지고 P2→X 가 끝난다(DB 는 그대로).
    later = edges.today() + timedelta(days=15)
    monkeypatch.setattr(edges, "today", lambda: later)
    monkeypatch.setattr(copycli, "FULL_EVERY_DAYS", 100)
    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "changes" and done["changed_relations"] >= 2, done
    _same(tmp_path, db, later)

    # 아무것도 안 바뀌면 — 바꿔 끼우기만(판의 시각이 새로).
    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "changes" and done["changed_objects"] == 0, done
    assert done["changed_relations"] == 0
    _same(tmp_path, db, later)


def test_정의가_바뀌면_그날은_통째로(
    client: TestClient, admin: Signed, db: Session, world: dict[str, Any], tmp_path: Path
) -> None:
    assert copycli.load(tmp_path) == 0
    _make_property(client, admin, world["part"], key="color", label="색", data_type="text")
    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "full" and "정의" in done["reason"], done
    _same(tmp_path, db, edges.today())


def test_화면_배치만_고치면_통째로가_아니다(
    client: TestClient, admin: Signed, db: Session, world: dict[str, Any], tmp_path: Path
) -> None:
    """정의 지문에서 화면 배치 칸(`list_view` 따위)을 뺀다 — 목록 칸을 고칠 때마다 밤이
    통째로가 되지 않게. 사본이 그 칸을 안 읽는 것은 아래 시험이 지킨다."""
    assert copycli.load(tmp_path) == 0
    before = copystate.definitions(db)
    db.rollback()
    db.execute(
        update(ObjectType)
        .where(ObjectType.slug == world["part"])
        .values(list_view={"columns": ["weight"]})
    )
    db.commit()
    assert copystate.definitions(db) == before
    db.rollback()
    assert copycli.load(tmp_path) == 0
    assert _manifest(tmp_path)["mode"] == "changes"


def test_사본은_정의_지문에서_뺀_칸을_읽지_않는다() -> None:
    """뺀 칸을 사본이 읽기 시작하면 그 칸만 고친 날 사본이 낡는다 — 그때는 `UI_ONLY` 에서
    뺀다."""
    root = Path(copyexport.__file__).parent
    for name in ("export.py", "copyexport.py", "copystate.py"):
        source = (root / name).read_text(encoding="utf-8")
        for column in copystate.UI_ONLY - {"created_at", "updated_at"}:
            assert f".{column}" not in source, f"{name} 가 {column} 을 읽는다"


def test_고친_뒤_개수가_어긋나면_그날은_통째로(
    client: TestClient,
    admin: Signed,
    db: Session,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert copycli.load(tmp_path) == 0
    _make_object(client, admin, world["part"], label="새것", key="NEW-1")
    # 넣기를 빼먹는 고장 — 견주기가 잡아 통째로 다시 싣는다.
    monkeypatch.setattr(copyapply.Writer, "add", lambda self, text: None)
    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "full" and "어긋남" in done["reason"], done
    _same(tmp_path, db, edges.today())


def test_바뀐_것이_많으면_통째로(
    client: TestClient,
    admin: Signed,
    world: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert copycli.load(tmp_path) == 0
    _make_object(client, admin, world["part"], label="새것", key="NEW-2")
    monkeypatch.setattr(copycli, "CHANGED_FLOOR", 0)
    monkeypatch.setattr(copycli, "CHANGED_RATIO", 0.0)
    assert copycli.load(tmp_path) == 0
    done = _manifest(tmp_path)
    assert done["mode"] == "full" and "많음" in done["reason"], done


def test_운영자가_고르면_다음_싣기는_통째로(world: dict[str, Any], tmp_path: Path) -> None:
    """`deploy.sh rdf-copy --full` 이 표식을 둔다 — 다 실으면 지운다(그 다음은 다시 바뀐
    것만)."""
    assert copycli.load(tmp_path) == 0
    (tmp_path / copycli.FULL_NEXT).touch()
    assert copycli.load(tmp_path) == 0
    assert copycli.FULL_NEXT in _manifest(tmp_path)["reason"]
    assert not (tmp_path / copycli.FULL_NEXT).exists()
    assert copycli.load(tmp_path) == 0
    assert _manifest(tmp_path)["mode"] == "changes"


def test_끄면_밤마다_통째로(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert copycli.load(tmp_path) == 0
    monkeypatch.setattr(get_settings(), "rdf_copy_changes", False)
    assert copycli.load(tmp_path) == 0
    assert "RDF_COPY_CHANGES" in _manifest(tmp_path)["reason"]


@pytest.mark.parametrize(
    "tail",
    [
        # 줄 끝에서 끊기고 실패 — 싣기는 「다 받았다」 로 끝난다. 종료 코드로 판다.
        "",
        # 줄 가운데서 끊김 — 싣기가 글을 못 읽는다.
        '<http://x/a> <http://x/b> "끊',
    ],
)
def test_내보내기가_실패하면_앞_판이_그대로_답한다(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tail: str
) -> None:
    assert copycli.load(tmp_path) == 0
    before = (tmp_path / store.ACTIVE).resolve()
    stamp = _manifest(tmp_path)["copied_at"]

    def broken(staging: Path, fd: int) -> list[str]:
        del staging
        script = (
            "import os, sys; fd = int(sys.argv[1]); "
            "os.write(fd, '<http://x/a> <http://x/b> <http://x/c> .\\n'.encode()); "
            f"os.write(fd, {tail!r}.encode()); sys.exit(3)"
        )
        return [sys.executable, "-c", script, str(fd)]

    monkeypatch.setattr(copycli, "_exporter", broken)
    assert copycli.load(tmp_path, full=True) == 1
    assert (tmp_path / store.ACTIVE).resolve() == before
    assert _manifest(tmp_path)["copied_at"] == stamp
    others = [one.name for one in tmp_path.iterdir() if one.name.startswith("slot-")]
    assert others == [before.name], "실패한 쪽 폴더를 치운다"


def test_통째로_하는_때() -> None:
    today = date(2026, 11, 15)  # 일요일
    meta = {"format": copystate.FORMAT, "full_on": "2026-11-12"}
    settings = get_settings()
    assert copycli._when_full(None, today) == "지난 판에 지문이 없음"
    assert copycli._when_full({**meta, "format": "0"}, today) == "지문 판이 다름"
    assert copycli._when_full({**meta, "full_on": "2026-11-08"}, today) == "통째로 한 지 7일"
    old = settings.rdf_copy_full_weekday
    try:
        settings.rdf_copy_full_weekday = "sun"
        assert copycli._when_full(meta, today) == "주 1회 통째로(sun)"
        # 그날 이미 통째로 했으면 또 하지 않는다.
        assert copycli._when_full({**meta, "full_on": "2026-11-15"}, today) is None
        assert copycli._when_full(meta, today - timedelta(days=1)) is None
        settings.rdf_copy_full_weekday = ""
        assert copycli._when_full(meta, today) is None
    finally:
        settings.rdf_copy_full_weekday = old
