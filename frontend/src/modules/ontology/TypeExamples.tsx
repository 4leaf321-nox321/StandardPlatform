/**
 * 타입의 **예시 객체** — 정의 화면에서 「이 타입이 실제로 무엇을 담나」 를 바로 본다.
 *
 * 정의만 보면 칸 이름의 뜻이 안 잡힌다(`base_code` 가 뭔지는 값을 보아야 안다). 그래서 최근
 * 것 몇 개를 값과 함께 보여 주고, 각 줄은 객체 화면으로, 끝은 목록으로 간다.
 */

import { Link } from 'react-router-dom'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { objectApi } from '@/modules/objects/api'
import type { ObjectRow } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { useResource } from '@/shared/hooks/useResource'

const SAMPLE = 5
const VALUE_COLUMNS = 4

function shown(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.map(shown).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function TypeExamples({ type }: { type: ObjectType & { properties: PropertyDef[] } }) {
  const page = useResource(() => objectApi.list(type.slug, { limit: SAMPLE }), [type.slug])
  if (page.error) return <ErrorNotice error={page.error} />
  if (!page.data) return <p className="text-muted-foreground text-xs">불러오는 중…</p>

  const rows: ObjectRow[] = page.data.items
  // 값이 있는 칸부터 — 빈 열만 보이면 예시가 아니다.
  const keys: string[] = type.properties
    .map((p: PropertyDef) => p.key)
    .filter((key: string) => rows.some((row) => shown(row.properties[key]) !== '—'))
    .slice(0, VALUE_COLUMNS)

  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        아직 객체가 없습니다.{' '}
        <Link to={`/o/${type.slug}`} className="underline">
          목록으로
        </Link>
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 text-left font-medium">이름</th>
            {type.key_policy !== 'none' && <th className="py-1 text-left font-medium">식별자</th>}
            {keys.map((key) => (
              <th key={key} className="py-1 text-left font-mono font-medium">
                {key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-t">
              <td className="py-1 pr-3">
                <Link
                  to={`/o/${type.slug}/${row.id}`}
                  className="underline-offset-2 hover:underline"
                >
                  {row.label}
                </Link>
              </td>
              {type.key_policy !== 'none' && (
                <td className="py-1 pr-3 font-mono">{row.key ?? '—'}</td>
              )}
              {keys.map((key) => (
                <td
                  key={key}
                  className="max-w-48 truncate py-1 pr-3"
                  title={shown(row.properties[key])}
                >
                  {shown(row.properties[key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground text-xs">
        최근 {rows.length}개{page.data.total > rows.length ? ` / 전체 ${page.data.total}개` : ''} ·{' '}
        <Link to={`/o/${type.slug}`} className="underline">
          목록 전체 보기
        </Link>
      </p>
    </div>
  )
}
