/**
 * 내보내기 — **온톨로지 화면 어디에 서 있든 같은 자리에서.**
 *
 * 묶음·타입·관계를 보다가 「이걸 표로 받고 싶다」 가 되는데, 그 단추가 「가져오기」 탭
 * 아래쪽에만 있으면 찾지 못한다. 못 찾은 사람은 화면을 손으로 옮겨 적는다 — 그 옮겨
 * 적기가 이 기능이 없애려던 일이다. 그래서 제목 줄에 둔다.
 *
 * ## 네 갈래 — 「구조만」 과 「데이터까지」, 각각 엑셀과 JSON
 *
 *   구조만      정의가 어떻게 생겼나. 작아서 **그 자리에서** 온다
 *   데이터까지  그 안에 무엇이 들어 있나. 타입 수만큼 행을 읽으므로 **작업**이 된다 —
 *               넣고 나서 워커가 만든 파일을 받는다
 *
 * 엑셀은 사람이 읽는 것이고 JSON 은 다시 넣을 수 있는 것이다. 무엇이 다른지 **고르는
 * 자리에 적는다** — 고른 뒤에 알게 되면 파일을 두 번 받는다.
 */

import { useState } from 'react'
import { Download, Loader2 } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'

interface Props {
  /** 오류는 화면이 이미 가진 한 자리에 세운다 — 단추 옆에 또 만들지 않는다. */
  onError: (error: Error | null) => void
}

export function OntologyExportButton({ onError }: Props) {
  const [busy, setBusy] = useState(false)

  async function run(work: () => Promise<void>) {
    setBusy(true)
    onError(null)
    try {
      await work()
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
          {busy ? '만드는 중…' : '내보내기'}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          구조만 — 정의가 어떻게 생겼나
        </DropdownMenuLabel>
        <DropdownMenuItem
          onSelect={() => void run(() => ontologyApi.exportStructure('xlsx', '온톨로지-구조.xlsx'))}
        >
          Excel — 타입마다 속성 표
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => void run(() => ontologyApi.exportStructure('json', '온톨로지-정의.json'))}
        >
          JSON — 가져오기에 그대로 다시 넣는 모양
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          데이터까지 — 채워진 객체를 함께 (작업)
        </DropdownMenuLabel>
        <DropdownMenuItem
          onSelect={() =>
            void run(() => ontologyApi.exportEverything('xlsx', '온톨로지-전체.xlsx'))
          }
        >
          Excel — 타입마다 객체 행
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() =>
            void run(() => ontologyApi.exportEverything('json', '온톨로지-전체.json'))
          }
        >
          JSON — 정의 · 객체 · 관계 묶음
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
