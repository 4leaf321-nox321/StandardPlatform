"""적용은 **사람이 읽은 그 계획대로** · 요청 안에서는 값을 훑지 않는다 · 지운 키의 값이
살아난다 · 옮긴 원 타입의 삭제(2026-11-28 네 번째 점검).

- 키 변경 · 옮기기 · 정의 가져오기 · 스냅샷 복원의 요청 안 적용은 `expected_fingerprint` 를
  받는다 — 미리 본 지문과 지금 계획이 다르면 ONTOLOGY-114, 안 주면 예전처럼.
- 옮기기의 지문은 병합할 짝 · 버리는 값의 수를 보고, 옮길 객체 · 값의 수는 안 본다(키 변경도
  수를 안 본다). 묶음의 지문은 정의가 잃는 값의 수를 본다.
- 값이 많은 종류 변경은 가져오기 · 제안 · 복원 · 검토함의 요청 안에서 값을 훑지 않는다 —
  경고만, 적용은 ONTOLOGY-115(작업으로). 경고 속의 셈은 상한까지만.
- 지운 속성의 키로 속성을 다시 세우면 남은 값을 세어 말하고, 새 정의에 안 맞으면 막는다.
- 공개 중에 옮긴 원 타입의 삭제 미리 보기는 수신 시스템이 옮긴 기록을 못 받았을 수 있다고
  말한다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import OntologySnapshot, PropertyDef
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_object, _make_property, _make_type

BOGUS = "0" * 64


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _code(response: Any) -> str:
    return str(response.json().get("error", {}).get("code", ""))


def _move(client: TestClient, admin: Signed, slug: str, to: str, **body: Any) -> Any:
    return client.post(
        f"/api/ontology/types/{slug}/move", json={"to": to, **body}, headers=admin.headers
    )


def _rename_key(
    client: TestClient, admin: Signed, slug: str, key: str, to: str, **body: Any
) -> Any:
    return client.post(
        f"/api/ontology/types/{slug}/properties/{key}/rename-key",
        json={"to": to, **body},
        headers=admin.headers,
    )


def _import(client: TestClient, admin: Signed, schema: dict[str, Any], **params: Any) -> Any:
    return client.post(
        "/api/ontology/import", params=params, json=schema, headers=admin.headers
    )


def _pair(client: TestClient, admin: Signed) -> tuple[str, str]:
    vendor = _make_type(client, admin, label=f"협력사 {_hex()}", key_policy="optional")
    _make_property(client, admin, vendor, key="country", label="국가", data_type="text")
    supplier = _make_type(client, admin, label=f"공급사 {_hex()}", key_policy="optional")
    _make_property(client, admin, supplier, key="country", label="국가", data_type="text")
    return vendor, supplier


def _type_of(db: Session, object_id: str) -> uuid.UUID | None:
    db.expire_all()
    row = db.get(ObjectInstance, uuid.UUID(object_id))
    return row.type_id if row is not None else None


# --- 1. 요청 안의 적용은 미리 본 지문대로 ----------------------------------------


def test_옮기기는_병합할_짝이_미리_본_것과_다르면_옮기지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """병합 1건을 읽고 고른 적용이, 그 사이 대상의 짝이 바뀐 계획(수는 같은 1건)에도 통했다 —
    지문이 짝을 안 봤다."""
    vendor, supplier = _pair(client, admin)
    key = f"V-{_hex()}"
    acme = _make_object(client, admin, vendor, label="에이컴", key=key)
    twin = _make_object(client, admin, supplier, label="에이컴(공급사)", key=key)
    plan = _move(client, admin, vendor, supplier, merge_duplicates=True).json()
    assert plan["duplicates"] == 1 and plan["errors"] == [], plan
    seen = plan["fingerprint"]

    # 그 사이 대상의 짝이 다른 객체로 바뀐다 — 병합 수는 그대로 1건.
    gone = client.delete(f"/api/objects/{supplier}/{twin['id']}", headers=admin.headers)
    assert gone.status_code in (200, 204), gone.text
    _make_object(client, admin, supplier, label="에이컴(새 짝)", key=key)
    again = _move(client, admin, vendor, supplier, merge_duplicates=True).json()
    assert again["duplicates"] == 1 and again["fingerprint"] != seen, again

    refused = _move(
        client,
        admin,
        vendor,
        supplier,
        merge_duplicates=True,
        apply=True,
        expected_fingerprint=seen,
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0114"), (
        refused.text
    )
    assert "미리 본 뒤 바뀌었습니다" in refused.text
    vendor_id = _type_of(db, acme["id"])
    still = client.get(f"/api/objects/{vendor}/{acme['id']}", headers=admin.headers)
    assert still.status_code == 200, "옮기지 않았다"

    done = _move(
        client,
        admin,
        vendor,
        supplier,
        merge_duplicates=True,
        apply=True,
        expected_fingerprint=again["fingerprint"],
    )
    assert done.status_code == 200 and done.json()["merged"] == 1, done.text
    assert _type_of(db, acme["id"]) != vendor_id


def test_옮기기의_지문은_옮길_객체_수의_흔들림을_보지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """계획 작업과 적용 작업 사이에 객체가 하나 늘면 전에는 늘 JOBS-20 이었다 — 사람이 고른
    것(칸 대응 · 병합 · 버림)은 그대로다."""
    vendor, supplier = _pair(client, admin)
    _make_object(client, admin, vendor, label="하나", properties={"country": "KR"})
    plan = _move(client, admin, vendor, supplier).json()
    _make_object(client, admin, vendor, label="둘", properties={"country": "US"})
    again = _move(client, admin, vendor, supplier).json()
    assert again["objects"] == plan["objects"] + 1
    assert again["fingerprint"] == plan["fingerprint"], (plan, again)

    job = client.post(
        f"/api/ontology/types/{vendor}/move/job", json={"to": supplier}, headers=admin.headers
    )
    assert job.status_code == 202, job.text
    planned = finish_job(client, admin, job.json())
    assert planned["status"] == "done", planned
    _make_object(client, admin, vendor, label="셋", properties={"country": "JP"})
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done" and done["result"]["moved"] == 3, done


def test_옮기기의_지문은_버리는_값의_수를_본다(client: TestClient, admin: Signed) -> None:
    vendor, supplier = _pair(client, admin)
    _make_property(client, admin, vendor, key="memo", label="메모", data_type="text")
    _make_object(client, admin, vendor, label="하나", properties={"memo": "가"})
    plan = _move(client, admin, vendor, supplier, mapping={"memo": None}).json()
    _make_object(client, admin, vendor, label="둘", properties={"memo": "나"})
    again = _move(client, admin, vendor, supplier, mapping={"memo": None}).json()
    assert again["fingerprint"] != plan["fingerprint"], "버리는 값이 늘었다"
    refused = _move(
        client,
        admin,
        vendor,
        supplier,
        mapping={"memo": None},
        apply=True,
        expected_fingerprint=plan["fingerprint"],
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0114"), (
        refused.text
    )


def test_키_변경은_미리_본_지문과_다르면_바꾸지_않고_수의_흔들림은_보지_않는다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = _make_type(client, admin, label=f"부품 {_hex()}")
    _make_property(client, admin, part, key="w", label="무게", data_type="number")
    _make_object(client, admin, part, label="볼트", properties={"w": 3})
    plan = _rename_key(client, admin, part, "w", "weight").json()
    assert plan["fingerprint"], plan

    refused = _rename_key(
        client, admin, part, "w", "weight", apply=True, expected_fingerprint=BOGUS
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0114"), (
        refused.text
    )
    db.expire_all()
    keys = set(
        db.scalars(
            select(PropertyDef.key).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.label == "무게",
                PropertyDef.key.in_(("w", "weight")),
            )
        )
    )
    assert "w" in keys

    # 값이 있는 객체가 늘어도 같은 계획이다 — 미리 본 지문으로 적용된다.
    _make_object(client, admin, part, label="너트", properties={"w": 1})
    done = _rename_key(
        client,
        admin,
        part,
        "w",
        "weight",
        apply=True,
        expected_fingerprint=plan["fingerprint"],
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text

    # 작업도 — 계획 작업 뒤 객체가 늘어도 적용된다(전에는 객체 수가 지문에 들어 JOBS-20).
    from app.modules.ontology import rename

    monkeypatch.setattr(rename, "INLINE_OBJECTS", 0)
    job = client.post(
        f"/api/ontology/types/{part}/properties/weight/rename-key/job",
        json={"to": "mass", "expected_fingerprint": BOGUS},
        headers=admin.headers,
    )
    assert job.status_code == 202, job.text
    assert "expected_fingerprint" not in job.json()["params"]["request"]
    planned = finish_job(client, admin, job.json())
    assert planned["status"] == "done", planned
    _make_object(client, admin, part, label="와셔", properties={"weight": 2})
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    done_job = finish_job(client, admin, applied.json())
    assert done_job["status"] == "done" and done_job["result"]["applied"] is True, done_job


def test_타입_slug_변경도_미리_본_지문을_받는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, label=f"부품 {_hex()}")
    to = f"p_{_hex()}"
    path = f"/api/ontology/types/{part}/rename"
    plan = client.post(path, json={"to": to}, headers=admin.headers).json()
    refused = client.post(
        path,
        json={"to": to, "apply": True, "expected_fingerprint": BOGUS},
        headers=admin.headers,
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0114"), (
        refused.text
    )
    done = client.post(
        path,
        json={"to": to, "apply": True, "expected_fingerprint": plan["fingerprint"]},
        headers=admin.headers,
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text


def _seen_schema(kind: str, label: str, data_type: str = "date") -> dict[str, Any]:
    return {
        "types": [
            {
                "slug": kind,
                "label": label,
                "properties": [{"key": "seen", "label": "본 날", "data_type": data_type}],
            }
        ]
    }


def test_정의_가져오기는_미리_본_뒤_잃는_값이_늘면_적용하지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    label = f"기록 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="text")
    _make_object(client, admin, kind, label="하나", properties={"seen": "2026-11-01 10:30"})
    schema = _seen_schema(kind, label)
    plan = _import(client, admin, schema).json()
    assert plan["fingerprint"] and plan["applied"] is False, plan
    snapshots = db.scalar(
        select(OntologySnapshot.id).order_by(OntologySnapshot.taken_at.desc())
    )

    _make_object(client, admin, kind, label="둘", properties={"seen": "2026-11-02 09:00"})
    refused = _import(
        client, admin, schema, dry_run="false", expected_fingerprint=plan["fingerprint"]
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0114"), (
        refused.text
    )
    db.expire_all()
    assert (
        db.scalar(select(OntologySnapshot.id).order_by(OntologySnapshot.taken_at.desc()))
        == snapshots
    ), "남긴 스냅샷도 함께 되돌린다"
    still = client.get(f"/api/ontology/types/{kind}/properties", headers=admin.headers).json()
    assert {one["key"]: one["data_type"] for one in still}["seen"] == "text"

    again = _import(client, admin, schema).json()
    done = _import(
        client, admin, schema, dry_run="false", expected_fingerprint=again["fingerprint"]
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text


def test_스냅샷_복원은_미리_본_지문을_받는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    label = f"설비 {_hex()}"
    kind = _make_type(client, admin, label=label)
    applied = _import(
        client,
        admin,
        {"types": [{"slug": kind, "label": label, "description": "처음"}]},
        dry_run="false",
    )
    assert applied.status_code == 200, applied.text
    snapshot = applied.json()["snapshot_id"]
    path = f"/api/ontology/snapshots/{snapshot}/restore"
    plan = client.post(path, params={"dry_run": "true"}, headers=admin.headers).json()
    assert plan["fingerprint"], plan
    refused = client.post(path, params={"expected_fingerprint": BOGUS}, headers=admin.headers)
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0114"), (
        refused.text
    )
    done = client.post(
        path, params={"expected_fingerprint": plan["fingerprint"]}, headers=admin.headers
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text


# --- 묶음의 지문은 잃는 값의 수를 본다 -------------------------------------------


def test_묶음의_지문은_정의가_잃는_값의_수를_본다() -> None:
    from app.modules.bundles import services as bundle_services
    from app.modules.ontology import importer

    def outcome(cleared: int, converted: int) -> bundle_services.Outcome:
        planned = importer.Plan()
        planned.changes.append(
            importer.Change("property", "log.seen", "update", ["data_type"])
        )
        planned.warnings.append(
            f"속성 log.seen: 종류를 글 → 날짜(으)로 바꿉니다 — 저장값 {converted}개 변환 · "
            f"0개 그대로 · {cleared}개 비움."
        )
        return bundle_services.Outcome(ontology=planned)

    same = bundle_services.fingerprint(outcome(1, 3))
    assert bundle_services.fingerprint(outcome(1, 300)) == same, "변환 수는 안 본다"
    assert bundle_services.fingerprint(outcome(12_000, 3)) != same, "비우는 수는 본다"
    assert bundle_services.fingerprint(outcome(0, 3)) == bundle_services.fingerprint(
        outcome(0, 30)
    )


def test_묶음_작업의_적용은_계획_뒤_잃는_값이_늘면_막힌다(
    client: TestClient, admin: Signed
) -> None:
    label = f"기록 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="text")
    _make_object(client, admin, kind, label="하나", properties={"seen": "2026-11-01 10:30"})
    made = client.post(
        "/api/bundles/import",
        json={"ontology": _seen_schema(kind, label)},
        headers=admin.headers,
    )
    assert made.status_code == 202, made.text
    planned = finish_job(client, admin, made.json())
    assert planned["status"] == "done" and planned["result"]["ok"] is True, planned
    _make_object(client, admin, kind, label="둘", properties={"seen": "2026-11-02 09:00"})
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "failed" and "JOBS-0020" in str(done["error"]), done


# --- 2. 값이 많은 종류 변경은 요청 안에서 훑지 않는다 ------------------------------


def _big_retype(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """값이 있는 객체가 문턱(1)을 넘는 글 칸 — 문턱을 낮추고, 값 훑기가 불리면 터지게 한다."""
    from app.modules.ontology import retype

    label = f"기록 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="text")
    for day in ("2026-11-01", "2026-11-02"):
        _make_object(client, admin, kind, label=day, properties={"seen": day})
    monkeypatch.setattr(retype, "RETYPE_INLINE", 1)

    def scanned(*_args: Any, **_kw: Any) -> Any:
        raise AssertionError("요청 안에서 값을 훑었다")

    monkeypatch.setattr(retype, "plan", scanned)
    return kind, label


def test_가져오기는_값이_많은_종류_변경을_요청_안에서_훑지_않고_적용은_작업으로_보낸다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind, label = _big_retype(client, admin, monkeypatch)
    schema = _seen_schema(kind, label)
    plan = _import(client, admin, schema)
    assert plan.status_code == 200, plan.text
    warned = [one for one in plan.json()["warnings"] if "적용 작업에서 검사합니다" in one]
    assert warned and f"/api/ontology/types/{kind}/properties/seen/retype/job" in warned[0]
    assert plan.json()["errors"] == []

    refused = _import(
        client, admin, schema, dry_run="false", expected_fingerprint=plan.json()["fingerprint"]
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0115"), (
        refused.text
    )
    details = refused.json()["error"]["details"]
    assert details["job_paths"] == [f"/api/ontology/types/{kind}/properties/seen/retype/job"]
    assert "/api/bundles/import" in refused.text
    props = client.get(f"/api/ontology/types/{kind}/properties", headers=admin.headers).json()
    assert {one["key"]: one["data_type"] for one in props}["seen"] == "text", "안 바꿨다"

    # 작업(묶음)은 값을 센다 — 같은 정의가 거기서는 들어간다.
    monkeypatch.undo()
    from app.modules.ontology import retype

    monkeypatch.setattr(retype, "RETYPE_INLINE", 1)
    made = client.post("/api/bundles/import", json={"ontology": schema}, headers=admin.headers)
    planned = finish_job(client, admin, made.json())
    assert planned["status"] == "done" and planned["result"]["ok"] is True, planned
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done", done
    props = client.get(f"/api/ontology/types/{kind}/properties", headers=admin.headers).json()
    assert {one["key"]: one["data_type"] for one in props}["seen"] == "date"


def test_제안은_값이_많은_종류_변경을_훑지_않고_검토함에서_적용하지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind, label = _big_retype(client, admin, monkeypatch)
    made = client.post(
        "/api/ontology/proposals",
        json={"schema": _seen_schema(kind, label), "note": "날짜로"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    proposal = made.json()
    assert any("적용 작업에서 검사합니다" in one for one in proposal["plan"]["warnings"])
    planned = client.post(
        "/api/review/plan",
        json={
            "mode": "decisions",
            "decisions": [
                {
                    "kind": "definition_proposal",
                    "item_id": proposal["id"],
                    "action": "choose",
                    "value": f"accept:{proposal['fingerprint'][:16]}",
                }
            ],
        },
        headers=admin.headers,
    )
    assert planned.status_code == 200, planned.text
    outcome = next(
        one for one in planned.json()["outcomes"] if one["item_id"] == proposal["id"]
    )
    assert outcome["status"] == "blocked", outcome
    assert "제안으로 적용하지 않습니다" in outcome["message"], outcome
    assert "/retype/job" in outcome["message"]


def test_복원은_값이_많은_종류_변경을_요청_안에서_하지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    label = f"기록 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="text")
    for day in ("2026-11-01", "2026-11-02"):
        _make_object(client, admin, kind, label=day, properties={"seen": day})
    retyped = client.post(
        f"/api/ontology/types/{kind}/properties/seen/retype",
        json={"data_type": "date", "apply": True},
        headers=admin.headers,
    )
    assert retyped.status_code == 200 and retyped.json()["applied"] is True, retyped.text
    snapshot = retyped.json()["snapshot_id"]  # 글이던 때

    from app.modules.ontology import retype

    monkeypatch.setattr(retype, "RETYPE_INLINE", 1)
    path = f"/api/ontology/snapshots/{snapshot}/restore"
    plan = client.post(path, params={"dry_run": "true"}, headers=admin.headers)
    assert plan.status_code == 200, plan.text
    assert any("적용 작업에서 검사합니다" in one for one in plan.json()["warnings"]), plan.text
    refused = client.post(path, headers=admin.headers)
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0115"), (
        refused.text
    )
    props = client.get(f"/api/ontology/types/{kind}/properties", headers=admin.headers).json()
    assert {one["key"]: one["data_type"] for one in props}["seen"] == "date", "안 되돌렸다"


def test_큰_타입의_종류_변경은_값이_많다고_하지_않고_타입이_커서라고_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """100만 건이 넘는 타입은 값이 몇 건이든 미룬다(값을 찾는 데도 타입을 통째로 훑는다) —
    그때 「값이 있는 객체가 2만 건을 넘어」 는 거짓이었다."""
    from app.modules.ontology import retype

    label = f"기록 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="text")
    _make_object(client, admin, kind, label="하나", properties={"seen": "2026-11-01"})
    _make_object(client, admin, kind, label="둘")
    monkeypatch.setattr(retype, "RETYPE_INLINE", 1)
    monkeypatch.setattr(retype, "COUNT_WITHOUT_SCAN", 1)
    plan = _import(client, admin, _seen_schema(kind, label)).json()
    said = [one for one in plan["warnings"] if "적용 작업에서 검사합니다" in one]
    assert said and "객체가 2건인 큰 타입이라" in said[0], plan["warnings"]
    assert "값이 있는 객체가" not in said[0]
    refused = _import(client, admin, _seen_schema(kind, label), dry_run="false")
    assert refused.status_code == 409 and "큰 타입이라" in refused.text, refused.text


def test_지문은_상한을_넘은_이상_표시를_보지_않는다() -> None:
    """셈이 상한을 넘기만 해도(「19,990개」 → 「20,000개 이상」) 미리 본 지문과 달라 적용이
    막혔다."""
    from app.modules.ontology import importer, planview

    def plan(shown: str) -> importer.Plan:
        out = importer.Plan()
        out.changes.append(importer.Change("property", "log.seen", "update", ["required"]))
        out.warnings.append(
            f"속성 log.seen: 필수로 바꿉니다. 지금 값이 있는 객체는 {shown}입니다"
        )
        return out

    assert planview.fingerprint(plan("19,990개")) == planview.fingerprint(
        plan("20,000개 이상")
    )


def test_가져오기_경고의_셈은_상한까지만(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ontology import bounded

    label = f"설비 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="maker", label="제조사", data_type="text")
    for name in ("가", "나", "다"):
        _make_object(client, admin, kind, label=name, properties={"maker": name})
    monkeypatch.setattr(bounded, "CAP", 1)
    plan = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": kind,
                    "label": label,
                    "is_active": False,
                    "properties": [
                        {
                            "key": "maker",
                            "label": "제조사",
                            "data_type": "text",
                            "required": True,
                        }
                    ],
                }
            ]
        },
    ).json()
    assert any("지금 값이 있는 객체는 1개 이상입니다" in one for one in plan["warnings"]), plan
    assert any("객체(1개 이상)는 남지만" in one for one in plan["warnings"]), plan


# --- 3. 지운 키를 다시 세우면 남은 값이 살아난다 ----------------------------------


def _orphaned(client: TestClient, admin: Signed, value: Any = "열두 개") -> tuple[str, str]:
    """글 칸 memo 에 값을 넣고 속성을 지운다 — 값은 남는다."""
    label = f"부품 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="memo", label="메모", data_type="text")
    _make_object(client, admin, kind, label="볼트", properties={"memo": value})
    gone = client.delete(f"/api/ontology/types/{kind}/properties/memo", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    return kind, label


def test_지운_키로_속성을_다시_만들면_남은_값을_세고_안_맞으면_막는다(
    client: TestClient, admin: Signed
) -> None:
    kind, _label = _orphaned(client, admin)
    path = f"/api/ontology/types/{kind}/properties"
    refused = client.post(
        path,
        json={"key": "memo", "label": "개수", "data_type": "number"},
        headers=admin.headers,
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0116"), (
        refused.text
    )
    assert "「열두 개」" in refused.text and "종류 변경" in refused.text
    props = client.get(path, headers=admin.headers).json()
    assert all(one["key"] != "memo" for one in props), "만들지 않았다"

    made = client.post(
        path, json={"key": "memo", "label": "메모", "data_type": "text"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    assert made.json()["revived"] == 1
    assert any("저장된 값 1건이 다시 보입니다" in one for one in made.json()["warnings"])

    fresh = client.post(
        path,
        json={"key": "other", "label": "다른", "data_type": "text"},
        headers=admin.headers,
    )
    assert fresh.status_code == 201 and fresh.json()["revived"] == 0, fresh.text
    assert fresh.json()["warnings"] == []


def test_가져오기도_지운_키의_남은_값을_세고_안_맞으면_막는다(
    client: TestClient, admin: Signed
) -> None:
    kind, label = _orphaned(client, admin)

    def schema(data_type: str) -> dict[str, Any]:
        return {
            "types": [
                {
                    "slug": kind,
                    "label": label,
                    "properties": [{"key": "memo", "label": "메모", "data_type": data_type}],
                }
            ]
        }

    number = _import(client, admin, schema("number")).json()
    assert any("새 정의의 종류(숫자)에 맞지 않습니다" in one for one in number["errors"]), (
        number
    )
    text = _import(client, admin, schema("text")).json()
    assert text["errors"] == [], text
    assert any("저장된 값 1건이 다시 보입니다" in one for one in text["warnings"]), text


def test_지운_적_없는_키는_남은_값을_찾느라_객체를_읽지_않는다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """속성에 통째 색인이 없어 처음 쓰는 키를 찾으면 타입을 통째로 훑었다(200만 건에 속성
    하나 1~4초) — 그 키를 지운 · 옮겨 온 기록이 없으면 읽지 않는다."""
    from app.modules.ontology import revived

    label = f"부품 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_object(client, admin, kind, label="볼트")

    def scanned(*_args: Any, **_kw: Any) -> Any:
        raise AssertionError("남은 값을 찾느라 객체를 읽었다")

    monkeypatch.setattr(revived, "_values", scanned)
    made = client.post(
        f"/api/ontology/types/{kind}/properties",
        json={"key": "fresh", "label": "새 칸", "data_type": "text"},
        headers=admin.headers,
    )
    assert made.status_code == 201 and made.json()["warnings"] == [], made.text
    plan = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": kind,
                    "label": label,
                    "properties": [{"key": "other", "label": "다른", "data_type": "number"}],
                }
            ]
        },
    )
    assert plan.status_code == 200 and plan.json()["errors"] == [], plan.text


def test_큰_타입은_요청_안에서_남은_값을_읽지_않고_다시_보일_수_있다고_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ontology import bounded, revived

    kind, _label = _orphaned(client, admin)
    _make_object(client, admin, kind, label="너트")
    monkeypatch.setattr(bounded, "CAP", 1)

    def scanned(*_args: Any, **_kw: Any) -> Any:
        raise AssertionError("큰 타입을 요청 안에서 읽었다")

    monkeypatch.setattr(revived, "_values", scanned)
    made = client.post(
        f"/api/ontology/types/{kind}/properties",
        json={"key": "memo", "label": "개수", "data_type": "number"},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    (said,) = made.json()["warnings"]
    assert "다시 보일 수 있습니다" in said and "객체가 많아(2건)" in said, said


def test_옮겨_온_옛_값의_키로_속성을_만들면_남은_값을_센다(
    client: TestClient, admin: Signed
) -> None:
    """옮기기는 원 타입의 정의 없는 옛 값을 그대로 옮긴다 — 그 키를 옮기기 기록(`carried`)에
    적어, 대상에 그 키로 속성을 만들 때 센다."""
    vendor, supplier = _pair(client, admin)
    _make_property(client, admin, vendor, key="memo", label="메모", data_type="text")
    _make_object(client, admin, vendor, label="에이컴", properties={"memo": "오래된 메모"})
    gone = client.delete(
        f"/api/ontology/types/{vendor}/properties/memo", headers=admin.headers
    )
    assert gone.status_code == 204, gone.text
    moved = _move(client, admin, vendor, supplier, apply=True)
    assert moved.status_code == 200 and moved.json()["applied"] is True, moved.text
    made = client.post(
        f"/api/ontology/types/{supplier}/properties",
        json={"key": "memo", "label": "메모", "data_type": "text"},
        headers=admin.headers,
    )
    assert made.status_code == 201 and made.json()["revived"] == 1, made.text
    assert any("다른 타입에서 옮겨 온 값" in one for one in made.json()["warnings"]), made.text


def test_고를_값을_뺀_뒤_지운_속성도_같은_정의의_복원으로_되살아난다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """제약만 안 맞는 값(뺀 고를 값 · 범위 밖)은 경고다 — 정의 가져오기가 고를 값을 뺄 때와
    같다. 종류가 안 맞을 때만 막는다. 전에는 같은 정의로 되살리는 복원이 통째로 막혔다."""
    label = f"부품 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(
        client,
        admin,
        kind,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B"],
    )
    _make_object(client, admin, kind, label="볼트", properties={"grade": "B"})
    narrowed = _import(
        client,
        admin,
        {
            "types": [
                {
                    "slug": kind,
                    "label": label,
                    "properties": [
                        {
                            "key": "grade",
                            "label": "등급",
                            "data_type": "enum",
                            "enum_options": ["A"],
                        }
                    ],
                }
            ]
        },
        dry_run="false",
    )
    assert narrowed.status_code == 200 and narrowed.json()["applied"] is True, narrowed.text
    gone = client.delete(f"/api/ontology/types/{kind}/properties/grade", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    snapshot = db.scalar(
        select(OntologySnapshot.id)
        .where(OntologySnapshot.reason == f"삭제 직전: 속성 {kind}.grade")
        .order_by(OntologySnapshot.taken_at.desc())
    )
    assert snapshot is not None
    path = f"/api/ontology/snapshots/{snapshot}/restore"
    plan = client.post(path, params={"dry_run": "true"}, headers=admin.headers).json()
    assert plan["errors"] == [], plan
    said = [one for one in plan["warnings"] if "저장된 값 1건이 다시 보입니다" in one]
    assert said and "제약" in said[0], plan["warnings"]
    done = client.post(
        path, params={"expected_fingerprint": plan["fingerprint"]}, headers=admin.headers
    )
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    props = client.get(f"/api/ontology/types/{kind}/properties", headers=admin.headers).json()
    assert any(one["key"] == "grade" for one in props)


# --- 4. 공개 중에 옮긴 원 타입의 삭제 -------------------------------------------


def test_공개_중에_옮긴_원_타입의_삭제_미리_보기는_옮긴_기록을_말한다(
    client: TestClient, admin: Signed
) -> None:
    from tests.api.test_core_api import _open

    vendor, supplier = _pair(client, admin)
    _open(client, admin, vendor)
    _open(client, admin, supplier)
    _make_object(client, admin, vendor, label="에이컴", key=f"V-{_hex()}")
    moved = _move(client, admin, vendor, supplier, apply=True, accept_core=True)
    assert moved.status_code == 200 and moved.json()["applied"] is True, moved.text

    def warnings(slug: str) -> list[str]:
        got = client.get(
            "/api/ontology/delete-plan",
            params={"kind": "type", "slug": slug},
            headers=admin.headers,
        )
        assert got.status_code == 200, got.text
        return list(got.json()["warnings"])

    said = [one for one in warnings(vendor) if "옮긴 기록을 수신 시스템이" in one]
    assert said and supplier in said[0], warnings(vendor)
    # 공개를 해제한 뒤에도(지우려면 해제해야 한다) — 옮길 때 공개였다.
    _open(client, admin, vendor, on=False)
    assert any("옮긴 기록을 수신 시스템이" in one for one in warnings(vendor))

    # 공개하지 않은 타입에서 옮긴 것은 말하지 않는다.
    quiet, other = _pair(client, admin)
    _make_object(client, admin, quiet, label="베타")
    assert _move(client, admin, quiet, other, apply=True).status_code == 200
    assert not any("옮긴 기록을 수신 시스템이" in one for one in warnings(quiet))
