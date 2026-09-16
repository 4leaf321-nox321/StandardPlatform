/**
 * 확장은 **켠 것만 붙는다** — 서버가 준 목록으로 메뉴 · 라우트가 갈린다.
 */
import { describe, expect, it } from 'vitest'

import { enabledExtensions, extensionNavGroups, extensionRoutes, missingExtensions } from '.'

describe('extensions', () => {
  it('켜지 않으면 아무것도 없다', () => {
    expect(enabledExtensions([])).toEqual([])
    expect(extensionNavGroups([])).toEqual([])
    expect(extensionRoutes([])).toEqual([])
  })

  it('켠 것의 메뉴와 라우트만 붙는다', () => {
    const groups = extensionNavGroups(['sample'])
    expect(groups.map((g) => g.title)).toEqual(['확장'])
    expect(groups[0].items.map((i) => i.to)).toEqual(['/ext/sample'])
    expect(extensionRoutes(['sample']).map((r) => r.path)).toEqual(['ext/sample'])
  })

  it('화면 쪽 짝이 없는 이름은 따로 알린다', () => {
    expect(missingExtensions(['sample', 'nope'])).toEqual(['nope'])
    expect(enabledExtensions(['nope'])).toEqual([])
  })
})
