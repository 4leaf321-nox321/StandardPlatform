/** 첨부 API. */

import { api, downloadFile } from '@/shared/api/client'

export interface Attachment {
  id: string
  owner_table: string
  owner_id: string
  original_name: string
  content_type: string
  size_bytes: number
  /** 내용의 해시. **같은 파일인지 눈으로 확인할 수 있는 유일한 값이다.** */
  sha256: string
  created_at: string
}

export const attachmentApi = {
  list: (ownerTable: string, ownerId: string) =>
    api.get<Attachment[]>(
      `/attachments?owner_table=${encodeURIComponent(ownerTable)}&owner_id=${ownerId}`,
    ),

  /**
   * 파일 하나를 붙인다.
   *
   * **FormData 를 쓸 때 Content-Type 을 직접 넣지 않는다.** multipart 는 본문에
   * boundary 문자열이 필요한데 브라우저가 헤더를 만들 때 그것을 붙여 준다 —
   * 손으로 넣으면 boundary 가 없어 서버가 본문을 못 읽는다. `client.ts` 가
   * FormData 를 알아보고 헤더를 비운다.
   */
  upload: ({
    ownerTable,
    ownerId,
    workspaceSlug,
    file,
  }: {
    ownerTable: string
    ownerId: string
    workspaceSlug: string | null
    file: File
  }) => {
    const body = new FormData()
    body.append('owner_table', ownerTable)
    body.append('owner_id', ownerId)
    if (workspaceSlug) body.append('workspace_slug', workspaceSlug)
    body.append('file', file)
    return api.postForm<Attachment>('/attachments', body)
  },

  /**
   * 내용을 내려받는다.
   *
   * 평범한 링크로는 안 된다 — access 토큰은 메모리에만 있어서 브라우저가 스스로
   * 여는 주소에는 안 실리고, 그러면 새 탭에서 401 이 나는데 화면에는 아무 표시도
   * 안 뜬다.
   */
  download: (id: string, filename: string) =>
    downloadFile(`/attachments/${id}/content`, filename),

  remove: (id: string) => api.delete<void>(`/attachments/${id}`),
}
