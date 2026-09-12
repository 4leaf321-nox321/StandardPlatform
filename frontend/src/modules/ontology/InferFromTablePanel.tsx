/**
 * 표에서 타입 만들기 — **CSV·JSON 하나로 타입과 데이터가 함께 생긴다.**
 *
 * 파일 가져오기는 타입이 먼저 있어야 했다. 여기서는 열을 보고 정의를 제안하고(숫자·날짜·참/거짓·
 * 고를 값·주소·글자), 사람이 역할과 종류를 고친 뒤 **정의를 만들고 행을 넣는다** — 둘 다 기존
 * 길(정의 가져오기·파일 가져오기)로 간다. 추론은 보수적이다: 애매하면 글자다.
 */

import { useRef, useState } from 'react'
import { FileUp, Play, Table2 } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { ImportPlan as RowsPlan } from '@/modules/objects/api'
import { ontologyApi } from '@/modules/ontology/api'
import type {
  DataType,
  ImportPlan,
  InferColumn,
  InferResult,
  InferRole,
  NavGroupRow,
} from '@/modules/ontology/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

const NONE = '__none__'

const ROLES: [InferRole, string][] = [
  ['label', '이름'],
  ['key', '식별자'],
  ['description', '설명'],
  ['aliases', '다른 이름'],
  ['property', '속성'],
  ['ignore', '무시'],
]

const TYPES: [DataType, string][] = [
  ['text', '글자'],
  ['text_long', '긴 글'],
  ['number', '숫자'],
  ['date', '날짜'],
  ['datetime', '날짜·시각'],
  ['bool', '참/거짓'],
  ['enum', '고를 값'],
  ['url', '주소'],
]

interface Props {
  groups: NavGroupRow[]
  /** 정의를 적용한 뒤 — 사이드바·스키마를 다시 읽게. */
  onChanged: () => void
}

export function InferFromTablePanel({ groups, onChanged }: Props) {
  const { user } = useAuth()
  const fileRef = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState('')
  const [pasted, setPasted] = useState('')
  const [result, setResult] = useState<InferResult | null>(null)
  const [columns, setColumns] = useState<InferColumn[]>([])
  const [slug, setSlug] = useState('')
  const [label, setLabel] = useState('')
  const [group, setGroup] = useState(NONE)
  const [keyPolicy, setKeyPolicy] = useState<'none' | 'optional' | 'required'>('optional')
  const [workspace, setWorkspace] = useState(user?.home_workspace_slug ?? NONE)
  const [schemaPlan, setSchemaPlan] = useState<ImportPlan | null>(null)
  const [rowsPlan, setRowsPlan] = useState<RowsPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function load(file: File) {
    setBusy(true)
    setError(null)
    setSchemaPlan(null)
    setRowsPlan(null)
    try {
      const got = await ontologyApi.infer(file)
      setResult(got)
      setColumns(got.columns)
      setFileName(file.name)
      if (!label) setLabel(file.name.replace(/\.[^.]+$/, ''))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  function patch(index: number, next: Partial<InferColumn>) {
    setColumns(columns.map((one, i) => (i === index ? { ...one, ...next } : one)))
    setSchemaPlan(null)
    setRowsPlan(null)
  }

  /** 계획 — 정의 계획만 본다(행 계획은 타입이 생긴 뒤에야 설 수 있다). */
  async function plan() {
    if (!result) return
    setBusy(true)
    setError(null)
    try {
      const built = await ontologyApi.inferBuild({
        slug: slug.trim(),
        label: label.trim(),
        nav_group_slug: group === NONE ? null : group,
        key_policy: keyPolicy,
        columns,
        raw_rows: result.raw_rows,
      })
      setSchemaPlan(await ontologyApi.importSchema(built.schema, true))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  /** 적용 — 정의를 만들고, 그 타입에 행을 넣는다. 행이 하나라도 오류면 행은 안 들어간다
   *  (정의는 이미 생겼다 — 그 사실을 말한다). */
  async function apply() {
    if (!result) return
    setBusy(true)
    setError(null)
    try {
      const built = await ontologyApi.inferBuild({
        slug: slug.trim(),
        label: label.trim(),
        nav_group_slug: group === NONE ? null : group,
        key_policy: keyPolicy,
        columns,
        raw_rows: result.raw_rows,
      })
      const applied = await ontologyApi.importSchema(built.schema, false)
      setSchemaPlan(applied)
      if (!applied.applied) return
      onChanged()
      setRowsPlan(
        await objectApi.importRows(slug.trim(), built.import_rows, {
          apply: true,
          workspaceSlug: workspace === NONE ? null : workspace,
        }),
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const labelCount = columns.filter((one) => one.role === 'label').length
  const ready = Boolean(result && slug.trim() && label.trim() && labelCount === 1)

  return (
    <section className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Table2 className="size-4" />
          표에서 타입 만들기
        </h2>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.json,text/csv,application/json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void load(file)
            event.target.value = ''
          }}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
        >
          <FileUp className="mr-1 size-4" />
          CSV·JSON 올리기
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">
        데이터 파일을 올리면 열을 보고 타입 정의를 제안합니다 — 역할(이름·식별자·속성)과 종류를 고친
        뒤 적용하면 <b>타입이 생기고 행이 그 타입에 들어갑니다.</b> 애매한 열은 글자로 둡니다.
        나중에 좁히는 것은 쉽고, 잘못 좁힌 것을 되돌리는 것은 어렵습니다.
      </p>
      {/* 붙여 넣기 — 엑셀에서 복사하면 탭으로 온다. 사내 DRM 이 저장을 잠그면 이것이 유일한 길이다. */}
      <details className="text-sm">
        <summary className="text-muted-foreground cursor-pointer">
          파일 대신 붙여 넣기 (엑셀에서 복사 · CSV · JSON)
        </summary>
        <div className="mt-2 space-y-2">
          <Textarea
            rows={6}
            value={pasted}
            placeholder={'코드\t이름\t무게\nP-1\t볼트\t1.5'}
            className="font-mono text-xs"
            onChange={(event) => setPasted(event.target.value)}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !pasted.trim()}
            onClick={() => {
              const text = pasted.trim()
              const isJson = text.startsWith('{') || text.startsWith('[')
              void load(
                new File([pasted], isJson ? '붙여넣기.json' : '붙여넣기.csv', {
                  type: 'text/plain',
                }),
              )
            }}
          >
            <Table2 className="mr-1 size-4" />
            붙여 넣은 표 읽기
          </Button>
        </div>
      </details>

      {error && <ErrorNotice error={error} />}

      {result && (
        <>
          <p className="text-sm">
            <b>{fileName}</b> — {result.rows}행, 열 {columns.length}개
          </p>
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="space-y-1.5">
              <Label htmlFor="inf-slug">타입 slug</Label>
              <Input
                id="inf-slug"
                value={slug}
                placeholder="part"
                onChange={(event) => setSlug(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="inf-label">타입 이름</Label>
              <Input
                id="inf-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>사이드바 묶음</Label>
              <Select value={group} onValueChange={setGroup}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>안 세움</SelectItem>
                  {groups.map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>식별자</Label>
              <Select
                value={keyPolicy}
                onValueChange={(next) => setKeyPolicy(next as typeof keyPolicy)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">안 씀</SelectItem>
                  <SelectItem value="optional">선택</SelectItem>
                  <SelectItem value="required">필수</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-muted-foreground text-xs">
                <tr>
                  <th className="px-2 py-1.5 text-left">바깥 열</th>
                  <th className="px-2 py-1.5 text-left">역할</th>
                  <th className="px-2 py-1.5 text-left">속성 키</th>
                  <th className="px-2 py-1.5 text-left">종류</th>
                  <th className="px-2 py-1.5 text-left">본보기</th>
                  <th className="px-2 py-1.5 text-left">왜</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((column, index) => (
                  <tr key={column.header} className="border-t align-top">
                    <td className="px-2 py-1.5 font-medium">{column.header}</td>
                    <td className="px-2 py-1.5">
                      <Select
                        value={column.role}
                        onValueChange={(next) => patch(index, { role: next as InferRole })}
                      >
                        <SelectTrigger className="h-8 w-28">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ROLES.map(([key, text]) => (
                            <SelectItem key={key} value={key}>
                              {text}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td className="px-2 py-1.5">
                      {column.role === 'property' && (
                        <Input
                          value={column.key}
                          className="h-8 w-36 font-mono text-xs"
                          onChange={(event) => patch(index, { key: event.target.value })}
                        />
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      {column.role === 'property' && (
                        <div className="flex items-center gap-1">
                          <Select
                            value={column.data_type}
                            onValueChange={(next) =>
                              patch(index, {
                                data_type: next as DataType,
                                enum_options: next === 'enum' ? column.enum_options : [],
                              })
                            }
                          >
                            <SelectTrigger className="h-8 w-28">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {TYPES.map(([key, text]) => (
                                <SelectItem key={key} value={key}>
                                  {text}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {column.multi && (
                            <span className="text-muted-foreground text-xs">여럿</span>
                          )}
                        </div>
                      )}
                      {column.role === 'property' && column.data_type === 'enum' && (
                        <p className="text-muted-foreground mt-1 max-w-56 truncate text-xs">
                          {column.enum_options.join(' · ')}
                        </p>
                      )}
                    </td>
                    <td className="text-muted-foreground max-w-48 truncate px-2 py-1.5 text-xs">
                      {column.samples.join(' · ')}
                      <span className="block">
                        {column.filled}/{result.rows} 채움 · 서로 다른 값 {column.distinct}
                      </span>
                    </td>
                    <td className="text-muted-foreground max-w-56 px-2 py-1.5 text-xs">
                      {column.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {labelCount !== 1 && (
            <p className="text-destructive text-xs">
              이름 역할의 열이 정확히 하나여야 합니다 (지금 {labelCount}개).
            </p>
          )}

          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="space-y-1.5">
              <Label>행을 넣을 부서</Label>
              <Select value={workspace} onValueChange={setWorkspace}>
                <SelectTrigger className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>전역</SelectItem>
                  {(user?.memberships ?? []).map((one) => (
                    <SelectItem key={one.slug} value={one.slug}>
                      {one.slug}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={plan} disabled={busy || !ready}>
                <Play className="mr-1 size-4" />
                정의 계획 보기
              </Button>
              <Button
                onClick={apply}
                disabled={busy || !ready || !schemaPlan || schemaPlan.errors.length > 0}
              >
                타입 만들고 {result.rows}행 넣기
              </Button>
            </div>
          </div>

          {schemaPlan && (
            <div className="space-y-1 rounded border p-3 text-sm">
              <p className="font-medium">
                {schemaPlan.applied ? '정의를 만들었습니다' : '정의 계획'} —{' '}
                {schemaPlan.changes.filter((one) => one.action !== 'unchanged').length}개 변경
              </p>
              {schemaPlan.errors.map((one) => (
                <p key={one} className="text-destructive text-xs">
                  {one}
                </p>
              ))}
              {schemaPlan.warnings.map((one) => (
                <p key={one} className="text-xs text-amber-700 dark:text-amber-400">
                  {one}
                </p>
              ))}
            </div>
          )}
          {rowsPlan && (
            <div className="space-y-1 rounded border p-3 text-sm">
              <p className="font-medium">
                {rowsPlan.applied
                  ? `행을 넣었습니다 — 새로 ${rowsPlan.counts.create}, 고침 ${rowsPlan.counts.update}`
                  : `행은 안 들어갔습니다 — 오류 ${rowsPlan.counts.error}개. 정의는 이미 생겼으니, 파일을 고쳐 「파일로 넣기」 로 넣으세요.`}
              </p>
              {rowsPlan.errors.map((one) => (
                <p key={one} className="text-destructive text-xs">
                  {one}
                </p>
              ))}
              {rowsPlan.rows
                .filter((one) => one.action === 'error')
                .slice(0, 20)
                .map((one) => (
                  <p key={one.row} className="text-destructive text-xs">
                    {one.row}행 {one.label}: {one.message}
                  </p>
                ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}
