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
# → streamable-http, 기본 http://127.0.0.1:8042/mcp
```

**개발에서는 이 명령을 칠 일이 없다.** `backend` 의 `python run.py` 가 이 venv 가 있으면
MCP 서버를 자식으로 함께 띄운다(개발 백엔드 8041 → MCP 8042, Ctrl+C 로 같이 내림).
위 명령은 따로 띄우고 싶을 때(`MCP_DEV=0 python run.py` 로 자동 기동을 끄고)나 운영
호스트에서 손으로 확인할 때 쓴다. 포트는 백엔드 포트 +2 다(플랫폼마다 10씩 벌리는 규칙
안에서 8040 운영 · 8041 개발 · 8042 MCP). `MCP_HOST`/`MCP_PORT` 로 바꾼다.

## Claude Code 등록 (사용자별 토큰)
```bash
claude mcp add --transport http standardplatform http://<host>:8042/mcp \
  --header "Authorization: Bearer <내 토큰>"

claude mcp list        # standardplatform: http://<host>:8042/mcp (HTTP) - ✔ Connected
```
이후 Claude 에게 "이 표를 시뮬레이션 툴로 넣어줘" 라고 하면 `ontology_schema` →
`objects_import(apply=false)` → 확인 → `apply=true` 로 들어간다.

> ### 토큰 얻는 법 — **화면의 「내 정보」(`/me`) → 액세스 토큰 발급**
>
> 이름과 범위를 고르고 발급하면 토큰이 한 번 보인다(그때 복사). 범위:
> - `read` — 스키마와 객체 읽기(SPARQL `rdf_query` 포함 — `SELECT` · `ASK` 만)
> - `objects:write` — 객체를 만들고 고치기(사진 · 파일 붙이기 포함 — 바이트는 셸의 `curl` 이 올린다)
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

## 도구 일흔넷

| 도구 | 무엇 |
| --- | --- |
| `get_guide` | 사용 안내 — **먼저 이것부터** |
| `whoami` · `search` | 이 토큰의 주인(내 부서 · 역할)과 **어느 플랫폼인가**(`platform`) · **타입을 모를 때** 이름·식별자·별칭으로 전부 찾기 |
| `platform_profile` · `platform_profile_update` | **이 플랫폼의 자기소개** — 사람이 쓴 소개(무엇을 하러 오는 곳 · 정본은 어디)와 **지금 담긴 것**(조회 때 센다 — 기록 · 축과 건수, 들어오는 곳, 정본이 바깥인 타입, 지표), 쓴 뒤 달라진 것(`stale`). 고치기는 미리 보기 뒤 저장(시스템 관리자). 같은 틀로 띄운 플랫폼 여럿이 붙으면 도구가 전부 같아서, **접속 때 그 사람의 토큰으로** 센 소개가 안내문 첫머리에 실려 어디에 물을지 가른다 |
| `ontology_schema` | 묶음·타입·속성·관계 전부 |
| `ontology_import` | 정의를 한 트랜잭션으로. **기본은 미리 보기**(`apply=false`) |
| `object_resolve` | **이름 하나가 어느 객체인가** — `exact`/`candidates`/`none`. 이름으로 가리키기 전에 부른다 |
| `objects_resolve_many` | 이름 **여럿을 한 번에**(500개까지) — 넣기 전에 「없는 것 · 여럿과 맞는 것」 을 먼저 걸러 묶음 전체가 거절되지 않게 |
| `objects_list` · `object_get` | 객체 읽기 — 화면과 같은 조건 거르기 (`object_get` 은 관련 객체까지). **0건이면 `diagnosis` 가 붙는다**. 긴 글 칸은 잘라 준다(목록 300자 · 상세 6,000자씩, `clipped` · `text_from`) |
| `objects_similar` | **비슷한 기록**(ADR 0022) — 축 태그(참조 칸)가 많이 겹치는 같은 타입의 기록, 드문 태그가 겹칠수록 위에. 겹친 태그(왜 비슷한가)와 함께. 기록 대신 태그 묶음으로도 |
| `objects_summary` · `object_fields` | 통계(서버가 센다 — 화면의 「통계」 와 같다) · 다른 타입의 칸 주소(`ref.vendor.country` 등) |
| `object_history` · `object_references` · `object_rollup` · `quality_report` | 이력 · 가리키는 것 · 아래 전부의 합 · 품질 — 화면의 읽기와 대칭 |
| `graph_neighbors` · `graph_overview` | **이것과 이어진 것들**(한 걸음 너머, 질의어 없이) · 타입 사이의 지형 — 화면의 지식 그래프와 같은 길 |
| `object_tree` · `audit_recent` | 계층 한 단계씩 · 누가 언제 무엇을 바꿨나(전체, 부서 관리자 이상) |
| `bulk_edit` · `bulk_edit_undo` | 여러 객체의 **한 칸**을 같은 값으로 — 계획 먼저, `batch_id` 로 통째로 되돌리기 |
| `object_create` · `object_update` | 객체 쓰기 (`update` 는 보낸 것만) |
| `objects_import` | 여러 행 한 번에(upsert). **작업이 된다** — 계획을 돌려주고, 적용은 `job_apply`. `aliases_mode="replace"` 면 파일에 없는 별칭을 지운다(기본은 더하기), 행의 `renamed_from`·`previous_keys` 로 **키를 바꾼다** |
| `aliases_pending` · `aliases_review` | **사람이 아직 안 본 별칭**(기계가 붙인 것) 목록 · 고른 것을 한 번에 확인/지우기. **확인은 사람의 판단이다** |
| `alias_candidates` · `alias_candidate_apply` | **못 찾은 말**(별칭 후보, ADR 0025) — 이름 풀이 · 검색 · 목록 검색이 아무것도 못 찾은 글자, 많이 · 여럿이 찾은 것부터(「이것 아닐까」 제안과 함께) · 그 객체의 별칭으로 붙이기(`attach`) · 무시(`ignore`) · 되돌리기(`restore`). **기본은 미리 보기**(`apply=false`), 제안은 짐작이라 사람이 고른다. 부서 관리자 이상 |
| `bundle_import` | 정의 · 객체 · 관계를 한 묶음으로 — 작업이 되어 한 번에 미리 보기, 적용은 `job_apply`(전부 아니면 무) |
| `bundle_runs` · `bundle_undo` | **넣은 판을 통째로 되돌린다** — 목록에서 번호를 찾고, 기본은 계획(사람 확인 뒤 `apply=True`). 되돌릴 기록은 30일 보관 |
| `job_status` · `job_apply` · `jobs_list` · `job_cancel` | 작업이 어디까지 됐나(`next` 가 종류마다 다음 할 일을 말한다) · 사람이 확인한 계획 적용 · 작업 목록(상태 · 종류 · 내 것으로 거르기)과 워커 생존 · 멈추기 |
| `relation_add` · `relation_update` · `relation_remove` · `relations_import` | 객체 둘을 잇기 (**근거를 적는다**) · 근거 고치기 · 끊기(틀리게 이은 것을 되돌리는 자리). `relations_import(mode="replace")` 는 **파일에 없는 선을 끊음으로** 계획에 올린다(`replace_type` 은 그 타입 전체에서) |
| `datasources_list` · `datasource_sync` · `datasource_runs` · `datasource_preview` | 바깥 시스템(OData · REST · 파일 · 다른 플랫폼)에서 읽어 채우기 — 계획 먼저. 지난 실행 기록(실패 이유 · 미룬 관계) · 앞 몇 행을 칸 대응한 모습 |
| `fill_priorities` | **어디부터 채우나**(ADR 0025) — 타입마다 필수 칸 · 칸별 채움률 · 선이 없는 관계 · 지워진 것을 가리키는 칸 · 별칭 없는 객체 · 객체 없는 타입을 세고, 쓰는 곳(필수 · 지표 · 코어 공개 · 뷰 · 개수 제약)으로 가중해 줄 세운다. 줄마다 「이것을 채우면 무엇이 좋아지나」(`gain`)와 빈 것만 거른 화면(`link`). 큰 타입은 표본 어림(`estimated` · `notes`). 「무엇이 나쁜가」 는 `quality_report` |
| `server_maintenance` · `notifications` · `filestore_gc` | 홈의 「남은 일」(보는 사람의 권한대로) · 내 알림 · 첨부 저장소의 고아 파일 정리(계획 → `job_apply`, 시스템 관리자) |
| `extensions_schema` · `extension_call` | **이 설치에만 있는 기능**(확장)의 자리 목록 · 그 자리 부르기. 쓰기는 그 확장의 범위를 가진 토큰만 |
| `metric_list` · `metric_query` · `metric_define` | **지표** — 미리 세어 둔 값(ADR 0013). 비율 · 추이 · 코호트 · 기준 값 목록은 목록을 받아 직접 세지 않고 여기서 읽는다. 정의는 계획 먼저(시스템 관리자) |
| `metric_runs` · `metric_recompute` · `metric_home` | 계산 기록(전부/바뀐 기간만 · 그 까닭 · 실패 이유) · 지금 전부 다시 세기(시스템 관리자) · 부서 홈에 추이 그림으로 올리기/내리기(그 부서 관리자) |
| `metric_analyze` | **분석** — 세어 둔 셀 위의 통계(ADR 0014): 수명 · B수명, 순차 검정(전작 대비), 관리도, 계절 · 변화점, 파레토 · 집중도. 셀을 받아 직접 계산하지 않는다 — 주의(`caveats`)를 그대로 전한다 |
| `metric_alerts` | **내 경보와 최근 발생**(ADR 0016) — 지표를 다시 셀 때마다 저장한 분석을 돌려 처음 본 결론. 읽기만(만들기는 화면의 「경보 저장」) |

### 왜 타입마다 도구를 안 만드나

타입 20개에 도구가 80개가 되고, **도구 목록이 길수록 모델은 엉뚱한 것을 고른다.**
도구는 일흔넷으로 고정하고 `ontology_schema` 하나가 「지금 무엇이 있고 각 타입이 무엇을
받는가」 를 말한다 — **동적인 것은 도구가 아니라 스키마다.**

검증도 권한도 백엔드가 한다. 여기에 규칙을 두면 **MCP 로는 되는데 화면에서는 안
되는** 상태가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.

## 헤매지 않게 두는 장치

도구를 늘리는 것만으로는 AI 가 잘 돌지 않는다. **틀린 답을 그럴듯하게 내는 자리**를
막는 장치가 따로 있다.

| 겹 | 무엇 | 어디 |
| --- | --- | --- |
| 상시 문맥 | 「하려는 일 → 부를 것」 표와 지켜야 할 셋 | `server.py` 의 `instructions` |
| 주문형 가이드 | 주제별 안내 — **서버가 최신본을 쥔다** | `get_guide` · `guide/GUIDE.md` |
| 해소 강제 | 이름이 하나로 안 정해지면 **판정을 준다** | `object_resolve` |
| 모름·빈 결과 구분 | 0건이 「없어서」 인지 「안 보여서」 인지 「조건이 좁아서」 인지 | `objects_list` 의 `diagnosis` |
| 쓰기 방어 | 이름이 여럿에 맞으면 **거절한다** | 백엔드(`bulk.py` 의 참조 풀이) |
| 못 찾은 말 | 아무것도 못 찾은 이름을 모아 사람이 별칭으로 붙이면 **다음부터 찾힌다** | `alias_candidates` · `alias_candidate_apply` |
| 측정 | 실제로 덜 헤매게 됐는지 | `eval/score.py` |

## 측정 — `eval/`

고친 것이 도움이 됐는지는 **수로만 안다.** 자취를 켜고(기본은 꺼짐) 시나리오를 시킨 뒤
점수를 본다:

```bash
MCP_TRACE_FILE=~/mcp-trace.jsonl ./venv/bin/python server.py
# 시나리오는 eval/cases.md — 고치기 전과 후에 같은 것을 시킨다
./venv/bin/python eval/score.py ~/mcp-trace.jsonl --baseline 지난주.jsonl
```

자취에 남는 것은 **도구 이름 · 시간 · 판정 · 수**뿐이다. 객체 이름도 속성 값도 안
남긴다 — 남기면 그 파일 자체가 유출 경로가 된다.

**CI 에 넣지 않는다.** AI 의 답은 같은 물음에도 흔들리고, 흔들리는 수로 빌드를 막으면
사람은 그 시험을 끄는 법부터 배운다.

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
