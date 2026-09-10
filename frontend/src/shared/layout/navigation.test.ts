/**
 * 사이드바가 지키는 규칙.
 *
 * 여기가 화면 목록의 정본이라, 이 파일이 틀리면 라우터와 권한이 함께 틀린다.
 */

import { describe, expect, it } from 'vitest'

import { NAV_GROUPS, canSee, itemHref, pendingItems, visibleGroups } from '@/shared/layout/navigation'

const ADMIN = { isSystemAdmin: true, isAnyManager: true }
const MANAGER = { isSystemAdmin: false, isAnyManager: true }
const MEMBER = { isSystemAdmin: false, isAnyManager: false }

describe('사이드바', () => {
  it('멤버에게는 관리 그룹이 통째로 안 보인다', () => {
    const titles = visibleGroups(MEMBER).map((group) => group.title)
    expect(titles).not.toContain('관리')
    expect(titles).not.toContain('내 부서')
  })

  it('부서 관리자는 내 부서를 보되 전사 관리는 못 본다', () => {
    const titles = visibleGroups(MANAGER).map((group) => group.title)
    expect(titles).toContain('내 부서')
    expect(titles).not.toContain('관리')
  })

  it('시스템 관리자는 전부 본다', () => {
    expect(visibleGroups(ADMIN).length).toBe(NAV_GROUPS.length)
  })

  it('빈 그룹은 제목까지 사라진다', () => {
    // **제목만 남으면 「뭔가 안 나온다」 로 읽힌다.** 항목이 하나도 없는 그룹은
    // 있으나 마나가 아니라 오해를 만든다.
    for (const group of visibleGroups(MEMBER)) {
      expect(group.items.length).toBeGreaterThan(0)
    }
  })

  it('부서 스코프 항목은 슬러그를 받아 주소를 만든다', () => {
    const home = NAV_GROUPS[0].items[0]
    expect(itemHref(home, 'material-lab')).toBe('/w/material-lab')
  })

  it('미구현 항목은 단계와 설명을 함께 든다', () => {
    // 라우터가 이 값으로 stub 을 그린다 — 비어 있으면 stub 이 「—」 만 보여 준다.
    for (const item of pendingItems()) {
      expect(item.phase, `${item.label} 에 단계가 없습니다`).toBeTruthy()
      expect(item.summary, `${item.label} 에 설명이 없습니다`).toBeTruthy()
    }
  })

  it('everyone 은 누구에게나 보인다', () => {
    expect(canSee(undefined, MEMBER)).toBe(true)
    expect(canSee('everyone', MEMBER)).toBe(true)
  })
})
