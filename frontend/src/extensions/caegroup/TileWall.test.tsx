/**
 * 벽이 지키는 것 — **색만으로 말하지 않는다.**
 *
 * 미평가는 색이 아니라 점선으로 구별되고(색맹 · 인쇄), 액자를 누르면 그 연계로 가고,
 * 호버가 서열의 **이름**을 말한다. 색이 유일한 전달 수단이면 그 그림은 절반이 안 읽힌다.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { TileWall, type WallItem } from '@/extensions/caegroup/TileWall'
import type { AxisDef, Tile } from '@/extensions/caegroup/api'

const AXIS: AxisDef = {
  key: 'scope',
  label: '적용 범위',
  kind: 'rung',
  rungs: [
    { key: 'issue', label: '이슈 대응' },
    { key: 'basic', label: '대표 모델' },
  ],
}

function tile(id: string, group: string, subject: string): Tile {
  return {
    id,
    workspace_id: 'w',
    workspace_name: '해석팀',
    subject_id: `s${id}`,
    subject_label: subject,
    agent_id: `a${id}`,
    agent_label: '낙하 구조 해석',
    agent_tools: [],
    agent_dept: group,
    assessed: 1,
    created_at: '2026-09-25T00:00:00Z',
    group,
    levels: {},
  }
}

const ITEMS: WallItem[] = [
  { tile: tile('1', '해석팀', '낙하 시험'), index: 1, label: '대표 모델' },
  { tile: tile('2', '해석팀', '굽힘 시험'), index: -1, label: '미평가' },
  { tile: tile('3', '설계팀', '발열 시험'), index: 0, label: '이슈 대응' },
]

describe('TileWall', () => {
  it('묶음마다 이름표가 서고 액자가 연계마다 하나씩 그려진다', () => {
    render(<TileWall items={ITEMS} axis={AXIS} steps={2} dark={false} onPick={vi.fn()} />)
    expect(screen.getByText('해석팀')).toBeInTheDocument()
    expect(screen.getByText('설계팀')).toBeInTheDocument()
    // 그림이 무엇을 말하는지 낭독기가 읽는다.
    expect(screen.getByRole('img', { name: /적용 범위 수준/ })).toBeInTheDocument()
  })

  it('미평가는 색이 아니라 점선으로 구별된다', () => {
    const { container } = render(
      <TileWall items={ITEMS} axis={AXIS} steps={2} dark={false} onPick={vi.fn()} />,
    )
    const dashed = container.querySelectorAll('rect[stroke-dasharray]')
    // 미평가 하나만 점선이다 — 색맹 · 인쇄에서도 남는 표시.
    expect(dashed).toHaveLength(1)
  })

  it('액자를 누르면 그 연계를 넘겨준다', async () => {
    const onPick = vi.fn()
    const { container } = render(
      <TileWall items={ITEMS} axis={AXIS} steps={2} dark={false} onPick={onPick} />,
    )
    const frames = [...container.querySelectorAll('rect')].filter((one) =>
      one.classList.contains('cursor-pointer'),
    )
    await userEvent.click(frames[0])
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick.mock.calls[0][0].id).toBe('1')
  })
})
