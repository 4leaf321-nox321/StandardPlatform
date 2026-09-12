<!-- StandardPlatform MCP 사용 가이드 — **서버가 쥔다.**

로컬 스킬(~/.claude/skills)에 본문을 두면 사람마다 복사 시점이 달라 낡는다.
그래서 본문은 여기 두고 로컬엔 짧은 스텁만 남긴다. `get_guide(topic)` 이 여기서
읽어 준다. 이 파일만 고치면 모두에게 즉시 반영된다(서버 재시작도 필요 없다).

주제 구분자: `<!--@ 주제이름 -->`. 순서는 상관없다. -->
GUIDE_VERSION: 2026-09-12g

<!--@ overview -->
## 무엇을 하려는가 → 어떤 도구

| 하려는 일 | 도구 | 주의 |
|---|---|---|
| 지금 무엇이 정의돼 있나 | `ontology_schema` | **다른 도구보다 먼저** |
| 타입·속성·관계 종류를 만들거나 고치기 | `ontology_import(apply=false)` → 사람 확인 → `apply=true` | 미리 보기를 건너뛰지 않는다 |
| 객체 찾기 | `objects_list(type_slug, q=, properties=, conditions=)` | 화면과 같은 거르기 |
| 객체 하나 자세히(관련 객체까지) | `object_get` | — |
| 언제 누가 무엇을 바꿨나 | `object_history` | 되돌리기는 화면에서 |
| 지우기·합치기 전에 무엇이 걸렸나 | `object_references` | 남의 부서 것은 수만 |
| 어셈블리 총 무게처럼 「아래 전부」 의 합 | `object_rollup` | `missing` 을 함께 말한다 |
| 무엇이 나빠지고 있나(필수값·고아·끊긴 참조·중복) | `quality_report` | 볼 수 있는 것만 |
| 객체 하나 만들기 | `object_create` | 정의에 없는 속성 키는 거절된다 |
| 객체 고치기 | `object_update` | **보낸 키만** 병합. 비우려면 `null` |
| 여러 행 한 번에(upsert) | `objects_import(apply=false)` → `apply=true` | 같은 `key` 면 고침. 한 행이라도 오류면 전부 안 넣음 |
| 객체 둘 잇기 | `relation_add` | **근거(evidence_note)를 적는다** |
| 관계 여러 줄 한 번에 | `relations_import(apply=false)` → `apply=true` | 이미 이어진 건 그대로 |
| 바깥 시스템(OData·REST·파일)에서 읽어 채우기 | `datasources_list` → `datasource_sync(apply=false)` → `apply=true` | 정의는 화면에서. 오류 행이 있으면 아무것도 안 넣음 |

## 기본 습관

- **스키마부터 읽는다.** 타입 slug·속성 키·관계 이름을 추측하지 않는다.
- **정의를 바꾸는 일은 두 단계다.** `apply=false` 로 계획과 경고를 받아 사람에게
  보여 주고, 판단을 받은 뒤에 `apply=true`. 에이전트의 실수는 기계 속도로 반영되고,
  온톨로지는 데이터의 모양이라 그 아래 쌓인 것이 전부 흔들린다.
- **일괄 도구도 같다.** `objects_import`·`relations_import` 는 기본이 계획이다.
- **오류는 서버의 말이다.** `{"error": "[코드] 문구"}` 가 오면 그 문구에 무엇을
  고쳐야 하는지 적혀 있다. 우회하지 말고 고쳐서 다시 부른다.
- **권한은 토큰이 정한다.** `read` 만 있는 토큰으로는 쓰기가 거절된다 —
  사용자에게 범위(`objects:write`·`ontology:write`)를 알린다.

<!--@ schema -->
## 정의 읽기·바꾸기

`ontology_schema` 가 돌려주는 것: `groups`(묶음) · `types`(타입, 각각 `properties`
와 `key_policy`) · `relation_types` · `data_types`(속성이 받는 값의 종류).

`ontology_import(schema, apply)` 의 `schema` 는 같은 모양이다:

```json
{
  "groups": [{"slug": "sim", "label": "시뮬레이션"}],
  "types": [
    {"slug": "sim_tool", "label": "시뮬레이션 툴", "nav_group_slug": "sim",
     "key_policy": "required",
     "properties": [
       {"key": "vendor", "label": "공급사", "data_type": "text"},
       {"key": "license", "label": "라이선스", "data_type": "enum",
        "enum_options": ["상용", "오픈소스"]}
     ]}
  ],
  "relation_types": [
    {"slug": "uses", "label": "사용", "src_type_slugs": ["sim_company"],
     "dst_type_slugs": ["sim_tool"]}
  ]
}
```

- **더하고 고치기만 한다.** 스키마에 없다고 지우지 않는다.
- `apply=false` 응답의 `warnings` 가 **조용히 잃는 것**이다(값 종류가 바뀌어 저장값이
  안 맞게 되는 것 등). 반드시 사람에게 보여 준다.
- `apply=true` 응답의 `snapshot_id` 가 되돌릴 자리다. 되돌리기는 화면의
  **관리 → 온톨로지 → 가져오기·이력**에서 한다.

<!--@ find -->
## 찾기와 살피기

`objects_list(type_slug, q=, properties=, conditions=, status=, limit=, offset=)`:

- `q` 는 이름·식별자·검색 속성. `properties={"grade": "A"}` 는 「같음」 의 짧은 꼴.
- `conditions` 는 화면의 조건 줄과 같다 — **칸끼리 AND, 같은 칸 안은 OR**:
  ```json
  [{"field": "power", "op": "gte", "value": "10"},
   {"field": "license", "op": "in", "value": "상용|오픈소스"},
   {"field": "vendor", "op": "notempty", "value": ""}]
  ```
  연산은 칸의 종류가 정한다(숫자·날짜 `eq ne gt gte lt lte in`, 글자 `eq ne contains
  starts in`, 선택·참조 `eq ne in`, 참/거짓 `eq`, 모두 `empty notempty`). 안 맞는 연산은
  서버가 `[코드] 문구` 로 거절한다 — 우회하지 말고 고친다.
- 응답의 `total` 이 전체 수다. 한 쪽은 200까지. 다 세야 하면 `offset` 으로 넘긴다.

`object_history(type_slug, object_id)` — 언제·누가·어느 칸을 전→후. 값 기록의
`snapshot` 이 그 시점의 값 전체다. 되돌리기는 사람이 화면에서 한다.

`object_references(type_slug, object_id)` — 이 객체를 가리키는 속성·관계. 지우거나
합치기 전에 본다. `hidden_*` 는 남의 부서 것이라 수만 온다 — 0 이 아니면 지우지 않는다.

`quality_report(kind=)` — 필수값 빈 객체·관계 없는 객체·지워진 것을 가리키는 칸·이름이
같은 객체. 사용자가 「데이터 정리해 줘」 라고 하면 여기서 시작한다: 찾고, 사람에게
보여 주고, 판단을 받은 뒤 `object_update` 로 고친다.

<!--@ objects -->
## 객체 하나씩

- `object_create(type_slug, label, key=, properties=, workspace_slug=)` —
  `workspace_slug` 를 비우면 **전역** 객체라 시스템 관리자만 만들 수 있다.
  `key_policy` 가 `required` 인 타입은 `key` 가 있어야 한다.
- `object_update(type_slug, object_id, properties=)` — **보낸 키만** 병합한다.
  값을 비우려면 그 키에 `null`. 통째로 덮지 않는다.
- 참조 속성(`data_type: object_ref`)에는 상대 객체의 **id** 를 넣는다. id 를 모르면
  `objects_list` 로 먼저 찾는다 — 찾기는 별칭에도 걸린다.
- **별칭** — 같은 것을 다르게 부르면(「Ansys」 「앤시스」 「ANSYS Inc.」)
  `object_update(aliases=[...])` 로 다른 이름을 붙인다. 그 뒤로 파일·참조·찾기가 그 표기로도
  같은 객체를 찾는다. **같은 것을 새로 만들지 말고 별칭을 붙인다.** 이미 둘이 됐으면 사람이
  화면에서 「합치기」 — 지는 쪽 이름이 자동으로 별칭이 된다.
- `object_get` 은 `object` · `properties_schema` · `related`(양방향) 를 함께 준다 —
  화면의 상세와 같은 것이다.
- `kind_class` 가 `system` 인 타입(부서·계정 등)은 **행이 없다** — 다른 표를 비춘다.
  `objects_list` 로 읽고 `object_ref`·관계의 상대로 쓸 수는 있지만, 만들거나 고치지는
  못한다(그 표의 화면에서 한다). 파일·`objects_import` 에서는 그 표의 식별자(부서면
  slug, 계정이면 로그인 아이디)로 적는다.

<!--@ bulk -->
## 여러 행 한 번에

`objects_import(type_slug, rows, workspace_slug=, apply=false)`:

```json
[{"key": "T-001", "label": "ANSYS Fluent", "vendor": "Ansys", "license": "상용"},
 {"key": "T-002", "label": "OpenFOAM", "vendor": null}]
```

- 같은 `key` 가 이미 있으면 **고친다**(upsert). 없는 키는 안 건드린다.
  `null` 이 비움이다.
- 참조 속성은 상대의 **식별자(key)**, **별칭**, 없으면 **이름(label)** 순으로 풀린다.
  겹치면 거절된다 — 그때는 id 로 적는다.
- `aliases` 열에 `;` 로 여럿 — 그 객체의 다른 이름을 함께 넣는다(통째로 바꿈).
- 응답은 행마다 `create` / `update` / `unchanged` / `error`. **한 행이라도 `error`
  면 `apply=true` 여도 아무것도 안 들어간다.** 오류를 고쳐 다시 보낸다.
- 한 번에 5000행까지. 더 많으면 나눈다.

<!--@ relations -->
## 객체 잇기

- `relation_add(type_slug, object_id, relation, dst_object_id, evidence_note)` —
  `relation` 은 `ontology_schema` 의 `relation_types[].slug`. 출발 타입·도착 타입이
  정의와 맞아야 한다.
- **근거를 적는다.** 어느 문서·어느 자료에서 이 연결이 나왔는지. 근거 없는 연결은
  시간이 지나면 아무도 못 믿고, 확인하려면 처음부터 다시 조사해야 한다.
- 여러 줄이면 `relations_import(type_slug, rows, apply)` — 행은
  `{"src": "<key 또는 label>", "relation": "<slug>", "dst": "...", "evidence_note": "..."}`.
  이미 이어진 것은 `unchanged` 라 두 번 올려도 두 겹이 안 된다.
