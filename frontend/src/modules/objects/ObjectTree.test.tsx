/**
 * 트리의 앞머리 — 타입의 `tree.prefix`(문서의 절 번호)를 이름 앞에 흐리게 붙이고, 펼치기 단추의 이름에도
 * 함께 넣는다. 앞머리가 없으면 이름 그대로.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { TreeOut } from '@/modules/objects/api'

const objectApi = vi.hoisted(() => ({ tree: vi.fn() }))
vi.mock('@/modules/objects/api', () => ({ objectApi }))

const NODES: TreeOut = {
  nodes: [
    { id: 'a', label: '측정 방법', key: null, status: 'active', child_count: 2, prefix: '3.2' },
    { id: 'b', label: '부록', key: null, status: 'active', child_count: 0, prefix: null },
  ],
  orphan_count: 0,
}

describe('트리의 앞머리', () => {
  it('이름 앞에 앞머리를 붙이고 펼치기 단추의 이름에도 넣는다', async () => {
    objectApi.tree.mockResolvedValue(NODES)
    const { ObjectTree } = await import('@/modules/objects/ObjectTree')
    render(<ObjectTree typeSlug="section" selected={null} onSelect={vi.fn()} reloadKey={0} />)

    const expand = await screen.findByRole('button', { name: '3.2 측정 방법 확장' })
    expect(expand).toBeInTheDocument()
    expect(screen.getByTitle('3.2 측정 방법')).toBeInTheDocument()
    expect(screen.getByText('3.2')).toHaveClass('text-muted-foreground')
    // 앞머리가 없으면 이름 그대로.
    expect(screen.getByTitle('부록')).toHaveTextContent('부록')
  })
})
