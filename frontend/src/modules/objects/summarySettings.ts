/**
 * 통계 설정 — **패널과 따로 산다.**
 *
 * 축·차트 종류를 들고 있는 쪽은 목록 화면이다(뷰를 불러오면 그 뷰의 축으로 열려야
 * 하고, 저장할 때는 지금 축이 함께 담겨야 하니까). 그런데 그 형과 기본값이
 * `SummaryPanel` 안에 있으면, 목록 화면이 패널을 **정적으로** 가져오게 된다 —
 * `lazy()` 로 감싼 것이 무색해지고 차트 라이브러리가 목록을 열 때마다 함께 내려온다
 * (rolldown 이 `INEFFECTIVE_DYNAMIC_IMPORT` 로 알려 준다).
 *
 * 그래서 **그림이 없는 이 조각만** 따로 둔다. 여기에는 값만 있고 import 는 하나뿐이다.
 */

import type { ChartKind } from '@/shared/charts'

export interface SummarySettings {
  groupBy: string
  /** 두 번째 축. 비면 계열이 하나다. */
  splitBy: string
  metric: string
  metricField: string | null
  chart: ChartKind | 'heatmap' | 'box' | 'scatter'
  /** 원값 그림이 쓰는 숫자 칸 — 상자는 x 하나, 산점도는 x·y 둘. */
  x: string
  y: string
  stacked: boolean
  /** desc(큰 값부터) · asc(작은 값부터). 「가장 낮은 것」 을 찾는 물음이 따로 있다. */
  order: string
}

export const DEFAULT_SUMMARY: SummarySettings = {
  groupBy: 'status',
  splitBy: '',
  metric: 'count',
  metricField: null,
  chart: 'bar',
  x: '',
  y: '',
  stacked: false,
  order: 'desc',
}
