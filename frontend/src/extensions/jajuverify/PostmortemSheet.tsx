/**
 * 자주검증 포스트모텀 한 장 — 머리 아래 4열 표. 지금은 세 회의가 같은 표를 쓰지만, 포스트모텀의
 * 머리 · 배치는 여기서 따로 바꾼다(사용자 결정 — 종류마다 화면이 코드).
 */

import { SheetFrame, SheetHead } from '@/extensions/jajuverify/SheetFrame'
import { SheetTable } from '@/extensions/jajuverify/SheetTable'

export default function PostmortemSheet() {
  return (
    <SheetFrame kind="postmortem">
      {(view) => (
        <>
          <SheetHead {...view} heading="돌아보기 — 무엇이 문제였고 무엇을 고치나" />
          <SheetTable {...view} />
        </>
      )}
    </SheetFrame>
  )
}
