/**
 * 코어 현황 — **무엇을 공개했고, 누가 접근할 수 있으며, 언제 조회되었나.**
 *
 * 셋이 흩어져 있으면(타입 목록 · 토큰 목록 · 감사 기록) 「현재 외부로 어떤 자료가 나가고
 * 있는가」 를 한 번에 확인할 수 없고, **확인할 수 없는 것은 관리되지 않는다.**
 *
 * 공개 설정 자체는 이 화면에 없다 — 타입의 속성이라 타입 수정 창에 있다. 여기는 **조회와
 * 전달**의 자리다.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, Loader2 } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate, shownDateTime } from '@/shared/lib/datetime'

export default function OntologyCorePage() {
  const status = useResource(() => ontologyApi.coreStatus(), [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function downloadKit() {
    setBusy(true)
    setError(null)
    try {
      await ontologyApi.downloadCoreKit()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  if (status.error) return <ErrorNotice error={status.error} />
  const data = status.data
  if (!data) return <p className="text-muted-foreground text-sm">조회 중…</p>

  return (
    <div className="space-y-6">
      <p className="text-muted-foreground text-sm">
        타입 수정 창에서 <b>「코어」</b> 를 지정하면 해당 타입이 외부 시스템에 공개됩니다. 수신
        시스템에는 <b>아래 연동 주소</b>와 <b>범위가 `core:read` 인 액세스 토큰</b>을 전달합니다.
      </p>
      <p className="bg-muted/50 rounded-md border p-2 font-mono text-xs break-all">{data.base}</p>

      {/* **전달물을 이 화면에서 만들어 준다.** 안내서만 보내면 수신 측이 개발을 해야 하는데,
          그 개발 일정이 연동 전체의 일정이 된다. 실행만 하면 되는 묶음을 함께 보낸다. */}
      <section className="space-y-3 rounded-md border p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">연동 키트</h2>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void downloadKit()}>
            {busy ? (
              <Loader2 className="mr-1 size-4 animate-spin" />
            ) : (
              <Download className="mr-1 size-4" />
            )}
            연동 키트 다운로드 (zip)
          </Button>
        </div>
        <p className="text-muted-foreground text-sm">
          수신 시스템에 그대로 전달하는 묶음입니다.{' '}
          <b>이 설치의 주소와 공개 타입이 이미 입력된 상태</b>로 생성되므로, 수신 측에서 수정할
          항목은 액세스 토큰 한 줄입니다.
        </p>
        <ul className="text-muted-foreground list-inside list-disc text-sm">
          <li>
            <code>README.md</code> — 연결 · 수신 · 자동 실행 절차, 응답 형식, 수신 측 준수 사항
          </li>
          <li>
            <code>config.example.ini</code> — 주소 · 공개 타입이 입력된 설정 견본
          </li>
          <li>
            <code>sp_core_pull.py</code> — 수신 스크립트(의존성 `requests` 하나). 증분 · 삭제 표식 ·
            페이지 이어받기 · 재시도 포함. 결과는 CSV · SQLite
          </li>
          <li>
            <code>check.sh</code> — 연결 점검(인증 · 카탈로그 · 첫 페이지)
          </li>
        </ul>
        <p className="text-muted-foreground text-sm">
          수신 측에 개발 인력이 있으면 <b>안내서만 전달해도 됩니다</b> — 호출 규약이 README 에 모두
          기재되어 있습니다.
        </p>
        <ErrorNotice error={error} />
      </section>

      {/* **셋을 한 화면에 쌓지 않는다.** 공개 타입이 늘면 토큰과 조회 이력이 화면 밖으로
          밀려나 「지금 누가 조회하고 있나」 를 보려면 매번 내려야 한다. */}
      <Tabs defaultValue="types">
        <TabsList>
          <TabsTrigger value="types">공개 타입 {data.types.length}</TabsTrigger>
          <TabsTrigger value="tokens">액세스 토큰 {data.consumers.length}</TabsTrigger>
          <TabsTrigger value="recent">최근 조회 이력 {data.recent.length}</TabsTrigger>
        </TabsList>

        <TabsContent value="types" className="space-y-2 pt-4">
          {data.types.length === 0 ? (
            <EmptyState
              title="공개된 타입이 없습니다"
              hint="타입 수정 창에서 「코어」 를 지정하면 이 목록에 표시됩니다."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>타입</TableHead>
                  <TableHead className="text-right">객체</TableHead>
                  <TableHead className="text-right">속성</TableHead>
                  <TableHead>최종 변경</TableHead>
                  <TableHead>엔드포인트</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.types.map((one) => (
                  <TableRow key={one.slug}>
                    <TableCell>
                      <Link to={`/o/${one.slug}`} className="hover:underline">
                        {one.label}
                      </Link>
                      <span className="text-muted-foreground ml-2 font-mono text-xs">
                        {one.slug}
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{one.count}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {one.properties.length}
                    </TableCell>
                    <TableCell className="text-sm">{shownDateTime(one.updated_at)}</TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs break-all">
                      {one.endpoint}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <p className="text-muted-foreground text-sm">
            공개 · 해제 기록은 관리 › 감사 기록에서 <code>ontology.type.core</code> 로 조회합니다 —
            <b>언제 누가 공개했는지</b>는 이 목록이 아니라 그 기록이 답합니다.
          </p>
        </TabsContent>

        <TabsContent value="tokens" className="space-y-2 pt-4">
          <p className="text-muted-foreground text-sm">
            코어를 조회할 수 있는 <b>유효한 토큰</b>입니다. <b>전체 읽기</b>로 표시된 토큰은 범위가{' '}
            <code>read</code> 라 코어 외 자료도 조회합니다 — 외부 시스템에는 <code>core:read</code>{' '}
            범위로 발급하십시오. 차단은 해당 토큰 소유 계정의 <b>내 정보</b> 에서 삭제합니다.
          </p>
          {data.consumers.length === 0 ? (
            <EmptyState
              title="발급된 토큰이 없습니다"
              hint="내 정보 › 액세스 토큰에서 core:read 범위로 발급합니다."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>토큰</TableHead>
                  <TableHead>계정</TableHead>
                  <TableHead>범위</TableHead>
                  <TableHead>만료</TableHead>
                  <TableHead>최종 사용</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.consumers.map((one) => (
                  <TableRow key={`${one.name}-${one.created_at}`}>
                    <TableCell>{one.name}</TableCell>
                    <TableCell className="text-sm">{one.owner}</TableCell>
                    <TableCell>
                      {one.narrow ? (
                        <span className="rounded border px-1.5 text-xs">코어 전용</span>
                      ) : (
                        <span className="rounded border border-amber-500/50 bg-amber-500/10 px-1.5 text-xs">
                          전체 읽기
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      {one.expires_at ? (
                        shownDate(one.expires_at)
                      ) : (
                        <span className="text-muted-foreground">없음</span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      {/* **미사용 토큰은 아직 연결되지 않은 연동이다** — 사용 중인 토큰과 구분해
                        표시한다. */}
                      {one.last_used_at ? (
                        shownDateTime(one.last_used_at)
                      ) : (
                        <span className="text-muted-foreground">미사용</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </TabsContent>

        <TabsContent value="recent" className="space-y-2 pt-4">
          <p className="text-muted-foreground text-sm">
            변경분이 <b>없는 조회는 기록하지 않습니다</b> — 야간 조회마다 기록하면 이 목록은 곧
            확인되지 않습니다. 전체 이력은 관리 › 감사 기록에서 <code>core.pull</code> 로
            조회합니다.
          </p>
          {data.recent.length === 0 ? (
            <EmptyState
              title="조회 이력이 없습니다"
              hint="수신 시스템이 주소와 토큰으로 조회하면 이 목록에 표시됩니다."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>일시</TableHead>
                  <TableHead>타입</TableHead>
                  <TableHead className="text-right">건수</TableHead>
                  <TableHead>토큰</TableHead>
                  <TableHead>기준 시각</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.recent.map((one, index) => (
                  <TableRow key={`${one.at}-${index}`}>
                    <TableCell className="text-sm">{shownDateTime(one.at)}</TableCell>
                    <TableCell className="font-mono text-xs">{one.type_slug}</TableCell>
                    <TableCell className="text-right tabular-nums">{one.rows}</TableCell>
                    <TableCell className="text-sm">{one.token ?? one.actor}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">{one.since}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
