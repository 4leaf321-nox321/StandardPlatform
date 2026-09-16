"""확장은 **켠 인스턴스에만 있다** — 번들 하나로 여러 플랫폼을 띄우는 장치."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.api.conftest import Signed


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


def test_없는_확장은_기동에서_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` 의 오타가 「메뉴가 안 보이는」 조용한 고장으로 남지 않게."""
    try:
        with pytest.raises(RuntimeError, match="확장 'nope' 이 없습니다"):
            _app_with(monkeypatch, "nope")
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
