/**
 * 구조 내보내기 — **온톨로지 화면 어디에 서 있든 같은 자리에서.**
 *
 * 묶음·타입·관계를 보다가 「이걸 표로 받고 싶다」 가 되는데, 그 단추가 「가져오기」 탭
 * 아래쪽에만 있으면 찾지 못한다. 못 찾은 사람은 화면을 손으로 옮겨 적는다 — 그 옮겨
 * 적기가 이 기능이 없애려던 일이다. 그래서 제목 줄에 둔다.
 *
 *   Excel   사람이 읽는 것. 개요 · 묶음 · 타입 · 속성 · 관계 종류 · 참조 칸이 시트로
 *           나뉘고, **타입마다 그 타입의 속성 표**가 하나씩 더 선다
 *   JSON    기계가 읽는 것. 가져오기가 받는 모양 **그대로**라 고쳐서 다시 넣거나 다른
 *           설치에 그대로 심을 수 있다
 *
 * 무엇이 다른지 **고르는 자리에 적는다** — 고른 뒤에 알게 되면 파일을 두 번 받는다.
 */

import { useState } from 'react'
import { Download, Loader2 } from 'lucide-react'

import { downloadFile } from '@/shared/api/client'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'

interface Props {
  /** 오류는 화면이 이미 가진 한 자리에 세운다 — 단추 옆에 또 만들지 않는다. */
  onError: (error: Error | null) => void
}

export function OntologyExportButton({ onError }: Props) {
  const [busy, setBusy] = useState(false)

  async function get(format: string, filename: string) {
    setBusy(true)
    onError(null)
    try {
      await downloadFile(`/ontology/export?format=${format}`, filename)
    } catch (caught) {
      onError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline" disabled={busy}>
          {busy ? (
            <Loader2 className="mr-1 size-4 animate-spin" />
          ) : (
            <Download className="mr-1 size-4" />
          )}
          구조 내보내기
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => void get('xlsx', '온톨로지-구조.xlsx')}>
          Excel — 타입마다 표 하나
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void get('json', '온톨로지-정의.json')}>
          JSON — 가져오기에 그대로 다시 넣는 모양
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
