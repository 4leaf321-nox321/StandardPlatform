/**
 * 코어 현황 — **무엇이 열려 있고, 누가 읽을 수 있고, 누가 받아 갔나.**
 *
 * 셋이 흩어져 있으면(타입 목록 · 토큰 목록 · 감사 기록) 「지금 바깥으로 뭐가 나가고 있지」 를
 * 아무도 한눈에 답할 수 없고, **답할 수 없는 것은 곧 잊힌다.** 열어 둔 창구는 잊히면 안 된다.
 *
 * 여는 것 자체는 여기서 안 한다 — 타입의 칸이라 타입 수정 창에 있다. 이 화면은 **보는 자리**다.
 */

import { Link } from 'react-router-dom'

import { ontologyApi } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate, shownDateTime } from '@/shared/lib/datetime'

export default function OntologyCorePage() {
  const status = useResource(() => ontologyApi.coreStatus(), [])

  if (status.error) return <ErrorNotice error={status.error} />
  const data = status.data
  if (!data) return <p className="text-muted-foreground text-sm">읽는 중…</p>

  return (
    <div className="space-y-6">
      <p className="text-muted-foreground text-sm">
        타입 수정 창에서 <b>「코어」</b> 를 켜면 그 타입이 바깥 시스템에 열립니다. 받아 가는 쪽에는{' '}
        <b>이 주소</b>와 <b>범위가 `core:read` 인 토큰</b>을 건넵니다.
      </p>
      <p className="bg-muted/50 rounded-md border p-2 font-mono text-xs break-all">{data.base}</p>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">열려 있는 타입 {data.types.length}</h2>
        {data.types.length === 0 ? (
          <EmptyState
            title="아직 아무것도 안 열었습니다"
            hint="타입 수정 창의 「코어」 를 켜면 여기 섭니다."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>타입</TableHead>
                <TableHead className="text-right">건수</TableHead>
                <TableHead className="text-right">칸</TableHead>
                <TableHead>마지막 변경</TableHead>
                <TableHead>주소</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.types.map((one) => (
                <TableRow key={one.slug}>
                  <TableCell>
                    <Link to={`/o/${one.slug}`} className="hover:underline">
                      {one.label}
                    </Link>
                    <span className="text-muted-foreground ml-2 font-mono text-xs">{one.slug}</span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{one.count}</TableCell>
                  <TableCell className="text-right tabular-nums">{one.properties.length}</TableCell>
                  <TableCell className="text-sm">{shownDateTime(one.updated_at)}</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-xs break-all">
                    {one.endpoint}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">읽을 수 있는 자격 {data.consumers.length}</h2>
        <p className="text-muted-foreground text-sm">
          이 창구를 읽을 수 있는 <b>살아 있는 토큰</b>입니다. <b>넓음</b>으로 표시된 것은{' '}
          <code>read</code> 라 코어 밖도 읽습니다 — 바깥 시스템에는 <code>core:read</code> 하나로
          발급하세요. 끊으려면 그 토큰을 가진 계정의 <b>내 정보</b> 에서 삭제합니다.
        </p>
        {data.consumers.length === 0 ? (
          <EmptyState
            title="발급된 자격이 없습니다"
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
                <TableHead>마지막 사용</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.consumers.map((one) => (
                <TableRow key={`${one.name}-${one.created_at}`}>
                  <TableCell>{one.name}</TableCell>
                  <TableCell className="text-sm">{one.owner}</TableCell>
                  <TableCell>
                    {one.narrow ? (
                      <span className="rounded border px-1.5 text-xs">코어만</span>
                    ) : (
                      <span className="rounded border border-amber-500/50 bg-amber-500/10 px-1.5 text-xs">
                        넓음
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
                    {/* **한 번도 안 쓴 자격은 아직 안 붙은 연동이다** — 어제도 받아 간 것과
                        무게가 다르므로 갈라 적는다. */}
                    {one.last_used_at ? (
                      shownDateTime(one.last_used_at)
                    ) : (
                      <span className="text-muted-foreground">아직 안 씀</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">최근에 받아 간 것</h2>
        <p className="text-muted-foreground text-sm">
          받아 갈 것이 <b>없던 밤은 안 남습니다</b> — 그런 줄이 쌓이면 이 목록은 곧 아무도 안
          읽습니다. 전부는 관리 › 감사 기록에서 <code>core.pull</code> 로 봅니다.
        </p>
        {data.recent.length === 0 ? (
          <EmptyState
            title="아직 아무도 안 받아 갔습니다"
            hint="상대가 주소와 토큰으로 한 번 부르면 여기 섭니다."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>때</TableHead>
                <TableHead>타입</TableHead>
                <TableHead className="text-right">건수</TableHead>
                <TableHead>토큰</TableHead>
                <TableHead>어디서부터</TableHead>
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
      </section>
    </div>
  )
}
