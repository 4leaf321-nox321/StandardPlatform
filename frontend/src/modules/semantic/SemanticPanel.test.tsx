/**
 * 뜻으로 찾기의 관리 칸(ADR 0052 · 0054) — **축은 일괄로, 기록은 칸을 골라** 대상으로 한다.
 *
 * - 「축 타입 전체 선택」 → 「저장」 이 축 타입을 칸 「자동」(null)으로 보낸다.
 * - 기록의 대상 칸을 누르면 칸 고르기가 먼저 열린다 — 고른 칸으로 대상이 되고, 그대로 보낸다.
 * - 줄마다 어림(조각 · 용량 · 지난 속도로 잰 시간). 칸을 바꾸면 「저장하면 다시 어림」.
 * - 계산이 돌면 진행을, 실패하면 그 말을 보인다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SemanticState, SemanticType } from '@/modules/semantic/api'

const semanticApi = vi.hoisted(() => ({ get: vi.fn(), setTargets: vi.fn(), sync: vi.fn() }))
vi.mock('@/modules/semantic/api', () => ({ semanticApi }))

function type(slug: string, label: string, extra: Partial<SemanticType> = {}): SemanticType {
  return {
    type_slug: slug,
    type_label: label,
    icon: '',
    usage: 'axis',
    objects: 100,
    synced: 0,
    pieces: 0,
    pending: null,
    enabled: false,
    fields: null,
    options: [],
    why_not: null,
    estimate_pieces: 100,
    estimate_bytes: 100 * 14 * 1024,
    ...extra,
  }
}

const STATE: SemanticState = {
  state: 'ready',
  problem: null,
  model: 'bge-m3',
  kb_per_object: 14,
  min_similarity: 0.45,
  per_second: 60,
  types: [
    type('part', '부품', { enabled: true, synced: 100, pieces: 100, pending: 0 }),
    type('country', '국가', { objects: 8, estimate_pieces: 8, estimate_bytes: 8 * 14 * 1024 }),
    type('svc_case', '시장 서비스', {
      usage: 'log',
      objects: 136508,
      estimate_pieces: 136508,
      estimate_bytes: 136508 * 14 * 1024,
      options: [
        { key: 'complaint', label: '고객 증상', data_type: 'text_long' },
        { key: 'repair_note', label: '수리 내역', data_type: 'text_long' },
      ],
    }),
  ],
  job: null,
}

async function mount(state: SemanticState = STATE) {
  semanticApi.get.mockResolvedValue(state)
  const { SemanticPanel } = await import('@/modules/semantic/SemanticPanel')
  render(
    <MemoryRouter>
      <SemanticPanel />
    </MemoryRouter>,
  )
  await screen.findByRole('checkbox', { name: '부품 대상' })
}

describe('뜻으로 찾기의 관리 칸', () => {
  beforeEach(() => vi.clearAllMocks())

  it('기록은 칸을 골라야 대상이 되고, 줄마다 조각 · 용량 · 시간을 어림한다', async () => {
    await mount()
    const row = screen.getByRole('checkbox', { name: '시장 서비스 대상' }).closest('tr')!
    expect(row).toHaveTextContent('선택 필요')
    // 13.6만 조각 x 14KB · 초당 60개면 약 38분.
    expect(row).toHaveTextContent('약 1.8GB')
    expect(row).toHaveTextContent('약 38분')

    await userEvent.click(screen.getByRole('checkbox', { name: '시장 서비스 대상' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: '확인' })
    expect(confirm).toBeDisabled() // 기록은 하나 이상
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /고객 증상/ }))
    await userEvent.click(confirm)

    expect(screen.getByRole('checkbox', { name: '시장 서비스 대상' })).toBeChecked()
    expect(row).toHaveTextContent('고객 증상')
    expect(row).toHaveTextContent('저장하면 다시 어림')

    semanticApi.setTargets.mockResolvedValue(STATE)
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(semanticApi.setTargets).toHaveBeenCalledWith({
        part: null,
        svc_case: ['complaint'],
      }),
    )
  })

  it('「축 타입 전체 선택」 은 축만 자동으로 고른다 — 기록은 그대로', async () => {
    semanticApi.setTargets.mockResolvedValue(STATE)
    await mount()
    await userEvent.click(screen.getByRole('button', { name: '축 타입 전체 선택' }))
    expect(screen.getByRole('checkbox', { name: '국가 대상' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '시장 서비스 대상' })).not.toBeChecked()
    expect(screen.getByText(/\(저장 전\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(semanticApi.setTargets).toHaveBeenCalledWith({ part: null, country: null }),
    )
  })

  it('계산이 돌면 진행을, 실패하면 그 까닭을 적는다', async () => {
    await mount({
      ...STATE,
      job: {
        id: 'j1',
        status: 'running',
        created_at: '2026-11-06T01:00:00Z',
        finished_at: null,
        error: null,
        result: null,
        progress: { stage: '뜻 계산', done: 120, total: 274 },
      },
    })
    expect(screen.getByText(/계산 중 — 뜻 계산 120 \/ 274/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '작업 화면에서 보기' })).toHaveAttribute(
      'href',
      '/jobs?job=j1',
    )
  })

  it('실패한 계산은 그 까닭을 적는다', async () => {
    await mount({
      ...STATE,
      job: {
        id: 'j2',
        status: 'failed',
        created_at: '2026-11-06T01:00:00Z',
        finished_at: '2026-11-06T01:00:05Z',
        error: 'Ollama 에 닿지 않습니다(http://127.0.0.1:11434)',
        result: null,
        progress: {},
      },
    })
    expect(screen.getByText(/마지막 계산이 실패했습니다/)).toHaveTextContent(
      'Ollama 에 닿지 않습니다',
    )
  })
})
