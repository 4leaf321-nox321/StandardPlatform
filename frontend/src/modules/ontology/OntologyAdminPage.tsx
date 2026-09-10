/**
 * 온톨로지 관리 — **도메인을 여기서 정의한다.**
 *
 * 그룹을 만들면 사이드바에 묶음이 서고, 타입을 만들면 그 묶음 안에 화면이 생기고,
 * 속성을 정의하면 그 화면의 폼이 생긴다. 코드를 고치는 일이 아니다.
 */

import { useState } from 'react'

import { GroupEditDialog } from '@/modules/ontology/GroupEditDialog'
import { TypeEditDialog } from '@/modules/ontology/TypeEditDialog'
import { ontologyApi } from '@/modules/ontology/api'
import type { DataType, NavGroupRow, ObjectType, PropertyDef } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
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
import { useResource } from '@/shared/hooks/useResource'

const DATA_TYPE_LABELS: Record<DataType, string> = {
  text: '글',
  number: '숫자',
  date: '날짜',
  bool: '예/아니오',
  enum: '선택',
  object_ref: '객체 참조',
  file: '파일',
}

const AUDIENCE_LABELS: Record<string, string> = {
  everyone: '모두',
  manager: '부서 관리자',
  system_admin: '시스템 관리자',
}

const KIND_LABELS: Record<string, string> = {
  reference: '어휘',
  record: '인스턴스',
  system: '투영',
}

export default function OntologyAdminPage() {
  const schema = useResource(() => ontologyApi.schema(), [])
  const [error, setError] = useState<Error | null>(null)
  const [openProperties, setOpenProperties] = useState<string | null>(null)
  const [editingType, setEditingType] = useState<string | null>(null)
  const [editingGroup, setEditingGroup] = useState<string | null>(null)

  async function act(run: () => Promise<unknown>) {
    setError(null)
    try {
      await run()
      schema.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  if (schema.error) return <ErrorNotice error={schema.error} />
  const data = schema.data
  const types = data?.types ?? []
  const groups = data?.groups ?? []

  const propertyTarget = types.find((row) => row.slug === openProperties) ?? null
  const typeTarget = types.find((row) => row.slug === editingType) ?? null
  const groupTarget = groups.find((row) => row.slug === editingGroup) ?? null

  /** 이 묶음에 걸린 타입 이름들. 지우기 전에 무엇이 걸렸는지 말하는 데 쓴다. */
  function attachedTo(group: NavGroupRow): string[] {
    return types.filter((row) => row.nav_group_slug === group.slug).map((row) => row.label)
  }

  return (
    <div className="mx-auto max-w-5xl space-y-10">
      <PageHeader
        title="온톨로지"
        description="타입을 정의하면 사이드바와 화면이 생깁니다. 코드를 고치지 않습니다."
      />

      {error && <ErrorNotice error={error} />}

      {/* **묶음이 먼저다.** 타입을 사이드바에 세우려면 들어갈 묶음이 먼저 있어야
          한다 — 순서가 곧 밟는 차례여야 「다음에 무엇을」 을 안 묻는다. */}
      <section className="space-y-4">
        <div>
          <h2 className="text-base font-semibold">1. 사이드바 묶음</h2>
          <p className="text-muted-foreground text-sm">
            「도메인」 처럼 화면을 묶는 이름입니다. 묶음이 없으면 타입을 만들어도 사이드바에
            서지 않습니다.
          </p>
        </div>

        <NewGroupForm onSubmit={(body) => act(() => ontologyApi.createGroup(body))} />

        {groups.length === 0 ? (
          <EmptyState
            title="묶음이 없습니다"
            hint="먼저 묶음 하나를 만드세요. 그다음 타입을 그 안에 넣습니다."
          />
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>slug</TableHead>
                  <TableHead>보이는 대상</TableHead>
                  <TableHead className="text-right">걸린 타입</TableHead>
                  <TableHead className="text-right">순서</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map((group) => (
                  <TableRow
                    key={group.slug}
                    className="cursor-pointer"
                    onClick={() => setEditingGroup(group.slug)}
                  >
                    <TableCell className="font-medium">
                      {group.label}
                      {!group.is_active && (
                        <span className="text-muted-foreground ml-2 text-xs">사용 안 함</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{group.slug}</TableCell>
                    <TableCell>{AUDIENCE_LABELS[group.audience] ?? group.audience}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {attachedTo(group).length}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{group.sort_order}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        <p className="text-muted-foreground text-xs">행을 누르면 고치거나 지웁니다.</p>
      </section>

      <section className="space-y-4">
        <div>
          <h2 className="text-base font-semibold">2. 타입</h2>
          <p className="text-muted-foreground text-sm">
            인스턴스의 종류입니다. 하나 만들면 그 순간 목록·상세 화면이 생깁니다.
          </p>
        </div>

        <NewTypeForm
          groups={groups.map((group) => group.slug)}
          onSubmit={(body) => act(() => ontologyApi.createType(body))}
        />

        {types.length === 0 ? (
          <EmptyState
            title="아직 타입이 없습니다"
            hint="타입 하나를 만들면 그 순간 목록·상세 화면이 생깁니다. 사이드바에 세우려면 묶음을 함께 고르세요."
          />
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>slug</TableHead>
                  <TableHead>분류</TableHead>
                  <TableHead>묶음</TableHead>
                  <TableHead className="text-right">인스턴스</TableHead>
                  <TableHead className="text-right">속성</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {types.map((row) => (
                  <TableRow
                    key={row.slug}
                    className="cursor-pointer"
                    onClick={() => setEditingType(row.slug)}
                  >
                    <TableCell className="font-medium">
                      {row.label}
                      {!row.is_active && (
                        <span className="text-muted-foreground ml-2 text-xs">사용 안 함</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.slug}</TableCell>
                    <TableCell>{KIND_LABELS[row.kind_class] ?? row.kind_class}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {/* **안 걸린 것은 사이드바에 안 선다.** 빈 칸으로 두면
                          「빠뜨렸나」 를 물을 자리가 없다. */}
                      {row.nav_group_slug ?? '사이드바에 없음'}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.object_count}</TableCell>
                    <TableCell className="text-right">
                      {/* **속성 정의는 그것대로 자기 자리가 있다.** 행 클릭은
                          타입 설정이고, 여기는 그 타입이 담는 값의 모양이다. */}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(event) => {
                          event.stopPropagation()
                          setOpenProperties(row.slug === openProperties ? null : row.slug)
                        }}
                      >
                        속성 {row.properties.length}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        <p className="text-muted-foreground text-xs">
          행을 누르면 묶음·분류·식별자 정책을 고치거나 지웁니다. 「속성」 을 누르면 아래에
          그 타입의 속성 정의가 열립니다.
        </p>
      </section>

      {propertyTarget && (
        <section className="space-y-4">
          <h2 className="text-base font-semibold">3. {propertyTarget.label} 의 속성</h2>
          <PropertyEditor
            type={propertyTarget}
            types={types}
            onChanged={() => schema.reload()}
            onError={setError}
          />
        </section>
      )}

      {typeTarget && (
        <TypeEditDialog
          type={typeTarget}
          groups={groups}
          onClose={() => setEditingType(null)}
          onChanged={() => schema.reload()}
        />
      )}

      {groupTarget && (
        <GroupEditDialog
          group={groupTarget}
          attached={attachedTo(groupTarget)}
          onClose={() => setEditingGroup(null)}
          onChanged={() => schema.reload()}
        />
      )}
    </div>
  )
}

function NewGroupForm({ onSubmit }: { onSubmit: (body: Record<string, unknown>) => void }) {
  const [slug, setSlug] = useState('')
  const [label, setLabel] = useState('')

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-md border p-4">
      <div className="space-y-1.5">
        <Label htmlFor="group-slug">slug</Label>
        <Input
          id="group-slug"
          value={slug}
          placeholder="domain"
          className="w-40 font-mono"
          onChange={(event) => setSlug(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="group-label">이름</Label>
        <Input
          id="group-label"
          value={label}
          placeholder="도메인"
          className="w-48"
          onChange={(event) => setLabel(event.target.value)}
        />
      </div>
      <Button
        disabled={!slug.trim() || !label.trim()}
        onClick={() => {
          onSubmit({ slug, label })
          setSlug('')
          setLabel('')
        }}
      >
        묶음 만들기
      </Button>
      <p className="text-muted-foreground w-full text-xs">
        slug 는 소문자·숫자·밑줄이고 <b>나중에 바꿀 수 없습니다.</b>
      </p>
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
            nav_group_slug: group || null,
            key_policy: keyPolicy,
          })
          setSlug('')
          setLabel('')
        }}
      >
        타입 만들기
      </Button>
      <p className="text-muted-foreground w-full text-xs">
        slug 는 <b>나중에 바꿀 수 없습니다</b> — 주소(<code>/o/&lt;slug&gt;</code>)와 관계·MCP
        도구 이름이 여기 물립니다.
      </p>
    </div>
  )
}

function PropertyEditor({
  type,
  types,
  onChanged,
  onError,
}: {
  type: ObjectType & { properties: PropertyDef[] }
  types: ObjectType[]
  onChanged: () => void
  onError: (error: Error | null) => void
}) {
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [dataType, setDataType] = useState<DataType>('text')
  const [refType, setRefType] = useState('')
  const [options, setOptions] = useState('')
  const [removing, setRemoving] = useState<PropertyDef | null>(null)
  const [usage, setUsage] = useState<number | null>(null)

  async function act(run: () => Promise<unknown>) {
    onError(null)
    try {
      await run()
      onChanged()
    } catch (caught) {
      onError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  return (
    <section className="space-y-4 rounded-md border p-4">
      {type.properties.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          아직 속성이 없습니다. 하나 정의하면 만들기·상세 화면의 폼에 칸이 생깁니다.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>키</TableHead>
              <TableHead>종류</TableHead>
              <TableHead>필수</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {type.properties.map((def) => (
              <TableRow key={def.key}>
                <TableCell className="font-medium">{def.label}</TableCell>
                <TableCell className="font-mono text-xs">{def.key}</TableCell>
                <TableCell>
                  {DATA_TYPE_LABELS[def.data_type]}
                  {def.multi && ' (여러 값)'}
                </TableCell>
                <TableCell>{def.required ? '예' : '—'}</TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      // **지우기 전에 몇 개가 안 보이게 되는지 먼저 읽는다.**
                      const found = await ontologyApi.propertyUsage(type.slug, def.key)
                      setUsage(found.objects_with_value)
                      setRemoving(def)
                    }}
                  >
                    지우기
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <div className="flex flex-wrap items-end gap-3 border-t pt-4">
        <div className="space-y-1.5">
          <Label htmlFor="prop-key">키</Label>
          <Input
            id="prop-key"
            value={key}
            placeholder="vendor"
            className="w-36 font-mono"
            onChange={(event) => setKey(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="prop-label">이름</Label>
          <Input
            id="prop-label"
            value={label}
            placeholder="공급사"
            className="w-36"
            onChange={(event) => setLabel(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="prop-type">종류</Label>
          <Select value={dataType} onValueChange={(next) => setDataType(next as DataType)}>
            <SelectTrigger id="prop-type" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(DATA_TYPE_LABELS) as DataType[]).map((one) => (
                <SelectItem key={one} value={one}>
                  {DATA_TYPE_LABELS[one]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {dataType === 'enum' && (
          <div className="space-y-1.5">
            <Label htmlFor="prop-options">고를 값 (쉼표로)</Label>
            <Input
              id="prop-options"
              value={options}
              placeholder="A, B, C"
              className="w-48"
              onChange={(event) => setOptions(event.target.value)}
            />
          </div>
        )}

        {dataType === 'object_ref' && (
          <div className="space-y-1.5">
            <Label htmlFor="prop-ref">가리킬 타입</Label>
            <Select value={refType} onValueChange={setRefType}>
              <SelectTrigger id="prop-ref" className="w-40">
                <SelectValue placeholder="고르세요" />
              </SelectTrigger>
              <SelectContent>
                {types.map((one) => (
                  <SelectItem key={one.slug} value={one.slug}>
                    {one.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <Button
          disabled={!key.trim() || !label.trim()}
          onClick={() => {
            void act(() =>
              ontologyApi.createProperty(type.slug, {
                key,
                label,
                data_type: dataType,
                enum_options:
                  dataType === 'enum'
                    ? options
                        .split(',')
                        .map((one) => one.trim())
                        .filter(Boolean)
                    : null,
                ref_type_slug: dataType === 'object_ref' ? refType || null : null,
              }),
            )
            setKey('')
            setLabel('')
            setOptions('')
          }}
        >
          속성 더하기
        </Button>
        <p className="text-muted-foreground w-full text-xs">
          키와 종류는 <b>나중에 바꿀 수 없습니다</b> — 이미 저장된 값이 새 종류에 안 맞아도
          화면이 그것을 말해 주지 못합니다. 바꾸려면 새 속성을 만들어 옮깁니다.
        </p>
      </div>

      {removing && (
        <ConfirmDialog
          open
          destructive
          title={`${removing.label} 속성을 지웁니다`}
          description={
            <>
              지금 이 값을 가진 객체가 <b>{usage ?? 0}개</b> 있습니다. 정의를 지우면 그
              값들은 <b>화면에서 사라집니다</b>(데이터는 남아 있어, 정의를 되살리면 다시
              보입니다).
            </>
          }
          confirmLabel="지우기"
          onConfirm={async () => {
            await ontologyApi.removeProperty(type.slug, removing.key)
            onChanged()
          }}
          onClose={() => {
            setRemoving(null)
            setUsage(null)
          }}
        />
      )}
    </section>
  )
}
