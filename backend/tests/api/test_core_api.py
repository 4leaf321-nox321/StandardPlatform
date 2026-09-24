"""코어 API — **바깥 시스템이 당겨 가는 창구.**

여기서 지키는 것: 연 것만 나가나, 지난번 이후만 오나, **사라진 것을 알려 주나**,
참조가 상대의 식별자로 나가나, 그리고 **좁은 토큰이 코어 밖을 못 읽나.**
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def _open(client: TestClient, admin: Signed, slug: str, on: bool = True) -> None:
    got = client.patch(f"/api/ontology/types/{slug}", json={"core": on}, headers=admin.headers)
    assert got.status_code == 200, got.text


def _world(client: TestClient, admin: Signed) -> tuple[str, str]:
    """공급사(코어) 와 부품(코어) — 부품이 공급사를 참조로 가리킨다."""
    tag = uuid.uuid4().hex[:6]
    vendor = _make_type(client, admin, label=f"공급사{tag}", key_policy="optional")
    part = _make_type(client, admin, label=f"부품{tag}", key_policy="optional")
    _make_property(
        client,
        admin,
        part,
        key="grade",
        label="등급",
        data_type="enum",
        enum_options=["A", "B"],
    )
    _make_property(
        client,
        admin,
        part,
        key="vendor",
        label="공급사",
        data_type="object_ref",
        ref_type_slug=vendor,
    )
    _make_property(
        client, admin, part, key="tags", label="꼬리표", data_type="text", multi=True
    )
    _open(client, admin, vendor)
    _open(client, admin, part)
    return vendor, part


def _rows(client: TestClient, who: Signed, slug: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/core/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_연_타입만_카탈로그에_선다(client: TestClient, admin: Signed) -> None:
    """목록 API 는 볼 수 있는 전부를 연다 — 여기는 **연 것만.**"""
    vendor, part = _world(client, admin)
    closed = _make_type(client, admin, label=f"비공개{uuid.uuid4().hex[:6]}")

    body = client.get("/api/core", headers=admin.headers).json()
    slugs = {one["slug"] for one in body["types"]}
    assert {vendor, part} <= slugs
    assert closed not in slugs

    # 받는 쪽이 하드코딩하지 않게 **칸과 주소를 우리가 적어 준다.**
    one = next(row for row in body["types"] if row["slug"] == part)
    keys = {p["key"]: p for p in one["properties"]}
    assert keys["grade"]["enum_options"] == ["A", "B"]
    assert keys["vendor"]["ref_type_slug"] == vendor
    assert keys["tags"]["multi"] is True
    assert one["endpoint"].endswith(f"/core/{part}")
    assert body["revision"] and body["system"]

    # 닫으면 사라진다.
    _open(client, admin, part, on=False)
    after = client.get("/api/core", headers=admin.headers).json()
    assert part not in {row["slug"] for row in after["types"]}
    assert client.get(f"/api/core/{part}", headers=admin.headers).status_code == 404


def test_끝_슬래시도_받는다(client: TestClient, admin: Signed) -> None:
    """받는 쪽 클라이언트가 리다이렉트를 안 따라가게 해 두면(흔하다) 슬래시 하나로 막힌다 —
    그 하나로 남의 팀이 「창구가 없다」 를 보게 할 이유가 없다. 실측(RA 첫 연결)."""
    _world(client, admin)
    plain = client.get("/api/core", headers=admin.headers)
    slashed = client.get("/api/core/", headers=admin.headers, follow_redirects=False)
    assert plain.status_code == 200 and slashed.status_code == 200, slashed.text
    assert {one["slug"] for one in slashed.json()["types"]} == {
        one["slug"] for one in plain.json()["types"]
    }
    # 주소도 같아야 한다 — 끝 슬래시가 endpoint 에 묻어 나가면 받는 쪽이 이어 붙일 때 깨진다.
    assert slashed.json()["types"][0]["endpoint"] == plain.json()["types"][0]["endpoint"]


def test_행은_봉투와_알맹이로_나뉘고_참조는_식별자로_나간다(
    client: TestClient, admin: Signed
) -> None:
    vendor, part = _world(client, admin)
    acme = _make_object(client, admin, vendor, label="ACME", key="ACME-001")["id"]
    # 화면·API 로 만들 때 참조는 id 다 — **파일로 넣을 때만** 식별자로 푼다.
    _make_object(
        client,
        admin,
        part,
        label="볼트",
        key="B-1",
        properties={"grade": "A", "vendor": acme, "tags": ["철", "표준"]},
    )

    body = _rows(client, admin, part)
    row = next(one for one in body["items"] if one["key"] == "B-1")
    assert row["label"] == "볼트" and row["status"] == "active" and row["deleted"] is False
    # 참조는 **상대의 key** — id 로 주면 받는 쪽이 아무것도 못 가리킨다.
    assert row["properties"]["vendor"] == "ACME-001"
    assert row["properties"]["tags"] == ["철", "표준"]
    # 빈 값은 키를 뺀다 — 카탈로그에 칸 목록이 있으니 없는 키가 곧 빈 값이다.
    assert "description" not in row["properties"]
    assert body["as_of"] and body["next"] is None


def test_지난번_이후만_오고_사라진_것도_온다(client: TestClient, admin: Signed) -> None:
    """목록은 살아 있는 것만 준다 — 그러면 받는 쪽은 **삭제를 영영 모른다.**"""
    vendor, _part = _world(client, admin)
    first = _make_object(client, admin, vendor, label="ACME", key="ACME-001")
    _make_object(client, admin, vendor, label="한화", key="HAN-002")

    full = _rows(client, admin, vendor)
    assert {one["key"] for one in full["items"]} == {"ACME-001", "HAN-002"}
    mark = full["as_of"]

    # 아무것도 안 바뀌었으면 빈 쪽이 온다 — 그게 「받아 갈 것 없음」 이다.
    assert _rows(client, admin, vendor, since=mark)["items"] == []

    client.patch(
        f"/api/objects/{vendor}/{first['id']}",
        json={"description": "이름 바꿈"},
        headers=admin.headers,
    )
    delta = _rows(client, admin, vendor, since=mark)
    assert [one["key"] for one in delta["items"]] == ["ACME-001"]

    # 지우면 **무덤이 온다.**
    gone = client.delete(f"/api/objects/{vendor}/{first['id']}", headers=admin.headers)
    assert gone.status_code in (200, 204), gone.text
    after = _rows(client, admin, vendor, since=delta["as_of"])
    grave = next(one for one in after["items"] if one["key"] == "ACME-001")
    assert grave["deleted"] is True and grave["properties"] == {}

    # 처음 받아 가는 쪽에는 무덤을 안 보낸다 — 없던 것을 지우라고 할 이유가 없다.
    fresh = _rows(client, admin, vendor)
    assert all(not one["deleted"] for one in fresh["items"])


def test_합쳐져_사라진_것은_이긴_쪽을_알려_준다(client: TestClient, admin: Signed) -> None:
    """받는 쪽이 **제 참조를 옮길 수 있어야** 한다."""
    vendor, _part = _world(client, admin)
    loser = _make_object(client, admin, vendor, label="ACME Inc.", key="ACME-OLD")
    winner = _make_object(client, admin, vendor, label="ACME", key="ACME-001")
    mark = _rows(client, admin, vendor)["as_of"]

    merged = client.post(
        f"/api/objects/{vendor}/{loser['id']}/merge",
        json={"into": winner["id"]},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text

    after = _rows(client, admin, vendor, since=mark)
    grave = next(one for one in after["items"] if one["key"] == "ACME-OLD")
    assert grave["deleted"] is True and grave["merged_into"] == "ACME-001"


def test_쪽이_남으면_시계를_안_옮긴다(client: TestClient, admin: Signed) -> None:
    """거기서 멈춘 쪽이 남은 쪽을 영영 안 받게 되기 때문이다."""
    vendor, _part = _world(client, admin)
    for at in range(3):
        _make_object(client, admin, vendor, label=f"공급사{at}", key=f"V-{at}")

    first = _rows(client, admin, vendor, limit=2)
    assert len(first["items"]) == 2 and first["next"]
    # **남았으면 시계를 안 준다.** 여기서 멈춘 쪽이 남은 쪽을 영영 안 받는 일을 막는다.
    assert first["as_of"] is None

    second = _rows(client, admin, vendor, cursor=first["next"], limit=2)
    assert [one["key"] for one in second["items"]] == ["V-2"]
    assert second["next"] is None and second["as_of"]


def test_좁은_토큰은_코어_밖을_못_읽는다(client: TestClient, admin: Signed) -> None:
    """바깥에 주는 토큰에 `read` 를 주면 그 계정이 볼 수 있는 전부가 나간다 —
    연동 하나 때문에 사내 전체를 여는 일이 된다."""
    vendor, _part = _world(client, admin)
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"matnexus-{uuid.uuid4().hex[:6]}", "scopes": ["core:read"]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    outside = {"Authorization": f"Bearer {made.json()['token']}"}

    assert client.get("/api/core", headers=outside).status_code == 200
    assert client.get(f"/api/core/{vendor}", headers=outside).status_code == 200
    # 코어 밖은 막힌다.
    denied = client.get(f"/api/objects/{vendor}", headers=outside)
    assert denied.status_code == 403
    assert "AUTH-0104" in denied.json()["error"]["code"]
    # 쓰기는 말할 것도 없다.
    assert (
        client.post(
            f"/api/objects/{vendor}", json={"label": "몰래"}, headers=outside
        ).status_code
        == 403
    )


def test_투영_타입은_못_연다(client: TestClient, admin: Signed, db: Session) -> None:
    """행이 원 표(부서 · 계정)에 있어 사람 정보가 그대로 나간다 — 다른 판단이다."""
    projected = _make_type(
        client,
        admin,
        label=f"부서투영{uuid.uuid4().hex[:6]}",
        kind_class="system",
        system_source="workspace",
    )
    denied = client.patch(
        f"/api/ontology/types/{projected}", json={"core": True}, headers=admin.headers
    )
    assert denied.status_code == 409
    assert "외부에 공개할 수 없습니다" in denied.json()["error"]["message"]


# --- 연 뒤의 약속 -----------------------------------------------------------------


def _token(client: TestClient, admin: Signed, name: str, scopes: list[str]) -> str:
    made = client.post(
        "/api/auth/tokens",
        json={"name": name, "scopes": scopes},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return str(made.json()["token"])


def test_연_타입의_칸은_한_번_더_묻고_지운다(client: TestClient, admin: Signed) -> None:
    """이 칸의 이름은 남의 시스템 코드에 박혀 있다 — 지우면 그쪽에서 조용히 사라진다."""
    _vendor, part = _world(client, admin)
    _token(client, admin, f"matnexus-{uuid.uuid4().hex[:6]}", ["core:read"])

    # 지우기 전에 무엇이 걸렸는지 — 화면의 확인 창이 이것을 읽는다.
    usage = client.get(
        f"/api/ontology/types/{part}/properties/grade/usage", headers=admin.headers
    ).json()
    assert usage["core_open"] is True
    assert any("matnexus" in one for one in usage["core_consumers"])

    denied = client.delete(
        f"/api/ontology/types/{part}/properties/grade", headers=admin.headers
    )
    assert denied.status_code == 409
    assert "외부에 공개 중" in denied.json()["error"]["message"]
    assert denied.json()["error"]["details"]["core_consumers"]

    # **막지는 않는다** — 정말 지워야 할 때가 있고, 확인했다고 말하면 지워진다.
    gone = client.delete(
        f"/api/ontology/types/{part}/properties/grade?accept_core=true", headers=admin.headers
    )
    assert gone.status_code == 204, gone.text


def test_닫힌_타입은_묻지_않는다(client: TestClient, admin: Signed) -> None:
    """열지 않은 타입에까지 경고를 세우면 그 경고는 곧 아무도 안 읽는다."""
    _vendor, part = _world(client, admin)
    _open(client, admin, part, on=False)
    usage = client.get(
        f"/api/ontology/types/{part}/properties/grade/usage", headers=admin.headers
    ).json()
    assert usage["core_open"] is False and usage["core_consumers"] == []
    assert (
        client.delete(
            f"/api/ontology/types/{part}/properties/grade", headers=admin.headers
        ).status_code
        == 204
    )


def test_연_타입은_끄기_전에_못_지운다(client: TestClient, admin: Signed) -> None:
    """열린 채로 지우면 남의 동기화가 404 를 받고, 그것이 「잠깐 장애」 인지 「없어진 것」 인지
    그쪽은 구별할 수 없다."""
    vendor, _part = _world(client, admin)
    denied = client.delete(f"/api/ontology/types/{vendor}", headers=admin.headers)
    assert denied.status_code == 409
    assert "먼저 「코어」 를 해제" in denied.json()["error"]["message"]

    _open(client, admin, vendor, on=False)
    assert (
        client.delete(f"/api/ontology/types/{vendor}", headers=admin.headers).status_code
        == 204
    )


def test_받아_간_것이_감사에_남는다(client: TestClient, admin: Signed) -> None:
    """토큰의 「마지막 사용」 만으로는 **어느 타입을 받아 갔는지** 알 수 없다."""
    vendor, _part = _world(client, admin)
    _make_object(client, admin, vendor, label="ACME", key="ACME-001")
    raw = _token(client, admin, f"matnexus-{uuid.uuid4().hex[:6]}", ["core:read"])
    outside = {"Authorization": f"Bearer {raw}"}

    body = _rows_as(client, outside, vendor)
    assert len(body["items"]) == 1

    recent = client.get(
        "/api/audit/entries?action=core.pull&limit=50", headers=admin.headers
    ).json()
    rows = recent["items"]
    pulled = next(one for one in rows if one["target_label"] == vendor)
    assert pulled["target_label"] == vendor
    # **통로가 함께 남는다** — 사람이 화면에서 본 것과 기계가 받아 간 것이 구별된다.
    assert "matnexus" in (pulled["actor_token"] or "")

    # 빈 응답(받아 갈 것 없음)은 안 남긴다 — 새벽마다 쌓이면 그 목록은 아무도 안 읽는다.
    before = recent["total"]
    _rows_as(client, outside, vendor, since=body["as_of"])
    after = client.get(
        "/api/audit/entries?action=core.pull&limit=1", headers=admin.headers
    ).json()
    assert after["total"] == before


def test_공개를_켜고_끈_일이_감사에_남는다(client: TestClient, admin: Signed) -> None:
    """「지금 무엇이 나가나」 는 코어 현황이 답한다.

    **「언제 누가 공개했나」 는 여기서만 답한다.**

    개발 설치에서 코어로 켜진 타입 하나의 사연을 아무도 댈 수 없었다 — 타입 수정 기록에
    섞여 있으면 그 한 줄을 찾을 수 없다.
    """
    slug = _make_type(
        client, admin, label=f"공개이력{uuid.uuid4().hex[:6]}", key_policy="optional"
    )

    def logged() -> list[dict[str, Any]]:
        got = client.get(
            "/api/audit/entries?action=ontology.type.core&limit=50", headers=admin.headers
        ).json()
        return [one for one in got["items"] if one["target_label"] == slug]

    _open(client, admin, slug)
    opened = logged()
    assert len(opened) == 1
    assert opened[0]["changes"] == {"core": True, "was": False}

    # **값이 안 바뀌면 안 남긴다.** 이름만 고칠 때마다 공개 이력이 늘면 그 목록은 곧 안 읽힌다.
    _open(client, admin, slug)
    assert len(logged()) == 1

    _open(client, admin, slug, on=False)
    shut = logged()
    assert len(shut) == 2
    assert {"core": False, "was": True} in [one["changes"] for one in shut]


def _rows_as(
    client: TestClient, headers: dict[str, str], slug: str, **params: Any
) -> dict[str, Any]:
    got = client.get(f"/api/core/{slug}", params=params, headers=headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_코어_현황이_한_화면에_모인다(client: TestClient, admin: Signed) -> None:
    """연 것 · 읽을 수 있는 자격 · 받아 간 기록이 흩어져 있으면 「지금 바깥으로 뭐가 나가고
    있지」 를 아무도 한눈에 답할 수 없다."""
    vendor, _part = _world(client, admin)
    _make_object(client, admin, vendor, label="ACME", key="ACME-001")
    raw = _token(client, admin, f"matnexus-{uuid.uuid4().hex[:6]}", ["core:read"])
    _rows_as(client, {"Authorization": f"Bearer {raw}"}, vendor)

    body = client.get("/api/ontology/core-status", headers=admin.headers).json()
    assert body["base"].endswith("/core")
    assert vendor in {one["slug"] for one in body["types"]}

    mine = next(one for one in body["consumers"] if "matnexus" in one["name"])
    # **좁은 자격인지 보인다** — `read` 토큰은 코어 밖도 읽으므로 무게가 다르다.
    assert mine["narrow"] is True and mine["last_used_at"]

    pulled = next(one for one in body["recent"] if one["type_slug"] == vendor)
    assert pulled["rows"] >= 1 and "matnexus" in (pulled["token"] or "")


def test_현황은_시스템_관리자만(client: TestClient, member: Signed, admin: Signed) -> None:
    """받아 가는 쪽이 다른 연동의 이름까지 보면 안 된다 — 그래서 코어 창구 밖에 둔다."""
    _world(client, admin)
    raw = _token(client, admin, f"outside-{uuid.uuid4().hex[:6]}", ["core:read"])
    assert (
        client.get(
            "/api/ontology/core-status", headers={"Authorization": f"Bearer {raw}"}
        ).status_code
        == 403
    )
    assert client.get("/api/ontology/core-status", headers=member.headers).status_code == 403


def test_연동_키트에_주소와_공개_타입이_채워진다(client: TestClient, admin: Signed) -> None:
    """비워 두고 「여기에 주소를 적으십시오」 라고 하면 수신 측이 메일에서 찾아 옮겨 적다가
    오타를 낸다 — 우리가 아는 값은 우리가 채운다."""
    import io as _io
    import zipfile

    vendor, _part = _world(client, admin)
    got = client.get("/api/ontology/core-kit", headers=admin.headers)
    assert got.status_code == 200, got.text
    assert "zip" in got.headers["content-type"]

    bundle = zipfile.ZipFile(_io.BytesIO(got.content))
    names = set(bundle.namelist())
    assert names == {
        "sp-core-client/README.md",
        "sp-core-client/config.example.ini",
        "sp-core-client/sp_core_pull.py",
        "sp-core-client/check.sh",
    }

    readme = bundle.read("sp-core-client/README.md").decode()
    config = bundle.read("sp-core-client/config.example.ini").decode()
    # **API 루트가 들어가야 한다** — 창구 주소(`…/api/core`)를 넣으면 클라이언트가
    # `/core/core/<타입>` 을 부른다(실측).
    assert "/api/core" in readme  # 카탈로그 예시
    assert "base_url = " in config and config.rstrip().endswith("retries = 3")
    base_line = next(one for one in config.splitlines() if one.startswith("base_url"))
    assert base_line.strip().endswith("/api"), base_line
    assert vendor in readme and vendor in config
    # 자리표시가 남아 있으면 그대로 전달된다 — 받는 쪽은 그것을 주소로 읽는다.
    assert "{BASE}" not in readme and "{TYPES}" not in config

    # 스크립트는 **그대로 돌아가야 한다** — 문법이 깨진 채로 나가면 상대가 고치게 된다.
    import ast

    ast.parse(bundle.read("sp-core-client/sp_core_pull.py").decode())


def test_키트는_시스템_관리자만(client: TestClient, member: Signed) -> None:
    assert client.get("/api/ontology/core-kit", headers=member.headers).status_code == 403
