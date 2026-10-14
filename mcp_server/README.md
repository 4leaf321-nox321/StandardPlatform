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
> 토큰이 폐기되거나 만료되면 도구가 `GET /api/… → HTTP 401: [APP-AUTH-0101] 토큰이 유효하지
> 않습니다.` 로 **실패한다**(결과가 아니라 오류 — 아래 「HWAX 게이트웨이를 거칠 때」) — 그때
> 「내 정보」 에서 새로 발급해 `claude mcp add` 를 다시 하면 된다.

**인증은 서버가 아니라 백엔드가 한다.** 이 서버는 들어온 `Authorization` 헤더를
그대로 백엔드로 넘길 뿐이라, 같은 서버 하나를 여러 사람이 **각자 토큰으로** 쓴다
(만능 토큰이 없다).

**위험 도구는 시스템 관리자만.** 권한은 백엔드가 보되, `DESTRUCTIVE_TOOLS`(지우기 · 합치기 ·
되돌리기 · 덮어쓰기 · 멈추기 · 계획 적용 `job_apply` 등 20개)는 MCP 가 한 겹 더 좁힌다 — 부를 때마다
`GET /api/auth/me` 로 시스템 관리자인지 묻고, 아니면 백엔드에 아무것도 안 보내고
`{"error": …, "admin_only": true}` 를 돌려준다(미리 보기도). 화면에서는 부서 관리자도 자기 부서 것을
지우지만, AI 가 기계 속도로 부르는 것은 무게가 달라서다(ADR 0030). `extension_call` 은 읽기(GET)만
그대로 열린다.

## HWAX 게이트웨이를 거칠 때 (ADR 0028)

HWAX 포털에 붙은 설치에서는 사람들의 Claude 가 **포털의 게이트웨이**
(`https://hwax.sec.samsung.net/mcp-gw/mcp`, 포털 개인 토큰)에 붙고, 게이트웨이가 이 서버를
`http://<서버>:<MCP 포트>/mcp` 로 부른다 — Report Archive 와 같은 길이다. 포털 nginx 에 `/<id>/mcp`
경로는 없다. 사람이 이 설치의 토큰을 발급해 붙여 넣을 일도 없다:

- **사람별 위임** — 게이트웨이가 그 사람마다 백엔드의 `POST /api/auth/sso`(공유 비밀
  `HEAX_SSO_SECRET`)에서 **그 사람 명의의 액세스 토큰**(이름 「HWAX 게이트웨이(gateway)」, 하루)을
  받아 `Authorization: Bearer` 로 싣는다. 이 서버는 지금처럼 그것을 백엔드에 넘길 뿐이다 — 권한 ·
  부서 · 감사가 그 사람이고, 감사의 통로는 `hwax-gateway` 다.
- **401 은 오류로 올린다** — 백엔드가 토큰을 거절하면 도구가 `<METHOD> <경로> → HTTP 401: <서버의
  말>` 예외로 실패하고, FastMCP 가 그것을 `Error executing tool <이름>: …` 로 감싼다. 게이트웨이는
  **그 모양일 때만** 위임 토큰을 새로 받아 한 번 다시 부른다(`_TOKEN_REJECTED`). 401 이 아닌 오류는
  지금처럼 `{"error": "[코드] 서버의 말"}` 결과다(다시 부르면 같은 일이 두 번 돈다).
- **도구 이름 앞에 `<id>_`** — 같은 틀의 인스턴스는 도구 이름이 전부 같아서, 게이트웨이는 겹친
  이름에 백엔드 키(= slug)를 붙인다(`jajuverify_objects_list`). 인스턴스가 둘 이상이면 92개 전부,
  하나뿐이어도 다른 앱과 겹치는 `search` 같은 것은 붙는다.
- **바인딩 · Host 검사** — 게이트웨이가 닿게 `MCP_HOST=0.0.0.0`. `MCP_ALLOWED_HOSTS` 를 쓰면 게이트웨이가
  부르는 주소를 **포트까지** 넣는다(`<서버 주소>:<MCP 포트>` — 이중화면 B 도). 없으면 421 `Invalid Host
  header`. 비워 두면 Host 를 보지 않는다(사내망 — 인증은 백엔드가 한다).
- **소속 머리는 버린다** — 게이트웨이가 함께 싣는 `X-Heax-User-Affiliation` · `X-Heax-Aff-Proof` 는
  백엔드로 넘기지 않는다. 이 플랫폼은 보이는 것을 부서와 권한으로 정한다.
- **위험 도구** — `server.py` 의 `DESTRUCTIVE_TOOLS`(지우기 · 되돌리기 · 덮어쓰기 · 멈추기 20개)를
  게이트웨이의 범용 실행기 차단(`_INVOKE_DENY_EXACT`)에 넣어 달라고 요청한다 — 게이트웨이의 이름 규칙
  (`delete_` 로 **시작**)에는 우리 이름이 안 걸린다. 목록을 고치면 요청서 틀(`docs/[참고]
  HWAX포탈_연동_요청서.md`)도 같이 — 시험이 둘을 견준다. 이 20개는 **MCP 로는 시스템 관리자만** 쓴다
  (위 「위험 도구는 시스템 관리자만」) — 게이트웨이 차단과 별개로, 직접 바인딩이어도.

포털 쪽이 할 일은 인스턴스마다 `sudo APP_SLUG=<slug> ./deploy.sh hwax-request` 가 쓰는 요청서 한 장이다
(운영 안내서 9절).

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

## 도구 아흔둘

| 도구 | 무엇 |
| --- | --- |
| `get_guide` | 사용 안내 — **먼저 이것부터** |
| `whoami` · `search` | 이 토큰의 주인(내 부서 · 역할)과 **어느 플랫폼인가**(`platform`) · **타입을 모를 때** 이름·식별자·별칭으로 전부 찾기 |
| `platform_profile` · `platform_profile_update` | **이 플랫폼의 자기소개** — 사람이 쓴 소개(무엇을 하러 오는 곳 · 정본은 어디)와 **지금 담긴 것**(조회 때 센다 — 기록 · 축과 건수, 들어오는 곳, 정본이 바깥인 타입, 지표), 쓴 뒤 달라진 것(`stale`). 고치기는 미리 보기 뒤 저장(시스템 관리자). 같은 틀로 띄운 플랫폼 여럿이 붙으면 도구가 전부 같아서, **접속 때 그 사람의 토큰으로** 센 소개가 안내문 첫머리에 실려 어디에 물을지 가른다 |
| `ontology_schema` | 묶음·타입·속성·관계 전부 |
| `ontology_import` | 정의를 한 트랜잭션으로. **기본은 미리 보기**(`apply=false`) |
| `object_resolve` | **이름 하나가 어느 객체인가** — `exact`/`candidates`/`none`. 이름으로 가리키기 전에 부른다 |
| `objects_resolve_many` | 이름 **여럿을 한 번에**(500개까지) — 넣기 전에 「없는 것 · 여럿과 맞는 것」 을 먼저 걸러 묶음 전체가 거절되지 않게 |
| `objects_list` · `object_get` | 객체 읽기 — 화면과 같은 조건 거르기, 조건 묶음을 빼는 **제외**(`exclude`, 주소의 `nf<번호>.`) 포함 (`object_get` 은 관련 객체까지). **0건이면 `diagnosis` 가 붙는다**. 긴 글 칸은 잘라 준다(목록 300자 · 상세 6,000자씩, `clipped` · `text_from`) |
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
| `review_list` · `review_decide` | **검토함**(ADR 0027) — 사람이 판단할 일(별칭 후보 · 검수 대기 별칭 · 병합 후보 · 지워진 것을 가리키는 칸, 처리 길이 없는 적용 대기 계획 · 선 기다림은 링크만)을 원래 자리에서 읽어 한 줄(물음 · 근거 · 후보 · 추천 · 확신도)로 · 고른 것(`decisions`)이나 **추천대로 모두**(`recommended=True`, 확신도 문턱 기본 0.9)를 정하기. **늘 미리 보기 먼저**(`apply=false` — 막히는 것 · 복원할 수 없는 것을 따로 센다), 사람이 확인한 뒤 미리 보기의 `fingerprint` 로 `apply=true`. 처리는 원래 기능 · 감사 기록 그대로, 결정은 기록(추천을 따랐나)에 남는다. 큰 묶음은 작업. 부서 관리자 이상 |
| `bundle_import` | 정의 · 객체 · 관계를 한 묶음으로 — 작업이 되어 한 번에 미리 보기, 적용은 `job_apply`(전부 아니면 무) |
| `bundle_runs` · `bundle_undo` | **넣은 판을 통째로 되돌린다** — 목록에서 번호를 찾고, 기본은 계획(사람 확인 뒤 `apply=True`). 되돌릴 기록은 30일 보관 |
| `job_status` · `job_apply` · `jobs_list` · `job_cancel` · `jobs_purge` | 작업이 어디까지 됐나(`next` 가 종류마다 다음 할 일을 말한다) · 사람이 확인한 계획 적용(`job_ids` 로 여럿) · 작업 목록(상태 · 종류 · 내 것으로 거르기)과 워커 생존 · 멈추기(여럿도) · 기한 지난 작업 파일 · 끝난 기록 정리(시스템 관리자) |
| `relation_add` · `relation_update` · `relation_remove` · `relations_import` | 객체 둘을 잇기 (**근거를 적는다**) · 근거 고치기 · 끊기(틀리게 이은 것을 되돌리는 자리). `relations_import(mode="replace")` 는 **파일에 없는 선을 끊음으로** 계획에 올린다(`replace_type` 은 그 타입 전체에서) |
| `datasources_list` · `datasource_save` · `datasource_sync` · `datasource_runs` · `datasource_preview` | 바깥 시스템(OData · REST · 파일 · 다른 플랫폼)에서 읽어 채우기 — 계획 먼저. 소스 하나 · 다른 플랫폼 칸 대응 초안 · RA 게시판(`datasources_list(slug, suggest=, boards=)`) · 만들기 · 고치기 · RA 보고서 기록 타입(`datasource_save` — **인증 비밀은 받지 않는다**, 화면에서) · 지난 실행 기록(실패 이유 · 미룬 관계) · 앞 몇 행을 칸 대응한 모습 |
| `fill_priorities` | **어디부터 채우나**(ADR 0025) — 타입마다 필수 칸 · 칸별 채움률 · 선이 없는 관계 · 지워진 것을 가리키는 칸 · 별칭 없는 객체 · 객체 없는 타입을 세고, 쓰는 곳(필수 · 지표 · 코어 공개 · 뷰 · 개수 제약)으로 가중해 줄 세운다. 줄마다 「이것을 채우면 무엇이 좋아지나」(`gain`)와 빈 것만 거른 화면(`link`). 큰 타입은 표본 어림(`estimated` · `notes`). 「무엇이 나쁜가」 는 `quality_report` |
| `server_maintenance` · `notifications` · `filestore_gc` | 홈의 「남은 일」(보는 사람의 권한대로) · 내 알림 · 첨부 저장소의 고아 파일 정리(계획 → `job_apply`, 시스템 관리자) |
| `extensions_schema` · `extension_call` | **이 설치에만 있는 기능**(확장)의 자리 목록 · 그 자리 부르기. 쓰기는 그 확장의 범위를 가진 토큰만 |
| `metric_list` · `metric_query` · `metric_define` | **지표** — 미리 세어 둔 값(ADR 0013). 비율 · 추이 · 코호트 · 기준 값 목록 · **구성비**(`composition`)는 목록을 받아 직접 세지 않고 여기서 읽는다. 정의는 계획 먼저(시스템 관리자). 기준은 주소거나 **규칙**(ADR 0015 — 첫 일치로 성격을 붙인다, 계획이 규칙마다의 수 · 막힌 규칙 · 기타의 몫을 말한다) |
| `metric_runs` · `metric_recompute` · `metric_home` | 계산 기록(전부/바뀐 기간만 · 그 까닭 · 실패 이유 · 규칙 판) · 지금 전부 다시 세기(시스템 관리자) · 부서 홈에 추이 그림으로 올리기/내리기(그 부서 관리자) |
| `metric_analyze` | **분석** — 세어 둔 셀 위의 통계(ADR 0014): 수명 · B수명, 순차 검정(전작 대비), 관리도, 계절 · 변화점, 파레토 · 집중도. 셀을 받아 직접 계산하지 않는다 — 주의(`caveats`)를 그대로 전한다 |
| `metric_alerts` · `metric_alert_save` | **내 경보와 최근 발생**(ADR 0016) — 지표를 다시 셀 때마다 저장한 분석을 돌려 처음 본 결론. 경보 하나의 발생 · 지금 확인(`check=True`) · 걸기 · 이름 · 끄기(미리 보기가 지금 분석을 한 번 돌려 보인다 — 인자는 못 고친다) |
| `core_status` | **외부 공개(코어) 현황** — 공개 타입 · 읽을 수 있는 토큰 · 최근 수신, 그리고 **안 나가는 관계 종류 · 참조 속성**(사유 · 공개하면 나가는 타입). 시스템 관리자 |
| `workspaces` · `notices` · `webhooks` | **읽기만** — 부서 목록(트리) · 부서가 가진 것 · 가리키는 것, 공지, 웹훅 목록 · 보낸 기록. 그 쓰기(부서 만들기 · 통폐합 · 공지 올리기 · 웹훅 다시 보내기)는 토큰에 안 열린다 — 「계정 · 부서 · 서버 관리는 어느 범위로도 안 열린다」(사람이 화면에서) |
| `views` · `view_save` | **저장된 뷰** — 읽기 · 저장 · 고치기(부서와 함께 쓰는 뷰는 그 부서 관리자). 거르기는 `objects_list` 와 같은 모양 |
| `object_watch` · `object_years` | 객체 **지켜보기**(바뀌면 내 알림) · 내가 지켜보는 것 · 해마다 쓰는 축의 **연도 배정**(통째로 — 미리 보기 먼저) |
| `review_history` · `review_undo` | **검토함의 지난 결정** · 추천 정확도 · 최근 묶음 · 묶음 되돌리기(미리 보기 먼저) |
| `server_status` | **서버 상태**(판 · 스키마 · 디스크 · 백업 · 확장 · 정제 도구 키트) · 느린 경로 · 최악 사례 · 작업 소요(`performance`). 읽기만 — 확장 켜고 끄기는 화면에서. 시스템 관리자 |
| `export_start` | **파일로 내보내기**(객체 — 목록과 같은 거르기 · 관계) — 작업만 시작하고 파일은 사람이 화면의 「작업」 에서 받는다 |
| `config_delete` | **설정 지우기** — 지표 · 경보 · 데이터 소스 · 뷰. 미리 보기 먼저, 되살리는 길이 없다 |

### 왜 타입마다 도구를 안 만드나

타입 20개에 도구가 80개가 되고, **도구 목록이 길수록 모델은 엉뚱한 것을 고른다.**
도구는 아흔둘로 고정하고 `ontology_schema` 하나가 「지금 무엇이 있고 각 타입이 무엇을
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
| 위험 도구 | 지우기 · 되돌리기 · 계획 적용은 **시스템 관리자만**(미리 보기도) | `server.py` 의 `_admin_only` |
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
| 도구가 `… → HTTP 401: [APP-AUTH-0100]` 로 실패 | 헤더가 안 갔다. `claude mcp add` 에 `--header "Authorization: Bearer ..."` |
| 도구가 `… → HTTP 401: [APP-AUTH-0101]` 로 실패 | 토큰이 폐기 · 만료됐다. 「내 정보」 에서 새로 발급(게이트웨이로 붙었으면 게이트웨이가 스스로 다시 받는다 — 그래도 실패하면 그 사람이 승인된 계정이 없거나 정지) |
| 도구가 `[APP-AUTH-0106] ... 범위가 없습니다` | 토큰 범위 부족. 「내 정보」 에서 범위를 넓혀 다시 발급 |
| 421 `Invalid Host header` | 비-localhost 로 열었다. `MCP_ALLOWED_HOSTS` 를 지정하거나 비워 둔다(server.py 주석) — HWAX 게이트웨이가 부르는 주소는 포트까지 |
