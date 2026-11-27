"""0.4.106 뒤 시스템 전반 점검에서 찾은 것(ADR 0098) — 정의 · 권한 · 파일 쪽. 0.4.107 뒤
재점검(2026-11-27)에서 찾은 것도 여기 있다 — 옮기기의 유일 범위 · 자동 정리의 멈춤과 알림 ·
잃는 값의 지문 · 표지 없는 옛 계획 · LIKE 의 밑줄 · `.env` 알림.

묶음의 문서 · 삭제 표식은 `test_bundle_documents.py`, 키트는 `test_pipeline_cli.py`, 그래프
엔진 · 대량 실종 문턱은 `tests/unit` 에 있다. RA 의 멈춤은 `test_datasource_ra.py`, 코어 구독의
플랫폼 칸은 `test_core_subscription.py`, 코어 키트는 `tests/unit/test_core_kit.py`.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
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


def _team(db: Session, name: str) -> str:
    from app.modules.workspaces.models import Workspace

    row = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name=name)
    db.add(row)
    db.commit()
    return row.slug


def test_범위가_부서인_대상의_유일_칸은_부서마다_따로_본다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """부서 A 와 B 에 같은 「S1」 이 있는데 대상의 유일 칸이 부서 범위면 겹치지 않는다 — 전에는
    값만 묶어 「옮길 객체끼리 같은 값」 으로 막았다(2026-11-27 점검). 같은 부서 안의 같은 값은
    그대로 막는다."""
    local = _make_type(
        client,
        admin,
        label=f"부서 협력사 {_hex()}",
        key_policy="optional",
        key_scope="workspace",
    )
    _make_property(client, admin, local, key="code", label="코드", data_type="text")
    other = _make_type(
        client,
        admin,
        label=f"부서 공급사 {_hex()}",
        key_policy="optional",
        key_scope="workspace",
    )
    _make_property(
        client, admin, other, key="code", label="코드", data_type="text", unique=True
    )
    team = _team(db, "다른 팀")
    for where, key in ((admin.workspace, "K-1"), (team, "K-2")):
        _make_object(
            client,
            admin,
            local,
            key=key,
            label=f"{where} 의 것",
            workspace_slug=where,
            properties={"code": "S1"},
        )
    body = _move(client, admin, local, other).json()
    assert body["errors"] == [], body

    # 같은 부서의 둘이 같은 값이면 대상에서 겹친다.
    _make_object(
        client,
        admin,
        local,
        key="K-3",
        label="같은 팀의 것",
        workspace_slug=team,
        properties={"code": "S1"},
    )
    body = _move(client, admin, local, other).json()
    assert any("같은 부서 안에서" in one and "묶음이 1개" in one for one in body["errors"]), (
        body
    )
    # 대상에 이미 있는 값도 부서마다 — 다른 부서의 「S2」 는 겹치지 않는다.
    _make_object(
        client,
        admin,
        other,
        key="T-1",
        label="대상의 것",
        workspace_slug=admin.workspace,
        properties={"code": "S2"},
    )
    _make_object(
        client,
        admin,
        local,
        key="K-4",
        label="다른 팀의 S2",
        workspace_slug=team,
        properties={"code": "S2"},
    )
    body = _move(client, admin, local, other).json()
    assert not any("이미 있는 객체" in one for one in body["errors"]), body


def test_같은_대상_객체로_병합되는_것끼리는_겹침이_아니다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """부서 → 전사 범위로 옮길 때 부서마다 같은 식별자는 겹친다 — 그러나 대상에 그 식별자가
    이미 있으면 둘 다 그 객체로 병합되어 하나가 된다. 유일 칸의 같은 값도 그렇다."""
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
    team = _team(db, "다른 팀")
    into = _make_object(client, admin, whole, key="K-1", label="전사의 것")
    for where in (admin.workspace, team):
        _make_object(
            client,
            admin,
            local,
            key="K-1",
            label=f"{where} 의 것",
            workspace_slug=where,
            properties={"code": "C-9"},
        )
    body = _move(client, admin, local, whole, merge_duplicates=True).json()
    assert body["errors"] == [], body
    done = _move(client, admin, local, whole, merge_duplicates=True, apply=True)
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    merged = client.get(f"/api/objects/{whole}/{into['id']}", headers=admin.headers)
    assert merged.json()["object"]["properties"].get("code") == "C-9", merged.json()

    # 병합되지 않는 것끼리는 그대로 막는다 — 대상에 없는 식별자 K-2 가 두 부서에.
    second = _make_type(
        client,
        admin,
        label=f"부서 협력사 {_hex()}",
        key_policy="optional",
        key_scope="workspace",
    )
    _make_property(client, admin, second, key="code", label="코드", data_type="text")
    for where in (admin.workspace, team):
        _make_object(
            client, admin, second, key="K-2", label=f"{where} 의 것", workspace_slug=where
        )
    body = _move(client, admin, second, whole).json()
    assert any("부서마다" in one for one in body["errors"]), body


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


def test_slug_의_밑줄은_문서_본문과_이력에서도_한_글자가_아니다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """slug 변경 계획의 「문서 본문에 옛 주소」 와 이력의 「그 뒤에 참조에서 바뀐 칸」 이 LIKE
    의 `_` 를 가리지 않아 이웃 타입(`a_1` 에 `a11`)의 것을 읽었다(2026-11-27 점검)."""
    from app.modules.audit.models import AuditEntry
    from app.modules.objects import history
    from app.modules.objects.models import ObjectDocument

    base = f"lk{_hex()}"
    mine = _make_type(client, admin, base=f"{base}_q", label=f"밑줄 {_hex()}")
    neighbour = mine.replace("_q", "xq")
    made = _make_object(client, admin, mine, label="밑줄의 것")
    db.add(
        ObjectDocument(
            object_id=uuid.UUID(made["id"]),
            key="memo",
            body=f"[이웃](/o/{neighbour}/{uuid.uuid4()})",
        )
    )
    db.commit()
    plan = client.post(
        f"/api/ontology/types/{mine}/rename", json={"to": f"{mine}z"}, headers=admin.headers
    )
    assert plan.status_code == 200, plan.text
    assert not any("문서" in one and "옛 주소" in one for one in plan.json()["warnings"]), (
        plan.json()
    )

    def entry(action: str, label: str, changes: dict[str, Any]) -> AuditEntry:
        row = AuditEntry(
            action=action,
            actor_label="시험",
            target_table="objects",
            target_label=label,
            changes=changes,
        )
        db.add(row)
        db.flush()
        return row

    since = entry("object.update", f"{mine}:밑줄의 것", {})
    retyped = {"data_type": {"before": "object_ref", "after": "text"}}
    entry("ontology.property.retype", f"{neighbour}.maker", retyped)
    db.commit()
    kind = db.scalar(select(ObjectType).where(ObjectType.slug == mine))
    assert kind is not None
    assert history._retyped_from_ref(db, kind, since.id) == set()
    entry("ontology.property.retype", f"{mine}.vendor", retyped)
    db.commit()
    assert history._retyped_from_ref(db, kind, since.id) == {"vendor"}


def test_키_변경의_env_알림은_이름이_같을_때만이다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """키 `foo` 를 바꾸거나 지울 때 `.env` 에 `foo_bar` 만 있어도 「옛 키가 있다」 고 알렸다 —
    앞머리로 견줬다(2026-11-27 점검)."""
    kind = _make_type(client, admin, label=f"기록 {_hex()}")
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="date")
    _make_property(client, admin, kind, key="seen_at", label="본 때", data_type="date")
    monkeypatch.setattr(get_settings(), "date_index_fields", f"{kind}.seen_at,other.seen")

    def warned(key: str) -> bool:
        plan = client.post(
            f"/api/ontology/types/{kind}/properties/{key}/rename-key",
            json={"to": f"{key}_new"},
            headers=admin.headers,
        )
        assert plan.status_code == 200, plan.text
        return any(".env" in one for one in plan.json()["warnings"])

    assert warned("seen") is False
    assert warned("seen_at") is True

    def dropped(key: str) -> bool:
        plan = client.get(
            "/api/ontology/delete-plan",
            params={"kind": "property", "slug": kind, "key": key},
            headers=admin.headers,
        )
        assert plan.status_code == 200, plan.text
        return any(".env" in one for one in plan.json()["warnings"])

    # 속성 삭제 계획도 같은 함수다.
    assert dropped("seen") is False
    assert dropped("seen_at") is True
    # slug 변경은 그 타입의 칸 전부 — 이름이 같은 타입만.
    renamed = client.post(
        f"/api/ontology/types/{kind}/rename", json={"to": f"{kind}z"}, headers=admin.headers
    )
    assert any(".env" in one for one in renamed.json()["warnings"]), renamed.json()
    monkeypatch.setattr(get_settings(), "date_index_fields", f"{kind}_x.seen")
    renamed = client.post(
        f"/api/ontology/types/{kind}/rename", json={"to": f"{kind}z"}, headers=admin.headers
    )
    assert not any(".env" in one for one in renamed.json()["warnings"]), renamed.json()


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


def test_계획의_지문은_잃는_값의_수를_본다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """숫자를 다 지우자 1개를 잃는 계획을 읽고 고른 「적용」 이 1만 2천 개를 잃는 계획에도
    통했다(2026-11-27 점검). 잃는 수만 견주고, 다른 수(변환 · 그대로)는 그대로 안 본다."""
    label = f"기록 {_hex()}"
    kind = _make_type(client, admin, label=label)
    _make_property(client, admin, kind, key="seen", label="본 날", data_type="text")
    payload = {
        "types": [
            {
                "slug": kind,
                "label": label,
                "properties": [{"key": "seen", "label": "본 날", "data_type": "date"}],
            }
        ]
    }

    def seen(value: str) -> None:
        _make_object(client, admin, kind, label=f"기록 {_hex()}", properties={"seen": value})

    seen("2026-11-01 10:30")  # 시각을 버린다
    first = importer.plan(db, payload)
    assert any("시각을 버리고 날짜만 남기는 값 1개" in one for one in first.warnings), (
        first.warnings
    )
    assert [planview.losses(one) for one in first.warnings if planview.losses(one)] == [[1]]
    db.rollback()

    seen("2026-11-02")  # 잃는 것 없이 바뀐다 — 변환 수만 는다
    same = importer.plan(db, payload)
    assert planview.fingerprint(same) == planview.fingerprint(first), same.warnings
    db.rollback()

    seen("2026-11-03 09:00")  # 잃는 것이 하나 더
    more = importer.plan(db, payload)
    assert planview.fingerprint(more) != planview.fingerprint(first), more.warnings
    db.rollback()

    # 다른 잃는 경고의 문구도 수를 읽는다 — 문구가 바뀌면 이 시험이 알린다.
    assert planview.losses(
        "속성 a.b: 종류를 … — 저장값 3개 변환 · 0개 그대로 · 12,000개 비움."
    ) == [12_000]
    assert planview.losses(
        "a.b: 허브가 관리하는 타입이라 숫자(으)로 변환할 수 없는 값 7건을 비웁니다"
    ) == [7]
    assert planview.losses("a.b: 필수 속성인데 값을 비우는 객체가 2개입니다 — 그대로") == [2]
    assert (
        planview.losses("속성 a.b: 필수로 바꿉니다. 지금 값이 있는 객체는 1,204개입니다") == []
    )


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


def test_표지가_없는_옛_부서_자료_옮기기_계획도_토큰으로_적용하지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """`human_only` 는 v0.4.107 부터 넣을 때 적는다 — 그 전에 세운 계획은 표지가 없어 토큰으로
    적용됐다(2026-11-27 점검). 종류로도 막는다."""
    from sqlalchemy import update

    from app.modules.jobs.models import Job

    source, target = _team(db, "옮길 팀"), _team(db, "받을 팀")
    planned = reassign_plan(client, admin, source, target, ["objects"])
    row = db.get(Job, uuid.UUID(planned["id"]))
    assert row is not None and row.params.get("human_only") is True
    old = {key: value for key, value in row.params.items() if key != "human_only"}
    db.execute(update(Job).where(Job.id == row.id).values(params=old))
    db.commit()
    refused = client.post(
        f"/api/jobs/{planned['id']}/apply",
        headers=_token(client, admin, ["read", "objects:write"]),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == code("JOBS", 31), refused.json()
    many = client.post(
        "/api/jobs/apply",
        json={"ids": [planned["id"]]},
        headers=_token(client, admin, ["read", "objects:write"]),
    )
    assert many.status_code == 200 and many.json()[0]["status"] == "error", many.text
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


def _stored(root: Path, n: int, *, days: float) -> str:
    """내용 주소 모양의 파일 하나 — `days` 일 전에 쓴 것처럼. 상대 경로를 돌려준다."""
    digest = f"{n:064x}"
    relative = f"{digest[:2]}/{digest[2:4]}/{digest}"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    when = time.time() - days * 86_400
    os.utime(path, (when, when))
    return relative


def _halt_notices(db: Session, email: str) -> list[Notification]:
    from app.modules.accounts.models import User
    from app.modules.notifications import services as notifications

    db.expire_all()
    user_id = db.scalar(select(User.id).where(User.email == email))
    return list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.kind == notifications.FILES_CLEANUP_HALTED,
            )
        )
    )


def test_자동_정리의_멈춤은_하루_지난_파일과_견주고_관리자에게_알린다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: Session, admin: Signed
) -> None:
    """하루 지난 고아 16개에 오늘 올린 파일 하나만 더해도 「전부 고아」 가 아니게 되어(훑은 수
    17) 16개가 지워졌다 — 개발 환경에서 원본이 사라진 첨부도 16개였다(2026-11-27 점검). 멈추면
    로그만 남겨 아무도 몰랐다."""
    from sqlalchemy import delete

    from app import worker
    from app.modules.notifications import services as notifications

    root = tmp_path / "filestore"
    monkeypatch.setattr(get_settings(), "filestore_dir", root)
    for n in range(16):
        _stored(root, n, days=2)
    _stored(root, 99, days=0)  # 오늘 올렸다 — 어차피 지우지 않는다
    (root / gc.INCOMING).mkdir()
    temp = root / gc.INCOMING / "tmpleft"
    temp.write_bytes(b"x")
    os.utime(temp, (0, 0))
    db.execute(
        delete(Notification).where(Notification.kind == notifications.FILES_CLEANUP_HALTED)
    )
    db.commit()

    found = gc.scan(db)
    assert (len(found.orphans), found.recent, found.scanned) == (16, 1, 17), found
    assert "저장소의 파일 17개 가운데 17개" in gc.looks_foreign(found)
    worker._clean_orphans()
    assert len(list(root.glob("*/*/*"))) == 17, "자동 정리는 지우지 않았다"
    assert temp.exists(), "저장소가 남의 것일 수 있어 임시 파일도 그대로"
    sent = _halt_notices(db, admin.email)
    assert len(sent) == 1, sent
    assert sent[0].title == "첨부 저장소의 자동 정리를 멈췄습니다"
    assert "임시 파일(_incoming) 1개의 정리도 함께 미룹니다" in (sent[0].body or "")
    assert sent[0].link == "/admin/server"

    # 다음 차례도 멈추지만 같은 날 또 알리지 않는다.
    worker._clean_orphans()
    assert len(_halt_notices(db, admin.email)) == 1
    later = datetime.now(UTC) + gc.HALT_NOTICE_EVERY + timedelta(hours=1)
    assert gc.halt(db, found, "까닭", now=later) is True, "하루가 지나면 다시 알린다"


def test_자동_정리는_작은_저장소가_거의_다_고아여도_멈추고_보통의_고아는_지운다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    from app import worker

    root = tmp_path / "filestore"
    monkeypatch.setattr(get_settings(), "filestore_dir", root)
    held = {_stored(root, 1000, days=5)}
    monkeypatch.setattr(gc, "_referenced", lambda _db: held)
    for n in range(16):
        _stored(root, n, days=2)
    found = gc.scan(db)
    assert (len(found.orphans), found.scanned) == (16, 17), found
    assert gc.looks_foreign(found), "17개 가운데 16개가 고아 — 멈춘다"
    worker._clean_orphans()
    assert len(list(root.glob("*/*/*"))) == 17

    # 보통의 저장소 — 가리키는 파일 사이의 고아는 지운다.
    held |= {_stored(root, 2000 + n, days=5) for n in range(40)}
    found = gc.scan(db)
    assert (len(found.orphans), found.scanned) == (16, 57), found
    assert gc.looks_foreign(found) == ""
    worker._clean_orphans()
    left = {path.relative_to(root).as_posix() for path in root.glob("*/*/*")}
    assert left == held, "고아만 지우고 가리키는 것은 남긴다"

    # 고아 둘까지는 비율을 보지 않는다 — 첨부 몇 개짜리 저장소에서 한두 개를 뗀 것.
    assert gc.looks_foreign(gc.Found(orphans=[("a", 1), ("b", 1)], scanned=3)) == ""
    assert gc.looks_foreign(gc.Found(orphans=[("a", 1), ("b", 1), ("c", 1)], scanned=5))
    assert gc.looks_foreign(gc.Found(orphans=[("a", 1)], scanned=1)), "전부면 하나라도"
    old = [(str(n), 1) for n in range(100)]
    # 사진 일괄 「교체」 다음 날 — 옛 100장이 고아, 오늘 올린 새 100장은 가리킨다: 정상.
    assert gc.looks_foreign(gc.Found(orphans=old, scanned=200)) == ""
    # 남의 저장소 — 오늘 올린 파일까지 이 DB 가 모른다.
    assert gc.looks_foreign(gc.Found(orphans=old[:16], recent=20, scanned=36))


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


def test_가운데_타입의_slug_를_바꾼_뒤_또_옮겨도_첫_기록을_지금_키로_읽는다(
    client: TestClient, admin: Signed
) -> None:
    """옮기기 기록의 원 타입은 그때의 slug 로 적힌다 — A → B 뒤 B 의 slug 를 바꾸고 C 로
    옮기면 둘째 기록의 원은 바꾼 이름이라, 첫 기록(「A → B」)과 글자로 이어지지 않아 A 에서
    고친 기록을 A 의 키로 읽었다(2026-11-27 점검). 타입의 id 로도 잇는다."""
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
    done = _move(client, admin, first, second, apply=True)
    assert done.status_code == 200 and done.json()["applied"] is True, done.text
    renamed = f"renamed_{_hex()}"
    rename = client.post(
        f"/api/ontology/types/{second}/rename",
        json={"to": renamed, "apply": True},
        headers=admin.headers,
    )
    assert rename.status_code == 200 and rename.json()["applied"] is True, rename.text
    done = _move(client, admin, renamed, third, apply=True)
    assert done.status_code == 200 and done.json()["applied"] is True, done.text

    history = client.get(f"/api/objects/{third}/{made['id']}/history", headers=admin.headers)
    assert history.status_code == 200, history.text
    edits = [one for one in history.json() if one["action"] == "object.update"]
    keys = {key for one in edits for key in one["changes"]}
    assert keys == {"properties.remark"}, [one["changes"] for one in edits]
