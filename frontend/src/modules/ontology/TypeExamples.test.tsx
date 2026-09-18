/**
 * 정의만으로는 칸의 뜻이 안 잡힌다 — 예시는 **값이 있는 칸**을 골라 보여 주고 객체로 이어진다.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { ObjectType, PropertyDef } from '@/modules/ontology/api'

const objectApi = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

import { TypeExamples } from './TypeExamples'

const TYPE = {
  slug: 'model',
  label: '개발모델',
  key_policy: 'required',
  properties: [
    { key: 'base_code', label: '기본코드' },
    { key: 'empty', label: '빈 칸' },
    { key: 'stage', label: '단계' },
  ],
} as unknown as ObjectType & { properties: PropertyDef[] }

describe('TypeExamples', () => {
  it('최근 객체와 값이 있는 칸만 보여 주고 목록으로 이어진다', async () => {
    objectApi.list.mockResolvedValue({
      items: [
        {
          id: 'a1',
          label: '모델 XA1',
          key: 'SM-XA1',
          properties: { base_code: 'XA', stage: 'PV' },
        },
        {
          id: 'a2',
          label: '모델 XA2',
          key: 'SM-XA2',
          properties: { base_code: 'XA', stage: null },
        },
      ],
      total: 40,
      limit: 5,
      offset: 0,
    })
    render(
      <MemoryRouter>
        <TypeExamples type={TYPE} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('모델 XA1')).toBeInTheDocument()
    expect(screen.getByText('SM-XA1')).toBeInTheDocument()
    expect(screen.getByText('base_code')).toBeInTheDocument()
    expect(screen.getByText('stage')).toBeInTheDocument()
    // 전부 빈 칸은 열로 안 선다.
    expect(screen.queryByText('empty')).not.toBeInTheDocument()
    expect(screen.getByText('모델 XA1').closest('a')).toHaveAttribute('href', '/o/model/a1')
    expect(screen.getByText('목록 전체 보기').closest('a')).toHaveAttribute('href', '/o/model')
    expect(screen.getByText(/전체 40개/)).toBeInTheDocument()
    expect(objectApi.list).toHaveBeenCalledWith('model', { limit: 5 })
  })

  it('객체가 없으면 그렇다고 말한다', async () => {
    objectApi.list.mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0 })
    render(
      <MemoryRouter>
        <TypeExamples type={TYPE} />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/아직 객체가 없습니다/)).toBeInTheDocument()
  })
})
