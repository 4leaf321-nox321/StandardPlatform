# MCP 서버 — 기계가 온톨로지를 채우는 길

이 저장소의 온톨로지를 **읽고 쓰는 도구**를 MCP 로 노출한다. 에이전트가 자료를
분석하고 **타입·속성·관계를 정의하면 그대로 화면이 생기는** 것이 목적이다
([ADR 0005](../docs/adr/0005-온톨로지-메타모델.md)).

## 도구 일곱

| 도구 | 무엇 |
| --- | --- |
| `ontology_schema` | **먼저 이것부터.** 묶음·타입·속성·관계 전부 |
| `ontology_import` | 정의를 한 트랜잭션으로. **기본은 미리 보기**(`apply=false`) |
| `objects_list` · `object_get` | 객체 읽기 (`object_get` 은 관련 객체까지) |
| `object_create` · `object_update` | 객체 쓰기 (`update` 는 보낸 것만) |
| `relation_add` | 객체 둘을 잇기 (**근거를 적는다**) |

### 왜 타입마다 도구를 안 만드나

타입 20개에 도구가 80개가 되고, **도구 목록이 길수록 모델은 엉뚱한 것을 고른다.**
도구는 일곱으로 고정하고 `ontology_schema` 하나가 「지금 무엇이 있고 각 타입이
무엇을 받는가」 를 말한다 — **동적인 것은 도구가 아니라 스키마다.**

## 준비

1. 화면 → **내 정보** → 개인 액세스 토큰 발급. 범위:
   - `read` — 스키마와 객체 읽기
   - `objects:write` — 객체를 만들고 고치기
   - `ontology:write` — **정의까지 고치기**
2. 셋을 가른 이유: 한 범위로 묶으면 「객체만 넣게」 하려던 토큰이 **타입까지 지울
   수 있다.**

**환경을 갈라 만든다.** `mcp` 는 `httpx2` 를 끌어오고, 그것이 백엔드 개발 환경에
깔리면 `starlette.testclient` 가 HTTP 스택을 바꿔 **그쪽 시험의 타입이 흔들린다**
(실측으로 겪었다).

```bash
python -m venv mcp_server/.venv
mcp_server/.venv/bin/pip install -r mcp_server/requirements.txt
```

토큰은 **저장소에 안 넣는다.** 홈 아래에 두고 권한을 좁힌다:

```bash
cat > ~/.standardplatform-mcp.env <<'ENV'
PLATFORM_URL=http://<서버>:8030/api
PLATFORM_TOKEN=<발급받은 토큰>
ENV
chmod 600 ~/.standardplatform-mcp.env
```

## Claude Code 에 붙이기

```bash
set -a; . ~/.standardplatform-mcp.env; set +a
claude mcp add standardplatform --scope local \
  --env PLATFORM_URL="$PLATFORM_URL" \
  --env PLATFORM_TOKEN="$PLATFORM_TOKEN" \
  -- "$PWD/mcp_server/.venv/bin/python" -m mcp_server.server

claude mcp list        # standardplatform: ... - ✔ Connected
```

`--scope local` 은 **이 저장소에서만** 붙는다는 뜻이다. 토큰이 들어가므로
`--scope project`(`.mcp.json`, 커밋됨) 로 두지 않는다.

Claude Desktop 은 설정 파일에 같은 값을 적는다:

```jsonc
{
  "mcpServers": {
    "standardplatform": {
      "command": "/path/to/StandardPlatform/mcp_server/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/StandardPlatform",
      "env": {
        "PLATFORM_URL": "http://<서버>:8030/api",
        "PLATFORM_TOKEN": "<토큰>"
      }
    }
  }
}
```

## 쓰는 차례

1. `ontology_schema` — 지금 무엇이 있나
2. `ontology_import` 를 **`apply=false` 로** — 무엇이 바뀌고 **무엇을 잃는지**
3. 사람이 계획을 읽고 판단
4. `ontology_import` 를 `apply=true` 로 — 한 트랜잭션. 되돌릴 스냅샷이 남는다
5. `object_create` · `relation_add` 로 채우기

**2번을 건너뛰지 않는다.** 에이전트의 실수는 기계 속도로 반영되고, 온톨로지는
데이터의 모양이라 그 아래 쌓인 것이 전부 흔들린다. 되돌리기는 화면의
**관리 → 온톨로지 → 가져오기·이력**에 있다.

## 알맹이와 어댑터를 가른 이유

`tools.py` 는 `httpx` 만 쓰고 `server.py` 만 `mcp` 를 쓴다. 그래서 **붙여 보기
전에 도는지 확인할 수 있다** — 어댑터에 규칙을 넣으면 그 확인이 불가능해진다.
검증도 권한도 서버가 한다: 두 벌이면 **MCP 로는 되는데 화면에서는 안 되는**
상태가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.
