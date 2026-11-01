/**
 * 선의 hover 말 — 이름(방향이 있으면 「개발사 ↔ 개발함」)과, 유효 기간이 든 선이면 그 기간(ADR
 * 0039). 지식 그래프 화면과 상세의 작은 그림이 같은 말을 쓴다.
 */

import type { GraphEdge } from '@/modules/graph/api'
import { hasPeriod, periodText } from '@/modules/objects/periods'

export function edgeTooltip(edge: GraphEdge): string {
  const name =
    edge.directed && edge.inverse_label && edge.inverse_label !== edge.label
      ? `${edge.label} ↔ ${edge.inverse_label}`
      : edge.label
  return hasPeriod(edge.valid_from, edge.valid_to)
    ? `${name} · ${periodText(edge.valid_from, edge.valid_to)}`
    : name
}
