/**
 * 타입 — **객체의 종류.** 하나 만들면 그 순간 목록·상세 화면이 생긴다.
 *
 * 행을 누르면 타입 자체의 설정을 고치고, 「속성」 을 누르면 그 타입이 담는 값의
 * 모양을 고친다. **한 창에 섞지 않는다** — 섞으면 둘이 같은 것처럼 읽힌다.
 */

import { useState } from 'react'
import { ChevronDown, Eye, Plus, Settings2 } from 'lucide-react'

import { PropertyEditDialog, DATA_TYPE_LABELS } from '@/modules/ontology/PropertyEditDialog'
import { TypeEditDialog } from '@/modules/ontology/TypeEditDialog'
import { TypeExamples } from '@/modules/ontology/TypeExamples'
import { useOntology } from '@/modules/ontology/OntologyLayout'
import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { IconPickerButton } from '@/shared/components/IconPicker'
import { TypeIcon } from '@/shared/components/TypeIcon'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { cn } from '@/shared/lib/utils'

const KIND_LABELS: Record<string, string> = {
  reference: '어휘',
  record: '객체',
  system: '투영',
}

export default function OntologyTypesPage() {
  const { schema, reload, setError } = useOntology()
  const [editing, setEditing] = useState<string | null>(null)
  const [openProperties, setOpenProperties] = useState<string | null>(null)
  // 「객체 N」 을 누르면 그 타입의 예시가 아래에 선다 — 정의만으로는 칸의 뜻이 안 잡힌다.
  const [openExamples, setOpenExamples] = useState<string | null>(null)

  const types = schema?.types ?? []
  const groups = schema?.groups ?? []
  const target = types.find((row) => row.slug === editing) ?? null
  const propertyTarget = types.find((row) => row.slug === openProperties) ?? null
  const exampleTarget = types.find((row) => row.slug === openExamples) ?? null

  async function create(body: Record<string, unknown>) {
    setError(null)
    try {
      await ontologyApi.createType(body)
      reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        객체의 종류입니다. 하나 만들면 그 순간 목록·상세 화면이 생깁니다.
      </p>

      {groups.length === 0 && (
        <EmptyState
          title="묶음을 먼저 만드세요"
          hint="타입은 만들 수 있지만, 들어갈 묶음이 없으면 사이드바에 표시되지 않습니다."
        />
      )}

      <NewTypeForm groups={groups.map((group) => group.slug)} onSubmit={create} />

      {types.length === 0 ? (
        <EmptyState
          title="아직 타입이 없습니다"
          hint="타입 하나를 만들면 그 순간 목록·상세 화면이 생깁니다. 사이드바에 표시하려면 묶음을 함께 선택하세요."
        />
      ) : (
        <>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>slug</TableHead>
                  <TableHead>분류</TableHead>
                  <TableHead>묶음</TableHead>
                  <TableHead className="text-right">객체 (예시)</TableHead>
                  {/* **열 이름이 「누르세요」 라고 말한다.** 숫자만 있으면 읽을 것으로
                      보이지 눌러 볼 것으로는 안 보인다. */}
                  <TableHead className="text-right">속성 정의</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {types.map((row) => (
                  <TableRow
                    key={row.slug}
                    className={row.managed_by ? undefined : 'cursor-pointer'}
                    // 허브가 내려준 타입은 여기서 안 고친다 — 허브에서 고친 뒤 받는다.
                    onClick={() => !row.managed_by && setEditing(row.slug)}
                  >
                    <TableCell className="font-medium">
                      {/* 사이드바에 설 그림을 **여기서도** 보여 준다 — 정의 화면과
                          메뉴가 다른 것을 보이면 고른 사람이 확인할 자리가 없다. */}
                      <TypeIcon name={row.icon} className="mr-2 inline align-text-bottom" />
                      {row.label}
                      {!row.is_active && (
                        <span className="text-muted-foreground ml-2 text-xs">사용 안 함</span>
                      )}
                      {row.managed_by && (
                        <span className="ml-2 rounded border px-1.5 text-xs">허브 관리</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.slug}</TableCell>
                    <TableCell>{KIND_LABELS[row.kind_class] ?? row.kind_class}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {/* **안 걸린 것은 사이드바에 안 선다.** 빈 칸으로 두면
                          「빠뜨렸나」 를 물을 자리가 없다. */}
                      {row.nav_group_slug ?? '사이드바에 없음'}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-expanded={row.slug === openExamples}
                        title="이 타입의 예시 객체"
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpenExamples(row.slug === openExamples ? null : row.slug)
                        }}
                      >
                        <Eye className="mr-1 size-3.5" />
                        {row.object_count}
                      </Button>
                    </TableCell>
                    <TableCell className="text-right">
                      {/* **눌러지는 것으로 보여야 누른다.** 행 클릭은 타입 설정이고
                          여기는 그 타입이 담는 값의 모양이라, 같은 표 안에서 두 길이
                          갈린다 — 테두리와 화살표가 그 갈림을 말한다. */}
                      <Button
                        variant="outline"
                        size="sm"
                        aria-expanded={row.slug === openProperties}
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpenProperties(row.slug === openProperties ? null : row.slug)
                        }}
                      >
                        <Settings2 className="mr-1 size-3.5" />
                        속성 {row.properties.length}
                        <ChevronDown
                          className={cn(
                            'ml-1 size-3.5 transition-transform',
                            row.slug === openProperties && 'rotate-180',
                          )}
                        />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-muted-foreground text-xs">
            <b>행</b>을 클릭하면 묶음·분류·식별자 정책을 수정하거나 삭제합니다. <b>「객체」 수</b>를
            클릭하면 그 타입의 예시 객체가, <b>「속성 정의」 단추</b>를 클릭하면 그 타입이 포함하는
            값의 모양이 아래에 열립니다.
          </p>
        </>
      )}

      {exampleTarget && (
        <section className="space-y-3 rounded-md border p-4">
          <h2 className="text-base font-semibold">
            <TypeIcon name={exampleTarget.icon} className="mr-2 inline align-text-bottom" />
            {exampleTarget.label} 의 예시
          </h2>
          <TypeExamples type={exampleTarget} />
        </section>
      )}

      {propertyTarget && (
        <section className="space-y-3">
          <h2 className="text-base font-semibold">{propertyTarget.label} 의 속성</h2>
          {/* 오류는 이 창 안에 선다 — 화면 맨 위로 올리면 아래를 보고 있던
              사람 눈에 안 들어온다. */}
          <PropertyEditor
            type={propertyTarget}
            types={types}
            onChanged={reload}
            readOnly={Boolean(propertyTarget.managed_by)}
          />
        </section>
      )}

      {target && (
        <TypeEditDialog
          type={target}
          groups={groups}
          relationTypes={schema?.relation_types ?? []}
          systemSources={schema?.system_sources ?? []}
          onClose={() => setEditing(null)}
          onChanged={reload}
        />
      )}
    </div>
  )
}

function NewTypeForm({
  groups,
  onSubmit,
}: {
  groups: string[]
  onSubmit: (body: Record<string, unknown>) => void
}) {
  const [slug, setSlug] = useState('')
  const [label, setLabel] = useState('')
  const [group, setGroup] = useState<string>('')
  const [keyPolicy, setKeyPolicy] = useState('none')
  // **만들 때 고른다.** 만들고 나면 그 타입은 곧 쓰이기 시작하고, 사이드바를 다듬으러
  // 다시 오는 사람은 없다 — 그래서 전부 같은 네모로 남는다.
  const [icon, setIcon] = useState('LayoutGrid')

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-md border p-4">
      <div className="space-y-1.5">
        <Label htmlFor="type-slug">slug</Label>
        <Input
          id="type-slug"
          value={slug}
          placeholder="part"
          className="w-40 font-mono"
          onChange={(event) => setSlug(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="type-label">이름</Label>
        <Input
          id="type-label"
          value={label}
          placeholder="부품"
          className="w-40"
          onChange={(event) => setLabel(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="type-icon">아이콘</Label>
        <IconPickerButton id="type-icon" value={icon} onChange={setIcon} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="type-group">사이드바 묶음</Label>
        <Select value={group} onValueChange={setGroup}>
          <SelectTrigger id="type-group" className="w-40">
            <SelectValue placeholder="없음" />
          </SelectTrigger>
          <SelectContent>
            {groups.map((one) => (
              <SelectItem key={one} value={one}>
                {one}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="type-key">식별자</Label>
        <Select value={keyPolicy} onValueChange={setKeyPolicy}>
          <SelectTrigger id="type-key" className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">안 씀</SelectItem>
            <SelectItem value="optional">선택</SelectItem>
            <SelectItem value="required">필수</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <Button
        disabled={!slug.trim() || !label.trim()}
        onClick={() => {
          onSubmit({
            slug,
            label,
            icon,
            nav_group_slug: group || null,
            key_policy: keyPolicy,
          })
          setSlug('')
          setLabel('')
        }}
      >
        타입 생성
      </Button>
      <p className="text-muted-foreground w-full text-xs">
        slug 는 <b>나중에 바꿀 수 없습니다</b> — 주소(<code>/o/&lt;slug&gt;</code>)와 관계·MCP 도구
        이름이 여기 물립니다.
      </p>
    </div>
  )
}

function PropertyEditor({
  type,
  types,
  onChanged,
  readOnly = false,
}: {
  type: ObjectType & { properties: PropertyDef[] }
  types: ObjectType[]
  onChanged: () => void
  /** 허브가 내려준 타입 — 속성을 보이기만 한다. */
  readOnly?: boolean
}) {
  const [editing, setEditing] = useState<PropertyDef | null>(null)
  const [creating, setCreating] = useState(false)

  return (
    <section className="space-y-3 rounded-md border p-4">
      {type.properties.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          아직 속성이 없습니다. 하나 정의하면{' '}
          <b>{type.label} 생성·상세 화면의 폼에 칸이 생깁니다.</b>
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>키</TableHead>
              <TableHead>종류</TableHead>
              <TableHead>단위</TableHead>
              <TableHead>필수</TableHead>
              <TableHead>여러 값</TableHead>
              <TableHead className="text-right">순서</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {type.properties.map((def) => (
              <TableRow
                key={def.key}
                className={readOnly ? undefined : 'cursor-pointer'}
                onClick={() => !readOnly && setEditing(def)}
              >
                <TableCell className="font-medium">{def.label}</TableCell>
                <TableCell className="font-mono text-xs">{def.key}</TableCell>
                <TableCell>{DATA_TYPE_LABELS[def.data_type]}</TableCell>
                <TableCell className="text-muted-foreground">{def.unit || '—'}</TableCell>
                <TableCell>{def.required ? '예' : '—'}</TableCell>
                <TableCell>{def.multi ? '예' : '—'}</TableCell>
                <TableCell className="text-right tabular-nums">{def.sort_order}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <div className="flex items-center justify-between gap-3 border-t pt-3">
        <p className="text-muted-foreground text-xs">
          {readOnly
            ? '허브가 내려준 타입이라 여기서 속성을 수정하지 않습니다 — 허브에서 수정한 뒤 받습니다.'
            : `${type.properties.length > 0 ? '행을 클릭하면 이름·단위·안내·필수·여러 값을 수정하거나 삭제합니다. ' : ''}키와 종류는 만들 때만 정합니다.`}
        </p>
        {!readOnly && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-1 size-4" />
            속성 추가
          </Button>
        )}
      </div>

      {(creating || editing) && (
        <PropertyEditDialog
          type={type}
          property={editing}
          types={types}
          onClose={() => {
            setCreating(false)
            setEditing(null)
          }}
          onChanged={onChanged}
        />
      )}
    </section>
  )
}
