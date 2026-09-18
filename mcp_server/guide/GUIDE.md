<!-- StandardPlatform MCP 사용 가이드 — **서버가 쥔다.**

로컬 스킬(~/.claude/skills)에 본문을 두면 사람마다 복사 시점이 달라 낡는다.
그래서 본문은 여기 두고 로컬엔 짧은 스텁만 남긴다. `get_guide(topic)` 이 여기서
읽어 준다. 이 파일만 고치면 모두에게 즉시 반영된다(서버 재시작도 필요 없다).

주제 구분자: `<!--@ 주제이름 -->`. 순서는 상관없다. -->
GUIDE_VERSION: 2026-09-18a

<!--@ overview -->
## 무엇을 하려는가 → 어떤 도구

| 하려는 일 | 도구 | 주의 |
|---|---|---|
| 지금 무엇이 정의돼 있나 | `ontology_schema` | **다른 도구보다 먼저** |
| 원천(엑셀 · PPT · PDF · Word)을 정제해 온톨로지로 만들기 전에 | `get_guide(topic="modeling")` | **무엇을 타입 · 속성 · 관계로 만드나** — 판단이 서지 않으면 만들지 않는다 |
| 타입·속성·관계 종류를 만들거나 고치기 | `ontology_import(apply=false)` → 사람 확인 → `apply=true` | 미리 보기를 건너뛰지 않는다 |
| **이름으로 무언가를 가리킨다** | `object_resolve(type_slug, name)` | `candidates` 면 **고르지 말고 사람에게 묻는다** |
| 객체 찾기 | `objects_list(type_slug, q=, properties=, conditions=)` | 화면과 같은 거르기. **0건이면 `diagnosis` 를 읽는다** |
| 몇 건인가 — 부서별·등급별·개발사 국가별 | `objects_summary(type_slug, group_by=, conditions=)` | **목록을 받아 직접 세지 않는다.** 「(비어 있음)」·「그 밖에」·`overlap` 을 함께 말한다 |
| 다른 타입의 칸으로 거르거나 세기(「미국 기업이 만든 툴」) | `object_fields` → 주소를 `conditions`·`group_by` 에 | 한 걸음까지. 주소를 추측하지 않는다 |
| 객체 하나 자세히(관련 객체까지) | `object_get` | — |
| 언제 누가 무엇을 바꿨나 | `object_history` | 되돌리기는 화면에서 |
| 지우기·합치기 전에 무엇이 걸렸나 | `object_references` | 남의 부서 것은 수만 |
| 어셈블리 총 무게처럼 「아래 전부」 의 합 | `object_rollup` | `missing` 을 함께 말한다 |
| 무엇이 나빠지고 있나(필수값·고아·끊긴 참조·중복) | `quality_report` | 볼 수 있는 것만 |
| 객체 하나 만들기 | `object_create` | 정의에 없는 속성 키는 거절된다 |
| 객체 고치기 | `object_update` | **보낸 키만** 병합. 비우려면 `null` |
| 정제 도구가 만든 묶음(정의 · 객체 · 관계)이 어떻게 들어갈지 | `bundle_import(bundle, apply=false)` | **넣는 것은 사람이 미리 보기를 본 뒤에만.** 원천을 곧바로 넣지 않는다 — `pipeline/AGENTS.md` |
| 여러 행 한 번에(upsert) | `objects_import(apply=false)` → `apply=true` | 같은 `key` 면 고침. 한 행이라도 오류면 전부 안 넣음 |
| 객체 둘 잇기 | `relation_add` | **근거(evidence_note)를 적는다** |
| 관계 여러 줄 한 번에 | `relations_import(apply=false)` → `apply=true` | 이미 이어진 건 그대로 |
| 바깥 시스템(OData·REST·파일)에서 읽어 채우기 | `datasources_list` → `datasource_sync(apply=false)` → `apply=true` | 정의는 화면에서. 오류 행이 있으면 아무것도 안 넣음 |
| 여러 타입을 건너뛰어 잇는 물음 · 역관계로 거슬러 세기 | `rdf_schema` → `rdf_query` | **먼저 `objects_summary` 로 되는 물음인지 본다.** 질의어는 그것으로 안 되는 자리에 |

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
- **이름은 해소하고 쓴다.** 참조 칸을 채우기 전에, 관계를 잇기 전에, 「그 부품」 이
  무엇인지 정하기 전에 `object_resolve`. 목록에서 첫 줄을 집으면 **틀린 줄도 첫 줄이면
  집힌다** — 그렇게 들어간 값은 사람 눈에 맞는 값처럼 보여서 아무도 안 고친다.
- **0건은 「없다」 가 아니다.** 목록이 0건이면 응답에 `diagnosis` 가 붙는다. 안 채운
  타입인지(`empty_type`), 부서 밖이라 안 보이는지(`not_visible`), 조건이 좁은지
  (`filters`) 거기 적혀 있다. 읽기 전에 「없습니다」 라고 답하지 않는다.
- **「모름」 과 「아님」 을 안 섞는다.** 조건에 안 맞아 빠진 것과 **값이 비어 있어서**
  빠진 것은 다른 일이다. 진단의 `filters[].unknown` 이 그 수다 — 「조건에 맞는 것이
  없다」 와 「그 칸을 아직 아무도 안 채웠다」 를 갈라 말한다.

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
     "key_policy": "required", "icon": "Cog",
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

- **`icon` 을 골라 준다.** 사이드바에 설 그림 이름(lucide)이다. 안 주면 타입이 전부
  같은 네모로 서고, 그러면 사람이 메뉴에서 이름을 한 자씩 읽어야 한다. 뜻이 가까운
  것으로: `Box`(부품) · `Cog`(설비·툴) · `FlaskConical`(시험) · `Microscope`(해석) ·
  `Building2`(조직) · `Users`(사람) · `FileText`(문서) · `Truck`(물류) ·
  `ClipboardCheck`(검사) · `Target`(지표) · `Database`(기준정보) · `Workflow`(공정).
  모르는 이름을 주면 기본 그림으로 떨어질 뿐 오류는 아니다.
- **더하고 고치기만 한다.** 스키마에 없다고 지우지 않는다.
- `apply=false` 응답의 `warnings` 가 **조용히 잃는 것**이다(값 종류가 바뀌어 저장값이
  안 맞게 되는 것 등). 반드시 사람에게 보여 준다.
- `apply=true` 응답의 `snapshot_id` 가 되돌릴 자리다. 되돌리기는 화면의
  **관리 → 온톨로지 → 가져오기·이력**에서 한다.

<!--@ find -->
## 찾기와 살피기

### 먼저 — 이름 하나가 어느 객체인가: `object_resolve(type_slug, name)`

돌아오는 `match` 가 셋 중 하나다.

| `match` | 뜻 | 할 일 |
|---|---|---|
| `exact` | 하나로 정해졌다 | `object.id` 를 쓴다 |
| `candidates` | 여럿이거나, 이름의 일부만 겹친다 | **쓰지 않는다.** 후보를 사람에게 보여 주고 묻는다 |
| `none` | 없다 | 오타인지 아직 안 만든 것인지 사람에게 묻는다. **짐작해서 다른 것을 쓰지 않는다** |

식별자 → 별칭 → 이름 → 포함 차례로 맞추고, 앞에서 정해지면 뒤는 안 본다. 별칭이 있으니
「앤시스」 로 물어도 「Ansys」 가 나온다. **포함으로 하나만 걸려도 `exact` 가 아니다** —
포함은 짐작이고, 짐작을 확정으로 부르면 그 짐작이 그대로 저장된다.

### 0건일 때 — 목록에 붙어 오는 `diagnosis`

`objects_list` 가 0건이면 응답에 `diagnosis` 가 붙는다:

```json
{"reason": "filters", "type_total": 240, "hidden": 0,
 "message": "…「등급 = Z」 하나만 빼면 240건입니다. 그 중 180건은 그 칸이 비어 있어서…",
 "filters": [{"label": "등급 = Z", "remaining": 240, "unknown": 180}],
 "next_steps": ["…"]}
```

- `empty_type` — 그 타입에 객체가 하나도 없다. 조건 문제가 아니다.
- `not_visible` — 있지만 내 부서 밖이라 안 보인다. **없는 것이 아니라 권한이 없는 것이다.**
- `filters` — 조건이 좁다. `filters[].remaining` 이 「이 조건만 빼면 몇 건」,
  `unknown` 이 「그 칸에 **값이 없어서**」 빠진 수다(다른 타입의 칸이면 **이어진 것이
  아예 없는 것**도 여기 든다). `unknown` 이 `null` 이면 셀 수 없는 조건이다 —
  0 이 아니라 **모른다**.

사용자에게 옮길 때 셋을 섞지 않는다. 「없습니다」 와 「안 보입니다」 와 「조건에 맞는 것이
없습니다」 와 「그 칸을 아직 아무도 안 채웠습니다」 는 **서로 다른 다음 행동**을 부른다.

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
- 응답의 `total` 이 전체 수다. 한 쪽은 200까지. **몇 건인지 세려면 넘기지 말고 통계로.**

### 다른 타입의 칸 — `object_fields(type_slug)`

「미국 기업이 만든 툴」 은 기업 목록을 거치지 않는다. `object_fields` 가 주는 주소를
`conditions` 의 `field` 에 그대로 넣는다:

```json
[{"field": "ref.vendor.country", "op": "eq", "value": "미국"},
 {"field": "out.used_by", "op": "notempty", "value": ""}]
```

- `ref.<참조 칸>.<칸>` 참조 칸이 가리키는 것의 칸 · `out.<관계>` 관계로 이어진 것 자체(값은
  상대 id) · `out.<관계>.<칸>` 그것의 칸 · `in.<관계>[.<칸>]` 들어오는 관계.
- **한 걸음까지다.** 「개발사의 모회사의 국가」 는 없다.
- 이어진 것이 여럿이면 **그중 하나라도** 맞으면 걸린다. 이어진 것이 **없는** 객체는 너머의 칸
  조건에 안 걸린다(`ne` 도). 「개발사가 없는 툴」 은 참조 칸 `vendor` 의 `empty` 로 묻는다.
- 참조 칸과 관계가 같은 이름일 수 있다 — `heading` 으로 가른다.
- 타입 사이의 **길 전부**는 `ontology_schema` 의 `relation_types` + `reference_edges` 다 — 참조 칸도
  `ref:<타입>.<칸>` 이라는 관계 모양으로 나온다(그래프 · 관련 객체가 같은 것을 쓴다).

### 통계 — `objects_summary(type_slug, group_by=, split_by=, metric=, metric_field=, order=, ...)`

「몇 건」 은 서버가 센다. 목록을 받아 세면 쪽 상한에서 틀린다. 거르기 인자는 `objects_list`
와 같아서 **같은 조건이면 total 이 같다.**

- `group_by`: `status`·`workspace`·`created_year`·`label`·`key`, 속성은 `properties.<키>`, 다른
  타입의 칸은 `object_fields` 의 주소. 쓸 수 있는 전부가 응답의 `group_options`.
- `metric` 이 `sum`·`avg`·`min`·`max` 면 `metric_field`(숫자 속성)가 필요하다.
- `order="asc"` 는 「가장 낮은 것」 을 찾을 때.
- 옮길 때 **빼먹지 않는다**: `total` 은 객체 수, 「(비어 있음)」 도 한 칸, `other_groups`·
  `other_count` 가 0 이 아니면 「그 밖에 N종류 M건」, `overlap` 이 true 면 한 객체가 여러 칸에
  들어 **칸의 합이 total 보다 클 수 있다**.
- 「그 칸이 뭔데」 는 `buckets[].key` 를 조건 값으로 `objects_list` — 기준이 `properties.<키>`
  면 field `<키>`, 다른 타입의 칸이면 그 주소, key 가 null 이면 `empty`.

`object_history(type_slug, object_id)` — 언제·누가·어느 칸을 전→후. 값 기록의
`snapshot` 이 그 시점의 값 전체다. 되돌리기는 사람이 화면에서 한다.

`object_references(type_slug, object_id)` — 이 객체를 가리키는 속성·관계. 지우거나
합치기 전에 본다. `hidden_*` 는 남의 부서 것이라 수만 온다 — 0 이 아니면 지우지 않는다.

`quality_report(kind=)` — 필수값 빈 객체·관계 없는 객체·지워진 것을 가리키는 칸·이름이
같은 객체. 사용자가 「데이터 정리해 줘」 라고 하면 여기서 시작한다: 찾고, 사람에게
보여 주고, 판단을 받은 뒤 `object_update` 로 고친다.

<!--@ objects -->
## 객체 하나씩

- **만들기 전에 `object_resolve(type_slug, label)`.** 이미 있는 것을 다른 표기로 또
  만들면 같은 것이 둘이 되고, 둘은 반드시 갈린다. `none` 일 때만 만든다.
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

### 묶음 — `bundle_import(bundle, apply=false)`

원천 데이터를 정제해 넣을 때는 **`pipeline/` 의 절차**를 따른다(실행 폴더 · 검증 · 미리 보기 ·
적용). 이 도구는 그 묶음을 **한 번에 미리 보는** 자리다 — 정의를 먼저 적용하지 않아도 그 정의로
객체 · 관계를 맞춰 본다. `apply=true` 는 전부 아니면 무. **사람이 미리 보기를 확인하기 전에는
`apply=true` 로 부르지 않는다.**

<!--@ relations -->
## 객체 잇기

- `relation_add(type_slug, object_id, relation, dst_object_id, evidence_note)` —
  `relation` 은 `ontology_schema` 의 `relation_types[].slug`. 출발 타입·도착 타입이
  정의와 맞아야 한다.
- **양 끝은 id 다.** 이름밖에 없으면 `object_resolve` 로 먼저 푼다 — `candidates` 가
  오면 잇지 말고 사람에게 묻는다. 틀리게 이은 선은 지워도 「왜 그렇게 이었는지」 를
  본 사람의 기억에 남고, 그 기억이 다음 판단을 흔든다.
- **근거를 적는다.** 어느 문서·어느 자료에서 이 연결이 나왔는지. 근거 없는 연결은
  시간이 지나면 아무도 못 믿고, 확인하려면 처음부터 다시 조사해야 한다.
- 여러 줄이면 `relations_import(type_slug, rows, apply)` — 행은
  `{"src": "<key 또는 label>", "relation": "<slug>", "dst": "...", "evidence_note": "..."}`.
  이미 이어진 것은 `unchanged` 라 두 번 올려도 두 겹이 안 된다.

<!--@ sparql -->
## 질의어로 묻기 (SPARQL)

**먼저 `objects_summary` · `objects_list` 로 되는지 본다.** 한 타입 안의 세기 · 거르기는 그쪽이
빠르고 답도 화면과 같다. 질의어는 **그것으로 안 되는 물음**에 쓴다:

- 타입을 둘 이상 건너뛰어 잇는 것 — 「이 프로젝트의 과제들에 달린 모델의 시험 진행」
- 적어 두지 않은 방향 — 「이 과제를 가리키는 모델」(역관계)
- 상속으로 묶어 보기 — 「제품인 것 전부」(개발모델 · 양산모델을 한 번에)

절차는 둘뿐이다:

1. `rdf_schema` — 클래스 · 속성 · 관계 이름을 **확인한다**(추측하지 않는다).
   `sp:<타입slug>` · `sp:<타입>.<속성키>` · `sp:rel.<관계slug>`, 역관계는 `.inverse`.
2. `rdf_query(query, types=[…], infer=False, limit=200)`.

지켜야 하는 것:

- **범위를 좁힌다**(`types`). 안 주면 설치 전체를 올린다 — 큰 설치에서는 느리다.
- `infer=True` 는 상속 · 역관계 · 이행이 **필요할 때만**. 느리고, 큰 범위는 서버가 거절한다.
  역관계만 필요하면 추론 없이 방향을 뒤집어 쓰는 편이 빠르다(`?t ^sp:plm_model.task ?m`).
- 답의 `truncated` 가 참이면 **잘린 것이다.** 「전부 이것뿐」 으로 말하지 않는다.
- 쓰기(INSERT · DELETE)와 바깥 호출(SERVICE)은 막혀 있다. 고치는 것은 `object_update` 등으로.
- 사람에게 옮길 때는 IRI 가 아니라 **이름**으로 말한다 — `rdfs:label` 을 함께 SELECT 한다.

```sparql
PREFIX sp: <…/ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?project ?task (COUNT(?m) AS ?models) WHERE {
  ?m a sp:plm_model ; sp:plm_model.task ?t .
  ?t rdfs:label ?task ; sp:plm_task.project ?p .
  ?p rdfs:label ?project .
} GROUP BY ?project ?task ORDER BY DESC(?models)
```

<!--@ modeling -->
## 모델링 규약 v0 — 무엇을 무엇으로 만드나

원천(엑셀 · PPT · PDF · Word)을 정제해 넣기 전에 읽는다. 절차(실행 폴더 · 검증 · 미리 보기 ·
적용)는 `pipeline/AGENTS.md`, 이 절은 **판단의 기준**이다. **판단이 서지 않으면 만들지 말고
`unresolved.json` 에 적는다** — 틀린 정의는 그 아래 쌓인 데이터를 전부 흔든다.

### 0. 질문에서 시작한다

그룹이 실제로 받는 질문(`pipeline/templates/파일럿-그룹-정리.md` 2장)에 답하는 데 필요한 것만
만든다. 질문에 안 쓰이는 칸은 만들지 않는다 — 안 쓰는 칸은 비어 가고, 빈 칸은 품질 화면을 채운다.
타입 · 속성 · 관계를 하나 더할 때마다 **「이것이 몇 번 질문에 답하나」** 를 `bundle.json` 의
`notes` 에 적는다.

### 1. 코어를 먼저 쓰고, 그룹은 확장만 한다

코어는 **모든 플랫폼이 함께 쓰는 정의**다. 복사본끼리 잇고 합치는 자리가 여기다. 둘로 나뉜다.

**PLM 기준정보** — `pipeline/core/plm-core.json`. **허브가 PLM 에서 받아 쌍둥이에 내려준다.**
쌍둥이에서는 **받기만 한다** — 이 타입에 객체를 만들거나 값을 고치거나 속성을 더하지 않는다.
허브의 사내 확장 정의가 고를 값(과제상태 · 지역 등)을 채운다. 받는 플랫폼에서 이 타입은
**허브 관리**(`managed_by: hub`)라 만들기 · 고치기 · 파일로 넣기 · 정의 고치기가 막힌다(409 —
「허브가 관리하는」). 틀린 것은 허브에서 고친 뒤 받는다. 이 설치의 관계로 **가리키는 것**은 된다.

| 타입 | 무엇 | 식별자 |
| --- | --- | --- |
| `plm_project` 프로젝트 | PLM 프로젝트 — 과제를 여럿 가진다 | 프로젝트명(코드가 있으면 코드) |
| `plm_task` 과제 | PLM 과제 — 개발모델을 여럿 가진다 | 과제코드 |
| `plm_model` 개발모델 | PLM 개발모델 — 이름을 해석한 칸(기본 모델코드 · 지역 · 모델 리비전 · 사업자) | 개발모델명 원문 |
| `plm_test_site` 시험 주체 | 지역에 고정된 시험 실행 주체 | 코드 |

관계: `plm_derived_from` 원 모델(파생 → 기본 모델, PLM 원천이 채운다).

**업무 공통** — `pipeline/core/work-core.json`. 그룹의 일이 PLM 과제에 붙는 자리.

| 타입 | 무엇 | 식별자 |
| --- | --- | --- |
| `work` 업무 | 기간 없이 반복되는 일 — 그룹이 「하는 일」 | 정규화한 이름 |
| `document` 문서 | 보고서 · 회의록 · 발표자료 · 기준서 한 건 | 문서번호, 없으면 `doc-<sha256 앞 12자>` |
| `person` 사람 | 업무 · 문서에 나오는 사람(계정이 없어도) | 사번 |
| `organization` 기관 | 바깥 회사 · 기관(고객 · 협력사 · 공급사) | 정규화한 이름 |
| `equipment` 설비 · 장비 | 물리적 설비 · 계측기 | 자산번호 |
| `software` 소프트웨어 | 업무에 쓰는 소프트웨어 · 시스템 | 정규화한 이름 |
| `topic` 기술 분야 | 과제 · 문서 · 설비를 가로지르는 분류(나무) | 정규화한 이름 |
| `dept` 부서 | 조직도(원 표를 비춘다) | — |

관계: `part_of` 상위(같은 타입끼리 나무) · `dept_in_charge` 담당 부서 · `responsible` 담당자 ·
`participates` 참여 과제 · `produces` 산출 문서 · `evidence` **근거 문서**(어느 타입에서나) · `uses`
사용 · `involves_org` 관계 기관 · `affiliated` 소속 · `about` 기술 분야. 과제 쪽 끝은 `plm_task` 다.

- **기간과 목표가 있는 일은 먼저 PLM 과제인지 본다.** 그렇다면 새 「과제」 타입을 만들지 않는다 —
  그룹의 일(해석 의뢰 · 시험 한 건)은 그룹 타입으로 만들고 **참조 칸으로 `plm_task` · `plm_model` 을
  가리킨다.** 「해석 보고서」 는 문서 + 문서 종류(보고서) + `produces`.
- PLM 식별자(과제코드 · 개발모델명)는 **원문 그대로** 적는다. 줄여 쓴 것은 허브의 것과 대조해 맞춘
  뒤에만 잇는다 — 조사(`source_profile` 의 코어 대조)가 비율을 보여 주고, 표를 옮길 때는 그 참조 칸에
  `match` 를 붙인다(그대로 · 대소문자만 다름 · 앞부분이 하나뿐인 것만 잇고, 못 맞춘 것은 비워 넣고
  보고서에 남긴다 — `pipeline_guide("table")`).
- 그룹 전용 타입 slug 는 **`<그룹코드>_`** 로 시작한다(`cae_load_case`). 코어의 slug · 속성 키 ·
  고를 값의 뜻은 바꾸지 않는다.
- **업무 공통** 타입에 그룹이 속성을 **더하는** 것은 된다. 그 속성 키도 `<그룹코드>_` 로 시작한다.
  **PLM 기준정보 타입에는 더하지 않는다** — 그룹이 적을 것은 그룹 타입에.

### 2. 무엇을 무엇으로 — 판단 표

| 원천에서 보이는 것 | 만드는 것 |
| --- | --- |
| 그것에 대해 적을 것이 있다(담당자 · 위치 · 버전), 여러 곳에서 가리킨다 | **타입** |
| 몇 가지로 정해진 말이고 그것에 대해 적을 것이 없다(단계 · 종류 · 등급) | **고를 값**(`enum`) — 새 값이 오면 정의를 고친다. 그게 맞는 비용이다 |
| 정해지지 않은 말 | 글자(`text`) · 긴 글(`text_long`) |
| 다른 타입의 것을 가리킨다 — **관계**다. 상대가 늘 **하나**이고 연결에 근거 · 수치가 안 붙으면 | **칸에 저장한다** — 참조 칸(`object_ref`), 역방향 이름(`inverse_label`)을 적는다. 개발모델의 과제, 소프트웨어의 개발사 |
| 다른 타입의 것을 가리키는데 여럿과 잇거나, 연결에 뜻 · 근거 · 수치가 붙는다 | **줄로 저장한다** — 관계 종류(`relation_types`). 참여 · 근거 문서 · BOM(수량) |
| 같은 종류의 값을 여럿 갖고 값마다 적을 것이 없다 | **여러 값**(`multi: true`) — 기관 종류 |
| 숫자 · 날짜 | `number`(단위 `unit`) · `date` — 글자로 넣으면 범위 조건 · 통계가 안 된다 |
| 계산으로 나오는 값(합계 · 경과일 · 건수) | **만들지 않는다** — 원천이 바뀌면 틀린다. 통계 · 롤업이 센다 |

**참조 칸과 관계 종류는 둘 다 관계다 — 저장 자리만 다르다.** 그래프 · 관련 객체 · 트리 · 스키마
(`reference_edges`)는 둘을 한 목록으로 보이고, 조건 · 통계에서는 `ref.<칸>` · `out.<관계>` 로 같은
「이어진 칸」 이다. **같은 사실을 두 곳에 두지 않는다** — 칸에도 줄에도 두면 어긋나고, 조건
고르개에 같은 이름(「개발사 › 국가」)이 두 번 선다.

### 3. 식별자(`key`) — 다시 돌려도 같은 값

식별자가 흔들리면 다시 넣을 때 **같은 것이 둘이 된다.** 순서대로:

1. **원천이 쓰는 번호** — 과제번호 · 문서번호 · 자산번호 · 사번. 앞뒤 공백을 떼고 대문자로 통일.
2. 없으면 **정규화한 이름** — 소문자, 공백 · 특수문자는 `-`, 회사 접미사(주식회사 · (주) · Inc. ·
   Ltd. · Co.)는 뗀다. `ANSYS, Inc.` → `ansys`.
3. 이름이 겹칠 수 있으면 구분자를 붙인다 — `<이름>-<부서|연도|위치>`.
4. 문서는 번호가 없으면 `doc-<원본 파일 내용 sha256 앞 12자>`. **파일 이름은 쓰지 않는다**(바뀐다).
   고친 판은 새 문서다 — 이전 판의 key 를 `_note` 에 적는다.

**쓰지 않는 것:** 행 번호 · 가져온 날짜 · 난수(uuid) · 시트 이름.

### 4. 이름 · slug

- `label` 은 사람이 부르는 이름 그대로(한글 가능). 약칭 · 다른 표기는 `aliases`(`;` 로).
- 타입 slug · 속성 key 는 **영어 소문자 snake_case, 단수**(`project`, `start_on`). 날짜는 `_on`,
  일시는 `_at`, 원문 그대로 옮긴 글자는 `_text`.
- 고를 값은 사람이 읽는 말(`진행`, `완료`) — 원천이 코드값(`P`, `C`)이면 대응을 속성 `help` 에.

### 5. 원천별 절차

#### 엑셀 · CSV — 구조가 있다

- 시트마다 **「한 행이 무엇인가」** 부터 정한다. 한 행에 여러 타입이 섞여 있으면(과제 + 담당자
  이름 + 고객사) 나눈다 — 과제 행, 사람 행, 기관 행, 그리고 담당자 · 관계 기관 관계.
- **열 → 속성 대응을 표로** `bundle.json` 의 `notes` 에 남긴다. 병합 셀 · 소계 행 · 빈 행은 버리고
  그 사실을 `notes` 에.
- 값 정리(날짜 형식 · 단위 · 공백 · 코드값)는 **규칙으로** — 스크립트로 두고 매번 같은 결과를 낸다.
  AI 가 행마다 판단하지 않는다. 판단이 필요한 행만 `unresolved.json` 으로.
- `_source`: `{"file", "sheet", "row"}`. 규칙으로 옮긴 값의 `_confidence` 는 1.

#### PPT · PDF · Word — 구조가 없다

- **문서 자체를 `document` 객체로 넣는다.** 뽑아낸 사실(과제 · 사람 · 설비 · 기관)은 그 문서와
  **`evidence`(근거 문서)** 로 잇는다 — 사실이 어디서 왔는지가 플랫폼 안에 남는다.
- 먼저 텍스트로 바꾼다. PDF 는 AI 가 바로 읽는다. PPT · Word 는 마크다운으로 바꾸되
  **슬라이드 · 쪽 번호를 보존**한다(`markitdown` 등).
- **표지 · 머리글 · 결재란을 먼저 본다** — 과제번호 · 날짜 · 작성자가 가장 믿을 만한 자리다.
- `_source`: `{"file", "page" | "slide", "quote"}` — **`quote` 는 원문 그대로 한두 문장.** 검토하는
  사람이 원문을 열지 않아도 된다.
- `_confidence`: 원문에 그대로 적힌 것 0.9 이상 · 표 · 그림에서 읽은 것 0.7~0.9 · 문맥으로 추론한 것
  0.7 미만 — **0.7 미만은 넣지 않고 `unresolved.json` 으로.**
- 한 문서만의 결론 · 수치는 속성으로 옮기지 않는다(문서마다 다르다). 옮기는 것은 **여러 문서에
  걸쳐 묻는 것**(과제번호 · 설비 · 기술 분야 · 기관)뿐 — 나머지는 문서의 `description` 에 한 문단.

### 6. 넣기 전에 이미 있는지 찾는다

- `objects_list(q=...)`(별칭까지 찾는다)로 먼저 본다. 있으면 **그 key 를 쓴다** — 새로 만들지 않는다.
- 같은 것의 다른 표기는 `aliases` 에 모으고, 그룹의 **동의어 사전**(작업 폴더)에도 적어 다음 실행이
  쓴다.
- 비슷한데 같은지 모르겠으면 `unresolved.json`.

### 7. 부서 · 시간 · 상태

- `workspace_slug` 는 **그 데이터를 책임지는 부서**. 모르면 `unresolved.json` — 전역으로 넣지 않는다.
- 기간이 있는 것은 `start_on` · `end_on`. 「그 해의 것」 으로 묻는 타입이면 `temporal_kind` 를
  온톨로지 담당이 정한다.
- 원천에서 사라진 것은 지우지 않는다 — `status: deprecated`.

### 8. 하지 않는 것

- 두 걸음 연결을 전제로 설계하지 않는다 — 조건 · 통계는 한 걸음까지다. 두 걸음 물음이 자주 나오면
  칸이 하나 빠진 것이다.
- 원문 전체 · 첨부 내용을 속성에 넣지 않는다.
- 개인정보 · 계약 금액 · 고객 기밀은 템플릿 6장(민감한 것)을 먼저 본다.
- 정의를 지우거나 slug 를 바꾸지 않는다 — 가져오기는 더하고 고치기만 한다.

