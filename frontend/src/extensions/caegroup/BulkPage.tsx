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

import {
  axesFor,
  dtApi,
  markColumnsFor,
  type AxisDef,
  type BulkResult,
  type CapacityWhat,
  type PairKind,
  type Sheet,
} from './api'

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
/** 재현 열의 key(시험 · 시장) — 칸에는 그 줄의 불량 유형 이름이 담긴다. */
const MARK = 'mark:'

/** 축 종류마다 고치는 칸이 다르다 — 식별 칸 셋은 같다.
 *
 * **이름 칸에는 등록된 목록을 준다**(드롭다운). 손으로 치게 하면 「낙하시험」 과
 * 「낙하 시험」 이 섞이고, 그 줄은 저장 자리에서 「못 찾음」 이 된다.
 *
 * 여러 항목을 고르는 축(자동화 · 시험 대체 · 모델링 바탕)은 **항목마다 한 열**을 두고
 * 체크한다 — 한 칸에 이름을 이어 적게 하면 이름을 정확히 외워야 하고, 엑셀에서 채우기도
 * 어렵다.
 *
 * 매트릭스의 **불량 유형별 재현**은 재현마다(시험 · 시장) 한 열이고, 칸에서 **그 줄의**
 * 불량 유형을 골라 담는다. 유형은 시험 항목에 붙어 줄마다 다르므로 열로 펼칠 수 없다 —
 * 펼치면 열이 줄마다 달라지고, 한 표에 다 세우면 대부분이 빈 칸인 넓은 표가 된다.
 */
function columnsFor(
  axis: AxisDef,
  known: { subjects: string[]; agents: string[] },
  /** 대상 이름 → 그 항목이 든 불량 유형 목록. 재현 칸의 드롭다운이 이것을 쓴다. */
  defectTypes: Map<string, string[]> = new Map(),
  kind: PairKind = 'test',
): GridColumn[] {
  const head: GridColumn[] = [
    {
      key: 'subject_label',
      // 대상의 이름은 **종류마다 다르다** — 전용 검토의 대상은 시험이 아니다.
      header: kind === 'sim_only' ? '전용 검토 항목' : '시험 항목',
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
    // 재현 열 — 칸에서 **그 줄의** 불량 유형을 고른다(첫 열이 시험 항목이다).
    ...markColumnsFor(axis, kind).map((col) => ({
      key: `${MARK}${col.key}`,
      header: col.label,
      help: '불량 유형을 고릅니다',
      multi: true,
      optionsFor: (row: string[]) => defectTypes.get((row[0] ?? '').trim()) ?? [],
    })),
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
          // 재현 열 — 지금 표시된 불량 유형 이름들.
          if (column.key.startsWith(MARK)) return one.defects[column.key.slice(MARK.length)] ?? ''
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
      // 목록 단추로 여러 개를 담는다 — 몫이 담당 수로 갈리므로 하나만 담기면 값이 틀린다.
      help: '단추로 여럿 고릅니다 — 몫이 1/n 로 갈립니다',
      options: known.agents,
      multi: true,
    },
    { key: 'outside', header: '조사 밖 업무', help: '있으면 체크', check: true },
    { key: 'skill_kinds', header: '역량 분야', help: '담당 해석이 없을 때 · 으로 이어 적습니다' },
    { key: 'note', header: '메모' },
  ]
}

/** 인프라 표 셋 — **열이 서로 다르다.** 한 표에 섞으면 라이선스 수가 CPU 코어 칸에 들어간다. */
const INFRA_TABS: { key: CapacityWhat; label: string }[] = [
  { key: 'sw', label: 'S/W 라이선스' },
  { key: 'hw', label: '계산 자원' },
  { key: 'base', label: '부서 기준' },
]

function infraColumns(
  kind: CapacityWhat,
  known: { workspaces: string[]; units: string[]; purposes: string[] },
): GridColumn[] {
  const dept: GridColumn = {
    key: 'workspace_name',
    header: '부서',
    help: '이 이름으로 부서를 찾습니다',
    required: true,
    options: known.workspaces,
  }
  if (kind === 'sw') {
    return [
      dept,
      { key: 'name', header: '툴', help: '이 이름으로 전사 합계가 묶입니다', required: true },
      { key: 'quantity', header: '수량', help: '숫자' },
      { key: 'unit', header: '단위', options: known.units },
      { key: 'purpose', header: '용도', options: known.purposes },
      // 공유 자원은 전사 합계에서 **한 번만** 센다 — 부서마다 더하면 이미 있는 것을 또 산다.
      { key: 'shared', header: '전사 공유', help: '합계에서 한 번만 셉니다', check: true },
    ]
  }
  if (kind === 'hw') {
    return [
      dept,
      { key: 'name', header: '자원', help: '이 이름으로 공유를 가립니다', required: true },
      { key: 'cpu_cores', header: 'CPU 코어', help: '숫자' },
      { key: 'ram_gb', header: 'RAM GB', help: '숫자' },
      // GPU 는 **글**이다(「A100 4장」) — 숫자로 강제하면 적을 수 있는 것을 못 적게 만든다.
      { key: 'gpu', header: 'GPU', help: '사양을 글로 적습니다 — 「A100 4장」' },
      { key: 'shared', header: '전사 공유', help: '합계에서 한 번만 셉니다', check: true },
    ]
  }
  return [
    dept,
    { key: 'material_types', header: '물성 종수', help: '숫자 · 비우면 모름(0 종이 아닙니다)' },
    { key: 'has_process_std', header: '공정 표준', help: '있으면 체크', check: true },
    { key: 'note', header: '메모' },
  ]
}

/** 열의 key 는 한 곳에서 나온다 — 표를 채울 때도 저장할 때도 이것으로 찾는다. */
function infraKeys(kind: CapacityWhat): string[] {
  return infraColumns(kind, { workspaces: [], units: [], purposes: [] }).map((one) => one.key)
}

/**
 * 불량 유형 표 — **목록을 늘리는 자리.** 시험 항목마다 한 줄이다.
 *
 * 유형은 시험 항목의 속성이라 저장은 **코어의 객체 저장**이 한다(검증 · 이력 · 권한이 거기
 * 있다). 이 표는 그 속성을 여러 건 한 번에 고치는 길일 뿐이다.
 *
 * 「불량 유형」 칸의 목록은 **이미 쓰는 이름**이다 — 고르기 편하려고 있는 것이고, 새 이름도
 * 그냥 적는다(그래서 붉게 뜨지 않는다).
 */
function defectColumns(known: { subjects: string[]; types: string[] }): GridColumn[] {
  return [
    {
      key: 'subject_label',
      header: '대상',
      help: '이 이름으로 찾습니다',
      required: true,
      options: known.subjects,
    },
    {
      key: 'defect_types',
      header: '불량 유형',
      help: '단추로 이미 쓰는 이름을 고르거나 새로 적습니다',
      options: known.types,
      multi: true,
      free: true,
    },
    {
      key: 'marked',
      header: '재현 표시',
      help: '읽기용 — 표시가 있는 유형입니다. 지우면 그 표시는 셈에서 빠집니다',
      readOnly: true,
    },
  ]
}

export default function BulkPage() {
  const defs = useResource(() => dtApi.defs(), [])
  const [what, setWhat] = useState<'axis' | 'staff' | 'infra' | 'defects'>('axis')
  // 평가 표는 **종류마다** 다르다 — 가상검증률 표에 전용 검토 줄이 뜨면 채울 수 없는 칸이다.
  const [pairKind, setPairKind] = useState<PairKind>('test')
  const [infraKind, setInfraKind] = useState<CapacityWhat>('sw')
  const [axisKey, setAxisKey] = useState<string | null>(null)
  const axes = axesFor(defs.data, pairKind)
  // 종류를 바꾸면 그 종류에 없는 축이 골라져 있을 수 있다 — 그때는 첫 축으로 떨어진다.
  const axis = axes.find((one) => one.key === axisKey) ?? axes[0] ?? null
  const sheet = useResource(
    () => (axis ? dtApi.sheet(axis.key, pairKind) : Promise.resolve(null)),
    [axis?.key, pairKind],
  )

  const staffSheet = useResource(() => dtApi.staffSheet(), [])
  // **등록된 목록**을 드롭다운에 준다. 표의 이름 칸은 이 목록에서 고른다.
  const setup = useResource(() => dtApi.setupStatus(), [])
  const subjectSlug =
    (pairKind === 'sim_only' ? setup.data?.sim_only_type_slug : setup.data?.subject_type_slug) ??
    null
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
  const capacitySheet = useResource(() => dtApi.capacitySheet(), [])
  // 불량 유형 탭에서만 받는다 — **어느 유형에 재현 표시가 있나**를 알려 주려고(지우면 셈에서
  // 빠진다). 다른 탭에서는 부르지 않는다.
  const marksSheet = useResource(
    () => (what === 'defects' ? dtApi.sheet('modeling', pairKind) : Promise.resolve(null)),
    [what, pairKind],
  )
  const [rows, setRows] = useState<string[][]>([])
  const [staffRows, setStaffRows] = useState<string[][]>([])
  // 표마다 따로 든다 — 탭을 옮겨도 고치던 값이 남는다(표마다 따로 저장한다).
  const [infraRows, setInfraRows] = useState<Record<CapacityWhat, string[][]>>({
    sw: [],
    hw: [],
    base: [],
  })
  const [defectRows, setDefectRows] = useState<string[][]>([])
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

  // 인프라도 **현재값이 채워진 채로** 시작한다.
  useEffect(() => {
    const body = capacitySheet.data
    if (!body) return
    const fill = (kind: CapacityWhat, source: Record<string, string>[]) =>
      source.map((one) => infraKeys(kind).map((key) => one[key] ?? ''))
    setInfraRows({
      sw: fill('sw', body.sw),
      hw: fill('hw', body.hw),
      base: fill('base', body.base),
    })
  }, [capacitySheet.data])

  // **시험 항목마다 불량 유형이 다르다** — 표가 줄마다 그 목록을 쓴다(서버가 줄에 실어 준다).
  const defectTypes = useMemo(() => {
    const out = new Map<string, string[]>()
    for (const one of sheet.data?.rows ?? []) {
      if (one.defect_types.length > 0) out.set(one.subject_label, one.defect_types)
    }
    return out
  }, [sheet.data])

  // **어느 유형에 재현 표시가 있나** — 시험 항목 이름마다 모은다(지우면 셈에서 빠진다).
  const markedTypes = useMemo(() => {
    const out = new Map<string, string[]>()
    for (const one of marksSheet.data?.rows ?? []) {
      const names = Object.values(one.defects)
        .flatMap((each) => each.split('·').map((name) => name.trim()))
        .filter(Boolean)
      if (names.length === 0) continue
      const was = out.get(one.subject_label) ?? []
      out.set(one.subject_label, [...new Set([...was, ...names])])
    }
    return out
  }, [marksSheet.data])

  // 불량 유형 표 — **시험 항목마다 한 줄**, 지금 든 유형이 채워진 채로 시작한다.
  const subjectRows = subjects.data?.items ?? []
  useEffect(() => {
    setDefectRows(
      subjectRows.map((one) => [
        one.label,
        (Array.isArray(one.properties.defect_types)
          ? (one.properties.defect_types as unknown[]).map((each) => String(each))
          : []
        ).join(' · '),
        (markedTypes.get(one.label) ?? []).join(' · '),
      ]),
    )
    // 줄은 시험 항목 목록이 정한다 — 재현 표시는 읽기용 칸이라 같이 다시 그린다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjects.data, markedTypes])

  const defectCols = useMemo(
    () =>
      defectColumns({
        subjects: known.subjects,
        // 이미 쓰는 이름 전부 — 철자가 갈리지 않게 고르는 자리다.
        types: [
          ...new Set(
            subjectRows.flatMap((one) =>
              Array.isArray(one.properties.defect_types)
                ? (one.properties.defect_types as unknown[]).map((each) => String(each))
                : [],
            ),
          ),
        ].sort(),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [known.subjects.length, subjects.data],
  )

  const columns = useMemo(
    () => (axis ? columnsFor(axis, known, defectTypes, pairKind) : []),
    // 목록은 불러온 뒤 바뀌지 않는다 — 길이로만 본다(매 렌더 새 배열이라 값 비교가 안 된다).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [axis, known.subjects.length, known.agents.length, defectTypes, pairKind],
  )
  const staffCols = useMemo(
    () => staffColumns(known),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [known.agents.length, known.workspaces.length],
  )
  const infraCols = useMemo(
    () =>
      infraColumns(infraKind, {
        workspaces: known.workspaces,
        // 단위 · 용도의 **이름**은 서버가 내려 준다(키를 저장하고 이름을 보여 준다).
        units: (defs.data?.sw_units ?? []).map((one) => one.label),
        purposes: (defs.data?.sw_purposes ?? []).map((one) => one.label),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [infraKind, known.workspaces.length, defs.data],
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

  /** 지울 유형에 재현 표시가 있나 — **셈에서 빠진다**고 미리 말한다. */
  const droppingMarked = useMemo(() => {
    const out: string[] = []
    for (const row of defectRows) {
      const label = (row[0] ?? '').trim()
      const next = new Set(
        (row[1] ?? '')
          .split('·')
          .map((one) => one.trim())
          .filter(Boolean),
      )
      for (const name of markedTypes.get(label) ?? []) {
        if (!next.has(name)) out.push(`${label} · ${name}`)
      }
    }
    return out
  }, [defectRows, markedTypes])

  async function saveDefects() {
    if (!subjectSlug) return
    setBusy(true)
    setFailed(null)
    try {
      // 이름이 겹치면 가릴 수 없다 — 부서와 같은 이유로 그 줄은 오류다.
      const byLabel = new Map<string, typeof subjectRows>()
      for (const one of subjectRows) {
        byLabel.set(one.label, [...(byLabel.get(one.label) ?? []), one])
      }
      const out: BulkResult[] = []
      for (const [index, row] of defectRows.entries()) {
        const line = index + 1
        const label = (row[0] ?? '').trim()
        if (!label) continue
        const found = byLabel.get(label) ?? []
        if (found.length === 0) {
          out.push({ line, status: 'error', message: `대상을 찾을 수 없습니다: ${label}` })
          continue
        }
        if (found.length > 1) {
          out.push({
            line,
            status: 'error',
            message: `대상 이름이 둘 이상입니다: ${label} — 이름이 겹쳐 가릴 수 없습니다.`,
          })
          continue
        }
        const next = [
          ...new Set(
            (row[1] ?? '')
              .split('·')
              .map((one) => one.trim())
              .filter(Boolean),
          ),
        ]
        const was = Array.isArray(found[0].properties.defect_types)
          ? (found[0].properties.defect_types as unknown[]).map((each) => String(each))
          : []
        if (was.join('\u0000') === next.join('\u0000')) {
          out.push({ line, status: 'skipped', message: '그대로입니다.' })
          continue
        }
        try {
          // **코어의 객체 저장**을 부른다 — 검증 · 이력 · 권한이 거기 있다.
          await objectApi.update(subjectSlug, found[0].id, {
            properties: { defect_types: next },
          })
          out.push({ line, status: 'ok', message: `${label} · ${next.length}종` })
        } catch (caught) {
          out.push({ line, status: 'error', message: (caught as Error).message })
        }
      }
      setResults(out)
      subjects.reload()
      marksSheet.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  async function saveInfra() {
    setBusy(true)
    setFailed(null)
    try {
      const keys = infraKeys(infraKind)
      const body = (infraRows[infraKind] ?? [])
        .filter((row) => row.some((one) => one.trim() !== ''))
        .map((row) => Object.fromEntries(keys.map((key, at) => [key, (row[at] ?? '').trim()])))
      if (body.length === 0) return
      setResults(await dtApi.capacityBulk(infraKind, body))
      capacitySheet.reload()
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
      const marks = columns.filter((one) => one.key.startsWith(MARK))
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
                  // 재현은 **표대로 맞춘다** — 칸을 비우면 그 열의 표시가 없어진다.
                  ...(marks.length > 0
                    ? {
                        defects: Object.fromEntries(
                          marks.map((one) => [one.key.slice(MARK.length), cell(row, one.key)]),
                        ),
                      }
                    : {}),
                }),
          note: cell(row, 'note'),
        }))
      if (body.length === 0) return
      setResults(await dtApi.bulkAssess(axis.key, body, pairKind))
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

      <ErrorNotice error={failed ?? defs.error ?? sheet.error ?? capacitySheet.error} />

      <Tabs value={what} onValueChange={(value) => setWhat(value as typeof what)}>
        <TabsList>
          <TabsTrigger value="axis">평가</TabsTrigger>
          <TabsTrigger value="staff">인력</TabsTrigger>
          <TabsTrigger value="infra">인프라</TabsTrigger>
          <TabsTrigger value="defects">불량 유형</TabsTrigger>
        </TabsList>
      </Tabs>

      {what === 'defects' ? (
        <>
          {/* 불량 유형도 **대상마다** 있다 — 시험 항목과 전용 검토 항목은 다른 목록이다. */}
          {(defs.data?.pair_kinds ?? []).length > 1 && setup.data?.sim_only_type_slug && (
            <Tabs value={pairKind} onValueChange={(value) => setPairKind(value as PairKind)}>
              <TabsList>
                {(defs.data?.pair_kinds ?? []).map((one) => (
                  <TabsTrigger key={one.key} value={one.key}>
                    {one.subject_label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                subjects.reload()
                marksSheet.reload()
              }}
              disabled={busy}
            >
              <RefreshCw className="size-4" /> 현재값 불러오기
            </Button>
            <Button
              className="ml-auto"
              onClick={() => void saveDefects()}
              disabled={busy || !subjectSlug || defectRows.length === 0}
            >
              <Save className="size-4" /> 저장
            </Button>
          </div>

          <p className="text-muted-foreground rounded-md border p-3 text-sm">
            대상마다 <b>재현 대상 불량 유형</b>을 적습니다 — 「신뢰성 시험 불량 재현」 ·
            「시장 불량 재현」 칸에서 고를 수 있는 것이 이 목록입니다. 목록은 이미 쓰는 이름이고,
            새 이름은 그냥 적습니다. 저장은 기준 정보(시험 항목)를 고치는 일이라 그 부서의
            관리자만 할 수 있습니다.
          </p>

          {droppingMarked.length > 0 && (
            <p className="text-sm text-amber-600 dark:text-amber-500">
              재현 표시가 있는 유형을 지웁니다 — {droppingMarked.slice(0, 3).join(', ')}
              {droppingMarked.length > 3 && ` 외 ${droppingMarked.length - 3}건`}. 그 표시는 모델링
              수준의 셈에서 빠집니다(기록은 남습니다).
            </p>
          )}

          <PasteGrid
            columns={defectCols}
            rows={defectRows}
            onRows={setDefectRows}
            header={
              <p className="text-muted-foreground text-sm">
                {pairKind === 'sim_only' ? '전용 검토 항목' : '시험 항목'} {defectRows.length}개 ·
                바뀐 줄만 저장됩니다.
              </p>
            }
          />
        </>
      ) : what === 'infra' ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Tabs value={infraKind} onValueChange={(value) => setInfraKind(value as CapacityWhat)}>
              <TabsList>
                {INFRA_TABS.map((one) => (
                  <TabsTrigger key={one.key} value={one.key}>
                    {one.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <Button
              variant="outline"
              size="sm"
              onClick={() => capacitySheet.reload()}
              disabled={busy}
            >
              <RefreshCw className="size-4" /> 현재값 불러오기
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                void downloadFile(dtApi.capacityAllFileUrl(), '인프라-현재값.xlsx')
              }
            >
              <Download className="size-4" /> 엑셀로 내려받기
            </Button>
            <Button
              className="ml-auto"
              onClick={() => void saveInfra()}
              disabled={busy || (infraRows[infraKind] ?? []).length === 0}
            >
              <Save className="size-4" /> 저장
            </Button>
          </div>

          {/* **규칙이 평가 · 인력과 다르다.** 목록이라 빈 칸을 건너뛰면 줄을 없앨 길이 없다. */}
          <p className="text-muted-foreground rounded-md border p-3 text-sm">
            표에 나온 부서를 <b>표대로 맞춥니다</b> — 줄을 지우면 그 부서에서 없어집니다(표에
            없는 부서는 그대로 둡니다). 한 줄이 틀리면 그 부서는 저장하지 않습니다. 쓰기는 그
            부서 멤버만 할 수 있습니다.
          </p>

          <PasteGrid
            columns={infraCols}
            rows={infraRows[infraKind] ?? []}
            onRows={(next) => setInfraRows((was) => ({ ...was, [infraKind]: next }))}
            header={
              <p className="text-muted-foreground text-sm">
                {(infraRows[infraKind] ?? []).length}줄 ·{' '}
                {infraKind === 'base'
                  ? '부서마다 한 줄입니다 — 물성 종수를 비우면 모름입니다(0 종이 아닙니다).'
                  : '전사 공유 자원은 전사 합계에서 한 번만 셉니다 — 그 칸을 체크합니다.'}
              </p>
            }
          />
        </>
      ) : what === 'staff' ? (
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
      {/* **종류가 표를 가른다.** 섞으면 채울 수 없는 칸이 생긴다(가상검증률은 시험이
          있어야 한다). 전용 검토 항목 타입이 없는 설치에는 이 줄이 안 선다. */}
      {(defs.data?.pair_kinds ?? []).length > 1 && setup.data?.sim_only_type_slug && (
        <Tabs value={pairKind} onValueChange={(value) => setPairKind(value as PairKind)}>
          <TabsList>
            {(defs.data?.pair_kinds ?? []).map((one) => (
              <TabsTrigger key={one.key} value={one.key}>
                {one.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Tabs value={axis?.key ?? ''} onValueChange={setAxisKey}>
          <TabsList>
            {axes.map((one) => (
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
            void downloadFile(
              dtApi.sheetFileUrl(axis.key, 'xlsx', pairKind),
              `${axis.label}-현재값.xlsx`,
            )
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
          바탕(형상 · 거동)은 체크하고, 재현은 열마다 <b>그 줄의 불량 유형</b>을 골라 담습니다
          — 유형은 시험 항목에 붙어 줄마다 다릅니다. 재현이 처음 표시되는 줄에는 이번 달이
          적히고, 이미 적힌 달은 그대로 둡니다. 수준은 이 표를 세어 정해집니다.
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
