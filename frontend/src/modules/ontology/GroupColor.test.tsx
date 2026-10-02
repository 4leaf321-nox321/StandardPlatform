/**
 * 묶음의 **그래프 색** — 화면에서 고르고, 비우면 자동으로 돌아간다.
 *
 * - 색을 안 정한 묶음도 색 칸이 **자동으로 받게 되는 색**을 보인다(`<input type="color">` 는
 *   빈 값을 못 보이므로, 안 보이면 사람은 「색이 없다」 로 읽고 아무 색이나 집는다).
 * - 상위 묶음을 바꾸면 자동 색도 **저장 전에** 따라 바뀐다 — 저장하고 그래프를 열어 봐야
 *   아는 것은 두 번 걸음이다.
 * - 고른 색은 `#rrggbb` 그대로, 「자동으로」 는 **빈 문자열**로 나간다(서버가 「안 보냄」 과
 *   구별해야 비울 수 있다).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { NavGroupRow } from '@/modules/ontology/api'

const ontologyApi = vi.hoisted(() => ({ updateGroup: vi.fn(), removeGroup: vi.fn() }))
vi.mock('@/modules/ontology/api', async (original) => {
  const real = await original<typeof import('@/modules/ontology/api')>()
  return { ...real, ontologyApi }
})

import { GroupEditDialog } from '@/modules/ontology/GroupEditDialog'

function group(slug: string, extra: Partial<NavGroupRow> = {}): NavGroupRow {
  return {
    id: slug,
    slug,
    label: slug,
    icon: '',
    audience: 'everyone',
    sort_order: 0,
    is_active: true,
    ...extra,
  }
}

/** 맨 위 묶음 둘 — 첫째가 팔레트 1번, 둘째가 2번을 받는다. */
const GROUPS = [group('기준정보'), group('시뮬레이션'), group('부품', { parent_slug: '기준정보' })]

function open(target: NavGroupRow, groups = GROUPS) {
  const view = render(
    <GroupEditDialog
      group={target}
      attached={[]}
      groups={groups}
      onClose={() => {}}
      onChanged={() => {}}
    />,
  )
  return { view, input: screen.getByLabelText('그래프 색') as HTMLInputElement }
}

describe('묶음의 그래프 색', () => {
  beforeEach(() => {
    ontologyApi.updateGroup.mockReset().mockResolvedValue({})
  })

  it('색을 안 정한 묶음도 자동으로 받는 색을 보인다', () => {
    const { input } = open(GROUPS[0])
    expect(input.value).not.toBe('#000000')
    expect(screen.getByText('자동')).toBeInTheDocument()
    expect(screen.getByText(input.value)).toBeInTheDocument()
  })

  it('맨 위 묶음끼리는 서로 다른 색을 받는다', () => {
    const first = open(GROUPS[0])
    const before = first.input.value
    first.view.unmount()
    expect(open(GROUPS[1]).input.value).not.toBe(before)
  })

  it('상위 묶음을 고치면 자동 색이 그 자리에서 바뀐다', async () => {
    // 지금은 「기준정보」 밑이라 그 색의 농도다. 「시뮬레이션」 밑으로 옮기면 계열이 바뀐다.
    const { input } = open(GROUPS[2])
    const before = input.value
    await userEvent.click(screen.getByLabelText('상위 묶음'))
    await userEvent.click(await screen.findByRole('option', { name: '시뮬레이션' }))
    await waitFor(() => expect(input.value).not.toBe(before))
  })

  it('고른 색은 그대로, 자동으로 되돌리면 빈 값으로 저장된다', async () => {
    open(group('품질', { color: '#112233' }))
    expect(screen.getByText('#112233')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '자동으로' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(ontologyApi.updateGroup).toHaveBeenCalled())
    expect(ontologyApi.updateGroup.mock.calls[0][1]).toMatchObject({ color: '' })
  })
})
