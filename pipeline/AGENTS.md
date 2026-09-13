# 정제 파이프라인 — AI 가 따르는 절차

이 폴더에서 일하는 AI(Claude Code · Gemini CLI)를 위한 정본이다. `CLAUDE.md` · `GEMINI.md` 는
여기를 가리킬 뿐이다 — 도구마다 따로 적으면 한쪽만 고쳐진다.

전체 계획은 [데이터 온톨로지화 계획](../docs/데이터-온톨로지화-계획.md) 에 있다.

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

## 무엇을 무엇으로 만드나 — `get_guide(topic="modeling")`

**판단의 기준은 모델링 규약이다** — 서버가 쥐고 `get_guide(topic="modeling")` 로 내려준다(본문은
`mcp_server/guide/GUIDE.md`). 정제를 시작하기 전에 읽는다. 요점:

- **코어 온톨로지를 먼저 쓴다** — [`core/core-ontology.json`](core/core-ontology.json). 과제 · 업무 ·
  문서 · 사람 · 기관 · 설비 · 소프트웨어 · 기술 분야, 그리고 근거 문서 · 담당 · 사용 같은 관계.
  그룹 전용 타입과 속성은 `<그룹코드>_` 로 시작한다.
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
