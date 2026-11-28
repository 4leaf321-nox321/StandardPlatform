/**
 * 속성 고치기 창 — **이 창이 안 다루는 칸도 그대로 실어 보낸다.** 서버의 PATCH 는 통째 교체라,
 * 빼면 그 칸이 비워진다. 묶음(`section` — 폼 · 상세의 섹션)이 그렇게 이름 한 글자만 고쳐도
 * 사라졌다(2026-10-08).
 *
 * 새 속성은 **남은 저장값**을 말한다 — 지운 키로 다시 만들면 그 값이 다시 보이므로, 서버가 준
 * 경고를 창을 닫지 않고 읽게 하고, 맞지 않아 막으면(ONTOLOGY-0116) 할 일을 오류 위에 적는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, onTestFinished, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'
import { ApiError } from '@/shared/api/client'

const ontologyApi = vi.hoisted(() => ({
  updateProperty: vi.fn(),
  propertyUsage: vi.fn(),
  createProperty: vi.fn(),
  removeProperty: vi.fn(),
}))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})

const WEIGHT = {
  id: 'p1',
  owner_kind: 'type',
  owner_id: 't1',
  key: 'w',
  label: '무게',
  data_type: 'text',
  unit: '',
  help: '',
  required: false,
  multi: false,
  enum_options: null,
  ref_type_slug: null,
  min_value: null,
  max_value: null,
  decimals: null,
  pattern: null,
  default_value: null,
  unique: false,
  section: '치수',
  sort_order: 0,
} as unknown as PropertyDef

const TYPE = { id: 't1', slug: 'part', label: '부품' } as unknown as ObjectType

describe('속성 고치기 창', () => {
  it('고치면 묶음(section)을 그대로 실어 보낸다', async () => {
    ontologyApi.updateProperty.mockResolvedValue(WEIGHT)
    ontologyApi.propertyUsage.mockResolvedValue({ count: 0, core_consumers: [] })
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <MemoryRouter>
        <PropertyEditDialog
          owner={{ kind: 'type', row: TYPE }}
          property={WEIGHT}
          types={[TYPE]}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateProperty).toHaveBeenCalled())
    const [, , body] = ontologyApi.updateProperty.mock.calls[0]
    expect(body).toMatchObject({ key: 'w', label: '무게', section: '치수' })
  })

  it('여러 값 칸의 기본값은 목록으로 보이고 목록으로 보낸다', async () => {
    ontologyApi.updateProperty.mockReset()
    ontologyApi.updateProperty.mockResolvedValue(WEIGHT)
    const multi = {
      ...WEIGHT,
      data_type: 'enum',
      multi: true,
      enum_options: ['A', 'B', 'C'],
      default_value: ['A', 'B'],
    } as unknown as PropertyDef
    const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
    render(
      <MemoryRouter>
        <PropertyEditDialog
          owner={{ kind: 'type', row: TYPE }}
          property={multi}
          types={[TYPE]}
          onClose={vi.fn()}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByLabelText('기본값')).toHaveValue('A, B')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateProperty).toHaveBeenCalled())
    const [, , body] = ontologyApi.updateProperty.mock.calls[0]
    expect(body.default_value).toEqual(['A', 'B'])
  })

  it('남은 저장값이 다시 보이면 닫지 않고 그 말을 보이며, 「추가」 대신 「닫기」 만 둔다', async () => {
    ontologyApi.createProperty.mockReset()
    ontologyApi.createProperty.mockResolvedValue({
      ...WEIGHT,
      revived: 12,
      warnings: [
        '속성 part.w: 저장된 값 12건이 다시 보입니다 — 예전에 지운 같은 키의 속성이 남긴 값입니다(지운 객체 포함).',
      ],
    })
    // jsdom 에는 없다 — 이 시험에서만 심고 끝에 거둔다.
    const scrolled = vi.fn()
    Element.prototype.scrollIntoView = scrolled
    onTestFinished(() => {
      delete (Element.prototype as Partial<Element>).scrollIntoView
    })
    const onClose = vi.fn()
    const onChanged = vi.fn()
    await openNew(onClose, onChanged)
    await userEvent.click(screen.getByRole('button', { name: '추가' }))
    expect(await screen.findByText(/저장된 값 12건이 다시 보입니다/)).toBeInTheDocument()
    expect(
      screen.getByText(/속성을 추가했습니다 — 남은 저장값이 다시 보입니다/),
    ).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    // 맨 위의 알림으로 굴린다 — 아래 칸을 채우다 「추가」 를 클릭한 사람 눈에 들게.
    expect(scrolled).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '추가' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '닫기' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('남은 저장값이 없으면 추가하고 그대로 닫는다', async () => {
    ontologyApi.createProperty.mockReset()
    ontologyApi.createProperty.mockResolvedValue({ ...WEIGHT, revived: 0, warnings: [] })
    const onClose = vi.fn()
    await openNew(onClose)
    await userEvent.click(screen.getByRole('button', { name: '추가' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('남은 저장값이 새 정의에 맞지 않아 막히면(ONTOLOGY-0116) 할 일과 서버의 말을 보인다', async () => {
    ontologyApi.createProperty.mockReset()
    ontologyApi.createProperty.mockRejectedValue(
      new ApiError(409, {
        error: {
          code: 'APP-ONTOLOGY-0116',
          message:
            '속성 part.w: 예전에 지운 같은 키의 속성이 남긴 값 3건 중 2건이 새 정의(숫자)에 맞지 않습니다.',
          details: { revived: 3, misfits: 2 },
        },
      }),
    )
    const onClose = vi.fn()
    await openNew(onClose)
    await userEvent.click(screen.getByRole('button', { name: '추가' }))
    expect(
      await screen.findByText(/같은 키에 남은 저장값이 새 정의에 맞지 않아 추가하지 않았습니다/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/남긴 값 3건 중 2건이 새 정의\(숫자\)에 맞지 않습니다/),
    ).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
    // 막혔으니 다시 추가할 수 있다 — 키 · 종류를 바꾼 뒤.
    expect(screen.getByRole('button', { name: '추가' })).toBeEnabled()
  })
})

/** 새 속성 창 — 키 · 이름을 채워 「추가」 를 클릭할 수 있게. */
async function openNew(onClose = vi.fn(), onChanged = vi.fn()) {
  const { PropertyEditDialog } = await import('@/modules/ontology/PropertyEditDialog')
  render(
    <MemoryRouter>
      <PropertyEditDialog
        owner={{ kind: 'type', row: TYPE }}
        types={[TYPE]}
        onClose={onClose}
        onChanged={onChanged}
      />
    </MemoryRouter>,
  )
  await userEvent.type(screen.getByLabelText('키'), 'w')
  await userEvent.type(screen.getByLabelText('이름'), '무게')
}
