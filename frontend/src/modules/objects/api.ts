/** 객체 API. */

import { jobsApi } from '@/modules/jobs/api'
import type { Job } from '@/modules/jobs/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { api, downloadFile } from '@/shared/api/client'
import type { Page } from '@/shared/api/paging'
import { conditionKey } from '@/modules/objects/urlConditions'

export interface ObjectRow {
  id: string
  type_slug: string
  key: string | null
  label: string
  description: string
  properties: Record<string, unknown>
  /** `object_ref` 가 가리키는 객체의 이름 (id -> 이름). **서버가 한 번에 모아 준다.** */
  ref_labels: Record<string, string>
  /** 사람이 붙인 다른 이름. 검색·참조 풀이·파일이 이것으로도 찾는다. 시험 자료에는 없을 수 있다. */
  aliases?: string[]
  /** {데이터 소스 slug: 그쪽 식별자}. 동기화가 남긴다 — 보기만. */
  external_ids?: Record<string, string>
  status: string
  owner_workspace_slug: string | null
  /** 부서 **이름**. 화면은 이것을 쓴다 — slug(hq)는 주소지 부서 이름이 아니다. */
  owner_workspace_name: string | null
  valid_from_year: number | null
  valid_to_year: number | null
  created_at: string
  updated_at: string
}

export interface AttachmentBrief {
  id: string
  owner_field: string | null
  original_name: string
  size_bytes: number
  created_at: string
  /** 서버가 열어 보고 이미지로 읽은 것 — 이것만 그림(미리보기)으로 띄운다(ADR 0012). */
  is_image?: boolean
}

/**
 * 목록의 파일 칸 한 칸 — 첫 장(사진이 있으면 사진)과 모두 몇 개인가. 바이트는 없다 — 화면은
 * `/attachments/{id}/thumbnail`(긴 변 320px)을 받는다.
 */
export interface FileCell {
  count: number
  first: AttachmentBrief
}

/**
 * 객체 목록 한 쪽. 목록 화면의 열에 파일 속성이 있으면 그 칸들이 `files` 로 온다 —
 * `{객체 id: {속성 키: 칸}}`. 줄마다 첨부를 물으러 가면 한 쪽에 요청이 수십 개 붙는다.
 */
export interface ObjectPage extends Page<ObjectRow> {
  files?: Record<string, Record<string, FileCell>>
  /**
   * 계보의 낡음(ADR 0041) — `{객체 id: 'direct' | 'upstream'}`, **낡은 줄만**. 계보 관계 종류가 없는
   * 설치는 늘 비어 있다.
   */
  lineage?: Record<string, LineageStaleState>
  /** 요청한 이어진 칸(`columns`, ADR 0043)의 머리 — 받은 차례대로. */
  linked_columns?: LinkedColumn[]
  /** `{객체 id: {주소: 칸}}` — 이어진 칸의 값. 이어진 것이 없는 칸은 없다. */
  linked?: Record<string, Record<string, LinkedCell>>
}

/** 이어진 칸 하나의 머리(ADR 0043). */
export interface LinkedColumn {
  field: string
  label: string
  /** 끝 칸의 종류 — 관계로 이어진 것 자체면 `object`. */
  data_type: string
  multi: boolean
}

/** 한 줄의 이어진 칸 — 20개까지, 더 있으면 `more`(몇 개인지는 세지 않았다). */
export interface LinkedCell {
  values: { value: unknown; label: string }[]
  more: boolean
}

/** 사진 일괄 업로드의 한 줄 — zip 안의 파일 하나가 무엇이 되나(`objects/photos.py`). */
export type PhotoStatus =
  | 'attach'
  | 'replace'
  | 'skip'
  | 'not_found'
  | 'ambiguous'
  | 'not_image'
  | 'too_large'
  | 'forbidden'
  | 'bad_entry'

export interface PhotoRow {
  /** zip 안의 경로(한글 이름을 풀어 읽은 것). */
  name: string
  status: PhotoStatus
  size_bytes: number
  object_id: string | null
  object_label: string
  object_key: string | null
  /** 무엇으로 찾았나 — `key` · `alias` · `label` · `id` · `folder` · `suffix`. */
  matched_by: string
  message: string
  /** 교체면 떼는 파일 수. */
  replaces: number
  attachment_id: string | null
}

/** 사진 일괄 업로드 작업의 결과 — 계획이든 적용이든 같은 모양이다. */
export interface PhotoPlan {
  photos: true
  applied: boolean
  /** 붙는 것이 하나라도 있나 — 그때만 적용할 수 있다. */
  ok: boolean
  type_slug: string
  field: string
  field_label: string
  existing: 'skip' | 'replace' | 'add'
  files: PhotoRow[]
  /** 줄 종류마다 몇 개 — `counts` 가 아니다(그 이름은 작업 화면 · 홈이 일괄 입력의 수로 읽는다). */
  tally: Partial<Record<PhotoStatus, number>>
  /** 세기만 하고 줄에 안 실은 숨김 파일(`__MACOSX` · `.DS_Store` …). */
  hidden: number
  summary: string
}

export interface RelatedObject {
  relation_id: string
  relation: string
  /** 이 줄에 적을 말 — **방향에 맞는 쪽**(정방향이면 label, 역방향이면 inverse_label). */
  label: string
  /** 내가 출발점인가. 거짓이면 저쪽이 나를 가리킨다. */
  outgoing: boolean
  object_id: string
  object_label: string
  object_key: string | null
  object_type_slug: string
  object_type_label: string
  properties: Record<string, unknown>
  evidence_note: string
  created_at: string
  /** `relation`(관계 줄) · `field`(참조 칸 — 칸에 저장한 관계, 끊는 대신 칸을 고친다). */
  stored_as?: 'relation' | 'field'
  field_key?: string | null
  /** 유효 기간의 시작일(포함, ADR 0039) — 유효 기간을 받는 관계 종류의 관계만. 비면 처음부터. */
  valid_from?: string | null
  /** 유효 기간의 끝날(**제외** — 그날부터 무효). 비면 끝없이. */
  valid_to?: string | null
  /**
   * 기준일에 이 관계가 어디인가 — `current`(유효) · `past`(지난 관계) · `future`(예정 관계).
   * 기본 상세에는 `current` 만 온다 — 「지난 · 예정 관계 포함」 이면 셋 다.
   */
  period_state?: 'current' | 'past' | 'future'
}

/**
 * 제안된 관계(ADR 0042) — 서버의 `RelationProposalOut`(`objects/schemas.py`)과 같은 모양.
 *
 * 생성 타입(`@/shared/api/schema`)을 쓰지 않는 까닭은 아래 「채울 곳」 과 같다 — 그 파일은 커밋하지
 * 않는 생성물이라 CI 의 빌드 단계에는 아직 없다.
 */
export type ProposalStatus = 'proposed' | 'accepted' | 'rejected'
/** 누가 · 무엇이 제안했나 — 사람 · AI(MCP) · 규칙 · 데이터 소스 · 파일 가져오기. */
export type ProposalOrigin = 'user' | 'mcp' | 'rule' | 'datasource' | 'import'
/** 제안 하나의 결과 — 만들지 않은 까닭도 결과다. */
export type ProposalOutcome = 'created' | 'open' | 'connected' | 'rejected_before' | 'error'

export interface RelationProposal {
  id: string
  relation: string
  relation_label: string
  /** 보는 객체 쪽에서 읽는 말 — 정방향이면 종류 이름, 역방향이면 반대 이름. */
  label: string
  /** 보는 객체가 출발점인가. */
  outgoing: boolean
  status: ProposalStatus
  src_object_id: string
  /** 못 보는 객체면 「(볼 수 없는 객체)」. */
  src_label: string
  src_key: string | null
  src_type_slug: string
  src_type_label: string
  dst_object_id: string
  dst_label: string
  dst_key: string | null
  dst_type_slug: string
  dst_type_label: string
  properties: Record<string, unknown>
  valid_from: string | null
  valid_to: string | null
  /** 근거 — 검토자가 읽는다. 확정하면 관계의 근거가 된다. */
  evidence_note: string
  /** 확신도(0 ~ 1) — 비면 모른다. */
  confidence: number | null
  origin: string
  /** 어느 대화 · 규칙 · 소스 · 파일인지. */
  origin_ref: string
  proposed_by_label: string
  created_at: string
  decided_by_label: string
  decided_at: string | null
  decision_note: string
  /** 확정으로 생긴 관계 — 해제되면 비어 있다. */
  relation_id: string | null
  /** 이 사람이 확정 · 거절할 수 있나(출발 객체를 수정할 수 있나). */
  can_decide: boolean
  /** 이 사람이 철회할 수 있나(제안한 사람 · 시스템 관리자). */
  can_withdraw: boolean
}

export interface RelationProposalCreate {
  relation: string
  dst_object_id: string
  properties?: Record<string, unknown>
  evidence_note?: string
  valid_from?: string | null
  valid_to?: string | null
  confidence?: number | null
  origin?: ProposalOrigin
  origin_ref?: string
  /** 거절된 적 있는 같은 제안도 다시 만든다. */
  again?: boolean
}

export interface RelationProposalResult {
  index: number
  status: ProposalOutcome
  message: string
  proposal?: RelationProposal | null
  /** `connected` 면 이미 있는 그 관계. */
  relation_id?: string | null
  /** `open` 이고 더 높은 확신도로 근거 · 확신도를 바꿨으면 참. */
  updated: boolean
  error_code?: string | null
}

/** 이 객체를 가리키는 기록 — 타입 · 칸 하나의 수(ADR 0011). */
export interface LogCount {
  type_slug: string
  type_label: string
  /** 가리키는 칸 — 목록 거르기 `f.<key>.eq=<이 객체 id>`. */
  key: string
  /** 칸 이름(「모델」). */
  label: string
  inverse_label: string
  count: number
}

export interface ObjectProfile {
  object: ObjectRow
  type_label: string
  properties_schema: PropertyDef[]
  attachments: AttachmentBrief[]
  /** 이 객체에 걸린 관계들. **양방향 다 온다.** 나를 가리키는 기록은 `log_counts` 에. */
  related: RelatedObject[]
  /** 이 객체를 가리키는 **기록**(ADR 0011) — 타입 · 칸마다의 수. */
  log_counts?: LogCount[]
  /** **서버가 판정한 것이다.** 화면이 스스로 정하면 화면마다 단추가 달라진다. */
  can_edit: boolean
  /**
   * 관계를 맺고 끊을 수 있나. 허브가 내려준 객체는 값을 못 고쳐도(`can_edit` 거짓)
   * 이 설치의 관계로 가리키는 것은 된다 — 과제의 산출 문서처럼.
   */
  can_link?: boolean
  /** 내가 지켜보고 있나 — 바뀌면 알림이 온다. */
  watching: boolean
  /** 몇 사람이 지켜보나. **혼자가 아니라는 것을 아는 것**이 고칠 때의 조심을 만든다. */
  watcher_count: number
}

export interface TreeNode {
  id: string
  label: string
  key: string | null
  status: string
  /** **자식 수를 미리 준다** — 없는데 펼침 화살표가 보이면 눌러 보고서야 안다. */
  child_count: number
}

export interface TreeOut {
  nodes: TreeNode[]
  /** 부모도 자식도 없는 것의 수. **안 보여 주면 눈에서 사라진 채 남는다.** */
  orphan_count: number
  /** 이 트리의 기준일(ADR 0039) — 안 주면 오늘. 트리가 없으면 null. */
  as_of?: string | null
}

/** 일괄 계획의 한 줄 — 행마다 무엇이 되는지. */
export interface ImportRow {
  row: number
  action: 'create' | 'update' | 'unchanged' | 'unlink' | 'delete' | 'error'
  label: string
  key: string | null
  object_id: string | null
  /** 바뀌는 칸. 「고침」 이 무엇을 고치는지 — 안 보여 주면 사람은 안 누른다. */
  changes: string[]
  message: string
}

export interface ImportPlan {
  applied: boolean
  rows: ImportRow[]
  /** 행과 무관한 오류(모르는 열, 상한). **하나라도 있으면 아무것도 안 넣는다.** */
  errors: string[]
  counts: Record<'create' | 'update' | 'unchanged' | 'unlink' | 'error', number> &
    Partial<Record<'delete' | 'merge' | 'deprecate', number>>
}

/** 여럿 골라 한 칸 바꾸기의 계획 — 행마다 무엇이 되는지. */
export interface BulkEditRow {
  id: string
  label: string
  action: 'change' | 'unchanged' | 'error'
  before: string
  after: string
  message: string
}

export interface BulkEditPlan {
  applied: boolean
  /** 적용했을 때만 — 같이 바뀐 것을 한 번에 되돌릴 때 이 번호를 쓴다. */
  batch_id: string | null
  field: string
  field_label: string
  rows: BulkEditRow[]
  counts: Record<string, number>
  /** 고를 수 있는 칸 — 고르개가 이것만 보여 준다. */
  fields: { field: string; label: string }[]
}

/** 여럿 골라 지우기의 계획 — 행마다 지워지나, 안 지워지면 왜. */
export interface BulkDeletePlan {
  applied: boolean
  mode: 'block' | 'detach'
  /** action 은 delete · error 뿐이다 — 지우는 데 「그대로」 는 없다. */
  rows: BulkEditRow[]
  counts: Record<string, number>
}

/** 이 객체를 가리키는 것 — 삭제 전에 보는 것. */
export interface References {
  property_refs: {
    object_id: string
    label: string
    key: string | null
    type_slug: string
    type_label: string
    property_key: string
    property_label: string
  }[]
  relations: {
    relation_id: string
    relation: string
    outgoing: boolean
    other_id: string
    other_label: string
    other_type_slug: string
    /** 유효 기간(ADR 0039) — 삭제하면 지난 · 예정 관계도 함께 해제된다. */
    valid_from?: string | null
    valid_to?: string | null
  }[]
  /** 볼 수 없는 부서의 것 — **수만 온다.** */
  hidden_property_refs: number
  hidden_relations: number
  /** 보이지만 목록에 다 싣지 않은 속성 참조 — 인기 객체는 수만 건이 가리킨다. */
  more_property_refs: number
  total: number
}

export interface MergeResult {
  into: string
  property_refs: number
  relations_moved: number
  relations_dropped: number
}

/** 그 시점의 값 전체 — 지금 값에서 기록을 거꾸로 대어 재구성한 것. */
export interface Snapshot {
  key: string | null
  label: string
  status: string
  properties: Record<string, unknown>
  description?: string
  valid_from_year?: number | null
  valid_to_year?: number | null
}

export interface HistoryEntry {
  id: string
  at: string
  actor_label: string
  action: string
  reason: string | null
  /** `object` 는 값이 바뀐 기록, `relation` 은 관계가 걸리거나 끊긴 기록. */
  kind: 'object' | 'relation'
  /** 칸별 `{before, after}`. 속성은 `properties.<키>`. */
  changes: Record<string, { before: unknown; after: unknown }>
  relation: {
    relation: string
    outgoing: boolean
    other_id: string
    other_label: string
    /** 그 관계의 유효 기간(ADR 0039) — 기간을 달리한 같은 관계가 여럿일 때 어느 것인지. */
    valid_from?: string | null
    valid_to?: string | null
    /** 수정 기록이면 무엇이 바뀌었나 — 속성 · 근거 · 기간의 `{before, after}`. */
    changed?: Record<string, { before: unknown; after: unknown }>
  } | null
  /** 값 기록에만 있다 — 복원의 목표. */
  snapshot: Snapshot | null
  /** 일괄 수정으로 **같이 바뀐** 기록이면 그 묶음. 한 번에 되돌리는 입구다. */
  batch?: { id: string; field_label: string; size: number } | null
  /** 문서를 고친 기록(ADR 0053) — 값이 아니라 판이라 그 시점의 값이 없다. */
  document?: {
    key: string
    revision: number
    chars: number
    via: string
    note: string
  } | null
}

/** 조건 하나 — `f.<칸>.<연산>=<값>`. 칸 안에서는 `in` 으로 OR, 칸끼리는 AND. */
export interface Condition {
  field: string
  op: ConditionOp
  value: string
  /**
   * **제외 묶음** 번호(1 부터, ADR 0015) — 같은 번호끼리 AND 로 묶어 그 묶음에 맞는 것을 뺀다.
   * 주소는 `nf<번호>.<칸>.<연산>=<값>`. 없거나 0 이면 보통 조건.
   */
  exclude?: number
  /**
   * **같은 관계 묶음** 번호(1 부터, ADR 0043) — 같은 번호의 조건은 같은 선 하나(선의 칸 · 상대 ·
   * 상대의 칸)에서 함께 맞아야 걸린다. 주소는 `fe<번호>.` · 제외 묶음 안이면 `nf<n>.fe<m>.`.
   * 없거나 0 이면 묶지 않음(「그중 하나라도」).
   */
  edge?: number
}

export type ConditionOp =
  | 'eq'
  | 'ne'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'in'
  | 'contains'
  | 'starts'
  | 'empty'
  | 'notempty'
  /** 하위 전체 — 그 객체이거나 그 타입의 트리로 아래 전부(ADR 0035). */
  | 'under'

/** 값 여럿(`in`)의 구분자. 서버와 같다. */
export const CONDITION_MULTI_SEP = '|'

export interface SavedViewQuery {
  q: string
  conditions: Condition[]
  status: string | null
}

/** 이 뷰를 **그림으로** 볼 때의 설정. `group_by` 가 비면 목록일 뿐이다. */
export interface SavedViewSummary {
  group_by: string
  /** 두 번째 축. 있으면 계열이 여럿이 된다. */
  split_by: string
  metric: string
  metric_field: string | null
  chart: string
  stacked: boolean
  order: string
  /** 날짜 축의 기간 단위. 비우면 해. */
  grain?: string
}

export interface SavedView {
  id: string
  type_slug: string
  name: string
  query: SavedViewQuery
  owner_user_id: string
  owner_label: string
  /** 있으면 그 부서가 함께 쓴다. 없으면 내 것. */
  workspace_slug: string | null
  summary: SavedViewSummary
  /** 부서 홈에 올린 자리. null 이면 홈에 없다. */
  home_order: number | null
  can_edit: boolean
  created_at: string
  updated_at: string
}

/** 부서 홈에 올라간 뷰 하나. **홈은 타입을 모르므로** 서버가 다 실어 준다. */
/** 부서 홈에 올린 지표 — 값은 위젯이 지표의 추이로 읽는다. */
export interface HomeMetric {
  id: string
  metric_slug: string
  metric_label: string
  /** 선을 나눌 기준 이름 — 없으면 합계 한 줄. */
  split: string | null
  home_order: number
}

/** 부서 홈의 한 칸 — **저장된 뷰이거나 지표다**(`kind`). 둘은 한 줄에 선다. */
export interface HomeWidget {
  kind?: 'view' | 'metric'
  view?: SavedView | null
  metric?: HomeMetric | null
  /** 뷰는 그 타입, 지표는 원천 기록 타입의 이름. */
  type_label: string
  icon: string
  /** 어느 부서의 것인가 — 여러 부서를 한 화면에 놓을 때 이것이 없으면 구별이 안 된다. */
  workspace_slug: string
  workspace_name: string
}

/** 내가 지켜보는 것 한 줄 — 최근 바뀐 것부터. */
export interface Watched {
  id: string
  type_slug: string
  type_label: string
  icon: string
  label: string
  key: string | null
  status: string
  updated_at: string
}

/** 품질 — 한 종류·한 타입의 걸린 것들. */
export interface QualityFinding {
  kind: 'missing_required' | 'orphan' | 'broken_ref' | 'duplicate' | 'alias_clash' | 'alias_pending'
  kind_label: string
  type_slug: string
  type_label: string
  /** 전부 센 수. `hits` 는 상한까지만. */
  count: number
  hits: { id: string; label: string; key: string | null; detail: string }[]
}

export interface QualityReport {
  findings: QualityFinding[]
  sample_limit: number
  /** **너무 커서 훑지 않은 타입** — 안 센 것을 말하지 않으면 「걸린 것이 없다」 로 읽힌다. */
  skipped?: string[]
}

export interface Rollup {
  property: string
  label: string
  fn: string
  /** 값이 하나도 없으면 null — 0 은 「합이 0」 으로 읽힌다. */
  value: number | null
  count: number
  /** 아래에 있지만 값이 빈 객체 수. 이것이 붙어야 합계가 「전부의 합」 으로 안 읽힌다. */
  missing: number
  /** 모은 대상 객체 수 — 롤업마다 다르다(「말단만」 이면 말단의 수). */
  descendants: number
  /** 「수량 반영」(ADR 0040) — 아래 객체마다 총수량만큼 셌다. */
  weighted: boolean
  /** 「말단만」 — 그 기준일의 트리에서 아래가 없는 객체만 모았다. */
  leaves_only: boolean
  /** 「수량 반영」 일 때 아래 관계 중 수량 칸이 빈 것 — 1개로 셌다. */
  quantity_missing: number
  /** 값을 못 낸 까닭(트리의 순환 등) — 비었으면 없다. */
  note: string
}

/** BOM 전개의 보기(ADR 0040) — 정리 · 단계별 · 어디에 쓰였나. */
export type ExplodeMode = 'summary' | 'indented' | 'where_used'

/**
 * 전개의 객체 하나. **볼 수 없거나 삭제된 것은 가린다** — `id` 가 null 이고 이름이 「(볼 수 없는
 * 객체)」 · 「(삭제된 객체)」 다. 그 객체를 지나 아래(위)는 센다.
 */
export interface ExplodeObject {
  id: string | null
  label: string
  key: string | null
  type_slug: string | null
  status: string | null
  hidden: boolean
  deleted: boolean
}

/** 전개의 줄 하나 — 보기마다 쓰는 칸이 다르다. */
export interface ExplodeLine extends ExplodeObject {
  /** 단계(시작 객체가 0). 정리에서는 가장 얕은 단계. */
  level: number
  /** 단계별: 누적 수량 · 정리: 총수량 · 어디에 쓰였나: 이 객체 하나에 시작 객체가 몇 개(그 경로). */
  total: number
  /** 단계별 · 어디에 쓰였나 — 그 줄로 오는 관계에 적힌 수량. null 이면 1개로 셌다. */
  quantity: number | null
  /** 그 관계가 수량 없음(수량 칸이 비었거나 숫자가 아님 — 1개로 셌다). */
  quantity_missing: boolean
  /** 정리 — 가장 깊은 단계 · 경로 수 · 이 객체로 오는 관계 중 수량 없음. */
  depth_max: number | null
  paths: number | null
  missing: number | null
}

/** 어디에 쓰였나의 맨 위 객체(부모가 없는 것) — 「그것 하나에 시작 객체가 총 몇 개」. */
export interface ExplodeTop extends ExplodeObject {
  total: number
  paths: number
  /** 가장 먼 단계. */
  level: number
}

export interface ExplodeTotals {
  /** 서로 다른 보이는 객체 수(시작 객체는 뺀다). */
  objects: number
  /** 실린 줄 수. */
  lines: number
  /** 자르기 전의 전체 줄 수. */
  lines_total: number
  /** 따라간 관계 수. */
  links: number
  /** 수량 없음 — 수량 칸이 비었거나 숫자가 아닌 관계 수(1개로 셌다). */
  missing: number
  /** 지나간 볼 수 없는 객체 수 — 줄로는 가린다. */
  hidden: number
  /** 지나간 삭제된 객체 수. */
  deleted: number
}

/** BOM 전개(ADR 0040) — 트리 관계의 수량을 경로 따라 곱한 것. */
export interface ExplodeOut {
  mode: ExplodeMode
  /** 시작 객체 — 전개면 뿌리, 어디에 쓰였나면 그 객체. */
  root: ExplodeObject
  /** 기준일 — 비워 부르면 서버가 오늘을 싣는다. */
  as_of: string
  relation: string
  relation_label: string
  parent_end: 'src' | 'dst'
  /** 관계 종류의 수량 칸. 빈 글자면 모든 관계를 1개로 셌다. */
  quantity_key: string
  quantity_label: string
  lines: ExplodeLine[]
  /** 어디에 쓰였나만 — 맨 위 객체마다 그것 하나에 시작 객체가 총 몇 개. */
  tops: ExplodeTop[]
  totals: ExplodeTotals
  /** 줄 상한에 닿아 다 싣지 않았다 — `totals.lines_total` 이 전체 줄 수. */
  truncated: boolean
}

/**
 * 계보의 낡음(ADR 0041) — `fresh` 낡지 않음 · `direct` 바로 낡음(원천이 고정 뒤에 바뀜) ·
 * `upstream` 위에서 낡음(원천 중 하나가 낡음).
 */
export type LineageState = 'fresh' | 'direct' | 'upstream'
export type LineageStaleState = Exclude<LineageState, 'fresh'>

/** 계보 줄의 한 끝 — 볼 수 없는 객체는 `id` 가 null 이고 이름을 가린다. */
export interface LineageEnd {
  id: string | null
  label: string
  key: string | null
  type_slug: string
  type_label: string
  state: LineageState
  hidden: boolean
}

/** 계보 선 하나 — **원천 → 파생물**로 돌려 세운 것(관계 종류의 방향과 상관없이). */
export interface LineageLine {
  relation_id: string
  relation: string
  relation_label: string
  /** 시작 객체에서 몇 걸음 — 1 이 바로 붙은 선. */
  depth: number
  source: LineageEnd
  derived: LineageEnd
  /** 원천 타입의 개정 칸 — 비었으면 수정 시각으로 판정한 선이다. */
  revision_key: string
  /** 고정 개정 — 맺을 때 · 다시 고정할 때의 원천 개정. */
  pinned_revision: string | null
  /** 고정 시각 — 그 순간 원천의 수정 시각. */
  pinned_at: string | null
  /** 원천의 지금 개정. */
  revision: string | null
  source_updated_at: string | null
  /** 이 선이 바로 낡았나 — 원천이 고정 뒤에 바뀌었다. */
  stale: boolean
  /** 낡았으면 그 까닭 — 「메시 M-12 의 개정 Rev.B → Rev.C」. */
  reason: string
}

/** 객체 하나의 계보 — 위(원천 사슬) · 아래(파생물 사슬), 줄마다 낡음과 까닭. */
export interface LineageOut {
  object_id: string
  type_slug: string
  label: string
  state: LineageState
  /** 낡았으면 까닭들 — 바로(선마다) · 위에서(낡은 원천마다). */
  reasons: string[]
  /** 이 타입의 개정 칸 · 이 객체의 지금 개정. */
  revision_key: string
  revision: string | null
  /** 원천 사슬 — 얕은 것부터. */
  up: LineageLine[]
  up_total: number
  /** 파생물 사슬 — 얕은 것부터. */
  down: LineageLine[]
  down_total: number
  /** 위 · 아래 중 하나라도 줄 상한(500)에서 잘렸다. */
  truncated: boolean
  /** 영향 범위 — 이것으로 만든 것 n개(낡음 m개). */
  impact: { derived: number; stale: number }
  /** 이 사람이 이 객체의 원천 관계를 다시 고정할 수 있나(표시일 뿐 — 서버가 다시 본다). */
  can_repin: boolean
  /** 이 설치의 계보 관계 종류(slug) — 비면 계보가 없는 설치. */
  kinds: string[]
  /** 빈 결과의 까닭. */
  note: string
}

/** 다시 고정의 근거 — `checked` 영향 없음 확인 · `rebuilt` 다시 만들었음. */
export type RepinBasis = 'checked' | 'rebuilt'

export interface LineageRepinRequest {
  /** 다시 고정할 원천 관계 — 비우면 이 객체의 원천 관계 전부. */
  relation_ids?: string[]
  basis: RepinBasis
  note?: string
}

export interface LineagePin {
  revision: string | null
  at: string | null
}

export interface LineageRepinOut {
  /** 고정이 바뀐 관계 — 이미 지금 값이던 것은 안 든다(`unchanged` 로 센다). */
  changed: {
    relation_id: string
    relation: string
    source_id: string
    source_label: string
    before: LineagePin
    after: LineagePin
  }[]
  unchanged: number
  /** 다시 고정한 뒤의 계보. */
  lineage: LineageOut
}

/** 전개의 기준 — 화면과 파일이 **같은 주소**를 쓴다. */
export interface ExplodeOptions {
  mode: ExplodeMode
  /** 기준일(`YYYY-MM-DD`). 비우면 오늘. */
  asOf?: string | null
  /** 줄 상한(1~5,000). 비우면 서버의 상한. */
  limit?: number
}

function explodeParams(options: ExplodeOptions): URLSearchParams {
  const params = new URLSearchParams({ mode: options.mode })
  if (options.asOf) params.set('as_of', options.asOf)
  if (options.limit) params.set('limit', String(options.limit))
  return params
}

export interface ObjectQuery {
  q?: string
  limit?: number
  offset?: number
  /** `?p.<키>=<값>` 으로 나간다. */
  properties?: Record<string, string>
  /** 트리에서 고른 노드. */
  under?: string | null
  /** 그 아래 것까지 포함할지. **기본은 포함**이다. */
  deep?: boolean
  /** 그 해에 해당하는 것만. 축의 시간 정책이 뜻을 정한다. */
  year?: number | null
  /** 조건 필터. 칸 안 OR, 칸끼리 AND. */
  conditions?: Condition[]
  status?: string | null
  /** 인터페이스 목록에서 그중 몇 타입만(ADR 0006). `types=a|b` 로 나간다. */
  types?: string[]
  /**
   * 기준일(`YYYY-MM-DD`, ADR 0039) — 그날 유효한 관계만 따라간다(하위 포함 · 관계 조건 · 관계
   * 기준 · 계층 축). 비우면 오늘. 목록 · 통계 · 분포 · 내보내기가 같은 값을 싣는다.
   */
  asOf?: string | null
}

function queryString(query: ObjectQuery): string {
  const params = new URLSearchParams()
  if (query.q) params.set('q', query.q)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset) params.set('offset', String(query.offset))
  if (query.under) params.set('under', query.under)
  if (query.under && query.deep === false) params.set('deep', 'false')
  if (query.year) params.set('year', String(query.year))
  for (const [key, value] of Object.entries(query.properties ?? {})) {
    if (value) params.set(`p.${key}`, value)
  }
  if (query.status) params.set('status', query.status)
  if (query.types?.length) params.set('types', query.types.join('|'))
  if (query.asOf) params.set('as_of', query.asOf)
  for (const one of query.conditions ?? []) {
    params.append(conditionKey(one), one.value)
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

function importForm(
  file: File,
  workspaceSlug?: string | null,
  modes?: {
    aliasesMode?: 'add' | 'replace'
    relationsMode?: 'add' | 'replace'
    /** 사람이 화면에서 고친 칸을 비켜 갈지(`keep`, 기본) 덮을지(`overwrite`). */
    humanEdits?: 'keep' | 'overwrite'
  },
): FormData {
  const form = new FormData()
  form.set('file', file)
  if (workspaceSlug) form.set('workspace_slug', workspaceSlug)
  if (modes?.aliasesMode) form.set('aliases_mode', modes.aliasesMode)
  if (modes?.relationsMode) form.set('relations_mode', modes.relationsMode)
  if (modes?.humanEdits) form.set('human_edits', modes.humanEdits)
  return form
}

export const qualityApi = {
  report: (kind?: string) =>
    api.get<QualityReport>(`/objects/quality/report${kind ? `?kind=${kind}` : ''}`),
}

/** 검수 대기 별칭 한 줄 — 기계가 붙였고 사람이 아직 안 본 것. */
export interface PendingAlias {
  id: string
  object_id: string
  object_label: string
  object_key: string | null
  value: string
  kind: string
  source: string
  note: string
  created_at: string
}

export const aliasReviewApi = {
  pending: (typeSlug: string, limit = 200) =>
    api.get<{ items: PendingAlias[]; total: number }>(
      `/objects/${typeSlug}/aliases/pending?limit=${limit}`,
    ),
  /**
   * 고른 별칭을 **한 번에** 확인하거나 지운다. 한 줄씩 누르게 하면 수백 줄을 끝까지 보는
   * 사람이 없다 — 그러면 검수는 안 한 것과 같다.
   */
  review: (typeSlug: string, ids: string[], action: 'approve' | 'remove') =>
    api.post<{ done: number; refused: string[] }>(`/objects/${typeSlug}/aliases/review`, {
      ids,
      action,
    }),
}

/**
 * 채울 곳 — 서버의 `FillReportOut`(`objects/schemas.py`)과 같은 모양.
 *
 * 생성 타입(`@/shared/api/schema`)을 쓰지 않는다 — 그 파일은 커밋하지 않는 생성물이라 CI 의 빌드
 * 단계에는 아직 없다(생성은 그 뒤 최신성 검사에서). 처음 쓴 판이 그것을 가져와 v0.4.50 CI 가
 * 빌드에서 떨어졌다(2026-10-09) — 이 PC 에는 파일이 있어 푸시 전 검사가 못 봤다.
 */
export interface FillPriority {
  kind: string
  type_slug: string
  type_label: string
  target: string
  target_label: string
  missing: number
  total: number
  score: number
  weight: number
  uses: string[]
  gain: string
  link: string
  estimated: boolean
}

export interface FillField {
  key: string
  label: string
  data_type: string
  required: boolean
  filled: number
  missing: number
  rate: number
  uses: string[]
}

export interface FillRelation {
  relation: string
  label: string
  side: string
  one: boolean
  missing: number
  uses: string[]
}

export interface FillType {
  type_slug: string
  type_label: string
  usage: string
  core: boolean
  objects: number
  estimated: boolean
  sample_rows: number | null
  required_missing: number
  fields: FillField[]
  fields_more: number
  relations: FillRelation[]
  unresolved_refs: number
  waiting_relations: number
  no_alias: number | null
  alias_candidates: number
  uses: string[]
}

export interface FillReport {
  priorities: FillPriority[]
  types: FillType[]
  notes: string[]
}

export const fillApi = {
  /** 어디부터 채우나 — 쓰는 곳(필수 · 지표 · 코어 공개 · 뷰)으로 가중해 줄 세운 것. */
  report: (options: { type?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    if (options.type) params.set('type', options.type)
    if (options.limit) params.set('limit', String(options.limit))
    const query = params.toString()
    return api.get<FillReport>(`/objects/fill-priorities${query ? `?${query}` : ''}`)
  },
}

/** 별칭 후보(못 찾은 말) — 서버의 `AliasCandidateOut` 등과 같은 모양(생성 타입을 안 쓰는 까닭은
 *  위 `FillReport`). */
export interface AliasSuggestion {
  id: string
  type_slug: string
  type_label: string
  label: string
  key: string | null
  matched: string
  matched_text: string
  score: number
}

export interface AliasCandidate {
  id: string
  text: string
  scope: string
  scope_label: string
  scope_kind: string
  hits: number
  people: number
  vias: string[]
  status: string
  object_id: string | null
  object_label: string | null
  object_type_slug: string | null
  first_at: string
  last_at: string
  decided_at: string | null
  suggestions: AliasSuggestion[]
  suggest_note: string
}

export interface AliasCandidatePage {
  items: AliasCandidate[]
  total: number
  limit: number
  offset: number
}

export interface AliasAttachPlan {
  applied: boolean
  candidate: AliasCandidate
  object: { id: string; type_slug: string; type_label: string; label: string; key: string | null }
  value: string
  aliases_before: string[]
  aliases_after: string[]
  warnings: string[]
  blocking: string[]
  closed?: number
}

export const aliasCandidateApi = {
  list: (options: { type?: string; status?: string; limit?: number; suggest?: boolean } = {}) => {
    const params = new URLSearchParams()
    if (options.type !== undefined) params.set('type', options.type)
    if (options.status) params.set('status', options.status)
    if (options.limit) params.set('limit', String(options.limit))
    if (options.suggest === false) params.set('suggest', 'false')
    const query = params.toString()
    return api.get<AliasCandidatePage>(`/objects/alias-candidates${query ? `?${query}` : ''}`)
  },
  /** 그 객체의 별칭으로 — `apply=false` 면 계획만(아무것도 안 바뀐다). */
  attach: (id: string, objectId: string, apply: boolean) =>
    api.post<AliasAttachPlan>(`/objects/alias-candidates/${id}/attach`, {
      object_id: objectId,
      apply,
    }),
  decide: (ids: string[], action: 'ignore' | 'restore') =>
    api.post<{ done: number; refused: string[] }>('/objects/alias-candidates/decide', {
      ids,
      action,
    }),
}

export const viewApi = {
  list: (typeSlug: string) => api.get<SavedView[]>(`/objects/${typeSlug}/views`),
  create: (
    typeSlug: string,
    body: {
      name: string
      query: SavedViewQuery
      workspace_slug?: string | null
      summary?: SavedViewSummary | null
      /** 저장하면서 바로 부서 홈에 올릴지. **한 요청이다** — 둘로 나누면 저장은 됐는데
       *  안 올라간 상태가 생기고, 그때 사람은 무엇을 빠뜨렸는지 모른다. */
      on_home?: boolean
    },
  ) => api.post<SavedView>(`/objects/${typeSlug}/views`, body),
  /** **보낸 것만 바뀐다.** `on_home` 만 보내 홈에 올리거나 내린다. */
  update: (
    typeSlug: string,
    id: string,
    body: {
      name?: string
      query?: SavedViewQuery
      summary?: SavedViewSummary | null
      on_home?: boolean
      /** 홈에서 몇 번째 자리로. 서버가 그 부서의 홈 뷰를 통째로 다시 매긴다. */
      home_position?: number
    },
  ) => api.patch<SavedView>(`/objects/${typeSlug}/views/${id}`, body),
  remove: (typeSlug: string, id: string) => api.delete<void>(`/objects/${typeSlug}/views/${id}`),
  /**
   * 홈에 올라간 것들. 부서를 주면 그 부서만, **안 주면 내 부서 전부.**
   *
   * 사람은 대개 여러 부서에 속하고, 그때 「저 부서 홈에 뭐가 있더라」 를 보려고 부서를
   * 갈아 가며 도는 일이 생긴다.
   */
  home: (workspaceSlug?: string | null) =>
    api.get<HomeWidget[]>(
      workspaceSlug
        ? `/objects/home?workspace=${encodeURIComponent(workspaceSlug)}`
        : '/objects/home',
    ),
}

/** 통계의 막대 하나. `key` 는 필터에 그대로 넣을 수 있는 값(빈 칸이면 null). */
/** 세부 기준으로 나눈 조각 하나 — 세부 기준의 값별로. 합은 그 칸의 `count` 와 맞는다. */
export interface Part {
  key: string | null
  label: string
  count: number
  value: number | null
}

export interface Bucket {
  /** 거르기에 그대로 넣을 값. 날짜 축이면 **기간의 시작일**이다. */
  key: string | null
  label: string
  count: number
  value: number | null
  parts: Part[]
  /** 날짜 축이면 이 칸의 범위 — 막대를 누르면 `gte` · `lt` 두 조건이 된다(`eq` 는 0건). */
  range?: { gte: string; lt: string } | null
  /**
   * 계층 축(`…@<단계>`, ADR 0035)이면 누를 때 거는 조건 — 그 단계의 조상이면 「하위 전체」,
   * 그보다 얕아 자기 자신이 값이면 「같음」. 기준의 주소는 조건의 칸이 아니라서 따로 온다.
   */
  drill?: { field: string; op: ConditionOp } | null
}

/** 묶을 수 있는(또는 셀 수 있는) 축 하나. */
export interface GroupOption {
  field: string
  label: string
  kind: string
  /** 여러 값 칸 — 한 행이 여러 막대에 든다. */
  multi?: boolean
  /** 이어진 것 너머의 기준이면 그 제목 — 「개발사 (시뮬레이션 기업)」. 자기 칸은 비어 있다. */
  heading?: string
}

export interface Summary {
  group_field: string
  group_label: string
  /** desc(큰 값부터) · asc(작은 값부터) · key(키 순 — 날짜는 시간순). */
  order: string
  /** 날짜 축이면 묶은 기간 단위. */
  grain?: string | null
  split_field: string
  split_label: string
  /** 계열의 **차례.** 칸마다 나오는 대로 만들면 첫 칸에 없던 값이 뒤에서 튀어나와 색이 밀린다. */
  splits: string[]
  other_splits: number
  metric: string
  metric_field: string | null
  metric_label: string
  /** 필터를 통과한 **전체 행 수.** 막대의 합과 다르면 그 차이가 「그 밖에」 다. */
  total: number
  buckets: Bucket[]
  other_groups: number
  other_count: number
  /** 기준·세부 기준이 여러 값 칸이면 true — **막대의 합이 total 보다 클 수 있다.** */
  overlap?: boolean
  group_options: GroupOption[]
  metric_options: GroupOption[]
}

/** 점 하나 — 상자 그림의 값, 산점도의 좌표. */
export interface Point {
  id: string
  label: string
  group: string
  x: number | null
  y: number | null
}

export interface Points {
  x_label: string
  y_label: string
  group_label: string
  rows: Point[]
  total: number
  /** 상한을 넘어 잘렸나. **잘렸는데 말 안 하면 그 그림은 「이게 전부」 로 읽힌다.** */
  truncated: boolean
  number_fields: GroupOption[]
  group_options: GroupOption[]
}

/** 통계의 기준 — 그림과 파일이 **같은 주소**를 쓴다(따로 만들면 숫자가 갈린다). */
export interface SummaryOptions {
  groupBy: string
  splitBy?: string | null
  metric?: string
  metricField?: string | null
  order?: string
  /** 날짜 축의 기간 단위 — day · week · month · quarter · year(기본). */
  grain?: string | null
}

/** 원값 그림의 칸. */
export interface PointsOptions {
  x: string
  y?: string | null
  groupBy?: string | null
  grain?: string | null
}

function summaryParams(query: ObjectQuery, options: SummaryOptions): URLSearchParams {
  const params = new URLSearchParams(
    queryString({ ...query, limit: undefined, offset: 0 }).replace(/^\?/, ''),
  )
  params.set('group_by', options.groupBy)
  if (options.splitBy) params.set('split_by', options.splitBy)
  if (options.order) params.set('order', options.order)
  if (options.metric) params.set('metric', options.metric)
  if (options.metricField) params.set('metric_field', options.metricField)
  if (options.grain) params.set('grain', options.grain)
  return params
}

function pointsParams(query: ObjectQuery, options: PointsOptions): URLSearchParams {
  const params = new URLSearchParams(
    queryString({ ...query, limit: undefined, offset: 0 }).replace(/^\?/, ''),
  )
  params.set('x', options.x)
  if (options.y) params.set('y', options.y)
  if (options.groupBy) params.set('group_by', options.groupBy)
  if (options.grain) params.set('grain', options.grain)
  return params
}

/**
 * 이어진 것 너머의 칸 — 조건 고르개가 이 타입 자신의 칸 아래에 제목별로 붙인다.
 *
 * 주소: `ref.<참조 칸>.<칸>` · `out.<관계>` · `out.<관계>.<칸>` · `in.<관계>[.<칸>]`.
 * 통계 기준도 **같은 주소**를 쓴다 — 막대를 누르면 그대로 조건이 된다.
 */
export interface LinkedField {
  field: string
  label: string
  heading: string
  /** `relation` 이면 상대 타입이 하나로 정해지지 않아 있음/없음만 걸 수 있다. */
  data_type: string
  multi: boolean
  enum_options: string[] | null
  ref_type_slug: string | null
  /** 선의 칸(`out.<관계>~<관계 속성>`, ADR 0043) — 같은 관계 묶음으로 묶을 수 있다. */
  edge?: boolean
}

export const objectApi = {
  /** 빈 CSV — 헤더가 「무엇을 채워야 하는지」 를 말한다. */
  template: (typeSlug: string) =>
    downloadFile(`/objects/${typeSlug}/template`, `${typeSlug}-template.csv`),
  /** 지금 거른 목록 그대로 — 쪽 상한 없이 전부. **작업이 된다** — 워커가 파일을 만들면 받는다. */
  export: (typeSlug: string, format: 'csv' | 'json', query: ObjectQuery = {}) => {
    const params = new URLSearchParams(queryString(query).replace(/^\?/, ''))
    params.set('format', format)
    return jobsApi.exportAndDownload(
      () => api.post<Job>(`/objects/${typeSlug}/export?${params.toString()}`),
      `${typeSlug}.${format}`,
    )
  },
  /** 일괄 입력 — `apply=false` 면 계획만. */
  /** 파일 대신 JSON 행으로 — 표에서 타입 생성이 쓴다. 규칙은 파일과 같다. */
  importRows: (
    typeSlug: string,
    rows: Record<string, unknown>[],
    opts: { apply: boolean; workspaceSlug?: string | null },
  ) =>
    api.post<ImportPlan>(`/objects/${typeSlug}/import-rows`, {
      rows,
      apply: opts.apply,
      workspace_slug: opts.workspaceSlug ?? null,
    }),
  /**
   * 일괄 입력는 **작업이 된다**(202). 돌아오는 것은 계획이 아니라 작업이고, 계획은
   * `jobsApi.waitFor` 로 기다린 뒤 `job.result` 에서 꺼낸다. 적용은 `jobsApi.apply(job.id)`.
   */
  import: (
    typeSlug: string,
    file: File,
    opts: {
      workspaceSlug?: string | null
      aliasesMode?: 'add' | 'replace'
      humanEdits?: 'keep' | 'overwrite'
    },
  ) =>
    api.postForm<Job>(
      `/objects/${typeSlug}/import`,
      importForm(file, opts.workspaceSlug, {
        aliasesMode: opts.aliasesMode,
        humanEdits: opts.humanEdits,
      }),
    ),
  exportRelations: (typeSlug: string, format: 'csv' | 'json') =>
    jobsApi.exportAndDownload(
      () => api.post<Job>(`/objects/${typeSlug}/relations/export?format=${format}`),
      `${typeSlug}-relations.${format}`,
    ),
  /**
   * 사진 일괄 업로드 — zip 하나. **작업이 된다**(202): 워커가 파일 이름으로 객체를 찾아 계획을
   * 세우고, 사람이 보고 `jobsApi.apply` 로 적용한다. 칸에 이미 사진이 있으면 `existing` 대로.
   */
  importPhotos: (
    typeSlug: string,
    file: File,
    opts: { field: string; existing: 'skip' | 'replace' | 'add' },
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('field', opts.field)
    body.append('existing', opts.existing)
    return api.postForm<Job>(`/objects/${typeSlug}/photos/import`, body)
  },
  importRelations: (typeSlug: string, file: File, mode: 'add' | 'replace' = 'add') =>
    api.postForm<Job>(
      `/objects/${typeSlug}/relations/import`,
      importForm(file, null, { relationsMode: mode }),
    ),
  /**
   * 고른 것들의 **한 칸**을 바꾼다 — `apply: false`(기본)면 계획만.
   *
   * 한 칸씩인 이유: 여러 칸을 동시에 바꾸면 그 창은 곧 「폼」 이 되고, 실수 한 번의
   * 크기가 수백 배가 된다.
   */
  bulkEdit: (
    typeSlug: string,
    body: { ids: string[]; field: string; value: unknown; apply: boolean },
  ) => api.post<BulkEditPlan>(`/objects/${typeSlug}/bulk-edit`, body),
  /**
   * 고른 것들을 지운다 — `apply: false`(기본)면 계획만.
   *
   * 가리키는 것이 있는 객체는 기본(`block`)으로 거절하고 몇 개가 걸렸는지 말한다.
   * 그래도 지우려면 `mode: 'detach'` — 가리키던 칸이 비고 관계가 끊긴다.
   */
  bulkDelete: (
    typeSlug: string,
    body: { ids: string[]; mode: 'block' | 'detach'; apply: boolean },
  ) => api.post<BulkDeletePlan>(`/objects/${typeSlug}/bulk-delete`, body),
  /**
   * 같이 바뀐 것을 **한 번에** 그때 값으로 — `apply: false` 면 계획만.
   *
   * 그 뒤에 누가 또 고친 행은 덮어쓰지 않고 이유를 적는다.
   */
  bulkEditUndo: (typeSlug: string, batchId: string, apply: boolean) =>
    api.post<BulkEditPlan>(`/objects/${typeSlug}/bulk-edit/${batchId}/undo`, {
      apply,
    }),
  /**
   * **안 센 값들** — 상자 그림과 산점도가 쓴다.
   *
   * 분포는 집계로 안 보인다: 평균이 같은 두 공정이 전혀 다른 모양일 수 있고, 그 차이가
   * 대개 문제의 자리다.
   */
  points: (typeSlug: string, query: ObjectQuery = {}, options: PointsOptions) =>
    api.get<Points>(`/objects/${typeSlug}/points?${pointsParams(query, options).toString()}`),
  /** 상자·산점도에 그린 원값을 파일로 — 행 하나가 객체 하나. */
  exportPoints: (
    typeSlug: string,
    query: ObjectQuery,
    options: PointsOptions,
    format: 'csv' | 'xlsx',
    filename: string,
  ) => {
    const params = pointsParams(query, options)
    params.set('format', format)
    return downloadFile(`/objects/${typeSlug}/points/export?${params.toString()}`, filename)
  },
  /** 이어진 것 너머의 칸 — 「개발사 › 국가」, 「관계 · 사용 부서」. */
  fields: (typeSlug: string) => api.get<LinkedField[]>(`/objects/${typeSlug}/fields`),
  /** 내가 지켜보는 것 — 최근 바뀐 것부터. **알림은 읽으면 사라지지만 이것은 남는다.** */
  watching: (limit = 10) => api.get<Watched[]>(`/objects/watching?limit=${limit}`),
  list: (typeSlug: string, query: ObjectQuery = {}) =>
    api.get<ObjectPage>(`/objects/${typeSlug}${queryString(query)}`),
  /**
   * 통계 — **목록과 같은 필터 위에서.**
   *
   * 필터를 따로 보내면 「목록에는 12건인데 묶어 보면 15건」 이 되고, 그때 어느
   * 쪽이 맞는지 아무도 모른다. 그래서 목록이 쓰는 `ObjectQuery` 를 그대로 받는다.
   */
  summary: (typeSlug: string, query: ObjectQuery = {}, options: SummaryOptions) =>
    api.get<Summary>(`/objects/${typeSlug}/summary?${summaryParams(query, options).toString()}`),
  /**
   * 묶어 본 표를 파일로 — **화면의 그림과 같은 숫자.** 막대를 보고 손으로 옮겨 적으면
   * 그 사이에 틀리고, 틀린 숫자가 회의에 들어간다.
   */
  exportSummary: (
    typeSlug: string,
    query: ObjectQuery,
    options: SummaryOptions,
    format: 'csv' | 'xlsx',
    filename: string,
  ) => {
    const params = summaryParams(query, options)
    params.set('format', format)
    return downloadFile(`/objects/${typeSlug}/summary/export?${params.toString()}`, filename)
  },
  /**
   * 이것이 바뀌면 알려 달라(또는 그만).
   *
   * **여러 번 눌러도 같은 결과다** — 두 번 켠 사람이 두 통을 받지 않는다.
   */
  setWatch: (typeSlug: string, id: string, on: boolean) =>
    api.put<{ watching: boolean; watcher_count: number }>(`/objects/${typeSlug}/${id}/watch`, {
      on,
    }),
  /** 사람이 붙인 다른 이름을 통째로. 같은 타입의 다른 객체가 쓰는 별칭이면 거절된다. */
  setAliases: (typeSlug: string, id: string, aliases: string[]) =>
    api.put<ObjectRow>(`/objects/${typeSlug}/${id}/aliases`, { aliases }),
  /**
   * 「아래 전부」 를 모은 수 — `list_view.rollups` 가 정한 대로. 없으면 빈 목록. 기준일
   * (`asOf`, 비우면 오늘)에 유효한 관계로 펼친다(ADR 0039).
   */
  rollup: (typeSlug: string, id: string, asOf?: string | null) =>
    api.get<Rollup[]>(
      `/objects/${typeSlug}/${id}/rollup${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ''}`,
    ),
  /**
   * BOM 전개(ADR 0040) — 트리 관계의 **수량을 경로 따라 곱한** 줄. 기준일(`asOf`, 비우면 오늘)에
   * 유효한 관계로 펼친다. 트리가 없으면(OBJECTS-0131) · 순환하면(OBJECTS-0130) 409.
   */
  explode: (typeSlug: string, id: string, options: ExplodeOptions) =>
    api.get<ExplodeOut>(`/objects/${typeSlug}/${id}/explode?${explodeParams(options).toString()}`),
  /** 전개를 파일로 — 화면의 표와 같은 열 · 같은 순서. 잘렸으면 파일 끝에 그렇다고 적힌다. */
  exportExplode: (
    typeSlug: string,
    id: string,
    options: ExplodeOptions,
    format: 'csv' | 'xlsx',
    filename: string,
  ) => {
    const params = explodeParams(options)
    params.set('format', format)
    return downloadFile(`/objects/${typeSlug}/${id}/explode/export?${params.toString()}`, filename)
  },
  /**
   * 계보(ADR 0041) — 위(원천 사슬) · 아래(파생물 사슬), 줄마다 낡음과 까닭, 이 객체의 낡음, 영향
   * 범위. 오늘 유효한 계보 관계만, 위 · 아래 각 500줄에서 자른다.
   */
  lineage: (typeSlug: string, id: string) =>
    api.get<LineageOut>(`/objects/${typeSlug}/${id}/lineage`),
  /**
   * 다시 고정 — 이 파생물의 원천 관계의 고정을 원천의 지금 값으로. 원천 객체는 안 바뀌므로 위에서
   * 낡음은 풀리지 않는다. 권한이 없으면 403(OBJECTS-0134).
   */
  repinLineage: (typeSlug: string, id: string, body: LineageRepinRequest) =>
    api.post<LineageRepinOut>(`/objects/${typeSlug}/${id}/lineage/repin`, body),
  tree: (
    typeSlug: string,
    opts: { parent?: string; orphans?: boolean; asOf?: string | null } = {},
  ) => {
    const params = new URLSearchParams()
    if (opts.parent) params.set('parent', opts.parent)
    if (opts.orphans) params.set('orphans', 'true')
    if (opts.asOf) params.set('as_of', opts.asOf)
    const text = params.toString()
    return api.get<TreeOut>(`/objects/${typeSlug}/tree${text ? `?${text}` : ''}`)
  },
  /**
   * 상세. 관련 객체는 기준일(`asOf`, 비우면 오늘)에 유효한 관계만 — `allPeriods` 면 지난 · 예정
   * 관계까지 모두(줄마다 `period_state`).
   */
  profile: (
    typeSlug: string,
    id: string,
    opts: { asOf?: string | null; allPeriods?: boolean } = {},
  ) => {
    const params = new URLSearchParams()
    if (opts.asOf) params.set('as_of', opts.asOf)
    if (opts.allPeriods) params.set('all_periods', 'true')
    const text = params.toString()
    return api.get<ObjectProfile>(`/objects/${typeSlug}/${id}${text ? `?${text}` : ''}`)
  },
  create: (typeSlug: string, body: Record<string, unknown>) =>
    api.post<ObjectRow>(`/objects/${typeSlug}`, body),
  update: (typeSlug: string, id: string, body: Record<string, unknown>) =>
    api.patch<ObjectRow>(`/objects/${typeSlug}/${id}`, body),
  /** 이 객체의 이력 — 최근 것이 앞. 볼 수 있는 사람이면 누구나. */
  history: (typeSlug: string, id: string) =>
    api.get<HistoryEntry[]>(`/objects/${typeSlug}/${id}/history`),
  /** 그 시점 값으로 고친다 — 저장과 같은 검증을 거쳐서. */
  restore: (typeSlug: string, id: string, entryId: string) =>
    api.post<ObjectRow>(`/objects/${typeSlug}/${id}/restore`, {
      entry_id: entryId,
    }),
  /** 삭제 전에 — 이 객체를 가리키는 것. */
  references: (typeSlug: string, id: string) =>
    api.get<References>(`/objects/${typeSlug}/${id}/references`),
  /** 비슷한 기록(ADR 0022) — 축 태그(참조 칸)가 많이 겹치는 같은 타입의 기록. */
  similar: (typeSlug: string, id: string) => api.get<Similar>(`/objects/${typeSlug}/${id}/similar`),
  /** `block`(기본)은 가리키는 것이 있으면 409. `detach` 는 참조를 비우고 관계를 끊고 지운다. */
  remove: (typeSlug: string, id: string, mode: 'block' | 'detach' = 'block') =>
    api.delete<void>(`/objects/${typeSlug}/${id}?mode=${mode}`),
  /** 다른 객체에 합치고 지운다 — 참조·관계가 이긴 쪽으로. */
  merge: (typeSlug: string, id: string, into: string) =>
    api.post<MergeResult>(`/objects/${typeSlug}/${id}/merge`, { into }),
  /**
   * 가리키는 기록이 많은 객체(OBJECTS-96 — 인기 모델은 10만 건)는 합치기 · 참조 비우고 지우기를
   * **작업으로** 한다. 작업의 결과는 같은 모양이다.
   */
  mergeJob: (typeSlug: string, id: string, into: string) =>
    api.post<Job>(`/objects/${typeSlug}/${id}/merge/job`, { into }),
  detachJob: (typeSlug: string, id: string) =>
    api.post<Job>(`/objects/${typeSlug}/${id}/detach/job`, {}),

  addRelation: (typeSlug: string, id: string, body: Record<string, unknown>) =>
    api.post<RelatedObject>(`/objects/${typeSlug}/${id}/relations`, body),
  updateRelation: (
    typeSlug: string,
    id: string,
    relationId: string,
    body: Record<string, unknown>,
  ) => api.patch<RelatedObject>(`/objects/${typeSlug}/${id}/relations/${relationId}`, body),
  removeRelation: (typeSlug: string, id: string, relationId: string) =>
    api.delete<void>(`/objects/${typeSlug}/${id}/relations/${relationId}`),
  /** 이 객체에 걸린 **열린** 제안(ADR 0042) — 양방향, 새것부터. 저쪽 끝을 못 보면 빠진다. */
  relationProposals: (typeSlug: string, id: string) =>
    api.get<RelationProposal[]>(`/objects/${typeSlug}/${id}/relation-proposals`),
  /**
   * 이 객체에서 출발하는 관계를 **제안**한다 — 양 끝을 볼 수만 있으면 된다. 만들지 않은 까닭(이미
   * 연결됨 · 이미 열린 제안 · 거절된 적 있음)도 결과로 온다.
   */
  proposeRelation: (typeSlug: string, id: string, body: RelationProposalCreate) =>
    api.post<RelationProposalResult>(`/objects/${typeSlug}/${id}/relation-proposals`, body),
  /** 확정 — 「관계 추가」 와 같은 검사. 막히면 409(OBJECTS-0141 · 0143)와 까닭. */
  acceptProposal: (proposalId: string, note = '') =>
    api.post<RelationProposal>(`/objects/relation-proposals/${proposalId}/accept`, { note }),
  /** 거절 — 까닭은 같은 제안이 다시 올 때 그대로 보인다. */
  rejectProposal: (proposalId: string, note = '') =>
    api.post<RelationProposal>(`/objects/relation-proposals/${proposalId}/reject`, { note }),
  /** 철회 — 제안한 사람이 열린 제안을 삭제한다. */
  withdrawProposal: (proposalId: string) =>
    api.delete<void>(`/objects/relation-proposals/${proposalId}`),

  years: (typeSlug: string, id: string) => api.get<number[]>(`/objects/${typeSlug}/${id}/years`),
  /** **통째로** 정한다 — 화면이 보여 준 것과 저장되는 것이 같아야 한다. */
  setYears: (typeSlug: string, id: string, years: number[]) =>
    api.put<number[]>(`/objects/${typeSlug}/${id}/years`, years),
}

/** 비슷한 기록의 태그 하나 — 참조 칸과 가리키는 객체, 무게(드물수록 크다). */
export interface SimilarTag {
  field: string
  field_label: string
  value: string
  value_label: string | null
  weight: number
  records: number
}

/** 비슷한 기록(ADR 0022) — 축 태그가 많이 겹치는 기록과 겹친 태그. */
export interface Similar {
  type_slug: string
  total: number
  fields: string[]
  query: SimilarTag[]
  items: {
    id: string
    key: string | null
    label: string
    status: string
    /** 무게를 단 자카드 — 0 ~ 1. */
    score: number
    shared: SimilarTag[]
    extra: number
  }[]
}
