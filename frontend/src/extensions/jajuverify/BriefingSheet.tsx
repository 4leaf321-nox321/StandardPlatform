/**
 * 자주검증 설명회 한 장 — 머리 아래 4열 표. 지금은 세 회의가 같은 표를 쓰지만, 설명회의 머리 ·
 * 배치는 여기서 따로 바꾼다(사용자 결정 — 종류마다 화면이 코드).
 */

import { SheetFrame, SheetHead } from '@/extensions/jajuverify/SheetFrame'
import { SheetTable } from '@/extensions/jajuverify/SheetTable'

export default function BriefingSheet() {
  return (
    <SheetFrame kind="briefing">
      {(view) => (
        <>
          <SheetHead {...view} heading="설명 내용 — 항목마다 문제점과 개선대책" />
          <SheetTable {...view} />
        </>
      )}
    </SheetFrame>
  )
}
