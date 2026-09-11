# StandardPlatform MCP 서버

Claude(Claude Code/Desktop)에서 온톨로지를 읽고 채우게 하는 MCP 서버.
백엔드와 의존성이 충돌해 **별도 venv·프로세스**로 돌리고, REST API 로 통신한다.
에이전트가 자료를 분석하고 **타입·속성·관계를 정의하면 그대로 화면이 생기는** 것이
목적이다([ADR 0005](../docs/adr/0005-온톨로지-메타모델.md)).

## 설치·실행
```bash
cd mcp_server
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
PLATFORM_API_BASE=http://localhost:8030 ./venv/bin/python server.py
# → streamable-http, 기본 http://127.0.0.1:8032/mcp
```

개발 백엔드(`run.py`, 8031)에 붙이려면 `PLATFORM_API_BASE=http://127.0.0.1:8031`.
포트는 백엔드 포트 +2 다(플랫폼마다 10씩 벌리는 규칙 안에서 8030 운영 · 8031 개발 ·
8032 MCP). `MCP_HOST`/`MCP_PORT` 로 바꾼다.

## Claude Code 등록 (사용자별 토큰)
```bash
claude mcp add --transport http standardplatform http://<host>:8032/mcp \
  --header "Authorization: Bearer <내 토큰>"

claude mcp list        # standardplatform: http://<host>:8032/mcp (HTTP) - ✔ Connected
```
이후 Claude 에게 "이 표를 시뮬레이션 툴로 넣어줘" 라고 하면 `ontology_schema` →
`objects_import(apply=false)` → 확인 → `apply=true` 로 들어간다.

> ### 토큰 얻는 법 — **화면의 「내 정보」(`/me`) → 액세스 토큰 발급**
>
> 이름과 범위를 고르고 발급하면 토큰이 한 번 보인다(그때 복사). 범위:
> - `read` — 스키마와 객체 읽기
> - `objects:write` — 객체를 만들고 고치기
> - `ontology:write` — **정의까지 고치기**
>
> 셋을 가른 이유: 한 범위로 묶으면 「객체만 넣게」 하려던 토큰이 **타입까지 지울
> 수 있다.** 토큰은 **저장소에 안 넣는다** — `claude mcp add` 의 기본 범위(local)는
> `~/.claude.json` 에 저장되고 커밋되지 않는다. `--scope project`(`.mcp.json`)로
> 두지 않는다.
>
> 토큰이 취소되면 도구가 `[APP-AUTH-0101] 토큰이 유효하지 않습니다.` 를 돌려준다 —
> 그때 「내 정보」 에서 새로 발급해 `claude mcp add` 를 다시 하면 된다.

**인증은 서버가 아니라 백엔드가 한다.** 이 서버는 들어온 `Authorization` 헤더를
그대로 백엔드로 넘길 뿐이라, 같은 서버 하나를 여러 사람이 **각자 토큰으로** 쓴다
(만능 토큰이 없다).

## 사용 안내는 서버가 준다 — 설치할 것 없음

MCP 를 등록했으면 끝이다. 사용 안내(도구 선택 표·정의를 바꿀 때 순서·일괄 입력
형식)는 **서버가 쥐고**(`guide/GUIDE.md`) `get_guide()` 도구로 내려준다. 이 도구의
설명 자체가 "작업 시작 전에 먼저 부르라"고 되어 있고, **도구 설명은 항상 모델에게
보이므로 아무것도 안 깔아도 된다.**

> 안내를 고칠 땐 저장소의 `mcp_server/guide/GUIDE.md` 를 고친다 — 배포하면 모두에게
> 즉시 반영된다. 서버가 매 호출마다 읽으므로 재시작도 필요 없다.

### (선택) 스킬 스텁

`skill/standardplatform/` 은 짧은 **스텁**이다. 깔면 "온톨로지 관련 요청"에 Claude
Code 가 이걸 먼저 띄워 `get_guide()` 호출을 한 번 더 밀어준다. **안 깔아도 동작한다**
— 넛지가 조금 약해질 뿐이다.

```bash
mkdir -p ~/.claude/skills
cp -r skill/standardplatform ~/.claude/skills/standardplatform   # 선택. 한 번만.
```

스텁엔 안내 본문이 없으므로 **한 번 깔면 다시 복사할 일이 없다.**

## 도구 열

| 도구 | 무엇 |
| --- | --- |
| `get_guide` | 사용 안내 — **먼저 이것부터** |
| `ontology_schema` | 묶음·타입·속성·관계 전부 |
| `ontology_import` | 정의를 한 트랜잭션으로. **기본은 미리 보기**(`apply=false`) |
| `objects_list` · `object_get` | 객체 읽기 (`object_get` 은 관련 객체까지) |
| `object_create` · `object_update` | 객체 쓰기 (`update` 는 보낸 것만) |
| `objects_import` | 여러 행 한 번에(upsert). 기본은 미리 보기 |
| `relation_add` · `relations_import` | 객체 둘을 잇기 (**근거를 적는다**) |

### 왜 타입마다 도구를 안 만드나

타입 20개에 도구가 80개가 되고, **도구 목록이 길수록 모델은 엉뚱한 것을 고른다.**
도구는 열로 고정하고 `ontology_schema` 하나가 「지금 무엇이 있고 각 타입이 무엇을
받는가」 를 말한다 — **동적인 것은 도구가 아니라 스키마다.**

검증도 권한도 백엔드가 한다. 여기에 규칙을 두면 **MCP 로는 되는데 화면에서는 안
되는** 상태가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.

## 운영 배포

릴리스 번들의 `deploy.sh install`/`update` 가 **이 서버까지 자동 설치**한다 — 별도
venv 생성 + (번들에 동봉된 휠로) 오프라인 pip 설치 + `<slug>-mcp` systemd 서비스
기동까지. 자세한 것은 [운영 배포 가이드](../deploy/README_OPERATOR.md)의 「MCP 서버」.

## 시험

`mcp` 를 백엔드 venv 에 깔지 않는다(HTTP 스택이 바뀌어 그쪽 시험이 흔들린다).

```bash
cd mcp_server && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt pytest
cd .. && mcp_server/venv/bin/python -m pytest mcp_server/tests
```

진짜 앱에 붙여 보는 시험은 백엔드 쪽에 있다(`backend/tests/api/test_mcp_tools.py`) —
가짜 `mcp` 로 도구 함수만 꺼내 TestClient 앱에 흘려보낸다. 라우터·권한·검증이
통째로 돈다.

## 겪게 될 것

| 증상 | 원인 |
| --- | --- |
| `ModuleNotFoundError: No module named 'mcp'` | venv 를 안 만들었거나 다른 python 으로 띄웠다. `./venv/bin/python server.py` |
| `claude mcp list` 에서 ✘ Failed | 서버가 안 떠 있다. 이 폴더에서 `server.py` 를 먼저 띄운다 |
| 도구가 `[APP-AUTH-0100]` | 헤더가 안 갔다. `claude mcp add` 에 `--header "Authorization: Bearer ..."` |
| 도구가 `[APP-AUTH-0106] ... 범위가 없습니다` | 토큰 범위 부족. 「내 정보」 에서 범위를 넓혀 다시 발급 |
| 421 `Invalid Host header` | 비-localhost 로 열었다. `MCP_ALLOWED_HOSTS` 를 지정하거나 비워 둔다(server.py 주석) |
