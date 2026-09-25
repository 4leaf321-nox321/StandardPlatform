/**
 * 디지털 트윈 › 일괄 입력 — **현재값을 표로 받아, 고쳐서 한 번에 되돌린다.**
 *
 * 한 줄씩 창을 열어 고치는 길만 있으면 스무 건이 넘는 순간 아무도 최신으로 유지하지
 * 않는다 — 그리고 안 채운 자료는 「모름」 과 구별되지 않는다. 그래서 이 화면이 있다.
 *
 *   1. 축을 고르면 **현재값이 채워진 표**가 뜬다(연계마다 한 줄)
 *   2. 표에서 바로 고치거나, 엑셀에서 복사해 **붙여넣는다**
 *   3. 저장하면 **줄마다 결과**가 온다 — 한 줄이 틀려도 나머지는 저장된다
 *
 * ⚠️ **빈 칸은 건너뛴다**(지우기가 아니다). 엑셀에서 일부만 채워 보내는 일이 흔한데, 빈
 *    칸을 「지움」 으로 읽으면 한 번의 붙여넣기가 남의 평가를 지운다.
 * ⚠️ 수준은 **이름**으로 주고받는다(「우열 판정」). key 를 적게 하면 아무도 못 채운다.
 */

import { Check, Download, RefreshCw, Save, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { objectApi } from '@/modules/objects/api'
import { workspaceApi } from '@/modules/workspaces/api'
import { downloadFile } from '@/shared/api/client'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { PasteGrid, isChecked, type GridColumn } from '@/shared/components/PasteGrid'
import { Button } from '@/shared/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { useResource } from '@/shared/hooks/useResource'

import { dtApi, type AxisDef, type BulkResult, type Sheet } from './api'

/** 이 축에서 **고르는 항목의 이름들** — 표는 항목마다 한 열을 둔다.
 *
 * 매트릭스는 **바탕**(형상 · 거동)만 고른다 — 불량 유형별 재현은 시험 항목마다 유형이
 * 달라 열로 세울 수 없다. `hide_empty` 축의 첫 칸(수동 · 없음)은 「아무것도 안 켠 것」
 * 이라 열이 없다.
 */
function picksOf(axis: AxisDef): string[] {
  if (axis.kind === 'matrix') return (axis.base ?? []).map((one) => one.label)
  return (axis.hide_empty ? axis.rungs.slice(1) : axis.rungs).map((one) => one.label)
}

/** 체크 열의 key — 열 순서가 축마다 달라, 저장할 때 **key 로 찾는다.** */
const PICK = 'pick:'

/** 축 종류마다 고치는 칸이 다르다 — 식별 칸 셋은 같다.
 *
 * **이름 칸에는 등록된 목록을 준다**(드롭다운). 손으로 치게 하면 「낙하시험」 과
 * 「낙하 시험」 이 섞이고, 그 줄은 저장 자리에서 「못 찾음」 이 된다.
 *
 * 여러 항목을 고르는 축(자동화 · 시험 대체 · 모델링 바탕)은 **항목마다 한 열**을 두고
 * 체크한다 — 한 칸에 이름을 이어 적게 하면 이름을 정확히 외워야 하고, 엑셀에서 채우기도
 * 어렵다.
 */
function columnsFor(
  axis: AxisDef,
  known: { subjects: string[]; agents: string[] },
): GridColumn[] {
  const head: GridColumn[] = [
    {
      key: 'subject_label',
      header: '시험 항목',
      help: '이 이름으로 연계를 찾습니다',
      options: known.subjects,
    },
    { key: 'agent_label', header: '시뮬레이션 해석', options: known.agents },
    { key: 'dept', header: '담당 부서', help: '읽기용', readOnly: true },
  ]
  const note: GridColumn = {
    key: 'note',
    header: '근거',
    help: '무엇을 보고 매겼는지 — 비우면 그 줄은 저장되지 않습니다',
  }
  if (axis.kind === 'value') {
    return [...head, { key: 'value', header: axis.label, help: `숫자 ${axis.unit ?? ''}`.trim() }, note]
  }
  if (axis.kind === 'rung') {
    const levels = picksOf(axis)
    return [
      ...head,
      { key: 'rung', header: axis.label, help: '목록에서 고릅니다', options: levels },
      note,
    ]
  }
  return [
    ...head,
    ...picksOf(axis).map((label) => ({ key: `${PICK}${label}`, header: label, check: true })),
    note,
  ]
}

function rowsOf(sheet: Sheet, columns: GridColumn[]): string[][] {
  return sheet.rows.map((one) =>
    columns.map((column) => {
      switch (column.key) {
        case 'subject_label':
          return one.subject_label
        case 'agent_label':
          return one.agent_label
        case 'dept':
          return one.agent_dept ?? ''
        case 'value':
          return one.value !== null ? String(one.value) : ''
        case 'rung':
          return one.rung
        case 'note':
          return one.note
        default:
          // 체크 열 — 지금 켜져 있으면 표시된 채로 시작한다.
          return one.rungs.includes(column.key.slice(PICK.length)) ? 'O' : ''
      }
    }),
  )
}

function staffColumns(known: { agents: string[]; workspaces: string[] }): GridColumn[] {
  return [
    { key: 'name', header: '이름', help: '이 이름과 부서로 그 줄을 찾습니다', required: true },
    { key: 'workspace_name', header: '부서', required: true, options: known.workspaces },
    {
      key: 'agents',
      header: '담당 해석',
      help: '여럿이면 · 으로 이어 적습니다 — 몫이 1/n 로 갈립니다',
      options: known.agents,
      multi: true,
    },
    { key: 'outside', header: '조사 밖 업무', help: '있으면 「예」', options: ['예'] },
    { key: 'skill_kinds', header: '역량 분야', help: '담당 해석이 없을 때' },
    { key: 'note', header: '메모' },
  ]
}

export default function BulkPage() {
  const defs = useResource(() => dtApi.defs(), [])
  const [what, setWhat] = useState<'axis' | 'staff'>('axis')
  const [axisKey, setAxisKey] = useState<string | null>(null)
  const axis = defs.data?.axes.find((one) => one.key === (axisKey ?? defs.data?.axes[0]?.key)) ?? null
  const sheet = useResource(
    () => (axis ? dtApi.sheet(axis.key) : Promise.resolve(null)),
    [axis?.key],
  )

  const staffSheet = useResource(() => dtApi.staffSheet(), [])
  // **등록된 목록**을 드롭다운에 준다. 표의 이름 칸은 이 목록에서 고른다.
  const setup = useResource(() => dtApi.setupStatus(), [])
  const subjectSlug = setup.data?.subject_type_slug ?? null
  const agentSlug = setup.data?.agent_type_slug ?? null
  const subjects = useResource(
    () => (subjectSlug ? objectApi.list(subjectSlug, { limit: 500 }) : Promise.resolve(null)),
    [subjectSlug],
  )
  const agentList = useResource(
    () => (agentSlug ? objectApi.list(agentSlug, { limit: 500 }) : Promise.resolve(null)),
    [agentSlug],
  )
  const workspaces = useResource(() => workspaceApi.list(true), [])
  const known = {
    subjects: (subjects.data?.items ?? []).map((one) => one.label),
    agents: (agentList.data?.items ?? []).map((one) => one.label),
    workspaces: (workspaces.data ?? []).map((one) => one.name),
  }
  const [rows, setRows] = useState<string[][]>([])
  const [staffRows, setStaffRows] = useState<string[][]>([])
  const [results, setResults] = useState<BulkResult[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  // **현재값 불러오기.** 축을 바꾸거나 다시 받으면 표가 그 값으로 채워진다.
  useEffect(() => {
    if (sheet.data) {
      setRows(rowsOf(sheet.data, columns))
      setResults(null)
    }
    // 열은 축이 정한다 — 열이 바뀔 때마다 채우면 고치던 값이 지워진다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheet.data])

  // 인력도 **현재값이 채워진 채로** 시작한다.
  useEffect(() => {
    if (staffSheet.data) {
      setStaffRows(
        staffSheet.data.map((one) => [
          one.name,
          one.workspace_name,
          one.agents,
          one.outside,
          one.skill_kinds,
          one.note,
        ]),
      )
    }
  }, [staffSheet.data])

  const columns = useMemo(
    () => (axis ? columnsFor(axis, known) : []),
    // 목록은 불러온 뒤 바뀌지 않는다 — 길이로만 본다(매 렌더 새 배열이라 값 비교가 안 된다).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [axis, known.subjects.length, known.agents.length],
  )
  const staffCols = useMemo(
    () => staffColumns(known),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [known.agents.length, known.workspaces.length],
  )

  // **같은 연계가 두 줄이면 알린다.** 붙여넣기에서 흔하고, 그대로 저장하면 뒤 줄이 앞 줄을
  // 덮어 「내가 적은 값이 아닌 값」 이 남는다.
  const duplicated = useMemo(() => {
    const seen = new Map<string, number>()
    for (const row of rows) {
      const key = `${(row[0] ?? '').trim()}|${(row[1] ?? '').trim()}`
      if (key === '|') continue
      seen.set(key, (seen.get(key) ?? 0) + 1)
    }
    return [...seen.entries()].filter(([, count]) => count > 1).map(([key]) => key.replace('|', ' · '))
  }, [rows])

  async function saveStaff() {
    setBusy(true)
    setFailed(null)
    try {
      const body = staffRows
        .filter((row) => (row[0] ?? '').trim() !== '')
        .map((row) => ({
          name: row[0] ?? '',
          workspace_name: row[1] ?? '',
          agents: row[2] ?? '',
          outside: row[3] ?? '',
          skill_kinds: row[4] ?? '',
          note: row[5] ?? '',
        }))
      if (body.length === 0) return
      setResults(await dtApi.staffBulk(body))
      staffSheet.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!axis) return
    setBusy(true)
    setFailed(null)
    try {
      // 열 순서가 축마다 다르다 — **key 로 찾는다**(자리로 세면 축을 늘릴 때 어긋난다).
      const at = new Map(columns.map((one, index) => [one.key, index]))
      const cell = (row: string[], key: string) => (row[at.get(key) ?? -1] ?? '').trim()
      const checks = columns.filter((one) => one.check)
      const body = rows
        .filter((row) => row.some((one) => one.trim() !== ''))
        .map((row) => ({
          subject_label: cell(row, 'subject_label'),
          agent_label: cell(row, 'agent_label'),
          ...(axis.kind === 'value'
            ? { value: cell(row, 'value') }
            : axis.kind === 'rung'
              ? { rung: cell(row, 'rung') }
              : {
                  // 체크한 열의 **이름**을 모아 보낸다. key 는 화면이 모른다.
                  rungs: checks
                    .filter((one) => isChecked(cell(row, one.key)))
                    .map((one) => one.header),
                }),
          note: cell(row, 'note'),
        }))
      if (body.length === 0) return
      setResults(await dtApi.bulkAssess(axis.key, body))
      sheet.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  const counts = {
    ok: (results ?? []).filter((one) => one.status === 'ok').length,
    skipped: (results ?? []).filter((one) => one.status === 'skipped').length,
    error: (results ?? []).filter((one) => one.status === 'error').length,
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="일괄 입력"
        description="현재값이 채워진 표를 고쳐 한 번에 저장합니다. 엑셀에서 복사해 붙여 넣어도 됩니다 — 빈 칸은 건너뜁니다(지우기가 아닙니다)."
      />

      <ErrorNotice error={failed ?? defs.error ?? sheet.error} />

      <Tabs value={what} onValueChange={(value) => setWhat(value as 'axis' | 'staff')}>
        <TabsList>
          <TabsTrigger value="axis">평가</TabsTrigger>
          <TabsTrigger value="staff">인력</TabsTrigger>
        </TabsList>
      </Tabs>

      {what === 'staff' ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => staffSheet.reload()}
              disabled={busy}
            >
              <RefreshCw className="size-4" /> 현재값 불러오기
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void downloadFile(dtApi.staffFileUrl('xlsx'), '인력-현재값.xlsx')}
            >
              <Download className="size-4" /> 엑셀로 내려받기
            </Button>
            <Button
              className="ml-auto"
              onClick={() => void saveStaff()}
              disabled={busy || staffRows.length === 0}
            >
              <Save className="size-4" /> 저장
            </Button>
          </div>
          <PasteGrid
            columns={staffCols}
            rows={staffRows}
            onRows={setStaffRows}
            header={
              <p className="text-muted-foreground text-sm">
                {staffSheet.data?.length ?? 0}명 · 이름과 부서가 같은 줄을 고칩니다. 새 이름이면
                새로 만듭니다 — 투입률은 적지 않습니다(담당 해석 수로 갈립니다).
              </p>
            }
          />
        </>
      ) : (
      <>
      <div className="flex flex-wrap items-center gap-2">
        <Tabs value={axis?.key ?? ''} onValueChange={setAxisKey}>
          <TabsList>
            {(defs.data?.axes ?? []).map((one) => (
              <TabsTrigger key={one.key} value={one.key}>
                {one.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <Button variant="outline" size="sm" onClick={() => sheet.reload()} disabled={busy}>
          <RefreshCw className="size-4" /> 현재값 불러오기
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!axis}
          onClick={() =>
            axis &&
            void downloadFile(dtApi.sheetFileUrl(axis.key, 'xlsx'), `${axis.label}-현재값.xlsx`)
          }
        >
          <Download className="size-4" /> 엑셀로 내려받기
        </Button>
        <Button className="ml-auto" onClick={() => void save()} disabled={busy || rows.length === 0}>
          <Save className="size-4" /> 저장
        </Button>
      </div>

      {axis?.kind === 'matrix' && (
        <p className="text-muted-foreground rounded-md border p-3 text-sm">
          {axis.label} 은 바탕(형상 · 거동)만 이 표에서 고칩니다. 불량 유형별 재현은 「역량」
          화면에서 표로 입력하고, 이 표의 저장은 그 표시를 건드리지 않습니다 — 유형이 시험
          항목마다 다르기 때문입니다.
        </p>
      )}
      {(axis?.kind === 'set' || axis?.kind === 'matrix') && (
        <p className="text-muted-foreground text-sm">
          한 줄의 체크를 전부 끄는 것은 이 표에서 할 수 없습니다(빈 줄은 건너뜁니다) — 「역량」
          화면에서 고칩니다.
        </p>
      )}

      {duplicated.length > 0 && (
        <p className="text-sm text-amber-600 dark:text-amber-500">
          같은 연계가 두 번 적힌 줄이 있습니다 — {duplicated.slice(0, 3).join(', ')}
          {duplicated.length > 3 && ` 외 ${duplicated.length - 3}건`}. 그대로 저장하면 뒤 줄이 앞
          줄을 덮습니다.
        </p>
      )}

      <PasteGrid
        columns={columns}
        rows={rows}
        onRows={setRows}
        header={
          <p className="text-muted-foreground text-sm">
            {sheet.data?.rows.length ?? 0}개 연계 · 값을 고치고 저장합니다.{' '}
            {axis?.kind === 'set' || axis?.kind === 'matrix'
              ? '해당하는 항목의 칸을 체크합니다 — 엑셀에서는 그 칸에 O 를 적어 붙여 넣습니다.'
              : axis?.kind === 'rung'
                ? `수준은 이름으로 적습니다(예: ${axis.rungs.at(-1)?.label}).`
                : ''}
          </p>
        }
      />
      </>
      )}

      {results && (
        <section className="space-y-2">
          <h2 className="flex flex-wrap items-center gap-3 text-sm font-semibold">
            결과
            <span className="text-muted-foreground font-normal">
              저장 {counts.ok} · 건너뜀 {counts.skipped} · 오류 {counts.error}
            </span>
          </h2>
          <ul className="space-y-1 text-sm">
            {results
              .filter((one) => one.status !== 'ok')
              .map((one) => (
                <li key={one.line} className="flex items-baseline gap-2">
                  <span className="text-muted-foreground text-xs tabular-nums">{one.line}줄</span>
                  {one.status === 'error' ? (
                    <TriangleAlert className="text-destructive size-4 shrink-0" />
                  ) : (
                    <Check className="text-muted-foreground size-4 shrink-0" />
                  )}
                  <span className={one.status === 'error' ? 'text-destructive' : undefined}>
                    {one.message}
                  </span>
                </li>
              ))}
            {counts.error === 0 && counts.skipped === 0 && (
              <li className="text-muted-foreground">모두 저장했습니다.</li>
            )}
          </ul>
        </section>
      )}
    </div>
  )
}
