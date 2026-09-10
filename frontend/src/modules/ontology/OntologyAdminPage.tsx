/**
 * 온톨로지 관리 — **도메인을 여기서 정의한다.**
 *
 * 그룹을 만들면 사이드바에 묶음이 서고, 타입을 만들면 그 묶음 안에 화면이 생기고,
 * 속성을 정의하면 그 화면의 폼이 생긴다. 코드를 고치는 일이 아니다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { DataType, ObjectType, PropertyDef } from '@/modules/ontology/api'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
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

const KIND_LABELS: Record<string, string> = {
  reference: '어휘',
  record: '인스턴스',
  system: '투영',
}

export default function OntologyAdminPage() {
  const schema = useResource(() => ontologyApi.schema(), [])
  const [error, setError] = useState<Error | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

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
  const type = data?.types.find((row) => row.slug === selected) ?? null

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="온톨로지"
        description="타입을 정의하면 사이드바와 화면이 생깁니다. 코드를 고치지 않습니다."
      />

      {error && <ErrorNotice error={error} />}

      <Tabs defaultValue="types">
        <TabsList>
          <TabsTrigger value="types">타입</TabsTrigger>
          <TabsTrigger value="groups">사이드바 묶음</TabsTrigger>
        </TabsList>

        <TabsContent value="types" className="space-y-6">
          <NewTypeForm
            groups={(data?.groups ?? []).map((group) => group.slug)}
            onSubmit={(body) => act(() => ontologyApi.createType(body))}
          />

          {data && data.types.length === 0 ? (
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
                  {(data?.types ?? []).map((row) => (
                    <TableRow
                      key={row.slug}
                      className="cursor-pointer"
                      onClick={() => setSelected(row.slug)}
                    >
                      <TableCell className="font-medium">{row.label}</TableCell>
                      <TableCell className="font-mono text-xs">{row.slug}</TableCell>
                      <TableCell>{KIND_LABELS[row.kind_class] ?? row.kind_class}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {/* **안 걸린 것은 사이드바에 안 선다.** 빈 칸으로 두면
                            「빠뜨렸나」 를 물을 자리가 없다. */}
                        {row.nav_group_slug ?? '사이드바에 없음'}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.object_count}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.properties.length}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {type && (
            <PropertyEditor
              type={type}
              types={data?.types ?? []}
              onChanged={() => schema.reload()}
              onError={setError}
            />
          )}
        </TabsContent>

        <TabsContent value="groups" className="space-y-6">
          <NewGroupForm onSubmit={(body) => act(() => ontologyApi.createGroup(body))} />
          {data && data.groups.length === 0 ? (
            <EmptyState
              title="묶음이 없습니다"
              hint="「도메인」 처럼 사이드바에서 화면을 묶는 이름입니다. 묶음이 없으면 타입은 만들어져도 사이드바에 서지 않습니다."
            />
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>이름</TableHead>
                    <TableHead>slug</TableHead>
                    <TableHead>보이는 대상</TableHead>
                    <TableHead className="text-right">순서</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(data?.groups ?? []).map((group) => (
                    <TableRow key={group.slug}>
                      <TableCell className="font-medium">{group.label}</TableCell>
                      <TableCell className="font-mono text-xs">{group.slug}</TableCell>
                      <TableCell>{group.audience}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {group.sort_order}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>
      </Tabs>
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
      <h2 className="text-base font-semibold">{type.label} 의 속성</h2>

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
