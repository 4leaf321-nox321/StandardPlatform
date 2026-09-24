/**
 * 데이터 소스 — **바깥 시스템(OData)에서 읽어 온톨로지를 채운다.**
 *
 * 규칙은 일괄 입력과 같다: 계획 먼저, 전부 아니면 무, 같은 객체면 고침, 빈 칸은 안 건드림.
 * 다른 것은 행이 어디서 오는가(OData)와 같은 객체를 어떻게 다시 찾는가(바깥 식별자를
 * 별칭으로 남김)뿐이다. 「미리 보기」 로 칸 대응을 맞추고, 「동기화」 는 계획을 보여 준 뒤
 * 적용한다.
 */

import { useState } from 'react'
import { Eye, Plus, RefreshCw, Trash2, Wand2, X } from 'lucide-react'

import { datasourceApi } from '@/modules/datasources/api'
import type {
  AuthKind,
  CoreSuggest,
  DataSource,
  SourceKind,
  SourceOptions,
  DataSourceWrite,
  MappingColumn,
  Preview,
  SyncResult,
} from '@/modules/datasources/api'
import { ontologyApi } from '@/modules/ontology/api'
import type { PropertyDef } from '@/modules/ontology/api'
import { workspaceApi } from '@/modules/workspaces/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { Textarea } from '@/shared/components/ui/textarea'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

const NONE = '__none__'
const KIND_LABEL: Record<string, string> = {
  odata: 'OData',
  rest: 'REST',
  file: '파일',
  sp_core: '형제 코어',
}

export default function DataSourcesPage() {
  const list = useResource(() => datasourceApi.list(), [])
  const schema = useResource(() => ontologyApi.schema(), [])
  const workspaces = useResource(() => workspaceApi.list(true), [])
  const [editing, setEditing] = useState<DataSource | 'new' | null>(null)
  const [removing, setRemoving] = useState<DataSource | null>(null)
  const [opened, setOpened] = useState<string | null>(null)
  const [syncing, setSyncing] = useState<{ source: DataSource; result: SyncResult } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)

  const sources = list.data ?? []

  async function plan(source: DataSource) {
    setBusy(source.slug)
    setError(null)
    try {
      setSyncing({ source, result: await datasourceApi.sync(source.slug, false) })
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        title="데이터 소스"
        description="바깥 시스템(OData)에서 읽어 온톨로지를 채웁니다. 규칙은 일괄 입력과 같습니다 — 계획 먼저, 전부 아니면 무."
        actions={
          <Button size="sm" onClick={() => setEditing('new')}>
            <Plus className="mr-1 size-4" />
            생성
          </Button>
        }
      />

      {list.error && <ErrorNotice error={list.error} />}
      {error && <ErrorNotice error={error} />}

      {list.data && sources.length === 0 ? (
        <EmptyState
          title="데이터 소스가 없습니다"
          hint="OData 서비스 주소·엔티티 셋·칸 대응을 적으면, 그 표의 행이 한 타입의 객체로 들어옵니다. ENOVIA·Teamcenter·ERP 가 이런 표를 냅니다."
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {sources.map((source) => (
            <li key={source.slug} className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="text-left font-medium hover:underline"
                  onClick={() => setOpened(opened === source.slug ? null : source.slug)}
                >
                  {source.name}
                </button>
                <code className="text-muted-foreground text-xs">{source.slug}</code>
                {!source.is_active && (
                  <span className="text-muted-foreground text-xs">사용 안 함</span>
                )}
                {source.last_status && (
                  <span
                    className={
                      source.last_status === 'ok'
                        ? 'text-xs text-emerald-700 dark:text-emerald-400'
                        : 'text-destructive text-xs'
                    }
                  >
                    마지막 {source.last_status === 'ok' ? '성공' : '실패'}
                    {source.last_run_at && ` · ${shownDateTime(source.last_run_at)}`}
                  </span>
                )}
                <span className="ml-auto flex gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === source.slug}
                    onClick={() => plan(source)}
                  >
                    <RefreshCw className="mr-1 size-3.5" />
                    동기화
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEditing(source)}>
                    수정
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="데이터 소스 삭제"
                    onClick={() => setRemoving(source)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </span>
              </div>
              <p className="text-muted-foreground font-mono text-xs break-all">
                {source.kind === 'file'
                  ? source.entity_set
                  : `${source.base_url}/${source.entity_set.replace(/^\//, '')}`}
                {source.kind === 'odata' && source.filter && ` ?$filter=${source.filter}`}
              </p>
              <p className="text-muted-foreground text-xs">
                {KIND_LABEL[source.kind] ?? source.kind} → {source.type_slug}
                {source.workspace_slug ? ` · ${source.workspace_slug} 부서` : ' · 전역'}
                {source.interval_minutes > 0
                  ? ` · ${source.interval_minutes}분마다`
                  : ' · 손으로만'}
                {source.deprecate_missing && ' · 사라진 행은 사용 중지'}
                {source.auth_kind !== 'none' && ` · 인증 ${source.auth_kind}`}
              </p>
              {opened === source.slug && <Runs source={source} />}
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <EditDialog
          source={editing === 'new' ? null : editing}
          types={(schema.data?.types ?? []).filter((one) => one.kind_class !== 'system')}
          workspaceSlugs={(workspaces.data ?? []).map((one) => one.slug)}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            list.reload()
          }}
        />
      )}

      {syncing && (
        <SyncDialog
          source={syncing.source}
          result={syncing.result}
          onClose={() => {
            setSyncing(null)
            list.reload()
          }}
        />
      )}

      <ConfirmDialog
        open={removing !== null}
        title={`「${removing?.name}」 을 삭제합니다`}
        description="가져온 객체는 남습니다. 그 객체에 남긴 바깥 식별자도 남아서, 같은 slug 로 다시 만들면 이어서 찾습니다."
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          if (removing) await datasourceApi.remove(removing.slug)
          setRemoving(null)
          list.reload()
        }}
        onClose={() => setRemoving(null)}
      />
    </div>
  )
}

/** 최근 동기화 기록. */
function Runs({ source }: { source: DataSource }) {
  const runs = useResource(() => datasourceApi.runs(source.slug), [source.slug])
  const rows = runs.data ?? []
  const [shown, setShown] = useState<string | null>(null)
  return (
    <div className="mt-2 space-y-1 rounded-md border p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">최근 동기화 {rows.length}건</span>
        <Button size="sm" variant="ghost" onClick={runs.reload}>
          <RefreshCw className="size-3.5" />
        </Button>
      </div>
      {runs.data && rows.length === 0 && (
        <p className="text-muted-foreground">아직 돌린 적이 없습니다.</p>
      )}
      {rows.map((run) => (
        <div key={run.id} className="flex flex-wrap items-center gap-2">
          <span
            className={
              run.status === 'ok'
                ? 'text-emerald-700 dark:text-emerald-400'
                : run.status === 'failed'
                  ? 'text-destructive'
                  : 'text-muted-foreground'
            }
          >
            {run.status === 'ok' ? '적용' : run.status === 'failed' ? '실패' : '계획만'}
          </span>
          <span className="text-muted-foreground">{shownDateTime(run.started_at)}</span>
          <span>{run.actor_label}</span>
          <span className="text-muted-foreground">
            행 {run.rows_seen} ·{' '}
            {Object.entries(run.counts)
              .filter(([, n]) => n > 0)
              .map(([k, n]) => `${COUNT_LABEL[k] ?? k} ${n}`)
              .join(' · ')}
          </span>
          {run.errors.length > 0 && (
            <button
              type="button"
              className="text-destructive hover:underline"
              onClick={() => setShown(shown === run.id ? null : run.id)}
            >
              오류 {run.errors.length}
            </button>
          )}
          {shown === run.id && (
            <ul className="bg-muted w-full rounded p-2">
              {run.errors.map((one, index) => (
                <li key={index}>{one}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}

const COUNT_LABEL: Record<string, string> = {
  create: '새로',
  update: '고침',
  unchanged: '그대로',
  error: '오류',
  deprecated: '사용 중지',
}

/** 계획을 보고 적용한다 — 일괄 입력의 계획 창과 같은 무늬. */
function SyncDialog({
  source,
  result: initial,
  onClose,
}: {
  source: DataSource
  result: SyncResult
  onClose: () => void
}) {
  const [result, setResult] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const ok = result.errors.length === 0 && (result.counts.error ?? 0) === 0 && !result.truncated
  const problems = result.rows.filter((row) => row.action === 'error')

  async function apply() {
    setBusy(true)
    setError(null)
    try {
      setResult(await datasourceApi.sync(source.slug, true))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {source.name} — {result.applied ? '적용했습니다' : '동기화 계획'}
          </DialogTitle>
          <DialogDescription>
            외부에서 {result.run.rows_seen}행을 조회했습니다.{' '}
            {Object.entries(result.counts)
              .filter(([, n]) => n > 0)
              .map(([k, n]) => `${COUNT_LABEL[k] ?? k} ${n}`)
              .join(' · ')}
            {!result.applied && !ok && ' — 오류가 있어 아무것도 추가하지 않습니다.'}
          </DialogDescription>
        </DialogHeader>
        {error && <ErrorNotice error={error} />}
        {result.errors.length > 0 && (
          <ul className="text-destructive space-y-1 text-sm">
            {result.errors.map((one, index) => (
              <li key={index}>{one}</li>
            ))}
          </ul>
        )}
        {problems.length > 0 && (
          <div className="max-h-64 overflow-auto rounded-md border text-xs">
            <table className="w-full">
              <tbody>
                {problems.slice(0, 100).map((row) => (
                  <tr key={row.row} className="border-b">
                    <td className="text-muted-foreground px-2 py-1 tabular-nums">{row.row}</td>
                    <td className="px-2 py-1">{row.label}</td>
                    <td className="text-destructive px-2 py-1">{row.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!result.applied && ok && (
          <p className="text-muted-foreground text-sm">
            같은 객체(바깥 식별자·식별자·별칭·이름 순)는 수정하고, 없으면 새로 만듭니다. 빈 칸은
            변경하지 않습니다.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {result.applied ? '닫기' : '취소'}
          </Button>
          {!result.applied && (
            <Button onClick={apply} disabled={busy || !ok}>
              적용 — {result.counts.create ?? 0}개 새로, {result.counts.update ?? 0}개 고침
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditDialog({
  source,
  types,
  workspaceSlugs,
  onClose,
  onSaved,
}: {
  source: DataSource | null
  types: { slug: string; label: string; properties: PropertyDef[] }[]
  workspaceSlugs: string[]
  onClose: () => void
  onSaved: () => void
}) {
  const [slug, setSlug] = useState(source?.slug ?? '')
  const [name, setName] = useState(source?.name ?? '')
  const [kind, setKind] = useState<SourceKind>(source?.kind ?? 'odata')
  const [options, setOptions] = useState<SourceOptions>(source?.options ?? {})
  const [baseUrl, setBaseUrl] = useState(source?.base_url ?? '')
  const [entitySet, setEntitySet] = useState(source?.entity_set ?? '')
  const [filter, setFilter] = useState(source?.filter ?? '')
  const [authKind, setAuthKind] = useState<AuthKind>(source?.auth_kind ?? 'none')
  const [authUser, setAuthUser] = useState(source?.auth_user ?? '')
  const [authSecret, setAuthSecret] = useState('')
  const [typeSlug, setTypeSlug] = useState(source?.type_slug ?? types[0]?.slug ?? '')
  const [workspace, setWorkspace] = useState(source?.workspace_slug ?? NONE)
  const [externalKey, setExternalKey] = useState(source?.mapping.external_key ?? '')
  const [columns, setColumns] = useState<MappingColumn[]>(source?.mapping.columns ?? [])
  const [deprecate, setDeprecate] = useState(source?.deprecate_missing ?? false)
  const [interval, setInterval] = useState(String(source?.interval_minutes ?? 0))
  const [active, setActive] = useState(source?.is_active ?? true)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [suggested, setSuggested] = useState<CoreSuggest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const type = types.find((one) => one.slug === typeSlug)
  const propertyTargets = (type?.properties ?? [])
    .filter((def) => def.data_type !== 'file')
    .map((def): [string, string] => [`properties.${def.key}`, `${def.label} (${def.key})`])

  /** 고정 칸(이름·식별자·설명·별칭)으로 가는 열 — 각자 자리 하나씩. */
  const fixedOf = (target: string) => columns.find((one) => one.target === target)?.source ?? ''
  function setFixed(target: string, source: string) {
    const rest = columns.filter((one) => one.target !== target)
    setColumns(source.trim() ? [...rest, { source: source.trim(), target }] : rest)
  }
  /** 속성으로 가는 열만 — 표에는 이것만 선다. */
  const propertyColumns = columns
    .map((column, index) => ({ column, index }))
    .filter(({ column }) => column.target.startsWith('properties.') || column.target === '')
  function patchColumn(index: number, patch: Partial<MappingColumn>) {
    setColumns(columns.map((one, i) => (i === index ? { ...one, ...patch } : one)))
  }

  function body(): DataSourceWrite {
    const out: DataSourceWrite = {
      slug: slug.trim(),
      name: name.trim(),
      kind,
      base_url: kind === 'file' ? '' : baseUrl.trim(),
      entity_set: entitySet.trim(),
      options,
      filter: filter.trim(),
      auth_kind: authKind,
      auth_user: authUser.trim(),
      type_slug: typeSlug,
      workspace_slug: workspace === NONE ? null : workspace,
      // 빈 줄(열 이름을 아직 안 적은 속성 줄)은 보내지 않는다 — 서버가 「source 가 없다」 로 거절한다.
      mapping: {
        external_key: externalKey.trim(),
        columns: columns.filter((one) => one.source.trim() && one.target),
      },
      deprecate_missing: deprecate,
      interval_minutes: Number(interval) || 0,
      is_active: active,
    }
    // 비밀은 **적었을 때만** 보낸다 — 빈 값으로 보내면 있던 비밀이 지워진다.
    if (authSecret || !source) out.auth_secret = authSecret
    return out
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      if (source) await datasourceApi.update(source.slug, body())
      else await datasourceApi.create(body())
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  /** 미리 보기 — 저장한 뒤 앞의 몇 행을 읽어 온다. 열 이름이 여기서 보이면 칸 대응이 쉽다. */
  async function peek() {
    setBusy(true)
    setError(null)
    try {
      // 미리 보기는 칸 대응을 **맞추기 전**에 누른다 — 아직 덜 된 대응은 보내지 않는다
      // (서버가 「이름 열이 없다」 로 거절하면 열 이름을 볼 길이 없다).
      const draft = body()
      const complete = Boolean(externalKey.trim() && fixedOf('label'))
      const payload = complete ? draft : { ...draft, mapping: {} }
      if (source) await datasourceApi.update(source.slug, payload)
      else await datasourceApi.create(payload)
      setPreview(await datasourceApi.preview(slug.trim()))
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  /**
   * 형제 설치의 카탈로그를 읽어 **대응 초안**을 채운다.
   *
   * 저장한 뒤에 부른다 — 상대의 토큰이 저장돼 있어야 읽을 수 있고, 비밀을 화면이 들고
   * 다니지 않게 한다. 채우기만 하고 **저장은 사람이 누른다**(초안을 보고 고칠 자리가 있어야
   * 한다).
   */
  async function suggest() {
    setBusy(true)
    setError(null)
    try {
      const draft = body()
      const complete = Boolean(externalKey.trim() && fixedOf('label'))
      const payload = complete ? draft : { ...draft, mapping: {} }
      if (source) await datasourceApi.update(source.slug, payload)
      else await datasourceApi.create(payload)
      const found = await datasourceApi.coreSuggest(slug.trim())
      setSuggested(found)
      setExternalKey(found.mapping.external_key ?? 'key')
      setColumns(found.mapping.columns ?? [])
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const sourceColumns = preview?.columns ?? []

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{source ? '데이터 소스 수정' : '데이터 소스 생성'}</DialogTitle>
          <DialogDescription>
            OData 서비스의 한 엔티티 셋을 한 타입으로. 「미리 보기」 로 열 이름을 받아 칸 대응을
            맞추고, 목록에서 「동기화」 로 계획을 본 뒤 적용합니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {error && <ErrorNotice error={error} />}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ds-slug">slug {source && '(바꿀 수 없음)'}</Label>
              <Input
                id="ds-slug"
                value={slug}
                disabled={Boolean(source)}
                placeholder="plm_suppliers"
                onChange={(event) => setSlug(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ds-name">이름</Label>
              <Input id="ds-name" value={name} onChange={(event) => setName(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>종류</Label>
              <Select value={kind} onValueChange={(next) => setKind(next as SourceKind)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="odata">OData (v4 · v2)</SelectItem>
                  <SelectItem value="rest">REST JSON</SelectItem>
                  <SelectItem value="file">파일 (CSV · Excel · JSON)</SelectItem>
                  <SelectItem value="sp_core">형제 Standard Platform (코어)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>인증</Label>
              <Select value={authKind} onValueChange={(next) => setAuthKind(next as AuthKind)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">없음</SelectItem>
                  <SelectItem value="basic">Basic (아이디·비밀번호)</SelectItem>
                  <SelectItem value="bearer">Bearer 토큰</SelectItem>
                  <SelectItem value="header">헤더 직접 지정 (X-API-Key 같은 것)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {(authKind === 'basic' || authKind === 'header') && (
              <div className="space-y-1.5">
                <Label htmlFor="ds-user">{authKind === 'basic' ? '아이디' : '헤더 이름'}</Label>
                <Input
                  id="ds-user"
                  value={authUser}
                  placeholder={authKind === 'header' ? 'X-API-Key' : ''}
                  onChange={(event) => setAuthUser(event.target.value)}
                />
              </div>
            )}
            {authKind !== 'none' && (
              <div className="space-y-1.5">
                <Label htmlFor="ds-secret">
                  {authKind === 'basic' ? '비밀번호' : authKind === 'header' ? '헤더 값' : '토큰'}{' '}
                  {source?.has_secret && '(비우면 그대로)'}
                </Label>
                <Input
                  id="ds-secret"
                  type="password"
                  value={authSecret}
                  onChange={(event) => setAuthSecret(event.target.value)}
                />
              </div>
            )}

            {/* 종류별 — 어디서 읽는가 */}
            {kind !== 'file' && (
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="ds-url">
                  {kind === 'odata'
                    ? 'OData 서비스 루트'
                    : kind === 'sp_core'
                      ? '형제 설치의 API 루트'
                      : 'API 루트'}
                </Label>
                <Input
                  id="ds-url"
                  value={baseUrl}
                  placeholder={
                    kind === 'odata'
                      ? 'https://plm.example.com/odata/v4'
                      : kind === 'sp_core'
                        ? 'https://hub.example.com/api'
                        : 'https://erp.example.com/api'
                  }
                  onChange={(event) => setBaseUrl(event.target.value)}
                />
              </div>
            )}
            <div className={kind === 'file' ? 'space-y-1.5 sm:col-span-2' : 'space-y-1.5'}>
              <Label htmlFor="ds-set">
                {kind === 'odata'
                  ? '엔티티 셋'
                  : kind === 'rest'
                    ? '경로'
                    : kind === 'sp_core'
                      ? '가져올 코어 타입'
                      : '파일 위치 (URL 또는 서버 폴더 아래 경로)'}
              </Label>
              <Input
                id="ds-set"
                value={entitySet}
                placeholder={
                  kind === 'odata'
                    ? 'Suppliers'
                    : kind === 'rest'
                      ? '/v1/suppliers'
                      : kind === 'sp_core'
                        ? 'material'
                        : 'erp/suppliers.xlsx'
                }
                onChange={(event) => setEntitySet(event.target.value)}
              />
              {kind === 'file' && (
                <p className="text-muted-foreground text-xs">
                  서버 폴더는 운영자가 .env 의 DATASOURCE_DIR 로 정합니다 — 그 아래만 읽습니다.
                  형식은 확장자로 압니다(.csv/.xlsx/.json).
                </p>
              )}
            </div>
            {kind === 'odata' && (
              <div className="space-y-1.5">
                <Label htmlFor="ds-filter">$filter (선택)</Label>
                <Input
                  id="ds-filter"
                  value={filter}
                  placeholder="Status eq 'Released'"
                  onChange={(event) => setFilter(event.target.value)}
                />
              </div>
            )}
            {kind === 'rest' && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="ds-rows">행이 있는 자리 (비우면 응답 자체가 배열)</Label>
                  <Input
                    id="ds-rows"
                    value={options.rows_path ?? ''}
                    placeholder="items 또는 data.results"
                    onChange={(event) => setOptions({ ...options, rows_path: event.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>쪽 넘김</Label>
                  <Select
                    value={options.paging ?? 'none'}
                    onValueChange={(next) =>
                      setOptions({ ...options, paging: next as SourceOptions['paging'] })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">없음 — 한 번에 전부</SelectItem>
                      <SelectItem value="page">page=1,2,… (쪽이 덜 차면 끝)</SelectItem>
                      <SelectItem value="offset">offset=0,N,2N…</SelectItem>
                      <SelectItem value="cursor">응답의 다음 커서/링크</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {options.paging && options.paging !== 'none' && (
                  <div className="space-y-1.5">
                    <Label htmlFor="ds-size">쪽 크기 파라미터 이름</Label>
                    <Input
                      id="ds-size"
                      value={options.size_param ?? ''}
                      placeholder="page_size (또는 limit)"
                      onChange={(event) =>
                        setOptions({ ...options, size_param: event.target.value || undefined })
                      }
                    />
                  </div>
                )}
                {options.paging === 'page' && (
                  <div className="space-y-1.5">
                    <Label htmlFor="ds-page">쪽 번호 파라미터 이름</Label>
                    <Input
                      id="ds-page"
                      value={options.page_param ?? ''}
                      placeholder="page"
                      onChange={(event) =>
                        setOptions({ ...options, page_param: event.target.value || undefined })
                      }
                    />
                  </div>
                )}
                {options.paging === 'offset' && (
                  <div className="space-y-1.5">
                    <Label htmlFor="ds-offset">오프셋 파라미터 이름</Label>
                    <Input
                      id="ds-offset"
                      value={options.offset_param ?? ''}
                      placeholder="offset"
                      onChange={(event) =>
                        setOptions({ ...options, offset_param: event.target.value || undefined })
                      }
                    />
                  </div>
                )}
                {options.paging === 'cursor' && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="ds-cpath">응답에서 다음 커서가 있는 자리</Label>
                      <Input
                        id="ds-cpath"
                        value={options.cursor_path ?? ''}
                        placeholder="next 또는 meta.next"
                        onChange={(event) =>
                          setOptions({ ...options, cursor_path: event.target.value || undefined })
                        }
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="ds-cparam">커서를 보낼 파라미터 이름</Label>
                      <Input
                        id="ds-cparam"
                        value={options.cursor_param ?? ''}
                        placeholder="cursor"
                        onChange={(event) =>
                          setOptions({ ...options, cursor_param: event.target.value || undefined })
                        }
                      />
                    </div>
                  </>
                )}
              </>
            )}
            {kind === 'file' && (
              <div className="space-y-1.5">
                <Label htmlFor="ds-sheet">시트 이름 (Excel, 비우면 첫 시트)</Label>
                <Input
                  id="ds-sheet"
                  value={options.sheet ?? ''}
                  onChange={(event) =>
                    setOptions({ ...options, sheet: event.target.value || undefined })
                  }
                />
              </div>
            )}
            <div className="space-y-1.5">
              <Label>넣을 타입</Label>
              <Select value={typeSlug} onValueChange={setTypeSlug}>
                <SelectTrigger>
                  <SelectValue />
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
            <div className="space-y-1.5">
              <Label>새 객체의 소유 부서</Label>
              <Select value={workspace} onValueChange={setWorkspace}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>전역</SelectItem>
                  {workspaceSlugs.map((one) => (
                    <SelectItem key={one} value={one}>
                      {one}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* --- 칸 대응 — 역할별로 가른다. 식별자·이름은 각자 자리에서, 속성은 표에서. --- */}
          <div className="space-y-4 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <Label>칸 대응 — 바깥 열이 우리 객체의 어느 칸으로 가는가</Label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={
                  busy || !slug.trim() || (kind !== 'file' && !baseUrl.trim()) || !entitySet.trim()
                }
                onClick={peek}
              >
                <Eye className="mr-1 size-3.5" />
                미리 보기 (저장하고 앞 5행 읽기)
              </Button>
            </div>
            {/* **옮겨 적게 하지 않는다** — 상대가 칸을 하나 더하는 날 그 대응이 조용히
                뒤처진다. 카탈로그를 읽어 채우고, 못 이은 것은 아래에 까닭과 함께 선다. */}
            {kind === 'sp_core' && (
              <div className="space-y-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || !slug.trim() || !baseUrl.trim() || !entitySet.trim()}
                  onClick={suggest}
                >
                  <Wand2 className="mr-1 size-3.5" />
                  대응 자동 제안 (상대 카탈로그 읽기)
                </Button>
                {suggested && (
                  <div className="space-y-1 rounded border p-2 text-xs">
                    <p>
                      <b>{suggested.system}</b> 의 「{suggested.type_label}」 — {suggested.count}건
                      <span className="text-muted-foreground"> · 판 {suggested.revision}</span>
                    </p>
                    {suggested.notes.length > 0 ? (
                      <ul className="text-muted-foreground list-inside list-disc">
                        {suggested.notes.map((one) => (
                          <li key={one}>{one}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-muted-foreground">상대의 칸이 모두 이어졌습니다.</p>
                    )}
                    <p className="text-muted-foreground">
                      초안입니다 — 고칠 것이 있으면 아래에서 고친 뒤 저장하세요.
                    </p>
                  </div>
                )}
              </div>
            )}
            {preview?.mapping_error && (
              <p className="text-destructive text-xs">{preview.mapping_error}</p>
            )}
            {sourceColumns.length > 0 && (
              <p className="text-muted-foreground text-xs">
                바깥 열:{' '}
                {sourceColumns.map((one) => (
                  <code key={one} className="mr-1">
                    {one}
                  </code>
                ))}
              </p>
            )}
            <datalist id="ds-columns">
              {sourceColumns.map((one) => (
                <option key={one} value={one} />
              ))}
            </datalist>

            {/* ① 같은 것 검색 */}
            <div className="space-y-1.5">
              <p className="text-sm font-medium">① 같은 것 검색</p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1">
                  <Label htmlFor="ds-ext" className="text-xs">
                    바깥 식별자 열
                  </Label>
                  <Input
                    id="ds-ext"
                    value={externalKey}
                    placeholder="SupplierID"
                    list="ds-columns"
                    className="w-48"
                    onChange={(event) => setExternalKey(event.target.value)}
                  />
                </div>
                <label className="flex cursor-pointer items-center gap-2 pb-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={fixedOf('key') === externalKey.trim() && externalKey.trim() !== ''}
                    onChange={(event) =>
                      setFixed('key', event.target.checked ? externalKey.trim() : '')
                    }
                  />
                  우리 식별자(key)로도 쓴다
                </label>
              </div>
              <p className="text-muted-foreground text-xs">
                이 열의 값이 그 객체에 남아, 다음 동기화가 같은 객체를 다시 찾습니다 — 우리 쪽
                이름·식별자를 수정해도 유지됩니다.
              </p>
            </div>

            {/* ② 이름 */}
            <div className="space-y-1.5">
              <p className="text-sm font-medium">② 이름</p>
              <div className="space-y-1">
                <Label htmlFor="ds-label" className="text-xs">
                  이름 열 (필수)
                </Label>
                <Input
                  id="ds-label"
                  value={fixedOf('label')}
                  placeholder="CompanyName"
                  list="ds-columns"
                  className="w-48"
                  onChange={(event) => setFixed('label', event.target.value)}
                />
              </div>
            </div>

            {/* ③ 속성 */}
            <div className="space-y-1.5">
              <p className="text-sm font-medium">③ 속성</p>
              {propertyTargets.length === 0 && (
                <p className="text-muted-foreground text-xs">
                  이 타입에 속성이 없습니다. 관리 › 온톨로지에서 먼저 만드세요.
                </p>
              )}
              {propertyColumns.map(({ column, index }) => (
                <div key={index} className="flex flex-wrap items-start gap-2">
                  <Input
                    value={column.source}
                    placeholder="바깥 열"
                    list="ds-columns"
                    className="w-40"
                    onChange={(event) => patchColumn(index, { source: event.target.value })}
                  />
                  <span className="text-muted-foreground pt-2 text-sm">→</span>
                  <Select
                    value={column.target}
                    onValueChange={(next) => patchColumn(index, { target: next })}
                  >
                    <SelectTrigger className="w-52">
                      <SelectValue placeholder="어느 속성으로" />
                    </SelectTrigger>
                    <SelectContent>
                      {propertyTargets.map(([key, text]) => (
                        <SelectItem key={key} value={key}>
                          {text}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Textarea
                    defaultValue={column.values ? JSON.stringify(column.values) : ''}
                    placeholder='값 대응표 (선택) {"US": "미국"}'
                    rows={1}
                    className="min-h-9 flex-1 font-mono text-xs"
                    onBlur={(event) => {
                      const text = event.target.value.trim()
                      try {
                        patchColumn(index, {
                          values: text ? (JSON.parse(text) as Record<string, unknown>) : undefined,
                        })
                      } catch {
                        setError(new Error(`값 대응표가 JSON 이 아닙니다: ${text.slice(0, 40)}`))
                      }
                    }}
                  />
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label="속성 대응 삭제"
                    onClick={() => setColumns(columns.filter((_one, i) => i !== index))}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
              {propertyTargets.length > 0 && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    setColumns([...columns, { source: '', target: propertyTargets[0][0] }])
                  }
                >
                  <Plus className="mr-1 size-4" />
                  속성 추가
                </Button>
              )}
              <p className="text-muted-foreground text-xs">
                값 대응표에 없는 값이 오면 그 행은 오류입니다(조용히 통과시키면 선택할 값이
                오염됩니다). 참조 속성은 상대의 식별자·별칭·이름으로 풀리고, 못 풀면 오류 행입니다.
              </p>
            </div>

            {/* ④ 그 밖에 */}
            <div className="space-y-1.5">
              <p className="text-sm font-medium">④ 그 밖에 (선택)</p>
              <div className="flex flex-wrap gap-3">
                <div className="space-y-1">
                  <Label htmlFor="ds-desc" className="text-xs">
                    설명 열
                  </Label>
                  <Input
                    id="ds-desc"
                    value={fixedOf('description')}
                    list="ds-columns"
                    className="w-48"
                    onChange={(event) => setFixed('description', event.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="ds-alias" className="text-xs">
                    다른 이름(별칭) 열
                  </Label>
                  <Input
                    id="ds-alias"
                    value={fixedOf('alias')}
                    placeholder="ShortName"
                    list="ds-columns"
                    className="w-48"
                    onChange={(event) => setFixed('alias', event.target.value)}
                  />
                </div>
              </div>
            </div>

            {preview && preview.mapped.length > 0 && (
              <div className="max-h-40 overflow-auto rounded border text-xs">
                <table className="w-full">
                  <tbody>
                    {preview.mapped.map((one, index) => (
                      <tr key={index} className="border-b">
                        <td className="px-2 py-1 font-mono">{one.external_id}</td>
                        <td className="px-2 py-1">
                          {one.error ? (
                            <span className="text-destructive">{one.error}</span>
                          ) : (
                            JSON.stringify(one.row)
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ds-interval">몇 분마다 (0 = 손으로만)</Label>
              <Input
                id="ds-interval"
                type="number"
                min={0}
                value={interval}
                onChange={(event) => setInterval(event.target.value)}
              />
              {/* 분으로만 적게 하면 「하루 한 번」 을 적으려고 계산기를 켠다. */}
              <p className="text-muted-foreground text-xs">
                1440 = 하루 한 번 · 60 = 한 시간마다. <b>0 이어도 목록의 「동기화」 로 언제든</b>{' '}
                돌릴 수 있습니다.
              </p>
            </div>
            <div className="space-y-2 pt-6">
              {/* **증분 소스에는 이 규칙이 없다.** 코어는 지난번 이후만 오므로 「안 온 것」 이
                  대부분이다 — 켜면 다음 날 멀쩡한 객체 전부가 사용 중지가 된다. 사라진 것은
                  상대가 무덤으로 알려 준다. */}
              {kind !== 'sp_core' ? (
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={deprecate}
                    onChange={(event) => setDeprecate(event.target.checked)}
                  />
                  외부에서 삭제된 행은 「사용 중지」 로 표시 (기본은 변경하지 않음)
                </label>
              ) : (
                <p className="text-muted-foreground text-sm">
                  상대에서 <b>지워진 것은 그쪽이 알려 줍니다</b> — 이쪽에서 자동으로 「사용 중지」
                  가 됩니다(합쳐진 것이면 이긴 쪽이 이력에 남습니다).
                </p>
              )}
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={active}
                  onChange={(event) => setActive(event.target.checked)}
                />
                사용
              </label>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            취소
          </Button>
          <Button
            onClick={save}
            disabled={
              busy ||
              !slug.trim() ||
              !name.trim() ||
              (kind !== 'file' && !baseUrl.trim()) ||
              !entitySet.trim() ||
              !typeSlug ||
              !externalKey.trim() ||
              !fixedOf('label')
            }
          >
            저장
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
