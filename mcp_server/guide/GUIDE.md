<!-- StandardPlatform MCP 사용 가이드 — **서버가 쥔다.**

로컬 스킬(~/.claude/skills)에 본문을 두면 사람마다 복사 시점이 달라 낡는다.
그래서 본문은 여기 두고 로컬엔 짧은 스텁만 남긴다. `get_guide(topic)` 이 여기서
읽어 준다. 이 파일만 고치면 모두에게 즉시 반영된다(서버 재시작도 필요 없다).

주제 구분자: `<!--@ 주제이름 -->`. 순서는 상관없다. -->
GUIDE_VERSION: 2026-10-04c

<!--@ overview -->
## 무엇을 하려는가 → 어떤 도구

| 하려는 일 | 도구 | 주의 |
|---|---|---|
| 지금 무엇이 정의돼 있나 | `ontology_schema` | **다른 도구보다 먼저** |
| 내가 누구고 어느 부서인가 | `whoami` | `object_create` 의 `workspace_slug` 는 여기서 — 짐작하지 않는다 |
| **어느 타입에 있는지 모른다** | `search(q)` | `types[]` 가 타입별 건수. 타입을 알면 `objects_list` · `object_resolve` |
| 원천(엑셀 · PPT · PDF · Word)을 정제해 온톨로지로 만들기 전에 | `get_guide(topic="modeling")` | **무엇을 타입 · 속성 · 관계로 만드나** — 판단이 서지 않으면 만들지 않는다 |
| 타입·속성·관계 종류를 만들거나 고치기 | `ontology_import(apply=false)` → 사람 확인 → `apply=true` | 미리 보기를 건너뛰지 않는다 |
| **표(엑셀 · CSV)로 기록 타입을 만들고 축에 잇기** | `table_infer(rows)` → 사람 확인 → `table_build` → `ontology_import` → `objects_import` | **시스템 관리자만.** 참조 후보를 스스로 확정하지 않는다 — `one` · `many` · `none` 과 못 찾은 견본을 보인다 |
| 정의 지우기(묶음 · 타입 · 인터페이스 · 관계 종류 · 속성) | `ontology_delete(apply=false)` → 사람 확인 → `apply=true` | **시스템 관리자만.** `blocking` 은 우회하지 않는다 — 먼저 할 일을 사람에게 |
| 속성 종류 변경 · 고를 값 이름 변경 · 코드표 승격 | `ontology_retype` · `ontology_rename_option` · `ontology_promote` — 모두 `apply=false` 먼저 | 저장값도 함께 바뀐다. 대체 값은 **사람이 정한 것만** |
| 정의를 그때로 되돌리기 | `ontology_restore()` → 스냅샷 고르기 → `ontology_restore(id)` → 사람 확인 → `apply=true` | 그 뒤에 새로 만든 정의는 안 지운다 |
| **이름으로 무언가를 가리킨다** | `object_resolve(type_slug, name)` | `candidates` 면 **고르지 말고 사람에게 묻는다** |
| 이름 **여럿**을 한 번에 | `objects_resolve_many(type_slug, names)` | 여러 줄을 넣기 전에 한 번. 왕복이 줄 수만큼 늘지 않는다 |
| 객체 찾기 | `objects_list(type_slug, q=, properties=, conditions=)` | 화면과 같은 거르기. **0건이면 `diagnosis` 를 읽는다** |
| 여러 타입을 한 개념으로 — 「설비 전부」 | `objects_list(<인터페이스 slug>)` · `objects_summary(<인터페이스>, group_by="type")` | **읽기만.** 줄의 `type_slug` 가 실제 타입 — 상세 · 고치기는 그것으로 |
| 몇 건인가 — 부서별·등급별·개발사 국가별 | `objects_summary(type_slug, group_by=, conditions=)` | **목록을 받아 직접 세지 않는다.** 「(비어 있음)」·「그 밖에」·`overlap` 을 함께 말한다 |
| **비율 · 추이 · 코호트** — 「판매월별 누적 인입률」 「생산월 x 공장별 건수」 | `metric_list` → `metric_query(slug, shape=)` | **미리 세어 둔 값**이다 — `computed_at` · `stale` · `overlap` 을 함께 말한다. 없으면 `metric_define(apply=false)` 로 제안 |
| **세어 둔 수에서 추론** — 「B10 수명」 「전작보다 나빠졌나」 「관리도 신호」 「언제 바뀌었나」 「몰려 있나」 | `metric_list` 의 `analyses` → `metric_analyze(slug, recipe, options=)` | **셀을 받아 직접 계산하지 않는다.** `caveats` 를 그대로 전하고, `unreachable` 인 B수명은 값이 없다 — `get_guide(topic="metrics")` 의 「분석」 |
| 다른 타입의 칸으로 거르거나 세기(「미국 기업이 만든 툴」) | `object_fields` → 주소를 `conditions`·`group_by` 에 | 한 걸음까지. 주소를 추측하지 않는다 |
| 객체 하나 자세히(관련 객체까지) | `object_get` | — |
| 언제 누가 무엇을 바꿨나 — 이 객체 | `object_history` | 되돌리기는 `object_restore(entry_id)` — **시점은 사람이 정한다** |
| 어제 무슨 일이 있었나 — 전체 | `audit_recent` | **부서 관리자 이상.** 시간 · 사람으로는 못 거른다 — 최근 것부터 받아 본다 |
| 계층(트리)을 한 단계씩 | `object_tree(parent=)` | 깊이 전부는 `rdf_query` 의 `+` 경로 |
| **이것과 이어진 것들**(한 걸음 너머) | `graph_neighbors(object_id, depth=, relations=, types=)` | 화면의 지식 그래프와 같은 길. **`truncated` 면 잘린 것** — 좁혀서 다시 |
| 어느 타입에서 어디로 갈 수 있나 | `graph_overview` | 관계 이름을 짐작하지 않는다 — 질의를 쓰기 전에 |
| 지우기·합치기 전에 무엇이 걸렸나 | `object_references` | 남의 부서 것은 수만 |
| 어셈블리 총 무게처럼 「아래 전부」 의 합 | `object_rollup` | `missing` 을 함께 말한다 |
| 무엇이 나빠지고 있나(필수값·고아·끊긴 참조·중복) | `quality_report` | 볼 수 있는 것만 |
| 객체 하나 만들기 | `object_create` | 정의에 없는 속성 키는 거절된다 |
| 객체 고치기 | `object_update` | **보낸 키만** 병합. 비우려면 `null`. 식별자 · 상태 · 유효 연도 · 별칭도 |
| **사진 · 파일 붙이기** | `attachment_upload_prepare` → 셸에서 `curl` | **파일을 읽거나 base64 로 옮기지 않는다** — 바이트는 셸이 직접 올린다. 셸이 없으면 사람에게 화면에서 |
| 잘못 붙인 첨부 떼기 | `attachment_remove(attachment_id)` | 사람이 떼라고 한 것만 |
| 객체 지우기(하나 · 여럿) | `objects_delete(apply=false)` → 사람 확인 → `apply=true` | 가리키는 것이 있으면 그 줄은 거절(`block`). `detach` 는 사람이 고른 뒤에만. 그만 쓰는 것이면 `status="deprecated"` |
| 같은 것이 둘 — 합치기 | `object_merge(apply=false)` → 사람 확인 → `apply=true` | **되돌리기 없음.** 어느 쪽이 남을지는 사람이 정한다 |
| 여러 객체의 **한 칸**을 같은 값으로 | `bulk_edit(apply=false)` → `apply=true` | `batch_id` 를 사용자에게 알린다 — `bulk_edit_undo` 가 통째로 되돌린다 |
| 정제 도구가 만든 묶음(정의 · 객체 · 관계)이 어떻게 들어갈지 | `bundle_import(bundle)` → 사람 확인 → `job_apply` | **넣는 것은 사람이 미리 보기를 본 뒤에만.** 원천을 곧바로 넣지 않는다 — `pipeline/AGENTS.md` |
| 넣은 묶음이 틀렸다 — 통째로 되돌리기 | `bundle_runs` → 번호 → `bundle_undo(run_id)` → 사람 확인 → `apply=True` | **스스로 되돌리지 않는다.** 건너뛴 줄의 이유도 함께 보여 준다 |
| 여러 행 한 번에(upsert) | `objects_import` → 사람 확인 → `job_apply(job_id)` | **작업이 된다.** 같은 `key` 면 고침. 한 행이라도 오류면 전부 안 넣음. **사람이 화면에서 고친 칸은 비켜 간다** — 계획의 그 말을 사용자에게 보여 준다 |
| 객체 둘 잇기 | `relation_add` | **근거(evidence_note)를 적는다** |
| 잘못 이은 관계 | `relation_update`(근거 · 속성) · `relation_remove`(끊기) | 양끝 · 종류는 못 바꾼다 — 끊고 새로 잇는다. 확실하지 않으면 끊지 말고 사람에게 |
| 관계 여러 줄 한 번에 | `relations_import(mode=)` → 사람 확인 → `job_apply` | 이미 이어진 건 그대로. `replace`·`replace_type` 은 **파일에 없는 선을 끊는다** |
| 기계가 붙인 별칭 검수 | `aliases_pending` → 사람 확인 → `aliases_review` | **스스로 승인하지 않는다** |
| 뒤에서 도는 작업이 어디까지 됐나 | `job_status(job_id)` · `jobs_list()` | 워커가 없으면 영영 대기 — `jobs_list` 의 `workers` 로 안다 |
| 바깥 시스템(OData·REST·파일)에서 읽어 채우기 | `datasources_list` → `datasource_sync(apply=false)` → `apply=true` | 정의는 화면에서. 오류 행이 있으면 아무것도 안 넣음 |
| **이 설치에만 있는 기능**(디지털 트윈 역량 등) | `extensions_schema` → `extension_call` | 경로를 짐작하지 않는다. 쓰기는 그 확장의 범위를 가진 토큰만 |
| 여러 타입을 건너뛰어 잇는 물음 · 역관계로 거슬러 세기 | `rdf_schema` → `rdf_query` | **먼저 `objects_summary` 로 되는 물음인지 본다.** 질의어는 그것으로 안 되는 자리에 |

## 기본 습관

- **스키마부터 읽는다.** 타입 slug·속성 키·관계 이름을 추측하지 않는다.
- **정의를 바꾸는 일은 두 단계다.** `apply=false` 로 계획과 경고를 받아 사람에게
  보여 주고, 판단을 받은 뒤에 `apply=true`. 에이전트의 실수는 기계 속도로 반영되고,
  온톨로지는 데이터의 모양이라 그 아래 쌓인 것이 전부 흔들린다.
- **일괄 도구도 같다.** `objects_import`·`relations_import`·`bundle_import` 는 **작업**이 되어
  계획만 세운다 — 적용은 사람의 판단을 받은 뒤 `job_apply` 로만.
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

`ontology_schema` 가 돌려주는 것: `groups`(묶음) · `interfaces`(인터페이스 — 여러 타입이
따르는 공통 모양, 각각 공통 `properties` 와 `implementers`) · `types`(타입, 각각 `properties`
와 `key_policy` · `interface_slugs`) · `relation_types` · `data_types`(속성이 받는 값의 종류).
타입 속성에 `interface_slug` 가 붙어 있으면 **공통 속성**이다 — 모양은 그 인터페이스에서 바꾼다.

`ontology_import(schema, apply)` 의 `schema` 는 같은 모양이다. 묶음은 `parent_slug` 로
**두 단계**까지 세운다 — 타입이 백 개가 되면 묶음이 평평하게 늘어서고, 그때 「어디에 속한
것인가」 를 화면이 말해 주지 못한다. **세 단계는 거절된다**(상위의 상위는 만들지 않는다).

묶음은 **그래프의 색**도 쥔다(`color`, `#rrggbb`). 그래프는 타입이 아니라 묶음으로 칠한다 —
색이 열둘이라 타입마다 주면 열셋째부터 회색이 된다. 비우면 순서대로 받고, 상위 묶음이 있으면
그 색의 농도만 달라진다. 보통은 비워 둔다.

```json
{
  "groups": [{"slug": "sim", "label": "시뮬레이션", "color": "#8b5cf6"},
             {"slug": "sim_run", "label": "해석 실행", "parent_slug": "sim"}],
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
- **인터페이스** — 여러 타입이 같은 개념이면(시험장비 · 계측기 · 생산설비는 다 「설비」)
  공통 속성을 인터페이스에 한 번 적고 타입이 `interface_slugs` 로 **구현**한다:

  ```json
  {"interfaces": [{"slug": "equip", "label": "설비",
                   "properties": [{"key": "maker", "label": "제조사", "data_type": "text"}]}],
   "types": [{"slug": "tester", "label": "시험장비", "interface_slugs": ["equip"]}]}
  ```

  구현한 타입은 **같은 키 · 같은 모양**의 속성을 갖는다 — 없으면 생기고, 같은 모양이면
  그대로 쓰이고, **다르면 거절된다**(`errors` 에 무엇이 다른지 적힌다 — 「종류: 인터페이스는
  enum, 이 타입은 text」). 짐작으로 맞추지 말고 사람에게 보여 준다. 공통 속성에는 유일 ·
  기본값 · 역방향 이름 · 파일 종류를 적지 않는다(타입마다 정한다). `parent_slug`(옛 상위
  타입)는 없어졌다 — 보내면 거절된다.
- 공통 속성을 고치면 **구현 타입들의 속성도 함께 바뀐다** — 계획의 `changes` 에 `via` 가 붙은
  줄이 그것이다(파일에 없던 타입이 왜 바뀌는지).
- **참조 대상(`ref_type_slug`)에도 인터페이스**를 적을 수 있다 — 「사용 설비」 칸이 시험장비 ·
  계측기 어느 것이든 가리킨다. 일괄 입력의 이름 풀이는 구현 타입 전부에서 찾고, 같은 식별자가
  두 타입에 있으면 짐작하지 않고 그 줄을 거절한다(id 로 적는다).
- **관계 끝(`src_type_slugs` · `dst_type_slugs`)에 인터페이스**를 적을 수 있다 — 그것을 구현한
  타입이면 된다. 「설비를 교정함」 을 시험장비 · 계측기마다 따로 적지 않는다: 구현 타입이 늘어도
  관계 종류는 그대로다. **구현한 타입이 없는 인터페이스만 적힌 끝은 아무것도 못 잇는다**
  (「아무 타입이나」 가 아니다). 구현을 해제하면 그 끝에서 빠진다는 경고가 `warnings` 에 온다 —
  이미 이은 선은 남는다.
- **더하고 고치기만 한다.** 스키마에 없다고 지우지 않는다. 지우는 것은 `ontology_delete`(아래).
- `apply=false` 응답의 `warnings` 가 **조용히 잃는 것**이다(고를 값을 빼서 저장값이 안 맞게
  되는 것, 종류 변경으로 시각을 버리는 값 등). 반드시 사람에게 보여 준다.
- **속성의 `data_type` 을 바꾸면 저장된 값도 변환된다**(종류 변경, ADR 0007) — 글 · 여러 줄 글 ·
  주소 · 숫자 · 날짜 · 날짜와 시각 · 예/아니오 · 선택 사이. 계획의 `warnings` 에 「저장값 N개 변환」 이,
  변환할 수 없는 값(「12 kg」 → 숫자)이 있으면 `errors` 에 그 값들이 온다. 가져오기에는 대체 값을 적을
  자리가 없다 — **값을 짐작해 고치지 말고** 사람에게 보여 주고, 사람이 정한 대체 값으로
  `ontology_retype(mapping=)` 을 부른다(아래). 화면의 「종류 변경」 도 같은 길이다. 파일 · 관계
  종류의 속성은 종류를 바꾸지 않고, 참조는 글 · 긴 글 · 선택과만 오간다(ADR 0009). 인터페이스의
  공통 속성을 바꾸면 구현 타입 전부의 값이 함께 변환된다.
- `apply=true` 응답의 `snapshot_id` 가 되돌릴 자리다. 되돌리기는 `ontology_restore`(아래) 또는
  화면의 **관리 → 온톨로지 → 가져오기·이력**.

### 종류 변경 · 고를 값 이름 · 승격 — 저장값까지 바꾸는 수정

- `ontology_retype(slug, key, data_type, owner=, mapping=, ref_type_slug=)` — 종류를 바꾸고 저장값을 변환한다.
  계획의 `failures` 가 변환할 수 없는 값(값 · 건수 · 견본)이다. 하나라도 남으면 적용되지 않는다 —
  **값마다 사람에게 대체 값을 묻고** `mapping={"12 kg": "12", "모름": null}` 로 다시 계획을 본다
  (`null` 은 값 삭제). 열쇠는 계획의 `value` 그대로. `owner="interface"` 면 구현 타입 전부가 한 번에.
  - **이미 넣은 기록을 축에 잇기** — 글 · 긴 글 · 선택 → `data_type="object_ref"` + `ref_type_slug`.
    값마다 넣을 때와 같은 이름 풀이로 바꾸고, 이름이 여럿에 맞거나 못 찾은 값이 `failures` 다 — 대체
    값은 상대의 식별자 · 이름 · id(사람이 고른 것만). 참조 → 글은 상대의 식별자(없으면 이름)가 되고,
    다시 참조로 바꾸면 같은 객체로 풀린다. 숫자 · 날짜와는 오가지 않는다.
  - 값이 있는 객체가 **2만 건을 넘으면 작업이 된다**(기록 200만 건) — 도구가 계획 작업을 돌려준다
    (`apply=True` 로 불러도 계획부터). 사용자 확인 뒤 `job_apply(job_id)`. 그 사이 건수가 바뀌면 적용이
    `JOBS-0020` 으로 멈춘다 — 다시 계획을 본다.
- `ontology_rename_option(slug, key, from_value, to_value)` — 고를 값의 이름을 바꾸면서 저장값도
  함께. 정의만 고치면(`ontology_import`) 옛 이름의 값이 거르기에서 조용히 빠진다.
- `ontology_promote(slug, key, new_slug=, new_label=)` — 고를 값을 코드표(참조 타입)로. 값 이전은
  스냅샷이 못 되돌린다.

### 축과 기록 — 타입의 `usage`

타입은 **축**(`axis` — 개발모델 · 과제 · 부서처럼 가리켜지는 쪽)이거나 **기록**(`log` — 시장 서비스 건 ·
시험 결과처럼 가리키는 쪽, 수십만 ~ 수백만 건)이다(ADR 0011). 저장 · 권한 · 값은 같고 기본 동작만 다르다.

- `search` 를 섞어 부르면 기록은 줄로 안 오고 `types[]` 의 건수 · `records` 로만 온다 — 기록에서 찾으려면
  `type_slug` 로 그 타입을 준다.
- `object_get`(축) · `graph_neighbors` 는 나를 가리키는 기록을 줄로 안 싣고 `log_counts`(타입 · 칸 · 수)로
  준다. 기록 자체는 `objects_list(기록 타입, conditions=[칸 = 그 축의 id])` 로 거르고, 세기는
  `objects_summary` 로.
- 기록은 참조 후보로 제안되지 않는다(`table_infer`). 표에서 만든 타입은 기본이 기록이다(`table_build(usage=)`).
- 축인지 기록인지는 사람이 정한다 — 크기로 가르지 않는다. 바꾸는 것은 `ontology_import` 의 `usage`.

### 표에서 기록 타입 — `table_infer(rows)` → `table_build(...)`

시장 서비스 건 · 시험 결과처럼 **축(개발모델 · 과제 · 부서)을 가리키는 기록**을 표에서 만든다.
기록마다 정의를 손으로 짜지 않는다(ADR 0009).

1. `table_infer(rows)` — 견본 행(5,000행까지)을 보낸다. 열마다 `role` · `data_type` · `note`, 그리고
   `ref_candidates`: 값이 그 타입의 객체로 **하나로 풀림(`one`) · 여럿에 맞음(`many`) · 못 찾음
   (`none`)** 의 수와 견본. 넣을 때와 **같은 이름 풀이**(식별자 → 별칭 → 이름)로 셌다.
2. 확실한 열만(하나로 90% 이상 · 3종 이상 · 짧은 숫자 아님) `data_type="object_ref"` ·
   `ref_type_slug` 로 온다. 아니면 글자 그대로이고 `ref_note` 가 까닭을 말한다(짧은 숫자는 우연히
   맞는다 — 판 번호 `01` · `02` 가 공급사 식별자와 65% 맞은 일이 있다).
3. **열 표를 사람에게 보인다** — 어느 열을 무엇에 이을지, 못 찾은 견본, 여럿에 맞는 값. 사람이
   정한 대로 `columns` 를 고친다(참조로 둘 열은 `data_type="object_ref"` · `ref_type_slug`).
   - **여럿에 맞는 값은 넣을 때 거절된다**(같은 이름이 여럿). 원천에서 식별자로 적게 하거나, 그
     수준의 축을 먼저 만든다. 축의 **다른 칸**(예: `base_code`)으로 잇지 않는다 — 넣을 때 다시
     풀 수 없다.
   - 못 찾은 값이 있으면 그 행은 넣을 때 오류다 — 축에 먼저 만들지, 그 칸을 비울지 사람이 정한다.
4. `table_build(slug, label, columns, rows)` → `schema` · `import_rows`. `ontology_import(schema)` 로
   계획을 보이고 `apply=True`, 그다음 `objects_import(slug, import_rows)` → `job_apply`.

기록 타입(`usage="log"`)은 후보로 보이되 제안하지 않는다(`ref_note` 가 말한다). 원 표(부서 · 계정)는
늘 본다.

### 지우기 — `ontology_delete(kind, slug, key=)`

**시스템 관리자만 된다.** `kind` 는 `group` · `type` · `interface` · `relation_type` · `property`
(타입의 속성) · `interface_property`(공통 속성). 기본(`apply=false`)은 계획이다:

- `blocking` — 먼저 할 일. 하나라도 있으면 지금은 못 지운다. **우회하지 않는다.**
  - 살아 있는 객체가 든 타입 · 관계가 맺힌 관계 종류 · 타입이 걸린 묶음 · 무엇이 가리키는
    인터페이스 · 데이터 소스가 넣고 있는 타입은 못 지운다. 그만 쓰려는 것이면
    `ontology_import` 로 `is_active: false` — 자료는 남고 화면에서만 빠진다. 관계 종류는 맺힌
    관계를 끊으면(`relation_remove`) 지울 수 있다.
  - 살아 있는 객체가 타입의 **지운 객체를 가리키면** 막는다 — `quality_report` 의 「지워진 것을
    가리키는 칸」 을 사람에게 보이고 먼저 비운다.
  - 공통 속성은 타입에서 못 지운다 — 인터페이스에서(`interface_property`).
  - 허브가 관리하는 정의는 허브에서 고쳐 받는다.
- `removes` · `keeps` — 함께 사라지는 것과 남는 것. **속성을 지워도 저장값은 남는다**(같은 키로
  정의를 되살리면 돌아온다).
- `purge_deleted` — 타입에 **지운 객체만** 남았으면 그 수. 지운 객체도 기록으로 남아 타입을
  붙들므로, 타입을 지우려면 그것까지 **영구 삭제**해야 한다(ADR 0008) — 그 객체들의 관계 · 별칭 ·
  첨부 행이 함께 사라지고 **되돌릴 수 없다**(감사 기록은 남는다). `removes` 를 사람에게 그대로
  보이고 확인받은 뒤에만 `apply=true, purge_deleted=true`. 영구 삭제는 타입 삭제에서만 된다 —
  객체 하나를 영구 삭제하는 길은 없다.
- `warnings` — 지울 수는 있지만 알아야 할 것(이 타입을 참조 대상으로 적은 칸 등).
- 외부 공개 타입의 속성이면 `core_consumers` 를 사람에게 보이고 통보를 확인받아 `accept_core=true`.

지우기 직전의 정의는 스냅샷으로 남는다(「삭제 직전: …」) — `ontology_restore` 로 되살린다. 속성
정의를 되살리면 남아 있던 저장값도 다시 보인다. **객체는 스냅샷에 없다** — `objects_delete` 로 지운
객체는 이것으로 안 돌아온다.

<!--@ find -->
## 찾기와 살피기

### 타입을 모를 때 — `search(q, type_slug=, limit=)`

「앤시스 관련된 거 뭐 있어」 처럼 **어느 타입에 있는지 모르면** 여기서 시작한다. `types[]` 가
타입별 건수라 어디 있는지가 먼저 보이고, `items[].matched` 가 무엇으로 걸렸는지(이름 · 식별자 ·
별칭)다. 타입을 알고 나면 `objects_list`(조건) 나 `object_resolve`(하나로 정하기)로 간다 —
`search` 는 「어디 있나」 를 묻는 도구지 「어느 것인가」 를 정하는 도구가 아니다.

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

**여러 개를 넣기 전에는 `objects_resolve_many(type_slug, names)` 로 한 번에 묻는다.**
한 줄에 한 번 물으면 이천 줄짜리 원천에 왕복이 이천 번이고, 그러고도 「없는 것」 하나가
묶음 전체를 거절시킨다. `counts` 가 몇 개가 안 풀렸는지 한 줄로 말하니, 사람에게 물을 것만
모아서 한 번에 묻는다.

### 여러 타입을 한 개념으로 — 인터페이스 slug

`objects_list` · `objects_summary` · `object_fields` · `object_resolve` · `objects_resolve_many` ·
`search(type_slug=)` 는 **인터페이스 slug** 도 받는다. 구현 타입 전부를 한 목록으로 묻는다 —
「설비 중 한국산」 이면 시험장비 · 계측기를 따로 묻고 더하지 않는다:

```
objects_list("equip", conditions=[{"field": "country", "op": "eq", "value": "KR"}])
objects_summary("equip", group_by="type")        # 어느 타입이 몇 건
```

- **읽기만이다.** 만들기 · 고치기 · 일괄 입력 · 트리는 타입으로 한다 — 인터페이스로 부르면
  `[OBJECTS-0092]` 로 거절된다. 줄마다 `type_slug` 가 그 객체의 실제 타입이다.
- 조건 · 기준은 **공통 속성**(구현 타입이 같은 키 · 같은 모양으로 가진다)으로 건다. 한 타입에만
  있는 칸은 그 타입으로 묻는다.
- 식별자는 타입마다 따로다 — `object_resolve` 가 두 타입에서 같은 식별자를 찾으면
  `candidates` 다. 후보의 `type_slug` 를 보고 사람에게 묻는다.
- 0건이고 `diagnosis.reason` 이 `no_implementers` 면 그 인터페이스를 **구현한 타입이 아직 없다** —
  객체가 없는 것과 다르다.

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
  타입의 칸은 `object_fields` 의 주소. 쓸 수 있는 전부가 응답의 `group_options`. 걸음은 셋까지
  이어 적을 수 있다(`ref.model.ref.base.series` — 고르개에는 한 걸음만 보이지만 주소는 받는다).
- `metric` 이 `sum`·`avg`·`min`·`max` 면 `metric_field`(숫자 속성)가 필요하다.
- `order="asc"` 는 「가장 낮은 것」 을 찾을 때. **「월별 추이」 는 `grain="month", order="key"`** —
  `grain`(`day`·`week`·`month`·`quarter`·`year`)이 날짜 축을 그 단위로 묶고, `order="key"` 가
  시간순으로 세운다. 날짜 칸의 `buckets[].key` 는 기간의 **시작일**, `range` 가 `{gte, lt}` 다.
- 옮길 때 **빼먹지 않는다**: `total` 은 객체 수, 「(비어 있음)」 도 한 칸, `other_groups`·
  `other_count` 가 0 이 아니면 「그 밖에 N종류 M건」, `overlap` 이 true 면 한 객체가 여러 칸에
  들어 **칸의 합이 total 보다 클 수 있다**.
- 「그 칸이 뭔데」 는 `buckets[].key` 를 조건 값으로 `objects_list` — 기준이 `properties.<키>`
  면 field `<키>`, 다른 타입의 칸이면 그 주소, key 가 null 이면 `empty`. **날짜 칸은 `range` 로** —
  `{field, op: "gte", value: range.gte}` 와 `{op: "lt", value: range.lt}` 둘(`eq` 는 0건).

`object_history(type_slug, object_id)` — 언제·누가·어느 칸을 전→후. 값 기록의
`snapshot` 이 그 시점의 값 전체다. 되돌리기는 `object_restore(entry_id)` — 그 `snapshot` 을
사람에게 보이고 그가 고른 시점으로만.

`object_references(type_slug, object_id)` — 이 객체를 가리키는 속성·관계. 지우거나
합치기 전에 본다. `hidden_*` 는 남의 부서 것이라 수만 온다 — 0 이 아니면 지우지 않는다.

`quality_report(kind=)` — 필수값 빈 객체·관계 없는 객체·지워진 것을 가리키는 칸·이름이
같은 객체. 사용자가 「데이터 정리해 줘」 라고 하면 여기서 시작한다: 찾고, 사람에게
보여 주고, 판단을 받은 뒤 `object_update` 로 고친다.

<!--@ objects -->
## 객체 하나씩

- **`workspace_slug` 는 `whoami` 의 `home_workspace_slug`(또는 `memberships[]`)에서.** 비우면
  전역이 되어 시스템 관리자가 아니면 거절된다. 부서를 짐작해 넣지 않는다.
- **만들기 전에 `object_resolve(type_slug, label)`.** 이미 있는 것을 다른 표기로 또
  만들면 같은 것이 둘이 되고, 둘은 반드시 갈린다. `none` 일 때만 만든다.
- `object_create(type_slug, label, key=, properties=, workspace_slug=)` —
  `workspace_slug` 를 비우면 **전역** 객체라 시스템 관리자만 만들 수 있다.
  `key_policy` 가 `required` 인 타입은 `key` 가 있어야 한다.
- `object_update(type_slug, object_id, properties=)` — **보낸 키만** 병합한다.
  값을 비우려면 그 키에 `null`. 통째로 덮지 않는다.
- 참조 속성(`data_type: object_ref`)에는 상대 객체의 **id** 를 넣는다. id 를 모르면
  `objects_list` 로 먼저 찾는다 — 찾기는 별칭에도 걸린다. 상대는 **그 칸의 대상 타입**이어야
  한다(`ref_type_slug` — 인터페이스면 그것을 구현한 타입). 아니면 `[OBJECTS-0094]` 로 거절된다 —
  대상이 인터페이스면 `objects_list(<인터페이스>)` · `object_resolve(<인터페이스>, 이름)` 로 찾는다.
- **별칭** — 같은 것을 다르게 부르면(「Ansys」 「앤시스」 「ANSYS Inc.」)
  `object_update(aliases=[...])` 로 다른 이름을 붙인다. 그 뒤로 파일·참조·찾기가 그 표기로도
  같은 객체를 찾는다. **같은 것을 새로 만들지 말고 별칭을 붙인다.** 이미 둘이 됐으면
  `object_merge(object_id, into=)` — 미리 보기로 두 객체와 지는 쪽을 가리키는 것을 사람에게 보이고,
  어느 쪽이 남을지 확인받은 뒤 `apply=true`. 지는 쪽 이름이 자동으로 별칭이 된다. 지는 쪽을 가리키는
  기록이 2만 건을 넘으면(인기 모델) 작업으로 돌고 도구가 기다린다 — 돌아온 `status` · `result` 를 전한다.
- **지우기** — `objects_delete(type_slug, ids, mode=)`. 미리 보기가 줄마다 지울지 · 거절할지를
  말한다. 적용은 **지울 수 있는 줄만 지우고 막힌 줄은 남긴다** — 무엇이 남았는지 사람에게 전한다.
  다른 것이 가리키는 객체는 기본(`block`)으로 거절된다 — `object_references` 를 사람에게
  보이고, 그가 「참조를 비우고 지운다」 고 정하면 `mode="detach"`(가리키는 기록이 2만 건 넘는 객체는 그
  줄이 작업으로 돌고 `rows[].job` 에 결과가 온다). 그만 쓰는 것이면 지우지 말고
  `object_update(status="deprecated")`. 지운 행도 기록으로 남는다.
- `object_get` 은 `object` · `properties_schema` · `related`(양방향) 를 함께 준다 —
  화면의 상세와 같은 것이다.
- `kind_class` 가 `system` 인 타입(부서·계정 등)은 **행이 없다** — 다른 표를 비춘다.
  `objects_list` 로 읽고 `object_ref`·관계의 상대로 쓸 수는 있지만, 만들거나 고치지는
  못한다(그 표의 화면에서 한다). 파일·`objects_import` 에서는 그 표의 식별자(부서면
  slug, 계정이면 로그인 아이디)로 적는다.

### 사진 · 파일 붙이기 — `attachment_upload_prepare` → 셸의 `curl`

**파일의 바이트는 모델(당신)을 거치지 않는다.** 파일을 열어 읽거나, base64 로 바꿔 도구 인자에
넣거나, 내용을 속성에 적지 않는다 — 1MB 가 수십만 토큰이고 그만큼 일을 못 한다. 받기도 같다:
첨부는 `object_get` 의 `attachments[]` 에 **이름 · 크기 · `is_image` · 가로세로만** 오고, 사진을
보려면 사람이 화면에서 본다.

1. 붙일 객체의 id 를 찾는다(`object_resolve`), 붙일 칸을 고른다 — `object_fields` · `ontology_schema`
   에서 `data_type: file` 인 칸. 칸의 `accept: image` 면 **서버가 이미지로 읽은 것(PNG · JPEG · GIF ·
   WebP)만** 붙는다 — PDF · 엑셀은 거절된다. 칸이 없으면 `field` 를 비운다(「그 밖의 첨부」).
2. `attachment_upload_prepare(type_slug, object_id, local_path, field=)` — `local_path` 는 **당신의
   셸에서 보이는 경로**다. 서버는 그 파일을 열지 않고 명령에 넣기만 한다.
3. 돌아온 `curl` 을 셸에서 **그대로 한 번** 실행한다(5분 안). 응답은 붙은 첨부(`id` · `is_image`)다.
   `{"error": ...}` 면 그 말을 사용자에게 전한다(이미지만 받는 칸 · 권한 · 표 만료).
4. 여러 장이면 장마다 1 ~ 3. 표는 한 장에 하나다.

- 셸이 없는 클라이언트(예: Claude Desktop)는 **올리지 못한다** — 사람에게 화면의 그 칸에서
  올려 달라고 한다. 다른 길(base64 · 파일 내용을 글로)을 찾지 않는다.
- Windows PowerShell 에서는 `curl` 이 다른 명령이다 — `curl.exe` 로 부른다.
- curl 이 인증서 오류(`SSL certificate problem`)를 내면 **`-k` 로 넘기지 않는다** — 셸이 회사
  인증서를 모르는 것이다. 그 말을 사람에게 전한다(화면에서 업로드하거나 인증서를 설치한다).
- 붙이고 뗀 일은 객체의 이력에 남는다(`object.attachment.add` · `.remove`). 잘못 붙였으면
  사람이 확인한 뒤 `attachment_remove(attachment_id)`.

### 여러 객체의 한 칸 — `bulk_edit(type_slug, ids, field, value, apply)`

「등급 A 인 것 전부 B 로」 는 `objects_list` 로 id 를 모아 여기로. `field` 는 `status` ·
`description` · `workspace` · `properties.<칸>`. 기본은 계획이다 — 몇 건이 바뀌고 몇 건은
**왜 안 되나**(남의 부서 것)가 행마다 온다. 적용하면 `batch_id` 가 오고, **그것을 사용자에게
알려 준다** — `bulk_edit_undo(type_slug, batch_id, apply)` 가 그 묶음을 통째로 되돌린다.
그 뒤에 따로 고쳐진 객체는 되돌리지 않고 이유를 적는다.

<!--@ bulk -->
## 여러 행 한 번에 — 작업이 된다

`objects_import(type_slug, rows, workspace_slug=)`:

```json
[{"key": "T-001", "label": "ANSYS Fluent", "vendor": "Ansys", "license": "상용"},
 {"key": "T-002", "label": "OpenFOAM", "vendor": null}]
```

**돌아오는 것은 계획이 아니라 작업이다.** 워커가 뒤에서 계획을 세우고, 작은 것은 그 호출 안에
끝난다:

```json
{"job_id": "…", "status": "done",
 "result": {"applied": false, "rows": [...], "counts": {"create": 2, ...}},
 "next": "계획이다 — 아직 아무것도 안 들어갔다. 사용자에게 보여 주고 판단을 받은 뒤 job_apply(job_id) 로 적용한다."}
```

- `status` 가 `queued`/`running` 이면 아직 도는 중 — `job_status(job_id)` 로 다시 묻는다.
  **끝났다고 지어내지 않는다.**
- `result.rows` 는 행마다 `create` / `update` / `unchanged` / `error`. **한 행이라도 `error`
  면 적용 작업 자체가 안 만들어진다.** 오류를 고쳐 다시 보낸다.
- 적용은 **사용자의 판단을 받은 뒤** `job_apply(job_id)`. 같은 파일 · 같은 지문으로 넣는다 —
  미리 본 뒤 누가 그 사이에 바꿨으면 서버가 거절한다(「미리 본 것과 달라졌습니다」). 그때는
  `objects_import` 부터 다시.
- 같은 `key` 가 이미 있으면 **고친다**(upsert). 없는 키는 안 건드린다. `null` 이 비움이다.
- 참조 속성은 상대의 **식별자(key)**, **별칭**, 없으면 **이름(label)** 순으로 풀린다.
  겹치면 거절된다 — 그때는 id 로 적는다.
- `aliases` 열에 `;` 로 여럿, 또는 `{"value": "…", "source": "…", "note": "…"}` 로도 적는다 —
  어디서 온 표기인지가 남아야 나중에 사람이 검수할 수 있다.
- `aliases_mode` 가 별칭을 **더할지 파일대로 맞출지**다. 기본 `add` — 파일에 없는 별칭은
  그대로 둔다. `replace` 는 **파일에 없는 별칭을 지운다**: 원천이 그 객체의 별칭 전부를
  가지고 있을 때만 쓴다. 한 열에서 뽑아 온 조각을 `replace` 로 보내면 사람이 손으로 붙인
  표기가 사라진다.
- 키를 바꿨으면 `renamed_from`(직전 키) · `previous_keys`(이력) 를 함께 적는다 — 받아 가는
  쪽이 「없어진 것」 과 「이름이 바뀐 것」 을 구별하는 자리다.
- **사람이 화면에서 고친 칸은 적재가 비켜 간다**(`human_edits` 기본 `keep`). 계획의 그 줄에
  「사람이 고친 칸은 그대로 둡니다 — 이름 · 공급사」 로 온다 — 그것을 **사용자에게 그대로
  보여 준다.** 칸 단위라 사람이 안 건드린 칸은 그대로 들어간다.
  **`overwrite` 를 스스로 고르지 않는다**: 사람의 수정을 되돌리는 일이고, 되돌린 사실은 그
  사람에게 안 보인다. 사용자가 「원천이 정본이다」 라고 말했을 때만 쓴다.
- 한 번에 10만 행까지.

### 기계가 붙인 별칭은 검수를 기다린다

도구로 넣은 별칭은 **검수 대기**로 남는다. 사람이 승인한 표기와 기계가 짐작한 표기가 같은
무게로 섞이면, 틀린 짐작 하나가 그 뒤의 모든 이름풀이를 조용히 끌고 간다.

- `aliases_pending(type_slug, limit=)` — 아직 검수 안 된 것들. 품질 화면의
  `alias_pending` 과 같은 목록이다.
- `aliases_review(type_slug, alias_ids, action=)` — `approve`(맞다) 또는 `remove`(아니다).
- **스스로 승인하지 않는다.** 내가 붙인 것을 내가 승인하면 검수가 아니라 서식이다. 목록을
  사람에게 보여 주고, 사람이 고른 것만 이 도구로 보낸다.

### 묶음 — `bundle_import(bundle)`

원천 데이터를 정제해 넣을 때는 **`pipeline/` 의 절차**를 따른다(실행 폴더 · 검증 · 미리 보기 ·
적용). 이 도구는 그 묶음을 **한 번에 미리 보는** 자리다 — 정의를 먼저 적용하지 않아도 그 정의로
객체 · 관계를 맞춰 본다. 돌아오는 것은 위와 같은 작업이고 `result` 가 묶음의 계획(정의 · 타입마다의
객체 · 관계)이다. **사람이 확인한 뒤** `job_apply(job_id)` — 전부 아니면 무.

### 넣은 판을 되돌린다 — `bundle_runs` → `bundle_undo(run_id)`

백필은 한 번에 수만 줄을 넣는다. 원천의 열을 잘못 맞춘 것을 그 뒤에 알면, 객체를 하나씩
고치는 길밖에 없었다 — 수만 줄에는 그 길이 없다.

- `bundle_runs(limit=)` — 넣은 판들(적용한 것만). `undoable` 이 거짓이면 이미 되돌렸거나
  보관 기간(30일)이 지난 것이다.
- `bundle_undo(run_id)` — **기본은 계획이다.** 무엇이 지워지고(`delete`) 무엇이 넣기 전 값으로
  돌아가고(`update`) 무엇이 다시 이어지는지(`create`) 줄마다 온다.
- **스스로 되돌리지 않는다.** 계획을 사람에게 보여 주고 판단을 받은 뒤 `apply=True`.
  되돌리기는 남의 하루를 지우는 일일 수 있다.
- 건너뛴 줄(`unchanged`)의 **이유도 함께 보여 준다** — 그 사이 남이 고친 줄, 밖에서 가리키는
  것이 생긴 객체, 합치기(참조를 옮긴 것이라 손으로 되돌린다). 「되돌렸습니다」 만 말하면
  남은 것을 아무도 모른다.

### 작업 셋 — `job_status` · `job_apply` · `jobs_list`

| | |
|---|---|
| `job_status(job_id, wait_seconds=20)` | 어디까지 됐나. 끝나기를 잠깐 기다렸다가 준다 |
| `job_apply(job_id)` | 계획을 **사람이 확인한 뒤** 적용. 사용자의 판단 없이 부르지 않는다 |
| `jobs_list()` | 내 작업 최근 것부터 + 워커가 살아 있나. `workers[].alive` 가 전부 거짓이면 작업은 영영 대기다 — 운영자에게 알린다 |

<!--@ relations -->
## 객체 잇기

- `relation_add(type_slug, object_id, relation, dst_object_id, evidence_note)` —
  `relation` 은 `ontology_schema` 의 `relation_types[].slug`. 출발 타입·도착 타입이
  정의와 맞아야 한다.
- 잘못 이었으면 `relation_update`(근거 · 속성만) 또는 `relation_remove`(끊기). `relation_id` 는
  `object_get` 의 `related[].relation_id`. **끊기는 되돌리기가 없다** — 확실하지 않으면 끊지
  말고 사람에게 `object_get` 결과를 보여 준다.
- **양 끝은 id 다.** 이름밖에 없으면 `object_resolve` 로 먼저 푼다 — `candidates` 가
  오면 잇지 말고 사람에게 묻는다. 틀리게 이은 선은 지워도 「왜 그렇게 이었는지」 를
  본 사람의 기억에 남고, 그 기억이 다음 판단을 흔든다.
- **근거를 적는다.** 어느 문서·어느 자료에서 이 연결이 나왔는지. 근거 없는 연결은
  시간이 지나면 아무도 못 믿고, 확인하려면 처음부터 다시 조사해야 한다.
- 여러 줄이면 `relations_import(type_slug, rows, mode=)` — 작업이 된다(`bulk` 주제). 행은
  `{"src": "<key 또는 label>", "relation": "<slug>", "dst": "...", "evidence_note": "..."}`.
  이미 이어진 것은 `unchanged` 라 두 번 올려도 두 겹이 안 된다. 관계에 속성이 있으면
  같은 행에 `"properties": {...}`.
- `mode` 가 **없어진 선을 어떻게 할지**다. 기본 `add` — 파일에 없는 선은 그대로 둔다.
  `replace` 는 파일에 나온 **출발 객체 · 관계마다** 그 밖의 선을 끊고, `replace_type` 은
  **타입 전체 · 관계마다** 끊는다. 원천이 그 범위를 통째로 가지고 있을 때만 쓴다 — 조각을
  `replace_type` 으로 보내면 나머지가 전부 지워진다. 끊을 선은 계획에 `unlink` 로 오니,
  **적용 전에 그 수를 사람에게 보여 준다.**

<!--@ sparql -->
## 질의어로 묻기 (SPARQL)

**먼저 `objects_summary` · `objects_list` 로 되는지 본다.** 한 타입 안의 세기 · 거르기는 그쪽이
빠르고 답도 화면과 같다. 질의어는 **그것으로 안 되는 물음**에 쓴다:

- 타입을 둘 이상 건너뛰어 잇는 것 — 「이 프로젝트의 과제들에 달린 모델의 시험 진행」
- 적어 두지 않은 방향 — 「이 과제를 가리키는 모델」(역관계)
- 인터페이스로 묶어 보기 — 「설비인 것 전부」(시험장비 · 계측기를 한 번에)

**질의어를 쓰기 전에 `graph_neighbors` 로 되는지 본다.** 「이것과 이어진 것들」 은 그쪽이 빠르고,
질의문을 틀리게 쓸 일이 없다. 질의어는 **조건을 걸어 묶어 세는** 물음에 쓴다(「부서별로 몇 건」).

**보이는 것만 답한다** — 목록과 같은 규칙(전역 + 내 부서)이다. 남의 부서 객체는 그래프에 없으므로
0건이 「없다」 는 뜻이 아닐 수 있다 — `objects_list` 의 `diagnosis` 로 확인한다.

**그래프는 메모리에 선다** — 올릴 객체가 5만 건을 넘으면 세우기 전에 `[RDF-0005]` 로 거절한다(기록
타입은 수백만 건이 된다). 그때는 `types` 로 좁히고, 한 타입 안의 세기 · 거르기는 `objects_summary` ·
`objects_list` 로 묻는다.

절차는 둘뿐이다:

1. `rdf_schema` — 클래스 · 속성 · 관계 이름을 **확인한다**(추측하지 않는다).
   `sp:<타입slug>` · `sp:<타입>.<속성키>` · `sp:rel.<관계slug>`, 역관계는 `.inverse`.
   인터페이스도 클래스다(`sp:<인터페이스slug>`) — 구현 타입의 객체는 `a sp:<인터페이스>` 로
   **추론 없이** 잡힌다. 공통 속성의 값은 타입마다의 속성(`sp:<타입>.<키>`)에 있고, 그것들은
   `sp:<인터페이스>.<키>` 의 `rdfs:subPropertyOf` 다 — 구현 타입 전부의 값을 한 번에 물으려면
   `?x ?p ?v . ?p rdfs:subPropertyOf sp:equip.maker`(추론 없이 된다).
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

<!--@ extensions -->
## 이 설치에만 있는 기능 — 확장

확장은 **한 설치에만 붙는 기능 묶음**이다(예: 디지털 트윈 역량 — 시험 항목과 시뮬레이션의
연계 · 축 다섯의 평가 · 인력 · 인프라). 코어(타입 · 객체 · 관계)와 달리 설치마다 있고 없다.

**도구는 둘이다.** 확장마다 도구를 만들면 확장 셋에 도구가 마흔이 되고, 목록이 길어질수록
엉뚱한 것이 골라진다 — 코어에서 타입마다 도구를 두지 않는 것과 같은 이유다.

1. `extensions_schema()` — 켠 확장과 그 자리들. **부르기 전에 여기서 경로를 본다.**
   `endpoints[]` 는 `{method, path, summary, query, body}` 이고, `path` 는 확장 뿌리부터다
   (`dt/pairs`). 이름 뒤의 `*` 는 **필수**다.
2. `extension_call(extension, path, method=, query=, body=)` — 그 자리를 부른다.

지켜야 하는 것:

- **경로를 짐작하지 않는다.** 목록에 없는 경로는 404 다. 꺼진 확장도 목록에 없다 — 「기능이
  없는 설치」 이고, 그렇게 답한다.
- **쓰기는 범위가 따로 있다.** 기계 자격(PAT)으로 고치려면 그 확장의 범위가 토큰에 있어야
  한다(디지털 트윈은 `caegroup:write`). 없으면 오류가 그 이름을 말해 준다 — 사람에게 그
  범위로 토큰을 새로 만들라고 전한다. 읽기는 `read` 로 된다.
- **되돌릴 수 없는 것은 사람에게 먼저 묻는다.** 「일괄」 이 붙은 자리(`.../bulk`)는 한 번에
  여러 줄을 바꾸고, 목록을 받는 자리는 **표대로 맞춘다**(보내지 않은 줄이 사라진다).
- **근거를 요구하는 자리가 있다.** 디지털 트윈의 평가는 근거가 비면 저장되지 않는다 — 무엇을
  보고 매겼는지를 모른 채 채우지 말고, 사람이 말한 것을 그대로 적는다.
- 파일을 주는 자리(`.../export` · 첨부 내용)는 도구로 받지 않는다 — 화면에서 내려받는다.
- 기준 정보는 **코어 도구로** 다룬다. 확장의 기준 정보도 온톨로지 객체다(시험 항목 ·
  시뮬레이션 해석) — `objects_list` · `object_create` · `object_update` · `objects_import`
  가 그대로 쓰이고, 불량 유형처럼 목록인 칸도 그 객체의 속성이다.

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
| 여러 그룹 · 타입이 **같은 개념**을 따로 갖는다(설비 · 부품 …) — 같은 칸을 따로 적으면 키 · 종류가 갈린다 | **인터페이스** — 공통 속성을 한 번 적고 타입이 구현한다(같은 키 · 같은 모양). 「A 는 B 의 일종」 도 이것이다 |
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
- **상태는 둘이다**(`active` · `deprecated`). 「아직 정본이 아니다」 를 말해야 할 때(표준어 제안,
  빈 분류의 승격 후보) 세 번째 상태를 만들지 말고 **그 타입의 속성**으로 둔다 —
  `enum` 칸 하나(`검토 상태`: 제안 · 확정 · 보류)면 목록 · 조건 · 통계가 전부 그것으로 걸린다.
  상태 값을 늘리면 코어 API 의 약속(`status: active · deprecated`)이 바뀌어, 그것을 읽는
  바깥 시스템이 모르는 값을 만난다 — **한 설치의 편의로 남의 코드를 깨는 일**이다.
  제안한 이름 자체는 별칭으로 두고 **검수 대기**로 남긴다(사람이 확인하면 정본이 된다).

### 8. 하지 않는 것

- 두 걸음 연결을 전제로 설계하지 않는다 — 조건 · 통계는 한 걸음까지다. 두 걸음 물음이 자주 나오면
  칸이 하나 빠진 것이다.
- 원문 전체 · 첨부 내용을 속성에 넣지 않는다.
- 개인정보 · 계약 금액 · 고객 기밀은 템플릿 6장(민감한 것)을 먼저 본다.
- 정의를 지우거나 slug 를 바꾸지 않는다 — 가져오기는 더하고 고치기만 한다.

<!--@ metrics -->
## 지표 — 기록을 미리 세어 둔 값

서비스 기록은 한 타입에 수백만 건이고, 사람이 묻는 것은 「판매월 코호트의 누적 인입률」 「생산월 x
공장별 증상 건수」 「부품 교체 집중도」 처럼 **분모가 있고 시간이 있는** 물음이다. 지표는 그것을
밤마다(그리고 적재 뒤에) 세어 둔 것이다 — **계산은 플랫폼이 하고, 너는 읽고 해석한다.**
`objects_list` 로 원시 행을 받아 직접 세지 않는다 — 쪽 상한에서 틀리고 토큰이 터진다.

**순서.**

1. `metric_list()` — 세어 둔 지표가 있나. `dims[]` 가 묶을 수 있는 기준, `grain` 이 기간 단위,
   `denominator` 가 있으면 비율이 나온다. `broken` 이 있으면 그 지표는 지금 안 센다 — 사용자에게
   알린다.
2. `metric_query(slug, shape=, dims=, by=, filters=, …)` —
   - `table`: 기준별 셀. `by=["period"]` 면 기간별, `by=["cohort"]` 면 코호트별.
   - `series`: 기간순 추이 — 빈 기간은 0, `prev` 전기, `yoy` 전년 동기. `split` 으로 선 나누기.
   - `cohort`: 코호트 x 경과 행렬. `cumulative=true` 면 경과순 누적(분자만 누적 ÷ 분모).
   `filters` 는 `{기준 이름: 값}` — 값은 응답의 `dims` 값(참조는 id). 사람이 이름으로 말하면
   `object_resolve` 로 id 를 먼저 푼다.
3. 지표가 없으면 `objects_summary`(기준 하나 · 세부 기준 하나 · 그때그때 센다)로 되는 물음인지
   보고, 그래도 안 되면 `metric_define(apply=false)` 로 **정의를 제안**한다 — 계획(오류 · 경고 ·
   어림한 셀 수)을 사람에게 보여 주고, 저장은 사람이 판단한 뒤 `apply=true`. **시스템 관리자만.**

**옮길 때 빼먹지 않는 것.**

- `computed_at`(계산 시각) — 세어 둔 값이다. `stale` 이 true 면 세 주기가 지나도록 안 셌다.
- `closed` 가 false 인 기간은 아직 더 들어올 수 있다(`settle_days`). 「3월 인입률 0.8%」 라고
  말할 때 그 달이 닫혔는지 함께 말한다.
- `overlap` — 한 기록이 여러 셀에 든다(여러 값 기준 · 여럿과 이어진 걸음). 셀의 합이 기록
  수보다 크다.
- `unbucketed` — 시간 칸이 비었거나 못 읽어 기간이 없는 기록 수. `truncated` — 상한에서 잘렸다. 좁혀서 다시.
- `denominator.missing` — 분모가 없거나 0 이라 비율이 빈 셀 수. 분모의 기간이 아직 안 들어온
  것일 수 있다.
- 「그 수가 뭔데」 — 셀의 `drill.params` 를 `objects_list(type_slug=drill.type_slug, conditions=)`
  로. `f.<칸>.<연산>=<값>` 을 `{"field": "<칸>", "op": "<연산>", "value": "<값>"}` 로 풀고,
  `status=` · `year=` 는 그 인자로. `drill.partial` 이 비어 있을 때 목록의 `total` 이 셀의
  `count` 와 같다.

**분석 — 세어 둔 셀 위의 통계(ADR 0014).** 「B10 이 몇 달이냐」 「새 모델이 전작보다
나빠졌나」 같은 물음은 셀을 받아 직접 맞추지 않는다 — `metric_analyze(slug, recipe, options=)`.
같은 물음에 같은 방법 · 같은 답 · 같은 주의가 나와야 한다. `metric_list` 의 `analyses[]` 가 그
지표에 되는 레시피와 **안 되는 이유**를 말한다(안 되면 그 이유를 그대로 전한다).

| 물음 | recipe | 지표 모양 | options |
| --- | --- | --- | --- |
| 수명 · B10 | `life` | 판매월 코호트 + 분모 `time=cohort` | `model`(`auto`) · `max_age` |
| 새 모델이 전작보다 나빠졌나 | `sprt` | 위 + 분모 `on` 에 모델 기준 | `target`(값, 참조면 id) · `reference` 또는 `reference_via` |
| 관리도 — 튀는 달 · 공장 | `control` | 기간 또는 코호트(출고 K 기간 안) | `axis` · `window` · `split`(분모 짝에 있는 기준) · `baseline_to` |
| 계절을 빼고 언제 바뀌었나 | `changes` | 기간 또는 코호트 | `axis` · `window` |
| 몇 값에 몰렸나 | `pareto` | 기준이 있는 건수 · 합계 | `dim` · `top` · `by_period` |

옮길 때 규칙:

- **`caveats[]` 의 `message` 를 그대로.** `warn` 은 숫자보다 먼저 말한다.
- 수명: `status="unreachable"` 인 B수명은 **값이 없다** — 「판매의 p 만 결국 고장 나 그 비율에
  이르지 않는다」 로 말한다. 필요하면 `conditional_age` 를 「결국 고장 나는 것들 중의 B10」 이라는
  이름으로. `extrapolated` 는 「관측 밖으로 늘려 읽은 값」, `observed` 는 관측 안. 고른 모형
  (`chosen` — 표준 · 결함)과 형상(β — 1 보다 크면 마모)을 함께.
- 순차 검정: `continue` 는 「아직 결론 없음」(문제없음이 아니다), `not_worse` 는 「ρ 배 나쁘지는
  않다」(같다가 아니다), `worse` 는 「ρ 배 쪽」. `decided_at` 과 SMR 구간을 함께. 「아직」 이면
  `periods_to_*` 가 결론까지의 어림이다.
- 관리도: 신호(넬슨 규칙 번호)는 「조사할 곳」 이지 원인이 아니다. 건수(`kind="c"`)로 그린
  관리도는 판매가 늘어도 신호처럼 보인다.
- 변화점: `provisional` 은 잠정 — 몇 기간 더 보고 판단한다. 비는 `ratio_ci` 와 함께.
- 근거는 지표 읽기와 같다 — `drill.params` → `objects_list` 의 `conditions`.
- 거절(422)은 「틀린 수를 낼 자리」 다: 셀이 상한에서 잘렸다(좁혀서 다시), 여러 값 기준을
  묶지도 거르지도 않았다(`filters` 로 하나를 고른다). 우회하지 않는다.

**정의할 때.** 시간 칸 · 코호트 칸은 **자기 타입의 날짜 칸**이어야 하고, 기준은 여섯까지, 분모의
`on` 은 양쪽에 같은 이름 · 같은 값 종류(같은 타입을 가리키는 참조, 같은 종류의 칸)여야 한다.
자유 글자 칸을 기준으로 두면 셀이 행 수만큼 나온다 — 계획의 경고를 그대로 전한다. 이름 붙인
기준의 주소는 `objects_summary` 의 `group_by` 와 같다(`ref.model.ref.base.series` 처럼 걸음 셋까지 — 끝은
칸 이름이다: SKU 의 기본 모델 참조 칸 자체는 `ref.model.base_model`, `ref.model.ref.base_model` 이 아니다).

**자주 세우는 셋 — 분모가 있는 비율.** 분모가 되는 집계 지표를 **먼저** 세우고(분모의 분모는
없다), 기록 지표가 `denominator` 로 그것을 부른다. 짝은 **기준 이름**으로 맞춘다 — 주소는 달라도
된다(기록은 SKU 를 거쳐 `ref.model.out.<SKU→기본 모델 관계>` 또는 `ref.model.<기본 모델 참조 칸>`, 판매
집계는 바로 `properties.base_model`). 사람의 말을 정의로 옮길 때 이 표를 따른다:

| 물음 | 분모 지표(먼저) | 기록 지표 | 읽기 |
| --- | --- | --- | --- |
| 판매월 코호트 누적 인입률 | 판매 집계 — `sum` 대수 · time 판매월(month) · 기준 `base_model` | `count` · time 접수일(month) · cohort 판매일(month) · 기준 `base_model`(+ 증상) · denominator `{"on": ["base_model"], "time": "cohort", "per": 100}` | `shape="cohort", cumulative=true` — 행의 `denominator` 가 그 달 판매 대수, 셀의 `ratio` 가 누적 인입률(%) |
| 생산월 x 공장 — 천 대당 | 생산 집계 — `sum` 대수 · time 생산월 · 기준 `base_model` · `factory` | `count` · time 접수일 · cohort 생산일 · 기준 `base_model` · `factory` · denominator `{"on": ["base_model", "factory"], "time": "cohort", "per": 1000}` | `shape="table", dims=["factory"], by=["cohort"]` |
| 부품 교체 집중도 | 판매 집계(위) | `count` · time 접수일(**quarter**) · 기준 `part`(교체 부품 — 여러 값 참조) · denominator `{"on": [], "time": null, "per": 1000}` | `shape="table", dims=["part"]` — **겹침**: 한 건이 부품 여럿이라 합이 건수보다 크다. 기본 모델 x 부품 x 월은 200만 건에서 셀 350만으로 상한을 넘는다 — 기본 모델별로 보려면 기간을 빼거나 거른다 |

- 분모 표(판매 · 생산)의 월은 날짜 칸이다 — 「2026-09」 처럼 연월만 적힌 값은 그 달 1일로 들어간다.
- 비율이 비면 `denominator.missing` — 분모 표에 그 달 · 그 기본 모델 줄이 없는 것이다. 지어내지
  않고 그렇게 말한다.
- 기준 하나에 값이 수천 가지면(기본 모델 2,000개 x 코호트 50개) 표 한 번이 상한(2만 셀)에서
  잘린다 — 기본 모델로 거르고(`filters`) 다시 묻는다.
