# 정제 파이프라인 (`pipeline/`)

원천 데이터를 **AI 로 정제해 결과물 묶음으로 만들고, 사람이 검토한 뒤 플랫폼에 넣는** 로컬 도구.
만드는 일은 사용자 PC 의 Claude Code · Gemini CLI 가, 검토 · 적재는 플랫폼이 한다.

- 계획과 순서: [docs/데이터-온톨로지화-계획.md](../docs/데이터-온톨로지화-계획.md)
- AI 가 따르는 절차와 실행 폴더의 모양: [AGENTS.md](AGENTS.md) (**정본**)
- 무엇을 무엇으로 만드나: 모델링 규약 — MCP `get_guide(topic="modeling")` (본문 `mcp_server/guide/GUIDE.md`)
- 공통 코어: [core/plm-core.json](core/plm-core.json)(PLM 기준정보 — 허브가 내려준다) ·
  [core/work-core.json](core/work-core.json)(업무 공통 — 그룹의 일이 PLM 과제에 붙는 자리)
- 그룹 전용 정의: [groups/](groups/) — 한 그룹의 일을 담는 타입(`<그룹코드>_`). 코어와 다른
  자리다(코어는 어느 설치에나, 이쪽은 그 그룹을 켠 설치만). 넣는 법과 원천 표를 옮기는 길은
  [groups/README.md](groups/README.md)
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
python sp_pipeline.py runs                                 # 넣은 판들 — 되돌릴 번호
python sp_pipeline.py undo <판번호>                          # 되돌리기 계획(--apply 로 되돌림)
```

| 명령 | 하는 일 | 종료 코드 |
| --- | --- | --- |
| `init` | `bundle.json` · `ontology.json` · `objects/` · `relations/` · `unresolved.json` | 0 |
| `validate` | JSON 모양, 식별자 겹침, 관계 행의 칸, 미해결 목록. 출처(`_source`) · 근거가 없으면 경고. **`SP_SERVER` · `SP_TOKEN` 이 있으면 끝점도 플랫폼에 묻는다**(없는 것 · 이름이 여럿과 맞는 것 · 별칭으로 풀린 것) | 오류 있으면 1 |
| `preview` | 검증 → 플랫폼 `POST /api/bundles/import`(apply=false, 202 작업) → 끝나기를 기다려 `preview.json` 에 결과와 **지문** | 계획에 오류 있으면 1 |
| `apply` | **미리 본 것과 지문이 같을 때만** 적용 → `applied.json` | 안 들어갔으면 1 |
| `init --backfill` | 대량 적재용 실행 폴더 — 미리 보기는 **계획만**(적용을 두 번 돌지 않는다) · 웹훅과 감사는 묶음 한 건 · 못 찾은 참조는 그 칸만 비움. `preview`/`apply` 에 `--backfill` 을 줘도 그때 켜진다 | 0 |
| `runs` | 넣은 판들(적용한 것만) — 최근 것부터. 되돌릴 번호를 여기서 찾는다. 「되돌릴 수 없음」 은 이미 되돌렸거나 기록 보관 기간(30일)이 지난 것 | 0 |
| `undo <판번호>` | 그 판을 **통째로 되돌린다** — 기본은 계획이다. 사람이 읽은 뒤 `--apply`. 그 사이 남이 고친 줄 · 밖에서 가리키는 것이 생긴 객체 · 합치기는 되돌리지 않고 이유를 적는다 | 오류 있으면 1 |
| `pull` | (쌍둥이) 허브가 내보낸 사이드바 묶음을 새 실행 폴더로 — `--group plm`(**여러 번 적어도 된다**), `SP_HUB_SERVER` · `SP_HUB_TOKEN`. 별칭·관계의 「맞춤」 과 **사라진 것**(`tombstones.json`)을 그대로 나른다. 그 뒤는 `validate` → `preview` → `apply`(받는 플랫폼에) | 0 |

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
| **선(관계)도 만든다** | `relations` 절 — 세 끝이 같으면 한 줄로 모으고, 붙는 값이 갈리면 미해결로 |
| 이름에 박힌 조각을 칸으로 | 해석기 — 자리 + 사전 · 정규식. 두 갈래로 읽히면 고르지 않는다. 못 읽은 행은 넣고 조각 칸만 비운다 |
| 보고서 `table-report.txt` | 값을 **가린 패턴**(`A` · `9` · `가`)으로만 — 사내 밖에서 규칙을 의논할 수 있다 |
| 종료 코드 | 0 미해결 없음 · 1 미해결 있음 · 2 대응 파일 · 원천 오류 |

대응 파일의 모양은 [AGENTS.md](AGENTS.md) 의 「표를 규칙으로 옮기기」. **대응 파일 · 사전 · 정의는
원천과 같이 저장소 밖 작업 폴더에 둔다** — 열 이름과 코드 체계도 사내 정보다.

## Claude Desktop · Gemini CLI 에 붙이기 (로컬 MCP)

AI 가 **사용자 PC 에서** 조사 · 변환 · 검증 · 미리 보기를 직접 돌리게 한다. 도구는
`sp_mcp.py` 하나이고, 두 클라이언트가 같은 설정 모양으로 붙는다.

**1. 설치 — 더블클릭.** 받는 사람은 개발자가 아니다(Claude Desktop 만 쓰는 데이터 담당자).

1. 플랫폼 화면 **「내 정보」 → 정제 도구 키트**에서 zip 을 받아 PC 에 푼다(처음 한 번). 서버 번들이
   같은 판을 들고 있다 — 사내망에서는 GitHub 릴리스에 못 닿는다. **Python 3.12** 가 있어야 한다
   (python.org 에서 설치할 때 첫 화면의 「Add python.exe to PATH」 를 체크).
2. 같은 자리의 **「이 PC 에 등록 정보 복사」** — 이 플랫폼용 토큰을 하나 발급해 이름 · 주소와 함께
   클립보드에 넣는다(`SP-PIPELINE-PLATFORM {…}` 한 줄).
3. 푼 폴더의 **`install.cmd` 를 더블클릭** — 처음이면 설치까지(venv · 동봉 휠 · Claude Desktop 설정),
   이미 설치했으면 그 플랫폼만 더한다. 클립보드의 등록 정보를 읽고 **비운다**(토큰이 다음
   붙여넣기에 딸려 나가지 않게). 작업 폴더는 처음에 `~/온톨로지작업` 에 생긴다.
4. **Claude Desktop 을 완전히 종료했다가 다시 켠다.**

**적용은 그 플랫폼 화면 「작업」 에서** 사람이 「적용」 을 누른다 — 미리 보기 뒤에 AI 가 그 계획이
펼쳐진 채로 열리는 링크(`apply_on_screen`)를 준다. 그 플랫폼의 화면이니 엉뚱한 곳에 넣을 일이 없다.

**명령으로 할 때**(Gemini CLI · 다른 작업 폴더 · macOS · Linux) — 저장소에 없는 사내 파일(허브 정의 ·
대응 파일)까지 함께 들고 가려면 `KIT_PRIVATE_DIR=<폴더> ./deploy/build_pipeline_kit.sh` 로 만든
`…-private.zip` 을 쓴다(그 zip 은 올리지 않는다):

```bash
python sp_setup.py --work-root "D:\온톨로지작업" --platform rootdesign \
                   --server http://<서버>:3030/rootdesign --token spt_... --write-claude
```

- venv(`venv/`)를 만들고 `mcp` 를 동봉 휠로 깐다(인터넷이 되면 `--online`).
- `--write-claude` · `--write-gemini` 는 각 설정 파일에 `sp-pipeline` 항목만 넣는다. 원래 파일은
  `.bak` 로 남고, 다른 MCP 항목은 안 건드린다. 빼면 넣을 내용만 보여 준다.
- **플랫폼을 이 PC 의 설정에 이름으로 등록한다**(`--platform` — 그 설치의 slug. 비우면 주소
  끝에서 짓는다). 주소 · 토큰은 클라이언트 설정이 아니라 **이 PC 의 설정 파일 한 곳**에 있다:

  | OS | 설정 파일 |
  | --- | --- |
  | Windows | `%APPDATA%\sp-pipeline\settings.json` |
  | macOS | `~/Library/Application Support/sp-pipeline/settings.json` |
  | Linux | `~/.config/sp-pipeline/settings.json` |

  토큰이 평문으로 들어 있다 — 남에게 넘기지 않는다(Claude Desktop 설정에 두던 것과 같은 값이다).
- **Claude Desktop 은 완전히 종료했다가 다시 켠다.**

**한 PC 가 플랫폼 여럿에 넣는다**(허브 · 쌍둥이 여럿). 각 플랫폼 화면 「내 정보」 의 설치 명령을
같은 PC 에서 **한 번씩** 실행한다 — 앞에 등록한 것은 남고 새 이름이 더해진다. 키트는 한 벌,
Claude Desktop 의 항목도 하나다(도구가 플랫폼 수만큼 불어나지 않는다).

```bash
python sp_setup.py --platform qings --server http://<서버>:3040/qings --token spt_... --no-install
python sp_setup.py --forget qings                # 등록을 뺀다
```

- **작업 폴더가 넣을 곳을 기억한다.** 만들 때 정한다 — 등록한 플랫폼이 하나뿐이면 그것이 되고,
  여럿이면 AI 가 어디에 넣을지 **묻는다**(짐작하지 않는다). 바꾸려면 `work_platform`.
- 검증 · 미리 보기는 그 작업의 플랫폼으로, **적용은 미리 본 그 플랫폼으로만** 간다. AI 가 알려 준
  적용 명령을 **아무 명령 창에나 그대로** 붙이면 된다 — 명령도 같은 설정 파일을 읽는다.
- 명령으로 직접 할 때는 `--platform <이름>`(하나뿐이면 생략). 한 번만 다른 곳을 볼 때는
  `--server … --token …`(등록 없이), 또는 환경 변수 `SP_SERVER` · `SP_TOKEN`.

**2. 설정의 모양** (직접 넣을 때 — `mcpServers` 안에):

```json
"sp-pipeline": {
  "command": "C:\\...\\sp-pipeline\\venv\\Scripts\\python.exe",
  "args": ["C:\\...\\sp-pipeline\\sp_mcp.py"],
  "env": {}
}
```

| 클라이언트 | 설정 파일 |
| --- | --- |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |

| 환경 변수(있으면 설정 파일보다 이긴다) | 뜻 |
| --- | --- |
| `SP_WORK_ROOT` | 작업 폴더들을 두는 곳 — **도구는 이 안만 읽고 쓴다** |
| `SP_SERVER` · `SP_TOKEN` | 등록 없이 한 곳만 볼 때(옛 설치의 모양). 플랫폼 이름을 정한 작업에는 안 쓰인다 |
| `SP_SETTINGS` | 설정 파일 자리를 바꿀 때 |

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
| `runs_list` · `run_undo` | 넣은 판들 · **되돌리기 계획**(되돌리는 것은 사람이 `undo_command` 로) |

**적용 도구도 되돌리는 도구도 없다.** `run_preview` 의 `apply_command` 와 `run_undo` 의
`undo_command` 를 사람이 확인한 뒤 직접 실행한다.

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
