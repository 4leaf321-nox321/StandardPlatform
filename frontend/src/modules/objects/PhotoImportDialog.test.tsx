/**
 * 사진 일괄 업로드 창 — **계획을 읽고 적용한다.** 못 붙는 파일(못 찾음 · 여럿에 맞음 …)이 있어도
 * 붙는 것은 붙으므로 그 줄을 위에 올려 읽게 하고, 적용은 계획을 세운 작업의 zip · 지문으로 간다
 * (다시 올리지 않는다).
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'

const objectApi = vi.hoisted(() => ({ importPhotos: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))
const jobsApi = vi.hoisted(() => ({ apply: vi.fn(), waitFor: vi.fn() }))
vi.mock('@/modules/jobs/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/jobs/api')>()),
  jobsApi,
}))

const TYPE = { slug: 'part', label: '부품' } as unknown as ObjectType
const DEFS = [
  { key: 'doc', label: '성적서', data_type: 'file', accept: null },
  { key: 'memo', label: '메모', data_type: 'text' },
  { key: 'photo', label: '사진', data_type: 'file', accept: 'image' },
] as unknown as PropertyDef[]

function row(name: string, status: string, over: Record<string, unknown> = {}) {
  return {
    name,
    status,
    size_bytes: 1000,
    object_id: null,
    object_label: '',
    object_key: null,
    matched_by: '',
    message: '',
    replaces: 0,
    attachment_id: null,
    ...over,
  }
}

const PLAN = {
  photos: true,
  applied: false,
  ok: true,
  type_slug: 'part',
  field: 'photo',
  field_label: '사진',
  existing: 'skip',
  files: [
    row('P-100.jpg', 'attach', { object_id: 'o1', object_label: '볼트', object_key: 'P-100' }),
    row('없는것.jpg', 'not_found', { message: '「없는것」 에 맞는 부품이(가) 없습니다.' }),
  ],
  tally: { attach: 1, not_found: 1 },
  hidden: 2,
  summary: '계획 — 업로드 1 · 못 찾음 1',
}

function done(id: string, result: unknown) {
  return { id, status: 'done', result, error: null, progress: { stage: '', done: 0, total: 0 } }
}

describe('사진 일괄 업로드', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('사진만 받는 칸이 먼저 골라져 있고, 계획을 읽은 뒤 적용한다', async () => {
    objectApi.importPhotos.mockResolvedValue({ id: 'j1', status: 'queued' })
    jobsApi.waitFor.mockResolvedValueOnce(done('j1', PLAN))
    const { PhotoImportDialog } = await import('@/modules/objects/PhotoImportDialog')
    const onApplied = vi.fn()
    render(
      <PhotoImportDialog type={TYPE} defs={DEFS} onClose={vi.fn()} onApplied={onApplied} />,
    )
    expect(screen.getByRole('combobox', { name: '업로드할 칸' })).toHaveTextContent('사진')

    const zip = new File(['PK'], '사진.zip', { type: 'application/zip' })
    fireEvent.change(screen.getByLabelText('사진 zip 파일'), { target: { files: [zip] } })
    await userEvent.click(screen.getByRole('button', { name: '미리 보기' }))
    await waitFor(() =>
      expect(objectApi.importPhotos).toHaveBeenCalledWith('part', zip, {
        field: 'photo',
        existing: 'skip',
      }),
    )
    // 못 붙는 줄이 위 — 300장 중 3장이면 그 셋이 먼저 보여야 한다.
    const lines = await screen.findAllByRole('row')
    expect(within(lines[1]).getByText('없는것.jpg')).toBeInTheDocument()
    expect(within(lines[1]).getByText('못 찾음')).toBeInTheDocument()
    expect(within(lines[2]).getByText('볼트 (P-100)')).toBeInTheDocument()
    expect(screen.getByText(/숨김 파일 2개/)).toBeInTheDocument()

    jobsApi.apply.mockResolvedValue({ id: 'j2', status: 'queued' })
    jobsApi.waitFor.mockResolvedValueOnce(
      done('j2', {
        ...PLAN,
        applied: true,
        files: [{ ...PLAN.files[0], attachment_id: 'a1' }, PLAN.files[1]],
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: '적용 — 업로드 1장' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('j1'))
    expect(await screen.findByText('적용했습니다.')).toBeInTheDocument()
    expect(onApplied).toHaveBeenCalled()
  })

  it('이미 있는 칸의 처리를 고르면 그대로 보낸다', async () => {
    objectApi.importPhotos.mockResolvedValue({ id: 'j1', status: 'queued' })
    jobsApi.waitFor.mockResolvedValue(done('j1', { ...PLAN, existing: 'replace' }))
    const { PhotoImportDialog } = await import('@/modules/objects/PhotoImportDialog')
    render(<PhotoImportDialog type={TYPE} defs={DEFS} onClose={vi.fn()} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByRole('radio', { name: '교체' }))
    const zip = new File(['PK'], '사진.zip', { type: 'application/zip' })
    fireEvent.change(screen.getByLabelText('사진 zip 파일'), { target: { files: [zip] } })
    await userEvent.click(screen.getByRole('button', { name: '미리 보기' }))
    await waitFor(() =>
      expect(objectApi.importPhotos).toHaveBeenCalledWith('part', zip, {
        field: 'photo',
        existing: 'replace',
      }),
    )
  })

  it('붙는 것이 없으면 적용이 잠긴다', async () => {
    objectApi.importPhotos.mockResolvedValue({ id: 'j1', status: 'queued' })
    jobsApi.waitFor.mockResolvedValue(
      done('j1', {
        ...PLAN,
        ok: false,
        files: [PLAN.files[1]],
        tally: { not_found: 1 },
      }),
    )
    const { PhotoImportDialog } = await import('@/modules/objects/PhotoImportDialog')
    render(<PhotoImportDialog type={TYPE} defs={DEFS} onClose={vi.fn()} onApplied={vi.fn()} />)
    const zip = new File(['PK'], '사진.zip', { type: 'application/zip' })
    fireEvent.change(screen.getByLabelText('사진 zip 파일'), { target: { files: [zip] } })
    await userEvent.click(screen.getByRole('button', { name: '미리 보기' }))
    expect(await screen.findByRole('button', { name: '적용 — 업로드 0장' })).toBeDisabled()
  })
})
