/**
 * 확장은 **켠 것만 붙는다** — 서버가 준 목록으로 메뉴 · 라우트가 갈린다.
 */
import { describe, expect, it } from 'vitest'

import { enabledExtensions, extensionNavGroups, extensionRoutes, missingExtensions } from '.'

describe('extensions', () => {
  it('켜지 않으면 메뉴가 없다', () => {
    expect(enabledExtensions([])).toEqual([])
    expect(extensionNavGroups([])).toEqual([])
  })

  it('켠 것의 메뉴만 붙는다', () => {
    const groups = extensionNavGroups(['sample'])
    expect(groups.map((g) => g.title)).toEqual(['확장'])
    expect(groups[0].items.map((i) => i.to)).toEqual(['/ext/sample'])
  })

  it('경로는 꺼진 것까지 등록한다 — 문이 막는다', () => {
    // 켠 것만 등록하면 **방금 켠 확장의 링크가 새로 고침 전까지 죽는다**. 사이드바는
    // 서버에게 받은 목록으로 이미 살아 있으므로, 링크는 있고 페이지는 없는 상태가 된다.
    expect(extensionRoutes().map((r) => r.path)).toEqual(['ext/sample'])
  })

  it('화면 쪽 짝이 없는 이름은 따로 알린다', () => {
    expect(missingExtensions(['sample', 'nope'])).toEqual(['nope'])
    expect(enabledExtensions(['nope'])).toEqual([])
  })
})
