"""API 문서 화면(`/api/docs`)이 **바깥 주소 없이** 뜨는가 — 폐쇄망에서 흰 화면이던 것.

FastAPI 기본값은 화면 파일을 `cdn.jsdelivr.net` · `fastapi.tiangolo.com` 에서 받았다. 시험은
바깥에 닿는 기계에서 돌아서 그 흰 화면을 못 본다 — 그래서 「화면이 부르는 주소가 모두 이
서버 안이고, 그 파일이 실제로 나오는가」 를 본다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.main import create_app

PREFIX = "/rootdesign"


def _refs(html: str) -> list[str]:
    """화면이 부르는 주소 — 태그의 `src` · `href` 와 명세 주소(`url: '…'`)."""
    return re.findall(r'(?:src|href)="([^"]+)"', html) + re.findall(r"url: '([^']+)'", html)


@pytest.fixture
def prefixed(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """접두어 아래 사는 같은 앱 — 설정만 다르다(`test_prefix_install` 과 같은 틀)."""
    monkeypatch.setenv("PUBLIC_PATH", PREFIX)
    get_settings.cache_clear()
    app = create_app()
    app.state.session_factory = SessionLocal
    try:
        with TestClient(app) as one:
            yield one
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_문서_화면이_부르는_주소가_모두_이_서버_안이고_나온다(client: TestClient) -> None:
    page = client.get("/api/docs")
    assert page.status_code == 200, page.text
    html = page.text
    # 바깥 주소가 하나라도 있으면 폐쇄망에서 그 자리가 빈다 — 화면 파일이면 흰 화면이다.
    assert "http://" not in html and "https://" not in html, html
    assert sorted(set(_refs(html))) == [
        "/api/docs/assets/favicon-32x32.png",
        "/api/docs/assets/swagger-ui-bundle.js",
        "/api/docs/assets/swagger-ui.css",
        "/api/openapi.json",
    ]
    for ref in _refs(html):
        got = client.get(ref)
        assert got.status_code == 200 and got.content, ref
    script = client.get("/api/docs/assets/swagger-ui-bundle.js")
    assert "javascript" in script.headers["content-type"]
    # 명세를 `validator.swagger.io` 로 보내는 검증 배지도 끈다 — 남은 바깥 호출 한 자리.
    assert '"validatorUrl": null' in html


def test_접두어_아래_설치에서도_문서_화면이_자기_주소를_가리킨다(prefixed: TestClient) -> None:
    """앞의 nginx 가 `/<slug>` 를 떼고 넘기든 붙인 채 넘기든, 화면은 브라우저가 보는 주소
    (접두어 포함)로 파일 · 명세를 부르고, 그 주소가 나온다."""
    html = prefixed.get("/api/docs").text
    refs = _refs(html)
    assert refs and all(ref.startswith(f"{PREFIX}/api/") for ref in refs), refs
    for ref in refs:
        assert prefixed.get(ref).status_code == 200, ref  # 붙인 채
        assert prefixed.get(ref.removeprefix(PREFIX)).status_code == 200, ref  # 뗀 채


def test_ReDoc_은_두지_않는다(client: TestClient) -> None:
    """ReDoc 도 CDN 에서 받고, `/redoc` 은 `/api` 밖이라 nginx 뒤에서 닿지도 않았다."""
    paths = [str(getattr(route, "path", "")) for route in cast(FastAPI, client.app).routes]
    assert not [path for path in paths if "redoc" in path], paths
