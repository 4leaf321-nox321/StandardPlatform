/**
 * 온톨로지 관리의 **두 번째 사이드바.**
 *
 * 한 화면에 쌓으면 스크롤이 길어지고, 「지금 어디를 보고 있나」 를 화면이 말해
 * 주지 못한다. 왼쪽에 줄을 하나씩 두고 각각을 제 화면에서 본다 — 지금 자리가
 * 굵게 서 있으므로 물을 일이 없다.
 *
 * **스키마는 여기서 한 번 받는다.** 화면마다 받으면 묶음을 만들고 타입 화면으로
 * 옮겼을 때 방금 만든 것이 안 보이는 순간이 생기고, 그것은 저장이 안 된 것과
 * 구별되지 않는다.
 */

import { useState } from 'react'
import { NavLink, Outlet, useOutletContext } from 'react-router-dom'
import { Boxes, LayoutList, Share2, Upload } from 'lucide-react'

import { OntologyExportButton } from '@/modules/ontology/OntologyExportButton'
import { ontologyApi } from '@/modules/ontology/api'
import type { OntologySchema } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { useResource } from '@/shared/hooks/useResource'
import { cn } from '@/shared/lib/utils'

export interface OntologyContext {
  schema: OntologySchema | null
  reload: () => void
  /** 화면들이 같은 자리에 오류를 세우도록 위로 올린다. */
  setError: (error: Error | null) => void
}

export function useOntology(): OntologyContext {
  return useOutletContext<OntologyContext>()
}

/** 두 번째 사이드바의 줄. **묶음이 먼저다** — 타입은 묶음 안에 들어간다. */
const SECTIONS = [
  {
    to: 'groups',
    label: '사이드바 묶음',
    icon: Boxes,
    count: (schema: OntologySchema | null) => schema?.groups.length,
  },
  {
    to: 'types',
    label: '타입',
    icon: LayoutList,
    count: (schema: OntologySchema | null) => schema?.types.length,
  },
  {
    // 타입 다음이다 — **관계는 타입과 타입을 잇는다.** 이을 것이 없으면 먼저
    // 만들 것이 타입이다.
    to: 'relations',
    label: '관계 종류',
    icon: Share2,
    count: (schema: OntologySchema | null) => schema?.relation_types.length,
  },
  {
    // **기계가 정의를 만드는 자리.** 사람이 한 칸씩 만드는 길과 나란히 둔다.
    to: 'import',
    label: '가져오기·이력',
    icon: Upload,
    count: () => undefined,
  },
]

export default function OntologyLayout() {
  const resource = useResource(() => ontologyApi.schema(), [])
  const [error, setError] = useState<Error | null>(null)

  const context: OntologyContext = {
    schema: resource.data,
    reload: resource.reload,
    setError,
  }

  return (
    <div className="flex min-h-full gap-6">
      <aside className="w-48 shrink-0 border-r pr-3">
        <nav className="space-y-0.5">
          {SECTIONS.map((section) => {
            const count = section.count(resource.data)
            return (
              <NavLink
                key={section.to}
                to={section.to}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2 rounded-md px-2.5 py-2 text-sm',
                    isActive
                      ? 'bg-accent text-accent-foreground font-medium'
                      : 'text-muted-foreground hover:bg-accent/50',
                  )
                }
              >
                <section.icon className="size-4 shrink-0" />
                <span className="flex-1">{section.label}</span>
                {/* **숫자를 함께 둔다.** 비어 있는 자리를 눌러 보고 알게 하지 않는다. */}
                {count !== undefined && (
                  <span className="text-muted-foreground text-xs tabular-nums">{count}</span>
                )}
              </NavLink>
            )
          })}
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        <PageHeader
          title="온톨로지"
          description="타입을 정의하면 사이드바와 화면이 생깁니다. 코드를 수정하지 않습니다."
          actions={<OntologyExportButton onError={setError} />}
        />

        {resource.error && <ErrorNotice error={resource.error} />}
        {error && <ErrorNotice error={error} />}

        <Outlet context={context} />
      </div>
    </div>
  )
}
