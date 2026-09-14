# 정제 파이프라인 — AI 가 따르는 절차
<!--@ overview -->

이 폴더에서 일하는 AI(Claude Code · Gemini CLI)를 위한 정본이다. `CLAUDE.md` · `GEMINI.md` 는
여기를 가리킬 뿐이다 — 도구마다 따로 적으면 한쪽만 고쳐진다.

전체 계획은 [데이터 온톨로지화 계획](../docs/데이터-온톨로지화-계획.md) 에 있다.

## 작업 폴더로 일한다 — `sp-pipeline` MCP

사용자 PC 에 등록한 로컬 MCP(`sp_mcp.py`)로 일한다. 도구는 **`SP_WORK_ROOT` 안의 작업 폴더만**
읽고 쓴다. MCP 가 없으면(셸만 있는 Gemini CLI 등) 같은 일을 `sp_work.py` · `sp_profile.py` ·
`sp_table.py` · `sp_pipeline.py` 명령으로 한다 — 도구와 명령은 같은 코드다.

**작업마다 `work_status` 를 먼저 부른다.** 어디까지 했고 다음이 무엇인지 거기 있다 — 그 「다음」 을
따른다. 대화가 끊겨도 폴더가 진행을 쥐고 있다.

| 단계 | 도구 | 멈추고 사람에게 |
| --- | --- | --- |
| 0 작업 폴더 | `work_list` · `work_init` | 원천을 `00-원천/` 에 넣어 달라고(엑셀은 시트마다 CSV UTF-8) |
| 1 조사 | `source_profile`(고를 값을 적을 땐 `show_values`, 코어에 붙나는 `match`) · `source_head` | 결과를 요약해 보인다. 애매한 것은 묻는다 |
| 2 정의 초안 | `pipeline_guide("modeling")` · 플랫폼 MCP `ontology_schema` · `work_write` 로 `02-정의/ontology.json` 과 `02-정의/판단표.md` | **판단표를 보이고 확정받는다** → `decision_record(confirms_ontology=true)` |
| 3 대응 | `pipeline_guide("table")` · `work_write` 로 `03-대응/<원천 이름>.table.json` | — |
| 4 변환 | `table_convert` → 보고서를 읽고 대응을 고쳐 다시 | **미해결은 사람에게 묻고** `decision_record` |
| 4′ 문서 추출 | `run_init` · `work_write` 로 `runs/<실행>/objects/…` · `relations/…` | 확신 없는 것은 `unresolved.json` 으로 |
| 5 검증 · 미리 보기 | `run_validate` → `run_preview` | **요약을 보이고, 적용은 사람이 `apply_command` 로** — 적용 도구는 없다 |
| 허브에서 받기(쌍둥이) | `hub_pull(work, group="plm")` → `run_validate` → `run_preview` | 적용은 사람이. 받은 타입은 받는 플랫폼에서 **허브 관리**가 되어 거기서는 못 고친다 — 정의 · 값이 틀렸으면 허브 쪽 작업에서 고친다 |

- **통계를 스스로 세지 않는다.** `source_profile` 결과를 인용한다 — 수천 행을 AI 가 읽어 센 수는
  틀려도 알 길이 없다.
- **사람이 정한 것은 전부 `decision_record` 에.** 다음 대화의 AI 가 같은 질문을 다시 하지 않게.
- **미해결을 지우거나 실행 폴더를 손봐 넘기지 않는다.** 정의 · 대응을 고쳐 **새로 변환**한다.
- 확정한 뒤 정의를 고쳤으면 **다시 확정받는다**(`work_status` 가 알려 준다).
- 원천(`00-원천`)은 통째로 읽지 않는다 — 표는 조사 · 앞부분 보기로, 문서는 사용자가 대화에 첨부한 것으로.
- 코어(허브에서 받은 정의)의 식별자는 **원문 그대로.** 짐작해 만든 코드가 우연히 실제 코드와 겹치면
  엉뚱한 것에 조용히 붙는다.

## 하지 않는 것

- **원천을 MCP 도구(`object_create` · `objects_import` · `ontology_import`)로 곧바로 넣지 않는다.**
  결과물은 언제나 **실행 폴더**에 만들고, 넣는 것은 사람이 `preview` 를 보고 `apply` 로 한다.
- **확신하지 못한 것을 지어내지 않는다.** `unresolved.json` 에 적고 사람에게 묻는다.
- **식별자를 매번 새로 만들지 않는다.** 원천에 식별자가 있으면 그것을, 없으면 같은 원천에서
  늘 같은 값이 나오는 규칙으로 만든다. 식별자가 흔들리면 다시 넣을 때 같은 것이 둘이 된다.
- 지우지 않는다. 플랫폼 가져오기는 더하고 고치기만 한다.

MCP 도구는 **읽기**에 쓴다: `get_guide` · `ontology_schema`(지금 무엇이 정의돼 있나) ·
`objects_list` · `object_fields`(이미 들어간 것과 겹치나) · `bundle_import(apply=false)`
(만든 묶음이 어떻게 들어갈지 스스로 확인).

<!--@ run -->
## 한 번 도는 절차

1. **실행 폴더를 만든다** — `python sp_pipeline.py init runs/<날짜>-<무엇>`
2. **원천을 적는다** — `bundle.json` 의 `sources` 에 파일 · 시스템 · 문서와 받은 날짜.
3. **지금 정의를 읽는다** — `ontology_schema`. 이미 있는 타입 · 속성 · 관계 종류를 먼저 쓴다.
   새로 만드는 것은 꼭 필요할 때만, 그리고 이유를 `bundle.json` 의 `notes` 에.
4. **정의가 더 필요하면 `ontology.json` 에** — `ontology_import` 와 같은 모양. 더하고 고치기만.
5. **객체를 `objects/<type_slug>.json` 에, 관계를 `relations/<type_slug>.json` 에** 만든다.
   모든 행에 `_source` 를 붙인다.
6. **판단이 안 서는 것은 `unresolved.json` 에** 적고 사람에게 묻는다. 답을 받아 반영한 뒤 비운다.
7. **검증** — `python sp_pipeline.py validate runs/...`. 오류가 0 이 될 때까지 고친다.
8. **미리 보기** — `python sp_pipeline.py preview runs/...`. 결과(`preview.json`)를 사람에게
   요약해 보여 준다: 정의 변경 · 경고, 타입마다 새로/고침/그대로/오류, 오류 행과 이유.
9. **사람이 확인한 뒤에만** `python sp_pipeline.py apply runs/...`. 미리 본 뒤 파일이 바뀌었으면
   도구가 거절한다 — 그때는 8 로 돌아간다.

## 실행 폴더의 모양

```
runs/2026-09-13-sim-tools/
  bundle.json          무엇 · 언제 · 원천 · 넣는 차례
  ontology.json        정의(없으면 빈 목록들)
  objects/
    sim_company.json   {"type_slug", "workspace_slug", "rows": [...]}
    sim_tool.json
  relations/
    sim_tool.json      {"type_slug", "rows": [{"src","relation","dst","evidence_note"}]}
  unresolved.json      [] 이어야 넣을 수 있다
  preview.json         (도구가 쓴다) 미리 본 결과와 지문
  applied.json         (도구가 쓴다) 넣은 결과
```

### bundle.json

```json
{
  "format": "sp-bundle/1",
  "title": "시뮬레이션 툴 목록 정리",
  "created_at": "2026-09-13T09:00:00+00:00",
  "sources": [{"name": "툴목록_2026.xlsx", "received": "2026-09-12", "owner": "해석팀"}],
  "objects_order": ["sim_company", "sim_tool"],
  "relations_order": [],
  "notes": "라이선스를 고를 값으로 둔 이유: ..."
}
```

**`objects_order` 가 넣는 차례다.** 다른 타입을 참조하는 타입(툴 → 개발사)은 참조되는 타입 뒤에
둔다. 안 적은 타입은 뒤에 파일 이름 순으로 붙는다.

### 객체 행 — 플랫폼 「파일로 넣기」 와 같다

```json
{
  "type_slug": "sim_tool",
  "workspace_slug": "cae",
  "rows": [
    {
      "key": "ansys-fluent",
      "label": "Ansys Fluent",
      "vendor": "ansys",
      "physics": ["유체", "열"],
      "aliases": "Fluent;ANSYS Fluent",
      "_source": {"file": "툴목록_2026.xlsx", "sheet": "CFD", "row": 12},
      "_confidence": 0.9,
      "_note": "개발사는 시트의 '제조사' 열"
    }
  ]
}
```

- 칸 이름은 **속성 키**(`ontology_schema` 의 `properties[].key`). 고정 칸은 `key` · `label` ·
  `description` · `status` · `aliases` · `valid_from_year` · `valid_to_year`.
- 참조 칸은 상대의 **식별자**로 적는다(없으면 별칭 · 이름으로 풀리지만, 겹치면 거절된다).
- 여러 값 칸은 배열. 값을 비우려면 `null`. **안 적은 칸은 안 건드린다.**
- **`_` 로 시작하는 칸은 플랫폼에 안 간다** — `_source`(필수에 가깝다) · `_confidence` · `_note`.
- `workspace_slug` 가 없으면 전역으로 들어가고, 전역은 시스템 관리자만 넣는다.
- 한 파일에 5000행까지.

### 관계 행

```json
{
  "type_slug": "sim_tool",
  "rows": [
    {
      "src": "ansys-fluent",
      "relation": "competes_with",
      "dst": "star-ccm",
      "evidence_note": "벤더 비교표(2026) 의 CFD 경쟁 제품",
      "_source": {"file": "비교표.pdf", "page": 3}
    }
  ]
}
```

`src` 는 이 파일의 `type_slug` 객체, `dst` 는 관계 종류가 허락한 타입의 객체다. **`evidence_note`
에 왜 이었는지 적는다** — 근거 없는 연결은 시간이 지나면 아무도 못 믿는다.

### unresolved.json

```json
[
  {"what": "시트 'CFD' 의 'PowerFLOW'", "question": "Dassault 제품인가 Exa 제품인가",
   "options": ["dassault", "exa"], "_source": {"file": "툴목록_2026.xlsx", "row": 31}}
]
```

사람이 답하면 반영하고 목록에서 뺀다. **비어 있지 않으면 `validate` 가 막는다.**

<!--@ table -->
## 표를 규칙으로 옮기기 — `sp_table.py`

엑셀 · CSV 는 행마다 AI 가 판단하지 않는다(규약 5장). **대응 파일**을 만들고 도구가 옮긴다.
AI 가 할 일은 대응 파일을 쓰는 것과 보고서를 읽고 규칙을 고치는 것이다.

```bash
python sp_table.py <대응.table.json> <원천.csv> runs/2026-09-13-plm-models
python sp_pipeline.py validate runs/2026-09-13-plm-models      # 그 뒤는 여느 실행과 같다
```

- 실행 폴더를 **새로** 만든다(있으면 멈춤). `bundle.json` 의 `sources` · `objects_order` ·
  `notes`(열 → 칸 대응)를 채우고, 5000행이 넘는 타입은 `<slug>-001.json` … 으로 나눈다.
- **같은 식별자가 여러 행에 나오면 한 객체로 모은다.** 칸 값이 행마다 다르면 그 칸은 보내지
  않고 `unresolved.json` 에 올린다 — 대개 그 칸이 윗단계(과제)가 아니라 행(모델)의 칸이라는 뜻이다.
  정의와 대응을 고쳐 **새 실행 폴더로 다시 돌린다.** 미해결을 손으로 지워 넘기지 않는다.
- 보고서 `table-report.txt` 는 값을 **가린 패턴**(영문 `A` · 숫자 `9` · 한글 `가`)으로만 적는다 —
  사내 밖으로 옮겨 규칙을 의논해도 된다. 원천 값은 실행 폴더의 파일에만 있다.
- 인코딩은 BOM 붙은 UTF-8, 안 되면 CP949 로 읽는다(`source.encoding` 으로 고정).

### 대응 파일 (`sp-table/1`)

```json
{
  "format": "sp-table/1",
  "title": "과제-모델 표",
  "workspace_slug": "plm",
  "ontology": "plm-ontology.json",
  "source": {"encoding": "auto", "delimiter": ","},
  "dictionaries": {"region": ["KOR", "NA", "EUR"]},
  "parsers": {
    "model_name": {
      "column": "모델명", "separator": "_", "rare_below": 6,
      "slots": [
        {"name": "base", "kinds": {"SM": "^SM-", "OL": "^OL-"}},
        {"name": "marker", "optional": true, "values": ["D1", "D2"]},
        {"name": "region", "dictionary": "region"},
        {"name": "revision", "optional": true, "kinds": {"OS 버전": "^[0-9]{2}$", "차수": "^[A-Z][0-9]$"}},
        {"name": "carrier"}
      ],
      "checks": [
        {"slot": "revision", "suffix_of": "과제명", "joiner": "_"},
        {"require": "revision", "when_column": "SRA일", "in": ["Skip"]}
      ]
    }
  },
  "types": [
    {"type_slug": "plm_project", "key": {"column": "프로젝트명"}},
    {"type_slug": "plm_task", "key": {"column": "과제코드", "upper": true}, "label": {"column": "과제명"},
     "fields": {
       "project": {"key_of": "plm_project"},
       "sra_on": {"column": "SRA일", "date": true, "blank": ["Skip", "-"]},
       "sra_kind": {"column": "SRA일", "map": {"<date>": "실적", "Skip": "면제", "-": "미도래"}}
     }},
    {"type_slug": "plm_model", "key": {"column": "모델명"},
     "fields": {
       "task": {"key_of": "plm_task"},
       "region": {"parser": "model_name", "slot": "region"},
       "revision_kind": {"parser": "model_name", "slot": "revision", "part": "kind"}
     }}
  ]
}
```

- **`types` 의 차례가 넣는 차례다** — 참조되는 타입을 먼저.
- `ontology` 는 대응 파일 기준 상대 경로. 있으면 실행 폴더의 `ontology.json` 이 된다.
- 칸 하나를 얻는 법 — 넷 중 하나:

  | 모양 | 뜻 |
  | --- | --- |
  | `{"column": "열"}` | 열의 값(앞뒤 공백 뗌). 빈 칸은 `null`(비움) |
  | `{"key_of": "<type_slug>"}` | 그 타입의 식별자를 **같은 규칙으로** — 참조 칸에 쓴다. 따로 적으면 대문자 · 공백 처리가 어긋난다 |
  | `{"parser": "<이름>", "slot": "<조각>", "part": "kind"?}` | 해석기의 조각 값, `part: "kind"` 면 그 조각의 종류 이름 |
  | `{"value": ...}` | 고정값 |

  `column` 에 붙는 것: `upper`(대문자로) · `blank`(빈 칸으로 볼 값들) · `date`(`YYYY-MM-DD` ·
  `.` · `/` → ISO, 아니면 비우고 보고서에) · `map`(값 → 값. `<date>` 는 날짜 모양, `<blank>` 는 빈 칸.
  대응에 없는 값은 비우고 보고서에).
- `key` 가 빈 행은 그 타입에서 건너뛴다(보고서에 건수). `label` 을 안 적으면 `key` 와 같다.
- **참조 대조** — 코어(허브에서 받은 PLM 기준정보)를 가리키는 칸은 `column` 에 `match` 를 붙인다:
  `{"column": "모델명", "match": {"keys": "plm_model", "prefix": true, "on_missing": "blank"}}`.
  - `keys`: 식별자를 받을 타입 slug(플랫폼에서 — `SP_SERVER` · `SP_TOKEN`) 또는 `@파일`(한 줄에 하나,
    대응 파일 기준 경로).
  - 맞추는 차례: 그대로 → 대소문자만 다름 → (`prefix` 면) 앞부분이 **하나뿐인** 식별자. 둘 이상이면
    고르지 않는다.
  - `on_missing`: `blank`(기본 — 비워 넣고 보고서에) · `unresolved`(미해결로 올려 사람에게) · `keep`
    (그대로 보냄 — 플랫폼이 거절하면 묶음 전체가 막힌다).
  - 보고서 `[참조 대조]` 에 그대로 · 대소문자 · 앞부분 · 여러 개 · 없음의 건수와 못 맞춘 것의 가린 패턴.

### 해석기 — 이름에 박힌 조각

- `separator` 로 자른 조각을 **`slots` 의 차례대로** 채운다. **위치가 자리를 정하고, 모양은
  그 자리 안의 종류만 가른다.**
- 조각 조건: `values`(목록) · `dictionary`(`dictionaries` 의 이름) · `pattern`(정규식) ·
  `kinds`(종류 이름 → 정규식, **적은 차례대로** 먼저 맞는 것). 조건이 없으면 무엇이든 맞는다 —
  마지막 자리처럼 **자리로만 정해지는** 조각.
- `optional` 조각은 맞을 때만 먹는다. 모든 조각을 남김없이 채우는 길이 **하나일 때만** 읽힌 것 —
  없으면 `못 나눔`, 둘 이상이면 `두 갈래로 읽힘`. **고르지 않는다.**
- `checks` — 읽힌 뒤의 검증. `suffix_of`: 조각이 있으면 다른 열이 `<joiner><조각>` 으로 끝나야
  한다. `require`: `when_column` 이 `in` 중 하나면 그 조각이 있어야 한다.
- 못 읽었거나 검증을 어긴 행은 **넣되 해석 칸을 모두 비운다**(`null`). 보고서에 이유별 패턴이 나온다.
- `rare_below`: 조각 값이 그보다 적게 나오면 보고서에 드문 값으로 — 마스터와 대조할 후보. 조각에
  `rare_below` 를 따로 적으면 그 조각만 바뀐다(기본 모델코드처럼 원래 종류가 많은 조각은 `0`).

<!--@ sources -->
## 무엇을 무엇으로 만드나 — `get_guide(topic="modeling")`

**판단의 기준은 모델링 규약이다** — 서버가 쥐고 `get_guide(topic="modeling")` 로 내려준다(본문은
`mcp_server/guide/GUIDE.md`). 정제를 시작하기 전에 읽는다. 요점:

- **코어를 먼저 쓴다** — PLM 기준정보 [`core/plm-core.json`](core/plm-core.json)(프로젝트 · 과제 ·
  개발모델 · 시험 주체 — 허브가 내려주고 쌍둥이는 **받기만** 한다)과 업무 공통
  [`core/work-core.json`](core/work-core.json)(업무 · 문서 · 사람 · 기관 · 설비 · 소프트웨어 · 기술
  분야, 근거 문서 · 담당 · 사용 같은 관계). 그룹의 일은 그룹 타입으로 만들고 **PLM 과제 · 모델을
  참조 칸으로 가리킨다.** 그룹 전용 타입과 속성은 `<그룹코드>_` 로 시작한다.
- **질문에서 시작한다** — 그룹이 채운 [`templates/파일럿-그룹-정리.md`](templates/파일럿-그룹-정리.md)
  의 질문 목록에 답하는 데 필요한 것만 만든다.
- **식별자는 다시 돌려도 같게** — 원천 번호 → 정규화한 이름 → 문서는 `doc-<sha256 앞 12자>`.

## 원천별로 다른 것

| | 엑셀 · CSV | PPT · PDF · Word |
| --- | --- | --- |
| 먼저 할 일 | 시트마다 「한 행이 무엇인가」 를 정하고 열 → 속성 대응표를 `notes` 에 | 텍스트로 바꾼다(PDF 는 바로, PPT · Word 는 쪽 · 슬라이드 번호를 보존해 마크다운으로) |
| 누가 옮기나 | **규칙(스크립트)** — 행마다 AI 가 판단하지 않는다 | AI 가 뽑는다 |
| 문서 자체 | — | **`document` 객체로 넣는다**, 뽑은 사실은 `evidence`(근거 문서)로 그 문서와 잇는다 |
| `_source` | `{"file", "sheet", "row"}` | `{"file", "page" \| "slide", "quote"}` — quote 는 원문 한두 문장 |
| `_confidence` | 대개 1 | 원문 그대로 0.9+ · 표 · 그림 0.7~0.9 · **0.7 미만은 넣지 않고 unresolved** |
| 옮기는 것 | 대응표에 있는 열 | 여러 문서에 걸쳐 묻는 것(과제번호 · 설비 · 기관 · 기술 분야)만. 나머지는 문서 description 에 요약 |
