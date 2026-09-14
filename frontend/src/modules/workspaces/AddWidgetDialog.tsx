/**
 * 위젯 추가 — **홈에서 출발하는 길.**
 *
 * 위젯을 올리는 자리는 목록 화면의 「통계」 다. 그런데 「여기 뭘 좀 띄우고 싶다」
 * 는 생각은 **홈을 보다가** 나고, 그때 사람은 어느 타입의 목록으로 가야 하는지부터
 * 막힌다. 여기서 타입만 고르면 그 목록이 통계를 펼친 채로 열린다.
 *
 * 이 창이 위젯을 직접 만들지 않는 이유: 축과 조건은 데이터를 보면서 정하는 것이다.
 * 여기서 다 고르게 하면 **무엇이 몇 건인지 안 보이는 채로** 고르는 일이 된다.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ontologyApi } from '@/modules/ontology/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { useResource } from '@/shared/hooks/useResource'

export function AddWidgetDialog({ onClose }: { onClose: () => void }) {
  const schema = useResource(() => ontologyApi.schema(), [])
  const [slug, setSlug] = useState<string | null>(null)
  const navigate = useNavigate()

  // 투영 타입은 행이 없어 못 센다 — 고를 수 있다고 보여 주고 나서 거절하지 않는다.
  const types = (schema.data?.types ?? []).filter(
    (one) => one.kind_class !== 'system' && one.is_active,
  )

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>홈에 위젯 추가</DialogTitle>
          <DialogDescription>
            무엇을 띄울지 고르면 그 목록이 <strong>통계를 펼친 채로</strong> 열립니다. 거기서 조건과
            기준을 정하고 「홈에 올리기」 를 누르면 여기 섭니다.
          </DialogDescription>
        </DialogHeader>

        <ErrorNotice error={schema.error} />
        <SearchablePicker
          options={types.map((one) => ({
            value: one.slug,
            label: one.label,
            hint: one.description || undefined,
            keywords: one.slug,
          }))}
          value={slug}
          onChange={setSlug}
          placeholder="어느 것을 띄울까요"
          searchPlaceholder="타입 이름으로 검색"
          emptyText="정의된 타입이 없습니다. 먼저 온톨로지에서 타입을 만드세요."
          loading={schema.loading}
        />

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            취소
          </Button>
          <Button disabled={!slug} onClick={() => slug && navigate(`/o/${slug}?group=1`)}>
            목록으로 가기
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
