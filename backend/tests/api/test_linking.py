"""표의 열이 어느 있는 타입을 가리키나 — **참조 후보**(ADR 0009).

판정은 일괄 입력의 이름 풀이 그대로다(식별자 → 별칭 → 이름 → id). 그래서 「하나로 풀림」
이라고 보인 값은 넣을 때도 풀린다. 그리고 **확실할 때만** 종류를 참조로 제안한다 — 짧은 숫자 ·
맞은 값이 몇 개뿐인 열 · 두 타입에 다 맞는 열은 후보로 보이기만 한다.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_interfaces import _make_interface
from tests.api.test_ontology import _make_object, _make_type


def _infer(client: TestClient, admin: Signed, lines: list[str]) -> dict[str, Any]:
    text = "\n".join(lines)
    response = client.post(
        "/api/ontology/infer",
        files={"file": ("rows.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
        headers=admin.headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _column(got: dict[str, Any], header: str) -> dict[str, Any]:
    return next(one for one in got["columns"] if one["header"] == header)


def _models(client: TestClient, admin: Signed, tag: str, n: int = 5) -> str:
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    for i in range(1, n + 1):
        _make_object(client, admin, model, key=f"{tag}-{i}", label=f"모델 {tag} {i}")
    return model


def test_축의_식별자로_풀리는_열은_참조로_제안하고_그대로_넣는다(
    client: TestClient, admin: Signed
) -> None:
    tag = f"M{uuid.uuid4().hex[:6]}"
    model = _models(client, admin, tag)
    got = _infer(
        client,
        admin,
        [
            "건번호,이름,모델,수량",
            *(f"C-{i},건 {i},{tag}-{i % 5 + 1},{i}" for i in range(20)),
        ],
    )
    column = _column(got, "모델")
    assert column["data_type"] == "object_ref" and column["ref_type_slug"] == model
    best = column["ref_candidates"][0]
    assert (best["target_slug"], best["one"], best["none"], best["one_values"]) == (
        model,
        20,
        0,
        5,
    )
    assert "100%" in column["note"]
    # 수량(0 ~ 19)은 아무 타입도 안 가리킨다.
    assert _column(got, "수량")["data_type"] == "number"

    slug = f"case_{uuid.uuid4().hex[:6]}"
    built = client.post(
        "/api/ontology/infer/build",
        json={
            "slug": slug,
            "label": "시장 서비스",
            "columns": got["columns"],
            "raw_rows": got["raw_rows"],
        },
        headers=admin.headers,
    )
    assert built.status_code == 200, built.text
    prop = next(
        one
        for one in built.json()["schema"]["types"][0]["properties"]
        if one["label"] == "모델"
    )
    assert prop["data_type"] == "object_ref" and prop["ref_type_slug"] == model
    applied = client.post(
        "/api/ontology/import?dry_run=false",
        json=built.json()["schema"],
        headers=admin.headers,
    )
    assert applied.status_code == 200 and applied.json()["applied"] is True, applied.text
    done = client.post(
        f"/api/objects/{slug}/import-rows",
        json={
            "rows": built.json()["import_rows"],
            "workspace_slug": admin.workspace,
            "apply": True,
        },
        headers=admin.headers,
    ).json()
    assert done["applied"] is True and done["counts"]["create"] == 20, done

    # 이어졌다 — 모델 쪽 「관련 객체」 에 기록이 보인다.
    target = client.get(
        f"/api/objects/{model}", params={"q": f"{tag}-1"}, headers=admin.headers
    ).json()["items"][0]
    refs = client.get(
        f"/api/objects/{model}/{target['id']}/references", headers=admin.headers
    ).json()
    assert refs["total"] == 4, refs


def test_못_찾는_값이_많거나_이름이_여럿에_맞으면_후보로만_보인다(
    client: TestClient, admin: Signed
) -> None:
    tag = f"M{uuid.uuid4().hex[:6]}"
    model = _models(client, admin, tag)
    _make_object(client, admin, model, key=f"{tag}-dup", label=f"모델 {tag} 1")
    got = _infer(
        client,
        admin,
        [
            "이름,모델",
            # 이름으로 적었다 — 「모델 … 1」 은 둘이라 여럿에 맞음, 「없는 …」 은 못 찾음.
            *(f"건 {i},모델 {tag} {i % 5 + 1}" for i in range(15)),
            *(f"건 x{i},없는 {tag} {i}" for i in range(5)),
        ],
    )
    column = _column(got, "모델")
    assert column["data_type"] == "text" and column["ref_type_slug"] is None
    best = column["ref_candidates"][0]
    assert best["target_slug"] == model
    assert (best["one"], best["many"], best["none"]) == (12, 3, 5)
    assert best["many_samples"] == [f"모델 {tag} 1"]
    assert len(best["none_samples"]) == 5


def test_짧은_숫자는_우연일_수_있어_제안하지_않는다(client: TestClient, admin: Signed) -> None:
    vendor = _make_type(client, admin, label="공급사", key_policy="required")
    base = 8300 + uuid.uuid4().int % 600
    for i in range(6):
        _make_object(client, admin, vendor, key=str(base + i), label=f"공급사 {base + i}")
    got = _infer(client, admin, ["이름,판", *(f"건 {i},{base + i % 6}" for i in range(18))])
    column = _column(got, "판")
    assert column["data_type"] == "number" and column["ref_type_slug"] is None
    mine = next(one for one in column["ref_candidates"] if one["target_slug"] == vendor)
    assert mine["one"] == 18 and mine["short"] is True
    assert "짧은 숫자" in column["ref_note"]


def test_구현_타입_여럿에_나뉘면_인터페이스를_제안하고_무관한_두_타입이면_고르라고_한다(
    client: TestClient, admin: Signed
) -> None:
    tag = f"E{uuid.uuid4().hex[:6]}"
    iface = _make_interface(client, admin, label="설비")
    tester = _make_type(
        client, admin, label="시험장비", key_policy="required", interface_slugs=[iface]
    )
    meter = _make_type(
        client, admin, label="계측기", key_policy="required", interface_slugs=[iface]
    )
    for i in range(3):
        _make_object(client, admin, tester, key=f"{tag}-T{i}", label=f"시험기 {tag} {i}")
        _make_object(client, admin, meter, key=f"{tag}-G{i}", label=f"게이지 {tag} {i}")
    got = _infer(
        client,
        admin,
        ["이름,설비", *(f"건 {i},{tag}-{'TG'[i % 2]}{i % 3}" for i in range(12))],
    )
    column = _column(got, "설비")
    assert column["data_type"] == "object_ref" and column["ref_type_slug"] == iface
    assert {one["target_slug"] for one in column["ref_candidates"]} >= {iface, tester, meter}

    # 무관한 두 타입에 같은 식별자 — 어느 쪽인지 사람이 고른다.
    other = f"X{uuid.uuid4().hex[:6]}"
    first = _make_type(client, admin, label="부품", key_policy="required")
    second = _make_type(client, admin, label="자재", key_policy="required")
    for kind in (first, second):
        for i in range(4):
            _make_object(client, admin, kind, key=f"{other}-{i}", label=f"{kind} {i}")
    got = _infer(client, admin, ["이름,품번", *(f"건 {i},{other}-{i % 4}" for i in range(8))])
    column = _column(got, "품번")
    assert column["data_type"] == "text"
    assert {one["target_slug"] for one in column["ref_candidates"]} == {first, second}
    assert "고르세요" in column["ref_note"]


def test_참조_열은_대상이_있어야_만든다(client: TestClient, admin: Signed) -> None:
    got = _infer(client, admin, ["이름,모델", "건 1,A", "건 2,B"])
    column = _column(got, "모델")
    column["data_type"] = "object_ref"
    body = {"slug": f"case_{uuid.uuid4().hex[:6]}", "label": "건", "raw_rows": got["raw_rows"]}

    missing = client.post(
        "/api/ontology/infer/build",
        json={**body, "columns": got["columns"]},
        headers=admin.headers,
    )
    assert missing.status_code == 409 and "가리킬 타입" in missing.json()["error"]["message"]

    column["ref_type_slug"] = "no_such_type"
    unknown = client.post(
        "/api/ontology/infer/build",
        json={**body, "columns": got["columns"]},
        headers=admin.headers,
    )
    assert unknown.status_code == 404, unknown.text


def test_등록이_빠진_원_표를_비추는_타입이_있어도_추론은_선다(
    client: TestClient, admin: Signed
) -> None:
    """도메인을 떼면 그 원 표를 비추던 투영 타입이 정의에 남는다 — 모든 타입을 훑는 참조
    후보가 그 하나 때문에 멈추면 표에서 타입을 아무도 못 만든다."""
    ghost = client.post(
        "/api/ontology/types",
        json={
            "slug": f"ghost_{uuid.uuid4().hex[:6]}",
            "label": "떼어 낸 원 표",
            "kind_class": "system",
            "system_source": "workspace",
        },
        headers=admin.headers,
    )
    assert ghost.status_code == 201, ghost.text
    from sqlalchemy import update

    from app.database import SessionLocal
    from app.modules.ontology.models import ObjectType

    def point_at(source: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(ObjectType)
                .where(ObjectType.slug == ghost.json()["slug"])
                .values(system_source=source)
            )
            db.commit()

    point_at("no_such_source")
    try:
        got = _infer(client, admin, ["이름,모델", "건 1,A", "건 2,B"])
        assert _column(got, "모델")["data_type"] == "text"
    finally:
        # 시험 DB 는 스위트가 함께 쓴다 — 남겨 두면 정의 전체를 되돌리는 다른 시험이 이
        # 타입에서 막힌다.
        point_at("workspace")
