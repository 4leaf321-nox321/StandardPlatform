"""정제 도구 키트 — **서버가 같은 판을 들고 있다가 내려준다.**

키트는 사용자 PC 에 푸는 zip(`sp-pipeline`)이다 — 원천 파일을 정제해 수만 줄을 한 번에 넣는
길. 따로(GitHub 릴리스에서) 받게 두었더니 사내망에서는 받을 길이 없었고, 운영의 사람도 AI 도
그런 것이 있는 줄 몰랐다(실측). 서버 번들이 같은 판을 이미지에 넣고(`build_bundle.sh`),
화면(내 정보)이 여기서 받는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import version
from app.config import get_settings
from tests.api.conftest import Signed


def test_없으면_없다고_말한다(
    client: TestClient, member: Signed, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """개발 · 옛 번들에는 없다 — 화면이 단추를 안 세우고, 받으려 하면 무엇을 해야 하는지
    말한다."""
    monkeypatch.setattr(get_settings(), "pipeline_kit", tmp_path / "none.zip")
    info = client.get("/api/server/pipeline-kit/info", headers=member.headers)
    assert info.status_code == 200, info.text
    assert info.json()["available"] is False and info.json()["size_bytes"] == 0

    got = client.get("/api/server/pipeline-kit", headers=member.headers)
    assert got.status_code == 404
    assert "build_pipeline_kit.sh" in got.json()["error"]["message"]


def test_있으면_이_서버의_판_이름으로_내려준다(
    client: TestClient, member: Signed, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**관리자가 아니어도 받는다** — 데이터를 정제하는 사람은 대개 관리자가 아니고, 비밀이
    없다(릴리스에 공개로 올라가는 것과 같은 파일이다). 이름에 판이 있어야 PC 에 여러 판이
    쌓여도 어느 것이 이 서버와 맞는지 안다."""
    kit = tmp_path / "pipeline-kit.zip"
    kit.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # 빈 zip
    monkeypatch.setattr(get_settings(), "pipeline_kit", kit)
    name = f"sp-pipeline-{version.current()}.zip"

    info = client.get("/api/server/pipeline-kit/info", headers=member.headers).json()
    assert info == {
        "available": True,
        "filename": name,
        "version": version.current(),
        "size_bytes": kit.stat().st_size,
    }

    got = client.get("/api/server/pipeline-kit", headers=member.headers)
    assert got.status_code == 200, got.text
    assert got.content == kit.read_bytes()
    assert got.headers["content-type"] == "application/zip"
    assert name in got.headers["content-disposition"]


def test_로그인하지_않으면_못_받는다(client: TestClient) -> None:
    assert client.get("/api/server/pipeline-kit/info").status_code == 401
    assert client.get("/api/server/pipeline-kit").status_code == 401
