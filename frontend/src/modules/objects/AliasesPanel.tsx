/**
 * 다른 이름(별칭) — **같은 것을 다르게 불러도 같은 것으로 풀리게.**
 *
 * 「Ansys」 를 「앤시스」 「ANSYS Inc.」 로도 부른다. 별칭을 두면 찾기·참조 풀이·파일·동기화가
 * 그것으로도 찾는다 — 못 찾은 사람이 새로 만들어 같은 것이 둘이 되는 일을 막는다.
 * 바깥 시스템의 식별자(동기화가 남김)는 보기만 한다.
 */

import { useState } from 'react'
import { Plus, Tags, X } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'

interface Props {
  typeSlug: string
  objectId: string
  aliases: string[]
  externalIds: Record<string, string>
  canEdit: boolean
  onChanged: () => void
}

export function AliasesPanel({
  typeSlug,
  objectId,
  aliases,
  externalIds,
  canEdit,
  onChanged,
}: Props) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const external = Object.entries(externalIds)

  async function save(next: string[]) {
    setBusy(true)
    setError(null)
    try {
      await objectApi.setAliases(typeSlug, objectId, next)
      setDraft('')
      onChanged()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  if (!canEdit && aliases.length === 0 && external.length === 0) return null

  return (
    <section className="space-y-2">
      <h2 className="flex items-center gap-2 text-base font-semibold">
        <Tags className="size-4" />
        다른 이름
        <span className="text-muted-foreground text-sm font-normal">
          이 이름으로도 찾고, 파일·동기화가 이것으로도 풉니다
        </span>
      </h2>
      {error && <ErrorNotice error={error} />}
      <div className="flex flex-wrap items-center gap-2">
        {aliases.map((one) => (
          <span
            key={one}
            className="bg-muted inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-sm"
          >
            {one}
            {canEdit && (
              <button
                type="button"
                aria-label={`별칭 ${one} 지우기`}
                className="text-muted-foreground hover:text-foreground"
                disabled={busy}
                onClick={() => save(aliases.filter((item) => item !== one))}
              >
                <X className="size-3" />
              </button>
            )}
          </span>
        ))}
        {aliases.length === 0 && !canEdit && (
          <span className="text-muted-foreground text-sm">없음</span>
        )}
        {canEdit && (
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault()
              if (draft.trim()) void save([...aliases, draft.trim()])
            }}
          >
            <Input
              value={draft}
              placeholder="다른 이름 더하기"
              className="h-8 w-48 text-sm"
              disabled={busy}
              onChange={(event) => setDraft(event.target.value)}
            />
            <Button type="submit" size="sm" variant="outline" disabled={busy || !draft.trim()}>
              <Plus className="size-3.5" />
            </Button>
          </form>
        )}
      </div>
      {external.length > 0 && (
        <p className="text-muted-foreground text-xs">
          바깥 식별자:{' '}
          {external.map(([source, value]) => (
            <span key={source} className="mr-2">
              <code>{source}</code> = <code>{value}</code>
            </span>
          ))}
        </p>
      )}
    </section>
  )
}
