/**
 * 정적 그리기의 거절 — **상한을 넘으면 그리지 않고 수를 말한다**(ADR 0045).
 *
 * 반쯤 그린 그림은 「이게 전부」 로 읽힌다 — 탐색 그림의 「+N」 이 막던 바로 그 거짓말이다. 그래서
 * 넘으면 그림 대신 무엇이 얼마나 많은지(타입별 · 단계별 · 선 종류별)를 보이고, 좁히는 길을 단추로
 * 낸다. 이 파일은 가볍게 둔다 — 거절을 보이려고 그리기 라이브러리(sigma)를 받을 까닭이 없다.
 */

import { Ban, Map as MapIcon } from 'lucide-react'

import type { WholeOver } from '@/modules/graph/api'
import { Button } from '@/shared/components/ui/button'

interface WholeRefusalProps {
  over: WholeOver
  depth: number
  /** 씨앗 타입 — 제외하고 다시 그릴 수 없는 타입(씨앗은 늘 든다). */
  seedTypes: string[]
  /** 단계를 하나 줄여 다시. 0단계면 없다. */
  onLessDepth?: () => void
  /** 이 타입을 이웃에서 제외하고 다시(「이웃 타입」 필터). */
  onExclude?: (slug: string) => void
  /** 정적 그리기를 끄고 탐색으로 — 한 걸음씩 펼친다. */
  onOff: () => void
  /** 전체 지도에서 보기(ADR 0046) — 거절된 범위의 타입만 켜고, 객체 씨앗이면 그 객체로. */
  onMap?: () => void
}

/** 표에 보일 타입 수 — 나머지는 「그 밖」 으로 합친다. */
const SHOWN_TYPES = 8

export function WholeRefusal({
  over,
  depth,
  seedTypes,
  onLessDepth,
  onExclude,
  onOff,
  onMap,
}: WholeRefusalProps) {
  const nodes = over.what === 'nodes'
  const count = `${over.count.toLocaleString()}개${over.exact ? '' : ' 이상'}`
  const shown = over.types.slice(0, SHOWN_TYPES)
  const rest = over.types.slice(SHOWN_TYPES).reduce((sum, one) => sum + one.count, 0)
  // 제외하면 가장 많이 줄어드는 타입 — 씨앗 타입은 늘 들어가므로 제외할 수 없다.
  const biggest = over.types.find((one) => !seedTypes.includes(one.slug)) ?? null
  return (
    <div className="flex min-h-0 flex-1 items-start justify-center overflow-y-auto rounded-md border p-6">
      <div className="w-full max-w-xl space-y-4 text-sm" role="status">
        <div className="flex items-start gap-2">
          <Ban className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div>
            <p className="font-medium">
              {nodes ? '노드' : '선'} {count} — 정적 그리기 상한({over.limit.toLocaleString()}
              개)을 넘습니다
            </p>
            <p className="text-muted-foreground mt-1">
              반쯤 그린 그림은 「이게 전부」 로 읽혀서 넘으면 그리지 않습니다.
              {over.exact ? '' : ' 상한을 넘은 자리에서 세기를 멈췄습니다.'} 아래 수를 보고 범위를
              좁히세요.
            </p>
          </div>
        </div>

        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-1 text-left font-normal">타입</th>
              <th className="py-1 text-right font-normal">센 노드</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((one) => (
              <tr key={one.slug} className="border-t">
                <td className="py-1">{one.label}</td>
                <td className="py-1 text-right tabular-nums">{one.count.toLocaleString()}</td>
              </tr>
            ))}
            {rest > 0 && (
              <tr className="text-muted-foreground border-t">
                <td className="py-1">그 밖 {over.types.length - SHOWN_TYPES}개 타입</td>
                <td className="py-1 text-right tabular-nums">{rest.toLocaleString()}</td>
              </tr>
            )}
          </tbody>
        </table>

        {over.layers.length > 1 && (
          <p className="text-muted-foreground text-xs">
            단계별:{' '}
            {over.layers
              .map(
                (n, at) =>
                  `${at}단계 ${n.toLocaleString()}${
                    nodes && !over.exact && at === over.layers.length - 1 ? '(세다가 멈춤)' : ''
                  }`,
              )
              .join(' · ')}
          </p>
        )}
        {!nodes && over.relations.length > 0 && (
          <p className="text-muted-foreground text-xs">
            선 종류별:{' '}
            {over.relations
              .slice(0, SHOWN_TYPES)
              .map((one) => `${one.label} ${one.count.toLocaleString()}`)
              .join(' · ')}
          </p>
        )}

        <div className="space-y-2">
          <p className="text-muted-foreground text-xs">
            왼쪽의 「관계 종류」 · 「이웃 타입」 필터로 범위를 좁히거나 단계를 줄이면 그림이
            작아집니다. 좁히지 않고 전부를 보려면 「지도에서 보기」 — 서버가 미리 놓아 둔 지도에서
            멀리서는 밀도로, 확대하면 점 하나하나로 봅니다.
          </p>
          <div className="flex flex-wrap gap-2">
            {onMap && (
              <Button size="sm" onClick={onMap}>
                <MapIcon className="mr-1 size-3.5" />
                지도에서 보기
              </Button>
            )}
            {biggest && onExclude && (
              <Button size="sm" variant="outline" onClick={() => onExclude(biggest.slug)}>
                「{biggest.label}」 제외하고 다시 그리기
              </Button>
            )}
            {onLessDepth && depth > 0 && (
              <Button size="sm" variant="outline" onClick={onLessDepth}>
                {depth - 1}단계로 줄이기
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={onOff}>
              정적 그리기 해제 — 한 걸음씩 탐색
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
