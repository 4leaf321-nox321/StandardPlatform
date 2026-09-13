# 정제 파이프라인 (`pipeline/`)

원천 데이터를 **AI 로 정제해 결과물 묶음으로 만들고, 사람이 검토한 뒤 플랫폼에 넣는** 로컬 도구.
만드는 일은 사용자 PC 의 Claude Code · Gemini CLI 가, 검토 · 적재는 플랫폼이 한다.

- 계획과 순서: [docs/데이터-온톨로지화-계획.md](../docs/데이터-온톨로지화-계획.md)
- AI 가 따르는 절차와 실행 폴더의 모양: [AGENTS.md](AGENTS.md) (**정본**)

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

도구가 막고 멈추면 종료 코드 2 와 함께 이유를 적는다(검증 실패 · 미리 본 뒤 바뀜 · 서버 거절).

## 실행 폴더를 어디에 두나

**그룹의 데이터는 이 저장소 밖에 둔다** — 원천 · 실행 폴더 · 정답 세트 · 동의어 사전. 사내 데이터가
git 에 섞이지 않게. 실행 폴더 하나가 한 번의 정제이고, 지난 폴더를 지우지 않으면 무엇이 언제
어떻게 들어갔는지가 남는다.
