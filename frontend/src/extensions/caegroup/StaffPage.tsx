/**
 * 디지털 트윈 › 인력 — **4단계에서 만든다.**
 *
 * 들어올 것: 사람 줄(담당 시뮬레이션 여럿) · FTE(한 사람은 1.0, n 개 줄이면 각 1/n — 셈은
 * 서버가 한다) · 가명 표시(실명은 고칠 수 있는 사람에게만).
 */

import { Placeholder } from '@/shared/components/Placeholder'

export default function StaffPage() {
  return (
    <Placeholder
      title="인력"
      phase="4단계"
      description="사람 줄과 FTE 가 이 자리에 섭니다. 투입률은 받지 않습니다 — 한 사람은 1.0 이고 몫은 1/n 으로 갈립니다."
    />
  )
}
