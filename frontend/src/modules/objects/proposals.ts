/**
 * 제안된 관계(ADR 0042)의 말 — 출처 이름 · 확신도 · 저쪽 끝. 관련 객체와 관계 추가 창이 같은 말을
 * 쓴다(용어 정본 `docs/용어.md` — 「출처 · 출처 글」 · 「확신도」).
 */

import type { ProposalOrigin, RelationProposal } from '@/modules/objects/api'

/** 출처 — 누가 · 무엇이 제안했나. 서버의 `ORIGIN_LABELS` 와 같다. */
export const ORIGIN_LABELS: Record<ProposalOrigin, string> = {
  user: '사람',
  mcp: 'AI(MCP)',
  rule: '규칙',
  datasource: '데이터 소스',
  import: '파일 가져오기',
}

/** 「AI(MCP) 「대화 #12」 · 홍길동」 — 출처 · 출처 글 · 제안한 사람. 모르는 출처는 그대로. */
export function originText(row: RelationProposal): string {
  const who = ORIGIN_LABELS[row.origin as ProposalOrigin] ?? row.origin
  const ref = row.origin_ref ? ` 「${row.origin_ref}」` : ''
  const by = row.proposed_by_label ? ` · ${row.proposed_by_label}` : ''
  return `${who}${ref}${by}`
}

/** 확신도 — 소수 둘째 자리까지, 없으면 「없음」. */
export function confidenceText(value: number | null): string {
  return value === null ? '없음' : value.toFixed(2)
}

/** 보는 객체에서 본 **저쪽 끝** — 내가 출발이면 도착, 아니면 출발. */
export function otherEnd(row: RelationProposal) {
  return row.outgoing
    ? {
        id: row.dst_object_id,
        label: row.dst_label,
        key: row.dst_key,
        typeSlug: row.dst_type_slug,
        typeLabel: row.dst_type_label,
      }
    : {
        id: row.src_object_id,
        label: row.src_label,
        key: row.src_key,
        typeSlug: row.src_type_slug,
        typeLabel: row.src_type_label,
      }
}
