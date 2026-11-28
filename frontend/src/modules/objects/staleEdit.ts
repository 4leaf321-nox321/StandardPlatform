/**
 * 「그 사이 다른 사람이 이 객체를 수정했습니다」(OBJECTS-0162) — 객체를 읽은 뒤(`updated_at`) 누가
 * 저장해, 서버가 덮지 않고 거절한 것. 수정 폼 · 되돌리기가 보낸 `base_updated_at` 이 지금 값과 다를
 * 때다. 받은 화면은 그 말을 보이고 다시 불러오게 한다 — 그대로 다시 보내면 같은 거절이 온다.
 */

import { ApiError } from '@/shared/api/client'

export function isStaleEdit(caught: unknown): caught is ApiError {
  return caught instanceof ApiError && caught.status === 409 && caught.code.endsWith('OBJECTS-0162')
}
