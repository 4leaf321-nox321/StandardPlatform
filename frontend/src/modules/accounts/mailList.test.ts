import { describe, expect, it } from 'vitest'

import { joinAddresses, mailList } from '@/modules/accounts/mailList'

describe('mailList', () => {
  it('메일 주소만 고른 차례로 — 메일이 아닌 아이디는 빼고 말한다', () => {
    expect(mailList(['kim@corp.example', 'admin', 'lee@corp.example'])).toEqual({
      addresses: ['kim@corp.example', 'lee@corp.example'],
      skipped: ['admin'],
    })
  })

  it('같은 주소는 한 번 — 대소문자만 다른 것도', () => {
    expect(mailList(['Kim@Corp.example', 'kim@corp.example']).addresses).toEqual([
      'Kim@Corp.example',
    ])
  })

  it('받는 사람 칸을 깨는 꼴은 뺀다 — 공백 · 구분자 · 도메인 없는 것', () => {
    const { addresses, skipped } = mailList(['a b@x.example', 'a;b@x.example', 'a@localhost', ''])
    expect(addresses).toEqual([])
    expect(skipped).toEqual(['a b@x.example', 'a;b@x.example', 'a@localhost'])
  })

  it('Outlook · Gmail 이 받는 「; 」 로 잇는다', () => {
    expect(joinAddresses(['a@x.example', 'b@x.example'])).toBe('a@x.example; b@x.example')
  })
})
