/**
 * BOM 전개(ADR 0040) — 객체 하나의 트리 아래를 **관계의 수량을 경로 따라 곱해** 펼친다.
 *
 * 제품 하나에 어셈블리 2개, 어셈블리 하나에 볼트 4개면 제품 하나에 볼트는 8개다. 같은 하위
 * 조립품을 여러 곳에서 쓰면 그 몫을 더한다(총수량). 보기는 셋이다 — 「정리」(객체마다 한 줄),
 * 「단계별」(경로마다 한 줄, 트리처럼), 「어디에 쓰였나」(위로 올라가며 이것을 쓰는 상위 객체).
 *
 * **세지 않은 것 · 1개로 센 것을 말한다.** 수량 칸이 빈 관계를 1개로 셌으면 「수량 없음」, 줄 상한에
 * 닿았으면 「잘림」 과 전체 줄 수, 지나간 볼 수 없는 객체는 그 수를 적는다 — 안 적으면 표가 「이게
 * 전부이고 맞는 수」 로 읽힌다.
 *
 * **접어 두고 펼칠 때 센다.** 큰 BOM 은 선이 수만이라 상세를 열 때마다 세면 그만큼 기다린다.
 */

import { useState } from 'react'
import { ChevronRight, Download, ListTree } from 'lucide-react'
import { Link } from 'react-router-dom'

import { objectApi } from '@/modules/objects/api'
import type {
  ExplodeLine,
  ExplodeMode,
  ExplodeObject,
  ExplodeOut,
  ExplodeTop,
} from '@/modules/objects/api'
import { AsOfPicker } from '@/modules/objects/AsOfPicker'
import { shown } from '@/modules/objects/RollupPanel'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { cn } from '@/shared/lib/utils'

const MODES: { value: ExplodeMode; label: string; hint: string }[] = [
  {
    value: 'summary',
    label: '정리',
    hint: '객체마다 한 줄 — 모든 경로의 누적 수량을 더한 총수량입니다.',
  },
  {
    value: 'indented',
    label: '단계별',
    hint: '경로마다 한 줄 — 트리처럼 단계만큼 들여 쓰고, 관계의 수량과 뿌리에서 그 줄까지 곱한 누적 수량을 적습니다.',
  },
  {
    value: 'where_used',
    label: '어디에 쓰였나',
    hint: '위로 올라가며 이 객체를 쓰는 상위 객체 — 맨 위 객체마다 그것 하나에 이 객체가 총 몇 개인지 적습니다.',
  },
]

interface Props {
  typeSlug: string
  objectId: string
  /** 트리 관계의 이름(「구성」) — 머리말에 적는다. */
  relationLabel?: string
  /**
   * 기준일(`YYYY-MM-DD`, 빈 글자 = 오늘) — 상세가 들고 있다. 관련 객체 · 롤업 · 관계도와 **같은
   * 날**이라야 롤업의 합과 전개의 줄이 같은 트리에서 나온다.
   */
  asOf?: string
  onAsOf?: (next: string) => void
  /**
   * 트리 관계가 유효 기간을 받나 — 받을 때만 기준일 고르개를 세운다. 안 받는 관계에 세우면 바꿔도
   * 아무것도 안 바뀌고, 그것은 고장으로 읽힌다.
   */
  periodic?: boolean
  /** 타입 slug → 이름 — 표의 「타입」 칸. 모르는 slug 는 그대로 적는다. */
  typeLabels?: Record<string, string>
  /** 바뀌면 다시 센다 — 상세에서 관계를 고친 횟수. */
  reloadKey?: unknown
}

export function ExplodePanel({ relationLabel, ...rest }: Props) {
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3" aria-label="BOM 전개">
      <h2 className="text-base font-semibold">
        <button
          type="button"
          aria-expanded={open}
          className="flex items-center gap-2 text-left"
          onClick={() => setOpen((now) => !now)}
        >
          <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} />
          <ListTree className="size-4" />
          BOM 전개
          <span className="text-muted-foreground text-sm font-normal">
            {relationLabel ? `「${relationLabel}」 트리의` : '트리의'} 수량을 경로 따라 곱합니다 —
            펼칠 때 셉니다
          </span>
        </button>
      </h2>
      {open && <ExplodeBody {...rest} />}
    </section>
  )
}

function ExplodeBody({
  typeSlug,
  objectId,
  asOf = '',
  onAsOf,
  periodic = false,
  typeLabels = {},
  reloadKey,
}: Omit<Props, 'relationLabel'>) {
  const [mode, setMode] = useState<ExplodeMode>('summary')
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<Error | null>(null)
  const found = useResource(
    () => objectApi.explode(typeSlug, objectId, { mode, asOf: asOf || null }),
    [typeSlug, objectId, mode, asOf, reloadKey],
  )
  // 오류가 나면 앞 보기(앞 기준일)의 응답이 남아 있다 — 그대로 그리면 그것이 이 보기의 결과로
  // 읽힌다. 그때는 오류만 보인다.
  const data = found.error && found.stale ? null : found.data
  const hint = MODES.find((one) => one.value === mode)?.hint

  async function download() {
    setDownloadError(null)
    setDownloading(true)
    try {
      await objectApi.exportExplode(
        typeSlug,
        objectId,
        { mode, asOf: asOf || null },
        'csv',
        `${typeSlug}-explode-${mode}.csv`,
      )
    } catch (caught) {
      setDownloadError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1" role="group" aria-label="보기">
          {MODES.map((one) => (
            <Button
              key={one.value}
              type="button"
              size="sm"
              variant={one.value === mode ? 'secondary' : 'ghost'}
              aria-pressed={one.value === mode}
              onClick={() => setMode(one.value)}
            >
              {one.label}
            </Button>
          ))}
        </div>
        {periodic && onAsOf && <AsOfPicker id="explode-as-of" value={asOf} onChange={onAsOf} />}
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={downloading || !data}
          onClick={download}
        >
          <Download className="mr-1 size-4" />
          다운로드
        </Button>
      </div>
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}

      <ErrorNotice error={found.error} />
      <ErrorNotice error={downloadError} />

      {data && (
        <div className={cn('space-y-3', found.loading && 'opacity-60')}>
          <Notices data={data} />
          {data.lines.length === 0 ? (
            <Empty data={data} periodic={periodic} />
          ) : (
            <LinesTable data={data} typeLabels={typeLabels} />
          )}
          {data.mode === 'where_used' && data.tops.length > 0 && (
            <TopsTable data={data} typeLabels={typeLabels} />
          )}
        </div>
      )}
      {!data && found.loading && <p className="text-muted-foreground text-sm">세는 중…</p>}
    </div>
  )
}

/** 표 위의 표시 — 무엇을 1개로 셌고, 무엇을 가렸고, 어디서 잘랐나. */
function Notices({ data }: { data: ExplodeOut }) {
  const { totals } = data
  const up = data.mode === 'where_used'
  return (
    <div className="space-y-1 text-sm">
      <p className="text-muted-foreground text-xs">
        「{data.relation_label || data.relation}」 관계 · {up ? '위로' : '아래로'} 펼침 · 객체{' '}
        {totals.objects.toLocaleString()}개 · 관계 {totals.links.toLocaleString()}개 · 기준일{' '}
        {data.as_of}
      </p>
      {!data.quantity_key && (
        <p className="text-muted-foreground text-xs">
          수량 칸이 정해지지 않아 모든 관계를 1개로 셉니다 — 관계 종류 편집에서 수량 칸을 정하면 그
          값을 곱합니다.
        </p>
      )}
      {totals.missing > 0 && (
        <p className="flex flex-wrap items-center gap-1.5">
          <StatusBadge kind="explode" value="missing" />
          {totals.missing.toLocaleString()}개 — 수량 칸 「{data.quantity_label || data.quantity_key}
          」 에 값이 없거나 숫자가 아닌 관계를 1개로 셌습니다.
        </p>
      )}
      {data.truncated && (
        <p className="flex flex-wrap items-center gap-1.5">
          <StatusBadge kind="explode" value="truncated" />
          전체 {totals.lines_total.toLocaleString()}줄 중 {totals.lines.toLocaleString()}줄 표시
          {data.mode === 'summary' ? '' : ' — 「정리」 는 객체마다 한 줄입니다'}.
        </p>
      )}
      {totals.hidden > 0 && (
        <p className="flex flex-wrap items-center gap-1.5">
          <StatusBadge kind="explode" value="hidden" />
          {totals.hidden.toLocaleString()}개 —{' '}
          {data.mode === 'summary' ? '줄로 표시하지 않지만' : '이름을 가린 줄로 표시하고'}{' '}
          {up ? '그 위' : '그 아래'}는 셉니다.
        </p>
      )}
      {totals.deleted > 0 && (
        <p className="flex flex-wrap items-center gap-1.5">
          <StatusBadge kind="explode" value="deleted" />
          {totals.deleted.toLocaleString()}개를 지나갔습니다 — {up ? '그 위' : '그 아래'}는 셉니다.
        </p>
      )}
    </div>
  )
}

/** 빈 결과의 까닭 — 아래(위)에 아무것도 없는지, 보이는 것만 없는지. */
function Empty({ data, periodic }: { data: ExplodeOut; periodic: boolean }) {
  const up = data.mode === 'where_used'
  const passed = data.totals.hidden + data.totals.deleted
  const relation = `「${data.relation_label || data.relation}」`
  const day = `기준일 ${data.as_of}에 유효한 ${relation} 관계로`
  const tip = periodic ? ' 기준일을 바꾸면 그날의 구성을 봅니다.' : ''
  if (passed > 0) {
    return (
      <EmptyState
        title="표시할 객체가 없습니다"
        hint={`${day} ${up ? '위' : '아래'}에 이어진 것은 모두 볼 수 없거나 삭제된 객체입니다(${passed.toLocaleString()}개).${tip}`}
      />
    )
  }
  return up ? (
    <EmptyState
      title="위에 아무것도 없습니다"
      hint={`${day} 이 객체를 쓰는 상위 객체가 없습니다 — 이 객체가 맨 위입니다.${tip}`}
    />
  ) : (
    <EmptyState
      title="아래에 아무것도 없습니다"
      hint={`${day} 이 객체 아래에 이어진 것이 없습니다.${tip}`}
    />
  )
}

/** 이름 칸 — 보이는 객체는 상세로 가는 링크, 가린 객체는 회색 글자로(링크 없음). */
function ObjectName({ one }: { one: ExplodeObject }) {
  if (one.id && one.type_slug) {
    return (
      <Link to={`/o/${one.type_slug}/${one.id}`} className="font-medium hover:underline">
        {one.label}
      </Link>
    )
  }
  return <span className="text-muted-foreground">{one.label}</span>
}

function typeText(one: ExplodeObject, typeLabels: Record<string, string>): string {
  if (!one.type_slug) return ''
  return typeLabels[one.type_slug] ?? one.type_slug
}

/** 그 줄로 오는 관계의 수량 — 수량 없음이면 「(없음)」(1개로 셌다). */
function QuantityCell({ line }: { line: ExplodeLine }) {
  if (line.quantity_missing) {
    return (
      <span className="text-muted-foreground" title="수량 없음 — 1개로 셌습니다">
        (없음)
      </span>
    )
  }
  return <>{line.quantity === null ? '—' : shown(line.quantity)}</>
}

/**
 * 줄 표 — **파일과 같은 열 · 같은 순서**(`/explode/export`). 정리: 가장 얕은 단계 · 식별자 · 이름 ·
 * 타입 · 총수량 · 가장 깊은 단계 · 경로 수 · 수량 없음. 단계별 · 어디에 쓰였나: 단계 · 식별자 ·
 * 이름 · 타입 · 수량 · 누적 수량(어디에 쓰였나는 「하나에 든 <시작 객체>」).
 */
function LinesTable({
  data,
  typeLabels,
}: {
  data: ExplodeOut
  typeLabels: Record<string, string>
}) {
  const summary = data.mode === 'summary'
  const amount = data.quantity_label || '수량'
  const head = summary
    ? [
        '가장 얕은 단계',
        '식별자',
        '이름',
        '타입',
        '총수량',
        '가장 깊은 단계',
        '경로 수',
        '수량 없음',
      ]
    : [
        '단계',
        '식별자',
        '이름',
        '타입',
        amount,
        data.mode === 'where_used' ? `하나에 든 ${data.root.label}` : '누적 수량',
      ]
  /** 숫자 칸 — 오른쪽 정렬. 이름 · 식별자 · 타입은 글이다. */
  const numeric = (index: number) => index !== 1 && index !== 2 && index !== 3

  return (
    <div className="max-h-[60vh] overflow-y-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {head.map((text, index) => (
              <TableHead
                key={index}
                className={cn('whitespace-nowrap', numeric(index) && 'text-right')}
              >
                {text}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.lines.map((line, index) => (
            // 단계별은 같은 객체가 경로마다 다시 나온다 — 열쇠는 줄의 자리다.
            <TableRow key={index} className={cn(!line.id && 'text-muted-foreground')}>
              <TableCell className="text-right tabular-nums">{line.level}</TableCell>
              <TableCell className="font-mono text-xs">{line.key ?? ''}</TableCell>
              <TableCell>
                <span
                  className="block"
                  style={summary ? undefined : { paddingLeft: `${(line.level - 1) * 1.25}rem` }}
                >
                  <ObjectName one={line} />
                </span>
              </TableCell>
              <TableCell className="text-muted-foreground text-xs">
                {typeText(line, typeLabels)}
              </TableCell>
              {summary ? (
                <>
                  <TableCell className="text-right font-medium tabular-nums">
                    {shown(line.total)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{line.depth_max ?? ''}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {line.paths === null ? '' : line.paths.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {line.missing ? line.missing.toLocaleString() : ''}
                  </TableCell>
                </>
              ) : (
                <>
                  <TableCell className="text-right tabular-nums">
                    <QuantityCell line={line} />
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {shown(line.total)}
                  </TableCell>
                </>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** 어디에 쓰였나의 맨 위 객체 — 「그것 하나에 <이 객체> 총 n개」. 파일의 아래 표와 같은 열. */
function TopsTable({ data, typeLabels }: { data: ExplodeOut; typeLabels: Record<string, string> }) {
  return (
    <div className="space-y-1.5">
      <h3 className="text-sm font-medium">
        맨 위 객체
        <span className="text-muted-foreground ml-2 text-xs font-normal">
          그것 하나에 든 「{data.root.label}」 의 총수량
        </span>
      </h3>
      <div className="rounded-md border">
        <Table aria-label="맨 위 객체">
          <TableHeader>
            <TableRow>
              <TableHead>식별자</TableHead>
              <TableHead>이름</TableHead>
              <TableHead>타입</TableHead>
              <TableHead className="text-right">하나에 든 {data.root.label}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.tops.map((top: ExplodeTop, index) => (
              <TableRow key={top.id ?? index}>
                <TableCell className="font-mono text-xs">{top.key ?? ''}</TableCell>
                <TableCell>
                  <ObjectName one={top} />
                </TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {typeText(top, typeLabels)}
                </TableCell>
                <TableCell className="text-right font-medium tabular-nums">
                  총 {shown(top.total)}개
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
