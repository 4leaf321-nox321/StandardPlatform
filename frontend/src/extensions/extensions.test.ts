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

  it('켠 것의 메뉴만 붙고, 그룹은 제 이름으로 선다', () => {
    // **「확장」 이라는 바구니에 넣지 않는다.** 켜고 끄는 일이 「그 기능이 열렸다 / 닫혔다」
    // 로 읽혀야 한다 — 사용자는 「확장」 이라는 말로 자기 일을 찾지 않는다.
    const groups = extensionNavGroups(['sample'])
    expect(groups.map((g) => g.title)).toEqual(['본보기'])
    expect(groups[0].items.map((i) => i.to)).toEqual(['/ext/sample'])
  })

  it('경로는 꺼진 것까지 등록한다 — 문이 막는다', () => {
    // 켠 것만 등록하면 **방금 켠 확장의 링크가 새로 고침 전까지 죽는다**. 사이드바는
    // 서버에게 받은 목록으로 이미 살아 있으므로, 링크는 있고 페이지는 없는 상태가 된다.
    const paths = extensionRoutes().map((r) => r.path)
    expect(paths).toContain('ext/sample')
    expect(paths).toContain('ext/caegroup/dt')
  })

  it('caegroup 은 「디지털 트윈」 그룹으로 화면 넷을 낸다', () => {
    const groups = extensionNavGroups(['caegroup'])
    expect(groups.map((g) => g.title)).toEqual(['디지털 트윈'])
    expect(groups[0].items.map((i) => i.label)).toEqual(['대시보드', '역량', '인력', '인프라'])
  })

  it('화면 쪽 짝이 없는 이름은 따로 알린다', () => {
    expect(missingExtensions(['sample', 'nope'])).toEqual(['nope'])
    expect(enabledExtensions(['nope'])).toEqual([])
  })
})
