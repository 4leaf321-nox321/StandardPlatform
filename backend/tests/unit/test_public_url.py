"""화면의 바탕 주소는 **설정이 아는 것을 차례로** 본다.

바깥 AI 의 답에 붙는 「같은 조건의 화면」 주소의 앞부분이다. 틀리면 열리지 않는 링크가 붙고,
사람은 그 링크가 왜 안 열리는지 AI 에게 묻는다 — 그래서 차례와 「만들지 않는 자리」 를 지킨다.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.shared.public_url import mcp_app_origin, screen_base


def _settings(**env: str) -> Settings:
    """`.env` 를 안 읽는 Settings — 주어진 값만으로. 주소에 닿는 값은 비워서 시작한다."""
    values: dict[str, Any] = {
        "_env_file": None,
        "DATABASE_URL": "",
        "PORT": "8040",
        "PUBLIC_PATH": "",
        "PUBLIC_URL": "",
        "PORTAL_LAUNCH_URL": "",
        "MCP_PUBLIC_URL": "",
        **env,
    }
    return Settings(**values)


def test_적은_공개_주소가_먼저다() -> None:
    """사람이 적은 정본이면 다른 것을 안 본다 — 끝의 `/` 만 뗀다."""
    got = screen_base(
        _settings(
            PUBLIC_URL="https://portal.example.com/sp/",
            PUBLIC_PATH="/sp",
            PORTAL_LAUNCH_URL="https://elsewhere.example.com/launch/sp",
        )
    )
    assert got == "https://portal.example.com/sp"


def test_포털_뒤면_포털의_호스트에_접두어() -> None:
    """포털은 `/<id>/` 를 벗겨 넘긴다 — 사람이 여는 화면은 포털 호스트의 그 접두어 아래다."""
    got = screen_base(
        _settings(
            PORTAL_LAUNCH_URL="https://portal.example.com/launch/sp",
            PUBLIC_PATH="sp/",
            MCP_PUBLIC_URL="https://other.example.com:8443/sp/mcp",
        )
    )
    assert got == "https://portal.example.com/sp"


def test_MCP_공개_주소가_앱_주소면_그_앞에_접두어() -> None:
    """표준이 아닌 포트의 프록시 뒤 — 업로드 주소와 같은 규칙이다(접두어는 한 번만)."""
    settings = _settings(
        MCP_PUBLIC_URL="https://h.example.com:8443/sp/mcp/", PUBLIC_PATH="/sp"
    )
    assert mcp_app_origin(settings) == "https://h.example.com:8443"
    assert screen_base(settings) == "https://h.example.com:8443/sp"
    # 접두어 없이 적어도 화면은 접두어 아래다.
    bare = _settings(MCP_PUBLIC_URL="https://h.example.com:8443/mcp", PUBLIC_PATH="/sp")
    assert screen_base(bare) == "https://h.example.com:8443/sp"


def test_MCP_포트를_바로_적은_값으로는_만들지_않는다() -> None:
    """`http://<IP>:<앱+2>/mcp`(ReportArchive 꼴)에서 `/mcp` 를 떼면 MCP 포트다 — 화면이
    아니다. `/mcp` 로 안 끝나는 값도 앱 주소인지 모른다."""
    direct = _settings(MCP_PUBLIC_URL="http://10.0.0.5:8042/mcp", PUBLIC_PATH="/sp")
    assert mcp_app_origin(direct) is None
    assert screen_base(direct) is None
    assert screen_base(_settings(MCP_PUBLIC_URL="https://h.example.com/sp")) is None


def test_아무것도_없으면_모른다() -> None:
    """None 이면 MCP 가 들어온 주소에 접두어를 붙여 짐작한다 — 여기서 지어내지 않는다."""
    assert screen_base(_settings(PUBLIC_PATH="/sp")) is None
    assert screen_base(_settings(PORTAL_LAUNCH_URL="/launch/sp")) is None


def test_읽을_수_없는_주소는_모르는_것으로_둔다() -> None:
    """2026-11-07 검토 — 포트 오타 하나로 생존 확인(`/api/health`)이 500 을 냈다. 깨진 값은
    모르는 것(None)으로 두고 다음 길로 간다."""
    assert mcp_app_origin(_settings(MCP_PUBLIC_URL="http://sp.example.com:80443/mcp")) is None
    assert screen_base(_settings(MCP_PUBLIC_URL="http://sp.example.com:80443/mcp")) is None
    broken_portal = _settings(
        PORTAL_LAUNCH_URL="http://[portal/launch/x",
        MCP_PUBLIC_URL="https://sp.example.com/mcp",
    )
    assert screen_base(broken_portal) == "https://sp.example.com"
