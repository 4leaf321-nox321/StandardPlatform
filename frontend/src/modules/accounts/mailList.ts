/**
 * 여러 계정에 한 번에 메일 쓰기 — 고른 계정의 아이디에서 **메일 주소만** 골라 잇는다.
 *
 * 로그인 아이디는 메일 형식을 강제하지 않는다(`admin` 같은 짧은 아이디도 있다). 사내 포털로
 * 들어온 계정은 포털이 준 메일 주소가 아이디다. 메일 주소가 아닌 것을 그대로 붙이면 메일
 * 프로그램이 받는 사람 칸 전체를 거절하거나 엉뚱한 주소로 보낸다 — 그래서 빼고, 뺀 것을 말한다.
 */

/** 받는 사람 칸이 받는 꼴 — 공백 · 구분자 없이 `무엇@어디`. */
const ADDRESS = /^[^\s@;,<>]+@[^\s@;,<>]+\.[^\s@;,<>]+$/

/** Outlook · Gmail · 사내 웹메일이 모두 받는 구분자. */
export const SEPARATOR = '; '

export type MailList = {
  /** 메일 주소 — 고른 차례, 같은 주소(대소문자만 다른 것 포함)는 한 번. */
  addresses: string[]
  /** 메일 주소가 아니어서 뺀 아이디. */
  skipped: string[]
}

export function mailList(ids: Iterable<string>): MailList {
  const addresses: string[] = []
  const skipped: string[] = []
  const seen = new Set<string>()
  for (const raw of ids) {
    const one = raw.trim()
    if (!ADDRESS.test(one)) {
      if (one) skipped.push(one)
      continue
    }
    const key = one.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    addresses.push(one)
  }
  return { addresses, skipped }
}

export function joinAddresses(addresses: string[]): string {
  return addresses.join(SEPARATOR)
}
