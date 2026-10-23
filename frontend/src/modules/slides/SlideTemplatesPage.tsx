/**
 * 장표 틀(ADR 0037) — 시스템 관리자. 조직의 정형 배치를 틀로 만들어 두면, 누구나 그 틀에 주제만
 * 선택해 장표를 만든다.
 */

import { Link } from 'react-router-dom'

import { slideApi } from '@/modules/slides/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDateTime } from '@/shared/lib/datetime'

export default function SlideTemplatesPage() {
  const templates = useResource(() => slideApi.templates(), [])

  return (
    <div className="space-y-4">
      <PageHeader
        title="장표 틀"
        description="조직의 정형 배치입니다. 격자(최대 3행 × 3열)에 영역을 나누고, 영역마다 어떤 기록을 어떤 칸으로 표시할지 정합니다."
        actions={
          <Button asChild size="sm">
            <Link to="/admin/slide-templates/new">새 틀</Link>
          </Button>
        }
      />
      <ErrorNotice error={templates.error} />
      {templates.data && templates.data.length === 0 ? (
        <EmptyState
          title="아직 틀이 없습니다"
          hint="「새 틀」 에서 주제 타입(예: 모델)과 영역을 정하면, 누구나 그 틀로 장표를 만듭니다."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead className="w-40">주제</TableHead>
              <TableHead className="w-28">격자</TableHead>
              <TableHead className="w-24 text-right">장표</TableHead>
              <TableHead className="w-44">수정</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(templates.data ?? []).map((one) => (
              <TableRow key={one.id}>
                <TableCell>
                  <Link
                    to={`/admin/slide-templates/${one.id}`}
                    className="font-medium hover:underline"
                  >
                    {one.name}
                  </Link>
                  {one.description && (
                    <p className="text-muted-foreground text-xs">{one.description}</p>
                  )}
                </TableCell>
                <TableCell className="text-sm">{one.subject_type_label}</TableCell>
                <TableCell className="text-sm">
                  {one.rows}행 × {one.cols}열 · 영역 {one.areas.length}
                </TableCell>
                <TableCell className="text-right text-sm tabular-nums">{one.slides}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {shownDateTime(one.updated_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
