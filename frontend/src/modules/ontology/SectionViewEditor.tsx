/**
 * 폼·상세의 **묶음 순서와 모양**을 정한다.
 *
 * **소속은 여기서 안 정한다** — 속성 정의의 「묶음」 이 들고 있다. 뷰가 소속까지
 * 정하면 두 벌이 되고, 갈린 두 벌은 한쪽만 고쳐진다.
 *
 * 속성이 40개인 타입을 생각하면 왜 필요한지 분명하다: 폼이 40칸 일렬로 서면
 * **사람은 그 폼을 안 채운다.**
 */

import { ArrowDown, ArrowUp } from 'lucide-react'

import type { PropertyDef, SectionView } from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

interface Props {
  defs: PropertyDef[]
  value: SectionView
  onChange: (next: SectionView) => void
  /** 「폼 화면」 인지 「상세 화면」 인지. 안내 문구가 갈린다. */
  what: string
}

export function SectionViewEditor({ defs, value, onChange, what }: Props) {
  /** 속성이 실제로 들고 있는 묶음들. 순서는 속성의 sort_order 를 따른다. */
  const known: string[] = []
  const counts = new Map<string, number>()
  for (const def of defs) {
    if (!def.section) continue
    if (!known.includes(def.section)) known.push(def.section)
    counts.set(def.section, (counts.get(def.section) ?? 0) + 1)
  }

  const listed = (value.sections ?? []).filter((one) => known.includes(one.name))
  const rest = known.filter((name) => !listed.some((one) => one.name === name))
  const loose = defs.filter((def) => !def.section).length

  function setSections(next: SectionView['sections']) {
    onChange({ ...value, sections: next })
  }

  function move(index: number, by: number) {
    const next = [...listed]
    const target = index + by
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setSections(next)
  }

  if (known.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        묶음이 하나도 없습니다. <b>속성 정의의 「묶음」</b> 에 이름을 적으면 여기서 순서와 열 수를
        정할 수 있습니다 — 소속은 속성이 들고 있고, 여기는 모양만 정합니다.
      </p>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-xs">
        {what}에서 묶음이 <b>표시 순서</b>와 <b>열 수·접힘</b>을 정합니다. 소속은 속성 정의의
        「묶음」 이 들고 있습니다.
        {loose > 0 && (
          <>
            {' '}
            묶음을 안 정한 속성 <b>{loose}개</b>는 맨 위에 표시됩니다.
          </>
        )}
      </p>

      {listed.length > 0 && (
        <ul className="space-y-1">
          {listed.map((one, index) => (
            <li key={one.name} className="flex items-center gap-2 rounded-md border px-2 py-1">
              <span className="flex-1 text-sm">
                {one.name}
                <span className="text-muted-foreground ml-1.5 text-xs tabular-nums">
                  {counts.get(one.name)}
                </span>
              </span>

              <Select
                value={String(one.columns ?? 1)}
                onValueChange={(next) =>
                  setSections(
                    listed.map((row) =>
                      row.name === one.name ? { ...row, columns: Number(next) as 1 | 2 | 3 } : row,
                    ),
                  )
                }
              >
                <SelectTrigger className="h-8 w-24">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">1열</SelectItem>
                  <SelectItem value="2">2열</SelectItem>
                  <SelectItem value="3">3열</SelectItem>
                </SelectContent>
              </Select>

              <label className="text-muted-foreground flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  className="size-3.5"
                  checked={one.collapsed ?? false}
                  onChange={(event) =>
                    setSections(
                      listed.map((row) =>
                        row.name === one.name ? { ...row, collapsed: event.target.checked } : row,
                      ),
                    )
                  }
                />
                접어 둠
              </label>

              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`${one.name} 위로`}
                disabled={index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp className="size-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`${one.name} 아래로`}
                disabled={index === listed.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {rest.length > 0 && (
        <div className="space-y-1.5">
          <Label className="text-xs">순서 미지정 묶음</Label>
          <div className="flex flex-wrap gap-1.5">
            {rest.map((name) => (
              <Button
                key={name}
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setSections([...listed, { name, columns: 1 }])}
              >
                {name} ({counts.get(name)})
              </Button>
            ))}
          </div>
          {/* **여기 없는 묶음도 화면에는 뜬다.** 새 속성을 만들었는데 뷰를 안
              고쳤다고 그 속성이 사라지면, 만든 사람은 저장이 안 된 줄 안다. */}
          <p className="text-muted-foreground text-xs">
            안 정해도 <b>맨 뒤에 1열로 표시됩니다</b> — 새로 만든 속성이 화면에서 사라지지 않게.
          </p>
        </div>
      )}
    </div>
  )
}
