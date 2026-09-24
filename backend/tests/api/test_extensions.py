"""확장은 **켠 인스턴스에만 있다** — 번들 하나로 여러 플랫폼을 띄우는 장치."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.server.models import ExtensionState
from tests.api.conftest import Signed


@pytest.fixture(autouse=True)
def _clean_states(db: Session) -> Iterator[None]:
    """**켜짐은 전역 상태다.** 시험 DB 는 세션마다 한 번만 비워지므로, 여기서 지우지
    않으면 다음 시험이 「꺼진 sample」 을 물려받는다 — 실측으로 그랬다(화면 메타를
    보는 시험이 빈 목록을 받았다). 확장을 켜고 끄는 시험은 이 뒷정리를 함께 둔다.
    """
    db.execute(delete(ExtensionState))
    db.commit()
    yield
    db.execute(delete(ExtensionState))
    db.commit()


def _app_with(monkeypatch: pytest.MonkeyPatch, extensions: str) -> FastAPI:
    from app.main import create_app

    monkeypatch.setenv("EXTENSIONS", extensions)
    get_settings.cache_clear()
    app = create_app()
    from app.database import SessionLocal

    app.state.session_factory = SessionLocal
    return app


def test_켜면_있고_끄면_없다(admin: Signed, monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        with TestClient(_app_with(monkeypatch, "sample")) as on:
            body = on.get("/api/ext/sample/ping", headers=admin.headers).json()
            assert body["extension"] == "sample" and body["user"] == admin.email
            info = on.get("/api/server/status", headers=admin.headers).json()
            assert info["extensions"] == ["sample"]
            assert on.get("/api/health").json()["slug"] == get_settings().app_slug
        with TestClient(_app_with(monkeypatch, "")) as off:
            assert off.get("/api/ext/sample/ping", headers=admin.headers).status_code == 404
            assert (
                off.get("/api/server/status", headers=admin.headers).json()["extensions"] == []
            )
    finally:
        get_settings.cache_clear()


def test_env_의_오타는_기동을_막지_않고_화면이_말한다(
    admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**켜짐이 화면으로 옮겨 온 뒤의 실패 방식.**

    예전에는 `.env` 의 오타가 기동을 막았다 — 운영 재시작 중이라면 오타 하나로 서비스가
    안 뜬다. 지금은 붙는 것이 번들에 든 확장뿐이라 오타는 켜지지도 않고, 대신 서버 화면이
    「이 번들에 없는 이름」 으로 말한다. 아무 데도 안 적으면 「켰는데 메뉴가 없다」 가 된다.
    """
    try:
        with TestClient(_app_with(monkeypatch, "nope")) as web:
            info = web.get("/api/server/status", headers=admin.headers).json()
            assert info["extensions"] == []
            assert info["extensions_unknown"] == ["nope"]
            # 켜지지 않았으므로 본보기 확장도 없다.
            assert web.get("/api/ext/sample/ping", headers=admin.headers).status_code == 404
    finally:
        get_settings.cache_clear()


def _patch(client: TestClient, admin: Signed, on: bool) -> dict[str, Any]:
    got = client.patch(
        "/api/server/extensions/sample", json={"enabled": on}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_화면에서_켜고_끈다(client: TestClient, admin: Signed) -> None:
    """**시스템 관리자가 재배포 없이 켜고 끈다** — 그리고 그 일이 감사에 남는다.

    `.env` 의 기본값을 딛지 않는다. CI 에는 `.env` 가 없어서 기본값이 「꺼짐」 이고,
    거기에 기댄 시험은 로컬에서만 통과한다 — 실측으로 그랬다. 그래서 먼저 켜 놓고
    같은 자리에서 시작한다.
    """
    assert _patch(client, admin, True)["enabled"] is True
    assert client.get("/api/ext/sample/ping", headers=admin.headers).status_code == 200

    off = _patch(client, admin, False)
    assert off["enabled"] is False and off["pinned"] is True
    assert client.get("/api/ext/sample/ping", headers=admin.headers).status_code == 404

    assert _patch(client, admin, True)["enabled"] is True
    assert client.get("/api/ext/sample/ping", headers=admin.headers).status_code == 200

    entries = client.get(
        "/api/audit/entries?action=extension.toggle&limit=20", headers=admin.headers
    ).json()["items"]
    changes = [one["changes"] for one in entries if one["target_label"] == "sample"]
    assert {"enabled": False, "was": True} in changes
    assert {"enabled": True, "was": False} in changes


def test_값이_안_바뀌면_감사에_안_남는다(client: TestClient, admin: Signed) -> None:
    """이미 켜진 것을 또 켜는 일이 이력에 쌓이면 그 목록은 곧 아무도 안 읽는다."""
    _patch(client, admin, True)
    before = client.get(
        "/api/audit/entries?action=extension.toggle&limit=1", headers=admin.headers
    ).json()["total"]
    assert _patch(client, admin, True)["enabled"] is True
    after = client.get(
        "/api/audit/entries?action=extension.toggle&limit=1", headers=admin.headers
    ).json()["total"]
    assert after == before


def test_켜진_목록은_누구나_읽는다(client: TestClient, admin: Signed, member: Signed) -> None:
    """**메뉴는 모든 사람이 그린다.** 그래서 이 목록은 시스템 관리자만의 것이 아니다.

    화면이 `index.html` 의 메타만 믿으면 관리자가 끈 뒤에도 새로 고침 전까지 옛 메뉴를
    들고 있다 — 실측으로 「껐는데 본보기가 그대로」 가 나왔다. 그래서 목록을 여기서 받는다.
    """
    _patch(client, admin, True)
    got = client.get("/api/server/enabled-extensions", headers=member.headers)
    assert got.status_code == 200 and got.json() == ["sample"]

    _patch(client, admin, False)
    assert client.get("/api/server/enabled-extensions", headers=member.headers).json() == []


def test_번들에_없는_이름은_못_켠다(client: TestClient, admin: Signed) -> None:
    """고를 수 있는 것은 코드에 있는 확장뿐이다 — `.env` 오타가 반복되지 않게."""
    got = client.patch(
        "/api/server/extensions/nope", json={"enabled": True}, headers=admin.headers
    )
    assert got.status_code == 404
    assert got.json()["error"]["code"].endswith("SERVER-0001")
    assert got.json()["error"]["details"]["available"] == ["sample"]


def test_시스템_관리자만_바꾼다(client: TestClient, member: Signed) -> None:
    """기능이 나타나고 사라지는 스위치다. 부서 권한으로 만질 자리가 아니다."""
    assert client.get("/api/server/extensions", headers=member.headers).status_code == 403
    assert (
        client.patch(
            "/api/server/extensions/sample", json={"enabled": False}, headers=member.headers
        ).status_code
        == 403
    )


def test_여럿_켜면_다_붙는다(admin: Signed, monkeypatch: pytest.MonkeyPatch) -> None:
    """**둘 이상 켜는 것이 되는가** — 확장을 여러 개 개발하면 첫날 부딪히는 물음이다.

    번들에 확장이 하나뿐이라 가짜 둘을 심어 확인한다(`sys.modules` 를 먼저 보는
    importlib 의 성질). 순서는 이름 순이고, 각자 자기 경로에 붙는다.
    """
    import sys
    import types

    from fastapi import APIRouter

    for name in ("alpha", "beta"):
        module = types.ModuleType(f"app.extensions.{name}")
        made = APIRouter(prefix=f"/ext/{name}")
        made.add_api_route("/ping", (lambda n=name: {"extension": n}), methods=["GET"])
        module.register = lambda api, made=made: api.include_router(made)  # type: ignore[attr-defined]
        sys.modules[f"app.extensions.{name}"] = module

    from app import extensions as loader

    monkeypatch.setattr(loader, "available", lambda: ("alpha", "beta", "sample"))
    try:
        with TestClient(_app_with(monkeypatch, "alpha,beta,sample")) as web:
            for name in ("alpha", "beta", "sample"):
                got = web.get(f"/api/ext/{name}/ping", headers=admin.headers)
                assert got.status_code == 200, f"{name}: {got.text}"
            info = web.get("/api/server/status", headers=admin.headers).json()
            assert info["extensions"] == ["alpha", "beta", "sample"]
            # 하나만 끄면 그 하나만 사라진다.
            web.patch(
                "/api/server/extensions/beta", json={"enabled": False}, headers=admin.headers
            )
            assert web.get("/api/ext/alpha/ping", headers=admin.headers).status_code == 200
            assert web.get("/api/ext/beta/ping", headers=admin.headers).status_code == 404
    finally:
        for name in ("alpha", "beta"):
            sys.modules.pop(f"app.extensions.{name}", None)
        get_settings.cache_clear()


def test_화면의_확장_목록은_켜짐을_따른다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admin: Signed, client: TestClient
) -> None:
    """**메뉴는 `index.html` 이 들고 온다** — 그래서 그 자리를 요청마다 채운다.

    기동 때 박아 두면 화면에서 끈 확장의 메뉴가 재시작까지 남고, 사람은 끈 것이
    안 꺼진 줄로 읽는다.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head></head></html>", encoding="utf-8"
    )
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.setenv("EXTENSIONS", "sample")
    get_settings.cache_clear()
    try:
        from app.database import SessionLocal
        from app.main import create_app

        app = create_app()
        app.state.session_factory = SessionLocal
        with TestClient(app) as web:
            assert '<meta name="app-extensions" content="sample" />' in web.get("/").text
            web.patch(
                "/api/server/extensions/sample", json={"enabled": False}, headers=admin.headers
            )
            assert '<meta name="app-extensions" content="" />' in web.get("/").text
    finally:
        get_settings.cache_clear()


def test_이름은_env_에서_오고_화면에_심긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """번들 하나 · 설치마다 다른 이름. 화면은 index.html 의 meta 로 자기 이름을 안다."""
    from app.main import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>x</title></head><body></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.setenv("APP_SLUG", "plmhub")
    monkeypatch.setenv("APP_NAME", "PLM 기준정보 <허브>")
    monkeypatch.setenv("APP_TAGLINE", "한 줄")
    monkeypatch.setenv("EXTENSIONS", "sample")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as web:
            page = web.get("/").text
            assert "<title>PLM 기준정보 &lt;허브&gt;</title>" in page
            assert '<meta name="app-name" content="PLM 기준정보 &lt;허브&gt;" />' in page
            assert '<meta name="app-slug" content="plmhub" />' in page
            assert '<meta name="app-tagline" content="한 줄" />' in page
            assert '<meta name="app-extensions" content="sample" />' in page
            health = web.get("/api/health").json()
            assert health["app"] == "PLM 기준정보 <허브>" and health["slug"] == "plmhub"
            assert (
                web.get("/api/openapi.json").json()["info"]["title"]
                == "PLM 기준정보 <허브> API"
            )
    finally:
        get_settings.cache_clear()
