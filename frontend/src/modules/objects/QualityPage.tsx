/**
 * 데이터 품질 — **나빠지고 있으면 여기 뜬다.**
 *
 * 넷을 센다: 필수값이 빈 객체 · 관계 없는 객체 · 지워진 것을 가리키는 칸 · 이름이 같은
 * 객체. 홈 「남은 일」 의 수가 여기로 온다(`#kind`). 종류마다, 타입마다 묶고, 줄을
 * 누르면 그 객체로 간다 — 고치는 것은 상세 화면의 몫이다(이름 같은 것은 「병합」).
 *
 * **볼 수 있는 것만 센다.** 남의 부서 것을 세어 주면 수가 새고, 고칠 수도 없다.
 */

import { useEffect, useMemo } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { AlertTriangle, ShieldCheck } from 'lucide-react'

import { qualityApi } from '@/modules/objects/api'
import type { QualityFinding } from '@/modules/objects/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/components/ui/badge'
import { useResource } from '@/shared/hooks/useResource'

const KIND_ORDER: QualityFinding['kind'][] = [
  'broken_ref',
  'missing_required',
  'duplicate',
  'alias_clash',
  'orphan',
]

const KIND_HINT: Record<QualityFinding['kind'], string> = {
  broken_ref:
    '가리키던 객체가 지워졌습니다. 그 칸을 비우거나 다른 것으로 바꾸세요 — 화면에는 뜻 모를 값으로 표시됩니다.',
  missing_required: '필수가 된 뒤에도 안 채운 옛 객체입니다. 수정할 때 거절되니 먼저 채우세요.',
  duplicate:
    '이름을 정규화(공백·대소문자·전각)하면 같은 것들입니다. 같은 것이면 한쪽 상세에서 「병합」 로 하나로.',
  orphan:
    '관계가 하나도 안 걸린 객체입니다. 관계가 정의된 타입에서만 셉니다 — 정말 홀로 있는 것인지 보세요.',
  alias_clash:
    '한 객체의 별칭이 다른 객체의 이름·식별자와 같습니다. 그 표기로 찾으면 둘이 나옵니다 — 같은 것이면 합치고, 다른 것이면 별칭을 지우세요.',
}

export default function QualityPage() {
  const report = useResource(() => qualityApi.report(), [])
  const location = useLocation()

  // 홈에서 `#kind` 로 들어오면 그 묶음으로 내려간다.
  useEffect(() => {
    if (!report.data || !location.hash) return
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: 'start' })
  }, [report.data, location.hash])

  const grouped = useMemo(() => {
    const out = new Map<QualityFinding['kind'], QualityFinding[]>()
    for (const one of report.data?.findings ?? []) {
      if (!out.has(one.kind)) out.set(one.kind, [])
      out.get(one.kind)!.push(one)
    }
    return out
  }, [report.data])

  const total = (report.data?.findings ?? []).reduce((sum, one) => sum + one.count, 0)

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader
        title="데이터 품질"
        description="검증은 넣을 때만 걸립니다. 그 뒤에 나빠진 것을 여기서 셉니다 — 내가 볼 수 있는 것만."
      />
      {report.error && <ErrorNotice error={report.error} />}
      {report.data && total === 0 && (
        <EmptyState
          title="해당 항목이 없습니다"
          hint="필수값이 빈 것, 관계 없는 것, 지워진 것을 가리키는 칸, 이름이 같은 것 — 넷 다 없습니다."
        />
      )}
      {KIND_ORDER.map((kind) => {
        const findings = grouped.get(kind)
        if (!findings?.length) return null
        const label = findings[0].kind_label
        const count = findings.reduce((sum, one) => sum + one.count, 0)
        return (
          <section key={kind} id={kind} className="scroll-mt-4 space-y-3">
            <h2 className="flex items-center gap-2 text-base font-medium">
              {kind === 'broken_ref' ? (
                <AlertTriangle className="size-4 text-amber-500" />
              ) : (
                <ShieldCheck className="text-muted-foreground size-4" />
              )}
              {label}
              <Badge variant={kind === 'broken_ref' ? 'destructive' : 'secondary'}>{count}</Badge>
            </h2>
            <p className="text-muted-foreground text-sm">{KIND_HINT[kind]}</p>
            {findings.map((one) => (
              <div key={`${one.kind}:${one.type_slug}`} className="rounded-md border">
                <div className="flex items-center justify-between border-b px-3 py-2 text-sm">
                  <Link to={`/o/${one.type_slug}`} className="font-medium hover:underline">
                    {one.type_label}
                  </Link>
                  <span className="text-muted-foreground">
                    {one.count}개
                    {one.count > one.hits.length && ` · 아래는 ${one.hits.length}개까지`}
                  </span>
                </div>
                <ul className="divide-y text-sm">
                  {one.hits.map((hit) => (
                    <li key={hit.id}>
                      <Link
                        to={`/o/${one.type_slug}/${hit.id}`}
                        className="hover:bg-muted/60 flex items-baseline justify-between gap-3 px-3 py-1.5"
                      >
                        <span className="min-w-0 truncate">
                          {hit.label}
                          {hit.key && (
                            <span className="text-muted-foreground ml-1 font-mono text-xs">
                              {hit.key}
                            </span>
                          )}
                        </span>
                        <span className="text-muted-foreground shrink-0 text-xs">{hit.detail}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        )
      })}
    </div>
  )
}
