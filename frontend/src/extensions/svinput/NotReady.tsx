/**
 * 설정이 안 된 설치가 보는 것 — **0건을 「없다」 로 읽게 두지 않는다.** 왜 비었는지(역할 → 타입이 안
 * 정해짐)와 무엇을 하면 되는지(설정)를 말한다. 시스템 관리자가 아니면 「관리자에게」.
 */

import { Link } from 'react-router-dom'

import { useAuth } from '@/shared/auth/AuthContext'
import { isSystemAdmin } from '@/shared/auth/roles'
import { EmptyState } from '@/shared/components/EmptyState'
import { Button } from '@/shared/components/ui/button'

import { paths, type Status } from './api'

export function NotReady({ status }: { status: Status | null }) {
  const { user } = useAuth()
  const missing = (status?.roles ?? [])
    .filter((one) => ['report', 'issue'].includes(one.key) && (!one.type_slug || one.missing.length))
    .map((one) => one.key)
  return (
    <EmptyState
      title="아직 타입 설정이 안 됐습니다"
      hint={
        <>
          자주검증 입력은 허브의 온톨로지 타입(문서 · 문제점) 위에 서는 화면입니다. 어느 타입 · 칸이
          어느 역할인지 설정에서 고르면 목록이 보입니다.
          {missing.length > 0 && <> (빠진 역할: {missing.join(' · ')})</>}
        </>
      }
      action={
        isSystemAdmin(user) ? (
          <Button asChild>
            <Link to={paths.setup()}>설정으로</Link>
          </Button>
        ) : (
          <span className="text-muted-foreground text-sm">시스템 관리자에게 설정을 요청하세요.</span>
        )
      }
    />
  )
}
