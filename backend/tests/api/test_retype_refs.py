"""글 ↔ 참조 종류 변경 — **이미 넣은 기록을 축에 잇는다**(ADR 0009).

글 → 참조는 값마다 일괄 입력과 같은 이름 풀이(식별자 → 별칭 → 이름 → id)로 바꾸고, 못 푼 값
(이름이 여럿 · 없음)은 대체 값을 정해야 적용된다. 참조 → 글은 상대의 식별자(없으면 이름)로 —
다시 참조로 바꾸면 같은 객체로 풀린다. 객체마다의 이력에는 바뀐 칸만 남는다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from tests.api.conftest import Signed, finish_job
from tests.api.test_interfaces import _code, _props
from tests.api.test_ontology import _make_object, _make_property, _make_type
from tests.api.test_retype import _retype, _value


def _world(client: TestClient, admin: Signed) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:6]
    model = _make_type(client, admin, label="개발모델", key_policy="optional")
    made = {
        "a": _make_object(client, admin, model, key=f"{tag}-A", label=f"모델 {tag} A"),
        "b": _make_object(client, admin, model, key=f"{tag}-B", label=f"모델 {tag} B"),
        "solo": _make_object(client, admin, model, label=f"외톨이 {tag}"),
        "twin1": _make_object(client, admin, model, label=f"쌍둥이 {tag}"),
        "twin2": _make_object(client, admin, model, label=f"쌍둥이 {tag}"),
    }
    return {"tag": tag, "model": model, **{name: one["id"] for name, one in made.items()}}


def _cases(
    client: TestClient,
    admin: Signed,
    values: dict[str, Any],
    data_type: str = "text",
    **kw: Any,
) -> tuple[str, dict[str, str]]:
    case = _make_type(client, admin, label="시장 서비스", key_policy="optional")
    _make_property(client, admin, case, key="model", label="모델", data_type=data_type, **kw)
    rows = {
        label: _make_object(client, admin, case, label=label, properties={"model": value})[
            "id"
        ]
        for label, value in values.items()
    }
    return case, rows


def test_글을_참조로_바꾸면_값마다_이름을_풀고_못_푼_값은_대체_값을_정해야_한다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    w = _world(client, admin)
    tag = w["tag"]
    case, rows = _cases(
        client,
        admin,
        {
            "식별자": f"{tag}-A",
            "이름": f"모델 {tag} B",
            "여럿": f"쌍둥이 {tag}",
            "없음": f"없는 {tag}",
        },
    )
    asked = {
        "data_type": "object_ref",
        "ref_type_slug": w["model"],
        "inverse_label": "시장 서비스",
    }
    plan = _retype(client, admin, case, "model", **asked).json()
    assert plan["errors"], plan
    failing = {one["value"]: one["reason"] for one in plan["failures"]}
    assert set(failing) == {f"쌍둥이 {tag}", f"없는 {tag}"}
    assert "2개에 맞습니다" in failing[f"쌍둥이 {tag}"]
    assert "찾지 못했습니다" in failing[f"없는 {tag}"]

    # 대체 값은 상대의 식별자 · 이름 · id — 여기서는 id 하나, 그리고 값 삭제.
    mapping = {f"쌍둥이 {tag}": w["twin1"], f"없는 {tag}": None}
    done = _retype(client, admin, case, "model", mapping=mapping, apply=True, **asked).json()
    assert done["applied"] is True, done
    values = {label: _value(client, admin, case, one, "model") for label, one in rows.items()}
    assert values == {"식별자": w["a"], "이름": w["b"], "여럿": w["twin1"], "없음": None}
    prop = _props(client, admin, case)["model"]
    assert (prop["data_type"], prop["ref_type_slug"], prop["inverse_label"]) == (
        "object_ref",
        w["model"],
        "시장 서비스",
    )

    # 이어졌다 — 모델 쪽 「가리키는 것」 에 기록이 보인다(참조 색인이 따라왔다).
    pointing = client.get(
        f"/api/objects/{w['model']}/{w['a']}/references", headers=admin.headers
    ).json()
    assert pointing["total"] == 1, pointing

    # 이력에는 **바뀐 칸만** — 속성 전체를 두 번 담지 않는다.
    entry = db.scalars(
        select(AuditEntry).where(
            AuditEntry.target_id == uuid.UUID(rows["식별자"]),
            AuditEntry.reason.contains("속성 종류 변경"),
        )
    ).one()
    assert entry.changes["properties"] == {
        "before": {"model": f"{tag}-A"},
        "after": {"model": w["a"]},
        "keys": ["model"],
    }

    # 그 전 이력으로 되돌리면 그때 글자를 이름으로 풀어 넣는다.
    history = client.get(
        f"/api/objects/{case}/{rows['이름']}/history", headers=admin.headers
    ).json()
    first = history[-1]
    assert first["snapshot"]["properties"] == {"model": f"모델 {tag} B"}
    assert history[0]["changes"]["properties.model"] == {
        "before": f"모델 {tag} B",
        "after": w["b"],
    }
    restored = client.post(
        f"/api/objects/{case}/{rows['이름']}/restore",
        json={"entry_id": first["id"]},
        headers=admin.headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["properties"] == {"model": w["b"]}


def test_참조를_글로_바꾸면_식별자가_되고_다시_참조로_바꾸면_같은_객체다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    case, rows = _cases(
        client,
        admin,
        {"식별자 있음": w["a"], "식별자 없음": w["solo"]},
        data_type="object_ref",
        ref_type_slug=w["model"],
    )
    done = _retype(client, admin, case, "model", data_type="text", apply=True).json()
    assert done["applied"] is True, done
    assert _value(client, admin, case, rows["식별자 있음"], "model") == f"{w['tag']}-A"
    assert _value(client, admin, case, rows["식별자 없음"], "model") == f"외톨이 {w['tag']}"
    assert _props(client, admin, case)["model"]["ref_type_slug"] is None

    back = _retype(
        client,
        admin,
        case,
        "model",
        data_type="object_ref",
        ref_type_slug=w["model"],
        apply=True,
    ).json()
    assert back["applied"] is True, back
    assert _value(client, admin, case, rows["식별자 있음"], "model") == w["a"]
    assert _value(client, admin, case, rows["식별자 없음"], "model") == w["solo"]


def test_참조에서_글로_바뀐_뒤_그_전_이력으로_되돌리면_식별자로_넣는다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    case, rows = _cases(
        client,
        admin,
        {"건": w["a"]},
        data_type="object_ref",
        ref_type_slug=w["model"],
    )
    one = rows["건"]
    client.patch(
        f"/api/objects/{case}/{one}",
        json={"properties": {"model": w["b"]}},
        headers=admin.headers,
    ).raise_for_status()
    done = _retype(client, admin, case, "model", data_type="text", apply=True).json()
    assert done["applied"] is True, done
    first = client.get(f"/api/objects/{case}/{one}/history", headers=admin.headers).json()[-1]
    assert first["snapshot"]["properties"] == {"model": w["a"]}
    restored = client.post(
        f"/api/objects/{case}/{one}/restore",
        json={"entry_id": first["id"]},
        headers=admin.headers,
    )
    assert restored.status_code == 200, restored.text
    # id 가 아니라 그 객체의 식별자 — 지금 칸은 글이다.
    assert restored.json()["properties"] == {"model": f"{w['tag']}-A"}


def test_선택에서_참조로_가고_정의_가져오기로도_같은_길이다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    tag = w["tag"]
    case, rows = _cases(
        client,
        admin,
        {"가": f"{tag}-A", "나": f"{tag}-B"},
        data_type="enum",
        enum_options=[f"{tag}-A", f"{tag}-B"],
    )
    plan = _retype(
        client, admin, case, "model", data_type="object_ref", ref_type_slug=w["model"]
    ).json()
    assert plan["errors"] == [] and plan["types"][0]["converted"] == 2, plan

    # 정의 가져오기 — 대체 값을 적을 자리가 없으니 다 풀리는 것만 간다.
    schema = {
        "types": [
            {
                "slug": case,
                "label": "시장 서비스",
                "properties": [
                    {
                        "key": "model",
                        "label": "모델",
                        "data_type": "object_ref",
                        "ref_type_slug": w["model"],
                    }
                ],
            }
        ]
    }
    planned = client.post(
        "/api/ontology/import?dry_run=true", json=schema, headers=admin.headers
    ).json()
    assert planned["errors"] == [], planned
    assert any("2개 변환" in one for one in planned["warnings"]), planned["warnings"]
    applied = client.post(
        "/api/ontology/import?dry_run=false", json=schema, headers=admin.headers
    ).json()
    assert applied["applied"] is True, applied
    assert _value(client, admin, case, rows["가"], "model") == w["a"]


def test_숫자는_참조와_오가지_않고_없는_대상은_거절한다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    case, _ = _cases(client, admin, {"건": 1}, data_type="number")
    refused = _retype(
        client, admin, case, "model", data_type="object_ref", ref_type_slug=w["model"]
    )
    assert refused.status_code == 409 and _code(refused).endswith("ONTOLOGY-0064")

    text, _ = _cases(client, admin, {"건": "A"})
    unknown = _retype(
        client, admin, text, "model", data_type="object_ref", ref_type_slug="no_such_type"
    )
    assert unknown.status_code == 404, unknown.text


def test_값이_많은_타입은_작업으로_계획하고_지문이_같을_때만_적용한다(
    client: TestClient, admin: Signed, monkeypatch: Any
) -> None:
    """기록 200만 건의 변환은 요청 안에서 안 끝난다 — 문턱을 넘으면 작업이다(ADR 0009).
    여기서는 문턱을 2건으로 낮춰 본다."""
    from app.modules.ontology import retype

    monkeypatch.setattr(retype, "RETYPE_INLINE", 2)
    w = _world(client, admin)
    tag = w["tag"]
    case, rows = _cases(
        client, admin, {"가": f"{tag}-A", "나": f"{tag}-B", "다": f"없는 {tag}"}
    )
    asked = {"data_type": "object_ref", "ref_type_slug": w["model"]}
    inline = _retype(client, admin, case, "model", **asked)
    assert inline.status_code == 409 and _code(inline).endswith("ONTOLOGY-0067")
    job_path = inline.json()["error"]["details"]["job_path"]

    made = client.post(job_path, json={**asked, "apply": True}, headers=admin.headers)
    assert made.status_code == 202, made.text
    plan = finish_job(client, admin, made.json())
    assert plan["status"] == "done", plan
    assert plan["result"]["applied"] is False, "작업은 늘 계획부터다"
    assert [one["value"] for one in plan["result"]["failures"]] == [f"없는 {tag}"]
    # 오류가 있는 계획은 적용 작업이 안 선다.
    refused = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert refused.status_code == 409, refused.text

    mapped = {**asked, "mapping": {f"없는 {tag}": None}}
    made = client.post(job_path, json=mapped, headers=admin.headers)
    plan = finish_job(client, admin, made.json())
    assert plan["result"]["errors"] == [] and plan["result"]["fingerprint"], plan

    # 계획과 적용 사이에 건수가 달라지면(누가 기록을 넣었다) 지문이 다르다 — 아무것도 안
    # 바꾼다. 지문은 건수 · 못 푼 값 · 대체 값이다: 새로 못 푸는 값은 지문이 아니어도 계획의
    # 오류가 막는다.
    rows["라"] = _make_object(
        client, admin, case, label="라", properties={"model": f"모델 {tag} B"}
    )["id"]
    applied = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    stale = finish_job(client, admin, applied.json())
    assert stale["status"] == "failed" and "JOBS-0020" in (stale["error"] or ""), stale
    assert _value(client, admin, case, rows["나"], "model") == f"{tag}-B"

    made = client.post(job_path, json=mapped, headers=admin.headers)
    plan = finish_job(client, admin, made.json())
    applied = client.post(f"/api/jobs/{plan['id']}/apply", headers=admin.headers)
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done" and done["result"]["applied"] is True, done
    assert done["result"]["snapshot_id"]
    values = {label: _value(client, admin, case, one, "model") for label, one in rows.items()}
    assert values == {"가": w["a"], "나": w["b"], "다": None, "라": w["b"]}
