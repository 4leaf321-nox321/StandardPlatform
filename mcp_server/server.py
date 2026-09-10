"""MCP 어댑터 — **알맹이는 `tools.py` 에 있다.**

여기는 `mcp` 패키지에 도구를 등록하고 stdio 로 말하는 일만 한다. 규칙을 여기
두지 않는 이유: 이 파일은 `mcp` 가 깔린 데서만 돌고, 그러면 **붙여 보기 전에는
아무것도 확인할 수 없다.** `tools.py` 는 httpx 만 쓰므로 시험이 그것을 본다.

## 띄우기

    export PLATFORM_URL=http://<서버>:8030/api
    export PLATFORM_TOKEN=<개인 액세스 토큰>
    python -m mcp_server.server

Claude Desktop·Claude Code 설정 예시는 `README.md` 에 있다.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server import tools
from mcp_server.tools import Platform, PlatformError

mcp = FastMCP("standardplatform-ontology")

#: **한 번만 만든다.** 도구마다 새로 만들면 토큰 검사와 연결이 매번 다시 붙는다.
_platform: Platform | None = None


def platform() -> Platform:
    global _platform
    if _platform is None:
        _platform = Platform()
    return _platform


def _register(name: str, function: Any) -> None:
    """`tools.py` 의 함수를 그대로 도구로 세운다.

    **설명은 그 함수의 docstring 이다.** 여기 다시 적으면 두 벌이 되고, 갈린 두
    벌 중 모델이 읽는 것은 이쪽이라 **실제 동작과 다른 설명을 읽게 된다.**
    """
    signature = inspect.signature(function)
    params = [one for one in signature.parameters.values() if one.name != "platform"]

    async def run(**kwargs: Any) -> str:
        try:
            got = function(platform(), **kwargs)
        except PlatformError as caught:
            # **서버의 말을 그대로 전한다.** 오류 문구에 무엇을 고쳐야 하는지가
            # 적혀 있고, 여기서 고쳐 쓰면 그것을 잃는다.
            return json.dumps({"error": str(caught)}, ensure_ascii=False)
        return json.dumps(got, ensure_ascii=False, indent=2, default=str)

    run.__name__ = name
    run.__doc__ = function.__doc__
    run.__signature__ = signature.replace(parameters=params)  # type: ignore[attr-defined]
    mcp.tool()(run)


for _name, _function in tools.TOOLS.items():
    _register(_name, _function)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
