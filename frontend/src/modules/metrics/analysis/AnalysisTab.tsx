/**
 * 「분석」 탭 — 세어 둔 셀 위의 통계(ADR 0014). **계산은 서버가, 화면은 읽고 보인다.**
 *
 * 레시피마다 되는지 · 안 되면 왜인지는 서버가 정의를 보고 말한다(`metric.analyses`) — 화면이
 * 짐작해 버튼을 숨기면 「왜 이 분석이 없지」 를 물을 데가 없다. 그래서 안 되는 것도 보이고,
 * 그 이유를 적는다.
 *
 * 그림 도구(plotly)가 무거워 이 탭은 열 때만 받는다(상세 화면이 `lazy` 로 부른다).
 */

import { useState } from 'react'

import type { Metric, ReadOptions } from '@/modules/metrics/api'
import { ChangesView } from '@/modules/metrics/analysis/ChangesView'
import { ControlView } from '@/modules/metrics/analysis/ControlView'
import { LifeView } from '@/modules/metrics/analysis/LifeView'
import { LogitView } from '@/modules/metrics/analysis/LogitView'
import { ParetoView } from '@/modules/metrics/analysis/ParetoView'
import { SprtView } from '@/modules/metrics/analysis/SprtView'
import { Button } from '@/shared/components/ui/button'

/** 화면에 늘어놓는 차례 — 물음 번호(②③④⑥⑦⑩)대로. */
const ORDER = ['life', 'control', 'sprt', 'logit', 'pareto', 'changes']

export interface AnalysisTabProps {
  metric: Metric
  /** 상세 화면의 기준 거르기 · 기간 — 분석에도 그대로 건다. */
  read: ReadOptions
}

export default function AnalysisTab({ metric, read }: AnalysisTabProps) {
  const listed = [...metric.analyses].sort(
    (a, b) => ORDER.indexOf(a.recipe) - ORDER.indexOf(b.recipe),
  )
  const [recipe, setRecipe] = useState<string | null>(
    () => listed.find((one) => one.ok)?.recipe ?? null,
  )
  const unavailable = listed.filter((one) => !one.ok)
  // 기간 범위는 접수 기간이다 — 수명 · 순차 검정은 코호트마다 닫힌 경과까지 보므로 안 쓴다.
  const cohortOnly = recipe === 'life' || recipe === 'sprt'
  const ranged = Boolean(read.period_from || read.period_to)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1" role="group" aria-label="분석 고르기">
        {listed.map((one) => (
          <Button
            key={one.recipe}
            size="sm"
            variant={recipe === one.recipe ? 'default' : 'outline'}
            aria-pressed={recipe === one.recipe}
            disabled={!one.ok}
            title={one.reason ?? undefined}
            onClick={() => setRecipe(one.recipe)}
          >
            {one.label}
          </Button>
        ))}
      </div>
      {unavailable.length > 0 && (
        <ul className="text-muted-foreground space-y-0.5 text-xs" aria-label="안 되는 분석">
          {unavailable.map((one) => (
            <li key={one.recipe}>
              {one.label} — {one.reason}
            </li>
          ))}
        </ul>
      )}
      {recipe === null && (
        <p className="text-muted-foreground text-sm">이 지표에 되는 분석이 없습니다.</p>
      )}
      {cohortOnly && ranged && (
        <p className="text-muted-foreground text-xs">
          기간 범위는 이 분석에 쓰지 않습니다 — 코호트마다 닫힌 경과까지 봅니다.
        </p>
      )}
      {recipe === 'pareto' && <ParetoView metric={metric} read={read} />}
      {recipe === 'life' && <LifeView metric={metric} read={read} />}
      {recipe === 'control' && <ControlView metric={metric} read={read} />}
      {recipe === 'changes' && <ChangesView metric={metric} read={read} />}
      {recipe === 'sprt' && <SprtView metric={metric} read={read} />}
      {recipe === 'logit' && <LogitView metric={metric} read={read} />}
    </div>
  )
}
