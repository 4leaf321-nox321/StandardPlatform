/**
 * 온톨로지를 고치는 창 셋(타입 · 속성 · 관계 종류)이 **같은 크기로 서고, 스크롤은 하나다.**
 *
 * 속성 하나를 고치러 들어갔는데 창 크기가 달라지면 사람은 어디를 보고 있었는지 잃는다. 그리고
 * `DialogContent` 가 이미 머리·바닥을 붙박이로 두고 가운데를 굴리므로(`ui/dialog.tsx`), 창이
 * 제 안에서 또 굴리면 스크롤바가 둘이 된다 — 그때 어느 것을 굴려야 하는지는 해 보기 전에는
 * 모른다. 눈으로만 확인하던 것이라 조용히 갈릴 수 있어 글자로 잡아 둔다.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const FILES = [
  'src/modules/ontology/TypeEditDialog.tsx',
  'src/modules/ontology/PropertyEditDialog.tsx',
  'src/modules/ontology/RelationTypeEditDialog.tsx',
]

function contentClass(source: string): string {
  const found = /<DialogContent className="([^"]+)"/.exec(source)
  return found?.[1] ?? ''
}

describe('온톨로지 수정 창', () => {
  it.each(FILES)('%s 는 화면의 80%% 로 열린다', (path) => {
    const className = contentClass(readFileSync(path, 'utf-8'))
    expect(className).toContain('h-[80vh]')
    expect(className).toContain('w-[80vw]')
    // 너비 상한이 남아 있으면 80vw 가 안 먹는다(shadcn 기본이 sm:max-w-lg).
    expect(className).toContain('sm:max-w-[80vw]')
    expect(className).not.toContain('sm:max-w-lg')
  })

  it.each(FILES)('%s 는 창 자체를 굴리지 않는다 — 스크롤은 하나뿐이다', (path) => {
    expect(contentClass(readFileSync(path, 'utf-8'))).not.toContain('overflow-y-auto')
  })

  it.each(FILES)('%s 는 두 단을 위에서부터 채우고, 긴 설명은 전폭으로 내린다', (path) => {
    // **한쪽으로 몰리지 않게.** 설명이 긴 체크박스를 단 안에 두면 그 단만 길어지고,
    // 반대쪽은 아래가 통째로 빈다. `items-start` 가 없으면 짧은 단이 늘어나 칸 사이가 벌어진다.
    const source = readFileSync(path, 'utf-8')
    const grid = /className="(grid[^"]*xl:grid-cols-2[^"]*)"/.exec(source)?.[1] ?? ''
    expect(grid).toContain('items-start')
    expect(source).toContain('xl:col-span-2')
  })

  it('탭이 있는 창은 탭 바를 붙박이로 두고 탭 안쪽만 굴린다', () => {
    const source = readFileSync('src/modules/ontology/TypeEditDialog.tsx', 'utf-8')
    expect(source).toContain('<Tabs defaultValue="basic" className="flex min-h-0 flex-1 flex-col">')
    for (const tab of ['basic', 'list', 'form']) {
      const found = new RegExp(`<TabsContent\\s+value="${tab}"[^>]*className="([^"]+)"`, 's').exec(
        source,
      )
      expect(found?.[1]).toContain('overflow-y-auto')
    }
  })
})

/**
 * 온톨로지 레이아웃 — **두 번째 사이드바는 붙박이, 내용만 굴린다.**
 *
 * 바깥(AppShell 의 main)이 통째로 굴리면 목록을 내려 보는 동안 「지금 어느 화면인가」 를
 * 말해 주던 줄과 제목이 화면 밖으로 사라진다. 눈으로만 확인하던 것이라 조용히 갈릴 수
 * 있어 글자로 잡아 둔다.
 */
describe('온톨로지 레이아웃', () => {
  const source = readFileSync('src/modules/ontology/OntologyLayout.tsx', 'utf8')

  it('바깥 상자가 높이를 잡고 넘침을 가둔다', () => {
    expect(source).toMatch(/<div className="flex h-full gap-6 overflow-hidden">/)
  })

  it('사이드바와 내용이 각자 굴린다 — 페이지 전체가 굴리지 않는다', () => {
    expect(source).toMatch(/<aside className="[^"]*overflow-y-auto/)
    expect(source).toMatch(/<div className="min-h-0 flex-1 overflow-y-auto/)
  })
})
