# 정제 파이프라인 (`pipeline/`)

원천 데이터를 **AI 로 정제해 결과물 묶음으로 만들고, 사람이 검토한 뒤 플랫폼에 넣는** 로컬 도구.
만드는 일은 사용자 PC 의 Claude Code · Gemini CLI 가, 검토 · 적재는 플랫폼이 한다.

- 계획과 순서: [docs/데이터-온톨로지화-계획.md](../docs/데이터-온톨로지화-계획.md)
- AI 가 따르는 절차와 실행 폴더의 모양: [AGENTS.md](AGENTS.md) (**정본**)
- 무엇을 무엇으로 만드나: 모델링 규약 — MCP `get_guide(topic="modeling")` (본문 `mcp_server/guide/GUIDE.md`)
- 공통 코어: [core/plm-core.json](core/plm-core.json)(PLM 기준정보 — 허브가 내려준다) ·
  [core/work-core.json](core/work-core.json)(업무 공통 — 그룹의 일이 PLM 과제에 붙는 자리)
- 그룹이 시작 전에 채우는 것: [templates/파일럿-그룹-정리.md](templates/파일럿-그룹-정리.md)
- 사내 AI 에게 단계마다 붙여 넣는 지시문: [templates/사내-AI-지시문.md](templates/사내-AI-지시문.md)

## 준비

파이썬 3.12 이상. **표준 라이브러리만 쓴다** — 더 깔 것이 없다.

```bash
export SP_SERVER=http://<플랫폼 주소>:<포트>
export SP_TOKEN=<개인 토큰>     # 플랫폼 「내 정보 → 개인 토큰」, 범위 read · objects:write
                               # 정의(ontology.json)까지 넣으려면 ontology:write 도(시스템 관리자)
```

AI 가 플랫폼을 읽게 하려면 MCP 도 붙인다(플랫폼 README 의 MCP 절). AI 는 MCP 로 **읽고**, 넣는 것은
이 도구로 한다.

## 한 바퀴

```bash
python sp_pipeline.py init     runs/2026-09-13-sim-tools   # 빈 실행 폴더
# AI 가 AGENTS.md 대로 ontology.json · objects/ · relations/ 를 채운다
python sp_pipeline.py validate runs/2026-09-13-sim-tools   # 보내기 전 모양 검사
python sp_pipeline.py preview  runs/2026-09-13-sim-tools   # 아무것도 저장하지 않고 미리 보기
python sp_pipeline.py apply    runs/2026-09-13-sim-tools   # 사람이 확인한 뒤 — 전부 아니면 무
```

| 명령 | 하는 일 | 종료 코드 |
| --- | --- | --- |
| `init` | `bundle.json` · `ontology.json` · `objects/` · `relations/` · `unresolved.json` | 0 |
| `validate` | JSON 모양, 식별자 겹침, 관계 행의 칸, 미해결 목록. 출처(`_source`) · 근거가 없으면 경고 | 오류 있으면 1 |
| `preview` | 검증 → 플랫폼 `POST /api/bundles/import`(apply=false) → `preview.json` 에 결과와 **지문** | 계획에 오류 있으면 1 |
| `apply` | **미리 본 것과 지문이 같을 때만** 적용 → `applied.json` | 안 들어갔으면 1 |
| `pull` | (쌍둥이) 허브가 내보낸 사이드바 묶음을 새 실행 폴더로 — `--group plm`, `SP_HUB_SERVER` · `SP_HUB_TOKEN`. 그 뒤는 `validate` → `preview` → `apply`(받는 플랫폼에) | 0 |

도구가 막고 멈추면 종료 코드 2 와 함께 이유를 적는다(검증 실패 · 미리 본 뒤 바뀜 · 서버 거절).

## 표(CSV)는 규칙으로 — `sp_table.py`

한 행에 여러 타입이 섞인 표를 **대응 파일**대로 타입마다 나눠 실행 폴더를 만든다. 행마다 AI 가
판단하지 않는다.

```bash
python sp_table.py plm-models.table.json 프로젝트-모델.csv runs/2026-09-13-plm-models
python sp_pipeline.py validate runs/2026-09-13-plm-models    # 그 뒤는 위와 같다
```

| 하는 일 | |
| --- | --- |
| 같은 식별자는 한 객체로 | 칸 값이 행마다 다르면 **짐작하지 않고** `unresolved.json` 으로 |
| 이름에 박힌 조각을 칸으로 | 해석기 — 자리 + 사전 · 정규식. 두 갈래로 읽히면 고르지 않는다. 못 읽은 행은 넣고 조각 칸만 비운다 |
| 보고서 `table-report.txt` | 값을 **가린 패턴**(`A` · `9` · `가`)으로만 — 사내 밖에서 규칙을 의논할 수 있다 |
| 종료 코드 | 0 미해결 없음 · 1 미해결 있음 · 2 대응 파일 · 원천 오류 |

대응 파일의 모양은 [AGENTS.md](AGENTS.md) 의 「표를 규칙으로 옮기기」. **대응 파일 · 사전 · 정의는
원천과 같이 저장소 밖 작업 폴더에 둔다** — 열 이름과 코드 체계도 사내 정보다.

## Claude Desktop · Gemini CLI 에 붙이기 (로컬 MCP)

AI 가 **사용자 PC 에서** 조사 · 변환 · 검증 · 미리 보기를 직접 돌리게 한다. 도구는
`sp_mcp.py` 하나이고, 두 클라이언트가 같은 설정 모양으로 붙는다.

**1. 설치** — 릴리스의 `sp-pipeline-<버전>.zip` 을 풀고(휠 동봉 · 인터넷 없이 설치). 저장소에 없는
사내 파일(허브 정의 · 대응 파일)까지 함께 들고 가려면 `KIT_PRIVATE_DIR=<폴더> ./deploy/build_pipeline_kit.sh`
로 만든 `…-private.zip` 을 쓴다(그 zip 은 올리지 않는다):

```bash
python sp_setup.py --work-root "D:\온톨로지작업" --server http://<플랫폼>:<포트> \
                   --token spt_... --write-claude --write-gemini
```

- venv(`venv/`)를 만들고 `mcp` 를 동봉 휠로 깐다(인터넷이 되면 `--online`).
- `--write-claude` · `--write-gemini` 는 각 설정 파일에 `sp-pipeline` 항목만 넣는다. 원래 파일은
  `.bak` 로 남고, 다른 MCP 항목은 안 건드린다. 빼면 넣을 내용만 보여 준다.
- **Claude Desktop 은 완전히 종료했다가 다시 켠다.**

**2. 설정의 모양** (직접 넣을 때 — `mcpServers` 안에):

```json
"sp-pipeline": {
  "command": "C:\\...\\sp-pipeline\\venv\\Scripts\\python.exe",
  "args": ["C:\\...\\sp-pipeline\\sp_mcp.py"],
  "env": {"SP_WORK_ROOT": "D:\\온톨로지작업", "SP_SERVER": "http://...", "SP_TOKEN": "spt_..."}
}
```

| 클라이언트 | 설정 파일 |
| --- | --- |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |

| 환경 변수 | 뜻 |
| --- | --- |
| `SP_WORK_ROOT` | 작업 폴더들을 두는 곳 — **도구는 이 안만 읽고 쓴다** |
| `SP_SERVER` · `SP_TOKEN` | 플랫폼 주소 · 개인 토큰(`read` · `objects:write`, 정의까지면 `ontology:write`). 미리 보기 · 코어 대조에 |

플랫폼 MCP(`standardplatform`)도 함께 붙여 두면 AI 가 지금 정의(`ontology_schema`)를 읽는다.

**3. 쓰기** — 대화에서 「`D:\온톨로지작업` 에 해석팀 의뢰 대장 작업을 시작하자」 처럼 말하면 AI 가
`pipeline_guide` → `work_init` → (원천을 `00-원천/` 에 넣어 달라고 한다) → `source_profile` …
순서로 간다. 절차와 **멈추는 자리**는 [AGENTS.md](AGENTS.md) 「작업 폴더로 일한다」.

| 도구 | 하는 일 |
| --- | --- |
| `pipeline_guide` | 절차 · 형식의 정본(이 폴더의 AGENTS.md, 모델링 규약) |
| `work_list` · `work_init` · `work_status` | 작업 폴더 — **어디까지 했고 다음이 무엇인지** |
| `source_profile` · `source_head` | 원천 표 조사(계산) · 앞부분 보기 |
| `work_read` · `work_write` | 정의 · 판단표 · 대응 · 실행 폴더 행 파일(쓸 수 있는 자리가 정해져 있다) |
| `decision_record` | 사람이 정한 것 — 정의 확정 · 미해결의 답 |
| `table_convert` · `run_init` · `run_validate` · `run_preview` | 변환 · 빈 실행 · 검증 · 미리 보기 |
| `hub_pull` | (쌍둥이) 허브의 PLM 기준정보를 새 실행 폴더로 받는다 — env 에 `SP_HUB_SERVER` · `SP_HUB_TOKEN`(설치: `--hub-server` · `--hub-token`) |

**적용 도구는 없다.** `run_preview` 가 돌려주는 `apply_command` 를 사람이 확인한 뒤 직접 실행한다.

**결과를 밖으로 못 들고 나오는 자리** — `source_profile` 의 끝과 `table_convert` 의 `brief` 에 **「말로
전할 요약」**(수와 열 이름만, 값 없음)이 있다. 그것을 읽어 전하면 규칙을 고칠 수 있다.

MCP 없이 명령으로도 같은 일을 한다 — `sp_work.py init|status|record`, `sp_profile.py`,
`sp_table.py`, `sp_pipeline.py`.

## 그룹을 시작할 때

1. 그룹 담당자가 `templates/파일럿-그룹-정리.md` 를 복사해 **작업 폴더(저장소 밖)** 에서 채운다 —
   특히 2장 「자주 묻는 질문」.
2. 플랫폼에 코어가 없으면 코어부터 넣는다. **허브**는 `core/plm-core.json`(+ 사내 확장 정의),
   **쌍둥이**는 허브에서 PLM 기준정보를 받은 뒤 `core/work-core.json` — `preview` → 확인 → `apply`.
3. AI 가 정리 문서와 규약을 읽고 그룹 전용 정의(`<그룹코드>_`)를 제안한다 → 온톨로지 담당이 확인.
4. 원천 종류마다 실행 폴더를 만들어 한 바퀴씩 돈다 — 엑셀은 규칙(스크립트)으로, 문서는 AI 추출로.

## 실행 폴더를 어디에 두나

**그룹의 데이터는 이 저장소 밖에 둔다** — 원천 · 실행 폴더 · 정답 세트 · 동의어 사전. 사내 데이터가
git 에 섞이지 않게. 실행 폴더 하나가 한 번의 정제이고, 지난 폴더를 지우지 않으면 무엇이 언제
어떻게 들어갔는지가 남는다.
