---
name: StandardPlatform 온톨로지
description: StandardPlatform(사내 구조화 데이터 플랫폼)의 온톨로지를 다룰 때 사용. 정의 읽기("무슨 타입이 있어"), 정의 만들기·고치기("시뮬레이션 툴 타입 만들어줘"), 객체 넣기·고치기("이 표를 넣어줘"), 관계 잇기("A 가 B 를 쓴다고 이어줘"), 일괄 입력까지 — standardplatform MCP 도구를 쓰는 모든 작업에 적용.
allowed-tools: mcp__standardplatform__*
---

# StandardPlatform 온톨로지

`standardplatform` MCP 서버로 온톨로지(타입·속성·관계)와 그 객체를 다룬다.

## 시작하기 전에 — `get_guide()` 를 먼저 부른다

```
mcp__standardplatform__get_guide()
```

무엇을 어떤 순서로 써야 하는지, 정의를 바꿀 때 무엇을 조심할지 거기 있다. **서버가
최신본을 쥐고 있으므로** 이 파일이 오래돼도 안내는 항상 최신이다.

- 인자 없이 부르면 **"하려는 일 → 어떤 도구" 표 + 기본 습관**이 온다. 대개 이걸로 충분하다.
- 세부가 필요하면 그때 주제를 지정한다 — `find` · `schema` · `objects` · `bulk` ·
  `relations` · `sparql` · `extensions` · `modeling`.
- 한 번에 다 받지 마라. 필요한 주제만 받는 게 싸다.

## 이 파일에 내용을 더 적지 마라

이 파일은 **각자 PC 에 복사된 사본**이라, 서버를 올려도 갱신되지 않는다. 내용을
고쳐야 하면 **저장소의 `mcp_server/guide/GUIDE.md`** 를 고친다. 그러면 모두에게
즉시 반영된다(각자 다시 복사할 필요 없음).

## 최소 원칙 (가이드를 못 받았을 때만)

`get_guide()` 가 실패하면 이것만 지키고, 사용자에게 가이드를 못 받았다고 알린다.

- **스키마부터 읽는다** — `ontology_schema`. 타입 slug·속성 키를 추측하지 않는다.
- **타입을 모르면 `search`, 부서를 모르면 `whoami`** — 둘 다 짐작하지 않는다.
- **정의를 바꾸는 일은 미리 보기부터** — `ontology_import(apply=false)` 의 계획과
  경고를 사람에게 보여 주고 판단을 받은 뒤 `apply=true`.
- **일괄 입력도 같다** — `objects_import`·`relations_import` 는 기본이 계획이다.
- **관계에는 근거를 적는다** — `evidence_note`.
- **이름은 해소하고 쓴다** — `object_resolve`(여러 개면 `objects_resolve_many`).
  `candidates` 가 오면 고르지 말고 사람에게 묻는다. 목록의 첫 줄을 집으면 틀린 줄도
  첫 줄이면 집힌다.
- **「파일대로 맞춤」 은 지우는 것이다** — `aliases_mode="replace"`,
  `relations_import(mode="replace"|"replace_type")` 는 파일에 없는 것을 끊고 지운다.
  원천이 그 범위를 통째로 가지고 있을 때만 쓰고, 끊을 수를 사람에게 먼저 보여 준다.
- **기계가 붙인 별칭은 스스로 승인하지 않는다** — `aliases_pending` 으로 목록을 사람에게
  보여 주고, 사람이 고른 것만 `aliases_review` 로 보낸다.
- **0건은 「없다」 가 아니다** — 목록이 0건이면 응답의 `diagnosis` 를 읽고, 안 채운
  타입인지·부서 밖이라 안 보이는지·조건이 좁은지 갈라 말한다.
- **이 설치에만 있는 기능은 `extensions_schema` 로 본다** — 그 목록에 없는 경로는
  부르지 않는다(짐작한 경로는 404 다). 쓰기는 그 확장의 범위를 가진 토큰만 한다.
