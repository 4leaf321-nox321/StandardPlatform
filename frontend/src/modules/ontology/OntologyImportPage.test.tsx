/**
 * 정의 가져오기 화면 — **적용은 미리 본 그 계획대로만.**
 *
 * - 미리 보기에는 지문을 싣지 않고, 적용에는 미리 본 계획의 지문(`fingerprint`)을 싣는다 — 그 사이
 *   바뀌었으면 서버가 ONTOLOGY-0114 로 막는다.
 * - 막히면(미리 본 뒤 바뀜 · 값이 많은 종류 변경) 서버의 말을 보이고 계획을 비워 다시 미리 보게 한다.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({ importSchema: vi.fn(), snapshots: vi.fn() }))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})
const reload = vi.hoisted(() => vi.fn())
vi.mock('@/modules/ontology/OntologyLayout', () => ({
  useOntology: () => ({ schema: null, reload }),
}))
// 표에서 타입 생성은 따로 시험한다(`InferFromTablePanel.test.tsx`).
vi.mock('@/modules/ontology/InferFromTablePanel', () => ({ InferFromTablePanel: () => null }))

import OntologyImportPage from './OntologyImportPage'

const BODY = { types: [{ slug: 'part', label: '부품' }] }

const PLAN = {
  applied: false,
  changes: [{ kind: 'type', slug: 'part', action: 'create', fields: [] }],
  warnings: [],
  errors: [],
  snapshot_id: null,
  fingerprint: 'fp-1',
}

function refused(code: string, message: string) {
  return new ApiError(409, { error: { code: `APP-${code}`, message } })
}

async function preview() {
  render(<OntologyImportPage />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: JSON.stringify(BODY) } })
  await userEvent.click(screen.getByRole('button', { name: '미리 보기' }))
  expect(await screen.findByText('적용하면 이렇게 됩니다')).toBeInTheDocument()
}

describe('정의 가져오기 — 미리 본 계획대로 적용', () => {
  beforeEach(() => {
    // 한 번만 돌려줄 값까지 비운다 — 앞 시험이 남긴 것이 다음 시험의 답이 되지 않게.
    vi.resetAllMocks()
    ontologyApi.snapshots.mockResolvedValue([])
  })

  it('미리 보기에는 지문을 싣지 않고, 적용에는 미리 본 계획의 지문을 싣는다', async () => {
    ontologyApi.importSchema
      .mockResolvedValueOnce(PLAN)
      .mockResolvedValueOnce({ ...PLAN, applied: true, snapshot_id: 's1' })
    await preview()
    expect(ontologyApi.importSchema).toHaveBeenLastCalledWith(BODY, true, undefined)

    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() =>
      expect(ontologyApi.importSchema).toHaveBeenLastCalledWith(BODY, false, 'fp-1'),
    )
    expect(await screen.findByText('적용했습니다')).toBeInTheDocument()
    expect(reload).toHaveBeenCalled()
  })

  it('미리 본 뒤 바뀌었으면(ONTOLOGY-0114) 그 말을 보이고 다시 미리 봐야 적용한다', async () => {
    ontologyApi.importSchema
      .mockResolvedValueOnce(PLAN)
      .mockRejectedValueOnce(
        refused('ONTOLOGY-0114', '미리 본 뒤 바뀌었습니다 — 다시 미리 보세요.'),
      )
    await preview()
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    expect(await screen.findByText(/미리 본 뒤 바뀌었습니다/)).toBeInTheDocument()
    expect(screen.queryByText('적용하면 이렇게 됩니다')).toBeNull()
    expect(screen.getByRole('button', { name: '적용' })).toBeDisabled()
    expect(reload).not.toHaveBeenCalled()
  })

  it('값이 많은 종류 변경(ONTOLOGY-0115)이면 작업의 길을 말한 서버의 말을 보인다', async () => {
    ontologyApi.importSchema
      .mockResolvedValueOnce(PLAN)
      .mockRejectedValueOnce(
        refused(
          'ONTOLOGY-0115',
          '값이 많은 종류 변경(part.w)이 들어 있어 요청 안에서 적용하지 않습니다 — 그 속성을 ' +
            '「종류 변경」 작업(POST /api/ontology/types/part/properties/w/retype/job)으로 먼저 ' +
            '바꾼 뒤 다시 하세요.',
        ),
      )
    await preview()
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    expect(
      await screen.findByText(/POST \/api\/ontology\/types\/part\/properties\/w\/retype\/job/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '적용' })).toBeDisabled()
  })
})
