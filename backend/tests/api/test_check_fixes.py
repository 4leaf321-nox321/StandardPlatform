"""0.4.106 뒤 시스템 전반 점검에서 찾은 것(ADR 0098) — 정의 · 권한 · 파일 쪽.

묶음의 문서 · 삭제 표식은 `test_bundle_documents.py`, 키트는 `test_pipeline_cli.py`, 그래프
엔진 · 대량 실종 문턱은 `tests/unit` 에 있다.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.files import gc
from app.modules.files.models import Attachment
from app.modules.notifications.models import Notification
from app.modules.ontology import importer, planview
from app.modules.ontology.models import ObjectType
from app.shared import scopes
from app.shared.errors import code
from tests.api.conftest import Signed
from tests.api.test_move import _made, _move
from tests.api.test_ontology import _make_object, _make_property, _make_type
from tests.api.test_workspace_reassign_job import reassign_plan


def _hex() -> str:
    return uuid.uuid4().hex[:10]


def _token(client: TestClient, who: Signed, scopes_: list[str]) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"check-{_hex()}", "scopes": scopes_},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


# --- 정의 -------------------------------------------------------------------------------


def test_옛_스냅샷_복원_코어_구독은_플랫폼_칸_키를_경고로만_둔다(db: Session) -> None:
    """새 속성 키로 `status` 를 막자(ONTOLOGY-113) 그 키를 쓰던 정의의 옛 스냅샷 복원과
    허브에서
    받는 정의(코어 구독)가 통째로 막혔다."""
    payload = {
        "types": [
            {
                "slug": f"legacy_{_hex()}",
                "label": "옛 정의",
                "properties": [{"key": "status", "label": "상태", "data_type": "text"}],
            }
        ]
    }
    strict = importer.plan(db, payload)
    assert any("플랫폼 칸" in one for one in strict.errors), strict.errors
    kept = importer.plan(db, payload, keep_reserved=True)
    assert not kept.errors, kept.errors
    assert any("키 변경" in one for one in kept.warnings), kept.warnings
    db.rollback()


def test_옮기기의_새_칸_키는_새_속성과_같은_규칙이다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label=f"협력사 {_hex()}", key_policy="optional")
    _make_property(client, admin, vendor, key="memo", label="메모", data_type="text")
    supplier = _make_type(client, admin, label=f"공급사 {_hex()}", key_policy="optional")
    _made(client, admin, vendor, label="에이", properties={"memo": "가"})
    for target, word in (
        ("status", "플랫폼 칸"),
        ("Bad Key", "소문자로 시작"),
        ("k" * 49, "48자"),
    ):
        body = _move(client, admin, vendor, supplier, mapping={"memo": target}).json()
        notes = [one["note"] for one in body["fields"] if one["blocked"]]
        assert notes and word in notes[0], (target, body)
        assert body["errors"], body


def test_옮기기는_정의_없이_남은_옛_값이_대상_칸과_겹치면_막고_버리기를_받는다(
    client: TestClient, admin: Signed
) -> None:
    vendor = _make_type(client, admin, label=f"협력사 {_hex()}", key_policy="optional")
    _make_property(client, admin, vendor, key="note", label="비고", data_type="text")
    supplier = _make_type(client, admin, label=f"공급사 {_hex()}", key_policy="optional")
    _make_property(client, admin, supplier, key="note", label="공급 비고", data_type="text")
    _made(client, admin, vendor, label="에이", properties={"note": "예전 값"})
    gone = client.delete(
        f"/api/ontology/types/{vendor}/properties/note", headers=admin.headers
    )
    assert gone.status_code in (200, 204), gone.text

    refused = _move(client, admin, vendor, supplier).json()
    assert any("정의 없이 남은 옛 값" in one for one in refused["errors"]), refused
    dropped = _move(client, admin, vendor, supplier, mapping={"note": None}).json()
    assert dropped["errors"] == [], dropped
    assert any(
        one["source"] == "note" and one["action"] == "drop" for one in dropped["fields"]
    ), dropped


def test_부서_범위에서_전사_범위로_옮기면_부서끼리_같은_식별자를_막는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    from app.modules.workspaces.models import Workspace

    local = _make_type(
        client,
        admin,
        label=f"부서 협력사 {_hex()}",
        key_policy="optional",
        key_scope="workspace",
    )
    _make_property(client, admin, local, key="code", label="코드", data_type="text")
    whole = _make_type(client, admin, label=f"전사 협력사 {_hex()}", key_policy="optional")
    _make_property(
        client, admin, whole, key="code", label="코드", data_type="text", unique=True
    )
    other = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="다른 팀")
    db.add(other)
    db.commit()
    for where in (admin.workspace, other.slug):
        _make_object(
            client,
            admin,
            local,
            key="K-1",
            label=f"{where} 의 것",
            workspace_slug=where,
            properties={"code": "C-9"},
        )
    body = _move(client, admin, local, whole).json()
    assert any("부서마다" in one for one in body["errors"]), body
    assert any("유일 칸" in one and "묶음이 1개" in one for one in body["errors"]), body


def test_slug_의_밑줄은_LIKE_의_한_글자가_아니다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """`a_1` 을 바꿀 때 `a11` 의 알림 링크까지 새 slug 로 바뀌었다."""
    base = f"lk{_hex()}"
    mine = _make_type(client, admin, base=f"{base}_q", label=f"밑줄 {_hex()}")
    assert "_q" in mine
    neighbour = mine.replace("_q", "xq")
    from app.modules.accounts.models import User

    user_id = db.scalar(select(User.id).where(User.email == admin.email))
    link = f"/o/{neighbour}/{uuid.uuid4()}"
    db.add(Notification(user_id=user_id, kind="system", title="이웃", body="", link=link))
    db.commit()
    renamed = client.post(
        f"/api/ontology/types/{mine}/rename",
        json={"to": f"{mine}z", "apply": True},
        headers=admin.headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["applied"] is True, renamed.json()
    db.expire_all()
    assert db.scalar(select(Notification.link).where(Notification.link == link)) == link


def test_값만_고쳐_다시_낸_정의_제안은_새_제안이다(client: TestClient, admin: Signed) -> None:
    """계획의 지문은 무엇이 바뀌는지만 담아, 값(종류 · 선택 값)을 고쳐 낸 제안이 옛 제안으로
    바뀌었다."""
    slug = f"proposed_{_hex()}"

    def propose(kind: str) -> dict[str, Any]:
        made = client.post(
            "/api/ontology/proposals",
            json={
                "schema": {
                    "types": [
                        {
                            "slug": slug,
                            "label": "협력사",
                            "properties": [
                                {
                                    "key": "country",
                                    "label": "국가",
                                    "data_type": kind,
                                    **({"enum_options": ["한국"]} if kind == "enum" else {}),
                                }
                            ],
                        }
                    ]
                }
            },
            headers=admin.headers,
        )
        assert made.status_code in (200, 201), made.text
        return dict(made.json())

    first = propose("text")
    second = propose("enum")
    assert second["id"] != first["id"] and second["created"] is True, (first, second)
    assert propose("enum")["id"] == second["id"], "정말 같은 것은 있는 것"


def test_계획의_지문은_경고_속_지금_수를_보지_않는다() -> None:
    def plan(count: int) -> importer.Plan:
        out = importer.Plan()
        out.changes.append(importer.Change("type", "partner", "update", ["label"]))
        out.warnings.append(f"값이 있는 객체는 {count:,}개입니다 — 함께 바뀝니다")
        return out

    assert planview.fingerprint(plan(12)) == planview.fingerprint(plan(1_204))
    other = plan(12)
    other.warnings[0] = "다른 경고"
    assert planview.fingerprint(other) != planview.fingerprint(plan(12))


def test_지운_칸이_설정_뜻으로_찾기에_쓰이면_삭제_계획이_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    from app.modules.semantic.models import SemanticTarget

    kind = _make_type(client, admin, label=f"기록 {_hex()}")
    _make_property(client, admin, kind, key="symptom", label="증상", data_type="text")
    type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == kind))
    db.add(SemanticTarget(type_id=type_id, fields=["symptom"]))
    db.commit()
    monkeypatch.setattr(get_settings(), "date_index_fields", f"{kind}.symptom")
    plan = client.get(
        "/api/ontology/delete-plan",
        params={"kind": "property", "slug": kind, "key": "symptom"},
        headers=admin.headers,
    )
    assert plan.status_code == 200, plan.text
    warnings = " / ".join(plan.json()["warnings"])
    assert "뜻으로 찾기" in warnings and "DATE_INDEX_FIELDS" in warnings, plan.json()


# --- 권한 -------------------------------------------------------------------------------


def test_읽기로_연_POST_는_그_주소_하나만이다(client: TestClient) -> None:
    """`/api/metrics/plan` 을 읽기로 열자 slug 가 `plan` 인 지표의 재계산 · 경보까지 읽기가
    됐다."""
    assert scopes.is_reading("POST", "/api/metrics/plan") is True
    assert scopes.is_reading("POST", "/api/metrics/plan/recompute") is False
    assert scopes.is_reading("POST", "/api/metrics/plan/alerts") is False
    assert scopes.is_reading("POST", "/api/rdf/sparql") is True, "바깥 SPARQL 도구의 POST"


def test_정의를_바꾸는_데이터_소스_자리는_정의_범위다(
    client: TestClient, admin: Signed
) -> None:
    assert scopes.needed_scope("/api/datasources/ra-report-type") == "ontology:write"
    assert (
        scopes.needed_scope("/api/datasources/core-subscriptions/x/sync") == "ontology:write"
    )
    assert scopes.needed_scope("/api/datasources/plm/sync") == "objects:write"
    objects_only = _token(client, admin, ["read", "objects:write"])
    refused = client.post(
        "/api/datasources/ra-report-type",
        json={"slug": f"ra_{_hex()}", "label": "RA 보고서"},
        headers=objects_only,
    )
    assert refused.status_code == 403, refused.text


def test_부서_자료_옮기기는_토큰으로_적용하지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    from app.modules.workspaces.models import Workspace

    source = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="옮길 팀")
    target = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="받을 팀")
    db.add_all([source, target])
    db.commit()
    planned = reassign_plan(client, admin, source.slug, target.slug, ["objects"])
    refused = client.post(
        f"/api/jobs/{planned['id']}/apply",
        headers=_token(client, admin, ["read", "objects:write"]),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == code("JOBS", 31), refused.json()
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text


def test_작업에는_사람만_정하는_표지를_넣을_수_없다(client: TestClient, admin: Signed) -> None:
    for name in ("human_only", "by_token"):
        made = client.post(
            "/api/jobs",
            data={"kind": "filestore_gc", "params": f'{{"{name}": true}}'},
            headers=admin.headers,
        )
        assert made.status_code in (400, 409, 422), made.text
        assert name in made.text


def test_검토함_전부_작업은_토큰이_시켰는지_작업_인자에_남긴다(
    client: TestClient, admin: Signed
) -> None:
    made = client.post(
        "/api/review/bulk",
        json={"mode": "recommended", "filters": {}, "threshold": 0.9, "action": "accept"},
        headers=_token(client, admin, ["read", "objects:write"]),
    )
    assert made.status_code == 202, made.text
    job = client.get(f"/api/jobs/{made.json()['job_id']}", headers=admin.headers).json()
    assert job["params"].get("by_token") is True, job["params"]


# --- 파일 -------------------------------------------------------------------------------


def test_저장소_대부분이_고아면_자동_정리는_지우지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    """다른 DB 에 붙은 워커가 같은 파일스토어를 보면 그 DB 가 모르는 파일이 모두 고아로 보여
    다 지워졌다 — 개발 환경의 첨부 원본이 그렇게 사라진 것으로 본다."""
    import os
    import time

    from app import worker

    root = tmp_path / "filestore"
    monkeypatch.setattr(get_settings(), "filestore_dir", root)
    old = time.time() - 3 * 86_400
    for n in range(3):
        digest = f"{n:064x}"
        path = root / digest[:2] / digest[2:4] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (old, old))
    found = gc.scan(db)
    assert len(found.orphans) == 3 and gc.looks_foreign(found), found
    worker._clean_orphans()
    assert len(list(root.rglob("*/*/*"))) == 3, "자동 정리는 지우지 않았다"


def test_원본_없는_첨부는_404_다(client: TestClient, admin: Signed, db: Session) -> None:
    from tests.api.test_files import _upload

    made = _upload(client, admin, content=b"hello", workspace=admin.workspace)
    assert made.status_code == 201, made.text
    row = db.get(Attachment, uuid.UUID(made.json()["id"]))
    assert row is not None
    path = Path(get_settings().filestore_dir) / row.relative_path
    path.unlink()
    got = client.get(f"/api/attachments/{row.id}/content", headers=admin.headers)
    assert got.status_code == 404, got.text
    assert got.json()["error"]["code"] == code("FILES", 6)


# --- 옮기기를 두 번 -----------------------------------------------------------------------


def test_두_번_옮긴_객체의_첫_주소도_지금_자리를_안내한다(
    client: TestClient, admin: Signed
) -> None:
    first = _make_type(client, admin, label=f"처음 {_hex()}", key_policy="optional")
    _make_property(client, admin, first, key="memo", label="메모", data_type="text")
    second = _make_type(client, admin, label=f"둘째 {_hex()}", key_policy="optional")
    _make_property(client, admin, second, key="note", label="메모", data_type="text")
    third = _make_type(client, admin, label=f"셋째 {_hex()}", key_policy="optional")
    _make_property(client, admin, third, key="remark", label="메모", data_type="text")
    made = _made(client, admin, first, label="에이", properties={"memo": "가"})
    changed = client.patch(
        f"/api/objects/{first}/{made['id']}",
        json={"properties": {"memo": "나"}},
        headers=admin.headers,
    )
    assert changed.status_code == 200, changed.text
    for src, dst in ((first, second), (second, third)):
        done = _move(client, admin, src, dst, apply=True)
        assert done.status_code == 200 and done.json()["applied"] is True, done.text

    old = client.get(f"/api/objects/{first}/{made['id']}", headers=admin.headers)
    assert old.status_code == 404, old.text
    assert old.json()["error"]["details"] == {"moved_to": third, "object_id": made["id"]}
    history = client.get(f"/api/objects/{third}/{made['id']}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    edits = [one for one in history.json() if one["action"] == "object.update"]
    assert edits, history.json()
    # 첫 타입(memo)에서 고친 기록을 지금 타입의 키(remark)로 읽는다 — 두 단계의 칸 대응을
    # 이어서.
    keys = {key for one in edits for key in one["changes"]}
    assert keys == {"properties.remark"}, [one["changes"] for one in edits]
