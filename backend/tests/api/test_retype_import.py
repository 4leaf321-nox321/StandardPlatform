"""종류 변경 — **정의 가져오기 · 허브 묶음 · 스냅샷 복원이 같은 규칙으로** 저장값을
변환한다(ADR 0007).

화면보다 먼저 선다: 화면이 내주는 「종류 변경 직전」 스냅샷이 실제로 복원돼야 하고, 허브에서
바꾼 종류가 쌍둥이로 전해져야 한다. 가져오기에는 대체 값을 적을 자리가 없으니 변환할 수 없는
값은 오류다 — 조용히 비우지 않는다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.ontology.models import OntologySnapshot
from tests.api.conftest import Signed, bundle_import, finish_job
from tests.api.test_interfaces import _add_common, _make_interface, _props
from tests.api.test_ontology import (
    _import,
    _make_object,
    _make_property,
    _make_relation,
    _make_type,
    _uniq,
)


def _retype(slug: str, key: str, data_type: str, **more: Any) -> dict[str, Any]:
    return {
        "types": [
            {
                "slug": slug,
                "properties": [{"key": key, "label": key, "data_type": data_type, **more}],
            }
        ]
    }


def _value(client: TestClient, who: Signed, slug: str, object_id: str, key: str) -> Any:
    got = client.get(f"/api/objects/{slug}/{object_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return got.json()["object"]["properties"].get(key)


def test_가져오기로_종류를_바꾸면_계획이_세고_변환할_수_없는_값을_들어_막는다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품", key_policy="optional")
    _make_property(client, admin, part, key="w", label="무게", data_type="text")
    light = _make_object(client, admin, part, label="가벼운 것", properties={"w": "1,234"})
    heavy = _make_object(client, admin, part, label="무거운 것", properties={"w": "12 kg"})

    plan = _import(client, admin, _retype(part, "w", "number")).json()
    assert any("12 kg" in one and "종류 변경" in one for one in plan["errors"]), plan
    # 미리 보기는 아무것도 안 바꾼다.
    assert _value(client, admin, part, light["id"], "w") == "1,234"

    # 화면에서 그 값을 고친 뒤 다시 — 이제 변환된다.
    client.patch(
        f"/api/objects/{part}/{heavy['id']}",
        json={"properties": {"w": "12"}},
        headers=admin.headers,
    ).raise_for_status()
    plan = _import(client, admin, _retype(part, "w", "number")).json()
    assert plan["errors"] == [], plan
    assert any("저장값 2개 변환" in one for one in plan["warnings"]), plan["warnings"]
    done = _import(client, admin, _retype(part, "w", "number"), dry_run=False).json()
    assert done["applied"] is True, done
    assert _value(client, admin, part, light["id"], "w") == 1234
    assert _value(client, admin, part, heavy["id"], "w") == 12

    # 객체 이력에 한 번(적용은 한 번만 돈다).
    history = client.get(
        f"/api/objects/{part}/{light['id']}/history", headers=admin.headers
    ).json()
    retyped = [one for one in history if "속성 종류 변경" in (one["reason"] or "")]
    assert len(retyped) == 1, history


def test_종류가_바뀐_뒤에도_그_전_스냅샷을_복원하면_값도_돌아간다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    part = _make_type(client, admin, key_policy="optional")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")
    row = _make_object(client, admin, part, label="볼트", properties={"qty": 3})

    done = _import(client, admin, _retype(part, "qty", "text"), dry_run=False).json()
    assert done["applied"] is True and done["snapshot_id"], done
    assert _value(client, admin, part, row["id"], "qty") == "3"

    # 「종류 변경 직전」 의 이 타입 — 시험 DB 는 스위트가 함께 써서, 통째 스냅샷을 되돌리면
    # 다른 시험이 남긴 정의(등록 안 된 원 표 등)에 걸린다. 그래서 이 타입만 담은 스냅샷으로
    # 본다.
    snapshot = OntologySnapshot(
        actor_label="시험",
        reason="종류 변경 직전",
        schema={
            "types": [
                {
                    "slug": part,
                    "properties": [{"key": "qty", "label": "수량", "data_type": "number"}],
                }
            ]
        },
    )
    db.add(snapshot)
    db.commit()
    restored = client.post(
        f"/api/ontology/snapshots/{snapshot.id}/restore", headers=admin.headers
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["applied"] is True, restored.json()
    assert _value(client, admin, part, row["id"], "qty") == 3


def test_안_되는_쌍과_관계_속성은_여전히_막는다(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin)
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    # 참조는 글 · 긴 글 · 선택과만 오간다(ADR 0009) — 숫자를 거치면 식별자 「007」 이 「7」
    # 이 된다.
    plan = _import(
        client, admin, _retype(part, "weight", "object_ref", ref_type_slug=part)
    ).json()
    assert any("에서만 바꿉니다" in one for one in plan["errors"]), plan

    # 관계 종류의 속성은 값이 관계 줄에 있어 이번 범위 밖이다.
    relation = _uniq("uses")

    def relation_with(data_type: str) -> dict[str, Any]:
        return {
            "relation_types": [
                {
                    "slug": relation,
                    "label": "사용",
                    "properties": [{"key": "n", "label": "건수", "data_type": data_type}],
                }
            ]
        }

    made = _import(client, admin, relation_with("number"), dry_run=False).json()
    assert made["applied"] is True, made
    plan = _import(client, admin, relation_with("text")).json()
    assert any("관계 종류의 속성" in one for one in plan["errors"]), plan


def test_롤업이_모으던_칸이_숫자가_아니게_되면_걷어내고_파일이_명시하면_막는다(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="weight", label="무게", data_type="number")
    kind = _make_relation(
        client, admin, "part_of", label="속함", transitive=True, acyclic=True
    )
    view = {
        "tree": {"relation": kind, "parent": "dst"},
        "rollups": [{"property": "weight", "fn": "sum"}],
    }
    client.patch(
        f"/api/ontology/types/{part}", json={"list_view": view}, headers=admin.headers
    ).raise_for_status()

    explicit = _retype(part, "weight", "text")
    explicit["types"][0]["list_view"] = view
    plan = _import(client, admin, explicit).json()
    assert any("롤업" in one for one in plan["errors"]), plan

    plan = _import(client, admin, _retype(part, "weight", "text")).json()
    assert any("롤업" in one for one in plan["warnings"]), plan
    done = _import(client, admin, _retype(part, "weight", "text"), dry_run=False).json()
    assert done["applied"] is True, done
    # 남은 롤업이 다음 타입 수정을 막지 않는다.
    again = client.patch(
        f"/api/ontology/types/{part}", json={"label": "부품2"}, headers=admin.headers
    )
    assert again.status_code == 200, again.text
    assert "rollups" not in (again.json().get("list_view") or {})


def test_변환으로_유일한_값이_겹치면_막고_기본값도_같은_규칙으로(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, key_policy="optional")
    _make_property(
        client, admin, part, key="code", label="코드", data_type="text", unique=True
    )
    _make_object(client, admin, part, label="하나", properties={"code": "010"})
    _make_object(client, admin, part, label="둘", properties={"code": "10"})
    plan = _import(client, admin, _retype(part, "code", "number")).json()
    assert any("겹칩니다" in one for one in plan["errors"]), plan

    other = _make_type(client, admin)
    _make_property(
        client, admin, other, key="size", label="크기", data_type="text", default_value="5"
    )
    done = _import(client, admin, _retype(other, "size", "number"), dry_run=False).json()
    assert done["applied"] is True, done
    assert _props(client, admin, other)["size"]["default_value"] == 5

    _make_property(
        client, admin, other, key="mark", label="표시", data_type="text", default_value="큼"
    )
    plan = _import(client, admin, _retype(other, "mark", "number")).json()
    assert any("큼" in one for one in plan["errors"]), plan


def test_인터페이스의_종류를_바꾸면_파일에_없던_구현_타입의_값도_변환한다(
    client: TestClient, admin: Signed
) -> None:
    iface = _make_interface(client, admin)
    _add_common(
        client, admin, iface, key="power", label="출력", data_type="text"
    ).raise_for_status()
    _add_common(
        client,
        admin,
        iface,
        key="country",
        label="국가",
        data_type="enum",
        enum_options=["KR", "US"],
    ).raise_for_status()
    kind = _make_type(client, admin, key_policy="optional", interface_slugs=[iface])
    row = _make_object(
        client, admin, kind, label="장비", properties={"power": "10", "country": "KR"}
    )

    body = {
        "interfaces": [
            {
                "slug": iface,
                "properties": [
                    {"key": "power", "label": "출력", "data_type": "number"},
                    {"key": "country", "label": "국가", "data_type": "text"},
                ],
            }
        ]
    }
    plan = _import(client, admin, body).json()
    assert plan["errors"] == [], plan
    assert any(f"인터페이스 {iface}" in one for one in plan["warnings"]), plan["warnings"]
    # 종류가 바뀌는 것을 「고를 값에서 뺍니다」 로 말하지 않는다.
    assert not any("고를 값에서" in one for one in plan["warnings"]), plan["warnings"]

    done = _import(client, admin, body, dry_run=False).json()
    assert done["applied"] is True, done
    assert _value(client, admin, kind, row["id"], "power") == 10
    assert _value(client, admin, kind, row["id"], "country") == "KR"
    assert _props(client, admin, kind)["power"]["data_type"] == "number"


def test_허브가_관리하는_타입은_변환할_수_없는_값을_비우고_경고한다(
    client: TestClient, admin: Signed
) -> None:
    """쌍둥이는 허브의 객체를 못 고친다 — 막으면 허브의 종류 변경이 영영 안 온다."""
    slug = _uniq("hubpart")
    hub: dict[str, Any] = {
        "ontology": {
            "types": [
                {
                    "slug": slug,
                    "label": "허브 부품",
                    "key_policy": "required",
                    "properties": [{"key": "w", "label": "무게", "data_type": "text"}],
                }
            ]
        },
        "objects": [
            {
                "type_slug": slug,
                "workspace_slug": admin.workspace,
                "rows": [
                    {"key": "P-1", "label": "하나", "w": "5"},
                    {"key": "P-2", "label": "둘", "w": "많이"},
                ],
            }
        ],
        "source": "hub",
        "apply": True,
    }
    assert bundle_import(client, admin, hub)["applied"] is True

    hub["ontology"]["types"][0]["properties"] = [
        {"key": "w", "label": "무게", "data_type": "number"}
    ]
    hub["objects"][0]["rows"] = [
        {"key": "P-1", "label": "하나", "w": "5"},
        {"key": "P-2", "label": "둘", "w": "7"},
    ]
    preview = bundle_import(client, admin, {**hub, "apply": False})
    assert preview["applied"] is False and preview["ontology"]["errors"] == [], preview
    assert any("비웁니다" in one for one in preview["ontology"]["warnings"]), preview
    # 미리 보기는 아무것도 안 남긴다.
    rows = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"]
    assert {one["properties"].get("w") for one in rows} == {"5", "많이"}

    assert bundle_import(client, admin, hub)["applied"] is True
    rows = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"]
    assert {one["key"]: one["properties"].get("w") for one in rows} == {"P-1": 5, "P-2": 7}


def test_종류가_바뀐_뒤_앞_묶음을_되돌리면_맞지_않는_값은_그_줄을_오류로(
    client: TestClient, admin: Signed
) -> None:
    part = _make_type(client, admin, key_policy="required")
    _make_property(client, admin, part, key="grade", label="등급", data_type="text")
    first = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": part,
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "P-1", "label": "볼트", "grade": "A"}],
                }
            ],
            "apply": True,
        },
    )
    second = bundle_import(
        client,
        admin,
        {
            "objects": [
                {
                    "type_slug": part,
                    "workspace_slug": admin.workspace,
                    "rows": [{"key": "P-1", "label": "볼트", "grade": "B"}],
                }
            ],
            "apply": True,
        },
    )
    assert first["applied"] and second["applied"]
    # 같은 글자를 지키는 종류 변경 — 「B」 는 고를 값이 되지만 「A」 는 아니다.
    done = _import(
        client, admin, _retype(part, "grade", "enum", enum_options=["B"]), dry_run=False
    ).json()
    assert done["applied"] is True, done

    started = client.post(
        f"/api/bundles/runs/{second['run_id']}/undo",
        params={"apply": False},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    result = finish_job(client, admin, started.json())["result"]
    row = next(one for one in result["rows"] if one.get("label", "").endswith("볼트"))
    assert row["action"] == "error" and "속성 종류 변경" in row["message"], result


def test_일괄_입력도_같은_숫자_규칙(client: TestClient, admin: Signed) -> None:
    part = _make_type(client, admin, key_policy="required")
    _make_property(client, admin, part, key="qty", label="수량", data_type="number")
    plan = client.post(
        f"/api/objects/{part}/import-rows",
        json={
            "rows": [
                {"key": "P-1", "label": "하나", "qty": "1,234"},
                {"key": "P-2", "label": "둘", "qty": "nan"},
            ]
        },
        headers=admin.headers,
    ).json()
    actions = [one["action"] for one in plan["rows"]]
    assert actions == ["create", "error"], plan["rows"]
