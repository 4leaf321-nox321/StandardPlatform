/**
 * 타입 — **객체의 종류.** 하나 만들면 그 순간 목록·상세 화면이 생긴다.
 *
 * 행을 누르면 타입 자체의 설정을 고치고, 「속성」 을 누르면 그 타입이 담는 값의
 * 모양을 고친다. **한 창에 섞지 않는다** — 섞으면 둘이 같은 것처럼 읽힌다.
 */

import { useState } from 'react'

import { TypeEditDialog } from '@/modules/ontology/TypeEditDialog'
import { useOntology } from '@/modules/ontology/OntologyLayout'
import { ontologyApi } from '@/modules/ontology/api'
import type { DataType, ObjectType, PropertyDef } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
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
  record: '객체',
  system: '투영',
}

export default function OntologyTypesPage() {
  const { schema, reload, setError } = useOntology()
  const [editing, setEditing] = useState<string | null>(null)
  const [openProperties, setOpenProperties] = useState<string | null>(null)

  const types = schema?.types ?? []
  const groups = schema?.groups ?? []
  const target = types.find((row) => row.slug === editing) ?? null
  const propertyTarget = types.find((row) => row.slug === openProperties) ?? null

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
          hint="타입은 만들 수 있지만, 들어갈 묶음이 없으면 사이드바에 서지 않습니다."
        />
      )}

      <NewTypeForm groups={groups.map((group) => group.slug)} onSubmit={create} />

      {types.length === 0 ? (
        <EmptyState
          title="아직 타입이 없습니다"
          hint="타입 하나를 만들면 그 순간 목록·상세 화면이 생깁니다. 사이드바에 세우려면 묶음을 함께 고르세요."
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
                  <TableHead className="text-right">객체</TableHead>
                  <TableHead className="text-right">속성</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {types.map((row) => (
                  <TableRow
                    key={row.slug}
                    className="cursor-pointer"
                    onClick={() => setEditing(row.slug)}
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
          <p className="text-muted-foreground text-xs">
            행을 누르면 묶음·분류·식별자 정책을 고치거나 지웁니다. 「속성」 을 누르면 아래에
            그 타입의 속성 정의가 열립니다.
          </p>
        </>
      )}

      {propertyTarget && (
        <section className="space-y-3">
          <h2 className="text-base font-semibold">{propertyTarget.label} 의 속성</h2>
          <PropertyEditor
            type={propertyTarget}
            types={types}
            onChanged={reload}
            onError={setError}
          />
        </section>
      )}

      {target && (
        <TypeEditDialog
          type={target}
          groups={groups}
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
