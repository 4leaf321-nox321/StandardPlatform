/**
 * 고른 계정의 메일 주소 복사 — 메일 프로그램의 받는 사람(숨은 참조) 칸에 붙여 넣는다.
 *
 * **http 에서도 된다.** 운영 서버는 http 로 열린다 — 브라우저가 클립보드 API 를 안 준다.
 * `copyText` 가 옛 방식으로 떨어지는데, 그 방식은 **누른 그 순간**에만 된다. 그래서 주소는
 * 미리 모아 두고(고를 때) 누름 처리 안에서 곧바로 복사한다 — 그 앞에 서버를 기다리지 않는다.
 * 그것마저 막히면 주소를 골라진 채로 보여 주고 Ctrl+C 를 말한다(조용히 실패하면 사람은
 * 「복사됐다」 고 믿고 빈 칸을 붙여 넣는다).
 */

import { useEffect, useRef, useState } from 'react'
import { Copy } from 'lucide-react'

import { joinAddresses, mailList } from '@/modules/accounts/mailList'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'
import { copyText } from '@/shared/lib/clipboard'

type Props = {
  /** 고른 계정의 아이디(고른 차례). */
  ids: string[]
  onClear: () => void
  /** 막대에 더 놓을 것 — 「이 거르기의 N명 모두 고르기」. */
  children?: React.ReactNode
}

function skippedNote(skipped: string[]): string {
  if (skipped.length === 0) return ''
  const shown = skipped.slice(0, 5).join(', ')
  const more = skipped.length > 5 ? ` 외 ${skipped.length - 5}개` : ''
  return ` 메일 주소가 아닌 아이디 ${skipped.length}개는 뺐습니다(${shown}${more}).`
}

export function MailCopyBar({ ids, onClear, children }: Props) {
  const [notice, setNotice] = useState<string | null>(null)
  // 자동 복사가 막혔을 때 보여 줄 글 — 골라진 채로.
  const [manual, setManual] = useState<string | null>(null)
  const manualRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (manual !== null) manualRef.current?.select()
  }, [manual])

  // 고른 것이 바뀌면 지난 알림은 지난 묶음의 것이다.
  const key = ids.join('\n')
  useEffect(() => {
    setNotice(null)
    setManual(null)
  }, [key])

  async function copy() {
    const { addresses, skipped } = mailList(ids)
    if (addresses.length === 0) {
      setManual(null)
      setNotice(`고른 계정에 메일 주소가 없습니다.${skippedNote(skipped)}`)
      return
    }
    const text = joinAddresses(addresses)
    try {
      await copyText(text)
      setManual(null)
      setNotice(
        `메일 주소 ${addresses.length}개를 복사했습니다 — 메일의 받는 사람 칸에 붙여 넣으세요` +
          `(여러 사람에게는 숨은 참조를 권합니다).${skippedNote(skipped)}`,
      )
    } catch {
      setManual(text)
      setNotice(
        `이 브라우저가 자동 복사를 막았습니다 — 아래 주소 ${addresses.length}개가 골라져 있으니 ` +
          `Ctrl+C(맥은 ⌘+C)로 복사하세요.${skippedNote(skipped)}`,
      )
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">고른 계정 {ids.length}명</span>
        <Button size="sm" onClick={() => void copy()} disabled={ids.length === 0}>
          <Copy className="size-4" />
          메일 주소 복사
        </Button>
        {children}
        {ids.length > 0 && (
          <Button size="sm" variant="ghost" onClick={onClear}>
            고른 것 비우기
          </Button>
        )}
      </div>
      {notice && (
        <p role="status" className="text-sm">
          {notice}
        </p>
      )}
      {manual !== null && (
        <Textarea
          ref={manualRef}
          aria-label="복사할 메일 주소"
          readOnly
          value={manual}
          onFocus={(event) => event.currentTarget.select()}
          className="font-mono text-xs"
        />
      )}
    </div>
  )
}
