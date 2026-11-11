/**
 * 복사 — **http 에서도 된다.** `navigator.clipboard` 는 보안 컨텍스트(https · localhost)에만 있어,
 * 메인 서버 없이 IP 로 직접 보는 동안(http)은 없다. 그때는 옛 방식(숨은 textarea + execCommand)으로.
 *
 * 옛 방식은 **누른 그 순간**에만 된다(브라우저가 사용자 동작 안에서만 허락한다) — 부르는 쪽은
 * 복사할 글을 미리 마련해 두고 누름 처리 안에서 곧바로 부른다(그 앞에 서버를 기다리지 않는다).
 * 그것도 막히면 던진다 — 부르는 쪽이 글을 골라진 채로 보여 주고 Ctrl+C 를 말한다.
 */
export async function copyText(text: string): Promise<void> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // 아래 폴백으로
    }
  }
  // 누른 단추로 초점을 돌려준다 — 숨은 칸에 초점이 남으면 키보드로 쓰던 사람이 길을 잃는다.
  const before = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '0'
  area.style.left = '0'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.focus()
  area.select()
  // iOS 사파리는 `select()` 만으로 고르지 않는다.
  area.setSelectionRange(0, text.length)
  try {
    if (!document.execCommand('copy')) throw new Error('copy failed')
  } finally {
    document.body.removeChild(area)
    before?.focus()
  }
}
